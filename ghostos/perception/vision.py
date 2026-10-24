"""Screenshot analysis: detect that the UI changed, where, and draw what the agent saw."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


@dataclass(frozen=True)
class UIChange:
    changed_ratio: float  # share of the screen that differs, 0..1
    hash_distance: int  # Hamming distance between perceptual hashes, 0..64
    region: tuple[int, int, int, int] | None  # x, y, w, h of the changed area (full-res px)

    @property
    def changed(self) -> bool:
        return self.changed_ratio > 0.02 or self.hash_distance > 6


def _gray(path: Path | str, size: tuple[int, int]) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("L").resize(size, Image.Resampling.BILINEAR), dtype=np.int16)


def dhash(path: Path | str, size: int = 8) -> int:
    px = _gray(path, (size + 1, size))
    bits = (px[:, 1:] > px[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def compare(before: Path | str, after: Path | str, grid: tuple[int, int] = (160, 100)) -> UIChange:
    with Image.open(after) as im:
        full_w, full_h = im.size
    a, b = _gray(before, grid), _gray(after, grid)
    mask = np.abs(a - b) > 24
    ratio = float(mask.mean())
    region = None
    if mask.any():
        ys, xs = np.nonzero(mask)
        sx, sy = full_w / grid[0], full_h / grid[1]
        region = (
            int(xs.min() * sx),
            int(ys.min() * sy),
            int((xs.max() - xs.min() + 1) * sx),
            int((ys.max() - ys.min() + 1) * sy),
        )
    distance = bin(dhash(before) ^ dhash(after)).count("1")
    return UIChange(round(ratio, 4), distance, region)


def annotate(
    screenshot: Path | str,
    out: Path | str,
    *,
    found: list[int] | None = None,
    previous: list[int] | None = None,
    caption: str = "",
) -> Path:
    """Draw the healed target (green) and where the element used to be (red, dashed)."""
    with Image.open(screenshot) as src:
        im = src.convert("RGB")
    draw = ImageDraw.Draw(im, "RGBA")
    if previous:
        x, y, w, h = previous
        _dashed_rect(draw, (x, y, x + w, y + h), (239, 68, 68, 255))
        draw.rectangle((x, y, x + w, y + h), fill=(239, 68, 68, 40))
    if found:
        x, y, w, h = found
        draw.rectangle((x - 3, y - 3, x + w + 3, y + h + 3), outline=(34, 197, 94, 255), width=4)
        draw.rectangle((x, y, x + w, y + h), fill=(34, 197, 94, 50))
    if caption:
        font = _font(18)
        tw = draw.textlength(caption, font=font)
        draw.rectangle((0, 0, tw + 24, 34), fill=(10, 10, 15, 220))
        draw.text((12, 7), caption, fill=(230, 255, 240, 255), font=font)
    out = Path(out)
    im.save(out)
    return out


def _dashed_rect(
    draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], color: tuple[int, ...]
) -> None:
    x0, y0, x1, y1 = box
    dash = 8
    for x in range(x0, x1, dash * 2):
        draw.line((x, y0, min(x + dash, x1), y0), fill=color, width=3)
        draw.line((x, y1, min(x + dash, x1), y1), fill=color, width=3)
    for y in range(y0, y1, dash * 2):
        draw.line((x0, y, x0, min(y + dash, y1)), fill=color, width=3)
        draw.line((x1, y, x1, min(y + dash, y1)), fill=color, width=3)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("/System/Library/Fonts/SFNSMono.ttf", "DejaVuSansMono.ttf", "Menlo.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()
