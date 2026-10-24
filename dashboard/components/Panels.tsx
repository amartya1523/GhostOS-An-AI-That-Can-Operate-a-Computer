"use client";

import { useEffect, useRef } from "react";

import { api } from "@/lib/api";
import type { Approval, GhostEvent, Healing, RunView, StepView } from "@/lib/state";

const STATUS_ICON: Record<StepView["status"], string> = {
  pending: "○",
  running: "◌",
  passed: "✓",
  healed: "✚",
  failed: "✕",
  skipped: "–",
};

export function PlanPanel({ steps }: { steps: StepView[] }) {
  if (!steps.length) return <p className="muted">No plan yet. Give GhostOS a task.</p>;
  return (
    <ol className="plan">
      {steps.map((s, i) => (
        <li key={s.id} className={`step ${s.status}`}>
          <span className="icon">{STATUS_ICON[s.status]}</span>
          <div className="step-body">
            <div className="step-intent">
              <span className="idx">{i + 1}</span> {s.intent}
            </div>
            <div className="step-meta">
              <code>{s.tool}</code>
              <span className={`risk risk-${s.risk}`}>{s.risk}</span>
              {s.attempts > 1 && <span className="attempts">×{s.attempts}</span>}
              {s.status === "healed" && <span className="healed-tag">self-healed</span>}
            </div>
          </div>
        </li>
      ))}
    </ol>
  );
}

export function LiveScreen({ run }: { run: RunView }) {
  const heal = run.healings[0];
  return (
    <div className="screen">
      <div className="screen-bar">
        <span className="dot r" />
        <span className="dot y" />
        <span className="dot g" />
        <span className="screen-caption">{run.frameCaption || "waiting for the browser…"}</span>
      </div>
      <div className="screen-body">
        {run.frame ? <img src={run.frame} alt="live browser frame" /> : <div className="screen-empty">◆</div>}
        {heal && run.status === "running" && run.activeAgent === "healer" && (
          <div className="heal-flash">
            <b>UI changed</b> — healed “{heal.label}” via {heal.strategy}
          </div>
        )}
      </div>
    </div>
  );
}

export function HealingFeed({ healings }: { healings: Healing[] }) {
  if (!healings.length) return <p className="muted">No healing needed (yet).</p>;
  return (
    <div className="heals">
      {healings.map((h, i) => (
        <figure key={`${h.label}-${i}`} className="heal">
          <a href={api.artifact(h.screenshot)} target="_blank" rel="noreferrer">
            <img src={api.artifact(h.screenshot)} alt={`annotated screenshot for ${h.label}`} />
          </a>
          <figcaption>
            <div className="heal-title">“{h.label}”</div>
            <div className="sel">
              <s>{h.oldSelector ?? "?"}</s> → <code>{h.newSelector}</code>
            </div>
            <div className="heal-meta">
              <span className="chip">{h.strategy}</span>
              <span className="chip">conf {h.confidence.toFixed(2)}</span>
              {h.uiChange !== null && <span className="chip">screen Δ {(h.uiChange * 100).toFixed(0)}%</span>}
            </div>
          </figcaption>
        </figure>
      ))}
    </div>
  );
}

const AGENT_COLOR: Record<string, string> = {
  planner: "#38bdf8",
  operator: "#c084fc",
  verifier: "#4ade80",
  healer: "#fbbf24",
  guardian: "#f87171",
  coder: "#60a5fa",
  system: "#94a3b8",
};

export function EventLog({ log }: { log: GhostEvent[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.scrollTo({ top: ref.current.scrollHeight, behavior: "smooth" });
  }, [log.length]);
  return (
    <div className="log" ref={ref}>
      {log.map((e, i) => (
        <div key={i} className="log-row">
          <span className="log-agent" style={{ color: AGENT_COLOR[e.agent] }}>
            {e.agent}
          </span>
          <span className="log-type">{e.type}</span>
          <span className="log-msg">{e.message}</span>
        </div>
      ))}
    </div>
  );
}

export function PatchView({ patches, tests }: Pick<RunView, "patches" | "tests">) {
  if (!patches.length && !tests) return null;
  return (
    <div className="patches">
      {tests && (
        <div className={`tests ${tests.failed ? "red" : "green"}`}>
          tests: <b>{tests.passed}</b> passed · <b>{tests.failed}</b> failed
        </div>
      )}
      {patches.map((p, i) => (
        <div key={i} className="patch">
          <div className="patch-head">
            attempt {i + 1}: {p.diagnosis}
          </div>
          <pre>
            {p.diff.split("\n").map((line, j) => (
              <span
                key={j}
                className={line.startsWith("+") && !line.startsWith("+++") ? "add" : line.startsWith("-") && !line.startsWith("---") ? "del" : ""}
              >
                {line + "\n"}
              </span>
            ))}
          </pre>
        </div>
      ))}
    </div>
  );
}

export function ApprovalModal({ approval }: { approval: Approval | null }) {
  if (!approval) return null;
  return (
    <div className="modal-backdrop">
      <div className="modal" role="dialog" aria-modal="true" aria-label="approval needed">
        <div className="modal-kicker">Guardian · human approval required</div>
        <h3>{approval.summary}</h3>
        <p>
          risk <span className={`risk risk-${approval.risk}`}>{approval.risk}</span> — {approval.reason}
        </p>
        <div className="modal-actions">
          <button className="btn ghost" onClick={() => api.decide(approval.id, false)}>
            Reject
          </button>
          <button className="btn primary" onClick={() => api.decide(approval.id, true)}>
            Approve
          </button>
        </div>
      </div>
    </div>
  );
}
