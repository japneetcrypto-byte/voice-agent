# Architecture Snapshot — Voice Agent MVP ("Aiva")

**Date:** 2026-08-26 · **Repo state:** `arena/01a03e6f-voice-agent` @ `e2c22c1` · **Purpose:** factual input for designing the Emotional Conversation State Model. No redesign proposed here.

**Evidence tags:** `[code]` = read directly from source (file:line) · `[live]` = verified in this session's testing · `[inferred]` = deduced from code behavior · `[assumed]` = plausible but unverified · `[gap]` = absent from codebase.

---

## 1. Current architecture & component flow

Custom hand-orchestrated LiveKit pipeline — **not** the standard `livekit-agents` `AgentSession`/`VoicePipelineAgent`. All orchestration (VAD gating, turn lifecycle, interruption, context) is manual in `agent/main.py` `[code:agent/main.py:90-473]`.

```
Browser (React/Vite, App.tsx)
  │  getUserMedia via LiveKit SDK, echoCancellation+NS+AGC ON [code:App.tsx:48]
  │  GET http://localhost:3001/token?room=<random>  [code:App.tsx:19]
  ▼
token_server.py (aiohttp, :3001)
  │  tries LiveKit Cloud room create → on quota/billing/auth error falls back
  │  to local docker LiveKit (ws://127.0.0.1:7880); returns JWT + URL [code:token_server.py:10-55]
  ▼
LiveKit room (cloud or local docker)
  │  user mic track ──────────────► agent worker (agent/main.py entrypoint)
  │                                      │  rtc.AudioStream → resample →16k
  │                                      │  TEN VAD → energy gate → Groq Whisper STT
  │                                      │  → Gemini LLM (streaming) → TTS (Fish Audio, Edge fallback)
  │                                      ▼
  │  ◄────────────────── agent audio track (48kHz mono "agent-mic")
  ▼
speaker
```

Two worker processes run side-by-side (cloud + local targets) so the agent exists wherever the user lands `[code:README]`. Each job creates one `ConversationSession` `[code:main.py:100]`.

## 2. File tree (roles)

```
voice-agent/
├── agent/
│   ├── main.py            # ALL orchestration: turn lifecycle, VAD loop, barge-in,
│   │                      #   echo/self-echo filters, metrics + event logging
│   ├── session.py         # ConversationSession: system prompt ("Aiva"), ephemeral
│   │                      #   message history, echo-reference buffer
│   ├── config.py          # env reads (LiveKit x2, Gemini, Fish Audio, STT thresholds)
│   └── token_server.py    # :3001 JWT issuer + cloud→local routing
├── providers/
│   ├── vad.py             # TEN VAD wrapper (hop 256, hysteresis counters)
│   ├── stt.py             # Groq whisper-large-v3-turbo, Devanagari→ITRANS romanizer
│   ├── llm.py             # Google genai streaming (gemini-3.5-flash-lite, temp 0.7)
│   └── tts.py             # FishAudioTTSProvider (s2.1-pro-free @44.1k) + EdgeTTS
│                          #   fallback + FallbackTTSProvider (5s first-chunk guard)
├── frontend/              # React+Vite: one button → token → LiveKit room (86 lines)
├── benchmark.py           # standalone local faster-whisper experiment (NOT in runtime path)
├── infra/, docs/          # Phase-1 Oracle/Fish-Speech plan + diagnostics (this workstream)
├── pyproject.toml / uv.lock  # deps pinned (livekit-agents 1.6.10 era)
└── logs/                  # runtime JSONL traces (gitignored; live on worker machine only)
```

## 3. Audio pipeline (implemented)

| Stage | Implementation | Key parameters `[code]` |
|---|---|---|
| **Capture (client)** | LiveKit browser SDK | `echoCancellation: true, noiseSuppression: true, autoGainControl: true` `[App.tsx:48]` |
| **Ingest (server)** | `rtc.AudioStream(track)` → `AudioResampler` → 16 kHz int16 | resampler recreated on rate change; state kept warm `[main.py:250-277]` |
| **VAD** | TEN VAD via `ten_vad` pkg, own wrapper | hop 256 samples; speech prob threshold 0.5; **SPEECH_STARTED** after ≥200 ms speech frames; **SPEECH_ENDED** after 300 ms silence; hysteresis counters reset opposite class `[vad.py:17-66]` |
| **Pre-roll** | 300 ms rolling int16 buffer prepended at SPEECH_STARTED | 4800 samples `[main.py:295-310]` |
| **Energy gate** | whole-utterance RMS ≥ 2000 **or** peak ≥ 3500 (int16 scale) else reject | `[main.py:329-346]` |
| **STT** | Groq `whisper-large-v3-turbo`, verbose_json; batch per-utterance (no streaming); rejects <0.25 s; Devanagari → ITRANS romanization; confidence from **segments[0]** only (no_speech_prob, avg_logprob, compression_ratio) | `[stt.py]` |
| **Validity filter** | `is_real_user_turn`: hallucination blacklist ("i am good", "thanks for watching", "subscribe."…), punctuation-only reject, no_speech_prob > 0.6, avg_logprob < −0.85 / < −1.0 | `[main.py:34-61]` |
| **Self-echo filter** | `is_echo`: difflib SequenceMatcher of transcript vs `session.recent_agent_text` (sliding word windows), reject > 0.65 similarity | `[main.py:64-88]` |
| **LLM** | Gemini `gemini-3.5-flash-lite` via `google-genai` async streaming; temperature 0.7; **no max_output_tokens**; generic dict context → Gemini schema mapping | `[llm.py]` |
| **TTS** | `FallbackTTSProvider` → Fish Audio `s2.1-pro-free`, wav, 44100 Hz (voice clone `f741bf…`), streamed over plugin WS; EdgeTTS `en-IN-NeerjaNeural` per-turn fallback if init fails or **first audio >5 s** | `[tts.py]` `[live]` |
| **Playback** | frames → `rtc.AudioSource(48000, 1)` → track "agent-mic"; 44.1k Fish frames resampled →48k in provider; `wait_for_playout()` before turn close | `[main.py:102,207-225]` `[tts.py]` |

## 4. Conversation state & context

`ConversationSession` `[code:session.py]` — **all state is ephemeral, in-process, per job**:

- `history: list[Message]` — `role`/`content`/`interrupted` only. No timestamps, no audio, no metadata, no user identity.
- **Grows unbounded** during a session; `get_context()` = system prompt + full history, sent verbatim to Gemini every turn. **No truncation, no windowing, no summarization** `[gap]`.
- `recent_agent_text: str` — reset to `""` at each response start, accumulates the LLM stream; used **only** for echo detection `[main.py:167,195]`.
- **No persistence**: nothing survives process restart or a new room join; no DB, no files (except debug logs). `clear()` exists but nothing calls it `[code]`.
- Interrupted agent replies are stored with suffix `" [interrupted before finishing]"` `[session.py:41]`.
- Room name is random per browser connect `[App.tsx:19]` → **no session continuity across reconnects** `[inferred]`.
- Per-turn debug record (`turn` dict) is written to `logs/session_*.log` — that's logging, **not** conversation state `[main.py:353-380]`.

**Bottom line:** the only "conversation state model" that exists today is a flat, growing message list + one echo-detection string. None of the seven state dimensions in your target model exist yet.

## 5. Prompts

**Exactly one prompt exists** — the system prompt, verbatim `[code:session.py:19-32]`:

> "You are Aiva, a sharp and warm voice assistant for natural phone-style conversations with Indian users who mix Hindi and English freely.
> RULES — follow these strictly:
> 1. Maximum 2 sentences per response. You are speaking aloud — brevity is everything.
> 2. No bullet points, lists, markdown, or special characters ever.
> 3. Reply in Romanized Hindi/Hinglish if the user speaks Hindi — never Devanagari script.
> 4. If you genuinely cannot answer a question, say: 'Mujhe is baare mein pata nahi, kuch aur poochh sakte ho?' — never make up facts.
> 5. Answer factual questions directly and precisely. No padding.
> 6. Never mention you are an AI unless directly asked.
> 7. Match the user's energy — casual if they're casual, precise if they ask something specific."

- Persona: factual phone-assistant ("Aiva") — **not** a venting/emotional-support persona.
- No per-turn prompt assembly beyond history append; no emotion/intent/risk directives `[gap]`. No other prompts anywhere in the repo (verified by grep).

## 6. Exact turn lifecycle

1. **User audio arrives** → resample →16k; VAD runs on **every** frame, always (needed for barge-in) `[main.py:289]`.
2. **SPEECH_STARTED** → 300 ms pre-roll prepended; if agent is speaking → `BARGE_IN_CANDIDATE` (speech buffered, *not* acted on yet); else `USER_SPEECH_STARTED` `[main.py:304-319]`.
3. **SPEECH_ENDED** (300 ms silence) → concatenate buffer → energy gate (RMS/peak) → pass → spawn `transcribe_and_respond` task `[main.py:320-458]`.
4. **STT** (blocking call in thread) → Groq Whisper → romanize → echo check (`is_echo` vs agent's recent text) → validity filter. Rejected turns are logged and dropped.
5. **Barge-in decision**: if agent was speaking **and** transcript valid → cancel previous agent task (`prev_task.cancel()`) → `AGENT_TASK_CANCEL_REQUESTED` `[main.py:425-428]`. If invalid → treated as echo/noise, agent keeps talking.
6. **LLM**: user text appended to history → full context streamed from Gemini; TTFT logged at first chunk `[main.py:180-199]`.
7. **TTS**: same stream tee'd into `synthesize_stream`; first audio frame → TTFA logged → `capture_frame` per chunk until stream ends → `wait_for_playout` `[main.py:201-229]`.
8. **Completion**: full reply stored (`add_agent_message`), `PLAYBACK_COMPLETED`, `agent_speaking_event` cleared, `agent_audio_ended_at` stamped → loop returns to step 1.
9. **Interruption path**: `CancelledError` inside the agent task → `flush_audio_source` (clears LiveKit buffer if SDK supports `clear_queue`), truncated reply saved with interrupted flag, task re-raises → cancelled `[main.py:235-249]`.
10. **Guard**: `run_agent_response` no-ops if a newer agent task is already active `[main.py:162-163]`.

**Instrumented metrics per turn** (all implemented): `stt_latency_s`, `llm_ttft_s`, `tts_first_audio_s`, `interrupted`, `interruption_timestamp`, plus ~20 event types in `logs/events_*.log` (`STT_STARTED/COMPLETED`, `LLM_STARTED/FIRST_TOKEN/COMPLETED`, `TTS_STARTED/FIRST_AUDIO/COMPLETED`, `PLAYBACK_*`, `BARGE_IN_*`, `AGENT_ECHO_IGNORED`, `AUDIO_GATE_REJECTED`, …) `[code:main.py]`.

**End-to-end latency budget** `[inferred from code]`: 300 ms silence end-point + STT round-trip (network, batch, full-utterance) + LLM TTFT + TTS TTFA. No streaming STT, no partial transcripts → user is always waiting for full utterance + cloud round-trips before the LLM even starts.

## 7. Known issues / limitations (evidence-tagged)

**Interruption & barge-in**
- Barge-in decision waits for **full STT round-trip** (speech end → cloud transcription → filters) before cancelling agent audio → perceptible lag; agent keeps talking ~0.5–1.5 s after user starts `[inferred from main.py:397-428]`.
- Cancellation race: previous task is cancelled by the *new* task after spawn; window where both run `[inferred, main.py:452-458]`.
- `flush_audio_source` fallback path pushes silence — it does **not** actually drain already-buffered audio if `clear_queue` is absent in the pinned SDK `[code:main.py:103-119]` → possible residual agent audio after barge-in `[inferred]`.

**Self-echo / false turns**
- Browser AEC is on, but agent audio leaking into user mic still reaches VAD; system relies on layered rejection (energy gate → text-similarity echo → Whisper confidence → hallucination blacklist). Each layer has tuned thresholds that will misfire on quiet/short real speech (e.g., RMS gate 2000 on int16 ≈ loud speech only) `[code + inferred]`.
- `is_echo` only compares against the **current turn's** agent text (`recent_agent_text` reset per response) — echoes of the *previous* reply pass through `[code:main.py:167]`.

**TTS (current, post-fix)**
- Plugin upstream bug: non-`finish` server error events are logged at debug and ignored → silent stall (observed today with 48 kHz rejection) `[live]`. Mitigated by the 5 s first-chunk fallback, but the stalled stream lingers.
- EdgeTTS fallback path **buffers the entire text before speaking** (old code streamed per sentence) → slow fallback starts, and cancelling mid-synthesis yields nothing `[code:tts.py]`.
- Per-turn fallback can alternate voices mid-conversation (Fish turn, Edge turn) — no sticky degradation `[code]`.
- Fish free model = `s2.1-pro-free` @44.1 kHz (server-verified); paid `s2.1-pro` blocked by **separate API credit (402)** `[live]`.

**STT**
- Batch-only: no partials → no early LLM start, no fast barge-in `[code]`.
- Confidence taken from `segments[0]` only — long multi-segment utterances misjudged `[code:stt.py]`.
- ITRANS romanization of Devanagari can produce non-colloquial spellings the LLM must interpret `[code + assumed]`.

**Context/state**
- Unbounded history → token growth, cost, and drift within long sessions; no windowing/summarization `[code]`.
- Nothing survives reconnect; random room per visit; no user identity `[code]`.
- Stale artifact: `.gitignore` lists `session_summary.jsonl` but nothing writes it `[code]`.

**Other**
- Frontend hardcodes `http://localhost:3001` `[App.tsx:19]` — breaks when deployed off-localhost.
- Token server CORS `*` with credentials `[token_server.py:61-67]` — fine for dev, not deployment.
- Per-frame VAD `print()` diagnostics (min/max/mean every chunk) — log spam + overhead in hot path `[code:vad.py:32-40]`.
- `benchmark.py` (local whisper) is legacy, not wired into runtime `[code]`.

## 8. Representative conversation trace

**No real trace is available in this repo** — `logs/` is gitignored and lives only on the worker machine `[code:main.py:133-142]`. Real traces recoverable at `logs/session_*.log` (one JSON object per turn) and `logs/events_*.log` (event stream) on the Mac that runs the worker.

Turn-record schema actually written per turn `[code:main.py:353-380]`:

```json
{"turn": 3, "user_speech_start": "...Z", "user_speech_end": "...Z",
 "agent_was_speaking": false, "ms_since_agent_audio_end": null,
 "stt_transcript": "...", "stt_language": "hi", "stt_no_speech_prob": 0.01,
 "stt_avg_logprob": -0.31, "stt_compression_ratio": 1.4, "stt_latency_s": 0.42,
 "stt_valid": true, "stt_rejection_reason": "accepted",
 "response_trigger_reason": "user_speech_ended", "conversation_turn_count": 7,
 "llm_input": [...], "llm_response": "...", "llm_ttft_s": 0.31,
 "tts_first_audio_s": 0.89, "interrupted": false, "interruption_timestamp": null}
```

**Reconstructed illustrative trace** (schema-accurate, values illustrative — **not** a real log):

```json
{"turn": 3, "user_speech_start": "T10:12:04.100Z", "user_speech_end": "T10:12:06.900Z",
 "stt_transcript": "yaar aaj bahut thak gaya hu, sab kuch galat ho raha hai",
 "stt_valid": true, "stt_latency_s": 0.42,
 "llm_ttft_s": 0.31, "llm_response": "Arre yaar, din kaisa tha? Batao kya hua.",
 "tts_first_audio_s": 0.94, "interrupted": false, "response_trigger_reason": "completed"}
```

Interruption example — event sequence as the code would emit it: `BARGE_IN_CANDIDATE` → `USER_SPEECH_ENDED` → `STT_COMPLETED` → `BARGE_IN_EVALUATED {decision: INTERRUPT_AGENT}` → `AGENT_TASK_CANCEL_REQUESTED` → `AGENT_CANCELLED_EXCEPTION` → `PLAYBACK_*` never completes; turn record gets `interrupted: true` + truncated `llm_response` stored with `[interrupted before finishing]`.

## 9. Existing emotion / intent / memory / safety / sentiment logic

| Capability | Status |
|---|---|
| Emotion detection / sentiment | **None** `[gap]` |
| Intent classification | **None** `[gap]` |
| Long-term / cross-session memory | **None** (ephemeral in-process history only) `[gap]` |
| Safety / risk / crisis handling | **None** — no self-harm, distress, or escalation logic anywhere `[gap]` |
| Emotional response policy | **None** — only "match the user's energy" as a vague prompt rule (§5) `[code]` |
| Closest existing analogues (speech-validity machinery, not emotion): | hallucination blacklist, no_speech/logprob gates, RMS/peak energy gate, text-similarity echo filter, Hinglish romanization, interrupted-message marking |

For a venting companion this is the single biggest gap: the system currently treats every utterance as a neutral Q&A turn.

## 10. Stack / models / config

| Layer | Tech | Model / params |
|---|---|---|
| Runtime | Python 3.11, `uv`; LiveKit agents SDK **1.6.10** (custom rtc orchestration) | worker via `python -m agent.main start`, `WORKER_TARGET=cloud\|local` |
| Transport | LiveKit Cloud primary / local docker fallback | ports 7880-7882; token server :3001; Vite :5173 |
| VAD | `ten-vad` 1.0.6.8 | hop 256, thr 0.5, silence-end 300 ms, min-speech 200 ms, 16 kHz |
| STT | Groq SDK → `whisper-large-v3-turbo` | batch per-utterance, verbose_json, 16 kHz wav |
| LLM | `google-genai` → `gemini-3.5-flash-lite` | temp 0.7, streaming, no token cap, unbounded history |
| TTS | `livekit-plugins-fishaudio` 1.6.10 → `s2.1-pro-free` (voice clone `f741bf56…`, wav 44.1 kHz → resampled 48 kHz); `edge-tts` fallback (`en-IN-NeerjaNeural`); 5 s first-chunk failover | `[live]` |
| Frontend | React + Vite + `@livekit/components-react` | AEC/NS/AGC on; random room per connect |
| Config | `.env`: `LIVEKIT_CLOUD_*`, `LIVEKIT_LOCAL_*`, `GEMINI_API_KEY`, `FISH_AUDIO_API_KEY`, `FISH_AUDIO_REFERENCE_ID`, `GROQ_API_KEY` (used by STT), thresholds `NO_SPEECH_THRESHOLD=0.6`, `AVG_LOGPROB_THRESHOLD=-1.0` | |
| Deps present but unused at runtime | `faster-whisper` (benchmark only), `scipy`, `indic-transliteration` (actually used by STT), `aiohttp-cors` (token server) | |

---

### Summary for the state-model design (facts only)

Implemented today: transport, capture, VAD-endpointing, batch STT + validity gates, streaming LLM with one static system prompt, streaming TTS with fallback, manual barge-in-after-STT, per-turn JSONL metrics. Not implemented: any emotional/sentiment/safety/intent state, memory beyond one session, session continuity, streaming STT, response-length policies beyond a prompt rule, and any notion of topic/thread structure. The seven target state dimensions (Turn, Conversation, Topic/Thread, Emotional, Memory, Safety/Risk, Interaction Mode) currently have **no first-class representation** — their only precursors are the `turn` debug dict, the flat message history, the `recent_agent_text` buffer, and the prompt's "match the user's energy" line.
