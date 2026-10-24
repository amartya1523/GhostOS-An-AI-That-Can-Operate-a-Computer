# Research notes: designing GhostOS

These notes record the questions asked before writing code, and how each answer shaped the design.

## Q1. How do current computer-use agents perceive the screen?

Two families:

* **Pixel-first** agents (screenshot → multimodal model → coordinates). They work on any surface
  (canvas, native apps), but every step is a slow, expensive and non-deterministic model call.
* **Structure-first** agents (DOM / accessibility tree → element references). They are fast and precise in
  browsers, but blind where there is no structure.

**Decision:** structure-first for speed and determinism, with pixels as *evidence* (screenshot diffs,
annotated healings) and an optional vision rung as a last resort. The fingerprint is defined over
properties that accessibility trees also expose (role, name, position, order), so the same idea can carry
over to desktop apps later.

## Q2. Why do automation scripts break, and how do "self-healing" tools fix it?

Scripts bind to one brittle fact (an id, an XPath). Self-healing test tools record more about each element
and fall back to similarity search when the primary locator fails. Comparisons of these tools list healing
signals such as DOM structure, tag, attributes and element position.

**Decision:** a weighted multi-signal fingerprint, plus three things those tools typically lack:
1. a **ratio test** against the runner-up, so the agent refuses to guess on ambiguous screens;
2. **evidence** for every healing (screenshot diff, annotated image, per-signal breakdown);
3. **memory**: a healing updates the stored selector, so the second run is a plain lookup and the healing
   becomes a recallable lesson.

## Q3. What makes long-horizon agent tasks fail?

Benchmarks such as WebArena (self-hosted websites with functional, state-based scoring) and OSWorld (real
desktop tasks) consistently show agents failing through error compounding. One wrong step is never
noticed, and every later step builds on it. OSWorld 2.0 (June 2026) pushes the average horizon past 250
steps, which makes this worse.

**Decisions:**
* a dedicated **Verifier** that checks post-conditions against the world, not the tool's own claim;
* a **Healer** that classifies failures instead of blindly retrying;
* **self-correction with evidence**: the verifier's output is appended to the coder's next prompt;
* bounded budgets (attempts, replans, graph transitions), so failure is explicit, never an infinite loop.

## Q4. How should an agent that runs code and clicks buttons be kept safe?

Threats: destructive commands, secret leakage via the environment, network exfiltration, irreversible
external actions (pushing, opening PRs, sending messages), and prompt injection from page content.

**Decisions:**
* **Least privilege by default**: Docker with no network, a read-only root and all capabilities dropped;
  a scrubbed environment in the process fallback.
* **Risk-scored tools + policy + human approval** for anything irreversible. Approval is a first-class
  event, visible in the dashboard and the trace.
* **Structural guardrails for the coder**: it may not edit tests, edits must match exactly once, and paths
  may not escape the workspace.
* Page content is only ever *data* to the planner. The deterministic grammar handles the common paths
  without a model in the loop.

## Q5. Framework or hand-rolled?

LangGraph, browser-use and similar frameworks are excellent, but for a system whose selling point is
*observable, explainable* control flow, a small owned core is easier to test and to explain line by line.
The graph engine is LangGraph-shaped, so migrating is mechanical if ever needed.

## Sources consulted

* OSWorld 2.0: Benchmarking Computer Use Agents on Long-Horizon Real-World Tasks — arXiv:2606.29537
* WebArena benchmark overviews (self-hosted sites, functional evaluation)
* Overviews of computer-use agent approaches (structure-first vs pixel-first), 2026
* Comparisons of self-healing Selenium tooling (healing signals: DOM structure, tag, attributes, position)
* Lowe, D. — *Distinctive Image Features from Scale-Invariant Keypoints* (2004): the ratio test
