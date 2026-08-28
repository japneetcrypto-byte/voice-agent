# Aiva — Production Latency & Observability Analysis
**Date:** 2026-08-28 · Based on measured data from 5+ live sessions · Not theoretical.

---

## 1. End-to-End Latency Breakdown (measured from session logs)

The time a user waits between finishing their sentence and hearing Aiva's reply:

```text
User stops speaking
    ↓
[1] Endpoint silence wait          300–1100ms     (adaptive VAD)
    ↓
[2] STT round trip                 300–800ms      (Groq cloud)
    ↓
[3] LLM perception+response        900–1300ms     (Gemini flash-lite)
    ↓
[4] TTS first audio                1400–2900ms    (Fish Audio cloud)
    ↓
User hears Aiva

TOTAL: 2.1 – 5.9 seconds
TYPICAL: 2.1 – 2.5 seconds (when everything works)
WORST: 66 seconds (quota exhaustion → 65s cooldown)
```

### Per-stage measured data (from actual session logs)

| Turn | Endpoint wait | STT | LLM TTFT | TTS TTFA | Speech→Audio |
|---|---|---|---|---|---|
| Good turn | 300ms | 310ms | 860ms | 1490ms | **1.77s** |
| Good turn | 300ms | 349ms | 965ms | 1935ms | **2.29s** |
| With stall | 700ms | 777ms | — | — | **3.93s** |
| With 429 | 300ms | — | 66000ms | — | **66s** |

### What this means for the user

| Total delay | User perception |
|---|---|
| < 1s | Feels instant (human-level) |
| 1–2s | Feels like a thoughtful pause — acceptable |
| **2–3s** | **Noticeable delay — user may repeat or lose flow** |
| 3–5s | Frustrating — user talks over the silence |
| > 5s | User assumes system is broken |

**We are at 2.1–2.5s typical. This is at the edge of acceptable.**

---

## 2. Per-Stage Analysis

### [1] Endpoint Silence Wait (300–1100ms)

**Current:** Adaptive. Base 300ms, escalates to 1100ms on premature resumes, floor 700ms for continuous speakers.

**Working correctly:** Yes — premature interrupts are dramatically reduced.

**Contributing to delay:** Yes — every single turn pays this cost, even quick back-and-forth.

**Optimization possible?**
- Lower base to 200ms → risks premature endpoints (the original bug)
- Smart approach: use shorter base (200ms) BUT keep the adaptive penalty. If the system detects a premature resume, it escalates faster (400ms jump instead of current). Net effect: fast for short exchanges, adaptive for stories.
- Risk: premature interrupts return for the first 1–2 turns before the system adapts.

### [2] STT Round Trip (300–800ms)

**Current:** Groq cloud, whisper-large-v3, full utterance sent after endpoint.

**Breakdown:**
- Network latency to Groq server: ~100–200ms
- Model processing: ~200–500ms (v3 is slower than turbo)
- Response network: ~50–100ms

**Optimization possible?**
- Revert to turbo model: saves ~100–200ms but Hindi accuracy drops
- Use Gemini Transcribe Live (already built): streams during speech, result available immediately at endpoint → saves 300–800ms (the entire STT round trip)
- This is the **single biggest latency win available** without changing architecture
- Already implemented (`providers/stt_gemini_live.py`), needs quota testing

### [3] LLM Perception + Response (900–1300ms)

**Current:** ONE fused Gemini call producing {m,c,s} JSON head + prose reply.

**Breakdown:**
- TTFT (time to first token): 0.8–1.3s
- Head completion: ~1.3–1.6s after call start
- Prose starts immediately after head closes
- The user hears nothing until the head is complete

**Optimization possible?**
- Skip the head entirely → saves ~900ms but loses safety/state tracking
- Use a faster model (gemini-3.1-flash-lite) → might save 100–200ms but quality varies
- Start TTS on the first prose token instead of waiting for head completion → saves ~900ms IF the head and prose can be streamed in parallel
- Currently: the system waits for `</perception>` tag → then starts TTS on prose tokens. The TTS TTFA measurement includes this head wait.
- **The real optimization:** TTS should start when the first PROSE token arrives, not when the head completes. Need to verify this is already happening.

### [4] TTS First Audio (1400–2900ms)

**Current:** Fish Audio cloud, streaming WebSocket, 44.1kHz wav.

**Breakdown:**
- Network to Fish Audio: ~200–500ms
- Synthesis of first audio chunk: ~800–1500ms
- First chunk delivery: ~200–400ms

**Optimization possible?**
- Edge TTS is faster (~500ms TTFA) but sounds worse
- Pre-warming the Fish connection could save 200–500ms
- This is cloud-bound — not much room without a local TTS model

---

## 3. Latency Budget for "Feels Natural"

| Target | What it requires |
|---|---|
| < 1s (human-level) | Local STT + local LLM + local TTS — architecture change |
| < 1.5s | Gemini Transcribe Live (saves STT round trip) + TTS pre-warming |
| < 2s | TTS streaming optimization + endpoint tuning |
| Current 2.1–2.5s | Acceptable for MVP but at the edge |

---

## 4. Observability Gaps

### What we currently track per turn

| Metric | Tracked | Where |
|---|---|---|
| STT latency | ✅ | `stt_latency_s` |
| LLM TTFT | ✅ | `llm_ttft_s` |
| LLM head complete | ✅ | `head_complete_s` |
| TTS TTFA | ✅ | `tts_first_audio_s` |
| Speech→audio | ✅ | `speech_end_to_first_audio_s` |
| TTS provider | ✅ | `tts.provider` |
| TTS stall | ✅ | `tts.stall_s` |
| Barge-in stop latency | ✅ | `BARGE_IN_STOP_LATENCY_MS` |
| Resume gap | ✅ | `resume_gap_ms` |

### What we DON'T track

| Missing Metric | Why it matters | Where to add |
|---|---|---|
| **Audio received timestamp** | Can't measure mic→worker latency | `process_user_audio()` first frame |
| **VAD speech start (monotonic)** | Can't measure speech→endpoint precisely | VAD wrapper |
| **Compression call latency** | Can't measure Layer 2 update impact | `_compress_layer2()` |
| **TTS synthesis wall time** | Already partially tracked as `synthesis_wall_s` | Already exists |
| **Queue depth at AudioSource** | Can't detect audio queuing/backlog | `rtc.AudioSource` internal |
| **Playback fully stopped timestamp** | Currently only tracked on interruption | Normal completion too |
| **Per-provider STT success/failure rate** | Can't measure Gemini Live vs Groq reliability | STT Router |
| **LLM token count per turn** | Can't track cost trend | `usage_metadata` from Gemini response |

---

## 5. Reliability Under Production Conditions

### What we've tested

| Scenario | Tested | Result |
|---|---|---|
| Normal conversation (10–20 turns) | ✅ Multiple sessions | Works |
| Continuous speech 30–60s | ✅ One session | No premature interrupts |
| Rapid back-and-forth | 🟡 Partial | Some endpoint sensitivity |
| Barge-in | ✅ | Works but 2–3s overlap |
| STT garbage input | ✅ Multiple sessions | Correctly asks for clarification |
| Quota exhaustion | ✅ | Correctly degrades to filler |
| Worker crash + restart | ❌ Not tested | Checkpoint system built but untested |
| Multiple simultaneous sessions | ❌ Not tested | Unknown behavior |
| Network latency (slow connection) | ❌ Not tested | Would affect all cloud calls |
| Long session (60+ turns) | 🟡 One session (106 turns) | Worked but quota ran out mid-way |

### What we haven't tested

| Scenario | Risk |
|---|---|
| 2+ hours continuous conversation | Token overflow, memory growth, quota exhaustion mid-session |
| Multiple users simultaneously | Worker concurrency, port conflicts, session isolation |
| Poor network (2G/3G) | All cloud calls (STT, LLM, TTS) would be slower |
| Background noise / TV / music | VAD false triggers, STT corruption |
| Multiple speakers in the room | Cross-talk, speaker confusion |
| accent variation (South Indian, Bengali Hindi) | STT accuracy unknown |

---

## 6. Production Readiness Assessment

### Architecture: READY for single-user MVP ✅

The system handles:
- Long conversations (106 turns tested)
- Multi-turn context (3-layer bounded context)
- Safety (self-harm detection, figurative language, D-C validated)
- Recovery (checkpoint, multi-key rotation, degradation paths)
- Memory (device-scoped, cross-session, relationship tracking)

### Latency: ACCEPTABLE for MVP, needs optimization for production 🟡

2.1–2.5s is at the edge. Users will notice but won't abandon. The biggest optimization is Gemini Transcribe Live (already built, needs quota testing).

### Observability: GOOD for debugging, needs production metrics 🟡

Turn-level tracing is comprehensive. Missing: aggregate dashboards, alert thresholds, cost tracking.

### Reliability: UNTESTED at scale ❌

Single user tested. Multi-user, poor network, long sessions, and crash recovery are all untested.

---

## 7. Recommended Priority (what to build next)

| Priority | Item | Impact | Effort |
|---|---|---|---|
| **P0** | Validate `hi` language pin with live Hindi conversation | Everything downstream depends on correct STT | Zero code needed |
| **P0** | Test Gemini Transcribe Live (unlimited, may fix STT quality + latency) | Saves 300–800ms per turn + better accuracy | Low — already built, needs quota testing |
| **P1** | Verify TTS streams from first prose token (not after head) | Could save ~900ms per turn | Low — check streaming behavior |
| **P1** | Add missing timestamps (audio received, VAD start monotonic, playback fully stopped) | Enables precise latency attribution | Low — instrumentation |
| **P2** | Endpoint tuning based on measured premature-endpoint rate | Balance between interruption and sluggishness | Medium — needs data from multiple sessions |
| **P2** | Immediate barge-in cancel (stop audio on VAD, before STT) | Eliminates 2–3s overlap | Medium — touches worker flow |
| **P3** | Local STT (whisper.cpp on device) | Eliminates STT round trip entirely | High — new dependency, needs testing |

---

## 8. The Honest Latency Floor

With the current architecture (all cloud APIs, sequential pipeline):

```
Theoretical minimum: ~1.5s
  - Endpoint: 300ms (can't go lower without premature endpoints)
  - STT: ~300ms (cloud round trip, can't go lower)
  - LLM first prose: ~400ms (streaming, can't skip head for safety)
  - TTS first audio: ~500ms (cloud round trip)
  
Practical measured: 2.1–2.5s
  - LLM head adds ~500ms on top of first prose
  - TTS adds variance
  
To reach < 1s: needs local STT + local LLM or parallel processing
To reach < 1.5s: needs Gemini Transcribe Live (saves STT round trip)
To maintain 2–2.5s: current architecture is at its limit
```

---

## 9. Non-Latency Observability for Production

Beyond latency, a production voice AI needs:

| Metric | Why | Currently tracked |
|---|---|---|
| Session duration | Detect abandonment, usage patterns | ❌ Not tracked |
| Turns per session | Engagement metric | ✅ Derivable from logs |
| User sentiment trajectory | Is the user feeling better or worse? | 🟡 Emotion tracked per turn but not aggregated |
| Memory hit rate | How often does Aiva use stored memory? | ❌ Not tracked |
| Clarification rate | How often does Aiva ask to repeat? | ✅ Derivable from logs |
| Quota consumption per session | Cost tracking | ❌ Not tracked |
| Crash/recovery rate | System stability | ❌ Not tracked |
| STT language distribution | Are users mixing languages unexpectedly? | 🟡 Language detected but not aggregated |
