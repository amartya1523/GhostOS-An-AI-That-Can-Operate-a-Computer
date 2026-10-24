// The dashboard is served by `ghost serve` on the same origin as the API.
// During `npm run dev`, point it at the API with NEXT_PUBLIC_GHOSTOS_API.

export const API = process.env.NEXT_PUBLIC_GHOSTOS_API ?? "";

export function streamUrl(): string {
  const base = API || window.location.origin;
  return base.replace(/^http/, "ws") + "/api/stream";
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(API + path, {
    ...init,
    headers: { "content-type": "application/json", "x-ghostos-client": "dashboard", ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`${init?.method ?? "GET"} ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  startRun: (task: string, provider?: string, script?: string) =>
    call<{ run_id: string }>("/api/runs", { method: "POST", body: JSON.stringify({ task, provider, script }) }),
  decide: (id: string, approved: boolean) =>
    call<{ ok: boolean }>(`/api/approvals/${id}`, { method: "POST", body: JSON.stringify({ approved }) }),
  deploy: (version: "v1" | "v2") =>
    call<{ version: string }>("/sites/acme/deploy", { method: "POST", body: JSON.stringify({ version }) }),
  siteVersion: () => call<{ version: string }>("/sites/acme/version"),
  memory: () => call<{ stats: Record<string, number>; healings: any[] }>("/api/memory"),
  artifact: (path: string) => `${API}/api/artifact?path=${encodeURIComponent(path)}`,
};
