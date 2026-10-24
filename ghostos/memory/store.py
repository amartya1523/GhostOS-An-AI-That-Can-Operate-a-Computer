"""Long-term memory backed by SQLite.

Three kinds of memory live here:

* **Procedural** — element fingerprints: "on site S, the thing called 'Sign in'
  looked like this and was reachable via selector X". Used by the self-healing locator.
* **Episodic** — runs, their events and every healing that happened.
* **Semantic** — free-text lessons with embeddings, recalled by similarity when a
  new task looks like an old failure.

SQLite keeps GhostOS zero-setup; the schema is plain enough to port to PostgreSQL
(+ pgvector for the lesson embeddings).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from ghostos.memory.vector import Embedder, HashingEmbedder, cosine_top_k

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, task TEXT NOT NULL, status TEXT NOT NULL,
  provider TEXT, created_at REAL NOT NULL, finished_at REAL, summary TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, ts REAL NOT NULL,
  type TEXT NOT NULL, agent TEXT, message TEXT, data TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, id);
CREATE TABLE IF NOT EXISTS fingerprints (
  site TEXT NOT NULL, label TEXT NOT NULL, selector TEXT NOT NULL,
  fingerprint TEXT NOT NULL, screenshot TEXT, hits INTEGER NOT NULL DEFAULT 1,
  updated_at REAL NOT NULL, PRIMARY KEY (site, label)
);
CREATE TABLE IF NOT EXISTS healings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, site TEXT NOT NULL,
  label TEXT NOT NULL, old_selector TEXT, new_selector TEXT NOT NULL, strategy TEXT NOT NULL,
  confidence REAL NOT NULL, ui_change REAL, evidence TEXT, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS lessons (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, text TEXT NOT NULL,
  tags TEXT, embedding BLOB NOT NULL, created_at REAL NOT NULL
);
"""


class MemoryStore:
    def __init__(self, path: Path | str, embedder: Embedder | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder or HashingEmbedder()
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def _exec(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, params)
            self._db.commit()
            return cur

    def _all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, params).fetchall()]

    # -- episodic -------------------------------------------------------------
    def start_run(self, run_id: str, task: str, provider: str) -> None:
        self._exec(
            "INSERT INTO runs(id, task, status, provider, created_at) VALUES (?,?,?,?,?)",
            (run_id, task, "running", provider, time.time()),
        )

    def finish_run(self, run_id: str, status: str, summary: str) -> None:
        self._exec(
            "UPDATE runs SET status=?, summary=?, finished_at=? WHERE id=?",
            (status, summary, time.time(), run_id),
        )

    def record_event(
        self, run_id: str, ts: float, type_: str, agent: str, message: str, data: dict[str, Any]
    ) -> None:
        self._exec(
            "INSERT INTO events(run_id, ts, type, agent, message, data) VALUES (?,?,?,?,?,?)",
            (run_id, ts, type_, agent, message, json.dumps(data, default=str)),
        )

    def runs(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,))

    def run(self, run_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM runs WHERE id=?", (run_id,))
        return rows[0] if rows else None

    def events(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM events WHERE run_id=? ORDER BY id", (run_id,))
        for r in rows:
            r["data"] = json.loads(r["data"] or "{}")
        return rows

    # -- procedural -----------------------------------------------------------
    def remember_element(
        self,
        site: str,
        label: str,
        selector: str,
        fingerprint: dict[str, Any],
        screenshot: str | None,
    ) -> None:
        self._exec(
            """INSERT INTO fingerprints(site, label, selector, fingerprint, screenshot, updated_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(site, label) DO UPDATE SET selector=excluded.selector,
                 fingerprint=excluded.fingerprint,
                 screenshot=COALESCE(excluded.screenshot, fingerprints.screenshot),
                 hits=fingerprints.hits+1, updated_at=excluded.updated_at""",
            (site, label.lower(), selector, json.dumps(fingerprint), screenshot, time.time()),
        )

    def recall_element(self, site: str, label: str) -> dict[str, Any] | None:
        rows = self._all(
            "SELECT * FROM fingerprints WHERE site=? AND label=?", (site, label.lower())
        )
        if not rows:
            return None
        row = rows[0]
        row["fingerprint"] = json.loads(row["fingerprint"])
        return row

    def record_healing(self, run_id: str, site: str, label: str, **fields: Any) -> None:
        self._exec(
            """INSERT INTO healings(run_id, site, label, old_selector, new_selector, strategy,
               confidence, ui_change, evidence, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                site,
                label.lower(),
                fields.get("old_selector"),
                fields["new_selector"],
                fields["strategy"],
                fields["confidence"],
                fields.get("ui_change"),
                json.dumps(fields.get("evidence", {})),
                time.time(),
            ),
        )

    def healings(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM healings ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["evidence"] = json.loads(r["evidence"] or "{}")
        return rows

    # -- semantic -------------------------------------------------------------
    def add_lesson(
        self, text: str, run_id: str | None = None, tags: list[str] | None = None
    ) -> None:
        vec = self.embedder.embed(text).astype(np.float32)
        self._exec(
            "INSERT INTO lessons(run_id, text, tags, embedding, created_at) VALUES (?,?,?,?,?)",
            (run_id, text, json.dumps(tags or []), vec.tobytes(), time.time()),
        )

    def recall_lessons(
        self, query: str, k: int = 3, min_score: float = 0.25
    ) -> list[dict[str, Any]]:
        rows = self._all("SELECT id, run_id, text, tags, embedding FROM lessons")
        if not rows:
            return []
        matrix = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
        hits = cosine_top_k(self.embedder.embed(query), matrix, k)
        return [
            {"text": rows[i]["text"], "tags": json.loads(rows[i]["tags"]), "score": round(s, 3)}
            for i, s in hits
            if s >= min_score
        ]

    def stats(self) -> dict[str, int]:
        out = {}
        for table in ("runs", "events", "fingerprints", "healings", "lessons"):
            out[table] = self._all(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]
        return out
