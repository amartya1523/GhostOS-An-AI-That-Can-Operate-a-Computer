"""Record the README demo videos from real GhostOS runs.

Nothing here is staged: the script boots a fresh GhostOS server (empty memory),
drives the dashboard in a real browser exactly like a person would, and films it.
Captions are burned into the video *and* exported as .srt/.vtt subtitle tracks.

    python scripts/record_demos.py            # both videos
    python scripts/record_demos.py healing    # just one

Outputs land in docs/media/: <name>.mp4 (with a soft subtitle track), <name>.gif,
<name>.srt, <name>.vtt and a few still frames for the README.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import imageio_ffmpeg
import uvicorn
from playwright.async_api import Page, async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ghostos.config import Settings  # noqa: E402
from ghostos.runtime import GhostOS  # noqa: E402
from ghostos.server.app import create_app  # noqa: E402

MEDIA = ROOT / "docs" / "media"
PORT = 8777
BASE = f"http://127.0.0.1:{PORT}"
W, H = 1600, 1000
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
REPO_URL = "github.com/amartya1523/GhostOS-An-AI-That-Can-Operate-a-Computer"

OVERLAY_JS = """
([kind, a, b]) => {
  const id = kind === 'card' ? '__card' : '__cap';
  let el = document.getElementById(id);
  if (kind === 'clear-card') { el?.remove(); document.getElementById('__card')?.remove(); return; }
  if (!el) {
    el = document.createElement('div');
    el.id = id;
    document.body.appendChild(el);
  }
  if (kind === 'card') {
    el.style.cssText = `position:fixed;inset:0;z-index:9999;display:flex;flex-direction:column;
      align-items:center;justify-content:center;gap:18px;background:radial-gradient(circle at 30% 20%,#2e1065,#07080d 65%);
      color:#fff;font-family:Inter,-apple-system,sans-serif;text-align:center;`;
    el.innerHTML = `<div style="font-size:84px;font-weight:800;letter-spacing:-2px">
        <span style="color:#a78bfa;text-shadow:0 0 30px #a78bfa">◆</span> ${a}</div>
      <div style="font-size:28px;color:#c4c8e0;max-width:1100px;line-height:1.4">${b}</div>`;
    return;
  }
  el.style.cssText = `position:fixed;left:50%;bottom:28px;transform:translateX(-50%);z-index:9998;
    max-width:1180px;width:max-content;padding:14px 26px;border-radius:14px;
    background:rgba(5,6,10,.9);border:1px solid rgba(167,139,250,.55);box-shadow:0 18px 50px rgba(0,0,0,.6);
    color:#fff;font:600 23px/1.4 Inter,-apple-system,sans-serif;text-align:center;`;
  el.textContent = a;
  el.style.display = a ? 'block' : 'none';
}
"""


@dataclass
class Captions:
    t0: float = field(default_factory=time.monotonic)
    cues: list[tuple[float, float, str]] = field(default_factory=list)
    _open: tuple[float, str] | None = None

    def _now(self) -> float:
        return time.monotonic() - self.t0

    def open(self, text: str) -> None:
        self.close()
        self._open = (self._now(), text)

    def close(self) -> None:
        if self._open:
            start, text = self._open
            self.cues.append((start, self._now(), text))
            self._open = None

    @staticmethod
    def _ts(t: float, sep: str) -> str:
        ms = round(t * 1000)
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"

    def write(self, stem: Path) -> None:
        self.close()
        srt = [
            f"{i}\n{self._ts(a, ',')} --> {self._ts(b, ',')}\n{t}\n"
            for i, (a, b, t) in enumerate(self.cues, 1)
        ]
        stem.with_suffix(".srt").write_text("\n".join(srt))
        vtt = [f"{self._ts(a, '.')} --> {self._ts(b, '.')}\n{t}\n" for a, b, t in self.cues]
        stem.with_suffix(".vtt").write_text("WEBVTT\n\n" + "\n".join(vtt))


class Director:
    def __init__(self, page: Page, captions: Captions) -> None:
        self.page = page
        self.captions = captions

    async def say(self, text: str, hold: float = 0) -> None:
        self.captions.open(text)
        await self.page.evaluate(OVERLAY_JS, ["cap", text, ""])
        if hold:
            await asyncio.sleep(hold)

    async def card(self, title: str, subtitle: str, hold: float) -> None:
        self.captions.open(
            re.sub(r"<[^>]+>", " ", f"{title} — {subtitle}").replace("  ", " ").strip()
        )
        await self.page.evaluate(OVERLAY_JS, ["card", title, subtitle])
        await asyncio.sleep(hold)
        await self.page.evaluate(OVERLAY_JS, ["clear-card", "", ""])
        self.captions.close()

    async def until(self, js: str, timeout: float = 90) -> None:
        try:
            await self.page.wait_for_function(js, timeout=timeout * 1000, polling=100)
        except Exception:
            debug = Path(tempfile.gettempdir()) / "ghostos-record-timeout.png"
            await self.page.screenshot(path=str(debug), full_page=True)
            print(f"timed out waiting for {js!r}; page captured to {debug}")
            raise

    async def run_and_wait(self) -> None:
        await self.page.get_by_role("button", name="Run ▸").click()
        await self.until("document.querySelector('.status')?.textContent === 'running'")
        await self.until(
            "['succeeded','failed'].includes(document.querySelector('.status')?.textContent)", 180
        )

    async def still(self, name: str, selector: str | None = None) -> None:
        await self.page.evaluate(OVERLAY_JS, ["cap", "", ""])
        target = self.page.locator(selector).first if selector else self.page
        await target.screenshot(path=str(MEDIA / f"{name}.png"))


async def naive_script_error() -> str:
    """What a classic selector-based script does against the redesigned site."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.goto(f"{BASE}/sites/acme/")
        try:
            await page.fill("#username", "ada@acme.io", timeout=2000)
            return "it worked?!"
        except Exception as exc:
            return type(exc).__name__
        finally:
            await browser.close()


async def scene_healing(d: Director) -> None:
    page = d.page
    await d.card(
        "GhostOS",
        "Self-healing browser agent<br><span style='font-size:20px;color:#8389a6'>real run · nothing scripted</span>",
        3.5,
    )
    await d.say(
        "GhostOS takes a task in plain English, then plans, acts, verifies and heals itself.", 4.5
    )
    await d.say(
        "Run 1 · Acme Billing v1. GhostOS has never seen this app — it plans 9 steps and fingerprints every element it touches."
    )
    await d.run_and_wait()
    await d.say(
        "Run 1 done: 9/9 steps. Selectors + fingerprints + screenshots are now in memory.", 3.5
    )

    await page.get_by_role("button", name="demo site").click()
    await d.say(
        "Now Acme ships a redesign: new layout, new IDs, renamed buttons and labels. Every v1 selector is dead.",
        4.5,
    )
    err = await naive_script_error()
    await d.say(
        f"A classic Playwright script breaks on step one:  page.fill('#username')  →  {err}", 4.5
    )

    await d.say("Run 2 · same task, redesigned site. Watch the Healer agent.")
    await page.get_by_role("button", name="Run ▸").click()
    await d.until("!!document.querySelector('.heal-flash')", 60)
    await d.say(
        "Healer: the remembered selector is gone → score every element on screen against the stored fingerprint."
    )
    await d.until("document.querySelectorAll('.heal').length >= 3", 60)
    await d.say(
        "Fingerprint = text, role, position, form order, classes, labels… many weak signals survive a redesign."
    )
    await d.until(
        "['succeeded','failed'].includes(document.querySelector('.status')?.textContent)", 120
    )
    await asyncio.sleep(1)
    healed, total = await page.locator(".heal").count(), await page.locator(".step").count()
    recap = (
        f"{healed} of {total} steps self-healed. Each healing is explained (old → new selector, "
        "strategy, confidence, screen diff) and saved to memory."
    )
    await page.locator(".heals").scroll_into_view_if_needed()
    await d.say(recap, 6.5)
    await d.still("healing-panel", ".heals")
    await d.say(recap, 1)
    await page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
    await d.say(
        "Run 3 · GhostOS remembers: zero healing needed, learned selectors come straight from memory."
    )
    await d.run_and_wait()
    await asyncio.sleep(2)
    await d.card("GhostOS", REPO_URL, 3.5)


async def scene_dev_agent(d: Director) -> None:
    page = d.page
    await d.card(
        "GhostOS · Dev agent",
        "find bugs → patch → self-correct → test → pull request<br><span style='font-size:20px;color:#8389a6'>with a human in the loop for anything irreversible</span>",
        4,
    )
    await page.get_by_role("button", name="Fix bugs + PR · dev agent").click()
    await d.say(
        "Task: “fix the failing tests in fixtures/buggy_repo and open a pull request”.", 3.5
    )
    await d.say(
        "Heads-up: this recording uses the scripted LLM provider so it's reproducible. Set ANTHROPIC_API_KEY for real reasoning.",
        5,
    )
    await page.get_by_role("button", name="Run ▸").click()
    await d.say("The repo is copied into an isolated sandbox and its test suite runs…")
    await d.until("document.querySelector('.tests')?.innerText.includes('3 failed')", 60)
    await d.say(
        "3 failing tests. The Coder agent reads the failures and the source, then proposes a patch."
    )
    await d.until("document.querySelectorAll('.patch').length >= 1", 60)
    await d.say(
        "Patch #1 fixes the separator and discount bugs… but the Verifier re-runs the suite: 1 test still fails."
    )
    await d.until("document.querySelectorAll('.patch').length >= 2", 60)
    await page.locator(".patch").nth(1).scroll_into_view_if_needed()
    await d.say(
        "Self-correction: the Healer feeds that failure back, and the Coder finds the real root cause (padding width).",
        5,
    )
    await d.until("!!document.querySelector('.tests.green')", 60)
    await d.say("12/12 tests green → commit on a new branch.", 2)
    await d.until("!!document.querySelector('.modal')", 60)
    await d.still("approval", ".modal")
    await d.say(
        "Guardian: opening a pull request is irreversible, so GhostOS stops and asks a human.", 4.5
    )
    await d.say("Approved ✓", 1.2)
    await page.get_by_role("button", name="Approve").click()
    await d.until(
        "['succeeded','failed'].includes(document.querySelector('.status')?.textContent)", 60
    )
    await page.locator(".patches").scroll_into_view_if_needed()
    await d.say(
        "Done. PR drafted with diagnosis, verification and diff — the original repo was never touched.",
        5,
    )
    await d.card("GhostOS", REPO_URL, 3.5)


SCENES: dict[str, Callable[[Director], Awaitable[None]]] = {
    "healing": scene_healing,
    "dev-agent": scene_dev_agent,
}
# Pause before each step (ms) so a viewer can follow; the dev agent does more per step.
PACE = {"healing": 650, "dev-agent": 2600}


def start_server(home: Path, step_delay_ms: int) -> uvicorn.Server:
    ghost = GhostOS(
        Settings(home=home, provider="offline", sandbox="process", step_delay_ms=step_delay_ms)
    )
    server = uvicorn.Server(
        uvicorn.Config(create_app(ghost), host="127.0.0.1", port=PORT, log_level="error")
    )
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return server


def ffmpeg(*args: str) -> None:
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", *args], check=True)


def encode(webm: Path, stem: Path) -> None:
    mp4, gif, srt = stem.with_suffix(".mp4"), stem.with_suffix(".gif"), stem.with_suffix(".srt")
    ffmpeg(
        "-i",
        str(webm),
        "-i",
        str(srt),
        "-map",
        "0:v",
        "-map",
        "1:s",
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "27",
        "-pix_fmt",
        "yuv420p",
        "-c:s",
        "mov_text",
        "-metadata:s:s:0",
        "language=eng",
        "-movflags",
        "+faststart",
        str(mp4),
    )
    palette = (
        "fps=7,scale=1100:-1:flags=lanczos,setpts=PTS/1.25,split[a][b];"
        "[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
    )
    ffmpeg("-i", str(mp4), "-vf", palette, str(gif))


async def record(name: str) -> None:
    home = Path(tempfile.mkdtemp(prefix="ghostos-demo-"))
    server = start_server(home, PACE[name])
    raw = Path(tempfile.mkdtemp(prefix="ghostos-video-"))
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            ctx = await browser.new_context(
                viewport={"width": W, "height": H},
                record_video_dir=str(raw),
                record_video_size={"width": W, "height": H},
            )
            page = await ctx.new_page()
            captions = Captions()
            await page.goto(f"{BASE}/")
            await page.wait_for_selector(".conn.on")
            await SCENES[name](Director(page, captions))
            video = page.video
            await ctx.close()
            await browser.close()
            assert video is not None
            webm = Path(await video.path())
        stem = MEDIA / name
        captions.write(stem)
        encode(webm, stem)
        for ext in (".mp4", ".gif", ".srt"):
            size = stem.with_suffix(ext).stat().st_size / 1e6
            print(f"  {stem.with_suffix(ext).relative_to(ROOT)}  {size:.1f} MB")
    finally:
        server.should_exit = True
        shutil.rmtree(raw, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)
        time.sleep(0.5)


async def main(names: list[str]) -> None:
    MEDIA.mkdir(parents=True, exist_ok=True)
    for name in names or list(SCENES):
        print(f"recording {name} …")
        await record(name)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
