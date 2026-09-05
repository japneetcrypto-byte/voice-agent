# Test tiers — QUICK · TARGETED · FULL REPLAY · LIVE

**Status:** infrastructure only (2026-09-05). No product logic, no change to
numeric observation / rail / L1–L6 / E1 behaviour. Replay identity and the
58-suite baseline preserved (59 with the tier self-test).

One entry point: `python3 phase5/harness/test_tiers.py <tier> [selectors]`.
`RUN_ALL.sh` STEP 1b runs the FULL tier; STEP 1's individual suites and STEPS
2–5 (real API calls) are untouched.

| Tier | Command | What runs | What it proves | Expensive calls |
|---|---|---|---|---|
| **QUICK** | `test_tiers.py quick [--changed P ..]` | suites whose import closure / source pins touch the changed modules + the directly affected rail fixture (`session_103339_rail`) replayed end to end | the changed unit still satisfies its invariants; the rail carrier still replays to identity | none (0 network) |
| **TARGETED** | `test_tiers.py targeted [--changed P ..]` | QUICK's suites + `test_replay_identity.py` + **prefix replay** of every archive through its last affected turn + subset proof | the affected archived turns still reproduce the archived decisions, on the **same frozen bytes** FULL uses | none |
| **FULL REPLAY** | `test_tiers.py full` | **every** suite + `python3 phase5/harness/replay.py 'phase5/harness/fixtures/*/session_*.log'` verbatim (exit code shown) + accepted-divergence profile check + frozen-input verification | the standing acceptance / **merge gate** — unchanged, not weakened | none |
| **LIVE** | `test_tiers.py live [--session LOG]` | offline pre-checks (pyflakes over live-path modules, `main.py` source-pin suites) + the owner runbook; with `--session`, verifies + replays a captured log | the audio → STT → pipeline path on the deployed worker; **required only when a live-path module changed** | STT/VAD/LLM/TTS on the owner's machine — never here |

`test_tiers.py plan` prints the selection without running anything.
Selectors: `--changed <paths>` (explicit), `--since <ref>` (git diff), `--area <name>`
(force), default = working tree vs HEAD (+untracked), else last commit.

## Frozen upstream artifacts

Every replayed turn is driven by evidence a live session paid for. `main.py`
archives it per turn in `logs/session_*.log`; the replay gate recomputes every
decision from a curated subset of those keys. `phase5/harness/frozen_inputs.py`
makes that contract explicit:

* `FROZEN_KEYS = INPUT_KEYS ∪ ORACLE_KEYS` — the only archive keys any tier
  reads. Grouped by the live call they stand in for: `STT_EVIDENCE`
  (`stt_transcript/valid/rejection_reason/language/no_speech_prob/avg_logprob/
  compression_ratio/provider/latency_s`), `ENDPOINT_EVIDENCE` (`endpoint`,
  `premature_resume`, `agent_was_speaking`, `ms_since_agent_audio_end`,
  `acoustic`, speech timestamps), `LLM_ARTIFACT` (`llm_response_full`,
  `head_plan`, `llm_called`), `ECHO_EVIDENCE` (`echo_shadow`, `echo_corr_score`,
  `echo_dropped`, `echo_overridden` — recorded for Phase 2, not consumed yet),
  `DELIVERY_EVIDENCE` (`response_state`, `interrupted`, `response_suppressed`,
  `tts`, `tts_text`), `CARRIER_STATE` (`precise_detail`, `detail_state`, latch,
  `engine_path`, `turn_type`), `ARCHIVED_DECISIONS` (oracle only).
* `numeric_observation` is **oracle-only**: replay recomputes it from the frozen
  transcript (`observe()` is pure) and compares; the archived record is never
  an input. `numeric_audit` (derived view) is neither.
* `frozen_manifest.json` per fixture: per-turn `inputs_sha256` / `oracle_sha256`,
  whole-file projection digest, classes present, provider, observation
  version/certainty. `test_tiers.py freeze` verifies; `freeze --update`
  re-records (deliberate act, reviewable in the diff); `freeze --import LOG
  --name FIXTURE` copies a raw captured log into a new fixture (**never
  overwrites** an existing archive) and freezes it.
* Immutability: an in-place edit of an archived `NumericObservation` changes
  `oracle_sha256` → verify fails, and the pure `observe()` replay diverges →
  gate fails. A future second look is a new record/version, never a mutation.
* `project_session_log.py` (the paste-through-chat projection) now uses the
  same `FROZEN_KEYS`: `replay(project(log)) == replay(log)` on every fixture
  (the old GATE_KEYS list pre-dated `precise_detail`/`numeric_observation`).

**Known gap (not hidden):** Whisper per-segment lists are not retained by
`providers/stt.py` (only the aggregated turn-level metrics reach `Transcript`),
so segments cannot be frozen today. Adding a `Transcript.segments` field + an
archive key is a live-STT-path change → LIVE tier; not done here.

## Why QUICK / TARGETED are subsets of the same frozen inputs

* Suite selection is a function of the code graph only (AST import closure ∪
  literal source pins); QUICK ⊆ FULL's suite set; monotone in the change set.
* Turn selection is a predicate over the **frozen projection** of each archived
  turn (`tiers_manifest.TURN_PREDICATES`); the test proves selection on the
  projection equals selection on the raw archive.
* TARGETED replays the **prefix** of each file through the last selected turn
  with the standing `replay_session` (new optional `stop_after`; default path
  byte-identical) — the carrier is threaded turn to turn, so a mid-file start
  would not be the same computation. The self-test proves the prefix diffs
  equal FULL's diffs restricted to those turns, on all three fixtures.
* Subset proof at run time: every selected turn's `inputs_sha256` equals the
  manifest record FULL is verified against.
* Fail-closed: a change to `response_pipeline.py`, the harness, or an unmapped
  `agent/`/`providers/` module escalates TARGETED to every archived turn.
  Reverse dependencies propagate (e.g. `value_transaction.py` → rail turns,
  `reply_guard.py` → fused/control-plane paths).

## FULL REPLAY remains the gate

* Runs the standing command verbatim and shows its output + exit code.
* `phase5/harness/fixtures/accepted_divergence.json` pins the documented
  standing profile (baseline t1 greeting-rail / t20 live cap-trim hard diffs,
  t11 stream-chunk note). FULL fails on **any** difference — a new divergence
  **or** a vanished one (re-pin only deliberately with `freeze
  --pin-divergence`, and say why in the commit).
* The fixture-writing suites (`test_session_103339_trace.py`,
  `test_replay_identity.py`) run first; frozen verification afterwards proves
  they regenerated byte-identical archives.

## Measured (sandbox, 2 cores)

| | suites | replay | wall |
|---|---|---|---|
| QUICK `--changed agent/numeric_observation.py` | 11 | 21 rail turns | 35 s (29 s = the adversarial suite) |
| TARGETED same change | 11 + identity | 38 / 60 turns (9 selected, prefix) | 32 s |
| TARGETED `--changed agent/reply_guard.py` | 20 | 60 / 60 (59 selected) | 33 s |
| FULL | 59 | 60 / 60 + frozen verify + profile | 33 s |

Every tier: 60 STT, 23 VAD/endpoint, 36 LLM, 50 TTS/playback, 12 echo-corr
live calls stood in for by frozen evidence at FULL; 0 network calls made.
