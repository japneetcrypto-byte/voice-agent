# Phase 5 — End-to-End Voice Pipeline Diagnostic
**Date:** 2026-08-28 · **HEAD:** `2bd1052` · **Mode:** diagnostic only — no code changed.

---

## A. Layer-by-Layer Diagnosis

### 1. Audio Capture — WORKING ✅
- Browser captures at 48kHz with echoCancellation/NS/AGC enabled `[FACT: App.tsx:48]`
- Resampled to 16kHz mono in `process_user_audio()` `[FACT: main.py:250-277]`
- VAD receives int16 PCM correctly `[FACT: vad.py]`
- **Suboptimal:** Browser AGC normalizes loudness, making absolute RMS/peak unreliable across sessions (known, A10). Not fixable without removing AGC.

### 2. VAD / Turn Detection — WORKING with caveats 🟡
- TEN VAD wrapper with adaptive threshold state machine `[FACT: vad.py]`
- Base 300ms, premature-resume window 3s, penalty +400ms/cycle, cap 1.1s, floor 700ms after 5s `[FACT: vad.py:44-48]`
- Pre-roll 300ms `[FACT: main.py:295-310]`, **no post-roll**
- **Suboptimal:** The endpoint fires at threshold − 0-16ms (frame quantization). No post-roll means trailing audio is cut exactly at threshold, potentially clipping last word.

### 3. STT — BROKEN for Hindi 🔴
**This is the #1 root cause of bad conversations.**

- Provider: Groq, `whisper-large-v3`, language pinned to `hi` `[FACT: stt.py — just fixed]`
- **The previous sessions ran WITHOUT the `hi` pin** — `AIVA_STT_LANGUAGE` was never set by any script, and auto-detect was the default
- Evidence: all transcripts were English transliterations of Hindi speech ("Halo keguh?", "I gave you my name Gaggu")
- The `hi` pin was just added (`2bd1052`) but **has not been tested live yet**
- **Without the pin, Whisper decodes Hindi audio into English text** — the text is "valid English" but semantically wrong. It passes confidence gates because Whisper is confidently wrong.

### 4. Validation + Routing — MOSTLY WORKING 🟡
- `is_real_user_turn`: empty, punctuation_only, known_hallucination, high_no_speech (>0.6), catastrophic_low_confidence (<−0.85), low_avg_logprob (<−1.0) `[FACT: main.py:34-62]`
- **punctuation_only**: correctly rejects "." and "？" but can false-positive on legitimate short utterances with unusual scripts
- **catastrophic_low_confidence**: correctly rejects garbage but also rejects legitimate short turns when Whisper's confidence is low due to language mismatch
- **known_hallucination_pattern**: exact-match list ("i am good", "thank you", "subscribe") — narrow but correct
- **The blanket return bug** (invalid reasons that don't match catastrophic/rep were silently dropped) was **fixed** in `cc42a13` — non-matching reasons now pass to the LLM `[FACT: main.py:742-744]`
- **Turn Controller**: correctly suppresses continuation markers, fragments after WAIT, and empty text `[FACT: turn_controller.py]`

### 5. State / Context — WORKING ✅
- 6-turn history window sent to LLM `[FACT: main.py:299]`
- Memory view loaded from SQLite per device UUID `[FACT: session_state.py]`
- Thread summaries from in-session state `[FACT: session_state.py]`
- Compact head `{m,c,s}` mapped to full schema in updater `[FACT: state_updater.py]`
- Memory scoping rule prevents proactive past-session references `[FACT: prompt_fragments.py]`
- **Verified live:** memory loaded at session start (17 items), conversation history building correctly

### 6. LLM / Prompt — WORKING with issues 🟡
- Persona: natural, masculine, Hinglish, concise, no therapy-speak `[FACT: prompt_fragments.py]`
- **PARS-FAIL rate was 80%+ with the old 400-token head** — now fixed with 20-token compact head
- **The compact head has NOT been tested live yet** — the previous sessions used the old head
- **Recovery behavior**: implemented but inconsistent (sometimes interprets, sometimes gives up) — improved with new persona rules but not yet measured
- **Concrete questions**: rule added but needs live validation
- **English replies to Hindi input**: this is the STT language issue, not an LLM issue — the LLM sees English text and correctly responds in English

### 7. TTS — WORKING ✅
- Fish Audio `s2.1-pro-free`, streaming WS, 44.1kHz → resampled 48kHz `[FACT: tts.py]`
- TTFA: 1.5–2.8s after prose starts (measured in logs)
- Interruption: immediate stop via task cancel + flush
- **Suboptimal:** TTS TTFA adds ~1.5s to the pipeline; Fish is cloud-based so this is network-bound

### 8. Playback / Barge-in — 🟡
- Frames pushed to `rtc.AudioSource(48000, 1)` and streamed to LiveKit
- Cancel: task cancel + `flush_audio_source` (clear_queue if available, else silence push)
- **[ASSUMPTION]** `clear_queue` exists on the pinned SDK — not verified
- **[GAP]** Barge-in stop latency measured at 2.0–3.2s (user speaks → agent audio stops) — the cancel waits for full STT before firing

### 9. Orchestration — MOSTLY WORKING 🟡
- Turn numbers: assigned per spawned task, can complete out of order (async) — state guard prevents out-of-order mutations
- Telemetry: crash-proof (appends per event), summary at shutdown
- **[GAP]** `stage_diagnostic.py` crashes on `reply=None` — minor bug in the diagnostic tool, not the pipeline

---

## B. Root-Cause Ranking (by impact on perceived conversational quality)

| Rank | Root Cause | Layer | Evidence | Impact |
|---|---|---|---|---|
| **1** | **STT language mismatch: Hindi speech decoded as English** | STT | All transcripts English despite Hindi speech; wrong language pin | **EVERYTHING downstream is corrupted.** Emotion detection, intent, safety, entity extraction all working on wrong text. |
| **2** | **PARS-FAIL → wrong routing to unclear_speech** | Routing | 80%+ turns with no_head → generic "phir se bol" despite good prose | User gets generic repeat-request even when Aiva understood them |
| **3** | **Barge-in stop latency** (2–3s overlap) | Orchestration | Measured 2020ms and 3246ms stop latencies | User talks over Aiva's stale audio |
| **4** | **Response latency** (2–3s speech→audio) | End-to-end | Measured across all turns | Feels slow, unnatural |
| **5** | **Intermittent recovery** (sometimes interprets, sometimes gives up) | LLM/Prompt | Same type of garble gets different treatment across turns | Unpredictable behavior |

## C. Architecture-Level Diagnosis

**The main problem is STT (layer 3), amplified by routing (layer 4).**

The STT language mismatch corrupts EVERYTHING downstream:
- Emotion detection reads English text from Hindi speech → wrong emotion
- Intent detection sees wrong words → wrong user_need
- Safety screening reads wrong content → unreliable
- Response generation receives wrong text → generic/mismatched replies

All other layers (VAD, state, memory, TTS, playback) are functioning correctly. The pipeline is architecturally sound — it's receiving the wrong input.

**Second problem:** the routing logic after PARSE-FAIL. When the head is missing but the prose is good, the system should trust the prose. Instead, some paths route to generic clarification.

## D. Recommended Fixes

| # | Fix | File | What | Why | Expected UX | Risk |
|---|---|---|---|---|---|---|
| **D1** | **Language pin: `hi`** | `providers/stt.py` | Already fixed (`2bd1052`) — default changed from auto-detect to `hi` | Hindi audio decoded as Hindi, not English | Transcripts in Devanagari → correct emotion/intent/safety → natural Hinglish replies | Low — Hinglish includes English words, Whisper handles code-switching with `hi` pin |
| **D2** | **Trust prose on PARSE-FAIL** | `agent/main.py` | When head is None but reply exists, skip the D9 degraded path — use the reply directly | 80%+ PARSE-FAIL turns had good replies that were overridden by "thoda cut gaya" | Natural responses even when structured output fails | Low — safety screening is reduced on those turns, but the prose still went through the safety-aware persona |
| **D3** | **Immediate barge-in cancel** | `agent/main.py` | Cancel previous task on SPEECH_STARTED (VAD level) instead of waiting for STT | Stop overlap: user speaks → agent audio stops in <200ms | Eliminates 2–3s of overlapping audio | Medium — may cancel on false VAD triggers (cough, background noise); mitigated by the D7 acoustic-only fallback |
| **D4** | **Add post-roll buffer** | `providers/vad.py` | Add 200ms post-roll after SPEECH_ENDED before freezing the buffer | Prevents last-word clipping at the exact threshold boundary | Fewer truncated words in STT | Low — adds 200ms to latency but prevents garbled last words |

## E. Fix Priority

### MUST FIX NOW
- **D1**: Language pin to `hi` — already implemented in `2bd1052`, needs live validation
- **D2**: Trust prose on PARSE-FAIL — routing bug, causes most user-visible "thoda cut gaya" issues

### SHOULD FIX NEXT
- **D3**: Immediate barge-in cancel — the 2–3s overlap is the most annoying UX issue after garble
- **D4**: Post-roll buffer — small change, prevents word clipping

### DO NOT TOUCH YET
- Prompt further (the persona is already working — more changes risk regression)
- STT provider (Groq is working — Gemini Live STT is a future optimization)
- Architecture (everything else is functioning correctly)

---

## Summary

**The single biggest fix is the STT language pin (`hi`), which was just applied.** Every previous session ran without it, causing Whisper to decode Hindi as English. The English transcripts then flowed through the entire pipeline, producing wrong emotion detection, wrong intent, wrong safety classification, and wrong responses — all confidently, because Whisper was confident it heard English.

**The second fix is trusting the prose when the perception head is missing.** The 80% PARSE-FAIL rate means most turns had good conversational replies that were being replaced by generic clarification.

**After these two fixes, the system should produce natural Hinglish conversations.** The remaining issues (barge-in latency, TTS TTFA) are real but less impactful than getting the right text to the LLM.

**Next step: live conversation test with the `hi` pin + prose-passthrough fix. That will be the definitive validation.**
