"""
promptfoo custom Python provider — the bridge between promptfoo and HelpdeskAgent.

promptfoo calls `call_api(prompt, options, context)` once per (test case × provider).
We evaluate the WHOLE agent (retrieval + tools + guardrails), not a bare prompt,
because that is what users actually experience.

Referenced from every lab config like this:

    providers:
      - id: file://../../helpdesk_agent/promptfoo_provider.py
        label: "fixed"
        config:                       # → AgentSettings (see settings.py)
          prompt_version: v2_grounded
          default_employee_id: E101   # optional: who is "logged in" if a test doesn't say

Inputs we read
--------------
  prompt                       the rendered prompt — our configs use '{{question}}'
  context["vars"]["employee_id"]  the logged-in employee for this test (default E101)
  context["vars"]["turns"]        OPTIONAL list of messages for multi-turn tests (Lab 5);
                                  when present, `prompt` is ignored

What we return
--------------
  output     the agent's structured result dict (answer, citations, tool_calls, …)
  tokenUsage / cost   so promptfoo's cost column and `cost` assertion work
  latencyMs  latency of the original live run (survives cache replays)
  cached     True when served from the agent's on-disk cache
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# promptfoo puts this file's folder on sys.path; we need the project root so that
# `import helpdesk_agent...` works no matter where promptfoo is launched from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:  # Load ANTHROPIC_API_KEY from <project>/.env if present.
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:  # python-dotenv is optional; promptfoo also loads .env itself
    pass

from helpdesk_agent.agent import HelpdeskAgent  # noqa: E402  (import after sys.path fix)
from helpdesk_agent.settings import AgentSettings  # noqa: E402

DEFAULT_LOGGED_IN_EMPLOYEE = "E101"  # Priya Sharma, Software Engineer

# Provider-config keys that are NOT AgentSettings fields: our own provider option,
# plus keys promptfoo itself adds or uses to run Python providers (e.g. it injects
# `basePath`). Everything else must be a valid AgentSettings field — a typo fails loudly.
_PROVIDER_ONLY_KEYS = {"default_employee_id", "basePath", "pythonExecutable", "timeout", "workers"}

# One agent instance per distinct config, reused across test cases in this process.
_AGENT_INSTANCES: dict[str, HelpdeskAgent] = {}


def _get_agent(settings_config: dict[str, Any]) -> HelpdeskAgent:
    key = json.dumps(settings_config, sort_keys=True)
    if key not in _AGENT_INSTANCES:
        _AGENT_INSTANCES[key] = HelpdeskAgent(AgentSettings.from_config(settings_config))
    return _AGENT_INSTANCES[key]


def _parse_turns(raw_turns: Any) -> list[str] | None:
    """`turns` may arrive as a real list (YAML) or a JSON string (CSV)."""
    if raw_turns in (None, "", []):
        return None
    if isinstance(raw_turns, str):
        raw_turns = json.loads(raw_turns)
    return [str(turn) for turn in raw_turns]


def call_api(prompt: str, options: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """promptfoo entry point."""
    provider_config = dict((options or {}).get("config") or {})
    default_employee_id = provider_config.get("default_employee_id", DEFAULT_LOGGED_IN_EMPLOYEE)
    settings_config = {k: v for k, v in provider_config.items() if k not in _PROVIDER_ONLY_KEYS}

    test_vars = (context or {}).get("vars") or {}
    employee_id = str(test_vars.get("employee_id") or default_employee_id)
    turns = _parse_turns(test_vars.get("turns")) or [prompt]

    try:
        agent = _get_agent(settings_config)
        result = agent.run_conversation(turns, employee_id)
    except Exception as exc:  # surface as a promptfoo error row instead of crashing the run
        return {"error": f"{type(exc).__name__}: {exc}"}

    usage = result.get("usage", {})
    return {
        "output": result,
        "tokenUsage": {
            "prompt": usage.get("input_tokens", 0),
            "completion": usage.get("output_tokens", 0),
            "total": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        },
        "cost": result.get("cost_usd", 0.0),
        # Report the latency of the ORIGINAL (uncached) run. promptfoo prefers a
        # provider-reported latencyMs, so the `latency` assertion and the latency
        # column stay meaningful even when the demo replays from cache.
        "latencyMs": result.get("latency_ms", 0),
        "cached": bool(result.get("served_from_cache")),
    }


# ------------------------------------------------------------------------------
# Manual test without promptfoo:
#   python helpdesk_agent/promptfoo_provider.py "How many leave days can I carry forward?"
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    question = " ".join(sys.argv[1:]) or "How many unused leave days can I carry forward?"
    response = call_api(question, {"config": {}}, {"vars": {}})
    print(json.dumps(response, indent=2))
