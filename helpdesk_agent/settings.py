"""
AgentSettings — the single place where every planted bug is switched on or off.

How the demo uses this
----------------------
Each promptfoo config declares the agent twice under `providers:`, for example:

    providers:
      - id: file://../../helpdesk_agent/promptfoo_provider.py
        label: "buggy: 80-word chunks, top-2"
        config: { chunk_size_words: 80, top_k: 2 }
      - id: file://../../helpdesk_agent/promptfoo_provider.py
        label: "fixed: 400-word chunks, top-4"
        config: { chunk_size_words: 400, top_k: 4 }

promptfoo passes that `config:` dict to the provider, which turns it into an
`AgentSettings` via `AgentSettings.from_config(...)`. Anything not specified
falls back to the defaults below, and the defaults are the FIXED / production
behaviour — so a lab only needs to name the one flag it is breaking.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any, Literal

# --- Models -------------------------------------------------------------------
# Exact model IDs (no date suffix). Pricing is per 1M tokens and is used to
# report a `cost` back to promptfoo so the `cost` assertion and the cost column
# in the web viewer work for our custom Python provider.
SONNET_MODEL_ID = "claude-sonnet-5-5"
HAIKU_MODEL_ID = "claude-haiku-5-5"

MODEL_PRICING_USD_PER_MILLION_TOKENS: dict[str, dict[str, float]] = {
    SONNET_MODEL_ID: {"input": 2.00, "output": 10.00},
    HAIKU_MODEL_ID: {"input": 0.10, "output": 0.50},
}

PromptVersion = Literal["v1_overhelpful", "v2_grounded"]
ToolSchemaStyle = Literal["vague", "precise"]


@dataclass(frozen=True)
class AgentSettings:
    """Every knob the demo turns. Defaults = the fixed, production-ready agent."""

    # ---- Model -----------------------------------------------------------------
    # Lab 6 compares Sonnet vs Haiku on cost, latency and quality.
    model: str = SONNET_MODEL_ID
    # Effort controls how much the model thinks. "low" keeps the demo fast and
    # cheap; the agent's tasks are short and well specified.
    effort: Literal["low", "medium", "high"] = "low"
    max_output_tokens: int = 4000

    # ---- Lab 1: Retrieval --------------------------------------------------------
    # PLANTED BUG: tiny chunks + too few results split policy rules apart, so the
    # sentence with the answer is never retrieved. Fixed: bigger chunks, more hits.
    chunk_size_words: int = 400
    chunk_overlap_words: int = 40
    top_k: int = 4

    # ---- Lab 2: Faithfulness -------------------------------------------------------
    # PLANTED BUG: "v1_overhelpful" tells the model to always give a complete
    # answer and fill gaps from general knowledge → it invents policies.
    # Fixed: "v2_grounded" answers only from retrieved context, cites sources,
    # and says "I don't know" when the documents are silent.
    prompt_version: PromptVersion = "v2_grounded"

    # ---- Lab 3: Tool calling -------------------------------------------------------
    # PLANTED BUG: "vague" tool schemas — fuzzy descriptions that overlap, free-text
    # priority/category/date fields. Fixed: "precise" — clear when-to-use guidance,
    # enums, ISO dates, `strict: true` schema validation.
    tool_schema_style: ToolSchemaStyle = "precise"

    # ---- Lab 4: Security -----------------------------------------------------------
    # PLANTED BUG: tools trust whatever employee_id the model passes in, so a user
    # can talk the model into reading someone else's records. Fixed: the tool
    # layer checks the logged-in caller against the target record (defense in
    # depth — never rely on the prompt alone for access control).
    enforce_access_control: bool = True

    # ---- Lab 5: Multi-turn ---------------------------------------------------------
    # PLANTED BUG: each user turn is sent to the model on its own, so follow-ups
    # like "actually make it Tuesday" have no context. Fixed: full history.
    use_conversation_memory: bool = True

    # ---- Demo reliability ----------------------------------------------------------
    # Cache agent results on disk so re-running a lab during the live demo is
    # instant and gives identical results. Disable with env HELPDESK_CACHE=0.
    use_response_cache: bool = True

    # ------------------------------------------------------------------------------
    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "AgentSettings":
        """Build settings from a promptfoo provider `config:` dict.

        Unknown keys raise immediately — a typo in a YAML config should fail
        loudly rather than silently run the default (fixed) agent.
        """
        config = dict(config or {})
        known_fields = {f.name for f in fields(cls)}
        unknown_keys = set(config) - known_fields
        if unknown_keys:
            raise ValueError(
                f"Unknown AgentSettings key(s) in promptfoo config: {sorted(unknown_keys)}. "
                f"Valid keys: {sorted(known_fields)}"
            )
        return cls(**config)

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict form, used for the cache key and for debug metadata."""
        return asdict(self)

    def estimate_cost_usd(self, input_tokens: int, output_tokens: int) -> float:
        """Convert token usage into dollars using the model's list price."""
        pricing = MODEL_PRICING_USD_PER_MILLION_TOKENS.get(self.model)
        if pricing is None:
            return 0.0
        return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000
