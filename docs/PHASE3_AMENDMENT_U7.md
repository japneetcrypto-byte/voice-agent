# Amendment A-U7 — `correction` field semantics (LOCKED per owner conditional approval)

**Date:** 2026-08-26 · **Amends:** C1 perception-head schema (optional field only) · **Boundary unchanged:** the LLM interprets; the deterministic updater consumes structured signals and applies predefined rules — **it never reads or infers natural language.**

## Answers to the owner's three clarifications

**1. `correction.about` = the dimension being corrected ONLY.** It never carries the corrected value.
```json
"correction": {"present": true, "about": "emotion|thread|fact|preference"}
```
The corrected *value* is simply the head's own interpreted output (e.g. `emotion.primary`) — because the LLM has already read the user's natural language ("nahi yaar, gussa nahi hai, bas udaas hu") and produced the interpretation. Nothing natural-language reaches the updater: it sees only `correction.present/about` + the already-structured head.

**2. The corrected value is LLM-produced → full existing validation applies.** Normalization → validation → degradation run on the head exactly as on any other turn. The correction changes **confidence and precedence only**, never the validation pipeline:
- Order: C1.1 normalize (near-miss table, −0.10) → enum validation → **then** CORR-OVERRIDE applies `confidence := 0.95` on the canonical label + precedence (overrides trajectory/decay contribution this turn) + `CORR-OVERRIDE` log.
- Near-miss (table match): canonical label kept, penalty applied, then 0.95 on the canonical value.
- **No-match (`NORM-UNKNOWN`):** correction does NOT rescue an invalid label → `neutral_unclear`, confidence cap 0.3, log `CORR-UNKNOWN-KEPT` (D3 spirit: a correction cannot validate an invalid LLM output).
- Invalid `correction` field itself (non-bool `present` / unknown `about`): treated as absent + log `CORR-INVALID` (fail-quiet, deterministic).
- Flat 0.95 regardless of head confidence (both up and down) — deterministic, documented.

**3. Exact C1 schema addition (optional field; absent = no correction):**
```json
"correction": {"present": false, "about": "emotion"}
```
- `present`: bool (required if field present). `about`: enum `emotion|thread|fact|preference` (required if present=true).
- Prompt fragment addition (TRANSPORT_V1 append): *"If the user is explicitly correcting something you previously said or interpreted, set correction to {\"present\": true, \"about\": \"emotion|thread|fact|preference\"}. Otherwise omit correction entirely."*
- Parser: absent field ≡ `{present:false}`; malformed → absent + `CORR-INVALID`.
- Effects by `about`: `emotion` → CORR-OVERRIDE (above); `thread` → advisory merge into active thread + `CORR-NOTE:thread` (C3 rules unchanged); `fact`/`preference` → head's memory_candidates (if any) tagged `criterion="corrective"` + `CORR-NOTE:<about>` (session-end commit per D3); no candidates → log only.

**Versioning:** head marker stays `"v": 1` (optional field, backward compatible); this amendment is `A-U7`, referenced from C1. Transport prompt fragment becomes `TRANSPORT_V1.1`.

**Replay fixtures:** U07 rewritten to the final semantics; U07b (no-match rescue refusal) and U07c (non-emotion correction) added — all deterministically replayable with no NL in the updater's inputs.
