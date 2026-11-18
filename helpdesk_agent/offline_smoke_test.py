"""
Offline smoke test — verifies the agent plumbing WITHOUT calling the Claude API.

Run:  npm run smoke-test     (or: python -m helpdesk_agent.offline_smoke_test)

It checks:
  1. Lab 1 bug is real: buggy chunking misses the carry-forward rule, fixed finds it.
  2. Lab 4 access-control rules behave correctly.
  3. The full agent loop (retrieval → model → tool call → tool result → final answer)
     works end to end, using a scripted fake Claude client.
  4. The promptfoo provider returns the shape promptfoo expects.

Use it before a demo (and in CI) to catch setup problems in seconds, for free.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

from helpdesk_agent.agent import HelpdeskAgent
from helpdesk_agent.knowledge_base import build_retriever
from helpdesk_agent.mock_hr_database import HRDatabase
from helpdesk_agent.settings import AgentSettings


# -----------------------------------------------------------------------------
# A scripted stand-in for anthropic.Anthropic
# -----------------------------------------------------------------------------
class _ScriptedMessages:
    """Returns pre-written responses in order, mimicking client.beta.messages."""

    def __init__(self, scripted_responses: list[SimpleNamespace]) -> None:
        self._responses = list(scripted_responses)
        self.requests: list[dict[str, Any]] = []

    def create(self, **request: Any) -> SimpleNamespace:
        # Snapshot the message list: the real SDK serialises it at call time, while
        # the agent keeps appending to the same list object afterwards.
        self.requests.append({**request, "messages": list(request["messages"])})
        return self._responses.pop(0)


class FakeClaudeClient:
    def __init__(self, scripted_responses: list[SimpleNamespace]) -> None:
        self.beta = SimpleNamespace(messages=_ScriptedMessages(scripted_responses))


def _response(stop_reason: str, *blocks: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        content=list(blocks),
        stop_reason=stop_reason,
        model="claude-sonnet-5-5",
        usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
    )


def _text(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _tool_use(tool_id: str, name: str, tool_input: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=tool_input)


# -----------------------------------------------------------------------------
# Checks
# -----------------------------------------------------------------------------
def check_retrieval_bug() -> None:
    question = "How many unused annual leave days can I carry forward to next year?"
    answer_phrase = "A maximum of 5 days may be moved"

    def hit(size: int, overlap: int, k: int) -> bool:
        return any(answer_phrase in c.text for c, _ in build_retriever(size, overlap).search(question, k))

    assert not hit(80, 0, 2), "Lab 1 bug not reproduced: buggy retriever unexpectedly found the answer"
    assert hit(400, 40, 4), "Lab 1 fix broken: fixed retriever did not find the answer"
    print("[ok] Lab 1 retrieval bug reproduces, and the fix retrieves the answer")


def check_access_control() -> None:
    db = HRDatabase.for_caller("E101", enforce_access_control=True)
    assert db.is_access_allowed("E101", "read")
    assert not db.is_access_allowed("E102", "read")
    assert HRDatabase.for_caller("E201", True).is_access_allowed("E102", "read")
    assert HRDatabase.for_caller("E105", True).is_access_allowed("E104", "read")
    print("[ok] Lab 4 access-control rules")


def check_agent_loop(enforce_access_control: bool) -> dict[str, Any]:
    """Model asks for a PEER's balance (E102) — denied or allowed depending on the flag."""
    fake_client = FakeClaudeClient(
        [
            _response("tool_use", _tool_use("toolu_1", "get_leave_balance", {"employee_id": "E102"})),
            _response("end_turn", _text("Here is what I found [leave_policy.md#0].")),
        ]
    )
    settings = AgentSettings(enforce_access_control=enforce_access_control, use_response_cache=False)
    agent = HelpdeskAgent(settings, client=fake_client)
    result = agent.run("What's Daniel's leave balance? E102", "E101")

    call = result["tool_calls"][0]
    expected_status = "denied" if enforce_access_control else "ok"
    assert call["status"] == expected_status, f"expected {expected_status}, got {call['status']}"
    assert result["citations"] == ["leave_policy.md#0"], result["citations"]
    assert result["usage"] == {"input_tokens": 2000, "output_tokens": 200}
    assert result["cost_usd"] > 0

    # The tool result must have been sent back to the model in the 2nd request.
    second_request = fake_client.beta.messages.requests[1]
    last_message = second_request["messages"][-1]
    assert last_message["role"] == "user" and last_message["content"][0]["type"] == "tool_result"
    return result


def check_provider_shape() -> None:
    os.environ["HELPDESK_CACHE"] = "0"
    from helpdesk_agent import promptfoo_provider

    fake_client = FakeClaudeClient([_response("end_turn", _text("You get 18 days [leave_policy.md#0]."))])
    agent = HelpdeskAgent(AgentSettings(use_response_cache=False), client=fake_client)
    promptfoo_provider._AGENT_INSTANCES['{"use_response_cache": false}'] = agent
    response = promptfoo_provider.call_api(
        "How many leave days do I get?", {"config": {"use_response_cache": False}}, {"vars": {}}
    )
    assert "error" not in response, response
    assert response["output"]["answer"].startswith("You get 18 days")
    assert {"tokenUsage", "cost", "latencyMs", "cached"} <= set(response)
    print("[ok] promptfoo provider returns output, tokenUsage, cost, latencyMs, cached")


def main() -> None:
    check_retrieval_bug()
    check_access_control()
    check_agent_loop(enforce_access_control=True)
    print("[ok] Agent loop: tool call to a peer's record is DENIED with access control on")
    check_agent_loop(enforce_access_control=False)
    print("[ok] Agent loop: same call SUCCEEDS with access control off (the planted Lab 4 bug)")
    check_provider_shape()
    print("\nAll offline checks passed.")


if __name__ == "__main__":
    main()
