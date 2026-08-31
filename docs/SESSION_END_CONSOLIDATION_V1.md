# Session-End Consolidation V1 — LLM-proposed candidates, deterministic disposition

**Date:** 2026-09-01 · **Status:** LOCKED (Phase A — design only; Phase B implementation gates on this doc) · **Owner:** approved direction "let's lock this in stages"

**One-line contract:** the session-end consolidation pass is an *additive, best-effort, bounded* post-processing step in which an LLM reviews THIS session's material and proposes memory candidates — which are ALWAYS untrusted until they pass evidence/anchor validation + MemoryGate and are confirmed by deterministic repeat sightings. It can never lose data, never write directly, never touch numbers/PII, and never block worker teardown.

---

## 0. Position in the staged plan

| Phase | Scope | State |
|---|---|---|
| A | This design doc | **DONE (this doc)** |
| B | Session-end consolidation implementation, tests-first (10 acceptance tests §10) | NEXT |
| C | L2 → L3 promotion (completion) | after B |
| D | Relevance-based retrieval / indexing | after C |
| E | `state_delta_compiler`: wire it or remove it | after D |

**Current-state note (accuracy, not staging):** L2→L3 promotion for *people* already ships as memory-continuity slice #1 (`promotable_people()` + `_compress_layer2` wiring, committed 49e0319). Phase C therefore means *completing* L2→L3 for the remaining L2 material (facts / open_items / topics) and coordinating it with the Phase-B pass — the ordering (C after B) is kept as directed so the pass and the promotion share one dedup/confirmation story.

**Retrieval/indexing is explicitly OUT of Phase B scope** (§9). `MemoryStore.view()` stays `ORDER BY last_seen DESC LIMIT 40` until Phase D.

---

## 1. The four stores stay separate (non-negotiable)

Locked by `docs/MEMORY_BOUNDARIES_V1.md`; this slice does not blur them:

| Store | Role in this slice |
|---|---|
| **History** | Never a source of truth for memory; the pass READS this session's validated user turns as its only raw material and must not echo them into memory |
| **Working State** | Never persisted; the Conversation Controller's task/topic state is NOT input to the pass (tasks die with the session) |
| **Session Memory** | The only bridge to Long-term: the pass's bullets enter here as `pending` (or `quarantined`); deterministic captures also live here |
| **Long-term Memory** | Only store that survives; the pass never reads it as input and only ever adds `pending` rows to it (never committed rows) |

**Boundary rules re-affirmed:** History ≠ Working State ≠ Session Memory ≠ Long-term Memory. The LLM never writes trusted memory. Raw transcripts are never stored. The perception head's `memory_candidates=[]` hardcode stays — the pass is a *separate, dedicated* call, not a re-opening of the head.

---

## 2. Locked pipeline

```
LLM (proposal, untrusted)
   │
   ▼
Candidate (schema-validated; criterion is ALWAYS "salient", never "explicit")
   │
   ▼
Evidence / anchor validation  ──► fail ──► QUARANTINE (or REJECT if degenerate)
   │
   ▼
MemoryGate (gate_candidate — last line of defense, unchanged)
   │
   ▼
pending  (or quarantine / reject / dedupe-skip)
   │
   ▼
Later confirmation (DETERMINISTIC repeat sighting only — extractor, L2 promotion,
                    saved-number confirm; NEVER another LLM proposal)
   │
   ▼
commit
```

Locked principles:
1. **LLM output is ALWAYS a candidate, never trusted memory.**
2. `nothing_important_missed` is **telemetry only, never an authority** (§8).
3. The LLM cannot set `criterion`, `status`, or `type` outside the enum — the pipeline sets criterion, the store sets status.
4. **A LLM proposal can never confirm a LLM proposal.** Recurrence (the confirmation signal) is earned only from deterministic sources. Otherwise an LLM could confirm its own inventions across sessions.
5. **LLM proposals never bump `occurrences`** of existing rows — recurrence counts are reserved for deterministic sightings.

---

## 3. Exact input boundary (what may enter the pass)

The pass receives ONE prompt built from, and only from:

1. **This session's validated USER turns** — `is_valid=True`, not echo, not dropped, not rail-owned, not greeting, not suppressed/WAIT. Assistant turns are EXCLUDED (the agent cannot state facts about the user). Cap: last **25** such turns or **~3000 chars**, oldest-first.
2. **The rolling Layer-2 state produced THIS session** (`lcm.get_layer2()`), if any — people/active_topic/open_items/emotional_context. If empty or compression failed, the pass still runs on (1) and logs the L2 status (§8).
3. **The session's deterministic captures** — `turn["place_facts"]`, `turn["fact_candidates"]`, `turn["user_relations"]`, saved-number confirm events (**digits redacted**, see below). These are passed to the pass as "already captured" so the LLM does not re-propose them and the completeness diff has its deterministic side.

**Hard prohibitions (never enter the prompt):**
- ❌ `memory_view()` / any previous-session memory row (Long-term is never an input — acceptance test 5).
- ❌ Numbers / PII / digits: any ASCII digit run ≥4 chars and any Devanagari digit run (`०१२…`, U+0966–U+096F) ≥4 chars are replaced with `[REDACTED]` before the prompt is built; dictated-number turns are excluded from the turn feed entirely; `saved_number` is NOT a valid bullet type (acceptance test 4).
- ❌ Raw transcripts as output material — the LLM never returns transcript text into memory; bullets must be canonical third-person lines.
- ❌ Working State (controller task/topic), checkpoint contents, owner metadata beyond the owner's own id used for keying.
- ❌ Assistant replies, supervisor/idle/rail/greeting turns.

---

## 4. Exact whitelisted output schema

One call, temperature 0.3, `gemini-3.5-flash-lite` (same model/budget discipline as L2 compression), single attempt, 15s timeout. JSON only, no markdown; unclosed JSON salvaged with the same logic as `salvage_unclosed_head`; any other failure → deterministic fallback (§7).

```json
{
  "bullets": [
    {
      "type": "preference" | "relationship" | "episodic" | "semantic",
      "content": "<canonical third-person line, max 200 chars>",
      "turn_ref": 12,
      "confidence": "low" | "med" | "high"
    }
  ],
  "nothing_important_missed": true
}
```

**Locked parsing rules:**
- `bullets` is an array, **0–8** entries. More → truncate to 8 with a log line.
- `type` ∈ {`preference`, `relationship`, `episodic`, `semantic`} — exactly the committed `view()` prefixes. **No `saved_number`, no `fact`, no free-form type.**
- `content`: 6–200 chars (MemoryGate floor is 6 / 3 letters), must not contain digits, must not contain `[REDACTED]` or any PII marker.
- `turn_ref`: integer present in this session's validated-user-turn range. Missing/out-of-range → quarantine (anchor failure).
- `confidence` ∈ {`low`, `med`, `high`}. Mapped: high → `salient`; med/low → `salient` with lower priority ordering in the diff log. **No confidence level ever maps to `explicit`.**
- `nothing_important_missed`: boolean; **telemetry only** (§8).
- Any extra top-level key, any type outside the enum, any non-JSON → **parse-reject the whole pass**: zero bullets accepted, deterministic captures untouched, loud log. A strict fail is safer than a partial acceptance (no partially-trusted LLM writes).

---

## 5. Evidence / anchor validation (deterministic, before MemoryGate)

Every bullet must be anchored to the session material it claims:

1. **Turn anchor:** `turn_ref` must reference a validated user turn from §3(1), and ≥1 content word of `content` (after stopword removal; `USER_STOPWORDS` + Hindi function words) must appear in that turn's text. Lexical overlap is a *necessary* condition — it is not sufficient trust, but its absence is decisive.
2. **Type-anchor consistency:**
   - `relationship` → the anchor word(s) must look like a name/relation mention (the MemoryGate R2 name checks run as backstop).
   - `episodic` → anchor must contain a place/travel content word (`गया/गई/घूम/रहता/हूँ/visited/…` or a place token); else quarantine.
   - `preference` / `semantic` → anchor must contain a first-person statement signal (`मुझे/मेरा/मेरे/मैं/I/my/…`); else quarantine. (Questions and commands are never anchored: the turn text is validated user speech, and the anchor word must be a statement-bearing word, not a question word.)
3. **Dedup anchor:** if `content` already exists as a committed, pending, or quarantined row for this owner (store lookup), or equals a deterministic capture from §3(3) → **skip** (dedupe, no new row, no occurrence bump) — acceptance test 7.

Outcomes:
- Anchored + gate passes → `pending` (criterion `salient`).
- Anchored + gate quarantine verdict (e.g., relationship name fails R2) → `quarantined`.
- Unanchored (any of 1–2 fail) → `quarantined` (suspicious but kept for audit), never pending, never committed.
- Degenerate (gate R1: <6 chars / <3 letters) → `reject` (not stored at all, logged).

---

## 6. MemoryGate behaviour (unchanged, last line of defense)

`MemoryStore.commit()` continues to route EVERY candidate — deterministic or LLM — through `gate_candidate` before touching the database. The Phase-B anchor layer runs *before* the gate, but the gate remains authoritative: a buggy anchor layer can still only produce a `quarantined` or `pending` row, never live-context pollution. No changes to `memory_gate.py` in Phase B.

`commit()` semantics reused as-is:
- `criterion="explicit"` + `immediate` → committed (deterministic extractors ONLY — the pass never calls this path).
- otherwise → pending row on first sighting; existing pending row with same content → the pass-level dedupe skips before `commit()` is even called.

**Phase-B addition (small, read-only):** `MemoryStore.lookup(owner_id, content) -> (id, type, status, occurrences) | None` — a deterministic read helper so the pass can dedupe against committed/pending/quarantined rows without bumping anything. No behavior change to existing write paths.

---

## 7. Pending vs commit semantics (locked)

| Candidate origin | First sighting | Later confirmation | If never confirmed |
|---|---|---|---|
| Deterministic extractor, explicit | committed immediately (immediate branch) | n/a | n/a |
| Deterministic extractor / L2 promotion, inferred | pending | repeat sighting (deterministic) or session-end `occurrences>=2` → committed | ages out (U2 90-day purge on `last_seen`; pending never promoted on 1 occurrence) |
| **LLM pass bullet** | **pending** (never committed) | **deterministic repeat only** — the same fact re-captured by an extractor / L2 promotion / saved-number confirm → that deterministic sighting bumps occurrences / promotes | ages out (U2); quarantine rows never auto-promote |
| Unanchored LLM bullet | quarantined | n/a (manual audit only) | purgeable, invisible to `view()` |

**Failure / timeout / teardown behaviour (locked):**
1. The pass runs as an `asyncio` task with a **15s hard timeout**, one attempt, **no retry loop**. Timeout → task cancelled, teardown proceeds.
2. LLM error / timeout / invalid JSON / empty bullets → **additive-only fallback**: deterministic captures were already committed (explicit) or stored pending mid-session; `end_session()` runs exactly as today (`promote_pending(keep=True)`, `occurrences>=2`); **nothing is lost** (acceptance tests 3 and 9).
3. The pass is triggered from the existing session-end/shutdown path (`_commit_session_memory` hook) — it is **never** on the response path, never awaited by a user turn, never awaited by worker teardown beyond the bounded task (§ acceptance test 10).
4. Empty session (no validated user turns) → pass skipped with a log line.
5. The pass may run at most once per session.

---

## 8. Completeness diff + logging / observability

**Completeness diff (deterministic, after the pass):**
- Let **D** = this session's deterministic captures (place facts ∪ fact candidates ∪ relationships ∪ saved-number confirm events, digits redacted in logs) that are already in the store (committed or pending).
- Let **B** = bullets the pass produced that reached any store outcome (pending / quarantine / dedupe-skip).
- Compute **D ∖ B** = deterministic captures the LLM did not independently propose.
- Log every D item with its status and whether the LLM covered it (`covered|not_covered`). The diff is **read-only**: it can never alter, remove, or add a deterministic row.
- `nothing_important_missed` from the LLM is compared against |D ∖ B|: if the LLM claimed `true` but the diff is large (>0) → log a **coverage warning** (soft signal). The bool never gates anything; the diff is the real check. **Telemetry only, never an authority.**

**Log lines (worker stdout + `state_*.jsonl` event + a non-turn JSON line in the session log, which diagnostics skip):**
- `[SessionConsolidation] owner=<short> bullets_proposed=N anchored=M quarantined=Q rejected=R deduped=K nothing_missed=<bool|unparsed> L2_state=<present|empty|failed> duration_ms=…`
- Per-bullet audit: `[SessionConsolidation] bullet type=<t> status=<pending|quarantine|reject|dedupe> turn_ref=<n> content=<40c>`
- Events: `CONSOLIDATION_STARTED` / `CONSOLIDATION_COMPLETED` / `CONSOLIDATION_FAILED` (reason + duration).
- **Failure visibility (#4 folded into this slice):** if Layer-2 is empty or compression failed, the `L2_state` field says so — no more silent no-op.

---

## 9. Explicitly OUT of scope for Phase B (locked)

- Retrieval / indexing / ranking changes — `view()` stays recency-last-40 (Phase D).
- `state_delta_compiler` wiring or removal (Phase E).
- Preference enforcement in `_derive_policy`.
- Changes to `precision_rail.py`, `conversation_controller.py`, `fused_turn.py` / perception head, `state_updater.py`, `prompt_fragments.py`, `memory_gate.py` behaviour.
- Any prompt/behavior change to the live response path.
- No broad refactor: Phase B touches only the new consolidation module, its wiring point, the read-only store lookup, and new tests.

---

## 10. Acceptance tests (Phase B, tests-first — the gate for Phase C)

New suite `phase5/tests/` (files below); all existing 30 suites must stay green and the real baseline gate must stay UNCHANGED (no rail/controller/TTS edits to drift it).

| # | Test | Proves |
|---|---|---|
| 1 | `test_llm_cannot_commit_explicit.py` | Even when the LLM returns `"criterion":"explicit"` / status keys (schema injection attempt), the pass hardcodes `salient`; the store row is `pending`, never `committed`. |
| 2 | `test_unanchored_candidate_quarantine.py` | A bullet whose `turn_ref`/content has no lexical anchor in the referenced turn → quarantined (never pending, never committed). |
| 3 | `test_deterministic_captures_survive_llm_failure.py` | Gemini raises / times out / returns invalid JSON → place facts, fact candidates, relationships, saved-number commits already in store; `end_session()` still promotes `occurrences>=2`; nothing lost. |
| 4 | `test_pii_never_enters_prompt.py` | A session containing a dictated number → the pass prompt contains no digit runs (ASCII or Devanagari), no saved-number row text; `[REDACTED]` markers only. |
| 5 | `test_previous_session_memory_not_input.py` | Rows committed in a prior session (injected store) never appear in the pass prompt; only this session's turns/L2/captures do. |
| 6 | `test_owner_isolation.py` | Owner A's pass cannot read or commit owner B's rows; all Phase-B writes are keyed to the session owner (C5). |
| 7 | `test_duplicate_proposals_deduped.py` | The same bullet proposed twice (one pass or across passes) creates ONE row; `occurrences` is NOT bumped by LLM proposals. |
| 8 | `test_completeness_diff_detects_omissions.py` | Deterministic capture present + LLM bullets omit it → diff flags it (`not_covered`), coverage warning logged, deterministic row untouched. |
| 9 | `test_clean_shutdown_preserves_deterministic.py` | Clean shutdown with a failing pass → explicit commits intact and `occurrences>=2` pending promoted (mirrors `test_multisession_recall`); a later session recalls the facts. |
| 10 | `test_consolidation_bounded_nonblocking.py` | A 30s-stalling LLM inside the pass → teardown completes within the ~15s budget; task cancelled; worker shutdown not blocked; no retry loop. |

Each test is written RED first (failing for the absence of the Phase-B behavior), then the implementation makes it green — the same tests-first discipline as slices #1/#2 and the rail work.

---

## 11. Phase B implementation footprint (locked, minimal)

- **New:** `agent/session_consolidation.py` — pure functions: prompt builder (with §3 input assembly + PII redaction), JSON parser/salvage + schema whitelist, anchor validator, completeness diff, orchestrator `consolidate(engine) -> summary` (bounded task).
- **New:** `MemoryStore.lookup(owner_id, content)` — read-only dedupe helper (§6).
- **Wiring:** `agent/main.py` — inside the existing `_commit_session_memory()` shutdown hook: schedule the bounded consolidation task before/alongside `end_session()`; log §8 lines. No other main.py path touched.
- **Tests:** the 10 suites of §10 under `phase5/tests/`.

**Definition of done (Phase B):** 40/40 suites green (30 existing + 10 new); replay identity ALL PASS; real baseline DIVERGENCE unchanged; `[SessionConsolidation]` lines visible in a synthetic end-to-end run; owner smoke on the deployed build per the Phase-0 protocol.
