"""Semantic recall without a model download: feature-hashed embeddings + cosine search.

The hashing embedder maps word unigrams/bigrams and character trigrams into a fixed
vector. It is not as smart as a neural embedding, but it is deterministic, instant
and good enough to recall "I failed on something like this before". The
``Embedder`` protocol lets you swap in a real model (or pgvector) without touching callers.
"""

from __future__ import annotations

import hashlib
import itertools
import re
from typing import Protocol

import numpy as np

_TOKEN = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    dim: int

    def embed(self, text: str) -> np.ndarray: ...


class HashingEmbedder:
    def __init__(self, dim: int = 512) -> None:
        self.dim = dim

    def _features(self, text: str) -> list[str]:
        words = _TOKEN.findall(text.lower())
        feats = [f"w:{w}" for w in words]
        feats += [f"b:{a}_{b}" for a, b in itertools.pairwise(words)]
        for w in words:
            padded = f"#{w}#"
            feats += [f"c:{padded[i : i + 3]}" for i in range(len(padded) - 2)]
        return feats

    def embed(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        for feat in self._features(text):
            digest = hashlib.blake2b(feat.encode(), digest_size=8).digest()
            h = int.from_bytes(digest, "little")
            sign = 1.0 if (h >> 63) & 1 else -1.0
            weight = 0.5 if feat.startswith("c:") else 1.0
            vec[h % self.dim] += sign * weight
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm else vec


def cosine_top_k(query: np.ndarray, matrix: np.ndarray, k: int) -> list[tuple[int, float]]:
    if matrix.size == 0:
        return []
    scores = matrix @ query
    order = np.argsort(-scores)[:k]
    return [(int(i), float(scores[i])) for i in order]
