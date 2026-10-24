"""HTTP + WebSocket API for the dashboard, plus the bundled demo site."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ghostos.runtime import GhostOS

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
DASHBOARD_OUT = ROOT / "dashboard" / "out"


class RunRequest(BaseModel):
    task: str
    provider: str | None = None
    script: str | None = None


class ApprovalDecision(BaseModel):
    approved: bool


class Deploy(BaseModel):
    version: str


def create_app(ghost: GhostOS | None = None) -> FastAPI:
    ghost = ghost or GhostOS()
    app = FastAPI(title="GhostOS", version="0.1.0")
    app.state.ghost = ghost
    app.state.acme_version = "v1"
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- demo site: same URL, two versions; "deploying" v2 breaks every v1 selector
    @app.get("/sites/acme/", response_class=HTMLResponse)
    async def acme() -> str:
        return (FIXTURES / f"acme_{app.state.acme_version}" / "index.html").read_text()

    @app.post("/sites/acme/deploy")
    async def deploy(body: Deploy) -> dict[str, str]:
        if body.version not in ("v1", "v2"):
            raise HTTPException(400, "version must be v1 or v2")
        app.state.acme_version = body.version
        return {"version": body.version}

    @app.get("/sites/acme/version")
    async def acme_version() -> dict[str, str]:
        return {"version": app.state.acme_version}

    # -- runs
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "memory": ghost.memory.stats(), "active_runs": list(ghost.active)}

    @app.post("/api/runs")
    async def create_run(req: RunRequest) -> dict[str, str]:
        script = Path(req.script).resolve() if req.script else None
        if script and FIXTURES not in script.parents:
            raise HTTPException(400, "scripts must live under fixtures/")
        return {"run_id": ghost.submit(req.task, provider=req.provider, script=script)}

    @app.get("/api/runs")
    async def list_runs() -> list[dict[str, Any]]:
        return ghost.memory.runs()

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        run = ghost.memory.run(run_id)
        if not run:
            raise HTTPException(404, "run not found")
        return {**run, "events": ghost.memory.events(run_id)}

    # -- approvals
    @app.get("/api/approvals")
    async def approvals() -> list[dict[str, Any]]:
        return [
            {
                "id": r.id,
                "run_id": r.run_id,
                "tool": r.tool,
                "summary": r.summary,
                "risk": r.risk,
                "reason": r.reason,
            }
            for r in ghost.broker.pending.values()
        ]

    @app.post("/api/approvals/{approval_id}")
    async def decide(approval_id: str, body: ApprovalDecision) -> dict[str, bool]:
        if not ghost.broker.resolve(approval_id, body.approved):
            raise HTTPException(404, "no such pending approval")
        return {"ok": True}

    # -- memory
    @app.get("/api/memory")
    async def memory() -> dict[str, Any]:
        return {"stats": ghost.memory.stats(), "healings": ghost.memory.healings(20)}

    @app.get("/api/artifact")
    async def artifact(path: str) -> FileResponse:
        target = Path(path).resolve()
        if ghost.settings.home not in target.parents or not target.is_file():
            raise HTTPException(404, "artifact not found")
        return FileResponse(target)

    @app.websocket("/api/stream")
    async def stream(ws: WebSocket) -> None:
        await ws.accept()
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            async for event in ghost.bus.stream():
                await ws.send_json(event.model_dump(mode="json"))

    if DASHBOARD_OUT.exists():
        app.mount("/", StaticFiles(directory=DASHBOARD_OUT, html=True), name="dashboard")

    return app
