# Phase 3 — Data Contracts (PROPOSAL — review then lock)

**Date:** 2026-08-26 · **Basis:** `STATE_MODEL_V1.1` (locked direction) + `PHASE3_FUSED_VALIDATION.md` (Task 1, accepted) + `LLM_API_AUDIT.md`.
**Scope:** contract design only. No implementation, no architecture change, no new state dimensions, no prosody work, no provider change.

**Unresolved decisions are labeled U1–U6 (§9) — nothing decided silently.**

---

## C1. Perception-head schema — `aiva.perception/v1`

The single JSON object streamed inside `<perception>...</perception>`. **This is the only LLM-produced structured artifact in the system.**

```json
{
  "v": 1,
  "emotion": {
    "primary": "anger_frustration|sadness|anxiety|overwhelm|loneliness_hurt|guilt_shame|relief|neutral_unclear",
    "valence": "negative|neutral|positive",
    "intensity": {"ordinal": 1},
    "confidence": 0.0,
    "evidence_quote": "verbatim ≤120 chars from the user's current message"
  },
  "thread": {
    "action": "new|continue|switch|return",
    "gist": "≤60 chars topic label",
    "entities": ["Name (role)"]
  },
  "safety": {
    "risk_level": "none|low|elevated_distress|high_risk",
    "self_harm": false,
    "harm_to_others": false,
    "other_flagged": false,
    "confidence": 0.0
  },
  "user_need": "be_heard|advice|clarify|other",
  "advice_requested": false,
  "memory_candidates": [
    {"type": "episodic|semantic|relationship|preference",
     "content": "≤140 chars, one line",
     "criterion": "explicit|salient|recurrent|corrective"}
  ]
}
```

**Field rules (prompt-enforced + parser-validated):**
- `emotion.primary`: **exactly one of the 8 canonical labels** — prompt hardening added per Task 1 finding: *"primary MUST be copied exactly from this list; never invent or modify a label."*
- `intensity.ordinal`: integer 1–5. `confidence`: float 0–1. `evidence_quote`: verbatim substring of the current turn (this becomes a channel=`transcript` evidence record downstream).
- `thread.entities`: plain strings, `"Name (role)"` optional suffix, max 5 [minor format decision — see U5].
- `memory_candidates`: ≤3 entries; empty array normal.
- Unknown fields / extra keys: **ignored by parser, logged** (forward compatibility).
- `safety` enums are hard: invalid → degradation rule D3 (never silently mapped).

### C1.1 Taxonomy normalization (deterministic, in the state updater — never an LLM call)

Ordered first-match table applied to the raw `emotion.primary` string (case-insensitive substring match):

| Raw label contains | Canonical | 
|---|---|
| `overwhelm` or `exhaust` or `burnout` | `overwhelm` |
| `anger` / `frustrat` / `irritat` / `annoy` | `anger_frustration` |
| `anx` / `worry` / `nervous` / `panic` / `stress` | `anxiety` |
| `lonely` / `alone` / `abandon` | `loneliness_hurt` |
| `hurt` | `loneliness_hurt` |
| `guilt` / `shame` / `ashamed` | `guilt_shame` |
| `sad` / `down` / `cry` / `udaas` / `dukhi` | `sadness` |
| `relief` / `relieved` | `relief` |
| `neutral` / `unclear` / `unsure` / `calm` | `neutral_unclear` |
| no match | `neutral_unclear` |

- **Confidence penalty: −0.10 per normalization** (Task 1 agreed); no-match → additionally cap confidence at 0.3.
- Every application logged with reason code `NORM-LABEL:<raw>→<canonical>`.
- Worked example (Task 1): `exhaustion_overwhelm` → matches `overwhelm` → `overwhelm`, confidence 0.95 → 0.85. ✓
- `valence` coercion: if canonical label is negative-family and `valence=="positive"` → `valence="negative"` (exception: `relief` keeps its valence). Logged `NORM-VALENCE`.

### C1.2 Evidence / confidence envelope (updater-owned)

The head carries one inline evidence (`evidence_quote`). The **updater** constructs the full channel-tagged evidence records per locked §3.1 — it owns all weights:

| Channel | Source | v1 weight cap |
|---|---|---|
| `user_correction` | turn record | 0.95 (override — beats everything) |
| `transcript` | `evidence_quote` | 0.4 |
| `history` | prior estimates/thread | 0.2 |
| `acoustic` | turn record RMS/peak/duration/rate | **≤0.2, within-session-relative only (A10)** |

Confidence caps (unchanged from v1.1 §4.2): transcript-only ≤ 0.5 · +acoustic corroboration ≤ 0.7 · acoustic-only ≤ 0.3 · channel conflict (incongruence) ≤ 0.4 · correction present → 0.95.

## C2. Persona contract — `aiva.persona/v1`

- **Self-reference: masculine grammatical forms**, matching the cloned voice (the user's own voice): *"main sun **raha** hoon"*, *"main **jaunga**"*. Feminine self-forms (**"sun rahi hoon/sunungi"**, *"jaungi"*) are **prohibited** — Task 1 finding #2.
- Where gendered agreement is unavoidable, prefer voice-neutral constructions ("main yahin hoon", "main sun raha hoon").
- This is a **contract field**: `persona.self_reference = "masculine"`. If the clone voice ever changes, this single field changes; prompt text is generated from it.
- Persona prompt fragment is versioned (`PERSONA_V2` = V1 rules + self-reference line + exact-taxonomy line). Prompt fragment versions are logged per turn for auditability.

## C3. Thread contract — action semantics (low-stakes, per Task 1)

Head `thread.action` is **advisory**. The deterministic updater resolves it:

| Action | Updater behavior |
|---|---|
| `new` | create thread only if no active/paused thread matches gist+entities (conservative match); else degrade → `continue` |
| `continue` | update `last_active_turn`, merge new entities/events into active thread |
| `switch` | requires an identifiable new thread (new gist/entities); else degrade → `continue` |
| `return` | requires a **confident match to a paused thread** (gist or shared entity); re-activates it, records `return_event`, re-points `active_thread`; **if no confident match → degrade → `continue`** (never guesses) |

**Low-stakes guarantee (Task 1 finding #3):** `continue` and `return` are structurally identical except for `return_event` + active-thread pointer. A mislabeled action can never close, merge, or destroy a thread. Inactive 10+ turns → `closed` (D7 parameter). Mismatches logged `THREAD-DEGRADE:<from>→<to>`.

## C4. Fused response contract — `aiva.transport/v1` (byte-shape = Task 1 validated shape, unchanged)

```
<perception>{"v":1, ... }</perception>

<prose — ≤2 sentences, spoken style, streamed>
```

- Head: single JSON object, **no code fences** (parser strips them if present — tolerance, not license), no comments, no keys outside C1.
- **Machine-parse rule:** scanner accumulates the stream; head complete at `</perception>`; head = JSON between tags; **prose = everything after the closing tag, leading whitespace stripped** → prose tokens stream directly to TTS from that point (validated in Task 1: head ≈1.3–1.6 s, prose immediately after).
- The prompt fragment specifying this shape is versioned `TRANSPORT_V1`; **the delimiter scheme is frozen** — changing delimiters requires a new transport version + revalidation, not a silent edit.
- Prompt order is fixed: system(persona+transport rules) → policy block → memory view → thread summaries → recent turns → user turn. Field order within the head JSON is **not** guaranteed (JSON object semantics); parser is key-based, never position-based.

## C5. Device-scoped identity contract — `aiva.identity/v1` (O2; **not implemented in this task**)

- **Identity = `device_id`: UUIDv4**, generated in the browser on first load, stored at `localStorage["aiva_device_id"]`, **never regenerated** except by explicit start-fresh.
- **Handoff:** frontend appends `?device=<uuid>` to `GET /token`; token server validates UUID format and **binds it as the participant identity** (`with_identity(device_id)`); the worker reads the participant identity from the room → that value is the Memory `owner_id`. One relay, no new services.
- Invalid/missing device param → token server issues an **ephemeral session-scoped id** (memory functions in-session, nothing persists) + log `IDENT-EPHEMERAL`. Never reject the user for a missing ID.
- **Ownership/lifecycle:** owned by the browser profile; survives reloads; not shared across browsers/devices (documented v1 limitation); no PII; transmitted only to token server + LiveKit.
- **Start-fresh:** frontend action deletes the localStorage key → new UUID on next load → new memory owner; old memories orphaned, not deleted.
- Implementation touchpoints (Phase 5, after lock): `frontend/src/App.tsx` (generate+attach), `agent/token_server.py` (validate+bind). Zero worker changes (identity already flows through participant identity).
- **Open items (flagged, not decided):** U2 retention/purge of orphaned memories; shared-device multi-user separation = explicitly OUT (v1 limitation).

## C6. State updater contract — `aiva.updater/v1`

**`update(prev_state, turn_record, perception_head | None, session_events) → (new_state, policy, update_log)`**

- **Pure, deterministic, offline.** No LLM calls, no network, no clock-dependence beyond turn timestamps. Same inputs → same outputs. Runs exactly once per turn (including rejected, acoustic-only, idle, and interrupted turns), latest-turn-wins.
- **Frozen parameter table** (all D7 internal parameters, single source of truth, tunable only by version bump): evidence weights (C1.2) · ring 5 · decay 3 turns · trajectory window 5 · mode hysteresis 2 · thread close 10 · safety de-escalation 3 consecutive safe turns · pacing ceilings (2 sentences / 1 question / ≤2 consecutive question turns).

**Fixed evaluation order (determinism by construction):**
1. Normalize head (C1.1) — or degradation path (C7) if head absent/invalid.
2. Apply `user_correction` if present → emotion := user's assertion, confidence 0.95 (`CORR-OVERRIDE`), store correction; optional preference memory candidate (`corrective`).
3. Assemble evidence records (C1.2 channels/weights).
4. Apply confidence caps (C1.2).
5. Commit emotion → push to `recent_estimates` ring → derive trajectory: `rising` if ordinal climb ≥2 across ring on negative labels; `falling` if drop ≥2; direction change ≥2 → `fluctuating`; else `stable` (`TRAJ-*`).
6. Decay: primary without corroborating evidence for 3 turns → drift to `neutral_unclear`, confidence ×0.6/turn (`DECAY`).
7. Thread update (C3 semantics; `THREAD-DEGRADE` on mismatch).
8. Memory candidates → validate `criterion` → `write_candidates` (auto-commit only `criterion=explicit`; rest at session end — D3).
9. Safety evaluation → override resolution. **Precedence: safety > explicit user request > mode rules.** `high_risk`/`elevated_distress` set override; de-escalation requires 3 consecutive `none/low` turns (`SAFE-HYSTERESIS`).
10. Mode transition (hysteresis 2 unless explicit request/safety).
11. Derive `phase` (mode + trajectory + session duration + explicit close).
12. Derive **policy** per v1.1 §4.8 rules (incl. turn-type rules S17/S18, interruption rule, ambiguity no-label rule).
13. Ledger update — only fully-spoken responses count toward `advice_given`/`last_move`.

- **Update log:** every run appends reason-coded entries (`NORM-LABEL`, `NORM-VALENCE`, `CORR-OVERRIDE`, `CAP-CHANNEL`, `TRAJ-*`, `DECAY`, `THREAD-DEGRADE`, `HYST-BLOCK`, `SAFE-OVERRIDE`, `SAFE-HYSTERESIS`, `POLICY-*`) — the regression-testable audit trail required by v1.1 §4.8 failure modes.

## C7. Failure & degradation contracts

| # | Trigger | Behavior | State impact | User experience |
|---|---|---|---|---|
| D1 | **Malformed head** (JSON invalid/fenced+unparseable) | stream continues; prose used as reply; head dropped | **no perception update this turn**; log `PARSE-FAIL` | response plays normally; next turn unaffected |
| D2 | **Missing head** (no `<perception>` tags) | identical to D1; prose = full stream | same | same |
| D3 | **Invalid enum** after normalization (e.g., safety risk_level garbage) | emotion → `neutral_unclear`, conf ≤0.3 (`NORM-UNKNOWN`); **safety → `low` + `other_flagged=true`** (`SAFE-INVALID` — never silently `none`, never auto-escalate) | flagged in handling_log | unaffected |
| D4 | **LLM failure** (429/timeout/5xx) | **one retry, only if zero prose streamed** (audit rule — no duplicate speech); if still failing → **deterministic filler** from a fixed list (e.g., "Main yahin hoon, thodi technical dikkat aa gayi — main aa gaya phir se, batao.") | turn logged `LLM-FAIL`; no state churn; consecutive-failure counter | brief, honest stumble — not silence ⚠ **U1 (wording/approval)** |
| D4b | **Partial stream** (prose started, then failure) | never restart; if ≥1 complete sentence arrived → play what arrived and stop cleanly; else → D4 filler | same | truncated-but-clean audio |
| D5 | **Safety uncertainty** (figurative/conflict) | clarify-first tier (v1.1); D3 for invalid enums | override only on confident levels | gentle check-in, never panic |
| D6 | **Interrupted response** | existing cancel path; truncated text stored with marker; next policy includes `avoid: repeating_interrupted_content` + brief resume | ledger: move invalidated; not counted as advice | agent stops, listens, resumes briefly |
| D7 | **Acoustic-only turn** (S17: energy gate passed, no valid transcript) | **LLM is skipped entirely** (nothing to perceive — saves the call); response = deterministic presence line from fixed list ("Main sun raha hoon, main yahin hoon.") | emotion: acoustic evidence only (conf ≤0.3, primary carried forward); thread/ledger unchanged; **safety unchanged** | acknowledgment of presence, never "I didn't catch that" |
| D8 | **Idle turn** (S18: no VAD activity post-response) | deterministic open-door line from fixed list; **max 1 per idle period**; idle threshold = internal parameter [U4] | idle record only; no question generation | open-door, zero interrogation |
| D9 | **Repeated parse failures** (D1/D2 on ≥2 consecutive fused calls) | session enters `degraded_perception` for the next call: response-only prompt variant (no head requested); exits on one success | perception skipped while degraded; counter reset on success | no behavioral change beyond state staleness |

**Degradation invariants:** the user always gets *something* (prose or filler) unless the turn was interrupted by them; no degradation path may restart mid-streamed audio; every degradation is reason-coded in the update log; safety never degrades to `none` silently.

---

## Contract-level change list vs `STATE_MODEL_V1.1`

| # | Change | Status vs v1.1 |
|---|---|---|
| CH1 | Head gains `"v":1` + exact-taxonomy prompt hardening + deterministic normalizer table (C1/C1.1) | implements Task 1 finding #1 — mechanism was already implied by P7/D7 |
| CH2 | Persona self-reference rule (C2) | implements Task 1 finding #2 |
| CH3 | Thread actions redefined as advisory with degrade rules (C3) | implements Task 1 finding #3; no new fields |
| CH4 | Transport byte-shape frozen as validated (C4) | formalizes §4.9; no delimiter change |
| CH5 | Identity handoff fully specified (C5) | detail for v1.1 §4.5/O2; touchpoints still Phase 5 |
| CH6 | Updater specified as pure function, fixed order, frozen params, reason-coded log (C6) | formalizes v1.1 §3.3 + §4.x mechanisms; zero new dimensions |
| CH7 | Degradation table (C7): D4 filler promoted from "Phase 5 flagged" to contract default ⚠; D7 acoustic-only = skip-LLM + deterministic line; D9 degraded_perception mode added | **⚠ U1 + U6 need owner sign-off** — these were explicitly deferred in v1.1 |
| CH8 | Phase 4 metric requirement: schema-valid ≠ parse-valid (Task 1: 30/30 vs 29/30) | evaluation hook, no schema change |

**No new state dimensions. No prosody/pitch/pause work. No provider/model change. v1.1 boundaries intact.**

## Unresolved decisions (flagged — require owner ruling, not silently decided)

| # | Decision | My recommendation |
|---|---|---|
| **U1** | Adopt D4 deterministic filler for total LLM failure (v1.1 deferred this to Phase 5) | Adopt now as contract default — silence on failure is the worst UX; wording list owner-approved before implementation |
| **U2** | Retention/purge for orphaned memories (start-fresh leaves them) | 90-day purge, decided in Phase 4 with the memory eval design |
| **U3** | S17 safety gap: should ≥2 consecutive acoustic-only turns with high RMS raise an `elevated_distress` clarify-first flag (deterministic rule, no LLM)? | Defer to Phase 4 — needs thresholds + eval data; gap is documented |
| **U4** | Idle-turn threshold (D8) | 45 s, as a D7-class internal parameter |
| **U5** | `entities` as strings vs objects | Strings `"Name (role)"` — simpler, sufficient for v1 |
| **U6** | D9 `degraded_perception` mode (response-only prompt after 2 consecutive parse failures) | Adopt — it is P7 degradation within the validated transport, but it is new behavior so it needs your tick |
