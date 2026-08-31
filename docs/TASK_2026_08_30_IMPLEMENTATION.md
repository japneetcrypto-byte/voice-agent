# Task 2026-08-30 — Implementation & Test Packet

**Branch:** `arena/01a05304-voice-agent` (pushed: `ed01a4b` latest)
**Scope:** Response Contract + Voice Quality Validation (owner-locked task).
No architecture redesign. No LLM regeneration loop.
**Purpose of this file:** give you the exact code, the approach, and the
test surface so you can tightly test/audit everything.

---

## 1. Approach (the principles I followed)

1. **Measure before changing behavior.** The STT fix started from your own
   log evidence (`high_no_speech_prob` rejects of clearly-spoken turns).
2. **Deterministic code decides; LLM interprets** (project principle).
   Every new decision is pure-function, unit-tested, no randomness
   (`pick_line`-style rotation by turn number).
3. **Nothing ambiguous goes on the critical path.** Only objectively
   unambiguous violations hard-block (topic-independent — see §6).
4. **Per-piece gate stays pre-TTS; full-text check is post-play
   measurement** (CA2 sign-off) so first-audio latency is untouched.
5. **Provider incidents are confounders in the A/B**, not behavior.
6. **Removed my own overfit** when you caught it: the topic lexicon is gone
   (§6) — the gate is identical for every user/topic.

---

## 2. Commits (this task, oldest → newest)

| Commit | What |
|---|---|
| `086fa1b` | STT segment aggregation + suspicious band + low-logprob dead-branch fix + semantic Fish acks + TTS pre-warm + barge-in reorder + contract wiring + detail-mode plan handling + A/B report v1 |
| `fc361b8` | Fall-through fix (invalid transcript + agent speaking → silent drop, never LLM) + deterministic near-repeat guard + report robustness |
| `353d543` | Gate dev-context downgrade (REMOVED AGAIN in ed01a4b) + rotating block filler + partial-ready Fish acks + report auto-split |
| `ed01a4b` | **Topic lexicon removed** — gate is topic-independent; ambiguous refs always flag; hard-block only objectively dangerous |

---

## 3. New files (full code)

### 3.1 `providers/segment_metrics.py` — STT root-cause fix
```python
"""Segment-metric aggregation for Whisper transcripts — PURE module.

Root-cause fix (task 2026-08-30): reading only segments[0] meant a quiet
lead-in / pre-roll noise segment with no_speech_prob≈0.9 could reject a
clearly-spoken turn as `high_no_speech_prob`. Whisper emits per-segment
confidence; the TURN-level signal must aggregate:

  no_speech_prob   — MIN across segments: if ANY segment is clearly
                     speech, the utterance contained speech.
  avg_logprob      — duration-weighted mean: a short garbled segment must
                     not dominate a longer clean one.
  compression_ratio— duration-weighted mean.

Handles Groq verbose_json segments (dicts) and faster-whisper objects.
No heavy dependencies (numpy not required) so the acceptance gates are
unit-testable in any environment.
"""
from __future__ import annotations


def _seg_field(seg, name: str, default=None):
    if isinstance(seg, dict):
        return seg.get(name, default)
    return getattr(seg, name, default)


def aggregate_segments(segments) -> tuple[float | None, float | None, float | None]:
    """Return (min_no_speech_prob, weighted_avg_logprob, weighted_compression)."""
    if not segments:
        return None, None, None
    min_nsp: float | None = None
    w_lp, w_cr, total_w = 0.0, 0.0, 0.0
    for seg in segments:
        start = _seg_field(seg, "start", 0.0) or 0.0
        end = _seg_field(seg, "end", 0.0) or 0.0
        nsp = _seg_field(seg, "no_speech_prob", None)
        lp = _seg_field(seg, "avg_logprob", None)
        cr = _seg_field(seg, "compression_ratio", None)
        w = max(float(end) - float(start), 0.0)
        if nsp is not None:
            min_nsp = float(nsp) if min_nsp is None else min(min_nsp, float(nsp))
        if lp is not None:
            w_lp += float(lp) * w
            w_cr += (float(cr) if cr is not None else 0.0) * w
            total_w += w
    avg_lp = (w_lp / total_w) if total_w > 0 else None
    avg_cr = (w_cr / total_w) if total_w > 0 else None
    return min_nsp, avg_lp, avg_cr
```
Used in `providers/stt.py` (`GroqSTT.transcribe` and the auto-mode language
learning) — replaced the `segments[0]`-only read.

### 3.2 `agent/stt_validation.py` — acceptance gate + classification (pure)
```python
"""STT turn validation + turn classification — PURE module (no livekit deps).

Extracted from agent/main.py (2026-08-30) so the acceptance rules are
unit-testable in isolation. Behavior-preserving extraction EXCEPT the
task-signed changes:

  1. Suspicious no-speech band (sign-off CA6): Whisper ~50/50 on whether
     this is speech -> never reaches a substantive LLM answer. The
     transcript is REJECTED with reason `suspicious_no_speech_band`;
     route_transcript then bounds it (>=4 words -> contextual_recovery
     with checkpoint policy; otherwise -> clarify).
  2. Multi-token filler ("hm hm acha", "haan theek") now classifies as
     `backchannel` so the deterministic backchannel policy answers with a
     1-3 word line instead of a substantive LLM call on junk.
"""
from __future__ import annotations

import re
from collections import Counter

BACKCHANNEL_TOKENS = {"haan", "han", "hmm", "hm", "hmmm", "okay", "ok", "accha", "achha",
                      "acha", "phir", "bol", "yeah", "yes", "theek", "thik", "hai", "hain",
                      "हाँ", "हम्म", "अच्छा", "ठीक"}
LISTEN_REQUEST_TOKENS = {"chup", "chupchup", "suno", "suno_bas", "bassuno", "pehlemeribaatsun",
                         "beechmeinmatbolo", "chupraho", "meribaatsun", "pehlesunomera"}

_HALLUCINATIONS = {"i am good.", "i am good", "thank you.", "thanks for watching.", "subscribe."}

_WORD_RE = re.compile(r"[\w\u0900-\u097F]+", re.UNICODE)
_NORM_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+", re.UNICODE)


def normalize_for_classify(text: str) -> str:
    norm = _NORM_RE.sub("", (text or "").lower()).strip()
    return _WS_RE.sub("", norm)


def classify_turn_relation(transcript_text: str) -> str:
    """listen_request / backchannel / content / empty.
    Extension: multi-token all-filler ("hm hm acha") -> backchannel."""
    norm = normalize_for_classify(transcript_text)
    if not norm:
        return "empty"
    if any(tok in norm for tok in LISTEN_REQUEST_TOKENS):
        return "listen_request"
    if norm in BACKCHANNEL_TOKENS or norm in {"bas", "hmmhaan", "haanhmm"}:
        return "backchannel"
    words = _WORD_RE.findall(transcript_text or "")
    if words and all(w.lower() in BACKCHANNEL_TOKENS for w in words):
        return "backchannel"
    return "content"


def is_repetition_loop(transcript_text: str) -> bool:
    """Whisper degeneration detector: same token >=4x or dominating."""
    words = _WORD_RE.findall(transcript_text or "")
    if len(words) >= 4:
        run, prev = 1, None
        for w in words:
            lw = w.lower()
            run = run + 1 if lw == prev else 1
            prev = lw
            if run >= 4 and len(prev) >= 2:
                return True
        top, n = Counter(w.lower() for w in words).most_common(1)[0]
        if n >= 3 and len(top) >= 2 and n / len(words) >= 0.5:
            return True
    return False


def validate_transcript(transcript, speech_duration_ms: float | None = None,
                        no_speech_threshold: float = 0.6,
                        suspicious_nsp_min: float = 0.5,
                        avg_logprob_threshold: float = -1.0,
                        catastrophic_logprob: float = -1.2) -> tuple[bool, str]:
    """Acceptance gate. Reasons:
      empty_transcript / known_hallucination_pattern / punctuation_only /
      high_no_speech_prob / suspicious_no_speech_band / low_avg_logprob /
      catastrophic_low_confidence / accepted

    CA6: suspicious band is a REJECTION — bounded recovery, never a
    substantive LLM answer on an uncertain transcript."""
    text = (getattr(transcript, "text", "") or "").strip()
    if not text:
        return False, "empty_transcript"
    lower_text = text.lower()
    if any(lower_text == h for h in _HALLUCINATIONS):
        return False, "known_hallucination_pattern"
    if not re.search(r"[a-zA-Z0-9\u0900-\u097F]", text):
        return False, "punctuation_only"

    nsp = getattr(transcript, "no_speech_prob", None)
    if nsp is not None and nsp > no_speech_threshold:
        return False, "high_no_speech_prob"
    if nsp is not None and nsp >= suspicious_nsp_min:
        return False, "suspicious_no_speech_band"

    lp = getattr(transcript, "avg_logprob", None)
    # Order matters: catastrophic is the LOWEST bound (< -1.2); the old
    # -0.85 floor made low_avg_logprob UNREACHABLE (dead branch).
    if lp is not None and lp < catastrophic_logprob:
        return False, "catastrophic_low_confidence"
    if lp is not None and lp < avg_logprob_threshold:
        return False, "low_avg_logprob"

    return True, "accepted"
```
`agent/main.py` imports `validate_transcript as is_real_user_turn` +
`classify_turn_relation` + `is_repetition_loop` from here (old local
definitions removed). Thresholds come from `agent/config.py`
(`Config.NO_SPEECH_THRESHOLD`, `Config.SUSPICIOUS_NSP_MIN`,
`Config.AVG_LOGPROB_THRESHOLD`, `Config.CATASTROPHIC_LOGPROB`).

### 3.3 `agent/tts_warmup.py` — pre-warm policy (pure)
```python
"""TTS warm-up policy — PURE module (task 2026-08-30, CA3-approved add-on).

Why: Fish Audio's server warms the cloned-voice model per request. A tiny
background warmup request shortly AFTER a completed reply keeps the model
hot, so the NEXT turn's time-to-first-audio drops (Solana audit #4:
-200..-500ms on next-reply start). This is the non-provider lever for the
"next reply wait" — the stop path is fixed separately by barge-in reorder.

Conservative by design:
  - warm only after an idle gap (never during rapid exchange)
  - bounded calls per session (Fish free-tier quota!)
  - a warmup is only "fresh" for a limited window
Deterministic, no I/O. Consumed by providers/tts.py.
"""
from __future__ import annotations


class WarmupPolicy:
    def __init__(self, idle_gap_s: float = 15.0, max_per_session: int = 4,
                 warm_fresh_s: float = 60.0):
        self.idle_gap_s = idle_gap_s
        self.max_per_session = max_per_session
        self.warm_fresh_s = warm_fresh_s
        self._last_synth_end: float | None = None
        self._last_warm_at: float | None = None
        self._warm_calls = 0

    def note_synthesis_end(self, now: float) -> None:
        self._last_synth_end = now

    def should_warm(self, now: float) -> bool:
        if self._warm_calls >= self.max_per_session:
            return False
        if self._last_synth_end is None:
            return False
        if now - self._last_synth_end < self.idle_gap_s:
            return False
        if self.is_warm(now):
            return False
        return True

    def on_warmup_done(self, now: float) -> None:
        self._last_warm_at = now
        self._warm_calls += 1

    def is_warm(self, now: float) -> bool:
        return (self._last_warm_at is not None
                and now - self._last_warm_at <= self.warm_fresh_s)
```
Wired in `providers/tts.py`: `FishAudioTTSProvider.warmup()` synthesizes
`"haan"` (discarded), `FallbackTTSProvider.warmup()` delegates;
`main.py` fires `asyncio.create_task(tts_provider.warmup())` after each
completed reply; `turn["tts"]["warm"]` = was the model warm at TTS start.

### 3.4 Report scripts
- **`phase5/contract_ab_report.py`** — 9+ metrics (audio duration,
  interruption rate, pre-audio cancel rate, barge vad→stop & cancel→stop,
  STT rejection rate, repeat rate, topic jumps, contract violations/blocks,
  detail/plan turns, chunk mid-sentence, speech→audio, LLM TTFT, TTS TTFA,
  warm turns, ack played, provider incidents/429s). **AUTO mode** splits all
  `logs/session_*.log` into PRE/POST by the `WORKER_BUILD` commit in the
  paired `logs/events_*.log`:
  ```bash
  uv run python phase5/contract_ab_report.py            # auto
  uv run python phase5/contract_ab_report.py --pre a.log --post b.log   # manual
  ```
- **`phase5/stt_rejection_report.py`** — rejection by reason, cross-tab
  vs acoustic features (rms/peak/dur) and agent-speaking context, suspicious
  band listing, recovery quality (short+checkpoint), substantive-reply watch.

---

## 4. Modified sites (file → what changed)

### `agent/response_contract.py` (final — topic-independent gate)
- `derive_constraints`: base MUST_NOTs now include **fabricate actions** and
  **stay on topic**; priority-ordered so the 5-cap never drops
  no-contradict/no-repeat/dangerous rules.
- `build_contract`: adds `CONTEXT` line (`your previous claim: ...` /
  `you just said: ...`) when available; compact ≤6 keys.
- Gate (`_GATE_PATTERNS`, `check_violations`, `gate_reply`):
  - **BLOCK always (topic-independent):** `I am an AI / language model /
    bot`; internal codenames (`perception`, `policy_constraints`,
    `response_contract`, `memory_gate`); action fabrication (`I have
    already sent the email`).
  - **FLAG always (spoken + measured):** `my system prompt/code/
    instructions`; memory_proactive (`remember when...`).
  - `gate_reply(reply, turn_no=None)` — blocked piece replaced by a rotating
    line from `GATE_BLOCK_LINES` (`haan, bolo na.` / `achha, samajh gaya —
    aage bolo.` / `haan, main yahin hoon.`).
  - **No topic lexicon anywhere.** (Removed in `ed01a4b`; test proves
    identical behavior across 4 unrelated topics.)

### `agent/reply_guard.py`
- `REPEAT_BREAK_LINES` + `repeat_break_for(piece, last_reply, user_text,
  turn_no)`:
  - verbatim / extension / near-identical repeat of the previous reply →
    substitute a short varied line + mark `trim["done"]` (stop the repeat).
  - **Never** guards when the user explicitly asks to repeat what Aiva said
    (`kya bola`, `dobara bolo`, `say that again` — `_REPEAT_REQUEST_RE`).
- `PLAN_CHUNK_CAP = 320` — plan-driven detail turns get a generous ceiling
  (code trim = fallback only).

### `agent/ack_bridge.py` (v2)
- `ACK_POOL` per semantics: question/venting/positive/neutral; selection via
  `pick_ack_for(text, turn_relation, turn_no)` — deterministic regex, no LLM.
  No ack for listen_request/backchannel/empty.
- Acks synthesized with **Fish** (cloned voice), disk-cached under
  `logs/acks_cache/` keyed by voice-id hash.
- **Partial ready**: any cached/synth clip → acks play; per-word retry with
  trailing `.`; `pick_for` falls back to sibling words in the same category
  (`sibling_fallback:cat`). Fish unavailable → acks disabled (silence),
  never a wrong-voice ack.
- `ACK_PLAYED` event with `word` + `reason`; `turn["ack_word"]`,
  `turn["ack_reason"]`.

### `providers/stt.py`
- Lazy imports (`groq`, `scipy`) so the module imports without heavy deps.
- Segment metrics aggregated via `providers.segment_metrics` (both
  `transcribe` and auto-mode language learning).

### `agent/config.py`
- `SUSPICIOUS_NSP_MIN = env AIVA_STT_SUSPICIOUS_BAND_MIN, default 0.5`
- `CATASTROPHIC_LOGPROB = env AIVA_STT_CATASTROPHIC_LOGPROB, default -1.2`
  (strictly below `AVG_LOGPROB_THRESHOLD -1.0` so the low branch is reachable)

### `agent/main.py` — the wiring (all task changes)
| Site | Change |
|---|---|
| import | `from agent.stt_validation import ...`, `repeat_break_for`, `PLAN_CHUNK_CAP`, `check_violations` |
| `_contract = build_contract(...)` | passes `last_claim` (first sentence of last reply) + `last_reply` — these were DEAD before |
| gate calls | `gate_reply(piece, turn_no=turn_number)` ×2 (pre-TTS per piece); `contract_block_count` per turn |
| repeat guard | in the streaming tee, first sentence vs `recent_reply_texts[-1]` → `REPEAT_GUARDED` event + substitute |
| post-play sweep | `check_violations("".join(spoken_text))` → `CONTRACT_VIOLATION_SWEEP` events (measurement only) |
| detail mode | plan turns get `PLAN_CHUNK_CAP` ceiling; `chunk_current/chunk_total`; `DETAIL_PLAN_DONE` when current≥total shrinks renewal to 1 |
| ack | `ack_bridge.pick_for(transcript.text, turn_relation, turn_number)` → `ACK_PLAYED`/`ack_word`/`ack_reason` |
| barge-in | cancel issued at `SPEECH_STARTED` when agent speaking (`BARGE_CANCEL_ISSUED`); `turn["barge_ms"] = {vad_to_stop_ms, cancel_to_stop_ms}`; false-barge counters |
| **fall-through fix** | invalid + agent-speaking → `dropped_reason=invalid_{action}_while_agent_speaking`, silent return — NEVER LLM (was the t14 bug) |
| pre-warm | `asyncio.create_task(tts_provider.warmup())` after reply; `turn["tts"]["warm"]` |

### `providers/tts.py`
- `FishAudioTTSProvider.warmup()` + `WarmupPolicy`; `FallbackTTSProvider.warmup()`.
- Edge fallback RETAINED (owner sign-off #3); **if Edge also fails** →
  `last_provider="none"`, `TTS_FAILED`-style silent turn with log.

### `.env.example` — new vars
```
AIVA_STT_SUSPICIOUS_BAND_MIN=0.5
AIVA_STT_CATASTROPHIC_LOGPROB=-1.2
AIVA_TTS_PREWARM=1        # 0 disables pre-warm (saves Fish quota)
```

---

## 5. Test surface — run everything

```bash
# from repo root (uv env recommended; pure suites run with any python3):
uv run python phase5/tests/test_stt_validation.py        # 22 cases
uv run python phase5/tests/test_ack_selection.py         # 16 cases
uv run python phase5/tests/test_tts_warmup.py            # 11 cases
uv run python phase5/tests/test_repeat_guard.py          # 11 cases
uv run python phase5/tests/test_contract_gate_topic_independent.py  # ~30 cases
uv run python phase5/tests/test_contract_wiring.py       # 12 cases
# + all pre-existing suites (18 more) — RUN_ALL.sh runs the full set
```
**All 20 suites green** in this sandbox (`ALL PASS` each). The 4 new suites
pin exactly the behaviors above (thresholds, band edge 0.50, weighted
logprob math, filler classification, ack semantics incl. "venting never
'theek hai'", sibling fallback, repeat guard incl. legit-repeat exemption,
gate identical across voice-agent/lawyer/cooking/cricket topics).

---

## 6. The gate — why it's now topic-independent (your review point)

Blocking "my system prompt/code" at runtime is **ambiguous**: it depends on
whose prompt and what context. My first attempt (a voice-agent lexicon)
fixed only YOUR topic and would have broken everyone else's. The locked
task's own words: *"hard-block only objectively dangerous violations."*
So:
- **Block**: identity deception, internal codenames, fabricated actions —
  objectively dangerous on every topic.
- **Flag (spoken, measured)**: ambiguous self-reference, memory_proactive.
- If the A/B shows frequent `self_reference_ambiguous` flags, the fix is a
  **persona-level instruction**, not another runtime regex.

---

## 7. Live test checklist (what to verify in a session)

1. `git pull origin arena/01a05304-voice-agent`; worker log shows the new
   commit hash (`WORKER_BUILD`).
2. **STT:** say a clearly-spoken 10+ word sentence, then check
   `stt_no_speech_prob` — with a quiet lead-in, the OLD code would show
   ~0.9 and reject; new code shows the min-across-segments value and
   accepts. Watch `[STT]` line.
3. **Filler:** say "hm hm acha" alone → expect a short backchannel line,
   not an LLM answer (log: `spoke_because=backchannel`).
4. **Suspicious band:** (hard to hit live; rely on the unit tests) nsp in
   [0.5, 0.6) → `suspicious_no_speech_band`, bounded recovery at most.
5. **Barge-in:** interrupt mid-reply → console shows `BARGE_CANCEL_ISSUED`
   early; session summary shows `barge vad->stop` ~100–300ms and
   `cancel->stop` separate; `false_barge_ratio` in the lifecycle summary.
6. **Ack voice/semantics:** ask a question → ack should be a question-cue
   word in the CLONE voice (`ACK_PLAYED word=... reason=question`); vent →
   never "theek hai". Delete `logs/acks_cache/` once to force re-synth and
   confirm Fish voice.
7. **Repeat:** reply, then immediately say something that makes Aiva repeat
   the same line → expect a varied short line + `REPEAT_GUARDED` event
   (and no guard when you say "kya bola tha?").
8. **Gate:** say "are you an AI?" → identity block (rotating filler, not
   constant "main sun raha hoon, bol."). Talk about YOUR voice agent's
   prompt/code → the reply is SPOKEN (flag), not replaced.
9. **Reports (after session):**
   ```bash
   uv run python phase5/contract_ab_report.py
   uv run python phase5/stt_rejection_report.py
   uv run python phase5/stage_diagnostic.py
   ```
   Pre/post split is automatic by commit.

---

## 8. Known limits / deliberate non-changes (so you test the right things)

- **TTFA unchanged** during rapid-fire talk (provider ceiling; pre-warm only
  helps after idle gaps — by design).
- **VENT-mode generic replies** ("kya chal raha hai?") are NOT reworked —
  the repeat guard kills verbatim duplication, but mode-policy is a separate
  decision (state engine, out of locked scope).
- **Edge fallback retained** for replies (your sign-off); ack clips are
  Fish-only; if Edge ALSO fails → silence-with-log (your nuance).
- **No prompt/persona changes** were made in this task (persona V1.x
  untouched) — all behavior is deterministic code.
- Thresholds are env-tunable; the A/B data may justify moving
  `AIVA_STT_SUSPICIOUS_BAND_MIN` or `CATASTROPHIC_LOGPROB` — deliberate,
  measured, reversible.

## 9. Review checklist — challenge these

1. Weighted logprob vs simple mean: is duration-weighting right for very
   short utterances (segments ≤ 0.3s dominate weight 0)?
2. Suspicious band [0.5, 0.6): should the LOW end be 0.4 or 0.55 for YOUR
   mic/setup? (Report §3 shows live band turns.)
3. Repeat guard: threshold 0.85 + extension containment — could a legit
   long answer get mis-guarded if the user says "what did you say?" in
   Hinglish not in the exemption regex? (Add forms if so.)
4. Barge immediate-cancel: false-barge ratio from the lifecycle summary —
   tune at what point?
5. Ack category regexes — do question/venting/positive cues over/under-fire
   on your actual speech? (ACK_PLAYED reasons in logs tell you.)
6. `PLAN_CHUNK_CAP=320` — is a 320-char chunk too long for a voice reply?
   (Pre-existing `REPLY_MAX_CHARS=240` was the cap before plans.)
