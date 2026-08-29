"""Layered Context Manager — 3-layer conversation context with checkpointing.

Replaces the fixed 6-turn history window with:
  Layer 1: Raw recent turns (~800 tokens, verbatim)
  Layer 2: Compressed state JSON (~100-150 tokens, rolling)
  Layer 3: Permanent memory (~100 tokens, relevance-based)

Plus: checkpoint/recovery, relationship promotion, and a separate
compression LLM call (isolated from the response-generation call).

Rules (locked design doc docs/LAYERED_CONTEXT_ARCHITECTURE.md):
- Layer 1 is token-budgeted (~800 tokens, char-based estimate)
- Compression triggers at ~650-700 tokens (safety margin)
- Layer 2 is a rolling state: old Layer 2 + overflow → new Layer 2
- Layer 1 always wins over Layer 2 on contradiction
- Checkpoint atomicity: last_processed_turn advances ONLY after
  compression + persistence both succeed
- Checkpoint is NOT a context layer — it is crash/recovery state only
- Relationship promotion: Layer 2 immediate + Layer 3 async
- Turn Controller precedence: current turn + Layer 1 > Layer 2 > Layer 3
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

# ---- Token estimation (character-based, no tokenizer) ----
CHARS_PER_TOKEN = 4

def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


class LayeredContextManager:
    def __init__(self, log_dir: str = "logs"):
        # Layer 1: raw turns (list of {"role", "content", "tokens"})
        self.layer1: list[dict] = []
        self.layer1_tokens: int = 0
        self.layer1_budget: int = 800  # hard cap

        # Layer 2: compressed state JSON
        self.layer2: dict = {
            "people": {},       # {normalised_name: {name, relation, source}}
            "active_topic": None,
            "open_items": [],   # unresolved questions, promises, tasks
            "emotional_context": None,
        }
        self.layer2_tokens: int = 0
        self.layer2_budget: int = 150

        # Layer 3: memory view (injected from MemoryStore, not managed here)

        # Compression settings
        self.compression_trigger: int = 650  # compress when Layer 1 exceeds this
        self.compression_model: str = os.getenv("AIVA_LLM_MODEL", "gemini-3.5-flash-lite")
        
        # Checkpoint
        self.checkpoint: dict | None = None
        self.last_processed_turn: int = 0
        self.checkpoint_dir = os.path.join(log_dir, "checkpoints")
        os.makedirs(self.checkpoint_dir, exist_ok=True)

        # Provenance tracking
        self.turn_counter: int = 0

    # ---- Layer 1 management ----
    def add_turn(self, role: str, content: str) -> None:
        """Add a turn to Layer 1. Triggers compression if over threshold."""
        tokens = estimate_tokens(content)
        self.layer1.append({
            "role": role,
            "content": content,
            "tokens": tokens,
            "turn": self.turn_counter,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        self.layer1_tokens += tokens
        self.turn_counter += 1

    def needs_compression(self) -> bool:
        return self.layer1_tokens >= self.compression_trigger

    def get_overflow_turns(self) -> list[dict]:
        """Returns turns that should be compressed (oldest first).
        Keeps the most recent turns within budget in Layer 1."""
        overflow = []
        tokens_to_remove = self.layer1_tokens - self.compression_trigger
        kept = 0
        for i, t in enumerate(self.layer1):
            if kept < tokens_to_remove:
                overflow.append(t)
                kept += t["tokens"]
            else:
                break
        return overflow

    def remove_overflow(self, overflow: list[dict]) -> None:
        """Remove compressed turns from Layer 1."""
        overflow_turns = {t["turn"] for t in overflow}
        self.layer1 = [t for t in self.layer1 if t["turn"] not in overflow_turns]
        self.layer1_tokens = sum(t["tokens"] for t in self.layer1)

    def get_layer1(self) -> list[dict]:
        """Returns Layer 1 turns formatted for LLM context."""
        return [{"role": t["role"], "content": t["content"]} for t in self.layer1]

    # ---- Layer 2 management ----
    def set_layer2(self, state: dict) -> None:
        self.layer2 = state
        self.layer2_tokens = estimate_tokens(json.dumps(state, ensure_ascii=False))

    def get_layer2(self) -> dict:
        return self.layer2

    # ---- Layer 3 (delegates to caller / MemoryStore) ----
    def set_layer3(self, memory_lines: list[str]) -> None:
        self._layer3_lines = memory_lines

    def get_layer3(self) -> list[str]:
        return getattr(self, "_layer3_lines", [])

    # ---- Compression prompt ----
    def get_compression_prompt(self, overflow: list[dict]) -> str:
        """Builds the prompt for the compression LLM call."""
        turns_text = "\n".join(
            f"{t['role']}: {t['content']}" for t in overflow
        )
        existing_state = json.dumps(self.layer2, ensure_ascii=False, indent=1)
        return (
            "Update this conversation state with the new turns. "
            "Keep existing people and relationships. Add new ones. "
            "Update emotional context and active topic. Track open items. "
            "NEVER remove existing people or facts unless explicitly corrected. "
            "Max 100 tokens.\n\n"
            f"CURRENT STATE:\n{existing_state}\n\n"
            f"NEW TURNS:\n{turns_text}\n\n"
            "OUTPUT (JSON only, no markdown):\n"
            '{"people": {"name": "relation"}, "active_topic": "...", '
            '"open_items": ["..."], "emotional_context": "..."}'
        )

    # ---- Checkpoint (atomic save/recover) ----
    def save_checkpoint(self) -> bool:
        """Atomically saves checkpoint. Returns True on success."""
        try:
            cp = {
                "checkpoint_id": f"cp_{int(time.time())}",
                "last_processed_turn": self.turn_counter,
                "layer2_state": self.layer2,
                "layer1_turns": self.layer1,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            path = os.path.join(self.checkpoint_dir, "latest_checkpoint.json")
            tmp_path = path + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(cp, f, ensure_ascii=False, indent=1, default=str)
            # Atomic rename (same filesystem)
            os.rename(tmp_path, path)
            self.checkpoint = cp
            self.last_processed_turn = self.turn_counter
            print(f"[Checkpoint] saved at turn {self.turn_counter}")
            return True
        except Exception as e:
            print(f"[Checkpoint] save failed: {e}")
            return False

    def discard_checkpoint(self) -> bool:
        """Remove the checkpoint after a CLEAN session end.

        The checkpoint exists for CRASH recovery only. Saving it at clean
        shutdown made the next session resume with the previous session's raw
        Layer-1 turns (evidence 2026-08-29: session 091548 started with
        hist=106 — the prior session's turns leaked into its context)."""
        try:
            path = os.path.join(self.checkpoint_dir, "latest_checkpoint.json")
            if os.path.exists(path):
                os.remove(path)
            self.checkpoint = None
            print("[Checkpoint] discarded (clean session end)")
            return True
        except Exception as e:
            print(f"[Checkpoint] discard failed: {e}")
            return False

    def recover_from_checkpoint(self) -> bool:
        """Attempts to recover from the latest checkpoint."""
        path = os.path.join(self.checkpoint_dir, "latest_checkpoint.json")
        if not os.path.exists(path):
            print("[Checkpoint] no checkpoint found — starting fresh")
            return False
        try:
            with open(path) as f:
                cp = json.load(f)
            self.layer2 = cp.get("layer2_state", self.layer2)
            self.layer1 = cp.get("layer1_turns", [])
            self.layer1_tokens = sum(t.get("tokens", 0) for t in self.layer1)
            self.turn_counter = cp.get("last_processed_turn", 0)
            self.last_processed_turn = self.turn_counter
            self.checkpoint = cp
            print(f"[Checkpoint] recovered at turn {self.turn_counter}")
            return True
        except Exception as e:
            print(f"[Checkpoint] recovery failed: {e}")
            return False

    # ---- Full context for LLM ----
    def build_context(self) -> str:
        """Builds the 3-layer context string for the LLM call."""
        parts = []
        
        # Layer 3
        l3 = self.get_layer3()
        if l3:
            parts.append("MEMORY (from previous sessions):\n" + "\n".join(f"- {m}" for m in l3))
        
        # Layer 2
        l2 = self.get_layer2()
        if l2.get("people") or l2.get("active_topic") or l2.get("open_items"):
            parts.append("CONVERSATION STATE:\n" + json.dumps(l2, ensure_ascii=False, indent=1))
        
        # Layer 1
        l1 = self.get_layer1()
        if l1:
            hist_lines = [f"{t['role']}: {t['content']}" for t in l1]
            parts.append("RECENT CONVERSATION:\n" + "\n".join(hist_lines))
        
        return "\n\n".join(parts)
