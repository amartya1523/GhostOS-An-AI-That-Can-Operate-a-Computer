# GhostOS architecture

This document follows one task through the system and explains the design decisions on the way.

## 1. A run, end to end

```
ghost run "open …/acme, log in as ada@acme.io with password hunter2, click \"Save\", verify \"created\""
```

1. **`GhostOS.run`** ([runtime.py](../ghostos/runtime.py)) creates a run id, a run directory
   (`$GHOSTOS_HOME/runs/<id>/` with `trace.jsonl`, screenshots and a workspace), picks an LLM provider
   (or none) and a sandbox, and builds a `ToolContext`: everything a tool may touch, passed explicitly.
2. **The orchestrator** ([agents/orchestrator.py](../ghostos/agents/orchestrator.py)) runs a `StateGraph`
   with five nodes: `plan → act → verify → (act | heal | finish)`, `heal → (act | plan | finish)`.
3. **Plan.** The planner recalls related *lessons* from semantic memory, then produces a `Plan` of `Step`s.
   Each step names a tool, typed arguments, and optionally an `Expectation` (post-condition).
4. **Act.** For each step the operator validates the arguments against the tool's pydantic model, computes
   the risk (some tools derive it from their arguments), asks the **permission gate**, and runs the tool.
   Any exception becomes an `Observation(ok=False, error_kind="crash")`, so a tool bug never takes down the run.
5. **Verify.** The verifier checks the post-condition against the world, not against the tool's own report.
6. **Heal.** On failure, the healer picks a recovery based on the failure *kind* (see §4).
7. **Finish.** A summary is written, failures become lessons, and `run.finished` is emitted.

Every transition emits an `Event`. The bus fans events out to the WebSocket (dashboard), the run's
`trace.jsonl`, and the SQLite `events` table. Nothing the agents do is invisible.

## 2. Why a hand-written state graph?

GhostOS needs only the control-flow core of LangGraph: nodes, conditional routing, a loop guard and hooks.
Owning about 130 lines buys three things:

* **Testability.** Routers are plain functions of state, and `tests/test_graph.py` covers looping, invalid
  routes, dangling nodes and runaway loops.
* **Observability.** The `hook` is how the dashboard lights up the active agent.
* **No lock-in.** The node functions are ordinary async methods and port to LangGraph unchanged.

## 3. Self-healing locator

Implemented in [perception/](../ghostos/perception). Targets are always **human labels** ("Sign in"),
never selectors, so plans survive UI changes by construction. Resolution:

1. Extract every visible interactive element with one in-page script (`EXTRACT_JS`): tag, type, role, id,
   name, autocomplete, classes, text, aria-label, placeholder, associated `<label>`, context (preceding
   label, section heading, container), order among its form's controls, normalised position, bbox, and a
   *unique* selector (id → data-testid → name → shortest unique nth-of-type path).
2. Climb the ladder: memory → semantic → fingerprint → fuzzy-text → vision.
3. On a heal, compare the stored screenshot with the current one (dHash Hamming distance + changed-pixel
   ratio on a 160×100 grid), render an annotated screenshot, and emit `heal.success` with the evidence.
4. After the action succeeds (and only then), `learn()` upserts the fingerprint and records the healing and a lesson.

**Why a ratio test?** Absolute thresholds on a hand-weighted similarity are brittle: a field that kept
its `autocomplete` but changed label, id, classes and position may only score 0.47, yet be unambiguous.
A genuinely ambiguous screen can have two candidates at 0.55. Requiring a *lead* over the runner-up
(like Lowe's ratio test for SIFT matches) accepts the first and refuses the second.

## 4. Failure taxonomy → recovery

| `error_kind` | Example | Healer decision |
|---|---|---|
| `denied`, `no_llm` | policy refused, no model configured | give up immediately (retrying can't help) |
| `locator_not_found`, `timeout`, `navigation`, `action_failed`, `http` | element still rendering, flaky network | retry with exponential back-off (300 → 600 → 1200 ms) |
| `assertion` | text not visible yet | retry with a doubled timeout |
| *(code.fix + tests still failing)* | incomplete patch | retry the coder with the verifier's evidence appended to `feedback` |
| budget exhausted | — | replan with an LLM (the failed step is described to it), else give up with a reason |

Replanning deliberately skips the deterministic grammar, which would produce the same failing plan.

## 5. Memory

One SQLite file ([memory/store.py](../ghostos/memory/store.py)), WAL mode, thread-safe writes:

| Table | Kind | Used by |
|---|---|---|
| `fingerprints` (site, label) → selector, fingerprint, screenshot, hits | procedural | locator rung 1 and 3 |
| `healings` | episodic | dashboard, `ghost memory`, audits |
| `runs`, `events` | episodic | dashboard history, replay |
| `lessons` (+ embedding BLOB) | semantic | planner context, recalled by cosine similarity |

Embeddings come from a feature-hashing embedder (word unigrams and bigrams plus character trigrams,
signed hashing, L2-normalised). It is deterministic, needs no downloads, and is good enough to recall
"I failed on something like this before". The `Embedder` protocol lets you swap in a neural model, and the
schema maps 1:1 onto PostgreSQL + pgvector.

## 6. Safety

* **Risk levels** are ordered (`read < write < execute < network < irreversible`) and declared per tool.
  `shell.run` derives its risk from `safety/shell_guard.py`.
* **Policy** decides `allow | ask | deny`. `ask` goes to the `ApprovalBroker`, which either calls a resolver
  (CLI prompt, auto-approve in tests) or waits for `POST /api/approvals/{id}` from the dashboard.
* **Sandboxes** implement one method: `run(command, workspace, limits) -> ExecResult`. The Docker sandbox
  uses `--network none` unless a step needs network (which raises its risk), plus `--read-only`, a tmpfs
  `/tmp`, `--cap-drop ALL`, `no-new-privileges`, uid 1000, and memory/CPU/PID limits.
* **The dev agent** works on a copy of the repository, may not edit test files, applies edits only when
  the search text matches **exactly once**, and resolves every path inside the workspace.

## 7. Dashboard

A static Next.js export served by FastAPI on the same origin, so there is no CORS and no second server. A
single reducer ([dashboard/lib/state.ts](../dashboard/lib/state.ts)) folds the event stream into view
state. The WebSocket reconnects with back-off. Screenshots stream as base64 frames, and annotated
healing evidence is served from `/api/artifact`, which only serves files inside `GHOSTOS_HOME`.
