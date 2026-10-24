import type { Agent } from "@/lib/state";

const NODES: { id: Agent; label: string; role: string; x: number; y: number }[] = [
  { id: "planner", label: "Planner", role: "task → plan", x: 70, y: 60 },
  { id: "operator", label: "Operator", role: "acts", x: 210, y: 60 },
  { id: "verifier", label: "Verifier", role: "checks", x: 350, y: 60 },
  { id: "healer", label: "Healer", role: "recovers", x: 280, y: 170 },
  { id: "guardian", label: "Guardian", role: "permissions", x: 120, y: 170 },
  { id: "coder", label: "Coder", role: "patches", x: 420, y: 170 },
];

const EDGES: [Agent, Agent, string?][] = [
  ["planner", "operator"],
  ["operator", "verifier"],
  ["verifier", "healer", "fail"],
  ["healer", "operator", "retry"],
  ["healer", "planner", "replan"],
  ["operator", "guardian", "risky"],
  ["healer", "coder", "fix"],
];

const pos = (id: Agent) => NODES.find((n) => n.id === id)!;

export function AgentGraph({ active }: { active: Agent | null }) {
  return (
    <svg viewBox="0 0 490 230" className="graph" role="img" aria-label="GhostOS agent graph">
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M0,0 L10,5 L0,10 z" fill="var(--line-strong)" />
        </marker>
        <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="6" result="b" />
          <feMerge>
            <feMergeNode in="b" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      {EDGES.map(([a, b, label]) => {
        const p = pos(a);
        const q = pos(b);
        const hot = active === a || active === b;
        const mx = (p.x + q.x) / 2;
        const my = (p.y + q.y) / 2;
        return (
          <g key={`${a}-${b}`}>
            <line
              x1={p.x} y1={p.y} x2={q.x} y2={q.y}
              className={hot ? "edge hot" : "edge"}
              markerEnd="url(#arrow)"
              strokeDasharray={label ? "4 4" : undefined}
            />
            {label && <text x={mx} y={my - 4} className="edge-label">{label}</text>}
          </g>
        );
      })}
      {NODES.map((n) => {
        const on = active === n.id;
        return (
          <g key={n.id} transform={`translate(${n.x - 52},${n.y - 22})`} className={on ? "node on" : "node"}>
            <rect width="104" height="44" rx="12" filter={on ? "url(#glow)" : undefined} />
            <text x="52" y="19" className="node-label">{n.label}</text>
            <text x="52" y="34" className="node-role">{n.role}</text>
          </g>
        );
      })}
    </svg>
  );
}
