# Helpdesk Copilot — LLM Evals Demo with promptfoo

A live demo showing **why LLM apps need evals**. It evaluates a realistic AI agent
(an HR & IT helpdesk copilot for the fictional *Acme Corp*) with
[promptfoo](https://www.promptfoo.dev/). The demo has six labs, and each lab plants
one realistic bug. Every lab runs the **buggy** and the **fixed** build side by
side, so the promptfoo viewer shows the failures (red) next to the fix (green).

| Lab | Layer tested | Planted bug | What the eval catches |
|-----|--------------|-------------|------------------------|
| 1 | Retrieval (RAG) | 80-word chunks, top-2 | The answer passage is never retrieved |
| 2 | Faithfulness | "Always give a specific answer" prototype prompt | Invented benefits, no citations |
| 3 | Tool calling | Tool docs drifted from the backend | Laptop issues routed to HR, invalid priorities and dates |
| 4 | Security | Prompt-only access control | Other employees' data leaked through tools |
| 5 | Multi-turn | No conversation memory | "Make it Tuesday instead" leaves Monday booked |
| 6 | Model choice + CI | (none) Sonnet vs Haiku | Quality vs cost vs latency, plus a CI pass-rate gate |

The presenter script is in **[DEMO_RUNBOOK.md](DEMO_RUNBOOK.md)**.

---

## Architecture

```
                        ┌──────────────────────── promptfoo ────────────────────────┐
  evals/labN/*.yaml ──▶ │ test cases × providers (buggy | fixed) → assertions → UI │
                        └───────────────┬───────────────────────────────▲──────────┘
                                        │ call_api(prompt, config, vars)│ {answer, citations,
                                        ▼                               │  tool_calls, contexts…}
                     helpdesk_agent/promptfoo_provider.py ──────────────┘
                                        │  AgentSettings.from_config(config)  ← bug flags
                                        ▼
  ┌─────────────────────────── HelpdeskAgent (agent.py) ───────────────────────────┐
  │  1. BM25 retrieval over knowledge_base/*.md   (chunk_size_words, top_k)  Lab 1 │
  │  2. Claude Messages API, system prompt v1|v2  (prompt_version)           Lab 2 │
  │  3. Tool-use loop, schemas vague|precise      (tool_schema_style)        Lab 3 │
  │  4. Tools → HRDatabase, access checks on|off  (enforce_access_control)   Lab 4 │
  │  5. Multi-turn history on|off                 (use_conversation_memory)  Lab 5 │
  │  6. Model: claude-sonnet-5-5 | claude-haiku-5-5  (model)                 Lab 6 │
  └────────────────────────────────────────────────────────────────────────────────┘
```

The provider evaluates the **whole agent**, not a bare prompt. Assertions can then
check the answer text **and** what the agent actually did: which passages it
retrieved, which tools it called with which arguments, whether access was denied,
and the final state of the HR system.

## Repository layout

```
helpdesk_agent/            The system under test (Python)
  settings.py              AgentSettings: every bug flag, documented. Defaults = fixed build
  knowledge_base.py        Chunking + BM25 retriever (pure Python, deterministic)
  mock_hr_database.py      In-memory HR data per conversation + access-control rule
  tools.py                 Tool schemas (vague vs precise) + implementations + call log
  system_prompts.py        v1_overhelpful vs v2_grounded system prompts
  agent.py                 Retrieval → Claude tool-use loop → structured result
  response_cache.py        On-disk result cache for stable, instant demo re-runs
  promptfoo_provider.py    promptfoo custom provider entry point (call_api)
  offline_smoke_test.py    Checks everything without calling the API
knowledge_base/            6 Acme policy documents (the RAG corpus)
data/hr_database.json      8 fictional employees, leave balances, tickets
evals/
  assertions/helpdesk_assertions.js   Reusable behavioural assertions
  lab1_retrieval/ … lab6_model_comparison/   One promptfoo config per lab
  lab4_security/redteam_config.yaml          Automated red teaming
  lab6_model_comparison/regression_suite.csv Test cases editable in Excel
.github/workflows/llm_evals.yml   CI gate: fail the PR if pass rate < 90%
```

## Setup

Requirements: Node.js 20+ and Python 3.10+.

```bash
npm install                       # installs promptfoo (pinned in package.json)
pip install -r requirements.txt   # anthropic SDK + python-dotenv
cp .env.example .env              # then set ANTHROPIC_API_KEY in .env
npm run smoke-test                # offline checks, no API key needed
```

On Windows, if promptfoo can't find Python, set `PROMPTFOO_PYTHON` in `.env`
(see `.env.example`).

## Running

```bash
npm run lab1          # … lab6. Each evaluates the buggy vs the fixed build
npm run view          # open the promptfoo web viewer (results of every run)
npm run lab4:redteam  # automated red team (slow: pre-run it)
npm run lab4:redteam-report
npm run ci:regression # what CI runs: the Lab 6 suite against the production config
npm run validate      # schema-check every config
npm run clear-agent-cache
```

### About the response cache

Current Claude models don't accept a `temperature` setting, so answers can vary a
little between runs. For a reliable live demo, the agent caches each complete
result in `.cache/agent_responses/` (see `response_cache.py`). Run every lab once
before presenting. Re-runs on stage are then instant, free and identical. Set
`HELPDESK_CACHE=0` to force live calls. CI does this.

Graded assertions (`llm-rubric`, `context-faithfulness`, …) go through promptfoo's
own cache. Its viewer keeps every past run anyway.

## Costs

Each lab makes roughly 15–40 agent conversations (each with 1–4 Claude calls) plus
grader calls on Claude Sonnet 5.5, with `effort: low`. With the cache warm,
re-runs cost nothing. The red team is the expensive part. Keep `numTests` small.

## Extending

- **Add a test:** copy a block in a lab YAML, or add a row to
  `regression_suite.csv`.
- **Add a bug flag:** add a field to `AgentSettings`, use it in the agent, and set
  it in a provider's `config:`. Unknown keys fail loudly, so typos are caught.
- **Use your own app:** replace `HelpdeskAgent` with your own system. Keep the
  `call_api` contract and return the fields your assertions need.

All people, data and policies in this repository are fictional.
