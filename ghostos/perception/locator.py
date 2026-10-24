"""The self-healing locator: turns a human label into a concrete element, even after a redesign.

Strategy ladder (cheapest and most certain first):

1. **memory**      — the selector that worked last time still points at a similar element.
2. **semantic**    — an element is named (text / aria / label / placeholder) almost exactly like the label.
3. **fingerprint** — the remembered multi-signal fingerprint matches a candidate strongly. *(heal)*
4. **fuzzy-text**  — an element is named somewhat like the label.                          *(heal)*
5. **vision**      — ask a multimodal LLM to pick from a numbered set of candidates.        *(heal)*

Whenever a rung below "memory" succeeds for an element GhostOS had seen before, that
is a *healing*: the UI changed under the agent and it adapted. The healing is
explained (screenshot diff + annotated screenshot + score breakdown) and stored,
so the next run goes straight to rung 1.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from ghostos.perception import vision
from ghostos.perception.fingerprint import EXTRACT_JS, Fingerprint, label_score, similarity

if TYPE_CHECKING:
    from playwright.async_api import Page

    from ghostos.core.events import Emitter
    from ghostos.llm.base import LLMProvider
    from ghostos.memory.store import MemoryStore

FILLABLE = {"input", "textarea", "select"}
SEMANTIC_THRESHOLD = 0.92
# A fingerprint match is accepted when it is either strong, or clearly better than
# every alternative (a ratio-test, like in feature matching): (min score, min margin).
FINGERPRINT_RULES = ((0.55, 0.05), (0.42, 0.12))
FUZZY_THRESHOLD = 0.6


class LocatorError(LookupError):
    def __init__(self, message: str, evidence: dict[str, Any]) -> None:
        super().__init__(message)
        self.evidence = evidence


@dataclass
class Resolution:
    fingerprint: Fingerprint
    strategy: str
    confidence: float
    healed: bool
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def selector(self) -> str:
        return self.fingerprint.selector


def accept_fingerprint(score: float, runner_up: float) -> bool:
    return any(score >= low and score - runner_up >= margin for low, margin in FINGERPRINT_RULES)


def site_key(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}{p.path.rstrip('/') or '/'}"


class SelfHealingLocator:
    def __init__(
        self,
        memory: MemoryStore,
        artifacts: Path,
        emit: Emitter,
        llm: LLMProvider | None = None,
    ) -> None:
        self.memory = memory
        self.artifacts = artifacts
        self.emit = emit
        self.llm = llm
        self._shots = 0

    async def candidates(self, page: Page, kind: str) -> list[Fingerprint]:
        raw = await page.evaluate(EXTRACT_JS)
        fps = [Fingerprint.from_dict(d) for d in raw]
        if kind == "fill":
            return [
                f
                for f in fps
                if f.tag in FILLABLE and f.type not in {"submit", "button", "checkbox", "radio"}
            ]
        return [
            f
            for f in fps
            if not (f.tag in FILLABLE and f.type not in {"submit", "button", "checkbox", "radio"})
        ]

    async def resolve(self, page: Page, label: str, kind: str = "click") -> Resolution:
        site = site_key(page.url)
        cands = await self.candidates(page, kind)
        if not cands:
            raise LocatorError(f"no interactive elements on {site}", {"site": site})

        remembered = self.memory.recall_element(site, label)
        ref = Fingerprint.from_dict(remembered["fingerprint"]) if remembered else None

        # 1. memory
        if remembered and ref:
            same = next((c for c in cands if c.selector == remembered["selector"]), None)
            if same is not None:
                score, _ = similarity(ref, same)
                if score >= 0.45:
                    return Resolution(same, "memory", 1.0, False, {"similarity": score})
            self.emit(
                "heal.detect",
                f"Remembered selector {remembered['selector']} for “{label}” no longer matches — UI changed",
                agent="healer",
                label=label,
                old_selector=remembered["selector"],
            )

        ranked_names = sorted(((label_score(label, c), c) for c in cands), key=lambda t: -t[0])
        best_name_score, best_named = ranked_names[0]

        # 2. semantic
        if best_name_score >= SEMANTIC_THRESHOLD:
            res = Resolution(best_named, "semantic", best_name_score, ref is not None)
            return await self._finish(page, site, label, res, ref, remembered)

        # 3. fingerprint
        if ref is not None:
            scored = sorted(((*similarity(ref, c), c) for c in cands), key=lambda t: -t[0])
            top_score, top_parts, top = scored[0]
            runner_up = scored[1][0] if len(scored) > 1 else 0.0
            self.emit(
                "heal.candidates",
                f"Scored {len(scored)} candidates against the remembered fingerprint",
                agent="healer",
                label=label,
                candidates=[
                    {"element": c.describe(), "selector": c.selector, "score": s, "parts": p}
                    for s, p, c in scored[:4]
                ],
            )
            if accept_fingerprint(top_score, runner_up):
                res = Resolution(
                    top,
                    "fingerprint",
                    top_score,
                    True,
                    {"parts": top_parts, "runner_up": runner_up},
                )
                return await self._finish(page, site, label, res, ref, remembered)

        # 4. fuzzy text
        if best_name_score >= FUZZY_THRESHOLD:
            res = Resolution(best_named, "fuzzy-text", best_name_score, ref is not None)
            return await self._finish(page, site, label, res, ref, remembered)

        # 5. vision
        if self.llm is not None and self.llm.supports_vision:
            picked = await self._ask_vision(page, label, cands)
            if picked is not None:
                res = Resolution(picked, "vision", 0.7, True)
                return await self._finish(page, site, label, res, ref, remembered)

        raise LocatorError(
            f"could not find “{label}” on {site}",
            {
                "site": site,
                "closest": [
                    {"element": c.describe(), "score": round(s, 3)} for s, c in ranked_names[:3]
                ],
            },
        )

    async def _finish(
        self,
        page: Page,
        site: str,
        label: str,
        res: Resolution,
        ref: Fingerprint | None,
        remembered: dict[str, Any] | None,
    ) -> Resolution:
        if not res.healed:
            return res
        shot = await self.screenshot(page, "heal")
        change = None
        if remembered and remembered.get("screenshot") and Path(remembered["screenshot"]).exists():
            change = vision.compare(remembered["screenshot"], shot)
        annotated = vision.annotate(
            shot,
            self.artifacts / f"{Path(shot).stem}-annotated.png",
            found=res.fingerprint.bbox,
            previous=ref.bbox if ref else None,
            caption=f"healed “{label}” via {res.strategy} ({res.confidence:.2f})",
        )
        res.evidence.update(
            screenshot=str(annotated),
            ui_change=change.changed_ratio if change else None,
            hash_distance=change.hash_distance if change else None,
            old=ref.describe() if ref else None,
            new=res.fingerprint.describe(),
        )
        self.emit(
            "heal.success",
            f"Healed “{label}”: {ref.describe() if ref else '?'} → {res.fingerprint.describe()} "
            f"via {res.strategy} (confidence {res.confidence:.2f})",
            agent="healer",
            label=label,
            strategy=res.strategy,
            confidence=res.confidence,
            old_selector=remembered["selector"] if remembered else None,
            new_selector=res.selector,
            ui_change=res.evidence.get("ui_change"),
            screenshot=str(annotated),
        )
        return res

    def learn(
        self, page_url: str, label: str, res: Resolution, screenshot: str | None, run_id: str
    ) -> None:
        site = site_key(page_url)
        previous = self.memory.recall_element(site, label)
        self.memory.remember_element(
            site, label, res.selector, res.fingerprint.to_dict(), screenshot
        )
        if res.healed:
            self.memory.record_healing(
                run_id,
                site,
                label,
                old_selector=previous["selector"] if previous else None,
                new_selector=res.selector,
                strategy=res.strategy,
                confidence=res.confidence,
                ui_change=res.evidence.get("ui_change"),
                evidence=res.evidence,
            )
            self.memory.add_lesson(
                f"On {site} the element “{label}” moved from {previous['selector'] if previous else '?'} "
                f"to {res.selector}; healed with {res.strategy}.",
                run_id=run_id,
                tags=["healing", site],
            )

    async def screenshot(self, page: Page, tag: str) -> str:
        self._shots += 1
        path = self.artifacts / f"{self._shots:03d}-{tag}.png"
        await page.screenshot(path=str(path))
        return str(path)

    async def _ask_vision(
        self, page: Page, label: str, cands: list[Fingerprint]
    ) -> Fingerprint | None:
        assert self.llm is not None
        image = await page.screenshot()
        listing = "\n".join(f"[{i}] {c.describe()} bbox={c.bbox}" for i, c in enumerate(cands[:40]))
        prompt = (
            f"The user wants to interact with the element called “{label}”.\n"
            f"Interactive elements on screen:\n{listing}\n\n"
            'Reply with JSON only: {"index": <number or null>, "reason": "<short>"}'
        )
        reply = await self.llm.complete(
            system="You locate UI elements in screenshots. Be conservative: answer null if unsure.",
            prompt=prompt,
            images=[image],
        )
        match = re.search(r"\{.*\}", reply, re.S)
        if not match:
            return None
        try:
            idx = json.loads(match.group(0)).get("index")
        except json.JSONDecodeError:
            return None
        return cands[idx] if isinstance(idx, int) and 0 <= idx < len(cands) else None
