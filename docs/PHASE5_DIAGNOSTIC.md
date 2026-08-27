# Phase 5 Diagnostic — How Aiva's Voice Loop Actually Works Today

**Date:** 2026-08-27 · **HEAD:** `9dcba05` · **Mode:** diagnostic only — no code changed, nothing fixed, no redesign proposed.
**Labels:** `[FACT]` read from source · `[ASSUMPTION]` not verified · `[GAP]` absent.

---

## A. Actual architecture (as implemented)

```text
Browser mic (echoCancellation/NS/AGC on)                    [FACT App.tsx:48]
 ↓ LiveKit track
rtc.AudioStream → AudioResampler → 16 kHz int16             [FACT main.py process_user_audio]
 ↓ every frame:
TEN VAD (adaptive threshold state machine)                  [FACT providers/vad.py]
   ├─ SPEECH_STARTED → pre-roll(300ms) prepended, buffer opens
   │    └─ if agent speaking: BARGE_IN_CANDIDATE (log only)
   └─ SPEECH_ENDED (adaptive silence threshold met) → segment frozen
 ↓ energy gate (whole-utterance RMS≥2000 or peak≥3500)
spawn transcribe_and_respond task (async; per-turn)
 ├─ Groq STT (session-language logic; temp 0; verbose_json)
 ├─ echo filter (difflib vs agent's last reply, romanized compare)
 ├─ validity filter (is_real_user_turn)
 │    ├─ invalid → acoustic_only / unclear_speech (deterministic lines) / drop
 │    └─ valid ↓
 ├─ BARGE-IN: if agent speaking & valid → prev_task.cancel()
 ├─ TURN CONTROLLER (deterministic respond-vs-wait)          [FACT turn_controller.py]
 │    └─ WAIT → user text into history, NO speech, return
 ↓
fused LLM call (TRANSPORT_V1.2): <perception> JSON head + spoken reply
 ↓ parse head (tags → JSON, raw_decode tolerance)
state updater (deterministic) → session state + policy (next turn)
 ↓ prose frames → TTS (Fish Audio WS stream; Edge fallback)
 ↓ frames → rtc.AudioSource(48k) → LiveKit → user's speaker
wait_for_playout → turn record → session JSONL
```

**Note:** "Perception" and "response generation" are ONE LLM call (fused, validated in Task 1). There is no separate perception service.

## B. Configuration table

| Component | Implementation | Exact parameters |
|---|---|---|
| **VAD core** | `ten_vad.TenVad` via `TenVADProvider` wrapper | hop 256 samples (16 ms), 16 kHz, threshold 0.5, min-speech 200 ms |
| **Endpointing** | Adaptive state machine in `TenVADProvider` | base silence 300 ms (`AIVA_SILENCE_MS`) · premature-resume window 3000 ms → +400 ms penalty/cycle (`AIVA_MAX_SILENCE_MS` cap 1100) · long-speech floor 700 ms after 5000 ms cumulative · genuine-gap reset 4000 ms · **no post-roll** · pre-roll 300 ms (in main.py, 4800 samples) |
| **SPEECH_ENDED trigger** | `silence_frames ≥ frames_for(effective_threshold)` while speaking | effective = min(base+penalty [floor 700 if stretch≥5s], 1100) |
| **STT** | Groq API, `whisper-large-v3` (owner-approved upgrade; revert `AIVA_STT_MODEL=whisper-large-v3-turbo`) | temperature 0 (`AIVA_STT_TEMPERATURE`), **no initial_prompt by default** (leak evidence), language: session-learned (auto-detect first qualifying utterance ≥1.2 s/≥3 words/non-catastrophic, then pin; wrong-pin re-opens after 2 catastrophic; `AIVA_STT_LANGUAGE` forces) |
| **STT audio** | mono 16 kHz int16 WAV in-memory | resampled in worker (48k→16k); no normalization |
| **Validation** | `is_real_user_turn` (main.py) | empty · punctuation_only (no [a-zA-Z0-9\u0900-\u097F]) · known_hallucination_pattern (exact list: "i am good", "i am good.", "thank you.", "thanks for watching.", "subscribe.") · high_no_speech_prob (>0.6) · catastrophic_low_confidence (<−0.85) · low_avg_logprob (<−1.0). **No** length/word-count/language-mismatch/semantic-completeness checks |
| **Unclear routing** | invalid-but-present text (poor confidence OR repetition-loop ≥4× same token / ≥50% dominant) → `unclear_speech` deterministic clarification ("haan? ek baar phir bol") — LLM skipped | `agent/main.py` + `fused_turn.py` |
| **Turn controller** | `turn_controller.decide()` — deterministic | WAIT: trailing/leading ellipsis · final-word connector (कि/तो/और/फिर/मतलब/क्योंकि/लेकिन + Roman) · ≤3-word fragment after previous WAIT · RESPOND: question(?), handoff imperative (bol/bata/sun…), anything else. Uncertainty → **respond** (`completed_or_unclear`) |
| **Backchannel/listen** | exact-token sets (`BACKCHANNEL_TOKENS`, `LISTEN_REQUEST_TOKENS`) | backchannel → 1–3 word deterministic line, LLM skipped, ≤2 consecutive then silent · listen_request → one short line then quiet |
| **Perception + LLM** | ONE fused Gemini call (`gemini-3.5-flash-lite`, temp 0.7, streaming) via `fused_turn.py` | input JSON: policy + memory view + thread summaries + last-6 history + user turn; output: `<perception>` JSON head (emotion/thread/safety/need/candidates/correction) + prose ≤2 sentences |
| **Policy** | deterministic `agent/state_updater.py` (pure function, frozen params) | precedence Safety > explicit request > mode rules; `emotion_reflection` carried; phase derived |
| **LLM failure** | retry once if zero prose (429 → 65 s cooldown) → D4 deterministic filler (U1-approved wording) | D4b: never restart mid-stream |
| **TTS** | Fish Audio `s2.1-pro-free` (livekit-plugins-fishaudio 1.6.10), streaming WebSocket, wav 44.1 kHz → resampled 48 kHz → `rtc.AudioSource(48000,1)`; fallback EdgeTTS `en-IN-NeerjaNeural` per-turn (5 s first-chunk timeout) | frames streamed as produced |
| **Barge-in** | VAD SPEECH_STARTED while `agent_speaking_event` set → candidate buffered; on valid transcript → `prev_task.cancel()` | decision latency = full STT round-trip (~0.5–1.5 s) — `[GAP]` immediate-cancel not implemented |

## C. Critical code locations

| Component | Location | Responsibility |
|---|---|---|
| VAD/endpointing | `providers/vad.py → TenVADProvider.process_audio/_effective_silence_ms` | frame loop, adaptive threshold, events, evidence (`last_endpoint`, `last_resume_gap_ms`) |
| Buffering | `agent/main.py → process_user_audio` | `pre_roll_buffer` (300 ms rolling), `speech_buffer`, task spawning |
| STT | `providers/stt.py → GroqSTT.transcribe` | API call, session-language state machine |
| Validation | `agent/main.py → is_real_user_turn` | accept/reject gates |
| Turn relation | `agent/main.py → classify_turn_relation` | backchannel/listen_request/content (exact tokens) |
| Repetition-loop | `agent/main.py → is_repetition_loop` | Whisper degeneration → unclear path |
| Turn decision | `agent/turn_controller.py → decide` + wiring in `main.py` | respond vs WAIT |
| Fused call/transport | `agent/fused_turn.py → FusedLLM.stream_prose` | streaming, head parse, D1/D2/D4/D4b/D7/D8/D9 paths |
| Updater/policy | `agent/state_updater.py → update/_derive_policy` | all state transitions, deterministic |
| Session store | `agent/session_state.py → SessionState` | JSONL, stale-turn guard, memory bridge |
| Barge-in/flush | `agent/main.py → flush_audio_source` + cancel block | clear_queue-or-silence, truncated-text preservation |
| Memory | `agent/memory_store.py → MemoryStore` | SQLite, explicit/pending commits, 90-day purge |

## D. Known failure modes (ranked by current evidence)

1. **Premature endpointing on continuous Hindi speech** — gold-eval: ≥8/32 premature (25 %+). Mitigation shipped (adaptive thresholds, recalibrated) but **not yet validated live**. Legacy runs also fragmented one monologue into 32 STT chunks — short-chunk STT garbling is downstream of this.
2. **STT garble on short segments passes validation** — `valid=true` requires no semantic completeness or length check; e.g. `"जाब उल्डर ही तब आई तू"`-class fragments can reach the LLM when logprob ≥ −0.85. Partially mitigated: repetition-loop + unclear_speech clarification; v3 model upgrade.
3. **Stale TTS after barge-in (SDK-dependent)** — `flush_audio_source` uses `clear_queue` only if the pinned SDK exposes it `[ASSUMPTION — verify]`; silence-fallback does not drain queued audio.
4. **Barge-in decision latency** — cancel waits for full STT; user hears overlap for the round-trip. (Design gap, unfixed.)
5. **Async turn ordering** — turn numbers assigned at task start; cancelled tasks can finish out of order. State is guarded (stale turns skipped); log ordering is cosmetic.
6. **Echo/self-hear residue** — layered filters work but depend on difflib vs last reply only (single-turn window).
7. **Logging gaps** — no monotonic wall-clock stamps for VAD events; perception stage not separately timed (inside fused call); `llm_context` persisted on all paths now.

**Missing telemetry (owner §11):** audio-received stamps, VAD-start monotonic, perception start/end, playback-fully-stopped, barge-in→audio-stop latency. Present: speech start/end (ISO), STT latency, LLM TTFT, TTS TTFA, speech-end→first-audio, playback vs audio duration, interrupted-at-ms, wait_duration_ms.

## E. Answers to the specific sub-questions (condensed)

**VAD reopen:** no literal reopen — but premature resume within 3 s is *remembered* (penalty raises the next threshold), and speech after an endpoint simply starts a new segment (old audio already sent to STT).
**Audio during STT/LLM/TTS:** VAD keeps processing; a new SPEECH_STARTED opens a fresh buffer (becomes barge-in candidate); during agent speech the buffer accumulates and can trigger barge-in.
**Post-roll:** none. **Pre-roll:** 300 ms.
**Partial sentence valid=true:** yes, possible today (no completeness check) — exactly the owner-suspected hole.
**Uncertainty default:** the turn controller's uncertainty → `respond`; confidence-relevant signals (STT confidence) reach the unclear path only at the hard thresholds.
**Barge-in:** cancel is deferred until STT validates (by design then; now a known latency cost). Cancelled response: truncated text stored with marker, flush called, no `wait_for_playout`.
**IDs/logging:** `turn_number` increments per spawned task (nonlocal); JSONL written synchronously per turn in `finally`; concurrent tasks can interleave → observed 18-before-17 ordering is real async completion order, not a logging bug.

---

**No fixes applied in this document.** Awaiting owner decisions on which failure mode to attack next (recommended order per §D: validate live-recalibrated endpointing → completeness/length check in validation → immediate barge-in cancel → SDK clear_queue verification).
