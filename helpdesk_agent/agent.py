"""
HelpdeskAgent — retrieval + Claude tool-use loop + structured result.

Flow for each employee turn
---------------------------
    employee message
          │
          ▼
    BM25 retrieval over knowledge_base/  (chunk_size_words, top_k)        ← Lab 1
          │  top-k excerpts
          ▼
    Claude (system prompt v1/v2, tools vague/precise)                      ← Labs 2, 3
          │  tool_use blocks
          ▼
    HelpdeskToolExecutor → HRDatabase (access control on/off)              ← Lab 4
          │  tool_result blocks, loop until end_turn
          ▼
    final answer text + citations
          │
    next turn: with or without conversation history                        ← Lab 5

The agent returns a plain dict (see `_build_result`) so promptfoo assertions can
inspect the answer AND the retrieved contexts, tool calls and side effects.

Why a hand-written loop instead of the SDK tool runner? The eval needs to record
every call, its arguments and whether access was denied, and the demo toggles bugs
inside the tool layer — explicit control is the point here.
"""

from __future__ import annotations

import datetime as dt
import re
import time
from typing import Any

import anthropic

from helpdesk_agent import response_cache
from helpdesk_agent.knowledge_base import build_retriever
from helpdesk_agent.mock_hr_database import HRDatabase
from helpdesk_agent.settings import SONNET_MODEL_ID, AgentSettings
from helpdesk_agent.system_prompts import build_system_prompt, build_user_message
from helpdesk_agent.tools import HelpdeskToolExecutor, get_tool_schemas

# Safety valve: an agent stuck in a tool loop should fail the test, not hang the demo.
MAX_TOOL_ITERATIONS_PER_TURN = 8

# Server-side refusal fallback (beta). If Claude Sonnet 5.5 declines a request for
# safety reasons, the API re-runs it on a fallback model inside the same call.
# Claude Haiku 5.5 has no server-side fallback, so it is only sent for Sonnet.
REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Citations look like [leave_policy.md#3]
_CITATION_PATTERN = re.compile(r"\[([a-z0-9_]+\.md#\d+)\]")


class HelpdeskAgent:
    """One configured build of the copilot (buggy or fixed, depending on settings)."""

    def __init__(self, settings: AgentSettings, client: anthropic.Anthropic | None = None) -> None:
        self.settings = settings
        self._client = client
        self.retriever = build_retriever(settings.chunk_size_words, settings.chunk_overlap_words)
        self.tool_schemas = get_tool_schemas(settings.tool_schema_style)

    @property
    def client(self) -> anthropic.Anthropic:
        # Created lazily so cache hits and offline checks never need an API key.
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    # ------------------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------------------
    def run(self, question: str, employee_id: str) -> dict[str, Any]:
        """Single-turn convenience wrapper."""
        return self.run_conversation([question], employee_id)

    def run_conversation(self, user_turns: list[str], employee_id: str) -> dict[str, Any]:
        """Run one or more employee turns and return the structured result.

        Results are served from the on-disk cache when possible (see response_cache.py).
        """
        use_cache = self.settings.use_response_cache and response_cache.is_cache_enabled()
        cache_key = response_cache.make_cache_key(
            {
                "settings": {k: v for k, v in self.settings.to_dict().items() if k != "use_response_cache"},
                "turns": user_turns,
                "employee_id": employee_id,
            }
        )
        if use_cache and (cached := response_cache.read_cached_result(cache_key)) is not None:
            cached["served_from_cache"] = True
            return cached

        result = self._run_uncached(user_turns, employee_id)
        if use_cache and not result.get("error"):
            response_cache.write_cached_result(cache_key, result)
        result["served_from_cache"] = False
        return result

    # ------------------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------------------
    def _run_uncached(self, user_turns: list[str], employee_id: str) -> dict[str, Any]:
        started_at = time.perf_counter()
        # Fresh, isolated copy of the HR data for this conversation.
        database = HRDatabase.for_caller(employee_id, self.settings.enforce_access_control)
        executor = HelpdeskToolExecutor(database)
        system_prompt = build_system_prompt(
            self.settings.prompt_version,
            today=database.today,
            weekday=dt.date.fromisoformat(database.today).strftime("%A"),
            employee=database.caller,
            employee_id=employee_id,
        )

        usage = {"input_tokens": 0, "output_tokens": 0}
        served_by_models: set[str] = set()
        conversation_history: list[dict[str, Any]] = []
        turn_results: list[dict[str, Any]] = []
        previous_user_message = ""

        for turn_index, user_message in enumerate(user_turns):
            # --- Retrieval -------------------------------------------------------------
            # With memory, include the previous message so follow-ups like "and how many
            # of those carry over?" still retrieve the right policy.
            remember = self.settings.use_conversation_memory
            retrieval_query = f"{previous_user_message} {user_message}" if remember else user_message
            hits = self.retriever.search(retrieval_query, self.settings.top_k)
            excerpts = [(chunk.chunk_id, chunk.text) for chunk, _score in hits]

            # --- Build the message list ------------------------------------------------
            # PLANTED BUG (Lab 5): without memory, every turn starts from an empty history,
            # so the model cannot resolve "it", "that one" or "make it Tuesday instead".
            messages = list(conversation_history) if remember else []
            messages.append({"role": "user", "content": build_user_message(user_message, excerpts)})

            answer_text, messages, stop_reason = self._tool_use_loop(
                system_prompt, messages, executor, usage, served_by_models
            )
            if remember:
                conversation_history = messages
            previous_user_message = user_message

            turn_results.append(
                {
                    "turn": turn_index + 1,
                    "user_message": user_message,
                    "answer": answer_text,
                    "stop_reason": stop_reason,
                    "retrieved_chunk_ids": [cid for cid, _ in excerpts],
                }
            )

        final_hits_text = [text for _cid, text in excerpts]
        return self._build_result(
            turn_results=turn_results,
            retrieved_contexts=final_hits_text,
            executor=executor,
            database=database,
            usage=usage,
            served_by_models=served_by_models,
            latency_ms=int((time.perf_counter() - started_at) * 1000),
        )

    def _tool_use_loop(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        executor: HelpdeskToolExecutor,
        usage: dict[str, int],
        served_by_models: set[str],
    ) -> tuple[str, list[dict[str, Any]], str]:
        """Standard agentic loop: call Claude, run requested tools, repeat until done."""
        stop_reason = "max_iterations"
        response = None
        for _ in range(MAX_TOOL_ITERATIONS_PER_TURN):
            response = self._create_message(system_prompt, messages)
            usage["input_tokens"] += response.usage.input_tokens
            usage["output_tokens"] += response.usage.output_tokens
            served_by_models.add(response.model)
            stop_reason = response.stop_reason or "unknown"

            # Append the FULL content (thinking + text + tool_use blocks) unchanged —
            # required so the model's reasoning stays valid on the next request.
            messages.append({"role": "assistant", "content": response.content})

            if stop_reason == "pause_turn":
                continue
            if stop_reason != "tool_use":
                break  # end_turn, refusal, max_tokens → this turn is finished

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                result_text, is_error = executor.execute(block.name, dict(block.input or {}))
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result_text, "is_error": is_error}
                )
            # All results for one assistant turn go back in ONE user message.
            messages.append({"role": "user", "content": tool_results})

        answer_text = ""
        if response is not None:
            answer_text = "\n".join(b.text for b in response.content if b.type == "text").strip()
        if stop_reason == "refusal":
            answer_text = answer_text or "[The model declined to answer this request.]"
        return answer_text, messages, stop_reason

    def _create_message(self, system_prompt: str, messages: list[dict[str, Any]]):
        """One Messages API call. Thinking is left at the model default (adaptive);
        `effort` keeps it short. No temperature: current models reject it."""
        request: dict[str, Any] = {
            "model": self.settings.model,
            "max_tokens": self.settings.max_output_tokens,
            "system": system_prompt,
            "tools": self.tool_schemas,
            "messages": messages,
            "output_config": {"effort": self.settings.effort},
        }
        if self.settings.model == SONNET_MODEL_ID:
            return self.client.beta.messages.create(
                **request, betas=[REFUSAL_FALLBACK_BETA], fallbacks="default"
            )
        return self.client.beta.messages.create(**request)

    def _build_result(
        self,
        *,
        turn_results: list[dict[str, Any]],
        retrieved_contexts: list[str],
        executor: HelpdeskToolExecutor,
        database: HRDatabase,
        usage: dict[str, int],
        served_by_models: set[str],
        latency_ms: int,
    ) -> dict[str, Any]:
        """The contract between the agent and the promptfoo assertions.

        Keys (all assertions in evals/ rely on these names):
          answer               final answer text of the LAST turn
          citations            chunk ids cited in the final answer, e.g. ["leave_policy.md#0"]
          retrieved_contexts   text of the excerpts retrieved for the LAST turn
          retrieved_chunk_ids  ids of those excerpts
          tool_calls           every tool call across all turns (name, input, status, target)
          leave_requests       leave requests in the HR system at the end (incl. cancelled)
          it_tickets_created   tickets created during this conversation
          turns                per-turn answers (multi-turn lab)
          caller_employee_id   the logged-in employee
          usage / cost_usd / latency_ms / model / served_by_models
        """
        final_answer = turn_results[-1]["answer"] if turn_results else ""
        return {
            "answer": final_answer,
            "citations": sorted(set(_CITATION_PATTERN.findall(final_answer))),
            "retrieved_contexts": retrieved_contexts,
            "retrieved_chunk_ids": turn_results[-1]["retrieved_chunk_ids"] if turn_results else [],
            "tool_calls": [record.to_dict() for record in executor.call_log],
            "leave_requests": database.leave_requests,
            "it_tickets_created": database.tickets_created_this_conversation,
            "turns": turn_results,
            "caller_employee_id": database.caller_employee_id,
            "model": self.settings.model,
            "served_by_models": sorted(served_by_models),
            "usage": usage,
            "cost_usd": round(self.settings.estimate_cost_usd(usage["input_tokens"], usage["output_tokens"]), 6),
            "latency_ms": latency_ms,
        }
