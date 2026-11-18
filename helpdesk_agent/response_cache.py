"""
On-disk cache of complete agent results — a demo-reliability feature.

Why: during a live presentation you want re-runs to be instant, free and identical.
The current Claude models do not accept a `temperature` setting, so outputs vary
slightly from run to run; caching the first (pre-recorded) run gives you a stable
demo. Run every lab once BEFORE the meeting to warm the cache.

The cache key covers everything that can change the result: agent settings, the
conversation turns, the logged-in employee, and a fingerprint of the knowledge
base + HR data + prompt/tool source code. Edit a policy document or a prompt and
the old entries are simply never hit again.

Disable with env HELPDESK_CACHE=0. Clear with `npm run clear-agent-cache`
(= `python -m helpdesk_agent.response_cache --clear`).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / ".cache" / "agent_responses"

# Files whose content changes the agent's behaviour. If any change, the key changes.
_FINGERPRINT_GLOBS = [
    "knowledge_base/*.md",
    "data/*.json",
    "helpdesk_agent/agent.py",
    "helpdesk_agent/system_prompts.py",
    "helpdesk_agent/tools.py",
    "helpdesk_agent/knowledge_base.py",
    "helpdesk_agent/mock_hr_database.py",
]


def is_cache_enabled() -> bool:
    return os.environ.get("HELPDESK_CACHE", "1") != "0"


@lru_cache(maxsize=1)
def _source_fingerprint() -> str:
    digest = hashlib.sha256()
    for pattern in _FINGERPRINT_GLOBS:
        for path in sorted(PROJECT_ROOT.glob(pattern)):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def make_cache_key(payload: dict[str, Any]) -> str:
    """Stable hash of the request payload + source fingerprint."""
    canonical = json.dumps(
        {"payload": payload, "source": _source_fingerprint()}, sort_keys=True, default=str
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def read_cached_result(cache_key: str) -> dict[str, Any] | None:
    path = CACHE_DIR / f"{cache_key}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None  # a corrupt entry is treated as a miss


def write_cached_result(cache_key: str, result: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = CACHE_DIR / f"{cache_key}.tmp"
    temp_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    temp_path.replace(CACHE_DIR / f"{cache_key}.json")  # atomic on the same volume


if __name__ == "__main__":
    if "--clear" in sys.argv:
        shutil.rmtree(CACHE_DIR, ignore_errors=True)
        print(f"Cleared agent response cache at {CACHE_DIR}")
    else:
        count = len(list(CACHE_DIR.glob("*.json"))) if CACHE_DIR.exists() else 0
        print(f"{count} cached agent results in {CACHE_DIR}")
