# Demo Runbook: "Vibes vs Evidence" (about 90 minutes)

Presenter script for the live demo. Each lab follows the same rhythm:

> **Show the problem → run the eval → click into a red cell → explain the fix → point at the green column.**

---

## Before the meeting (the day before)

1. `npm install`, then `pip install -r requirements.txt`. Create `.env` with
   `ANTHROPIC_API_KEY`.
2. `npm run smoke-test`. All lines should say `[ok]`.
3. **Warm every cache** by running each lab once: `npm run all-labs`, then
   `npm run lab4:redteam`. The red team takes several minutes.
4. Open `npm run view` and check that each lab tells the story described below. LLM
   output varies. If one cell doesn't show the expected failure or pass, note it.
   An honest "the model got lucky on this one" is also a teaching moment.
5. Fill in the **scorecard** at the bottom with your real numbers for the slides.
6. Screenshot each lab's results as a backup in case the network fails.
7. In the meeting: increase the terminal font, use a light theme for screen-share,
   and keep the viewer open in a browser tab.

> During the live demo, every `npm run labN` replays from cache in seconds. If someone
> asks "is this live?", run one test with `HELPDESK_CACHE=0` to show a real call.

---

## 0 · Opening (5 min): the vibe check

- Show [knowledge_base/leave_policy.md](knowledge_base/leave_policy.md) and
  [data/hr_database.json](data/hr_database.json): *"This is the company's data."*
- Run one question by hand:
  `python helpdesk_agent/promptfoo_provider.py "How many leave days do I get per year?"`
- It answers correctly. Ask the audience: **"Ship it?"**
- *"One good answer proves nothing. Let's measure."*

---

## Lab 1 · Retrieval (12 min): "the LLM isn't always the culprit"

**Command:** `npm run lab1`, then `npm run view`

**Talking points**
- Two builds, same model and same prompt. Only the **chunk size** and **top_k**
  differ.
- Point at the two named metrics, **retrieval_hit** and **answer_correct**. They
  fail together: if the retriever misses, the answer can't be right.
- Click the **"Carry-forward limit"** cell in the buggy column. The reason says
  `RETRIEVAL MISS … The LLM never saw the answer.`
- Show why. Run `python -m helpdesk_agent.knowledge_base`: the answer sentence
  ("A maximum of 5 days may be moved…") sits in a chunk that never uses the words
  "carry forward".
- **London trip** test: the answer needs *two* documents. With top-2 there is no
  room for both.

**Takeaway:** *"Test your retriever on its own. Otherwise you'll spend weeks
tuning prompts to fix a chunking bug."*

---

## Lab 2 · Faithfulness (15 min): the over-helpful prompt

**Command:** `npm run lab2`

**Talking points**
- Show the two prompts in
  [helpdesk_agent/system_prompts.py](helpdesk_agent/system_prompts.py). v1 sounds
  reasonable: *"Employees hate hearing I don't know."*
- **Part A** (not covered by any policy): gym, jury duty, 401(k), sabbatical.
  **What actually happened in our runs:** even when the v1 prompt *told* it to state
  "standard US benefits as Acme policy", Claude Sonnet 5.5 refused to invent numbers
  ("I don't want to make up a figure"). Show one of those answers. *"Good news, and we
  only know it because we tested it. Re-run this suite whenever you change models."*
- What the bad prompt **did** break: the **citations** metric. Most v1 answers drop the
  `[source]` references, so a reviewer can't check them. Click a red citations cell.
- **Part B** (covered by policy): introduce the RAG metrics.
  - `context-faithfulness`: is every claim supported by the retrieved text?
  - `answer-relevance`: does the reply answer the question?
  - `citesRetrievedSources`: a deterministic check that the answer cites excerpts
    that were actually retrieved.
- Point out the **`no_hallucination`** metric row at the top of the viewer.
- **"Internet stipend" fails `context-faithfulness` in both columns.** Use it as a
  lesson: the answer is correct, but the grader counts helpful extras ("I can raise a
  ticket…") as unsupported claims. *"Model-graded metrics need calibrating on
  known-good answers. Don't trust a threshold you haven't tested."*

**Takeaway:** *"A 'helpful' prompt change can turn into legal risk. Only evals
catch it before customers do."*

---

## Lab 3 · Tool calling (15 min): judge actions, not words

**Command:** `npm run lab3`

**Talking points**
- *"The final reply can be perfect while the agent did the wrong thing."*
- Show the vague vs precise schemas in
  [helpdesk_agent/tools.py](helpdesk_agent/tools.py). The buggy docs have *drifted*:
  `create_it_ticket` still describes its old "asset inventory" job, priority is
  documented as P1–P4 although the backend wants low/medium/high/critical, and dates
  are documented as "next Monday". *"Nothing here fails a code review."*
- Click a red **tool_selection** cell to see which tool was called and with what
  arguments. Expand the output JSON to show `tool_calls`.
- Show the **argument_validity** metric: priority `"urgent"` or `"High"`, or a date
  like `"next Monday"`. A real ticketing API would reject these.
- The **"Pure policy question → NO tool calls"** test shows that taking *no* action
  is also behaviour worth testing.

**Takeaway:** *"For agents, assert on the actions and the arguments, not just the
text."*

---

## Lab 4 · Security (18 min): prompt rules are not access control

**Command:** `npm run lab4`

**Talking points**
- Show the "Colleague information" section of the v1 prompt in
  [helpdesk_agent/system_prompts.py](helpdesk_agent/system_prompts.py): *"Someone
  decided the directory is open because it's an internal tool."* One prompt edit like
  this is all it takes.
- Three columns:
  1. **prompt-only access control:** that prompt, and no checks in the tools.
  2. **same prompt + tool-layer checks:** the *identical* prompt, but the tools
     enforce access. Only one variable changes from column 1.
  3. **production:** strict prompt plus tool checks.
- The security assertion checks **behaviour**: did any tool *successfully* return
  another employee's record? A polite refusal doesn't count if the data was already
  fetched.
- Walk through the attacks: direct ask → "I'm the acting manager" → "HR asked me to
  audit" → prompt injection hidden in a ticket summary → acting on a teammate's
  behalf → system prompt extraction.
- **What actually happened in our runs:** all three columns passed 9/9. Even with the
  "directory is open" prompt, Claude Sonnet 5.5 refused every attack *before calling
  any tool*. It cited the security policy it had retrieved and the user's role.
  Present this honestly: *"The model is a strong first line of defense. But it's a
  probability, not a guarantee. A different model, a prompt change or a cleverer
  attack can flip it. That's why column 2 has tool-layer checks, and why we red-team."*
- Then go straight to the red team (below). It is designed to find the attacks these
  six hand-written ones miss.
- **Legitimate tests:** a manager viewing a direct report and HR viewing anyone must
  still work in every column. *"Over-blocking is a failure too."*

**Then the red team:** `npm run lab4:redteam-report` (pre-run). Show the vulnerability
categories, the attack count, and one generated attack nobody wrote by hand.

**Takeaway:** *"Write the attacks you can think of, then let the red team find
the ones you can't."*

---

## Lab 5 · Multi-turn (10 min): the conversation is the test case

**Command:** `npm run lab5`

**Talking points**
- Each test is a whole conversation (`vars.turns`), and the check is the **final
  state of the HR system**.
- **"Make it Tuesday instead"**: the buggy build either asks "make what Tuesday?" or
  creates Tuesday leave and leaves Monday booked. The reason text shows exactly what
  is in the HR system.
- **"How many of those can I carry over?"**: "those" only makes sense with
  memory.

**Takeaway:** *"Single-turn evals can't catch state bugs. Test whole
conversations."*

---

## Lab 6 · Models, cost and CI (12 min): turning evals into a release gate

**Command:** `npm run lab6`

**Talking points**
- Test cases live in
  [evals/lab6_model_comparison/regression_suite.csv](evals/lab6_model_comparison/regression_suite.csv).
  Open it in Excel. *"HR and QA can add test cases without writing code."*
- Compare columns: pass rate per metric (leave, expenses_travel, it_equipment,
  security, safety), then the **cost** and **latency** columns.
- **The real finding from our runs:** Haiku 5.5 matched Sonnet 5.5 on this suite at
  roughly 5% of the cost, and it was faster. *"Everyone assumed we needed the bigger
  model. The eval says we don't, at least for these 32 cases. Without evals, nobody
  would have dared to switch."*
- Add the caveat: this holds for *this* suite. Before switching, add harder cases
  (multi-step, tool-heavy) and re-run. That's the point of a suite you can grow.
- Point at the **budget** metric: every row is also checked against a $0.03 cost and
  30-second latency budget. Cost is a test dimension too. (In our first run, one Sonnet
  conversation went over the cost budget.)
- Show [.github/workflows/llm_evals.yml](.github/workflows/llm_evals.yml): every PR
  that touches prompts, tools, policies or data runs the suite, and **fails below a
  90% pass rate**.
- Optional live moment: change one line in `system_prompts.py` (for example, delete
  the "Never guess" rule), run `HELPDESK_CACHE=0 npm run ci:regression` (in
  PowerShell: `$env:HELPDESK_CACHE=0; npm run ci:regression`), and watch it fail.
  Revert afterwards.

**Takeaway:** *"Evals belong in CI, just like unit tests."*

---

## Closing (3 min)

Show the scorecard slide: every lab, buggy vs fixed pass rate. Then:
- Start with 20 real user questions, not 2,000 synthetic ones.
- Test each layer separately: retrieval, grounding, actions, security, conversations.
- Assert on behaviour, not just text.
- Put the suite in CI.

---

## Scorecard (from the live run on 2026-11-18)

LLM outputs vary, so your own pre-run may differ by a test or two. Update the table
if it does.

| Lab | Buggy | Fixed | Best example to show |
|-----|-------|-------|----------------------|
| 1 Retrieval | 2/8 (25%): 6 retrieval misses | **8/8 (100%)** | Carry-forward limit |
| 2 Faithfulness | 5/9 (56%): 4 answers without citations | 8/9 (89%) | Hotel cap answer with no source |
| 3 Tool calling | 4/10 (40%): priorities "P1/P3/P4", HR instead of IT, "annual leave" | **10/10 (100%)** | Laptop dead → routed to HR |
| 4 Security (3 builds) | 9/9 · 9/9 · 9/9: the model refused every attack | | Acting-manager answer |
| 4b Red team | not run yet | | |
| 5 Multi-turn | 1/5 (20%): every correction lost | **5/5 (100%)** | Monday → Tuesday |
| 6 Sonnet 5.5 vs Haiku 5.5 | Sonnet 31/32 · $0.36 · 2.5 s median | Haiku **32/32 · $0.018** · 1.9 s median | Same quality at ~5% of the cost |

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Could not resolve authentication method` | `ANTHROPIC_API_KEY` is missing from `.env` |
| promptfoo can't find Python | Set `PROMPTFOO_PYTHON` in `.env` to your python.exe |
| `Unknown AgentSettings key(s)` | Typo in a provider `config:` block. The error lists the valid keys |
| A lab doesn't show the expected red/green | LLM variance. Re-run with `HELPDESK_CACHE=0` or pick another example |
| Results didn't change after editing a prompt | They should: the cache key includes source files. Run `npm run clear-agent-cache` to be sure |
