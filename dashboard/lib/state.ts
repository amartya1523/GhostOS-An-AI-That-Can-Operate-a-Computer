// Folds the GhostOS event stream into the state the dashboard renders.

export type Agent = "planner" | "operator" | "verifier" | "healer" | "guardian" | "coder" | "system";

export interface GhostEvent {
  run_id: string;
  type: string;
  agent: Agent;
  message: string;
  data: Record<string, any>;
  ts: number;
}

export type StepStatus = "pending" | "running" | "passed" | "healed" | "failed" | "skipped";

export interface StepView {
  id: string;
  intent: string;
  tool: string;
  risk: string;
  status: StepStatus;
  attempts: number;
  detail?: string;
}

export interface Healing {
  label: string;
  strategy: string;
  confidence: number;
  uiChange: number | null;
  oldSelector: string | null;
  newSelector: string;
  screenshot: string;
  message: string;
}

export interface Approval {
  id: string;
  summary: string;
  risk: string;
  reason: string;
}

export interface RunView {
  runId: string | null;
  task: string;
  provider: string;
  sandbox: string;
  status: "idle" | "running" | "succeeded" | "failed";
  summary: string;
  steps: StepView[];
  activeAgent: Agent | null;
  frame: string | null;
  frameCaption: string;
  healings: Healing[];
  approval: Approval | null;
  patches: { diagnosis: string; diff: string }[];
  tests: { passed: number; failed: number } | null;
  log: GhostEvent[];
  startedAt: number | null;
  finishedAt: number | null;
  /** A run this tab started; the view switches to it as soon as it begins. */
  wanted: string | null;
}

/** Dispatched locally when this tab starts a run, so the view follows *that* run. */
export interface FocusAction {
  type: "focus";
  run_id: string;
}

export const initialRun: RunView = {
  runId: null,
  task: "",
  provider: "",
  sandbox: "",
  status: "idle",
  summary: "",
  steps: [],
  activeAgent: null,
  frame: null,
  frameCaption: "",
  healings: [],
  approval: null,
  patches: [],
  tests: null,
  log: [],
  startedAt: null,
  finishedAt: null,
  wanted: null,
};

const HIDDEN_FROM_LOG = new Set(["node.exit", "browser.frame", "graph.topology", "heal.candidates"]);

function patchStep(steps: StepView[], index: number, patch: Partial<StepView>): StepView[] {
  return steps.map((s, i) => (i === index ? { ...s, ...patch } : s));
}

export function reduce(run: RunView, action: GhostEvent | FocusAction): RunView {
  if (action.type === "focus") return { ...run, wanted: action.run_id };
  const e = action as GhostEvent;
  if (e.type === "run.started") {
    // Never yank the view away from a live run (and its approval modal) unless this tab asked for it.
    const busy = run.status === "running" && run.runId !== null;
    if (busy && run.wanted !== e.run_id) return run;
    return {
      ...initialRun,
      runId: e.run_id,
      task: e.message,
      provider: e.data.provider,
      sandbox: e.data.sandbox,
      status: "running",
      startedAt: e.ts,
      log: [e],
    };
  }
  if (!run.runId || e.run_id !== run.runId) return run;

  const log = HIDDEN_FROM_LOG.has(e.type) || (e.type === "node.enter" && e.data.node !== "plan")
    ? run.log
    : [...run.log, e].slice(-250);
  const next: RunView = { ...run, log };

  switch (e.type) {
    case "node.enter":
      return { ...next, activeAgent: e.agent };
    case "plan.created": {
      const offset: number = e.data.offset ?? 0;
      const fresh: StepView[] = e.data.steps.map((s: any) => ({ ...s, status: "pending", attempts: 0 }));
      return { ...next, steps: [...run.steps.slice(0, offset), ...fresh] };
    }
    case "step.start":
      return {
        ...next,
        activeAgent: "operator",
        steps: patchStep(run.steps, e.data.index, { status: "running", attempts: e.data.attempt, detail: e.data.summary }),
      };
    case "step.verdict":
      return { ...next, steps: patchStep(run.steps, e.data.index, { status: e.data.status }) };
    case "browser.frame":
      return { ...next, frame: `data:image/png;base64,${e.data.image}`, frameCaption: e.message };
    case "heal.detect":
    case "heal.candidates":
    case "heal.decision":
      return { ...next, activeAgent: "healer" };
    case "heal.success":
      return {
        ...next,
        activeAgent: "healer",
        healings: [
          {
            label: e.data.label,
            strategy: e.data.strategy,
            confidence: e.data.confidence,
            uiChange: e.data.ui_change,
            oldSelector: e.data.old_selector,
            newSelector: e.data.new_selector,
            screenshot: e.data.screenshot,
            message: e.message,
          },
          ...run.healings,
        ],
      };
    case "approval.request":
      return {
        ...next,
        activeAgent: "guardian",
        approval: { id: e.data.approval_id, summary: e.message, risk: e.data.risk, reason: e.data.reason },
      };
    case "approval.resolved":
      return { ...next, approval: null };
    case "coder.thinking":
      return { ...next, activeAgent: "coder" };
    case "code.patch":
      return { ...next, activeAgent: "coder", patches: [...run.patches, { diagnosis: e.message, diff: e.data.diff }] };
    case "tests.report":
      return { ...next, activeAgent: "verifier", tests: { passed: e.data.passed, failed: e.data.failed } };
    case "run.finished":
      return { ...next, status: e.data.status, summary: e.message, activeAgent: null, finishedAt: e.ts };
    default:
      return next;
  }
}
