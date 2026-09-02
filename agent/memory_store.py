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

from agent.memory_gate import gate_candidate
from agent.memory_units import (EpisodeInfo, FactInfo, mention_keys,
                                time_mark_of, decide_membership,
                                decide_supersede_target)

DEFAULT_DB = os.path.join("logs", "aiva_memory.db")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
RETENTION_DAYS = 90  # U2


def valid_device_id(device: str) -> bool:
    return bool(device) and bool(UUID_RE.fullmatch(device))


def ephemeral_id() -> str:
    return "ephemeral-" + re.sub(r"[^0-9a-f]", "", str(time.time_ns()))[:12]


_SCHEMA = """CREATE TABLE IF NOT EXISTS memory (
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
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id TEXT NOT NULL,
    session_id INTEGER,
    created_at TEXT NOT NULL,
    last_touched_at TEXT NOT NULL,
    archived_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_episodes_owner ON episodes(owner_id, last_touched_at);
CREATE TABLE IF NOT EXISTS memory_mentions (
    fact_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    PRIMARY KEY (fact_id, kind, key)
);
CREATE INDEX IF NOT EXISTS idx_mentions_key ON memory_mentions(kind, key);
CREATE TABLE IF NOT EXISTS session_seq (
    owner_id TEXT PRIMARY KEY,
    last_session_id INTEGER NOT NULL
);
"""

# Episode-memory slice (2026-09-02): cross-session attach window in calendar
# days. Policy knob (env AIVA_W_CROSS_DAYS); the pure rules receive it as a
# parameter — they never read env themselves.
W_CROSS_DAYS = int(os.getenv("AIVA_W_CROSS_DAYS", "30"))


class MemoryStore:
    def __init__(self, db_path: str = DEFAULT_DB):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.executescript(_SCHEMA)
        # Guarded column additions (episode-memory slice) — idempotent across
        # existing databases. Never drops or rewrites existing columns.
        existing_cols = {r[1] for r in self.db.execute("PRAGMA table_info(memory)")}
        for col, decl in (("episode_id", "INTEGER"),
                          ("supersedes_id", "INTEGER"),
                          ("time_mark", "TEXT")):
            if col not in existing_cols:
                self.db.execute(f"ALTER TABLE memory ADD COLUMN {col} {decl}")
        self.db.commit()
        self.db.executescript(_SCHEMA)
        self.db.commit()

    def view(self, owner_id: str) -> list:
        cur = self.db.execute(
            "SELECT type, content, occurrences, sessions, criterion FROM memory "
            "WHERE owner_id=? AND status='committed' ORDER BY last_seen DESC LIMIT 40",
            (owner_id,))
        lines = []
        for typ, content, occ, sess, criterion in cur.fetchall():
            prefix = {"preference": "preference", "relationship": "relationship",
                      "semantic": "fact", "episodic": "episodic",
                      "saved_number": "saved number"}.get(typ, typ)
            suffix = f" (recurring x{occ} across {sess} sessions)" if typ == "relationship" and occ > 1 else ""
            if criterion == "explicit":
                # Provenance (owner acceptance: "provenance should be clear").
                suffix += " (explicit)"
            lines.append(f"{prefix}: {content}{suffix}")
        return lines

    def commit(self, owner_id: str, candidate: dict, immediate: bool = False,
               context: dict | None = None) -> int | None:
        """Commit (or park) one candidate. Returns the affected memory row id
        when the row is NEWLY committed (fresh committed insert, or a pending
        row promoted by this call) AND carries `context`; otherwise None.
        Context = {"session_id": int, "session_start": ISO str, "text": str}
        — when present, a newly-committed row is finalized into the
        episode/memory-units layer (episode attach, mention keys, time mark,
        supersede). All existing callers are unaffected (return ignored).
        """
        typ = candidate.get("type", "semantic")
        content = (candidate.get("content", "") or "").strip()[:200]
        criterion = candidate.get("criterion", "salient")
        now = datetime.now(timezone.utc).isoformat()

        # GUARDRAIL (owner directive 2026-08-29): every write passes the
        # store-level gate BEFORE touching the database. Blast radius of any
        # upstream extractor/policy bug: a rejected or quarantined row —
        # never live-context pollution.
        verdict, reason = gate_candidate(candidate | {"criterion": criterion,
                                                      "immediate": immediate})
        if verdict == "reject":
            print(f"[MemoryGate] REJECTED {typ} {content[:40]!r}: {reason}")
            self.db.commit()
            return None
        if verdict == "quarantine":
            print(f"[MemoryGate] QUARANTINED {typ} {content[:40]!r}: {reason}")
            self.db.execute(
                "INSERT INTO memory (owner_id, type, content, criterion, status, created_at, last_seen)"
                " VALUES (?,?,?,?, 'quarantined', ?, ?)",
                (owner_id, typ, content, criterion, now, now))
            self.db.commit()
            return None

        # Exact-content dedup (status='superseded' rows are excluded: a
        # restatement of a superseded fact is a FRESH fact, never a ghost bump
        # of an invisible row).
        existing = self.db.execute(
            "SELECT id, status FROM memory WHERE owner_id=? AND type=? AND content=?"
            " AND status != 'superseded'",
            (owner_id, typ, content)).fetchone()
        rid: int | None = None
        if existing:
            eid, estatus = existing
            self.db.execute(
                "UPDATE memory SET occurrences=occurrences+1, last_seen=? WHERE id=?",
                (now, eid))
            if immediate and estatus == "pending":
                # Promotion guard (bug 3 of 182736, caught by
                # test_memory_promotion_guard): a REPEAT sighting with
                # immediate=True must promote a still-pending row to
                # committed. Previously the existing-row branch only bumped
                # occurrences and ignored `immediate`, so repeated facts
                # stayed pending forever (until session end).
                self.db.execute(
                    "UPDATE memory SET status='committed' WHERE id=? AND status='pending'",
                    (eid,))
                estatus = "committed"
            self.db.commit()
            # B3: a dedup bump on an ALREADY-committed row never re-runs
            # membership. Only a just-promoted row may finalize (it became
            # committed for the first time, with context).
            if estatus == "committed" and existing[1] == "pending":
                rid = eid
        elif criterion == "explicit" or immediate:
            cur = self.db.execute(
                "INSERT INTO memory (owner_id, type, content, criterion, status, created_at, last_seen)"
                " VALUES (?,?,?,?, 'committed', ?, ?)",
                (owner_id, typ, content, criterion, now, now))
            self.db.commit()
            rid = cur.lastrowid
        else:
            if self.db.execute("SELECT id FROM memory WHERE owner_id=? AND content=? AND status='pending'",
                               (owner_id, content)).fetchone():
                return None
            cur = self.db.execute(
                "INSERT INTO memory (owner_id, type, content, criterion, status, created_at, last_seen)"
                " VALUES (?,?,?,?, 'pending', ?, ?)",
                (owner_id, typ, content, criterion, now, now))
            self.db.commit()
            # Design §4: mention keys are derived at FIRST WRITE even for a
            # pending row (they annotate verbatim content only; they never
            # affect view()).
            if context and context.get("text"):
                text = context["text"]
                keys = mention_keys(text)
                if keys:
                    self._add_mentions(cur.lastrowid, keys)
            return None
        if rid is not None and context:
            self._finalize_fact(owner_id, rid, typ, context)
        return rid

    def lookup(self, owner_id: str, content: str) -> dict | None:
        """Phase-B read-only dedupe helper (session-end consolidation): exact
        content lookup across ALL statuses (committed/pending/quarantined).
        Never mutates — the caller uses it to decide whether a candidate
        already exists before committing."""
        row = self.db.execute(
            "SELECT id, type, content, status, occurrences FROM memory "
            "WHERE owner_id=? AND content=?",
            (owner_id, content)).fetchone()
        if not row:
            return None
        return {"id": row[0], "type": row[1], "content": row[2],
                "status": row[3], "occurrences": row[4]}

    def quarantine(self, owner_id: str, candidate: dict) -> None:
        """Phase-B pass-forced quarantine for anchor-failed LLM candidates
        (session-end consolidation). Same INSERT shape as the MemoryGate
        quarantine path: status='quarantined', invisible to view(), never
        auto-promoted. Deterministic; does not override the gate (the gate has
        no anchor concept — the pass IS the upstream detector here)."""
        typ = candidate.get("type", "semantic")
        content = (candidate.get("content") or "").strip()[:200]
        now = datetime.now(timezone.utc).isoformat()
        if self.lookup(owner_id, content) is not None:
            return  # already stored in any status — never duplicate
        self.db.execute(
            "INSERT INTO memory (owner_id, type, content, criterion, status, created_at, last_seen)"
            " VALUES (?,?,?,?, 'quarantined', ?, ?)",
            (owner_id, typ, content, "salient", now, now))
        self.db.commit()

    def promote_pending(self, owner_id: str, keep: bool = True) -> None:
        """Session-end commit evaluation (D3), GUARDED (directive 2026-08-29):
        a pending row is promoted ONLY if seen >=2 times (repeated facts are
        real; one-offs stay pending to age out or be confirmed next session).
        Closes the blanket-promotion path that would commit one-off garbles."""
        if keep:
            self.db.execute(
                "UPDATE memory SET status='committed' WHERE owner_id=? AND status='pending' "
                "AND occurrences>=2", (owner_id,))
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

    # ------------------------------------------------------------------
    # Episode-memory slice (2026-09-02) — container + mention bookkeeping.
    # All decision logic lives in agent/memory_units.py (pure); the store
    # only persists its verdicts. view()/context output is BYTE-IDENTICAL:
    # episodes/mentions/supersede are internal layers, never in view().
    # ------------------------------------------------------------------
    def start_session(self, owner_id: str) -> int:
        """Per-owner monotonic session id (owner decision 2026-09-02). The
        session-start timestamp is metadata only, never the identity token."""
        self.db.execute(
            "INSERT INTO session_seq (owner_id, last_session_id) VALUES (?, 1) "
            "ON CONFLICT(owner_id) DO UPDATE SET last_session_id = "
            "last_session_id + 1", (owner_id,))
        row = self.db.execute(
            "SELECT last_session_id FROM session_seq WHERE owner_id=?",
            (owner_id,)).fetchone()
        self.db.commit()
        return int(row[0])

    def episode_snapshot(self, owner_id: str) -> list:
        """Non-archived episodes of one owner as pure EpisodeInfo snapshots
        (member time marks + mention keys aggregated)."""
        infos = []
        rows = self.db.execute(
            "SELECT id, session_id, created_at, last_touched_at FROM episodes "
            "WHERE owner_id=? AND archived_at IS NULL ORDER BY last_touched_at",
            (owner_id,)).fetchall()
        for eid, sid, created, touched in rows:
            marks = frozenset(r[0] for r in self.db.execute(
                "SELECT DISTINCT time_mark FROM memory WHERE episode_id=? "
                "AND status='committed' AND time_mark IS NOT NULL", (eid,)))
            keys = frozenset((k, key) for (k, key) in self.db.execute(
                "SELECT mm.kind, mm.key FROM memory_mentions mm WHERE mm.fact_id IN "
                "(SELECT id FROM memory WHERE episode_id=? AND status='committed')",
                (eid,)))
            infos.append(EpisodeInfo(episode_id=eid, session_id=sid,
                                     created_at=created, last_touched_at=touched,
                                     time_marks=marks, keys=keys))
        return infos

    def _ensure_episode(self, owner_id: str, session_id: int | None,
                        now: str) -> int:
        cur = self.db.execute(
            "INSERT INTO episodes (owner_id, session_id, created_at, last_touched_at)"
            " VALUES (?,?,?,?)", (owner_id, session_id, now, now))
        return int(cur.lastrowid)

    def _add_mentions(self, fact_id: int, keys: list) -> None:
        for kind, key in keys:
            self.db.execute(
                "INSERT OR IGNORE INTO memory_mentions (fact_id, kind, key)"
                " VALUES (?,?,?)", (fact_id, kind, key))

    def _committed_facts(self, owner_id: str, kind: str) -> list:
        """Committed same-kind facts (with mention keys) for supersede
        candidate selection. Any committed row is a candidate — including one
        that already superseded an earlier fact (chains F1<-F2<-F3 must
        work); rows whose STATUS is 'superseded' are never candidates."""
        out = []
        for fid, content, last_seen, status in self.db.execute(
                "SELECT id, content, last_seen, status FROM memory "
                "WHERE owner_id=? AND type=? AND status='committed'",
                (owner_id, kind)):
            keys = frozenset((k, key) for (k, key) in self.db.execute(
                "SELECT kind, key FROM memory_mentions WHERE fact_id=?", (fid,)))
            out.append(FactInfo(fact_id=fid, kind=kind, content=content,
                                keys=keys, last_seen=last_seen, status=status))
        return out

    def _finalize_fact(self, owner_id: str, fact_id: int, kind: str,
                       context: dict) -> None:
        """Post-commit annotation for a newly-committed row WITH session
        context: time mark, mention keys, episode attach (or standalone),
        supersede of a same-kind key-overlapping committed fact. Read-only
        vs the fact's content; never edits it."""
        text = (context.get("text") or "").strip()
        session_id = context.get("session_id")
        session_start = context.get("session_start")
        if not text or session_id is None or not session_start:
            # No context -> the row stays committed-but-unannotated
            # (standalone). Never guess.
            return
        now = datetime.now(timezone.utc).isoformat()
        tm = time_mark_of(text)
        decision, ep_id = decide_membership(
            owner_id=owner_id, session_id=session_id, session_start=session_start,
            kind=kind, text=text, episodes=self.episode_snapshot(owner_id),
            w_cross_days=W_CROSS_DAYS)
        if decision == "new":
            ep_id = self._ensure_episode(owner_id, session_id, now)
        if decision in ("attach", "new") and ep_id is not None:
            self.db.execute(
                "UPDATE memory SET episode_id=?, time_mark=? WHERE id=?",
                (ep_id, tm, fact_id))
            self.db.execute(
                "UPDATE episodes SET last_touched_at=? WHERE id=?", (now, ep_id))
        else:
            self.db.execute(
                "UPDATE memory SET time_mark=? WHERE id=?", (tm, fact_id))
        keys = mention_keys(text)
        if keys:
            self._add_mentions(fact_id, keys)
        target = decide_supersede_target(
            kind=kind, text=text, keys=set(keys),
            facts=self._committed_facts(owner_id, kind), exclude_id=fact_id)
        if target is not None:
            # The old row is marked superseded (content untouched, leaves
            # view()); the new row records the pointer. Any committed row may
            # be the target (chains F1<-F2<-F3 work); a 'superseded' row is
            # never a target because it is not in the candidate list.
            self.db.execute(
                "UPDATE memory SET supersedes_id=? WHERE id=?", (target, fact_id))
            self.db.execute(
                "UPDATE memory SET status='superseded' WHERE id=? "
                "AND status='committed'", (target,))
        self.db.commit()
