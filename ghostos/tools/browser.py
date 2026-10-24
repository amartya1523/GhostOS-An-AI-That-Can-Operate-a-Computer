"""Browser tools built on Playwright, with every element lookup routed through the healer."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ghostos.core.types import Observation, RiskLevel
from ghostos.perception.locator import LocatorError, SelfHealingLocator
from ghostos.tools.base import Tool, ToolContext

if TYPE_CHECKING:
    from playwright.async_api import Browser, Page, Playwright


class BrowserSession:
    """Owns one Chromium page for the duration of a run and streams frames to the dashboard."""

    def __init__(self, ctx: ToolContext, headless: bool = True) -> None:
        self.ctx = ctx
        self.headless = headless
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self.page: Page | None = None
        self.locator = SelfHealingLocator(ctx.memory, ctx.run_dir, ctx.emit, ctx.llm)

    async def start(self) -> Page:
        if self.page is None:
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=self.headless)
            context = await self._browser.new_context(viewport={"width": 1280, "height": 800})
            self.page = await context.new_page()
            self.page.set_default_timeout(4000)
        return self.page

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self.page = self._browser = self._pw = None

    async def frame(self, caption: str) -> str:
        """Capture a screenshot and publish it as a live frame."""
        page = await self.start()
        path = await self.locator.screenshot(page, "frame")
        with open(path, "rb") as fh:
            thumb = base64.b64encode(fh.read()).decode()
        self.ctx.emit(
            "browser.frame", caption, agent="operator", path=path, url=page.url, image=thumb
        )
        return path


async def _session(ctx: ToolContext) -> BrowserSession:
    if ctx.browser is None:
        ctx.browser = BrowserSession(ctx, headless=ctx.scratch.get("headless", True))
    await ctx.browser.start()
    return ctx.browser


class GotoArgs(BaseModel):
    url: str = Field(description="Absolute URL to open")


class Goto(Tool[GotoArgs]):
    name = "browser.goto"
    description = "Open a URL in the browser."
    risk = RiskLevel.READ
    Args = GotoArgs

    def url_for(self, args: GotoArgs) -> str | None:
        return args.url

    async def run(self, args: GotoArgs, ctx: ToolContext) -> Observation:
        s = await _session(ctx)
        assert s.page
        try:
            resp = await s.page.goto(args.url, wait_until="domcontentloaded")
        except Exception as exc:  # playwright raises a zoo of error types
            return Observation(ok=False, error=str(exc), error_kind="navigation")
        shot = await s.frame(f"opened {args.url}")
        status = resp.status if resp else 0
        return Observation(
            ok=status < 400,
            output=f"HTTP {status} {s.page.url}",
            screenshot=shot,
            error=None if status < 400 else f"HTTP {status}",
            error_kind=None if status < 400 else "http",
        )


class ClickArgs(BaseModel):
    target: str = Field(description="Human label of the element, e.g. 'Sign in'")


class Click(Tool[ClickArgs]):
    name = "browser.click"
    description = (
        "Click an element identified by its human label (text, aria-label, or field label)."
    )
    risk = RiskLevel.WRITE
    Args = ClickArgs

    async def run(self, args: ClickArgs, ctx: ToolContext) -> Observation:
        return await _act(ctx, args.target, "click")


class FillArgs(BaseModel):
    target: str = Field(description="Label of the input field")
    text: str = Field(description="Text to type")
    secret: bool = Field(default=False, description="Mask the value in logs")


class Fill(Tool[FillArgs]):
    name = "browser.fill"
    description = "Type text into an input field identified by its label or placeholder."
    risk = RiskLevel.WRITE
    Args = FillArgs

    def summarize(self, args: FillArgs) -> str:
        return f"type {'•' * 6 if args.secret else repr(args.text)} into “{args.target}”"

    async def run(self, args: FillArgs, ctx: ToolContext) -> Observation:
        return await _act(ctx, args.target, "fill", args.text)


async def _act(ctx: ToolContext, label: str, kind: str, text: str | None = None) -> Observation:
    s = await _session(ctx)
    assert s.page
    page = s.page
    try:
        res = await s.locator.resolve(page, label, kind)
    except LocatorError as exc:
        shot = await s.frame(f"could not find “{label}”")
        return Observation(
            ok=False,
            error=str(exc),
            error_kind="locator_not_found",
            data=exc.evidence,
            screenshot=shot,
        )

    url_before = page.url
    before = await s.locator.screenshot(page, "before")
    target = page.locator(res.selector).first
    try:
        await target.scroll_into_view_if_needed()
        await _highlight(page, res.selector, healed=res.healed)
        if kind == "fill":
            await target.fill(text or "")
        else:
            await target.click()
            await page.wait_for_load_state("domcontentloaded")
    except Exception as exc:
        return Observation(ok=False, error=str(exc), error_kind="action_failed", screenshot=before)

    s.locator.stage(url_before, label, res, before, ctx.run_id)
    shot = await s.frame(f"{kind} “{label}”" + (" (healed)" if res.healed else ""))
    data = {
        "selector": res.selector,
        "strategy": res.strategy,
        "confidence": res.confidence,
        "healed": res.healed,
        "evidence": res.evidence,
    }
    return Observation(
        ok=True,
        output=f"{kind} {res.fingerprint.describe()} via {res.strategy}",
        data=data,
        screenshot=shot,
    )


async def _highlight(page: Page, selector: str, healed: bool) -> None:
    color = "#22c55e" if healed else "#a78bfa"
    await page.evaluate(
        """([sel, color]) => { const el = document.querySelector(sel); if (!el) return;
             el.style.outline = `3px solid ${color}`; el.style.outlineOffset = '2px';
             setTimeout(() => { el.style.outline = ''; }, 900); }""",
        [selector, color],
    )
    await page.wait_for_timeout(250)


class AssertTextArgs(BaseModel):
    text: str = Field(description="Text that must be visible on the page")
    timeout_ms: int = 4000


class AssertText(Tool[AssertTextArgs]):
    name = "browser.assert_text"
    description = "Verify that some text is visible on the current page."
    risk = RiskLevel.READ
    Args = AssertTextArgs

    async def run(self, args: AssertTextArgs, ctx: ToolContext) -> Observation:
        s = await _session(ctx)
        assert s.page
        try:
            await s.page.get_by_text(args.text, exact=False).first.wait_for(
                state="visible", timeout=args.timeout_ms
            )
        except Exception:
            shot = await s.frame(f"“{args.text}” not visible")
            return Observation(
                ok=False,
                error=f"text “{args.text}” not visible",
                error_kind="assertion",
                screenshot=shot,
            )
        shot = await s.frame(f"verified “{args.text}”")
        return Observation(ok=True, output=f"“{args.text}” is visible", screenshot=shot)


class PressArgs(BaseModel):
    key: str = Field(description="Key name, e.g. Enter, Tab, Escape")


class Press(Tool[PressArgs]):
    name = "browser.press"
    description = "Press a keyboard key on the focused element."
    risk = RiskLevel.WRITE
    Args = PressArgs

    async def run(self, args: PressArgs, ctx: ToolContext) -> Observation:
        s = await _session(ctx)
        assert s.page
        await s.page.keyboard.press(args.key)
        shot = await s.frame(f"pressed {args.key}")
        return Observation(ok=True, output=f"pressed {args.key}", screenshot=shot)


class ReadArgs(BaseModel):
    max_chars: int = 4000


class ReadPage(Tool[ReadArgs]):
    name = "browser.read"
    description = "Return the visible text of the current page."
    risk = RiskLevel.READ
    Args = ReadArgs

    async def run(self, args: ReadArgs, ctx: ToolContext) -> Observation:
        s = await _session(ctx)
        assert s.page
        text = await s.page.inner_text("body")
        return Observation(ok=True, output=text[: args.max_chars])


BROWSER_TOOLS: list[Tool[Any]] = [Goto(), Click(), Fill(), AssertText(), Press(), ReadPage()]
