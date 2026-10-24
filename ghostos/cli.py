"""`ghost` command-line interface."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from ghostos.config import Settings
from ghostos.core.types import Event
from ghostos.runtime import GhostOS
from ghostos.safety.gate import ApprovalRequest, auto_approve

app = typer.Typer(add_completion=False, help="GhostOS — an AI that operates a computer.")
console = Console()

_STYLE = {
    "planner": "cyan",
    "operator": "magenta",
    "verifier": "green",
    "healer": "yellow",
    "guardian": "red",
    "coder": "blue",
    "system": "white",
}
_QUIET = {"node.enter", "node.exit", "browser.frame", "graph.topology", "heal.candidates"}


def _clip(text: str, limit: int = 220) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _print_event(event: Event) -> None:
    if event.type in _QUIET:
        return
    color = _STYLE.get(event.agent, "white")
    console.print(
        f"[bold {color}]{event.agent:>9}[/] [dim]{event.type:<16}[/] {escape(_clip(event.message))}"
    )
    if event.type == "plan.created":
        for i, step in enumerate(event.data.get("steps", []), 1):
            console.print(
                f"{'':>10} [dim]{i:>2}.[/] {step['intent']} [dim]({step['tool']}, {step['risk']})[/]"
            )
    if event.type == "code.patch" and event.data.get("diff"):
        console.print(Panel(event.data["diff"], title="patch", border_style="blue"))


async def _cli_approve(req: ApprovalRequest) -> bool:
    console.print(
        Panel(
            f"{req.summary}\n[dim]risk: {req.risk} — {req.reason}[/]",
            title="approval needed",
            border_style="red",
        )
    )
    return await asyncio.to_thread(Confirm.ask, "Allow GhostOS to do this?", default=False)


@app.command()
def run(
    task: str = typer.Argument(..., help="What GhostOS should do, in plain English"),
    provider: str = typer.Option("auto", help="auto | offline | anthropic | openai | scripted"),
    script: Path | None = typer.Option(None, help="Reply script for --provider scripted"),
    headed: bool = typer.Option(False, help="Show the browser window"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-approve risky actions (careful!)"),
) -> None:
    """Run a task end to end."""
    ghost = GhostOS(Settings(provider=provider), resolver=auto_approve if yes else _cli_approve)
    ghost.bus.subscribe_sync(_print_event)
    result = asyncio.run(ghost.run(task, provider=provider, script=script, headless=not headed))
    color = "green" if result.status == "succeeded" else "red"
    console.print(
        Panel(
            f"{result.summary}\n[dim]trace: {result.run_dir / 'trace.jsonl'}[/]",
            title=f"run {result.status}",
            border_style=color,
        )
    )
    raise typer.Exit(0 if result.status == "succeeded" else 1)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Start the API server, the dashboard and the demo site."""
    import uvicorn

    from ghostos.server.app import create_app

    console.print(
        f"[bold]GhostOS[/] dashboard → http://{host}:{port}   demo site → http://{host}:{port}/sites/acme/"
    )
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


@app.command()
def memory() -> None:
    """Show what GhostOS has learned."""
    ghost = GhostOS()
    stats = ghost.memory.stats()
    console.print(" · ".join(f"{k}: [bold]{v}[/]" for k, v in stats.items()))
    table = Table(
        "label",
        "old selector",
        "new selector",
        "strategy",
        "confidence",
        "ui change",
        title="healings",
    )
    for h in ghost.memory.healings(20):
        change = f"{h['ui_change']:.0%}" if h["ui_change"] is not None else "—"
        table.add_row(
            h["label"],
            escape(h["old_selector"] or "—"),
            escape(h["new_selector"]),
            h["strategy"],
            f"{h['confidence']:.2f}",
            change,
        )
    console.print(table)


if __name__ == "__main__":
    app()
