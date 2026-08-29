"""aiva.memory - SQLite memory store (Phase 5, 5.6).

Contract: docs/PHASE3_CONTRACTS.md C5 (identity) + STATE_MODEL_V1.1 section 4.5
(memory principles), owner rulings U2 (90-day orphan purge) and D3 (explicit
auto-commit, others at session end).

Deterministic, stdlib-only. No LLM calls. Raw transcripts are never stored.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

DEFAULT_DB = os.path.join("logs", "aiva_memory.db")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
RETENTION_DAYS = 90  # U2


def valid_device_id(device: str) -> bool:
    return bool(device) and bool(UUID_RE.fullmatch(device))


def ephemeral_id() -> str:
    return "ephemeral-" + re.sub(r"[^0-9a-f]", "", str(time.time_ns()))[:12]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id TEXT NOT NULL,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    criterion TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    occurrences INTEGER NOT NULL DEFAULT 1,
    sessions INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_memory_owner ON memory(owner_id, type);
"""


class MemoryStore:
    def __init__(self, db_path: str = DEFAULT_DB):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.executescript(_SCHEMA)
        self.db.commit()

    def view(self, owner_id: str) -> list:
        cur = self.db.execute(
            "SELECT type, content, occurrences, sessions FROM memory "
            "WHERE owner_id=? AND status='committed' ORDER BY last_seen DESC LIMIT 40",
            (owner_id,))
        lines = []
        for typ, content, occ, sess in cur.fetchall():
            prefix = {"preference": "preference", "relationship": "relationship",
                      "semantic": "fact", "episodic": "episodic"}.get(typ, typ)
            suffix = f" (recurring x{occ} across {sess} sessions)" if typ == "relationship" and occ > 1 else ""
            lines.append(f"{prefix}: {content}{suffix}")
        return lines

    def commit(self, owner_id: str, candidate: dict, immediate: bool = False) -> None:
        typ = candidate.get("type", "semantic")
        content = (candidate.get("content", "") or "").strip()[:200]
        criterion = candidate.get("criterion", "salient")
        now = datetime.now(timezone.utc).isoformat()
        existing = self.db.execute(
            "SELECT id FROM memory WHERE owner_id=? AND type=? AND content=?",
            (owner_id, typ, content)).fetchone()
        if existing:
            self.db.execute(
                "UPDATE memory SET occurrences=occurrences+1, last_seen=? WHERE id=?",
                (now, existing[0]))
            if immediate:
                # Promotion guard (bug 3 of 182736, caught by
                # test_memory_promotion_guard): a REPEAT sighting with
                # immediate=True must promote a still-pending row to
                # committed. Previously the existing-row branch only bumped
                # occurrences and ignored `immediate`, so repeated facts
                # stayed pending forever (until session end).
                self.db.execute(
                    "UPDATE memory SET status='committed' WHERE id=? AND status='pending'",
                    (existing[0],))
        elif criterion == "explicit" or immediate:
            self.db.execute(
                "INSERT INTO memory (owner_id, type, content, criterion, status, created_at, last_seen)"
                " VALUES (?,?,?,?, 'committed', ?, ?)",
                (owner_id, typ, content, criterion, now, now))
        else:
            if self.db.execute("SELECT id FROM memory WHERE owner_id=? AND content=? AND status='pending'",
                               (owner_id, content)).fetchone():
                return
            self.db.execute(
                "INSERT INTO memory (owner_id, type, content, criterion, status, created_at, last_seen)"
                " VALUES (?,?,?,?, 'pending', ?, ?)",
                (owner_id, typ, content, criterion, now, now))
        self.db.commit()

    def promote_pending(self, owner_id: str, keep: bool = True) -> None:
        """Session-end commit evaluation (D3): pending -> committed (or dropped)."""
        if keep:
            self.db.execute(
                "UPDATE memory SET status='committed' WHERE owner_id=? AND status='pending'",
                (owner_id,))
        else:
            self.db.execute("DELETE FROM memory WHERE owner_id=? AND status='pending'", (owner_id,))
        self.db.execute(
            "UPDATE memory SET sessions=sessions+1, last_seen=? WHERE owner_id=? AND status='committed'",
            (datetime.now(timezone.utc).isoformat(), owner_id))
        self.db.commit()

    def record_session(self, owner_id: str) -> None:
        self.promote_pending(owner_id, keep=True)

    def purge_orphans(self, days: int = RETENTION_DAYS) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cur = self.db.execute(
            "DELETE FROM memory WHERE last_seen < ? AND status != 'pending'", (cutoff,))
        self.db.commit()
        return cur.rowcount
