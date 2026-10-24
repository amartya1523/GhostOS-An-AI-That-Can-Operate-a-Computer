"""Element fingerprints: a multi-signal description of a UI element that survives redesigns.

A CSS selector captures *one* fact about an element (its id, or its path). When a
redesign changes that fact, the selector dies. A fingerprint captures many weak
facts — tag, visible text, accessible name, position, neighbouring labels, order
within its form, class vocabulary — and similarity over all of them stays high
even when several individually change. That is the core of GhostOS self-healing.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from typing import Any

# Runs inside the page. Returns every visible, interactive element with its signals
# and a unique selector GhostOS can act on.
EXTRACT_JS = r"""
() => {
  const vw = Math.max(1, window.innerWidth), vh = Math.max(1, window.innerHeight);
  const q = 'button, a[href], input:not([type=hidden]), select, textarea, [role=button], [role=link], [onclick], [contenteditable=true]';
  const clean = s => (s || '').replace(/\s+/g, ' ').trim().slice(0, 120);
  const esc = s => (window.CSS && CSS.escape) ? CSS.escape(s) : s.replace(/[^a-zA-Z0-9_-]/g, '\\$&');
  const unique = sel => { try { return document.querySelectorAll(sel).length === 1; } catch { return false; } };
  const selectorFor = el => {
    if (el.id && unique('#' + esc(el.id))) return '#' + esc(el.id);
    const tid = el.getAttribute('data-testid');
    if (tid && unique(`[data-testid="${tid}"]`)) return `[data-testid="${tid}"]`;
    const name = el.getAttribute('name');
    if (name && unique(`${el.tagName.toLowerCase()}[name="${name}"]`)) return `${el.tagName.toLowerCase()}[name="${name}"]`;
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node !== document.body) {
      let part = node.tagName.toLowerCase();
      const sibs = node.parentElement ? [...node.parentElement.children].filter(c => c.tagName === node.tagName) : [];
      if (sibs.length > 1) part += `:nth-of-type(${sibs.indexOf(node) + 1})`;
      parts.unshift(part);
      const candidate = 'body > ' + parts.join(' > ');
      if (unique(candidate)) return candidate;
      node = node.parentElement;
    }
    return 'body > ' + parts.join(' > ');
  };
  const labelFor = el => {
    if (el.labels && el.labels.length) return clean(el.labels[0].innerText);
    const wrap = el.closest('label');
    return wrap ? clean(wrap.innerText) : '';
  };
  const contextFor = el => {
    const bits = [];
    const prev = el.previousElementSibling;
    if (prev && !prev.matches(q)) bits.push(clean(prev.innerText));
    const section = el.closest('form, section, nav, header, aside, main, dialog, [role=dialog]');
    if (section) {
      const h = section.querySelector('h1, h2, h3, h4, legend');
      if (h) bits.push(clean(h.innerText));
      bits.push(section.tagName.toLowerCase());
    }
    return bits.filter(Boolean).join(' | ').slice(0, 160);
  };
  const all = [...document.querySelectorAll(q)];
  const out = [];
  all.forEach((el) => {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || st.visibility === 'hidden' || st.display === 'none' || el.disabled) return;
    const form = el.closest('form, section, nav, header, aside, dialog');
    const peers = form ? [...form.querySelectorAll(q)] : all;
    out.push({
      selector: selectorFor(el),
      tag: el.tagName.toLowerCase(),
      type: (el.getAttribute('type') || '').toLowerCase(),
      role: el.getAttribute('role') || '',
      id: el.id || '',
      name: el.getAttribute('name') || '',
      autocomplete: el.getAttribute('autocomplete') || '',
      classes: [...el.classList].slice(0, 12),
      text: clean(el.innerText || el.value),
      aria: clean(el.getAttribute('aria-label') || el.getAttribute('title')),
      placeholder: clean(el.getAttribute('placeholder')),
      label: labelFor(el),
      context: contextFor(el),
      order: peers.indexOf(el),
      x: (r.left + r.width / 2) / vw, y: (r.top + r.height / 2) / vh,
      w: r.width / vw, h: r.height / vh,
      bbox: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
    });
  });
  return out;
}
"""


@dataclass
class Fingerprint:
    selector: str
    tag: str
    type: str = ""
    role: str = ""
    id: str = ""
    name: str = ""
    autocomplete: str = ""
    classes: list[str] = field(default_factory=list)
    text: str = ""
    aria: str = ""
    placeholder: str = ""
    label: str = ""
    context: str = ""
    order: int = -1
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    bbox: list[int] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Fingerprint:
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def names(self) -> list[str]:
        """Every human-facing name the element answers to."""
        return [n for n in (self.text, self.aria, self.placeholder, self.label, self.name) if n]

    def describe(self) -> str:
        name = next(iter(self.names), self.selector)
        return f"<{self.tag}> “{name}”"


_WORD = re.compile(r"[a-z0-9]+")


def _tokens(s: str) -> set[str]:
    return set(_WORD.findall(s.lower()))


def text_similarity(a: str, b: str) -> float:
    """Blend of character-level and token-level similarity, in [0, 1]."""
    a, b = a.lower().strip(), b.lower().strip()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    seq = SequenceMatcher(None, a, b).ratio()
    ta, tb = _tokens(a), _tokens(b)
    jac = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    contains = 0.9 if (a in b or b in a) and min(len(a), len(b)) >= 3 else 0.0
    return max(contains, 0.5 * seq + 0.5 * jac)


def label_score(label: str, fp: Fingerprint) -> float:
    """How well a human label (e.g. "Sign in") names this element."""
    candidates = [*fp.names, fp.id.replace("-", " ").replace("_", " ")]
    return max((text_similarity(label, c) for c in candidates if c), default=0.0)


# Weights sum to 1.0. Tuned on the bundled fixtures; see tests/test_fingerprint.py.
WEIGHTS = {
    "names": 0.22,
    "kind": 0.16,
    "position": 0.16,
    "context": 0.14,
    "order": 0.10,
    "classes": 0.10,
    "identity": 0.12,
}


def similarity(ref: Fingerprint, cand: Fingerprint) -> tuple[float, dict[str, float]]:
    """Weighted multi-signal similarity between a remembered element and a candidate."""
    parts: dict[str, float] = {}

    name_pairs = [text_similarity(a, b) for a in ref.names for b in cand.names]
    parts["names"] = max(name_pairs, default=0.0)

    kind = 1.0 if ref.tag == cand.tag else 0.0
    if kind and ref.type != cand.type:
        kind = 0.6
    parts["kind"] = kind

    dist = ((ref.x - cand.x) ** 2 + (ref.y - cand.y) ** 2) ** 0.5
    parts["position"] = max(0.0, 1.0 - dist / 0.6)

    parts["context"] = text_similarity(ref.context, cand.context)

    if ref.order < 0 or cand.order < 0:
        parts["order"] = 0.0
    else:
        parts["order"] = (
            1.0 if ref.order == cand.order else max(0.0, 1.0 - abs(ref.order - cand.order) / 3)
        )

    rc, cc = set(ref.classes), set(cand.classes)
    parts["classes"] = len(rc & cc) / len(rc | cc) if rc | cc else 0.5

    ident = [
        text_similarity(ref.id.replace("-", " "), cand.id.replace("-", " "))
        if ref.id and cand.id
        else 0.0,
        1.0 if ref.name and ref.name == cand.name else 0.0,
        1.0 if ref.role and ref.role == cand.role else 0.0,
        1.0 if ref.autocomplete and ref.autocomplete == cand.autocomplete else 0.0,
    ]
    has_identity = any((ref.id, ref.name, ref.role, ref.autocomplete)) or any(
        (cand.id, cand.name, cand.role, cand.autocomplete)
    )
    parts["identity"] = max(ident) if has_identity else 0.5  # nothing to compare: neutral

    score = sum(WEIGHTS[k] * v for k, v in parts.items())
    return round(score, 4), {k: round(v, 3) for k, v in parts.items()}
