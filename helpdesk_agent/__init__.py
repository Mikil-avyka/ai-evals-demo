"""
Acme HR & IT Helpdesk Copilot — the "system under test" for the promptfoo evals demo.

The agent combines three things that real enterprise assistants do, so the demo
can show evals at every layer:

    1. RAG          — retrieves passages from company policy documents (knowledge_base/)
    2. Tool calling — reads/writes a mock HR system (data/hr_database.json)
    3. Guardrails   — access control, grounded answers, structured JSON output

Every planted bug is a named flag in `settings.AgentSettings`, so each promptfoo
lab can run a "buggy" and a "fixed" build of the same agent side by side.
"""

from helpdesk_agent.settings import AgentSettings

__all__ = ["AgentSettings"]
