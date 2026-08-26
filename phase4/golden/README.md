# Golden Suite — `aiva.golden/v1`

**Status:** v1 authored 2026-08-26 (T4.4, owner-approved start). 19 fixtures covering all 18 locked scenarios (S16 split into G16 explicit / G17 figurative).
**Source of truth for scenarios:** `docs/STATE_MODEL_V1.md` §2 · **policy/updater semantics:** `docs/PHASE3_CONTRACTS.md` (locked) · **gates:** `docs/PHASE4_EVALUATION_DESIGN.md` §1 (G-POL, G-DET, G-SAFE).

## Files

- `suite_v1.json` — the fixtures (single file, self-contained; provenance: synthetic authored corpus per D-4a phase 1)
- `README.md` — this file

Real/consented voice data (D-4a phase 2) will live under `datasets/` (gitignored) and will be **tagged `source: real-consented`** — never mixed into this suite.

## Fixture schema

| Key | Meaning |
|---|---|
| `scenario` | locked scenario id (v1.1 §2) |
| `provisional` | `"safety_taxonomy_pending"` on G16/G17 — expectations run against the v1.1 scaffold and are refined when T4.1 lands; **G-SAFE (FN=0 / figurative-FP=0) applies regardless** |
| `turn_type` | `speech` / `acoustic_only` / `idle` |
| `context` | policy (what the updater would have derived from prior state) + memory view + threads + history (+ optional `interrupted_agent_response`) |
| `expected.perception` | **tolerance bands** on the LLM head — membership/range assertions, never exact strings (the LLM owns interpretation) |
| `expected.policy` | **sparse exact assertions** on the deterministic updater's derived policy (the updater is deterministic; sparse keys = "these keys must match") |
| `expected.updater_log_contains` | reason-code assertions (e.g. `CORR-OVERRIDE`) |
| `expected.degradation` | C7 path assertion (`D7` skip-LLM presence line, `D8` open-door line) |
| `rubric` | human-judged items (G-POL), 0/1/2, scenario passes at ≥80% weighted |
| `safety_critical` | true on G16/G17 — 100% required, no partial credit |

## Assertion layers (what runs against what)

1. **Perception layer (LLM):** head must parse + validate (`aiva.perception/v1`), values within `expected.perception` bands. Reported **split: parse-valid vs schema-valid** (CH8).
2. **Updater layer (deterministic):** given the head + turn record, derived policy must match `expected.policy`; update log must contain `expected.updater_log_contains`. This is a **pure-function test — no LLM, no network** (G-DET).
3. **Reply layer (human rubric):** the generated response scored against `rubric` items (G-POL).

## Batch 2 — TODO (updater replay sequences, not yet authored)

Pure-function fixtures that replay synthetic state sequences through the updater spec (no LLM):
- trajectory derivation: rising (G04), falling (G05), fluctuating sequences
- decay after 3 uncorroborated turns · hysteresis blocking a mode flip · thread close after 10 inactive turns
- corrections: CORR-OVERRIDE precedence over head estimate
- safety: SAFE-OVERRIDE entry + SAFE-HYSTERESIS de-escalation after 3 safe turns
- degradation D1–D6, D9: parse-fail → no state churn; invalid safety enum → `low`+`other_flagged`; D4 filler trigger (zero-prose rule); partial-stream stop rule; degraded_perception enter/exit (U6)
- determinism property test harness (G-DET): replay k times, byte-identical outputs

## Change discipline

Fixtures are **pre-registered expectations**. Changing a fixture after evaluation runs requires an owner decision with evidence attached (same discipline as gate thresholds). Version bump → `suite_v2.json`; v1 is immutable once evaluation starts.
