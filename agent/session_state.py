"""aiva.state — per-session state store (Phase 5, 5.3).

Wraps the committed AivaSessionState (locked schemas) around the production
updater. Persists one JSONL line per turn (mirrors the logs/ pattern) and
bridges memory commits to agent.memory_store (C5 owner keying, D3 commit
rules, session-end evaluation).

No interpretation logic: consumes structured heads only (locked boundary).
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

from agent.memory_store import MemoryStore
from agent.state_updater import default_state, derive_policy, update


class SessionState:
    def __init__(self, owner_id: str, store: MemoryStore, log_dir: str = "logs"):
        self.owner_id = owner_id
        self.store = store
        self.state = default_state()
        self.policy = derive_policy(self.state, {"turn": 0})
        self.last_applied_turn = 0
        os.makedirs(log_dir, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.log_path = os.path.join(log_dir, f"state_{ts}_{owner_id[:8]}.jsonl")
        with open(self.log_path, "a") as f:
            f.write(json.dumps({"event": "SESSION_START", "owner_id": owner_id,
                                 "memory_view": self.memory_view()}) + "\n")
        self._pending_log = []

    # ---- context builders for the LLM call (C6: bounded + structured) ----
    def memory_view(self) -> list:
        return self.store.view(self.owner_id)

    def entity_context(self) -> str:
        """Known entities for LLM context — from delta compiler."""
        return self.delta_compiler.to_context_string() if hasattr(self, 'delta_compiler') else ""

    def thread_summaries(self) -> list:
        out = []
        for t in self.state.get("threads", []):
            if t.get("status") in ("active", "paused"):
                out.append(f"{t.get('gist')} ({t.get('status')})")
        return out

    def policy_for_turn(self) -> dict:
        return dict(self.policy)

    def history_window(self, history: list, window: int = 6) -> list:
        """D6: bounded recent-turn window from the existing ConversationSession history."""
        return [{"role": m.role, "content": m.content}
                for m in history[-window:]]

    # ---- updater entry (deterministic; consumes structured head only) ----
    def apply_turn(self, turn_record: dict, head: dict | None) -> dict:
        turn_record = dict(turn_record)
        turn_record.setdefault("owner_id", self.owner_id)
        # P2 async order guard: a cancelled older task must never mutate state
        # out of order. Stale turns are logged, not applied.
        tno = int(turn_record.get("turn") or 0)
        if tno and tno <= self.last_applied_turn:
            with open(self.log_path, "a") as f:
                f.write(json.dumps({"event": "STALE_TURN_SKIPPED", "turn": tno}) + "\n")
            return self.policy
        self.last_applied_turn = tno
        self.state, policy, log = update(self.state, turn_record, head)
        entry = {"turn": turn_record.get("turn"), "head": head,
                  "head_raw_snippet": turn_record.get("head_raw_snippet"),
                  "head_fail_class": turn_record.get("head_fail_class"),
                  "policy": policy, "log": log,
                  "state_digest": {"emotion_primary": self.state["emotion"]["primary"],
                                    "intensity": self.state["emotion"]["intensity"]["ordinal"],
                                    "trajectory": self.state["emotion"]["trajectory"],
                                    "risk_level": self.state["safety"]["risk_level"],
                                    "mode": self.state["mode"]["current"],
                                    "phase": self.state["conversation"]["phase"]},
                  "ts": datetime.now(timezone.utc).isoformat()}
        with open(self.log_path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        self.policy = policy
        self._commit_explicit_memory(head)
        return policy

    def _commit_explicit_memory(self, head: dict | None) -> None:
        for mc in (head or {}).get("memory_candidates", []) or []:
            if mc.get("criterion") == "explicit":
                self.store.commit(self.owner_id, mc, immediate=True)

    # ---- session lifecycle ----
    def end_session(self, keep_pending: bool = True) -> None:
        for c in self.state.get("memory", {}).get("write_candidates", []):
            self.store.commit(self.owner_id, {"type": c.get("type", "semantic"),
                                               "content": c.get("content", ""),
                                               "criterion": c.get("criterion", "salient")})
        self.store.record_session(self.owner_id)
        with open(self.log_path, "a") as f:
            f.write(json.dumps({"event": "SESSION_END"}) + "\n")
