"use client";

import { useEffect, useState } from "react";

import { AgentGraph } from "@/components/AgentGraph";
import { ApprovalModal, EventLog, HealingFeed, LiveScreen, PatchView, PlanPanel } from "@/components/Panels";
import { api } from "@/lib/api";
import { useGhostStream } from "@/lib/useGhostStream";

interface Preset {
  name: string;
  task: (origin: string) => string;
  provider?: string;
  script?: string;
}

const PRESETS: Preset[] = [
  {
    name: "Invoice flow · browser",
    task: (o) =>
      `open ${o}/sites/acme/, log in as ada@acme.io with password hunter2, click "New invoice", ` +
      `type "Globex" into customer, type "420" into amount, click "Save", verify "created for Globex"`,
  },
  {
    name: "Fix bugs + PR · dev agent",
    task: () => "fix the failing tests in fixtures/buggy_repo and open a pull request",
    provider: "scripted",
    script: "fixtures/buggy_repo/.ghost/script.json",
  },
];

function elapsed(start: number | null, end: number | null): string {
  if (!start) return "—";
  const s = (end ?? Date.now() / 1000) - start;
  return `${s.toFixed(1)}s`;
}

export default function Home() {
  const { run, connected, focus } = useGhostStream();
  const [task, setTask] = useState("");
  const [preset, setPreset] = useState<Preset>(PRESETS[0]);
  const [site, setSite] = useState<"v1" | "v2">("v1");
  const [stats, setStats] = useState<Record<string, number>>({});
  const [, tick] = useState(0);

  useEffect(() => {
    setTask(PRESETS[0].task(window.location.origin.replace(":3000", ":8765")));
    api.siteVersion().then((v) => setSite(v.version as "v1" | "v2")).catch(() => undefined);
  }, []);

  useEffect(() => {
    api.memory().then((m) => setStats(m.stats)).catch(() => undefined);
  }, [run.status, connected]);

  useEffect(() => {
    if (run.status !== "running") return;
    const t = setInterval(() => tick((n) => n + 1), 200);
    return () => clearInterval(t);
  }, [run.status]);

  const choose = (p: Preset) => {
    setPreset(p);
    setTask(p.task(window.location.origin.replace(":3000", ":8765")));
  };

  const start = async () => {
    const { run_id } = await api.startRun(task, preset.provider, preset.script);
    focus(run_id);
  };

  const toggleSite = async () => {
    const next = site === "v1" ? "v2" : "v1";
    await api.deploy(next);
    setSite(next);
  };

  const healed = run.steps.filter((s) => s.status === "healed").length;
  const done = run.steps.filter((s) => s.status === "passed" || s.status === "healed").length;

  return (
    <main className="app">
      <header className="top">
        <div className="brand">
          <span className="ghost">◆</span> GhostOS
          <span className="tagline">an AI that operates a computer</span>
        </div>
        <div className="top-right">
          {Object.entries(stats).map(([k, v]) => (
            <span key={k} className="chip">
              {k} <b>{v}</b>
            </span>
          ))}
          <span className={`conn ${connected ? "on" : ""}`}>{connected ? "live" : "offline"}</span>
        </div>
      </header>

      <section className="command card">
        <div className="presets">
          {PRESETS.map((p) => (
            <button key={p.name} className={`pill ${p === preset ? "on" : ""}`} onClick={() => choose(p)}>
              {p.name}
            </button>
          ))}
          <button className={`pill deploy ${site}`} onClick={toggleSite} title="Swap the demo site's UI">
            demo site: <b>{site === "v1" ? "v1 classic" : "v2 redesign"}</b> ⇄
          </button>
        </div>
        <div className="command-row">
          <textarea value={task} onChange={(e) => setTask(e.target.value)} rows={2} aria-label="task" />
          <button className="btn primary run" onClick={start} disabled={run.status === "running"}>
            {run.status === "running" ? "Running…" : "Run ▸"}
          </button>
        </div>
      </section>

      <section className="grid">
        <div className="col">
          <div className="card">
            <h2>Plan</h2>
            <div className="run-meta">
              <span className={`status ${run.status}`}>{run.status}</span>
              <span>
                {done}/{run.steps.length} steps
              </span>
              {healed > 0 && <span className="healed-tag">{healed} self-healed</span>}
              <span>{elapsed(run.startedAt, run.finishedAt)}</span>
            </div>
            <PlanPanel steps={run.steps} />
            {run.summary && <p className="summary">{run.summary}</p>}
          </div>
          <div className="card">
            <h2>Agents</h2>
            <AgentGraph active={run.activeAgent} />
            <div className="run-meta small">
              <span>llm: {run.provider || "—"}</span>
              <span>sandbox: {run.sandbox || "—"}</span>
            </div>
          </div>
        </div>

        <div className="col wide">
          <div className="card">
            <h2>Live screen</h2>
            <LiveScreen run={run} />
          </div>
          <PatchView patches={run.patches} tests={run.tests} />
          <div className="card">
            <h2>Self-healing</h2>
            <HealingFeed healings={run.healings} />
          </div>
        </div>

        <div className="col">
          <div className="card fill">
            <h2>Event stream</h2>
            <EventLog log={run.log} />
          </div>
        </div>
      </section>

      <ApprovalModal approval={run.approval} />
    </main>
  );
}
