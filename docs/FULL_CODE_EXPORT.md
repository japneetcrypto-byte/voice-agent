# Aiva — Full Code Export (voice pipeline, current state)

Generated from branch `arena/01a03e6f-voice-agent` @ `02275df`

Files: the complete conversational engine (Phase 5) + providers + frontend identity handoff.

## `agent/main.py`  (768 lines)

```python
import os
from dotenv import load_dotenv

load_dotenv()

# Map the explicit Cloud/Local variables from .env to the standard LiveKit variables 
# before the CLI runs, depending on which worker we are starting.
target = os.getenv("WORKER_TARGET", "cloud")
if target == "cloud":
    os.environ["LIVEKIT_URL"] = os.environ.get("LIVEKIT_CLOUD_URL", "")
    os.environ["LIVEKIT_API_KEY"] = os.environ.get("LIVEKIT_CLOUD_API_KEY", "")
    os.environ["LIVEKIT_API_SECRET"] = os.environ.get("LIVEKIT_CLOUD_API_SECRET", "")
else:
    os.environ["LIVEKIT_URL"] = os.environ.get("LIVEKIT_LOCAL_URL", "ws://127.0.0.1:7880")
    os.environ["LIVEKIT_API_KEY"] = os.environ.get("LIVEKIT_LOCAL_API_KEY", "devkey")
    os.environ["LIVEKIT_API_SECRET"] = os.environ.get("LIVEKIT_LOCAL_API_SECRET", "secret")

import time
import asyncio
import numpy as np
from livekit import rtc
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli

from .session import ConversationSession
from providers.vad import get_vad_provider, VADEvent
from providers.stt import get_stt_provider, devanagari_to_roman
from agent.turn_controller import decide as turn_controller_decide
from providers.llm import get_llm_provider
from providers.tts import get_tts_provider

from agent.config import Config

import re

def is_real_user_turn(transcript, speech_duration_ms: float) -> tuple[bool, str]:
    text = transcript.text.strip()
    if not text:
        return False, "empty_transcript"
        
    # Hallucination patterns
    lower_text = text.lower()
    hallucinations = ["i am good.", "i am good", "thank you.", "thanks for watching.", "subscribe."]
    if any(lower_text == h for h in hallucinations):
        return False, "known_hallucination_pattern"
        
    # Reject if it's just punctuation/symbols (common Whisper noise hallucination)
    if not re.search(r'[a-zA-Z0-9\u0900-\u097F]', text):
        return False, "punctuation_only"
        
    if (transcript.no_speech_prob is not None and 
            transcript.no_speech_prob > Config.NO_SPEECH_THRESHOLD):
        return False, "high_no_speech_prob"
        
    if (transcript.avg_logprob is not None and 
            transcript.avg_logprob < -0.85):
        return False, "catastrophic_low_confidence"
        
    if (transcript.avg_logprob is not None and 
            transcript.avg_logprob < Config.AVG_LOGPROB_THRESHOLD):
        return False, "low_avg_logprob"
        
    return True, "accepted"


# C7-style deterministic turn-taking flags (exact-match only — no interpretation;
# same class as is_real_user_turn's hallucination blacklist). Consumed by the
# policy derivation as structured booleans.
BACKCHANNEL_TOKENS = {"haan", "han", "hmm", "hm", "hmmm", "okay", "ok", "accha", "achha",
                       "acha", "phir", "bol", "yeah", "yes", "हाँ", "हम्म", "अच्छा", "ठीक"}
LISTEN_REQUEST_TOKENS = {"chup", "chupchup", "suno", "suno_bas", "bassuno", "pehlemeribaatsun",
                          "beechmeinmatbolo", "chupraho", "meribaatsun", "pehlesunomera"}


def is_repetition_loop(transcript_text: str) -> bool:
    """Deterministic detector for Whisper degeneration (evidence 2026-08-27):
    'ake ake ake ake', 'bake bake bake': same token repeated >=4x consecutively,
    or one token dominating the transcript."""
    words = re.findall(r"[\w\u0900-\u097F]+", (transcript_text or ""), re.UNICODE)
    if len(words) >= 4:
        run, prev = 1, None
        for w in words:
            lw = w.lower()
            run = run + 1 if lw == prev else 1
            prev = lw
            if run >= 4 and len(prev) >= 2:
                return True
        from collections import Counter
        top, n = Counter(w.lower() for w in words).most_common(1)[0]
        if n >= 3 and len(top) >= 2 and n / len(words) >= 0.5:
            return True
    return False


def classify_turn_relation(transcript_text: str) -> str:
    """Exact-token match on the normalized transcript (no interpretation)."""
    norm = re.sub(r"[^\w\s]", "", (transcript_text or "").lower()).strip()
    norm = re.sub(r"\s+", "", norm)
    if not norm:
        return "empty"
    if any(tok in norm for tok in LISTEN_REQUEST_TOKENS):
        return "listen_request"
    if norm in BACKCHANNEL_TOKENS or norm in {"bas", "hmmhaan", "haanhmm"}:
        return "backchannel"
    return "content"


def is_echo(transcript_text: str, recent_agent_text: str) -> tuple[bool, float]:
    if not recent_agent_text or not transcript_text:
        return False, 0.0
    import re
    import difflib
    norm_trans = re.sub(r'[^\w\s]', '', transcript_text.lower()).strip()
    norm_agent = re.sub(r'[^\w\s]', '', recent_agent_text.lower()).strip()
    if not norm_trans or not norm_agent:
        return False, 0.0
    trans_words = norm_trans.split()
    agent_words = norm_agent.split()
    if not trans_words:
        return False, 0.0
    window_size = len(trans_words)
    max_ratio = 0.0
    if window_size >= len(agent_words):
        max_ratio = difflib.SequenceMatcher(None, norm_trans, norm_agent).ratio()
    else:
        for w_size in [window_size, window_size + 1]:
            if w_size > len(agent_words): continue
            for i in range(len(agent_words) - w_size + 1):
                window_text = " ".join(agent_words[i:i+w_size])
                ratio = difflib.SequenceMatcher(None, norm_trans, window_text).ratio()
                max_ratio = max(max_ratio, ratio)
    return max_ratio > 0.65, max_ratio

async def entrypoint(ctx: JobContext):
    agent_audio_ended_at: float = None  # monotonic time
    print("Connecting to room...")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    print(f"Agent connected to room: {ctx.room.name}")

    vad_provider = get_vad_provider()
    stt_provider = get_stt_provider()
    llm_provider = get_llm_provider()
    tts_provider = get_tts_provider()
    session = ConversationSession()

    # ---- Phase 5 state engine (5.1/5.2/5.3/5.4/5.6) — flag-gated, falls back ----
    state_engine_on = os.getenv("AIVA_STATE_ENGINE", "1") == "1"
    engine = None
    if state_engine_on:
        try:
            from agent.session_state import SessionState
            from agent.fused_turn import FusedLLM
            from agent.memory_store import MemoryStore
            engine = {"fused": FusedLLM(), "store": MemoryStore(), "sess": None}
            print("[StateEngine] on (TRANSPORT_V1.1)")
        except Exception as e:
            print(f"[StateEngine] init failed, plain path: {type(e).__name__}: {e}")
            engine = None

    agent_source = rtc.AudioSource(48000, 1)
    async def flush_audio_source(source: rtc.AudioSource):
        """Clear LiveKit's internal audio buffer."""
        if hasattr(source, "clear_queue"):
            source.clear_queue()
        else:
            # Fallback if clear_queue is not available
            silence = np.zeros(4800, dtype=np.int16)
            frame = rtc.AudioFrame(
                data=silence.tobytes(),
                sample_rate=48000,
                num_channels=1,
                samples_per_channel=4800,
            )
            try:
                await source.capture_frame(frame)
            except Exception:
                pass

    agent_track = rtc.LocalAudioTrack.create_audio_track("agent-mic", agent_source)
    options = rtc.TrackPublishOptions()
    options.source = rtc.TrackSource.SOURCE_MICROPHONE
    await ctx.room.local_participant.publish_track(agent_track, options)
    
    agent_task = None  # Tracks the active response task so we can interrupt it
    agent_speaking_event = asyncio.Event()

    import json
    from datetime import datetime, timezone
    
    session_start = datetime.now(timezone.utc)
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    events_log_path = os.path.join(
        log_dir,
        f"events_{session_start.strftime('%Y%m%d_%H%M%S')}.log"
    )
    session_log_path = os.path.join(
        log_dir,
        f"session_{session_start.strftime('%Y%m%d_%H%M%S')}.log"
    )
    turn_number = 0

    def log_event(event_name: str, turn_id: int = None, response_id: str = None, details: dict = None):
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event_name,
            "turn_id": turn_id,
            "response_id": response_id,
        }
        if details:
            entry.update(details)
        with open(events_log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def log_turn(turn_data: dict):
        with open(session_log_path, "a") as f:
            f.write(json.dumps(turn_data) + "\n")

    async def run_agent_response(user_text: str, turn: dict):
        if agent_task and not agent_task.done() and agent_task != asyncio.current_task():
            return  # newer task already running, don't double-respond
        
        log_event("AGENT_TASK_CREATED", turn_id=turn.get("turn"), details={"task_id": str(id(asyncio.current_task()))})
        
        session.recent_agent_text = ""
        response_id = f"R{turn.get('turn', 0)}"
        if turn.get("turn_type", "speech") == "speech":
            session.add_user_message(user_text)
        messages = session.get_context()
        turn["conversation_turn_count"] = len(messages)
        try:
            turn["llm_input"] = [{"role": msg["role"], "content": msg["content"]} for msg in messages]
        except Exception:
            turn["llm_input"] = [str(msg) for msg in messages]
        
        print("Agent thinking...")
        llm_start = time.time()
        log_event("LLM_STARTED", turn_id=turn.get("turn"), response_id=response_id)
        if engine and engine.get("sess"):
            sess = engine["sess"]
            turn["policy"] = sess.policy_for_turn()
            text_stream = engine["fused"].stream_prose(
                user_text=user_text,
                turn_type=turn.get("turn_type", "speech"),
                policy=sess.policy_for_turn(),
                memory_view=sess.memory_view(),
                threads=sess.thread_summaries(),
                history=[m for m in messages if m.get("role") != "system"][-6:],
                turn_no=int(turn.get("turn", 0)),
                degraded=bool(sess.state.get("degraded_perception")),
                key=os.getenv("GEMINI_API_KEY", ""),
            )
        else:
            text_stream = llm_provider.generate_response_stream(messages)
        
        spoken_text = []
        ttft_logged = False
        async def text_stream_tee():
            nonlocal ttft_logged
            async for chunk in text_stream:
                if not ttft_logged:
                    ttft_s = time.time() - llm_start
                    turn["llm_ttft_s"] = round(ttft_s, 3)
                    print(f"[Metrics] LLM Time to First Token: {ttft_s:.2f}s")
                    ttft_logged = True
                    log_event("LLM_FIRST_TOKEN", turn_id=turn.get("turn"), response_id=response_id)
                print(chunk, end="", flush=True)
                spoken_text.append(chunk)
                session.recent_agent_text += chunk
                yield chunk
            print(f"\n[Metrics] LLM Total Generation Time: {time.time() - llm_start:.2f}s")
            turn["llm_response"] = "".join(spoken_text)
            log_event("LLM_COMPLETED", turn_id=turn.get("turn"), response_id=response_id)
            
        print("Agent speaking...")
        agent_speaking_event.set()
        tts_start = time.time()
        ttfa_logged = False
        tts_audio_start = None
        tts_total_samples = 0
        turn["tts_text"] = ""  # final text spoken to TTS (spoken_text snapshot at end)
        log_event("TTS_STARTED", turn_id=turn.get("turn"), response_id=response_id)
        try:
            audio_stream = tts_provider.synthesize_stream(text_stream_tee())
            async for audio_chunk in audio_stream:
                if tts_audio_start is None:
                    tts_audio_start = time.time()
                tts_total_samples += getattr(audio_chunk.frame, "samples_per_channel", 0)
                if not ttfa_logged:
                    ttfa_s = time.time() - tts_start
                    turn["tts_first_audio_s"] = round(ttfa_s, 3)
                    try:
                        speech_end_epoch = datetime.fromisoformat(turn.get("user_speech_end"))
                        turn["speech_end_to_first_audio_s"] = round(time.time() - speech_end_epoch.timestamp(), 2)
                    except Exception:
                        pass
                    print(f"[Metrics] TTS Time to First Audio: {ttfa_s:.2f}s")
                    ttfa_logged = True
                    log_event("TTS_FIRST_AUDIO", turn_id=turn.get("turn"), response_id=response_id)
                    log_event("PLAYBACK_STARTED", turn_id=turn.get("turn"), response_id=response_id)
                log_event("TTS_AUDIO_CHUNK", turn_id=turn.get("turn"), response_id=response_id)
                if agent_source is not None:
                    await agent_source.capture_frame(audio_chunk.frame)
            
            print(f"[Metrics] TTS Total Synthesis Time: {time.time() - tts_start:.2f}s")
            log_event("TTS_COMPLETED", turn_id=turn.get("turn"), response_id=response_id)
            
            if hasattr(agent_source, "wait_for_playout"):
                await agent_source.wait_for_playout()

            turn["tts_text"] = "".join(spoken_text)[:600]
            turn["tts"] = {
                "provider": getattr(tts_provider, "last_provider", "unknown"),
                "fallback_reason": getattr(tts_provider, "last_fallback_reason", None),
                "audio_duration_s": round(tts_total_samples / 48000, 2) if tts_total_samples else None,
                "playback_duration_s": round(time.time() - tts_audio_start, 2) if tts_audio_start else None,
                "synthesis_wall_s": round(time.time() - tts_start, 2),
            }
            print(f"[TurnEval] turn={turn.get('turn')} lang={turn.get('stt_language')} "
                  f"rel={turn.get('turn_relation')} spoke_because={turn.get('response_trigger_reason')} "
                  f"stt={turn.get('stt_latency_s')}s llm_ttft={turn.get('llm_ttft_s')}s "
                  f"tts_ttfa={turn.get('tts_first_audio_s')}s "
                  f"speech->audio={turn.get('speech_end_to_first_audio_s')}s "
                  f"provider={turn['tts']['provider']} audio={turn['tts']['audio_duration_s']}s")

            print("Agent finished speaking.")
            turn["response_trigger_reason"] = "completed"
            log_event("PLAYBACK_COMPLETED", turn_id=turn.get("turn"), response_id=response_id)
            log_event("AGENT_TASK_COMPLETED", turn_id=turn.get("turn"), details={"task_id": str(id(asyncio.current_task()))})
            
            # Finished naturally without interruption
            session.add_agent_message("".join(spoken_text))
            if engine and engine.get("sess"):
                try:
                    tr = {"turn": turn.get("turn"),
                            "turn_type": turn.get("turn_type", "speech"),
                            "acoustic": turn.get("acoustic"),
                            "response_completed": True,
                            "interrupted": False,
                           "policy_derived": {"mode": (turn.get("policy") or {}).get("mode")},
                           "last_move": "response_completed"}
                    if engine["fused"].head is None:
                        turn["head_raw_snippet"] = engine["fused"].meta.get("head_raw_snippet", "")
                        turn["head_fail_class"] = engine["fused"].meta.get("head_fail_class", "unknown")
                        print(f"[StateEngine] PARSE-FAIL turn {turn.get('turn')} class={turn['head_fail_class']} raw={turn['head_raw_snippet'][:240]!r}")
                    turn["llm_context"] = engine["fused"].meta.get("context")
                    policy = engine["sess"].apply_turn(tr, engine["fused"].head)
                    engine["policy"] = policy
                    turn["policy_next"] = policy
                    if engine["fused"].meta.get("head_raw_snippet"):
                        print(f"[StateEngine] PARSE-FAIL raw head: {engine['fused'].meta['head_raw_snippet']}")
                except Exception as e:
                    print(f"[StateEngine] apply failed: {type(e).__name__}: {e}")
            
        except asyncio.CancelledError:
            print("\n[Agent was interrupted]")
            turn["interrupted"] = True
            turn["tts_text"] = "".join(spoken_text)[:600]
            if engine and engine.get("fused"):
                turn["prompt_version"] = engine["fused"].meta.get("prompt_version")
                turn["system_sha1"] = engine["fused"].meta.get("system_sha1")
                turn["llm_context"] = engine["fused"].meta.get("context")
            turn["tts"] = {
                "provider": getattr(tts_provider, "last_provider", "unknown"),
                "fallback_reason": getattr(tts_provider, "last_fallback_reason", None),
                "audio_duration_s": round(tts_total_samples / 48000, 2) if tts_total_samples else None,
                "playback_duration_s": round(time.time() - tts_audio_start, 2) if tts_audio_start else None,
                "interrupted_at_ms": round((time.time() - tts_audio_start) * 1000) if tts_audio_start else None,
            }
            print("[TurnTrace] " + json.dumps({
                "turn_id": turn.get("turn"), "endpoint": turn.get("endpoint"),
                "stt": {"text": turn.get("stt_transcript"), "logprob": turn.get("stt_avg_logprob")},
                "perception": bool(engine["fused"].head) if engine else None,
                "prompt_version": turn.get("prompt_version"),
                "response": (turn.get("llm_response") or "")[:120],
                "tts": turn.get("tts"), "tts_text": (turn.get("tts_text") or "")[:100],
                "interrupted": True,
            }, ensure_ascii=False, default=str)[:1200])
            print("[TurnTrace] " + json.dumps({
                "turn_id": turn.get("turn"), "endpoint": turn.get("endpoint"),
                "stt": {"text": turn.get("stt_transcript"), "logprob": turn.get("stt_avg_logprob")},
                "perception": bool(engine["fused"].head) if engine else None,
                "prompt_version": turn.get("prompt_version"),
                "response": (turn.get("llm_response") or "")[:120],
                "tts": turn.get("tts"), "tts_text": (turn.get("tts_text") or "")[:100],
                "interrupted": True,
            }, ensure_ascii=False, default=str)[:1200])
            print(f"[TurnEval] turn={turn.get('turn')} INTERRUPTED provider={turn['tts']['provider']} "
                  f"audio={turn['tts']['audio_duration_s']}s "
                  f"at_ms={turn['tts'].get('interrupted_at_ms')}")
            log_event("AGENT_CANCELLED_EXCEPTION", turn_id=turn.get("turn"), response_id=response_id)
            log_event("AGENT_TASK_CANCELLED", turn_id=turn.get("turn"), details={"task_id": str(id(asyncio.current_task()))})
            await flush_audio_source(agent_source)
            truncated_message = "".join(spoken_text).strip()
            if truncated_message and ttfa_logged:
                session.add_agent_message(truncated_message, interrupted=True)
            if engine and engine.get("sess"):
                try:
                    tr = {"turn": turn.get("turn"),
                           "response_completed": False,
                           "interrupted": True,
                           "interrupted_agent_response": {"response_id": response_id,
                                                           "spoken_text": truncated_message,
                                                           "completed": False}}
                    engine["sess"].apply_turn(tr, engine["fused"].head)
                except Exception as e:
                    print(f"[StateEngine] apply(interrupted) failed: {type(e).__name__}: {e}")
            raise
        finally:
            agent_speaking_event.clear()
            nonlocal agent_audio_ended_at
            agent_audio_ended_at = time.monotonic()
            if engine:
                idle_state["last_activity"] = time.monotonic()

    async def process_user_audio(track: rtc.RemoteAudioTrack, participant=None):
        nonlocal agent_task
        
        audio_stream = rtc.AudioStream(track)
        resampler = None
        is_speaking = False
        speech_buffer = []
        pre_roll_buffer = []
        speech_start_ts = None
        current_sample_rate = None

        print("Listening for user speech...")

        # C5: bind memory ownership to the participant identity (device-scoped UUID)
        if engine and engine.get("sess") is None:
            try:
                from agent.session_state import SessionState
                owner = (getattr(participant, "identity", None) or "ephemeral-unknown")
                engine["sess"] = SessionState(owner, engine["store"])
                print(f"[StateEngine] session bound to owner={owner}")
            except Exception as e:
                print(f"[StateEngine] session init failed: {type(e).__name__}: {e}")

        print(f"AudioStream started, track muted: {track.muted}")
        frame_count = 0
        current_sample_rate = None
        async for event in audio_stream:
            frame = event.frame
            frame_count += 1
            
            # Resampler always runs — keeps state warm
            if current_sample_rate != frame.sample_rate:
                current_sample_rate = frame.sample_rate
                resampler = rtc.AudioResampler(
                    input_rate=frame.sample_rate, output_rate=16000
                )
                print(f"Resampler created: {frame.sample_rate}Hz -> 16000Hz")
            resampled_frames = resampler.push(frame)

            for r_frame in resampled_frames:
                audio_np = np.frombuffer(r_frame.data, dtype=np.int16)
                max_amp = np.max(np.abs(audio_np)) if len(audio_np) > 0 else 0
                if frame_count <= 20:
                    print(f"Frame {frame_count}: samples={len(audio_np)} max_amp={max_amp}")
                elif max_amp > 50:
                    print(f"Audio level spike: {np.max(np.abs(audio_np))}")

                vad_start = time.time()
                # VAD always runs — needed to detect interruptions
                vad_events = vad_provider.process_audio(audio_np)
                vad_time = time.time() - vad_start
                if frame_count % 50 == 0:
                    print(f"[Metrics] VAD Processing Time (per frame): {vad_time * 1000:.2f}ms")

                # Maintain 300ms pre-roll buffer (4800 samples at 16kHz)
                pre_roll_buffer.extend(audio_np.tolist())
                if len(pre_roll_buffer) > 4800:
                    pre_roll_buffer = pre_roll_buffer[-4800:]

                # ALWAYS buffer speech if speaking (we evaluate echo later)
                if is_speaking:
                    speech_buffer.append(audio_np)

                for vad_event in vad_events:
                    if vad_event == VADEvent.SPEECH_STARTED:
                        is_speaking = True
                        if engine:
                            idle_state["last_activity"] = time.monotonic()
                            idle_state["line_sent"] = False
                        # Endpointing evidence: did speech resume right after an endpoint?
                        wait_duration_ms = None
                        if engine and engine.get("wait_started_at"):
                            wait_duration_ms = round((time.monotonic() - engine["wait_started_at"]) * 1000, 1)
                            engine["wait_started_at"] = None
                        premature_resume = None
                        resume_gap = getattr(vad_provider, "last_resume_gap_ms", None)
                        vad_provider.last_resume_gap_ms = None
                        if resume_gap_ms_ := resume_gap:
                            premature_resume = {
                                "resumed_after_endpoint_ms": resume_gap_ms_,
                                "agent_tts_active_at_resume": agent_speaking_event.is_set(),
                                "previous_endpoint": dict(getattr(vad_provider, "last_endpoint", {}) or {}),
                            }
                            print(f"[Endpoint] PREMATURE resume +{resume_gap_ms_}ms "
                                  f"(tts_active={premature_resume['agent_tts_active_at_resume']}, "
                                  f"next penalty={vad_provider.endpoint_penalty_ms}ms)")
                        if wait_duration_ms is not None:
                            print(f"[TurnController] continuation arrived after {wait_duration_ms}ms "
                                  f"of waiting — previous pause was NOT a turn end")
                        
                        # Prepend the pre-roll buffer to capture the speech onset
                        audio_pre_roll = np.array(pre_roll_buffer, dtype=np.int16)
                        speech_buffer = [audio_pre_roll, audio_np] if len(audio_pre_roll) > 0 else [audio_np]
                        
                        speech_start_ts = datetime.now(timezone.utc).isoformat()
                        
                        if agent_speaking_event.is_set():
                            print("BARGE_IN_CANDIDATE: Agent is speaking. Buffering to evaluate.")
                            log_event("BARGE_IN_CANDIDATE", turn_id=turn_number + 1, details={"agent_speaking": True})
                        else:
                            log_event("USER_SPEECH_STARTED", turn_id=turn_number + 1)
                            print(f"User started speaking... (pre-roll: {len(audio_pre_roll)} samples)")
                    elif vad_event == VADEvent.SPEECH_ENDED:
                        is_speaking = False
                        endpoint_info = dict(getattr(vad_provider, "last_endpoint", {}) or {})
                        print(f"[Endpoint] turn-complete decision: {endpoint_info}")
                        print("User/Echo stopped. Transcribing to evaluate...")
                        log_event("USER_SPEECH_ENDED", turn_id=turn_number + 1)
                        if not speech_buffer:
                            continue
                        full_audio = np.concatenate(speech_buffer)
                        speech_duration_ms = (len(full_audio) / 16000) * 1000
                        
                        # TRUE audio metrics calculation
                        peak_amplitude = int(np.max(np.abs(full_audio)))
                        rms_amplitude = float(np.sqrt(np.mean(np.square(full_audio.astype(np.float64)))))
                        mean_absolute_amplitude = float(np.mean(np.abs(full_audio)))
                        
                        print(f"Audio Segment Metrics: duration={speech_duration_ms:.1f}ms, "
                              f"peak={peak_amplitude}, rms={rms_amplitude:.1f}, mean_abs={mean_absolute_amplitude:.1f}")
                        
                        MIN_RMS_AMPLITUDE = 2000
                        MIN_PEAK_AMPLITUDE = 3500
                        
                        if rms_amplitude < MIN_RMS_AMPLITUDE and peak_amplitude < MIN_PEAK_AMPLITUDE:
                            print("AUDIO_GATE_REJECTED: Signal below speech energy threshold.")
                            log_event("AUDIO_GATE_REJECTED", turn_id=turn_number + 1, details={
                                "rms": rms_amplitude, "peak": peak_amplitude, "duration": speech_duration_ms
                            })
                            speech_buffer = []
                            continue
                            
                        print("AUDIO_GATE_PASSED: Proceeding to STT.")
                        
                        float_audio = full_audio.astype(np.float32) / 32768.0
                        speech_end_ts = datetime.now(timezone.utc).isoformat()
                        
                        async def transcribe_and_respond(audio_data, duration_ms, speech_start_ts, 
                                                          speech_end_ts, agent_was_speaking_at_detection: bool,
                                                          ms_since_agent_audio_end: float = None,
                                                          prev_task = None,
                                                          endpoint_info = None,
                                                          premature_resume = None):
                            nonlocal turn_number
                            turn_number += 1
                            turn = {
                                "turn": turn_number,
                                "endpoint": endpoint_info,
                                "premature_resume": premature_resume,
                                "acoustic": {"duration_ms": round(speech_duration_ms, 1),
                                              "rms": round(rms_amplitude, 1),
                                              "peak": int(peak_amplitude)},
                                "user_speech_start": speech_start_ts,
                                "user_speech_end": speech_end_ts,
                                "agent_was_speaking": agent_was_speaking_at_detection,
                                "ms_since_agent_audio_end": ms_since_agent_audio_end,
                                "stt_transcript": None,
                                "stt_language": None,
                                "stt_no_speech_prob": None,
                                "stt_avg_logprob": None,
                                "stt_compression_ratio": None,
                                "stt_latency_s": None,
                                "stt_valid": None,
                                "stt_rejection_reason": None,
                                "response_trigger_reason": None,
                                "llm_input": None,
                                "llm_response": None,
                                "llm_ttft_s": None,
                                "tts_first_audio_s": None,
                                "interrupted": False,
                                "interruption_timestamp": None,
                            }
                            try:
                                log_event("STT_STARTED", turn_id=turn.get("turn"))
                                stt_start = time.time()
                                transcript = await asyncio.to_thread(stt_provider.transcribe, audio_data)
                                turn["stt_latency_s"] = round(time.time() - stt_start, 3)
                                log_event("STT_COMPLETED", turn_id=turn.get("turn"))

                                if not transcript:
                                    print("STT returned no text")
                                    return

                                turn["stt_transcript"] = transcript.text
                                turn["stt_language"] = transcript.language
                                turn["stt_no_speech_prob"] = transcript.no_speech_prob
                                turn["stt_avg_logprob"] = transcript.avg_logprob
                                turn["stt_compression_ratio"] = transcript.compression_ratio

                                echo_text = devanagari_to_roman(transcript.text)
                                turn["turn_relation"] = classify_turn_relation(transcript.text)
                                is_echo_detected, similarity = is_echo(echo_text, session.recent_agent_text)
                                if is_echo_detected:
                                    print(f"ECHO_DETECTED: similarity={similarity:.2f}")
                                    log_event("AGENT_ECHO_IGNORED", turn_id=turn.get("turn"), details={"transcript": transcript.text, "similarity": similarity, "language": transcript.language})
                                    return
                                    
                                is_valid, rejection_reason = is_real_user_turn(transcript, duration_ms)
                                turn["stt_valid"] = is_valid
                                turn["stt_rejection_reason"] = rejection_reason

                                print(f"[STT] '{transcript.text}' | valid={is_valid} | "
                                      f"lang={transcript.language} | "
                                      f"no_speech={transcript.no_speech_prob} | "
                                      f"logprob={transcript.avg_logprob}")

                                if agent_was_speaking_at_detection:
                                    log_event("BARGE_IN_EVALUATED", turn_id=turn.get("turn"), details={
                                        "transcript": transcript.text,
                                        "valid": is_valid,
                                        "reason": rejection_reason,
                                        "duration_ms": duration_ms,
                                        "decision": "INTERRUPT_AGENT" if is_valid else "IGNORE_ECHO"
                                    })

                                if not is_valid:
                                    print(f"[STT Rejected] reason={rejection_reason}")
                                    # C7/D7: real speech detected (energy gate passed) but no
                                    # valid transcript -> acoustic-only presence turn. Never on
                                    # echo (agent's own voice) or while the agent is speaking.
                                    if (engine and engine.get("sess")
                                            and not agent_was_speaking_at_detection):
                                        if not transcript.text.strip():
                                            turn["turn_type"] = "acoustic_only"
                                            turn["response_trigger_reason"] = "acoustic_only_presence"
                                            await run_agent_response(transcript.text, turn)
                                            return
                                        # P0: garbled-but-present text (poor confidence or
                                        # Whisper repetition loop) -> deterministic
                                        # clarification, never invention
                                        if rejection_reason in ("high_no_speech_prob",
                                                                 "low_avg_logprob",
                                                                 "catastrophic_low_confidence") \
                                                or is_repetition_loop(transcript.text):
                                            turn["turn_type"] = "unclear_speech"
                                            turn["response_trigger_reason"] = "unclear_stt_clarify"
                                            await run_agent_response(transcript.text, turn)
                                            return
                                    return
                                    
                                if prev_task and not prev_task.done():
                                    print("AGENT_INTERRUPTED_BY_USER")
                                    log_event("AGENT_TASK_CANCEL_REQUESTED", turn_id=turn.get("turn"), details={"task_id": str(id(asyncio.current_task())), "previous_task_id": str(id(prev_task))})
                                    prev_task.cancel()

                                if not (speech_start_ts and speech_end_ts and transcript.text.strip() and turn_number):
                                    print("[Turn Gate Rejected] Missing valid turn lifecycle state")
                                    return

                                # ---- Conversation Turn Controller (locked brief):
                                # a VAD speech-end is only a POSSIBLE handoff.
                                prev_wait = bool(engine.get("last_turn_wait")) if engine else False
                                action, ctrl_reason = turn_controller_decide(transcript.text, prev_wait)
                                turn["continuation_detected"] = action == "suppress"
                                turn["turn_end_decision"] = action
                                if action == "suppress":
                                    turn["suppression_reason"] = ctrl_reason
                                    turn["response_suppressed"] = True
                                    turn["wait_started_at"] = time.monotonic()
                                    if engine:
                                        engine["last_turn_wait"] = True
                                    session.add_user_message(transcript.text)  # context preserved
                                    print(f"[TurnController] WAIT ({ctrl_reason}) — silent, "
                                          f"context kept: {transcript.text[:80]!r}")
                                    return
                                if engine:
                                    engine["last_turn_wait"] = False
                                turn["response_trigger_reason"] = "user_speech_ended"
                                await run_agent_response(transcript.text, turn)

                            except asyncio.CancelledError:
                                turn["interrupted"] = True
                                turn["interruption_timestamp"] = datetime.now(timezone.utc).isoformat()
                                print("[STT cancelled]")
                            except Exception as e:
                                print(f"Pipeline Error: {e}")
                            finally:
                                log_turn(turn)
                                
                        agent_was_speaking = agent_speaking_event.is_set()
                        ms_since_end = None
                        if agent_audio_ended_at is not None:
                            ms_since_end = round(
                                (time.monotonic() - agent_audio_ended_at) * 1000, 1
                            )
                        previous_task = agent_task
                        agent_task = asyncio.create_task(
                            transcribe_and_respond(
                                float_audio, speech_duration_ms, speech_start_ts, speech_end_ts,
                                agent_was_speaking, ms_since_end, prev_task=previous_task,
                                endpoint_info=endpoint_info, premature_resume=premature_resume
                            )
                        )
                        speech_buffer = []

    # ---- C7/D8 idle watcher: 45s without speech or agent audio -> one open-door line ----
    idle_state = {"last_activity": time.monotonic(), "line_sent": False, "seq": 1000}

    async def idle_watcher():
        while True:
            await asyncio.sleep(5)
            try:
                if not (engine and engine.get("sess")):
                    continue
                if agent_speaking_event.is_set():
                    idle_state["last_activity"] = time.monotonic()
                    continue
                idle_for = time.monotonic() - idle_state["last_activity"]
                if idle_for >= 45 and not idle_state["line_sent"]:
                    idle_state["line_sent"] = True
                    idle_state["seq"] += 1
                    idle_turn = {"turn": idle_state["seq"], "turn_type": "idle",
                                  "response_trigger_reason": "idle_45s"}
                    print("[StateEngine] idle 45s - open-door turn")
                    await run_agent_response("", idle_turn)
            except Exception as e:
                print(f"[StateEngine] idle watcher error: {type(e).__name__}: {e}")

    if engine:
        asyncio.create_task(idle_watcher())

    # Phase 5: session-end memory commit (best effort; SDK hook may vary)
    async def _commit_session_memory():
        if engine and engine.get("sess"):
            try:
                engine["sess"].end_session(keep_pending=True)
                print("[StateEngine] memory committed at session end")
            except Exception as e:
                print(f"[StateEngine] memory commit failed: {type(e).__name__}: {e}")
    if hasattr(ctx, "add_shutdown_callback"):
        ctx.add_shutdown_callback(_commit_session_memory)
    else:
        print("[StateEngine] no shutdown hook available - pending memory not committed")

    # Handle tracks from participants that join AFTER the agent
    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            print(f"New audio track subscribed from {participant.identity}")
            asyncio.create_task(process_user_audio(track, participant))

    # Handle tracks from participants ALREADY in the room when agent connects
    for participant in ctx.room.remote_participants.values():
        for publication in participant.track_publications.values():
            if publication.track and publication.track.kind == rtc.TrackKind.KIND_AUDIO:
                print(f"Found existing audio track from {participant.identity}")
                asyncio.create_task(process_user_audio(publication.track, participant))

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
        )
    )

```

## `agent/session.py`  (55 lines)

```python
class Message:
    def __init__(self, role: str, content: str, interrupted: bool = False):
        self.role = role  # "user" or "assistant"
        self.content = content
        self.interrupted = interrupted

    def to_dict(self):
        return {
            "role": self.role,
            "content": self.content,
            "interrupted": self.interrupted
        }

class ConversationSession:
    def __init__(self):
        # Ephemeral memory — resets completely on every new session instance.
        self.history: list[Message] = []
        self.recent_agent_text = ""
        self.system_prompt = (
            "You are Aiva, a sharp and warm voice assistant for natural phone-style "
            "conversations with Indian users who mix Hindi and English freely.\n\n"
            "RULES — follow these strictly:\n"
            "1. Maximum 2 sentences per response. You are speaking aloud — brevity is everything.\n"
            "2. No bullet points, lists, markdown, or special characters ever.\n"
            "3. Reply in Romanized Hindi/Hinglish if the user speaks Hindi — never Devanagari script.\n"
            "4. If you genuinely cannot answer a question, say: "
            "'Mujhe is baare mein pata nahi, kuch aur poochh sakte ho?' "
            "— never make up facts.\n"
            "5. Answer factual questions directly and precisely. No padding.\n"
            "6. Never mention you are an AI unless directly asked.\n"
            "7. Match the user's energy — casual if they're casual, precise if they ask something specific."
        )

    def add_user_message(self, text: str):
        """Append transcribed user speech to the ephemeral history."""
        self.history.append(Message(role="user", content=text))

    def add_agent_message(self, text: str, interrupted: bool = False):
        """Append the generated agent response to the ephemeral history."""
        if interrupted:
            text = text.strip() + " [interrupted before finishing]"
        self.history.append(Message(role="assistant", content=text, interrupted=interrupted))

    def get_context(self) -> list[dict]:
        """
        Returns the conversation history in a generic dict format.
        The LLM provider will map this generic state to its specific API schema.
        """
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend([msg.to_dict() for msg in self.history])
        return messages

    def clear(self):
        """Reset the conversation state entirely."""
        self.history.clear()

```

## `agent/session_state.py`  (98 lines)

```python
"""aiva.state — per-session state store (Phase 5, 5.3).

Wraps the committed AivaSessionState (locked schemas) around the production
updater. Persists one JSONL line per turn (mirrors the logs/ pattern) and
bridges memory commits to agent.memory_store (C5 owner keying, D3 commit
rules, session-end evaluation).

No interpretation logic: consumes structured heads only (locked boundary).
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

from agent.memory_store import MemoryStore
from agent.state_updater import default_state, derive_policy, update


class SessionState:
    def __init__(self, owner_id: str, store: MemoryStore, log_dir: str = "logs"):
        self.owner_id = owner_id
        self.store = store
        self.state = default_state()
        self.policy = derive_policy(self.state, {"turn": 0})
        self.last_applied_turn = 0
        os.makedirs(log_dir, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.log_path = os.path.join(log_dir, f"state_{ts}_{owner_id[:8]}.jsonl")
        with open(self.log_path, "a") as f:
            f.write(json.dumps({"event": "SESSION_START", "owner_id": owner_id,
                                 "memory_view": self.memory_view()}) + "\n")
        self._pending_log = []

    # ---- context builders for the LLM call (C6: bounded + structured) ----
    def memory_view(self) -> list:
        return self.store.view(self.owner_id)

    def thread_summaries(self) -> list:
        out = []
        for t in self.state.get("threads", []):
            if t.get("status") in ("active", "paused"):
                out.append(f"{t.get('gist')} ({t.get('status')})")
        return out

    def policy_for_turn(self) -> dict:
        return dict(self.policy)

    def history_window(self, history: list, window: int = 6) -> list:
        """D6: bounded recent-turn window from the existing ConversationSession history."""
        return [{"role": m.role, "content": m.content}
                for m in history[-window:]]

    # ---- updater entry (deterministic; consumes structured head only) ----
    def apply_turn(self, turn_record: dict, head: dict | None) -> dict:
        turn_record = dict(turn_record)
        turn_record.setdefault("owner_id", self.owner_id)
        # P2 async order guard: a cancelled older task must never mutate state
        # out of order. Stale turns are logged, not applied.
        tno = int(turn_record.get("turn") or 0)
        if tno and tno <= self.last_applied_turn:
            with open(self.log_path, "a") as f:
                f.write(json.dumps({"event": "STALE_TURN_SKIPPED", "turn": tno}) + "\n")
            return self.policy
        self.last_applied_turn = tno
        self.state, policy, log = update(self.state, turn_record, head)
        entry = {"turn": turn_record.get("turn"), "head": head,
                  "head_raw_snippet": turn_record.get("head_raw_snippet"),
                  "head_fail_class": turn_record.get("head_fail_class"),
                  "policy": policy, "log": log,
                  "state_digest": {"emotion_primary": self.state["emotion"]["primary"],
                                    "intensity": self.state["emotion"]["intensity"]["ordinal"],
                                    "trajectory": self.state["emotion"]["trajectory"],
                                    "risk_level": self.state["safety"]["risk_level"],
                                    "mode": self.state["mode"]["current"],
                                    "phase": self.state["conversation"]["phase"]},
                  "ts": datetime.now(timezone.utc).isoformat()}
        with open(self.log_path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        self.policy = policy
        self._commit_explicit_memory(head)
        return policy

    def _commit_explicit_memory(self, head: dict | None) -> None:
        for mc in (head or {}).get("memory_candidates", []) or []:
            if mc.get("criterion") == "explicit":
                self.store.commit(self.owner_id, mc, immediate=True)

    # ---- session lifecycle ----
    def end_session(self, keep_pending: bool = True) -> None:
        for c in self.state.get("memory", {}).get("write_candidates", []):
            self.store.commit(self.owner_id, {"type": c.get("type", "semantic"),
                                               "content": c.get("content", ""),
                                               "criterion": c.get("criterion", "salient")})
        self.store.record_session(self.owner_id)
        with open(self.log_path, "a") as f:
            f.write(json.dumps({"event": "SESSION_END"}) + "\n")

```

## `agent/state_updater.py`  (580 lines)

```python
#!/usr/bin/env python3
"""aiva.updater/v1 — production deterministic state updater (Phase 5, 5.2).

Ported phase-identical from the Phase 4 reference implementation so the
Batch-2 replay harness tests the production module directly. Implements the
locked spec (docs/PHASE3_CONTRACTS.md C1/C1.1/C6/C7 + amendment A-U7).

Deterministic: pure functions, no clock, no LLM calls, no natural language
beyond structured fields (locked interpretation boundary).
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Frozen parameter table (C6 — single source of truth)
# ---------------------------------------------------------------------------
PARAMS = {
    "ring": 5,
    "decay_turns": 3,
    "decay_conf_factor": 0.6,
    "mode_hysteresis": 2,
    "thread_close_inactive": 10,
    "safety_deescalate_turns": 3,
    "idle_threshold_s": 45,           # U4
    "max_sentences": 2,
    "max_questions": 1,
    "max_consecutive_question_turns": 2,
    "caps": {"transcript_only": 0.5, "with_acoustic": 0.7, "acoustic_only": 0.3, "conflict": 0.4},
    "weights": {"user_correction": 0.95, "transcript": 0.4, "history": 0.2, "acoustic": 0.2},
    "corr_confidence": 0.95,          # A-U7
    "emotion_label_threshold": 0.7,   # internal parameter — name an emotion only at >= this confidence (P6: calibration pending)
}

TAXONOMY = ["anger_frustration", "sadness", "anxiety", "overwhelm",
            "loneliness_hurt", "guilt_shame", "relief", "neutral_unclear"]
RISK_LEVELS = ["none", "low", "elevated_distress", "high_risk"]

# C1.1 ordered first-match normalization table (substring, case-insensitive)
NORM_TABLE = [
    (("overwhelm", "exhaust", "burnout"), "overwhelm"),
    (("anger", "frustrat", "irritat", "annoy"), "anger_frustration"),
    (("anx", "worry", "nervous", "panic", "stress"), "anxiety"),
    (("lonely", "alone", "abandon"), "loneliness_hurt"),
    (("hurt",), "loneliness_hurt"),
    (("guilt", "shame", "ashamed"), "guilt_shame"),
    (("sad", "down", "cry", "udaas", "dukhi"), "sadness"),
    (("relief", "relieved"), "relief"),
    (("neutral", "unclear", "unsure", "calm"), "neutral_unclear"),
]

NEGATIVE_LABELS = {"anger_frustration", "sadness", "anxiety", "overwhelm",
                   "loneliness_hurt", "guilt_shame"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _log(log: list, code: str) -> None:
    log.append(code)


def _dig(d: dict, path: str):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _set(d: dict, path: str, value) -> None:
    parts = path.split(".")
    cur = d
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def normalize_label(raw: str, conf: float, log: list) -> tuple[str, float, bool]:
    """C1.1. Returns (canonical, confidence, unknown). Applies -0.10 on table match."""
    low = (raw or "").lower()
    if raw in TAXONOMY:
        return raw, conf, False
    for patterns, canonical in NORM_TABLE:
        if any(p in low for p in patterns):
            _log(log, f"NORM-LABEL:{raw}→{canonical}")
            return canonical, max(0.0, round(conf - 0.10, 3)), False
    _log(log, f"NORM-UNKNOWN:{raw}")
    return "neutral_unclear", min(conf, 0.3), True


def default_state() -> dict:
    return {
        "mode": {"current": "VENT", "since_turn": 0, "pending_target": None, "pending_count": 0},
        "emotion": {"primary": "neutral_unclear", "valence": "neutral",
                    "intensity": {"ordinal": 2}, "confidence": 0.0,
                    "trajectory": "stable", "recent_estimates": [],
                    "unverified_turns": 0},
        "safety": {"risk_level": "none", "override_active": False,
                   "safe_streak": 0,
                   "categories": {"self_harm": {"present": False},
                                   "harm_to_others": {"present": False},
                                   "other_flagged": {"present": False}}},
        "threads": [],
        "active_thread": None,
        "memory": {"episodic": [], "semantic": [], "relationship": [],
                    "preferences": [], "write_candidates": []},
        "conversation": {"phase": "opening", "turn_count": 0,
                          "agent_behavior_ledger": {"questions_this_conversation": 0,
                                                     "questions_last_2_turns": 0,
                                                     "advice_given": 0,
                                                     "last_move": None},
                          "mode_history": ["VENT"]},
        "degraded_perception": False,
        "parse_fail_streak": 0,
        "idle": {"line_used": False},
    }


def merge_state(base: dict | None) -> dict:
    st = default_state()
    if base:
        for k, v in base.items():
            if isinstance(v, dict) and isinstance(st.get(k), dict):
                for kk, vv in v.items():
                    st[k][kk] = copy.deepcopy(vv)
            else:
                st[k] = copy.deepcopy(v)
    return st


# ---------------------------------------------------------------------------
# Main entry: update(prev_state, turn_record, head, events) -> (state, policy, log)
# ---------------------------------------------------------------------------
def update(prev_state: dict | None, turn_record: dict, head: dict | None,
           events: dict | None = None) -> tuple[dict, dict, list]:
    state = merge_state(prev_state)
    log: list = []
    tr = turn_record or {}
    head = copy.deepcopy(head) if head else None
    turn = int(tr.get("turn", state["conversation"]["turn_count"] + 1))
    state["conversation"]["turn_count"] = turn
    events = events or {}

    turn_type = tr.get("turn_type", "speech")

    # ---- Degradation turns (C7 D7/D8): no head, deterministic responses ----
    if turn_type == "unclear_speech":
        # P0: garbage STT -> clarification; never invent entities from unreliable text
        _log(log, "TURN-UNCLEAR-SPEECH")
        policy = _derive_policy(state, turn_record, head=None, degradation="clarify")
        return state, policy, log

    if turn_type == "acoustic_only":
        _log(log, "TURN-ACOUSTIC-ONLY")
        e = state["emotion"]
        e["confidence"] = min(e.get("confidence", 0.0), PARAMS["caps"]["acoustic_only"])
        _log(log, "CAP-CHANNEL")
        policy = _derive_policy(state, turn_record, head=None, degradation="D7")
        return state, policy, log

    if turn_type == "idle":
        if not state["idle"]["line_used"]:
            state["idle"]["line_used"] = True
            _log(log, "IDLE-OPEN-DOOR")
            policy = _derive_policy(state, turn_record, head=None, degradation="D8")
        else:
            _log(log, "IDLE-SUPPRESSED")
            policy = _derive_policy(state, turn_record, head=None, degradation="D8")
            policy["response_suppressed"] = True
        return state, policy, log

    # ---- D1/D2/D9: missing or malformed head ----
    if head is None or tr.get("head_parse") == "failed":
        state["parse_fail_streak"] = int(state.get("parse_fail_streak", 0)) + 1
        _log(log, "PARSE-FAIL")
        if state["parse_fail_streak"] >= 2 and not state["degraded_perception"]:
            state["degraded_perception"] = True
            _log(log, "DEGRADED-PERCEPTION-ENTER")
        policy = _derive_policy(state, turn_record, head=None)
        return state, policy, log

    # ---- C1.1 normalization (never an interpretation step) ----
    emo = head.get("emotion", {}) or {}
    raw_primary = emo.get("primary", "neutral_unclear")
    raw_conf = float(emo.get("confidence", 0.0) or 0.0)
    primary, conf, unknown = normalize_label(raw_primary, raw_conf, log)
    valence = emo.get("valence", "neutral")
    if primary in NEGATIVE_LABELS and valence == "positive" and primary != "relief":
        valence = "negative"
        _log(log, "NORM-VALENCE")

    ordinal = int((emo.get("intensity", {}) or {}).get("ordinal", 2) or 2)

    # ---- Safety normalization first (D3: invalid enums never map to none) ----
    saf = head.get("safety", {}) or {}
    risk = saf.get("risk_level", "none")
    if risk not in RISK_LEVELS:
        risk = "low"
        saf["other_flagged"] = True
        _log(log, f"SAFE-INVALID:{saf.get('risk_level')}")
    self_harm = bool(saf.get("self_harm", False))
    harm_others = bool(saf.get("harm_to_others", False))
    other_flag = bool(saf.get("other_flagged", False))
    safety_conf = float(saf.get("confidence", 0.0) or 0.0)
    if self_harm or harm_others:
        # T4.1 taxonomy rule 1 (locked): any self-harm / harm-to-others signal
        # -> high_risk regardless of the head's risk_level or confidence.
        if risk != "high_risk":
            _log(log, "SAFE-RULE1-ESCALATE")
        risk = "high_risk"

    # ---- Correction (A-U7): after normalize/validate, pins confidence ----
    corr = head.get("correction") or {}
    if isinstance(corr, dict) and corr.get("present") is False:
        # A-U7: semantically absent — no log noise
        corr = {"present": False, "about": "emotion"}
    elif not isinstance(corr, dict) or not isinstance(corr.get("present"), bool) \
            or corr.get("about") not in ("emotion", "thread", "fact", "preference"):
        if corr:
            _log(log, "CORR-INVALID")
        corr = {"present": False, "about": "emotion"}

    # ---- Evidence + confidence caps (C1.2) ----
    acoustic_available = bool(tr.get("acoustic_available", False)) or turn_type == "speech" and bool(tr.get("acoustic"))
    cap = PARAMS["caps"]["with_acoustic"] if acoustic_available else PARAMS["caps"]["transcript_only"]
    if unknown:
        conf = min(conf, PARAMS["caps"]["acoustic_only"] + 0.0)  # NORM-UNKNOWN cap 0.3 (set in normalize)
        conf = min(conf, 0.3)
    elif conf > cap:
        conf = cap
        _log(log, "CAP-CHANNEL")

    # ---- Step 2: correction override (A-U7) ----
    correction_applied = False
    if corr.get("present"):
        about = corr["about"]
        if about == "emotion":
            if unknown:
                _log(log, "CORR-UNKNOWN-KEPT")   # cannot rescue an invalid label (D3 spirit)
            else:
                conf = PARAMS["corr_confidence"]
                correction_applied = True
                _log(log, "CORR-OVERRIDE")
        elif about in ("fact", "preference"):
            for mc in head.get("memory_candidates", []) or []:
                mc["criterion"] = "corrective"
            _log(log, f"CORR-NOTE:{about}")
        elif about == "thread":
            _log(log, "CORR-NOTE:thread")

    # ---- Step 5: emotion commit + ring + trajectory ----
    # Carry rule (v1.1 section 4.2): a weak neutral_unclear sensing never overwrites a
    # specific committed estimate — it increments the unverified counter; 3 consecutive
    # unverified turns -> DECAY drift to neutral_unclear. Corrections never carry
    # (A-U7: unknown labels degrade immediately, CORR-UNKNOWN-KEPT).
    prev_committed_primary = state["emotion"].get("primary")
    prev_committed_valence = state["emotion"].get("valence", "neutral")
    correction_turn = bool(corr.get("present"))
    e = state["emotion"]
    carry = (primary == "neutral_unclear"
             and prev_committed_primary not in (None, "neutral_unclear")
             and not correction_turn)
    e.setdefault("recent_estimates", []).append({"turn": turn, "primary": primary, "ordinal": ordinal})
    e["recent_estimates"] = e["recent_estimates"][-PARAMS["ring"]:]
    if carry:
        e["primary"] = prev_committed_primary
        e["valence"] = prev_committed_valence
        e["intensity"] = {"ordinal": ordinal}
        e["confidence"] = conf
        e["unverified_turns"] = int(e.get("unverified_turns", 0)) + 1
        _log(log, "EMOTION-CARRY")
    else:
        e["primary"], e["valence"], e["confidence"] = primary, valence, conf
        e["intensity"] = {"ordinal": ordinal}
        if primary != prev_committed_primary or correction_applied or primary == prev_committed_primary:
            e["unverified_turns"] = 0

    ords = [r["ordinal"] for r in e["recent_estimates"]]
    if len(ords) >= 3:
        net = ords[-1] - ords[0]
        ups = sum(1 for a, b in zip(ords, ords[1:]) if b > a)
        downs = sum(1 for a, b in zip(ords, ords[1:]) if b < a)
        if net >= 2:
            traj = "rising"; _log(log, "TRAJ-RISING")
        elif net <= -2:
            traj = "falling"; _log(log, "TRAJ-FALLING")
        elif (ups >= 1 and downs >= 1) and (ups + downs) >= 3:
            traj = "fluctuating"; _log(log, "TRAJ-FLUCTUATING")
        else:
            traj = "stable"; _log(log, "TRAJ-STABLE")
        e["trajectory"] = traj
    else:
        e["trajectory"] = "stable"
    e["updated_at_turn"] = turn

    # ---- Step 6: decay ----
    if e["unverified_turns"] >= PARAMS["decay_turns"] and e["primary"] != "neutral_unclear":
        e["primary"] = "neutral_unclear"
        e["confidence"] = round(e["confidence"] * PARAMS["decay_conf_factor"], 3)
        e["unverified_turns"] = 0
        _log(log, "DECAY")

    # ---- Step 7: threads (C3 advisory semantics) ----
    thr = head.get("thread", {}) or {}
    action = thr.get("action", "continue")
    gist = thr.get("gist", "")
    entities = thr.get("entities", []) or []
    threads = state["threads"]
    def _match(g: str, ents: list) -> str | None:
        for t in threads:
            if t.get("status") in ("active", "paused") and (
                    (g and g.lower() == t.get("gist", "").lower()) or
                    any(ent.lower() in [x.lower() for x in t.get("entities", [])] for ent in ents)):
                return t["id"]
        return None

    if action == "new":
        if _match(gist, entities) is None:
            nid = f"T{len(threads) + 1}"
            threads.append({"id": nid, "gist": gist or "untitled", "status": "active",
                             "entities": entities, "events": [], "open_loops": [],
                             "first_turn": turn, "last_active_turn": turn})
            if state["active_thread"]:
                for t in threads:
                    if t["id"] == state["active_thread"]:
                        t["status"] = "paused"
            state["active_thread"] = nid
            _log(log, f"THREAD-NEW:{nid}")
        else:
            action = "continue"
            _log(log, "THREAD-DEGRADE:new→continue")
    if action in ("continue", "return"):
        tid = state["active_thread"]
        if action == "return":
            m = _match(gist, entities)
            paused = [t for t in threads if t.get("status") == "paused"]
            if m and m != state["active_thread"] or (not m and len(paused) == 1):
                tid = m or (paused[0]["id"] if paused else tid)
                for t in threads:
                    if t["id"] == tid:
                        t["status"] = "active"
                    elif t["id"] == state["active_thread"]:
                        t["status"] = "paused"
                state["active_thread"] = tid
                _log(log, f"THREAD-RETURN:{tid}")
            else:
                _log(log, "THREAD-DEGRADE:return→continue")
        for t in threads:
            if t["id"] == tid:
                t["last_active_turn"] = turn
                for ent in entities:
                    if ent not in t.get("entities", []):
                        t.setdefault("entities", []).append(ent)
    elif action == "switch":
        m = _match(gist, entities)
        if m and m != state["active_thread"]:
            for t in threads:
                if t["id"] == state["active_thread"]:
                    t["status"] = "paused"
                if t["id"] == m:
                    t["status"] = "active"
            state["active_thread"] = m
            _log(log, f"THREAD-SWITCH:{m}")
        elif not m and gist:
            nid = f"T{len(threads) + 1}"
            threads.append({"id": nid, "gist": gist, "status": "active", "entities": entities,
                             "events": [], "open_loops": [], "first_turn": turn,
                             "last_active_turn": turn})
            for t in threads:
                if t["id"] == state["active_thread"]:
                    t["status"] = "paused"
            state["active_thread"] = nid
            _log(log, f"THREAD-NEW:{nid}")
        else:
            _log(log, "THREAD-DEGRADE:switch→continue")
    # close inactive
    for t in threads:
        if t.get("status") in ("active", "paused") and t["id"] != state["active_thread"]:
            if turn - int(t.get("last_active_turn", turn)) >= PARAMS["thread_close_inactive"]:
                t["status"] = "closed"
                _log(log, f"THREAD-CLOSE:{t['id']}")

    # ---- Step 8: memory candidates ----
    mem = state["memory"]
    for mc in head.get("memory_candidates", []) or []:
        crit = mc.get("criterion", "salient")
        entry = {"type": mc.get("type", "semantic"), "content": mc.get("content", ""),
                 "criterion": crit, "turn": turn}
        if crit == "explicit" and mc.get("type") == "preference":
            mem["preferences"].append({"rule": mc.get("content", ""), "origin": "explicit user statement",
                                        "scope": "persistent", "set_turn": turn, "supersedes": None})
            _log(log, "MEM-COMMIT-EXPLICIT")
        else:
            mem["write_candidates"].append(entry)
            _log(log, "MEM-PEND")

    # ---- exit degraded mode on first successful perception (U6) ----
    if state.get("degraded_perception"):
        state["degraded_perception"] = False
        state["parse_fail_streak"] = 0
        _log(log, "DEGRADED-PERCEPTION-EXIT")
    state["parse_fail_streak"] = 0

    # ---- Step 9: safety evaluation ----
    sf = state["safety"]
    sf["categories"]["self_harm"]["present"] = self_harm
    sf["categories"]["harm_to_others"]["present"] = harm_others
    if other_flag:
        sf["categories"]["other_flagged"]["present"] = True
    sf["categories"]["self_harm"]["confidence"] = safety_conf
    if risk in ("none", "low"):
        sf["safe_streak"] = int(sf.get("safe_streak", 0)) + 1
    else:
        sf["safe_streak"] = 0

    override = state["safety"].get("override_active", False)
    if risk == "high_risk" or self_harm or harm_others:
        sf["risk_level"] = "high_risk"
        if not override:
            _log(log, "SAFE-OVERRIDE")
        override = True
        sf["safe_streak"] = 0
    elif risk == "elevated_distress":
        sf["risk_level"] = "elevated_distress"
        override = True
        sf["safe_streak"] = 0
        _log(log, "SAFE-ELEVATED")
    elif override:
        # de-escalation needs N consecutive safe turns
        if sf["safe_streak"] >= PARAMS["safety_deescalate_turns"]:
            override = False
            sf["risk_level"] = "none"
            _log(log, "SAFE-HYSTERESIS-CLEAR")
        else:
            _log(log, "SAFE-HYSTERESIS")
    else:
        sf["risk_level"] = risk
    sf["override_active"] = override

    # ---- Step 10: mode ----
    md = state["mode"]
    explicit = bool(tr.get("advice_requested_explicit") or (head.get("advice_requested") is True))
    if override:
        if md["current"] != "CALM":
            md["current"], md["since_turn"], md["entered_via"] = "CALM", turn, "safety"
            state["conversation"]["mode_history"].append("CALM")
            _log(log, "MODE-SAFETY:CALM")
        md["pending_target"], md["pending_count"] = None, 0
    elif explicit:
        if md["current"] != "ADVICE":
            md["current"], md["since_turn"], md["entered_via"] = "ADVICE", turn, "explicit"
            state["conversation"]["mode_history"].append("ADVICE")
        _log(log, "MODE-EXPLICIT:ADVICE")
        md["pending_target"], md["pending_count"] = None, 0
    else:
        target = "ADVICE" if head.get("user_need") == "advice" else None
        if target and target != md["current"]:
            if md.get("pending_target") == target:
                md["pending_count"] = int(md.get("pending_count", 0)) + 1
            else:
                md["pending_target"], md["pending_count"] = target, 1
            if md["pending_count"] >= PARAMS["mode_hysteresis"]:
                md["current"], md["since_turn"], md["entered_via"] = target, turn, "inferred"
                state["conversation"]["mode_history"].append(target)
                _log(log, f"MODE-INFERRED:{target}")
                md["pending_target"], md["pending_count"] = None, 0
            else:
                _log(log, f"HYST-BLOCK:{target}")
        elif not target and md.get("pending_target"):
            md["pending_target"], md["pending_count"] = None, 0

    # ---- Step 11: phase derivation ----
    traj = state["emotion"]["trajectory"]
    if override:
        phase = "venting"
    elif state["conversation"]["turn_count"] <= 1:
        phase = "opening"
    elif traj == "falling" or (state["conversation"].get("turn_count", 0) > 12):
        phase = "winding_down"
    else:
        phase = "venting"
    state["conversation"]["phase"] = phase

    # ---- Step 13: ledger (only fully-spoken responses count) ----
    led = state["conversation"]["agent_behavior_ledger"]
    if tr.get("response_completed"):
        effective_mode = (tr.get("policy_derived", {}) or {}).get("mode", md["current"])
        if effective_mode == "ADVICE":
            led["advice_given"] = int(led.get("advice_given", 0)) + 1
        led["last_move"] = tr.get("last_move", led.get("last_move"))

    # ---- Step 12: policy derivation ----
    policy = _derive_policy(state, tr, head=head, correction_applied=correction_applied,
                             safety_override=override, mode=md["current"])
    return state, policy, log


def derive_policy(state: dict, turn_record: dict | None = None) -> dict:
    """Public policy derivation on committed state (C6 step 12). Deterministic."""
    return _derive_policy(state, turn_record or {}, head=None)


def _derive_policy(state: dict, tr: dict, head: dict | None, degradation: str | None = None,
                    correction_applied: bool = False, safety_override: bool | None = None,
                    mode: str | None = None) -> dict:
    md = state["mode"]
    m = mode or md["current"]

    # Turn-taking decision (owner brief 2026-08-27): backchannels get minimal
    # acknowledgments; listen requests suppress content responses. Deterministic
    # structured flags from orchestration (exact-match, no interpretation).
    relation = tr.get("turn_relation")
    if relation == "backchannel" and not state["safety"].get("override_active"):
        return {"mode": m, "response_goal": "backchannel",
                 "advice_permission": "not_granted",
                 "safety_override_active": bool(state["safety"].get("override_active")),
                 "avoid": ["long_response", "questions", "advice", "naming_emotion"],
                 "pacing": {"max_sentences": 1, "max_questions": 0, "max_words": 3},
                 "emotion_label_allowed": False,
                 "phase": state["conversation"].get("phase")}
    if relation == "listen_request" and not state["safety"].get("override_active"):
        return {"mode": m, "response_goal": "listen_quietly",
                 "advice_permission": "not_granted",
                 "safety_override_active": bool(state["safety"].get("override_active")),
                 "avoid": ["questions", "advice", "naming_emotion", "long_response"],
                 "pacing": {"max_sentences": 1, "max_questions": 0},
                 "emotion_label_allowed": False,
                 "phase": state["conversation"].get("phase")}
    override = state["safety"].get("override_active", False) if safety_override is None else safety_override
    if override:
        m = "CALM"
    avoid = {"VENT": ["advice", "escalation", "judgement", "endorsing_accusations"],
             "ADVICE": [],
             "CALM": ["advice", "minimising", "interrogation"],
             "REFLECT": ["advice", "endorsing_accusations"],
             "CLOSING": ["advice", "new_threads"]}.get(m, ["advice"])
    prefs = state["memory"].get("preferences", [])
    if any("no advice" in p.get("rule", "") for p in prefs) and m != "ADVICE":
        avoid.append("advice")
    if tr.get("interrupted_agent_response"):
        avoid.append("repeating_interrupted_content")
    emo = state["emotion"]
    policy = {
        "mode": m,
        "response_goal": {"VENT": "encourage_continuation", "ADVICE": "problem_solve",
                           "CALM": "support", "REFLECT": "reflect",
                           "CLOSING": "wind_down"}.get(m, "encourage_continuation"),
        "advice_permission": "granted" if m == "ADVICE" else "not_granted",
        "safety_override_active": bool(override),
        "avoid": avoid,
        "pacing": {"max_sentences": PARAMS["max_sentences"], "max_questions": PARAMS["max_questions"]},
        "emotion_label_allowed": bool(emo.get("confidence", 0.0) >= PARAMS["emotion_label_threshold"]
                                       and emo.get("primary") != "neutral_unclear"),
        "emotion_reflection": {
            "label_to_use": (emo.get("primary")
                              if (emo.get("confidence", 0.0) >= PARAMS["emotion_label_threshold"]
                                  and emo.get("primary") != "neutral_unclear")
                              else None),
            "ok_to_name": bool(emo.get("confidence", 0.0) >= PARAMS["emotion_label_threshold"]
                                and emo.get("primary") != "neutral_unclear"),
            "user_interpretation_neutral": True,
        },
        "phase": state["conversation"].get("phase"),
    }
    if degradation == "clarify":
        policy["degradation"] = "clarify"
        policy["turn_type_handling"] = "short speech-native clarification asking them to repeat"
    if degradation == "D7":
        policy["degradation"] = "D7"
        policy["turn_type_handling"] = "gentle presence acknowledgment; invite sharing; never claim mishearing"
    if degradation == "D8":
        policy["degradation"] = "D8"
        policy["turn_type_handling"] = "open-door response; zero questions"
    if policy.get("response_suppressed"):
        policy["response_suppressed"] = True
    return policy

```

## `agent/turn_controller.py`  (68 lines)

```python
"""Conversation Turn Controller (owner brief 2026-08-27) — deterministic gate
between VAD endpoint and response generation.

VAD speech-end only means "the user stopped making sound for the moment".
This controller decides whether that was a genuine handoff or a pause inside
a continuing thought.

Decision (pure function, no LLM, no interpretation beyond the listed
deterministic cues — same discipline as the hallucination/echo filters):

  WAIT (suppress response)  when the utterance looks unfinished:
    - trail-off connector at the end (ki/toh/aur/phir/matlab/kyunki/lekin…,
      Devanagari + Roman)
    - trailing ellipsis ("…")
    - very short fragment (<=2 words) while the previous turn was already
      suppressed as a continuation (user is stringing fragments together)

  RESPOND  otherwise.

Suppressed turns STILL enter conversation history (context is preserved so
the eventual response sees the complete thought) — only speech is withheld.
"""
from __future__ import annotations

import re

TRAIL_OFF_MARKERS = {
    # Devanagari connectors (checked against the final word)
    "कि", "की", "तो", "और", "फिर", "मतलब", "क्योंकि", "क्यूंकि", "लेकिन",
    "मगर", "असल", "वो", "वह", "ये", "यह",
    # Roman Hinglish
    "ki", "kie", "toh", "aur", "phir", "matlab", "kyunki", "kyu", "lekin",
    "magar", "actually", "asal",
}

# User handing the floor back ("haan bol", "ab bata") — always respond.
HANDOFF_MARKERS = {"bol", "bolo", "bata", "batao", "sun", "suno", "बोल",
                    "बता", "बताओ", "सुन", "सुनो"}


def decide(user_text: str, previous_turn_was_wait: bool = False) -> tuple[str, str]:
    """Returns (action, reason). action ∈ {"respond", "suppress"}."""
    text = (user_text or "").strip()
    if not text:
        return "suppress", "empty"

    if text.startswith("...") or text.startswith("…"):
        return "suppress", "leading_ellipsis"      # resuming their own thought

    if text.endswith("?") or text.endswith("？"):
        return "respond", "user_question"          # a question is a handoff

    if text.endswith("...") or text.endswith("…"):
        return "suppress", "trailing_ellipsis"

    words = re.findall(r"[\w\u0900-\u097F]+", text, re.UNICODE)
    last = words[-1].lower() if words else ""

    if last in HANDOFF_MARKERS:
        return "respond", "handoff"

    if last in TRAIL_OFF_MARKERS:
        return "suppress", "continuation_marker"

    if len(words) <= 3 and previous_turn_was_wait:
        return "suppress", "continuation_fragment"

    return "respond", "completed_or_unclear"

```

## `agent/fused_turn.py`  (215 lines)

```python
"""aiva.transport — fused perception + response turn (Phase 5, 5.1 + 5.4).

Implements C4 (transport byte-shape), C7 (degradation D1/D2/D4/D4b/D9/D7/D8),
A-U7 (correction head field) against gemini via google-genai, mirroring the
Task-1-validated call pattern from providers/llm.py.

The stream_prose() async generator yields ONLY spoken prose chunks (the
perception head is stripped and exposed via .head / .meta after the stream).
Degradation never restarts mid-streamed audio (D4b); LLM failure with zero
prose yields the deterministic D4 filler (U1 wording draft — pending approval).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import AsyncGenerator

from agent.prompt_fragments import (
    SYSTEM_FUSED_V11, SYSTEM_PLAIN_V11, PROMPT_VERSION,
    FILLER_LINES, PRESENCE_LINES_D7, OPENDOOR_LINES_D8, pick_line,
    BACKCHANNEL_LINES, LISTEN_LINES, CLARIFY_LINES,
)

TAG_RE = re.compile(r"<perception>(.*?)</perception>", re.DOTALL)


class FusedLLM:
    def __init__(self, model: str | None = None):
        self.model = model or os.getenv("AIVA_LLM_MODEL", "gemini-3.5-flash-lite")
        self._client = None
        self.head: dict | None = None
        self.meta: dict = {}

    def _client_for(self, key: str):
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=key)
        return self._client

    def _degraded_system(self) -> str:
        return SYSTEM_PLAIN_V11

    def build_contents(self, user_text: str, policy: dict, memory_view: list,
                       threads: list, history: list) -> str:
        return json.dumps({
            "policy": policy,
            "memory": memory_view,
            "threads": threads,
            "history": history,
            "user_turn": user_text,
        }, ensure_ascii=False)

    async def stream_prose(self, *, user_text: str, turn_type: str, policy: dict,
                            memory_view: list, threads: list, history: list,
                            turn_no: int, degraded: bool,
                            key: str) -> AsyncGenerator[str, None]:
        """Yields spoken prose. Sets self.head / self.meta for the updater."""
        self.head, self.meta = None, {}
        self.meta["turn_type"] = turn_type

        # Turn-taking (owner brief): backchannels get 1-3 word acknowledgments;
        # listen requests get one short listening line. No LLM call — the policy
        # decision is deterministic (structured turn_relation flag -> policy goal).
        # P0 low-confidence STT: deterministic clarification, no LLM, no invention
        if turn_type == "unclear_speech":
            line = pick_line(CLARIFY_LINES, turn_no)
            self.meta.update({"degradation": None, "llm_called": False, "spoke_because": "unclear_speech"})
            yield line
            return

        goal = (policy or {}).get("response_goal")
        if goal == "backchannel":
            line = pick_line(BACKCHANNEL_LINES, turn_no)
            self.meta.update({"degradation": None, "llm_called": False, "spoke_because": "backchannel"})
            yield line
            return
        if goal == "listen_quietly":
            line = pick_line(LISTEN_LINES, turn_no)
            self.meta.update({"degradation": None, "llm_called": False, "spoke_because": "listen_request"})
            yield line
            return

        # D7 / D8: no LLM call — deterministic lines (updater policy already encodes these)
        if turn_type == "acoustic_only":
            line = pick_line(PRESENCE_LINES_D7, turn_no)
            self.meta.update({"degradation": "D7", "llm_called": False})
            yield line
            return
        if turn_type == "idle":
            policy = policy or {}
            if policy.get("response_suppressed"):
                self.meta.update({"degradation": "D8", "llm_called": False, "suppressed": True})
                return
            line = pick_line(OPENDOOR_LINES_D8, turn_no)
            self.meta.update({"degradation": "D8", "llm_called": False})
            yield line
            return

        system = self._degraded_system() if degraded else SYSTEM_FUSED_V11
        import hashlib
        self.meta["prompt_version"] = PROMPT_VERSION
        self.meta["system_sha1"] = hashlib.sha1(system.encode()).hexdigest()[:10]
        contents = self.build_contents(user_text, policy, memory_view, threads, history)
        self.meta["context"] = contents
        config = {"temperature": 0.7, "system_instruction": system}
        client = self._client_for(key)

        attempt, prose_started, spoken_any = 0, False, False
        while True:
            buf, emitted = "", 0
            t0 = time.perf_counter()
            try:
                stream = await client.aio.models.generate_content_stream(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
                async for chunk in stream:
                    txt = chunk.text or ""
                    if not txt:
                        continue
                    buf += txt
                    self.meta.setdefault("ttft_s", round(time.perf_counter() - t0, 3))
                    m = TAG_RE.search(buf)
                    if m and not prose_started:
                        # head closed — everything after the tag is prose
                        prose_started = True
                        self.meta["head_complete_s"] = round(time.perf_counter() - t0, 3)
                        try:
                            self.head = json.loads(m.group(1).strip())
                        except json.JSONDecodeError:
                            try:
                                obj, _ = json.JSONDecoder().raw_decode(m.group(1).strip())
                                self.head = obj if isinstance(obj, dict) else None
                            except json.JSONDecodeError:
                                self.head = None  # D1: prose passthrough, no state update
                                self.meta["head_raw_snippet"] = m.group(1).strip()[:200]
                    if prose_started:
                        start = max(m.end(), emitted) if m else emitted
                        if len(buf) > start:
                            piece = buf[start:]
                            emitted = len(buf)
                            if not spoken_any:
                                piece = piece.lstrip()   # drop newlines right after </perception>
                            if piece:
                                spoken_any = True
                                yield piece
                    elif degraded and not prose_started:
                        # degraded mode: no head expected — everything is prose
                        prose_started = True
                        piece = buf[emitted:].lstrip()
                        if piece:
                            spoken_any = True
                            yield piece
                        emitted = len(buf)
                if not prose_started and buf.strip():
                    # D2 missing head: full stream is prose; keep the raw for diagnosis
                    self.meta["head_raw_snippet"] = buf.strip()[:400]
                    if "<perception>" in buf and "</perception>" not in buf:
                        self.meta["head_fail_class"] = "unclosed_tags"
                    elif "<perception>" not in buf:
                        self.meta["head_fail_class"] = "missing_tags"
                    piece = buf[emitted:].strip()
                    if piece:
                        spoken_any = True
                        yield piece
                    emitted = len(buf)
                if not spoken_any:
                    self.meta.setdefault("head_raw_snippet", buf.strip()[:400])
                    self.meta.setdefault("head_fail_class", "head_only_no_prose" if self.head else "empty_stream")
                    # nothing spoken: head-only response, or truly empty stream.
                    # Silence is a contract violation — deterministic fallback speaks.
                    self.meta["empty_prose_fallback"] = True
                    tail = ""
                    m2 = TAG_RE.search(buf)
                    if m2:
                        tail = buf[m2.end():].strip()
                    if tail:
                        yield tail
                    else:
                        yield pick_line(FILLER_LINES, turn_no)
                break  # success — D4b: never restart after the stream finished
            except Exception as e:
                if "429" in str(e) and attempt == 0 and not prose_started:
                    attempt += 1
                    await asyncio.sleep(65)   # audit rule: retry once, zero-prose only
                    continue
                self.meta["llm_failed"] = True
                self.meta["llm_error"] = f"{type(e).__name__}: {str(e)[:150]}"
                if not prose_started:
                    # D4: deterministic filler (U1 wording approved 2026-08-26)
                    self.meta["degradation"] = "D4"
                    yield pick_line(FILLER_LINES, turn_no)
                # D4b: >=1 complete sentence already streamed -> stop cleanly
                return
            finally:
                # D1/D2 final head extraction (even on D4b partial streams)
                full = buf
                m = TAG_RE.search(full)
                if m and self.head is None:
                    self.meta["head_raw_snippet"] = m.group(1).strip()[:400]
                    try:
                        self.head = json.loads(m.group(1).strip())
                    except json.JSONDecodeError:
                        try:
                            obj, _ = json.JSONDecoder().raw_decode(m.group(1).strip())
                            self.head = obj if isinstance(obj, dict) else None
                            if self.head is not None:
                                self.meta["head_fail_class"] = "trailing_data_rawdecode_recovered"
                        except json.JSONDecodeError as e2:
                            self.head = None
                            self.meta["head_fail_class"] = f"invalid_json: {e2}"

```

## `agent/prompt_fragments.py`  (134 lines)

```python
"""TRANSPORT_V1.1 — versioned prompt fragments (Phase 5, 5.1).

Locked sources: docs/PHASE3_CONTRACTS.md (C1/C2/C4, A-U7),
docs/PHASE4_T4_1_SAFETY_TAXONOMY.md (safety guidance),
docs/STATE_MODEL_V1.md (persona P4/P8, D7/D8 phrase lists).

The transport byte-shape is frozen per C4:
  <perception>{single JSON object}</perception>
  <prose — <=2 sentences, spoken style>
Delimiters may only change with a transport version bump + revalidation.
"""
from __future__ import annotations

import random

# ---------------------------------------------------------------------------
# C2 persona — masculine self-reference pinned to the cloned voice (locked)
# ---------------------------------------------------------------------------
PROMPT_VERSION = "TRANSPORT_V1.2"

PERSONA = (
    "You are Aiva — a close friend, present in the room on a voice call. Not a counsellor, "
    "not an assistant. Be a real human presence: warm, informal, fully present.\n"
    "ROOM AWARENESS: the person may talk TO you, or talk with someone else while you listen. "
    "Don't assume every sentence is addressed to you. React briefly when it involves you or "
    "when a real friend would naturally react; stay quiet while others talk among themselves.\n"
    "SPEAK LIKE A REAL PERSON:\n"
    "1. Max 2 short sentences. Spoken style. No lists, no markdown, no special characters.\n"
    "2. LANGUAGE MIRRORING: reply in the SAME language the user is using right now — "
    "pure English from them -> reply in English; Hindi or Hinglish from them -> reply in "
    "natural spoken Hinglish (Roman script). Match their register (tum/aap) and keep it "
    "consistent.\n"
    "3. Match their register exactly: if they say 'tum', use 'tum'; if 'aap', use 'aap' — "
    "pick ONE based on how they started and keep it for the whole conversation.\n"
    "4. NO therapy-speak. Never open with 'main samajh (raha/gaya) hoon ki...' — understanding "
    "must show in WHAT you say about their situation, not in emotion-labeling formulas.\n"
    "5. SHORT IS NATURAL: a 2-6 word reply ('haan?', 'achha', 'phir kya hua?', 'seriously?') is "
    "often the most human response. Never pad to fill space.\n"
    "6. React to the SPECIFIC content (the exam, the manager, the friend) — not just a detected "
    "emotion. Let the policy's emotion color your tone instead of naming the feeling.\n"
    "7. Fewer than 1 in 4 replies should end with a question. Statements ('achha', 'phir?') "
    "move things forward without interviewing them.\n"
    "8. Validate feelings without endorsing accusations or interpretations. Do NOT invent "
    "or amplify emotions the user didn't state (never 'mann toh kar raha hoga sab todh dein' "
    "unless they actually said that). React to what was actually said.\n"
    "9. Never give advice unless the current policy explicitly allows it.\n"
    "10. If the user is in serious distress: stay calm and close, gently point them to someone "
    "they trust or a helpline. Never advise, never lecture, never minimize.\n"
    "11. Never claim to be human; if asked directly, be honest and gentle.\n"
    "12. This is a VOICE call: if you did not catch something, say so naturally "
    "('haan? ek baar phir bol', 'yeh wala part miss ho gaya'). Never reference "
    "typing, text, or writing.\n"
    "SELF-REFERENCE: masculine grammar ('main sun raha hoon', 'main samajh gaya'). "
    "Never feminine self-forms (sun rahi / sunungi / jaungi)."
)

TAXONOMY = ["anger_frustration", "sadness", "anxiety", "overwhelm",
            "loneliness_hurt", "guilt_shame", "relief", "neutral_unclear"]
RISK_LEVELS = ["none", "low", "elevated_distress", "high_risk"]
THREAD_ACTIONS = ["new", "continue", "switch", "return"]

# ---------------------------------------------------------------------------
# C1 perception-head spec (v1.1 + A-U7 correction field)
# ---------------------------------------------------------------------------
PERCEPTION_SPEC = (
    "FIRST, silently assess the user's current message. Output your assessment as ONE JSON "
    "object between the tags <perception> and </perception>, with exactly this shape:\n"
    '{"v": 1,\n'
    ' "emotion": {"primary": "<one of: %s>", "valence": "negative|neutral|positive", '
    '"intensity": {"ordinal": <1-5>}, "confidence": <0-1>, "evidence_quote": "<short quote>"},\n'
    ' "thread": {"action": "<one of: %s>", "gist": "<short topic>", "entities": ["Name (role)"]},\n'
    ' "safety": {"risk_level": "<one of: %s>", "self_harm": <bool>, "harm_to_others": <bool>, '
    '"other_flagged": <bool>, "confidence": <0-1>},\n'
    ' "user_need": "be_heard|advice|clarify|other", "advice_requested": <bool>,\n'
    ' "memory_candidates": [{"type": "episodic|semantic|relationship|preference", '
    '"content": "<one line>", "criterion": "explicit|salient|recurrent|corrective"}],\n'
    ' "correction": {"present": <bool>, "about": "emotion|thread|fact|preference"}}\n'
    "Rules for the JSON: no commentary inside it; no code fences; use false/empty for absent "
    "fields; omit the correction key entirely unless the user is explicitly correcting something "
    "you previously said or interpreted; when present, about MUST be one of emotion|thread|fact|preference; "
    "if unsure, lower the confidence instead of guessing.\n"
    "The <perception> block is REQUIRED on EVERY turn. Never skip it, never leave it unclosed, "
    "especially when the user is distressed or the message is short.\n"
    "emotion.primary MUST be copied exactly from the list above — never invent or modify a label.\n"
    "THEN, on a new line after </perception>, write your spoken reply. The JSON is never spoken."
) % ("|".join(TAXONOMY), "|".join(THREAD_ACTIONS), "|".join(RISK_LEVELS))

SAFETY_GUIDANCE = (
    "SAFETY ASSESSMENT GUIDANCE (calibrated):\n"
    "- If the user says their emotional strength is breaking (e.g. 'himmat tut gayi', "
    "'can't take it anymore', 'bas ab nahi seh sakta'), set safety.risk_level to at least "
    "'elevated_distress'.\n"
    "- If the user expresses worry that SOMEONE ELSE may harm themselves or is unsafe "
    "(e.g. 'mera dost marna chahta hai', 'my friend is scared to be alone'), set "
    "safety.other_flagged=true and safety.risk_level to at least 'elevated_distress'.\n"
    "- If the user describes not eating or not sleeping for DAYS, loss of interest in everything, or feeling worse day by day — self-referential and persistent (e.g. 'khaana nahi khaya dinon se', 'neend nahi aa rahi dinon se', 'din ba din gir raha hai') — set safety.risk_level to at least 'elevated_distress'. Hyperbole about annoyances (fan noise, traffic, exams, slow internet: 'pagal ho jaunga', 'goli dena padegi') is NEVER elevated_distress — it stays 'none'.\n"
    "- Single figurative complaints about work/study/objects remain 'none'. Never escalate those."
)

SYSTEM_FUSED_V11 = PERSONA + "\n\n" + PERCEPTION_SPEC + "\n\n" + SAFETY_GUIDANCE

# D9 degraded_perception mode: response-only prompt variant (no head requested)
SYSTEM_PLAIN_V11 = PERSONA + (
    "\n\nRespond normally with at most 2 spoken sentences. No JSON, no tags, no markdown."
)

# ---------------------------------------------------------------------------
# C7 deterministic phrase lists — D4 wording APPROVED by owner (U1, 2026-08-26)
# ---------------------------------------------------------------------------
FILLER_LINES = [
    "Main yahin hoon, thodi technical dikkat aa gayi thi — main wapas aa gaya, batao.",
    "Sorry, ek second ke liye line kat gayi thi. Main sun raha hoon, bolo.",
    "Main hoon yahin. Chalo, jahan chhoda tha wahi se shuru karte hain.",
]
# Turn-taking minimal responses (owner brief 2026-08-27; wording editable)
# P0 low-confidence STT clarification (speech-native; deterministic)
CLARIFY_LINES = ["haan? ek baar phir bol.", "yeh wala part miss ho gaya — phir se bol na.", "sun nahi paya, dobara bol na."]

BACKCHANNEL_LINES = ["haan?", "hmm.", "achha.", "haan bol.", "phir?"]
LISTEN_LINES = ["achha, main sun raha hoon. bolo.", "haan, bolo — main sun raha hoon."]

PRESENCE_LINES_D7 = [
    "Main yahin hoon, tumhare saath. Jab mann kare, bolo.",
    "Main sun raha hoon. Jo bhi feel ho raha hai, sab theek hai.",
]
OPENDOOR_LINES_D8 = [
    "Main yahin hoon — jab baat karni ho, bata dena.",
    "Main hoon yahin. Jab chahe, shuru kar dena.",
]


def pick_line(lines: list[str], turn: int) -> str:
    """Deterministic pick — no randomness (updater determinism discipline)."""
    return lines[turn % len(lines)]

```

## `agent/memory_store.py`  (115 lines)

```python
"""aiva.memory - SQLite memory store (Phase 5, 5.6).

Contract: docs/PHASE3_CONTRACTS.md C5 (identity) + STATE_MODEL_V1.1 section 4.5
(memory principles), owner rulings U2 (90-day orphan purge) and D3 (explicit
auto-commit, others at session end).

Deterministic, stdlib-only. No LLM calls. Raw transcripts are never stored.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

DEFAULT_DB = os.path.join("logs", "aiva_memory.db")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
RETENTION_DAYS = 90  # U2


def valid_device_id(device: str) -> bool:
    return bool(device) and bool(UUID_RE.fullmatch(device))


def ephemeral_id() -> str:
    return "ephemeral-" + re.sub(r"[^0-9a-f]", "", str(time.time_ns()))[:12]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id TEXT NOT NULL,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    criterion TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    occurrences INTEGER NOT NULL DEFAULT 1,
    sessions INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_memory_owner ON memory(owner_id, type);
"""


class MemoryStore:
    def __init__(self, db_path: str = DEFAULT_DB):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.executescript(_SCHEMA)
        self.db.commit()

    def view(self, owner_id: str) -> list:
        cur = self.db.execute(
            "SELECT type, content, occurrences, sessions FROM memory "
            "WHERE owner_id=? AND status='committed' ORDER BY last_seen DESC LIMIT 40",
            (owner_id,))
        lines = []
        for typ, content, occ, sess in cur.fetchall():
            prefix = {"preference": "preference", "relationship": "relationship",
                      "semantic": "fact", "episodic": "episodic"}.get(typ, typ)
            suffix = f" (recurring x{occ} across {sess} sessions)" if typ == "relationship" and occ > 1 else ""
            lines.append(f"{prefix}: {content}{suffix}")
        return lines

    def commit(self, owner_id: str, candidate: dict, immediate: bool = False) -> None:
        typ = candidate.get("type", "semantic")
        content = (candidate.get("content", "") or "").strip()[:200]
        criterion = candidate.get("criterion", "salient")
        now = datetime.now(timezone.utc).isoformat()
        existing = self.db.execute(
            "SELECT id FROM memory WHERE owner_id=? AND type=? AND content=?",
            (owner_id, typ, content)).fetchone()
        if existing:
            self.db.execute(
                "UPDATE memory SET occurrences=occurrences+1, last_seen=? WHERE id=?",
                (now, existing[0]))
        elif criterion == "explicit" or immediate:
            self.db.execute(
                "INSERT INTO memory (owner_id, type, content, criterion, status, created_at, last_seen)"
                " VALUES (?,?,?,?, 'committed', ?, ?)",
                (owner_id, typ, content, criterion, now, now))
        else:
            if self.db.execute("SELECT id FROM memory WHERE owner_id=? AND content=? AND status='pending'",
                               (owner_id, content)).fetchone():
                return
            self.db.execute(
                "INSERT INTO memory (owner_id, type, content, criterion, status, created_at, last_seen)"
                " VALUES (?,?,?,?, 'pending', ?, ?)",
                (owner_id, typ, content, criterion, now, now))
        self.db.commit()

    def promote_pending(self, owner_id: str, keep: bool = True) -> None:
        """Session-end commit evaluation (D3): pending -> committed (or dropped)."""
        if keep:
            self.db.execute(
                "UPDATE memory SET status='committed' WHERE owner_id=? AND status='pending'",
                (owner_id,))
        else:
            self.db.execute("DELETE FROM memory WHERE owner_id=? AND status='pending'", (owner_id,))
        self.db.execute(
            "UPDATE memory SET sessions=sessions+1, last_seen=? WHERE owner_id=? AND status='committed'",
            (datetime.now(timezone.utc).isoformat(), owner_id))
        self.db.commit()

    def record_session(self, owner_id: str) -> None:
        self.promote_pending(owner_id, keep=True)

    def purge_orphans(self, days: int = RETENTION_DAYS) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cur = self.db.execute(
            "DELETE FROM memory WHERE last_seen < ? AND status != 'pending'", (cutoff,))
        self.db.commit()
        return cur.rowcount

```

## `agent/token_server.py`  (83 lines)

```python
import os
import logging
from aiohttp import web
from livekit import api
from .config import Config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def try_cloud_room(room_name: str) -> bool:
    if not Config.LIVEKIT_CLOUD_URL or not Config.LIVEKIT_CLOUD_API_KEY:
        return False
        
    try:
        # LiveKit API needs http/https, not ws/wss
        http_url = Config.LIVEKIT_CLOUD_URL.replace("wss://", "https://").replace("ws://", "http://")
        async with api.LiveKitAPI(http_url, Config.LIVEKIT_CLOUD_API_KEY, Config.LIVEKIT_CLOUD_API_SECRET) as lkapi:
            # We trigger an API call to explicitly catch quota/billing/auth failures
            await lkapi.room.create_room(api.CreateRoomRequest(name=room_name))
        return True
    except Exception as e:
        err_str = str(e).lower()
        if "quota" in err_str or "limit" in err_str or "billing" in err_str or "402" in err_str or "429" in err_str or "unauthorized" in err_str:
            logger.warning(f"[Routing] Cloud quota/billing/auth error: {e}. Falling back to local.")
            return False
        # To be safe, if we get any other unexpected error attempting to reach Cloud, we fallback
        logger.warning(f"[Routing] Unexpected Cloud API error: {e}. Falling back to local.")
        return False

async def handle_token(request):
    room_name = request.query.get('room', 'voice-agent-room')
    participant_name = request.query.get('participant', 'user')

    # C5 identity contract: device-scoped UUID binds memory ownership.
    from agent.memory_store import valid_device_id, ephemeral_id
    device = request.query.get('device', '')
    if valid_device_id(device):
        identity = device
    else:
        identity = ephemeral_id()
        logging.warning("[Routing] missing/invalid device param - ephemeral identity %s", identity)
    participant_name = identity

    use_cloud = await try_cloud_room(room_name)

    if use_cloud:
        logger.info("[Routing] Using LiveKit Cloud path")
        url = Config.LIVEKIT_CLOUD_URL
        api_key = Config.LIVEKIT_CLOUD_API_KEY
        api_secret = Config.LIVEKIT_CLOUD_API_SECRET
    else:
        logger.info("[Routing] Using LiveKit Local path (Fallback)")
        url = Config.LIVEKIT_LOCAL_URL
        api_key = Config.LIVEKIT_LOCAL_API_KEY
        api_secret = Config.LIVEKIT_LOCAL_API_SECRET

    token = api.AccessToken(api_key, api_secret)
    token.with_identity(participant_name).with_name(participant_name).with_grants(
        api.VideoGrants(room_join=True, room=room_name)
    )

    return web.json_response({
        'token': token.to_jwt(),
        'url': url
    })

app = web.Application()
app.router.add_get('/token', handle_token)

import aiohttp_cors
cors = aiohttp_cors.setup(app, defaults={
    "*": aiohttp_cors.ResourceOptions(
        allow_credentials=True,
        expose_headers="*",
        allow_headers="*"
    )
})
for route in list(app.router.routes()):
    cors.add(route)

if __name__ == '__main__':
    logger.info("Starting Token Server on port 3001")
    web.run_app(app, port=3001)

```

## `agent/config.py`  (22 lines)

```python
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # LiveKit Cloud
    LIVEKIT_CLOUD_URL = os.getenv("LIVEKIT_CLOUD_URL")
    LIVEKIT_CLOUD_API_KEY = os.getenv("LIVEKIT_CLOUD_API_KEY")
    LIVEKIT_CLOUD_API_SECRET = os.getenv("LIVEKIT_CLOUD_API_SECRET")
    
    # LiveKit Local
    LIVEKIT_LOCAL_URL = os.getenv("LIVEKIT_LOCAL_URL", "ws://127.0.0.1:7880")
    LIVEKIT_LOCAL_API_KEY = os.getenv("LIVEKIT_LOCAL_API_KEY", "devkey")
    LIVEKIT_LOCAL_API_SECRET = os.getenv("LIVEKIT_LOCAL_API_SECRET", "secret")
    
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    FISH_AUDIO_API_KEY = os.getenv("FISH_AUDIO_API_KEY")
    FISH_AUDIO_REFERENCE_ID = os.getenv("FISH_AUDIO_REFERENCE_ID")
    
    NO_SPEECH_THRESHOLD = float(os.getenv("NO_SPEECH_THRESHOLD", "0.6"))
    AVG_LOGPROB_THRESHOLD = float(os.getenv("AVG_LOGPROB_THRESHOLD", "-1.0"))

```

## `providers/vad.py`  (151 lines)

```python
import os
import time
import numpy as np
from enum import Enum
import ten_vad

class VADEvent(Enum):
    SPEECH_STARTED = "SPEECH_STARTED"
    SPEECH_ENDED = "SPEECH_ENDED"

class VADProvider:
    def process_audio(self, audio_chunk: np.ndarray) -> list[VADEvent]:
        """
        Process incoming audio chunk (int16 PCM) and yield VADEvents.
        """
        raise NotImplementedError

class TenVADProvider(VADProvider):
    """Adaptive endpointing (owner brief 2026-08-27) — no architecture change.

    Same SPEECH_STARTED/SPEECH_ENDED events; the end-of-turn DECISION becomes
    adaptive so normal mid-story pauses don't trigger the agent:

    - base silence threshold unchanged (AIVA_SILENCE_MS, default 300 ms) —
      short turns stay fast
    - PREMATURE-RESUME PENALTY: if speech resumes within RESUME_WINDOW_MS after
      an endpoint, the next endpoint requires +PENALTY_STEP_MS more trailing
      silence (capped at AIVA_MAX_SILENCE_MS, default 1100 ms)
    - LONG-SPEECH FLOOR: after LONG_SPEECH_AFTER_MS of accumulated speech in
      the current stretch, require at least LONG_SPEECH_FLOOR_MS of trailing
      silence (continuous speakers pause mid-story without finishing)
    - GENUINE-GAP RESET: GENUINE_GAP_MS of real silence clears the penalty
      (a real turn change happened)

    Full endpoint evidence is exposed via self.last_endpoint and
    self.last_resume_gap_ms for instrumentation. Deterministic throughout.
    """

    RESUME_WINDOW_MS = 1500
    GENUINE_GAP_MS = 2500
    LONG_SPEECH_FLOOR_MS = 700
    LONG_SPEECH_AFTER_MS = 8000
    PENALTY_STEP_MS = 250

    def __init__(self, hop_size=256, threshold=0.5, silence_duration_ms=None,
                 sample_rate=16000, min_speech_ms=200, max_silence_ms=None):
        self.vad = ten_vad.TenVad(hop_size=hop_size, threshold=threshold)
        self.hop_size = hop_size
        self.threshold = threshold
        self.buffer = np.array([], dtype=np.int16)
        self.sample_rate = sample_rate
        self.hop_ms = hop_size / sample_rate * 1000.0

        self.base_silence_ms = int(silence_duration_ms if silence_duration_ms is not None
                                    else os.getenv("AIVA_SILENCE_MS", "300"))
        self.max_silence_ms = int(max_silence_ms if max_silence_ms is not None
                                   else os.getenv("AIVA_MAX_SILENCE_MS", "1100"))
        self.silence_frames_threshold = int((self.base_silence_ms / 1000) * sample_rate / hop_size)
        self.speech_frames_threshold = int((min_speech_ms / 1000) * sample_rate / hop_size)

        self.is_speaking = False
        self.silence_frames = 0
        self.speech_frames = 0

        # adaptive state
        self.endpoint_penalty_ms = 0
        self.stretch_speech_ms = 0.0
        self.pending_stretch_speech_ms = 0.0
        self.last_endpoint_monotonic = None
        self.last_endpoint = None            # evidence dict, refreshed per endpoint
        self.last_resume_gap_ms = None       # consumed by main.py instrumentation

    # ---- adaptive threshold state machine (deterministic) ----
    def _effective_silence_ms(self) -> float:
        eff = self.base_silence_ms + self.endpoint_penalty_ms
        if self.stretch_speech_ms >= self.LONG_SPEECH_AFTER_MS:
            eff = max(eff, self.LONG_SPEECH_FLOOR_MS)
        return min(eff, self.max_silence_ms)

    def note_premature_resume(self) -> None:
        self.endpoint_penalty_ms = min(
            self.endpoint_penalty_ms + self.PENALTY_STEP_MS,
            max(0, self.max_silence_ms - self.base_silence_ms),
        )

    def _frames_for(self, ms: float) -> int:
        return int(ms / 1000 * self.sample_rate / self.hop_size)

    def process_audio(self, audio_chunk: np.ndarray) -> list[VADEvent]:
        if audio_chunk.dtype == np.float32 or audio_chunk.dtype == np.float64:
            int16_chunk = np.clip(audio_chunk * 32767.0, -32768, 32767).astype(np.int16)
        else:
            int16_chunk = audio_chunk.astype(np.int16)

        self.buffer = np.concatenate((self.buffer, int16_chunk))
        events = []

        while len(self.buffer) >= self.hop_size:
            frame = self.buffer[:self.hop_size]
            self.buffer = self.buffer[self.hop_size:]

            prob, is_speech_class = self.vad.process(frame)
            is_speech = prob > self.threshold

            if is_speech:
                self.speech_frames += 1
                self.silence_frames = 0
                self.stretch_speech_ms += self.hop_ms
                if not self.is_speaking and self.speech_frames >= self.speech_frames_threshold:
                    self.is_speaking = True
                    # premature-resume detection: endpoint recently declared?
                    if self.last_endpoint_monotonic is not None:
                        gap_ms = (time.monotonic() - self.last_endpoint_monotonic) * 1000.0
                        if gap_ms <= self.RESUME_WINDOW_MS:
                            self.note_premature_resume()
                            self.last_resume_gap_ms = round(gap_ms, 1)
                            # merge the interrupted stretch back (long-speech floor continues)
                            self.stretch_speech_ms += self.pending_stretch_speech_ms
                    self.pending_stretch_speech_ms = 0.0
                    events.append(VADEvent.SPEECH_STARTED)
            else:
                self.silence_frames += 1
                self.speech_frames = 0
                # genuine-gap reset while not speaking
                if (not self.is_speaking
                        and self.silence_frames * self.hop_ms >= self.GENUINE_GAP_MS
                        and self.endpoint_penalty_ms):
                    self.endpoint_penalty_ms = 0
                    self.stretch_speech_ms = 0.0
                if self.is_speaking:
                    eff = self._effective_silence_ms()
                    if self.silence_frames >= self._frames_for(eff):
                        self.is_speaking = False
                        self.last_endpoint = {
                            "speech_duration_ms": round(self.stretch_speech_ms, 1),
                            "trailing_silence_ms": round(self.silence_frames * self.hop_ms, 1),
                            "threshold_ms": round(eff, 1),
                            "penalty_ms": self.endpoint_penalty_ms,
                        }
                        self.last_endpoint_monotonic = time.monotonic()
                        self.pending_stretch_speech_ms = self.stretch_speech_ms
                        self.stretch_speech_ms = 0.0
                        events.append(VADEvent.SPEECH_ENDED)

        return events

def get_vad_provider() -> VADProvider:
    return TenVADProvider(
        silence_duration_ms=int(os.getenv("AIVA_SILENCE_MS", "300")),
        max_silence_ms=int(os.getenv("AIVA_MAX_SILENCE_MS", "1100")),
    )

```

## `providers/stt.py`  (168 lines)

```python
import numpy as np
from faster_whisper import WhisperModel

class Transcript:
    def __init__(self, text: str, language: str = "", 
                 no_speech_prob: float = None,
                 avg_logprob: float = None,
                 compression_ratio: float = None):
        self.text = text
        self.language = language
        self.no_speech_prob = no_speech_prob
        self.avg_logprob = avg_logprob
        self.compression_ratio = compression_ratio

class STTProvider:
    def transcribe(self, audio_data: np.ndarray) -> Transcript:
        """
        Transcribe a single audio segment.
        audio_data: float32 numpy array of audio samples at 16kHz
        """
        raise NotImplementedError

import os
import io
import scipy.io.wavfile as wavfile
from groq import Groq

class GroqSTT(STTProvider):
    def __init__(self):
        self.client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        # Natural language detection (owner request 2026-08-27):
        # - forced pin via AIVA_STT_LANGUAGE (hi/en/...) if set
        # - otherwise: auto-detect on the FIRST real utterance (detection is
        #   reliable on longer audio), then pin the session to that language.
        #   Short utterances reuse the session language -> no per-clip drift.
        forced = os.getenv("AIVA_STT_LANGUAGE", "").strip().lower()
        self.session_language = forced or None  # None = not yet detected
        self.auto_mode = not forced

    def transcribe(self, audio_data: np.ndarray) -> Transcript:
        if len(audio_data) < 4000:  # 0.25s minimum, not 0.5s
            return Transcript(text="", language="auto")
        
        # audio_data is float32 [-1.0, 1.0], 16kHz
        # convert to int16 for WAV
        int_audio = (audio_data * 32767).astype(np.int16)
        
        # write to in-memory WAV file
        wav_io = io.BytesIO()
        wavfile.write(wav_io, 16000, int_audio)
        wav_io.seek(0)
        wav_io.name = "audio.wav"

        # STT config history (owner-visible):
        # - no pin: Whisper auto-detect drifts on SHORT clips (es/ro/en outputs)
        #   -> solution: detect on the first utterance, then pin the session
        # - 'en' pin on Hindi speech: English news-anchor hallucinations + <|hi|>
        #   token leaks (2026-08-27 run)
        # - initial_prompt leaked into transcripts on unclear audio -> default OFF
        # Language: forced via AIVA_STT_LANGUAGE; otherwise session auto-detect.
        stt_temperature = float(os.getenv("AIVA_STT_TEMPERATURE", "0.0"))
        stt_prompt = os.getenv("AIVA_STT_PROMPT", "")  # default: no prompt (leak evidence)
        kwargs = dict(
            file=("audio.wav", wav_io.read()),
            model="whisper-large-v3-turbo",
            response_format="verbose_json",
            temperature=stt_temperature,
        )
        if self.session_language:
            # session language established (forced or learned) -> pin it
            kwargs["language"] = self.session_language
        # else: no language param -> true auto-detect for the first utterance
        if stt_prompt:
            kwargs["prompt"] = stt_prompt
        transcription = self.client.audio.transcriptions.create(**kwargs)

        # ---- session language learning (auto mode only) ----
        # Learn ONLY from a qualifying utterance: >=1.2s audio, >=3 words, and
        # not catastrophic confidence. Junk greetings ("Mm-hmm") must never
        # teach the session language (evidence: 2026-08-27 run locked English
        # from a 0.5s grunt, force-decoding all subsequent Hindi as English).
        if self.auto_mode:
            duration_ms = len(audio_data) / 16
            n_words = len((transcription.text or "").split())
            seg_conf = None
            if getattr(transcription, "segments", None):
                seg = transcription.segments[0]
                seg_conf = seg.get("avg_logprob") if isinstance(seg, dict) else getattr(seg, "avg_logprob", None)
            qualifies = (duration_ms >= 1200 and n_words >= 3
                          and (seg_conf is None or seg_conf >= -1.0))
            detected = normalize_lang(getattr(transcription, "language", "") or "")
            if detected:
                if not self.session_language:
                    if qualifies:
                        self.session_language = detected
                        self.mismatch_streak = 0
                        print(f"[STT] session language learned: {detected} "
                              f"({duration_ms:.0f}ms, {n_words} words, conf={seg_conf})")
                    else:
                        print(f"[STT] detection '{detected}' not qualifying yet "
                              f"({duration_ms:.0f}ms, {n_words} words) — staying unpinned")
                else:
                    # pinned mode: catastrophic confidence means the pin may be
                    # wrong -> re-open detection after 2 consecutive failures
                    if seg_conf is not None and seg_conf < -1.0:
                        self.mismatch_streak = getattr(self, "mismatch_streak", 0) + 1
                        if self.mismatch_streak >= 2:
                            self.session_language = None
                            self.mismatch_streak = 0
                            print("[STT] confidence poor twice — re-opening language detection")
                    else:
                        self.mismatch_streak = 0
        
        # Owner decision 2026-08-27: feed Devanagari to the LLM directly.
        # (Roman-Hinglish remains the REPLY style; echo comparison romanizes separately.)
        cleaned = transcription.text.strip()
        
        no_speech_prob = None
        avg_logprob = None
        compression_ratio = None

        if hasattr(transcription, 'segments') and transcription.segments:
            seg = transcription.segments[0]
            if isinstance(seg, dict):
                no_speech_prob = seg.get('no_speech_prob')
                avg_logprob = seg.get('avg_logprob')
                compression_ratio = seg.get('compression_ratio')
            else:
                no_speech_prob = getattr(seg, 'no_speech_prob', None)
                avg_logprob = getattr(seg, 'avg_logprob', None)
                compression_ratio = getattr(seg, 'compression_ratio', None)

        detected_language = getattr(transcription, 'language', "auto")
        return Transcript(
            text=cleaned, 
            language=detected_language,
            no_speech_prob=no_speech_prob,
            avg_logprob=avg_logprob,
            compression_ratio=compression_ratio
        )

def devanagari_to_roman(text: str) -> str:
    """Module-level so the echo filter can compare in a common script.
    The STT transcript itself is NO LONGER romanized (owner decision 2026-08-27):
    the LLM reads Devanagari natively; only comparisons vs Roman text use this."""
    from indic_transliteration import sanscript
    from indic_transliteration.sanscript import transliterate
    if any('\u0900' <= c <= '\u097F' for c in text):
        return transliterate(text, sanscript.DEVANAGARI, sanscript.ITRANS)
    return text


# Default export to be swapped if needed
def get_stt_provider() -> STTProvider:
    return GroqSTT()


# Language map: normalizes Whisper's verbose language names to API codes
_LANG_MAP = {"hindi": "hi", "english": "en", "urdu": "hi"}


def normalize_lang(name: str) -> str | None:
    if not name:
        return None
    low = name.lower()
    if low in _LANG_MAP:
        return _LANG_MAP[low]
    return low if len(low) == 2 else None

```

## `providers/llm.py`  (57 lines)

```python
from typing import AsyncGenerator
import os
from google import genai
from google.genai import types

class LLMProvider:
    async def generate_response_stream(self, messages: list[dict]) -> AsyncGenerator[str, None]:
        """
        Generate a streaming response from the LLM based on conversation context.
        Yields text chunks as they are generated.
        """
        raise NotImplementedError

class GeminiLLM(LLMProvider):
    def __init__(self, model: str = "gemini-3.5-flash-lite"):
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not set.")
        self.client = genai.Client(api_key=self.api_key)
        self.model_name = model

    async def generate_response_stream(self, messages: list[dict]) -> AsyncGenerator[str, None]:
        system_instruction = None
        contents = []
        
        for msg in messages:
            if msg["role"] == "system":
                system_instruction = msg["content"]
                continue
            
            # Map generic roles to Gemini specific roles
            gemini_role = "user" if msg["role"] == "user" else "model"
            
            contents.append(
                types.Content(role=gemini_role, parts=[types.Part.from_text(text=msg["content"])])
            )
            
        config_kwargs = {}
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
            
        # Optional: Add temperature, max_output_tokens
        config_kwargs["temperature"] = 0.7
        config = types.GenerateContentConfig(**config_kwargs)
        
        response_stream = await self.client.aio.models.generate_content_stream(
            model=self.model_name,
            contents=contents,
            config=config
        )
        
        async for chunk in response_stream:
            if chunk.text:
                yield chunk.text

def get_llm_provider() -> LLMProvider:
    return GeminiLLM()

```

## `providers/tts.py`  (188 lines)

```python
from typing import AsyncGenerator
import os
import asyncio
import logging
import numpy as np
from livekit import rtc
from livekit.agents import tts

logger = logging.getLogger(__name__)

class TTSProvider:
    async def synthesize_stream(
        self, text_stream: AsyncGenerator[str, None]
    ) -> AsyncGenerator[tts.SynthesizedAudio, None]:
        raise NotImplementedError


class FishAudioTTSProvider(TTSProvider):
    def __init__(self):
        from livekit.plugins.fishaudio import TTS as FishTTS
        api_key = os.getenv("FISH_AUDIO_API_KEY")
        if not api_key:
            raise ValueError("FISH_AUDIO_API_KEY is not set.")
        voice_id = os.getenv("FISH_AUDIO_REFERENCE_ID")
        if not voice_id:
            raise ValueError("FISH_AUDIO_REFERENCE_ID is not set.")
        kwargs = {
            "api_key": api_key,
            "model": "s2.1-pro-free",
            "voice_id": voice_id,
            # s2.1-pro-free supports max 44100 Hz for wav (server-verified 2026-08-26);
            # synthesize_stream resamples to 48 kHz for AudioSource(48000, 1).
            "sample_rate": 44100,
        }
        self.engine = FishTTS(**kwargs)
        self.last_provider = "fish"

    async def synthesize_stream(
        self, text_stream: AsyncGenerator[str, None]
    ) -> AsyncGenerator[tts.SynthesizedAudio, None]:
        tts_stream = self.engine.stream()

        async def push_text():
            async for chunk in text_stream:
                tts_stream.push_text(chunk)
            tts_stream.flush()
            tts_stream.end_input()

        push_task = asyncio.create_task(push_text())
        try:
            resampler = None
            async for audio_chunk in tts_stream:
                if (resampler is None and 
                        audio_chunk.frame.sample_rate != 48000):
                    resampler = rtc.AudioResampler(
                        input_rate=audio_chunk.frame.sample_rate,
                        output_rate=48000
                    )
                if resampler:
                    for r_frame in resampler.push(audio_chunk.frame):
                        yield tts.SynthesizedAudio(
                            request_id=audio_chunk.request_id,
                            frame=r_frame
                        )
                else:
                    yield audio_chunk
            if resampler:
                for r_frame in resampler.flush():
                    yield tts.SynthesizedAudio(request_id="", frame=r_frame)
        finally:
            if not push_task.done():
                push_task.cancel()
            await tts_stream.aclose()


class EdgeTTSProvider(TTSProvider):
    def __init__(self, voice: str = "en-IN-NeerjaNeural"):
        self.voice = voice

    async def synthesize_stream(
        self, text_stream: AsyncGenerator[str, None]
    ) -> AsyncGenerator[tts.SynthesizedAudio, None]:
        import edge_tts
        import av as pyav
        import io

        self.last_provider = "edge"
        chunks = []
        async for chunk in text_stream:
            chunks.append(chunk)
        full_text = "".join(chunks).strip()
        if not full_text:
            return

        def _sync_work():
            communicate = edge_tts.Communicate(full_text, self.voice)
            mp3_data = bytearray()
            for event in communicate.stream_sync():
                if event["type"] == "audio":
                    mp3_data.extend(event["data"])
            if not mp3_data:
                return []
            buf = io.BytesIO(bytes(mp3_data))
            container = pyav.open(buf, format="mp3")
            audio_stream = container.streams.audio[0]
            resampler = pyav.AudioResampler(
                format="s16", layout="mono", rate=48000
            )
            out_chunks = []
            for packet in container.demux(audio_stream):
                for frame in packet.decode():
                    for r_frame in resampler.resample(frame):
                        pcm = bytes(r_frame.planes[0])
                        audio_np = np.frombuffer(pcm, dtype=np.int16)
                        chunk_size = 960
                        for i in range(0, len(audio_np), chunk_size):
                            piece = audio_np[i:i + chunk_size]
                            lk_frame = rtc.AudioFrame(
                                data=piece.tobytes(),
                                sample_rate=48000,
                                num_channels=1,
                                samples_per_channel=len(piece),
                            )
                            out_chunks.append(
                                tts.SynthesizedAudio(
                                    request_id="edge-tts",
                                    frame=lk_frame
                                )
                            )
            return out_chunks

        for chunk in await asyncio.to_thread(_sync_work):
            yield chunk


class FallbackTTSProvider(TTSProvider):
    def __init__(self):
        try:
            self.primary = FishAudioTTSProvider()
            logger.info("[TTS] Primary: Fish Audio "
                       f"(voice: {os.getenv('FISH_AUDIO_REFERENCE_ID')})")
        except ValueError as e:
            logger.warning(f"[TTS] Fish Audio unavailable: {e}. "
                          "Using EdgeTTS only.")
            self.primary = None
        self.fallback = EdgeTTSProvider()
        self.last_provider = None
        self.last_fallback_reason = None

    async def synthesize_stream(
        self, text_stream: AsyncGenerator[str, None]
    ) -> AsyncGenerator[tts.SynthesizedAudio, None]:
        text_chunks = []
        async for chunk in text_stream:
            text_chunks.append(chunk)

        async def make_stream():
            for chunk in text_chunks:
                yield chunk

        if not self.primary:
            async for chunk in self.fallback.synthesize_stream(make_stream()):
                yield chunk
            return

        try:
            self.last_provider = "fish"
            primary_stream = self.primary.synthesize_stream(make_stream())
            first_chunk = await asyncio.wait_for(
                primary_stream.__anext__(), timeout=5.0
            )
            yield first_chunk
            async for audio in primary_stream:
                yield audio
        except (asyncio.TimeoutError, StopAsyncIteration, Exception) as e:
            self.last_provider = "edge (fallback)"
            self.last_fallback_reason = f"{type(e).__name__}: {str(e)[:150]}"
            if not isinstance(e, StopAsyncIteration):
                logger.error(
                    f"[TTS Fallback] Fish Audio failed: {e}. "
                    "Switching to EdgeTTS."
                )
            async for audio in self.fallback.synthesize_stream(make_stream()):
                yield audio


def get_tts_provider() -> TTSProvider:
    return FallbackTTSProvider()

```

## `frontend/src/App.tsx`  (101 lines)

```tsx
import { useState, useCallback } from 'react';
import {
  LiveKitRoom,
  RoomAudioRenderer,
  BarVisualizer,
  useVoiceAssistant,
} from '@livekit/components-react';
import '@livekit/components-styles';

export default function App() {
  const [token, setToken] = useState<string | null>(null);
  const [serverUrl, setServerUrl] = useState<string>('');
  const [connecting, setConnecting] = useState(false);

  // C5 identity contract: anonymous device-scoped UUID (localStorage, never PII)
  const getDeviceId = (): string => {
    let id = localStorage.getItem('aiva_device_id');
    if (!id) {
      id = (crypto.randomUUID ? crypto.randomUUID() : 'dev-' + Math.random().toString(36).slice(2) + Date.now().toString(36));
      localStorage.setItem('aiva_device_id', id);
    }
    return id;
  };

  const resetMemory = useCallback(() => {
    localStorage.removeItem('aiva_device_id');
    alert('Memory reset. A fresh identity will be used on your next conversation.');
  }, []);

  const startConversation = useCallback(async () => {
    try {
      setConnecting(true);
      const randomRoom = 'room-' + Math.random().toString(36).substring(7);
      const res = await fetch(`http://localhost:3001/token?room=${randomRoom}&device=${getDeviceId()}`);
      const data = await res.json();
      setToken(data.token);
      setServerUrl(data.url);
    } catch (e) {
      console.error('Failed to fetch token', e);
      alert('Failed to get token. Is the token server running?');
    } finally {
      setConnecting(false);
    }
  }, []);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '50px', fontFamily: 'sans-serif' }}>
      <h1>Voice Agent</h1>

      {!token ? (
        <button
          onClick={startConversation}
          disabled={connecting}
          style={{ padding: '10px 20px', fontSize: '16px', cursor: 'pointer' }}
        >
          {connecting ? 'Connecting...' : 'Start Conversation'}
        </button>
      ) : (
        <LiveKitRoom
          serverUrl={serverUrl}
          token={token}
          connect={true}
          audio={{ echoCancellation: true, noiseSuppression: true, autoGainControl: true }}
          video={false}
          onDisconnected={() => setToken(null)}
        >
          <RoomAudioRenderer />
          <VoiceAssistantUI />
        </LiveKitRoom>
      )}
    </div>
  );
}

function VoiceAssistantUI() {
  const { state, audioTrack } = useVoiceAssistant();

  return (
    <div style={{ marginTop: '20px', textAlign: 'center' }}>
      <div style={{ display: 'flex', gap: '10px', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{
          width: '12px', height: '12px', borderRadius: '50%',
          backgroundColor: state === 'connected' ? 'green' : 'orange'
        }} />
        <span>State: {state}</span>
      </div>

      <div style={{ height: '50px', marginTop: '20px' }}>
        {audioTrack && <BarVisualizer state={state} trackRef={audioTrack} />}
      </div>

      <p style={{ color: '#888', fontSize: '14px' }}>Mic is live — just speak!</p>

      <div style={{ marginTop: '10px' }}>
        <button onClick={() => window.location.reload()} style={{ padding: '10px 20px', cursor: 'pointer' }}>
          Disconnect
        </button>
      </div>
    </div>
  );
}

```
