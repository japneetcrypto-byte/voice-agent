# Live Memory Management — Status vs the Locked 3-Layer Design

**Date:** 2026-08-31 · **Scope:** full audit of the LIVE memory/context path vs
`docs/LAYERED_CONTEXT_ARCHITECTURE.md` (LOCKED DESIGN), `STATE_MODEL_V1.md`
§4.5 (memory state contract), `PHASE3_CONTRACTS.md` C5 (identity), and the
memory-gate directive (2026-08-29). Owner: "memory management is as
important as speech — check it out."

---

## Verdict (one line)

**The READ side of the 3-layer design is live and wired; the WRITE side is
only ~30% implemented** — and until this round, the write path for anything
except family relationships was *dead*, which is exactly why the agent
"could not retrieve from memory" and hallucinated wrong Uttarakhand places.

---

## Layer-by-layer: DESIGNED vs LIVE

### Identity (C5 — the key everything hangs on) — ✅ FULLY LIVE
| Design | Live |
|---|---|
| Device-scoped UUID in browser localStorage, passed as `?device=` to /token | **Yes, end-to-end**: `frontend/src/App.tsx:15-34` generates/reads `aiva_device_id` → appends to `/token?room=…&device=…` → `agent/token_server.py:30-47` validates (invalid → ephemeral id) and binds it as the LiveKit participant identity → `agent/main.py:975-989` reads `participant.identity` → `SessionState(owner_id=…)` → `MemoryStore` keyed by owner. |
| Missing/invalid → ephemeral session-scoped id, memory in-session only | **Yes** (`ephemeral_id()`, logs `IDENT-EPHEMERAL`). |
| Start-fresh escape hatch | **Yes** (`localStorage.removeItem('aiva_device_id')`). |

### Layer 1 — RAW recent turns (~800 tokens, verbatim) — ✅ LIVE
- `response_pipeline.py:82-83` adds every user turn (`lcm.add_turn`); `agent/main.py:429` feeds `history=lcm.get_layer1()` to the LLM; token-budgeted (`needs_compression` at ~650).
- Highest trust, wins on contradiction. **As designed.**

### Layer 2 — COMPRESSED STATE (rolling JSON, ~100-150 tokens) — ⚠️ PARTIALLY LIVE
- Compression trigger + separate background call: **live** — `response_pipeline.py:84-89` → `main.py:420` schedules `_compress_layer2` (own Gemini call, temperature 0.3), on success `set_layer2` + `save_checkpoint` (`main.py:1744-1764`).
- Injected into the LLM payload **only when non-empty** (`fused_turn.build_contents`: people/open_items/emotional_context).
- **Gaps:**
  1. **L2 → L3 promotion does not exist.** The design's rule 10 (relationships → Layer 3 immediately) is handled by the *direct* relationship path, but a fact that only ever lands in L2 (compressed people/open items) is **lost at clean shutdown** — the checkpoint is *discarded* by design (`main.py:1787-1790`), so L2 is crash-recovery-only, never a memory source.
  2. Compression silently no-ops without `GEMINI_API_KEY` (returns early) — no surface warning when L2 stays empty.
  3. `state_delta_compiler` (the deterministic complement named in the design) is **never instantiated** — `session_state.py:42` only guards with `hasattr`.

### Layer 3 — PERMANENT MEMORY (from previous sessions) — ⚠️ READ LIVE, WRITE ~30%
- **Read: live.** `sess.memory_view()` → `store.view(owner)`: `committed` rows, `ORDER BY last_seen DESC LIMIT 40`, prefixed (`relationship:/fact:/episodic:/preference:`). Fed as `memory_view` → payload `memory` every LLM call (`main.py:424`).
- **Write — what is actually live:**
  | Source | Status |
  |---|---|
  | Family relationships (deterministic `extract_entities_from_user_text` → `_promote_relationship`) | ✅ live, pending-first, repeat-confirms |
  | Place/travel facts (`extract_place_facts` → `_promote_memory`, **added this round**) | ✅ now live |
  | LLM-proposed `memory_candidates` (semantic facts, preferences, name, job…) | ❌ **DEAD** — compact head `{m,c,s}` hardcodes `head["memory_candidates"] = []` (`state_updater.py:262-263`); the §4.5 `write_candidates` → end-session-commit path exists but is fed only from those empty candidates |
  | `state_delta_compiler` entity tracking | ❌ dead (never constructed) |
  | Preference supersession / episodic salience decay | ❌ not implemented (only U2 90-day purge) |
  | Retrieval relevance-based ("top N most relevant to current topic", design #5) | ❌ not implemented — it is *recency*-based last-40 |
- **Safety gates: fully live.** Every write routes through `MemoryStore.commit → gate_candidate` (reject / quarantine / pending / commit); quarantined rows never reach `view()`. Raw transcripts are never stored (only deterministic clauses). **This is the strongest part of the live memory system.**

### Checkpoint / crash recovery — ✅ LIVE AS DESIGNED (crash-only)
- `recover_from_checkpoint()` at startup (`main.py:103`), `save_checkpoint()` after each compression, **discarded at clean shutdown** (anti-leak fix, `main.py:1787-1790`). Correct per design ("checkpoint is NOT a context layer"), but it means anything only in L2 is ephemeral — see the L2→L3 gap.

---

## Why the agent hallucinated Uttarakhand places (root cause chain)

1. The user talked about Uttarakhand in an earlier session → the **place fact was never written** (LLM candidates dead; only relationships were captured; `extract_place_facts` didn't exist).
2. Later recall question → Layer 3 had nothing about places → **Layer 1/2 also empty** (session over, checkpoint discarded) → the LLM had *no record at all*.
3. The prompt never told it how to answer when there is no record ("memory is background you silently KNOW") → **it invented places**.

**Fixed this round** (tests-first, in the working tree):
- `extract_place_facts()` — deterministic travel/location capture → episodic pending-first commits.
- Prompt **rule 14 — NEVER INVENT RECALL** + `memory_note` injected when `memory_view` is empty → honest "yaad nahi hai — batao na".

---

## PROGRESS (2026-08-31, owner-directed slices #1 + #2 — DONE, tests-first)

- **#1 L2→L3 promotion** — DONE: `promotable_people()` (pure) + wiring in
  `_compress_layer2`; L2 people with relations reach SQLite mid-session and
  survive restart (checkpoint still discarded by design).
- **#2 Deterministic write path for explicit facts + preferences** — DONE:
  `extract_fact_candidates()` (name/job/likes/no-advice), explicit → immediate
  commit (§4.5), inferred → pending→confirm; `view()` exposes "(explicit)"
  provenance. Place facts are now explicit too.
- **Acceptance**: `test_multisession_recall.py` passes (session-1 state →
  clean shutdown → restart → session-2 payload carries the fact; negative
  control → memory_note, no fabrication). 29/29 suites.
- **Remaining** (staged): #3 relevance-ranked retrieval, #4 compression
  failure visibility, #5 state_delta_compiler, preference enforcement in
  policy.

## What memory management still needs (priority order)

| # | Gap | Why it matters | Cost / note |
|---|---|---|---|
| 1 | **L2 → L3 promotion** (compressed people/facts → committed memory before shutdown) | L2 dies at clean shutdown today; cross-session continuity depends on L3 | Small: in `_compress_layer2`, diff new people/open_items vs store, `_promote_memory` them |
| 2 | **Deterministic write path for preferences + explicit facts** (name, "no advice", job…) | The only remaining dead write path; without it, memory stays relationship+places-only | Extend `entity_extractor` patterns (preference/identity), same pending-first policy |
| 3 | **Relevance-weighted L3 retrieval** (design #5) + salience | Recency-last-40 ignores topic match; long-memory users lose relevant old facts | Keyword/topic match over `content`, keep budget ~100 tokens |
| 4 | **L2 compression failure visibility** (missing key / failed JSON) | Silent no-op today → L2 can be empty without anyone knowing | Log/flag on the diagnostic |
| 5 | (Design-consistent, not a bug) `state_delta_compiler` | Deterministic entity complement is dead | Wire or cut from the design doc |

**Recommended stance (consistent with your controller-first ruling):** keep the
LLM **out of the write path** — deterministic extraction + gates is the safe,
auditable design already in place. Items 1-3 are the natural next slice:
"memory lifecycle = pending → confirmed → retrievable → reused", the same
stateful discipline as the Conversation Controller.

---

*Gates this round: 26/26 suites pass; synthetic replay EMPTY DIFF; real
baseline UNCHANGED. Working tree only (owner WIP uncommitted).*
