# Root-Cause Report — "Stale context" on the first turn of a fresh session

**Date:** 2026-08-27 · **Mode:** diagnosis only (per instruction — no fixes). HEAD `af6afbe`+turn-controller lineage.

## Answer to the direct question

**"Can a completely fresh session currently receive information from a previous conversation?"**

**YES — through `memory_view`, and that is the designed behavior of the locked
contracts (C5 identity + §4.5 memory).** It is not a leak, not shared state, and
not stale session data. What the owner observed ("Aiva referenced something from
an older conversation on turn 1") is the memory feature doing exactly what it was
locked to do. The remaining question for the owner is a PRODUCT one: whether
first-turn memory seeding should be *proactive referencing* or *silent
availability*. Currently the design says "seed silently" but the persona prompt
does not explicitly forbid proactively referencing old context on turn 1 —
that gap is what makes it *feel* like a bug.

## Field-by-field trace (fresh session, first turn)

| # | Field | Value on turn 1 of a FRESH session | Source |
|---|---|---|---|
| 1 | `ConversationSession.history` | **Guaranteed empty** — a new `ConversationSession()` is constructed per job in `entrypoint()` [main.py:100]; nothing restores/restores into it. `[FACT]` | `agent/session.py` |
| 2 | `SessionState.state` | Fresh `default_state()` — no threads, no ledger, mode VENT `[FACT]` | `agent/session_state.py.__init__` |
| 3 | `memory_view` | **NOT empty by design.** `MemoryStore.view(owner_id)` returns up to 40 committed rows for the SAME device UUID from ALL prior sessions (SQLite `WHERE owner_id=? AND status='committed'`), formatted `"episodic: user is frustrated with work"` etc. `[FACT]` — this is the C5/O2 device-scoped continuity feature | `agent/memory_store.py.view` |
| 4 | `thread_summaries` | Empty on turn 1 (in-session threads only; none loaded from memory) `[FACT]` | `agent/session_state.py.thread_summaries` |
| 5 | `policy` | Fresh VENT policy (derived from empty state) `[FACT]` | `derive_policy(default_state)` |
| 6 | Owner binding | New browser tab with same profile → SAME localStorage UUID → same owner_id → same memory rows. New profile/incognito → new UUID → empty memory `[FACT]` | `App.tsx.getDeviceId`, `token_server` |
| 7 | Worker/job reuse | Worker process is long-lived but session state is per-track-subscription; a new connection creates a new `SessionState`. Old in-memory state is not reused `[FACT]` | `main.py` binding block |

## The exact stale-information path

```text
Browser (same profile as an older chat)
  → localStorage 'aiva_device_id' = SAME UUID
  → /token?device=<uuid>
  → participant identity = <uuid>
  → SessionState(owner=<uuid>, store) — fresh in-memory state
  → turn 1: memory_view() → SQLite: committed rows for <uuid> (e.g.
    "episodic: user is frustrated with work")
  → fused LLM context JSON includes  "memory": ["episodic: ..."]
  → Gemini's first reply may reference it  ← what the owner saw
```

**Additional amplifier found `[FACT]`:** the fused `history` passed to the LLM is
built from `ConversationSession.messages` (`[m for m in messages ...][-6:]`),
and WAIT-suppressed fragments are deliberately appended there (controller
design). So fragments of the *current* session also appear; but cross-session
content can only enter via `memory_view` (item 3) — confirmed no other path.

## Reproduction (offline, exact)

```python
# proven earlier in-session: the offline pipeline check logs
# {"event": "SESSION_START", "owner_id": ..., "memory_view": [...]}
# — memory_view is populated on TURN 1 before any LLM call. Same device UUID
# + committed rows ⇒ they appear in the first fused context JSON's "memory" key.
```

## Where the stale info enters (single point)

`agent/session_state.SessionState.memory_view()` → `MemoryStore.view(owner_id)`
→ fused context key `"memory"`. History and threads are clean on turn 1; policy
is default. **One field.**

## Design assessment (not a bug per locked contracts)

- Locked C5/O2: device-scoped continuity — "reconnect: memory seeded" is in the
  golden suite (G14 expects a memory-seeded first response).
- But G14's rubric says "references the prior thread gently, does not dump".
  Nothing in the *prompt* scopes WHEN memory may be referenced. On a plain
  "Hello, kya kar rahe ho?" the correct conversational behavior is arguably a
  plain greeting, with memory used only as background — the model is currently
  free to (and does) proactively surface old context.

## Recommended fix direction (NOT applied — owner decision needed)

1. Prompt-scoping rule (smallest, contract-safe): add to TRANSPORT persona —
   "Memory is background. Do not mention past-session information unless the
   user brings it up first or asks." One line; keeps G14 seeding behavior.
2. Or: suppress `memory` key on turns where `history` is empty EXCEPT explicit
   topic-match (requires deterministic topic match — heavier, ambiguous).
3. Or: keep current behavior and accept proactive referencing as a feature.

Recommendation: option 1 (one-line persona scoping), owner to approve.

## Verification recipe (owner, after any chosen fix)

Fresh profile/incognito (or click Reset Memory) → "Hello, kya kar rahe ho?" →
inspect turn-1 `llm_context` (persisted in session log): `memory` key will still
LIST the rows (by design), and with option 1 the REPLY should not reference them.
