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
    stt_provider = get_stt_provider()  # kept for backward compat
    from providers.stt_router import STTRouter
    stt_router = STTRouter()
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
    # Mark this participant as the assistant so frontend useVoiceAssistant()
    # can identify the agent and animate the BarVisualizer.
    await ctx.room.local_participant.set_metadata('{"agent": true}')
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

    # ---- Phase-1 turn lifecycle telemetry (owner plan; monotonic, per event) ----
    # Crash-proof: every event APPENDS to disk immediately (never buffered), so
    # telemetry survives Ctrl+C / hard kills. tl_dump only adds the summary.
    t0_mono = time.monotonic()
    tl_events = []
    tl_resume_gaps = []
    tl_barge_stop = []
    tl_frag = []
    tl_playback = {"user_speech_mono": None}
    tl_path = os.path.join(log_dir,
        f"turn_lifecycle_{session_start.strftime('%Y%m%d_%H%M%S')}.jsonl")

    def tmark(ev, **fields):
        rec = {"ev": ev, "t": round(time.monotonic() - t0_mono, 3)}
        rec.update(fields)
        tl_events.append(rec)
        try:
            with open(tl_path, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            print(f"[Telemetry] write failed: {e}")
        return rec

    def tl_dump():
        if not tl_events:
            return None
        path = tl_path
        buckets = {"<500": 0, "500-1000": 0, "1000-2000": 0, "2000-3000": 0, ">3000": 0}
        for g in tl_resume_gaps:
            k = "<500" if g < 500 else "500-1000" if g < 1000 else \
                "1000-2000" if g < 2000 else "2000-3000" if g < 3000 else ">3000"
            buckets[k] += 1
        frag_d = [f["duration_ms"] for f in tl_frag if f.get("duration_ms")]
        frag_w = [f["words"] for f in tl_frag if f.get("words") is not None]
        summary = {
            "total_events": len(tl_events),
            "resume_gap_buckets": buckets,
            "barge_stop_latency_ms": {
                "n": len(tl_barge_stop),
                "avg": round(sum(tl_barge_stop) / len(tl_barge_stop), 1) if tl_barge_stop else None,
                "max": round(max(tl_barge_stop), 1) if tl_barge_stop else None,
            },
            "stt_fragmentation": {
                "n": len(tl_frag),
                "avg_duration_ms": round(sum(frag_d) / len(frag_d), 1) if frag_d else None,
                "avg_words": round(sum(frag_w) / len(frag_w), 2) if frag_w else None,
                "under_1500ms": sum(1 for d in frag_d if d < 1500),
            },
        }
        with open(path, "a") as f:
            f.write(json.dumps({"ev": "SESSION_SUMMARY", **summary}, ensure_ascii=False) + "\n")
        print(f"[Telemetry] dumped {len(tl_events)} events -> {path}")
        print("[Telemetry] summary: " + json.dumps(summary))
        return path

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
        tmark("LLM_STARTED", turn=turn.get("turn"))
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
                    tmark("LLM_FIRST_TOKEN", turn=turn.get("turn"))
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
        tmark("TTS_STARTED", turn=turn.get("turn"))
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
                    tmark("TTS_FIRST_AUDIO", turn=turn.get("turn"))
                    tmark("PLAYBACK_STARTED", turn=turn.get("turn"))
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
            tmark("TURN_COMPLETED", turn=turn.get("turn"))
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
                        turn["llm_error"] = engine["fused"].meta.get("llm_error", "")
                        print(f"[StateEngine] PARSE-FAIL turn {turn.get('turn')} class={turn['head_fail_class']} raw={turn['head_raw_snippet'][:240]!r}")
                    if engine["fused"].meta.get("llm_failed"):
                        turn["llm_error"] = engine["fused"].meta.get("llm_error", "")
                        turn["active_model"] = engine["fused"].meta.get("active_model", "unknown")
                        print(f"[StateEngine] LLM ERROR turn {turn.get('turn')}: {turn['llm_error'][:120]}")
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
            tmark("TTS_CANCEL_REQUESTED", turn=turn.get("turn"))
            log_event("AGENT_CANCELLED_EXCEPTION", turn_id=turn.get("turn"), response_id=response_id)
            log_event("AGENT_TASK_CANCELLED", turn_id=turn.get("turn"), details={"task_id": str(id(asyncio.current_task()))})
            await flush_audio_source(agent_source)
            tmark("PLAYBACK_STOPPED", turn=turn.get("turn"))
            if tl_playback["user_speech_mono"] is not None:
                lat = round((time.monotonic() - tl_playback["user_speech_mono"]) * 1000, 1)
                tl_barge_stop.append(lat)
                tmark("BARGE_IN_STOP_LATENCY_MS", turn=turn.get("turn"), latency_ms=lat)
                tl_playback["user_speech_mono"] = None
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
                    if 'raw_pcm_chunks' not in dir():
                        raw_pcm_chunks = []
                    raw_pcm_chunks.append(audio_np.tobytes())

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
                        tmark("VAD_SPEECH_STARTED", turn=turn_number + 1,
                              resume_gap_ms=resume_gap, waited_ms=wait_duration_ms)
                        if resume_gap is not None:
                            tl_resume_gaps.append(resume_gap)
                        
                        # Prepend the pre-roll buffer to capture the speech onset
                        audio_pre_roll = np.array(pre_roll_buffer, dtype=np.int16)
                        speech_buffer = [audio_pre_roll, audio_np] if len(audio_pre_roll) > 0 else [audio_np]
                        raw_pcm_chunks = [audio_pre_roll.tobytes(), audio_np.tobytes()]
                        
                        speech_start_ts = datetime.now(timezone.utc).isoformat()
                        
                        if agent_speaking_event.is_set():
                            tl_playback["user_speech_mono"] = time.monotonic()
                            tmark("USER_SPEECH_DURING_PLAYBACK", turn=turn_number + 1)
                            print("BARGE_IN_CANDIDATE: Agent is speaking. Buffering to evaluate.")
                            log_event("BARGE_IN_CANDIDATE", turn_id=turn_number + 1, details={"agent_speaking": True})
                        else:
                            log_event("USER_SPEECH_STARTED", turn_id=turn_number + 1)
                            print(f"User started speaking... (pre-roll: {len(audio_pre_roll)} samples)")
                    elif vad_event == VADEvent.SPEECH_ENDED:
                        is_speaking = False
                        endpoint_info = dict(getattr(vad_provider, "last_endpoint", {}) or {})
                        tmark("VAD_SPEECH_ENDED", turn=turn_number + 1, **endpoint_info)
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
                                                          premature_resume = None,
                                                          raw_chunks = None):
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
                                tmark("STT_STARTED", turn=turn.get("turn"))
                                stt_start = time.time()
                                # Use router (streaming primary + batch fallback)
                                transcript = await stt_router.transcribe(audio_data, raw_chunks=raw_chunks)
                                turn["stt_latency_s"] = round(time.time() - stt_start, 3)
                                log_event("STT_COMPLETED", turn_id=turn.get("turn"))
                                tmark("STT_COMPLETED", turn=turn.get("turn"),
                                      latency_ms=round(turn["stt_latency_s"] * 1000, 1))
                                tl_frag.append({"turn": turn.get("turn"),
                                                 "duration_ms": round(duration_ms, 1),
                                                 "words": len((transcript.text or "").split())})

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
                                    tmark("TURN_DROPPED", turn=turn.get("turn"), reason="echo", similarity=round(similarity, 2))
                                    log_event("AGENT_ECHO_IGNORED", turn_id=turn.get("turn"), details={"transcript": transcript.text, "similarity": similarity, "language": transcript.language})
                                    return
                                    
                                is_valid, rejection_reason = is_real_user_turn(transcript, duration_ms)
                                turn["stt_valid"] = is_valid
                                turn["stt_rejection_reason"] = rejection_reason
                                tmark("VALIDATION_COMPLETED", turn=turn.get("turn"),
                                      valid=is_valid, reason=rejection_reason)

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
                                        if rejection_reason in ("catastrophic_low_confidence",) or is_repetition_loop(transcript.text):
                                            turn["turn_type"] = "unclear_speech"
                                            turn["response_trigger_reason"] = "unclear_stt_clarify"
                                            await run_agent_response(transcript.text, turn)
                                            return
                                    # Other invalid reasons (punctuation_only, high_no_speech):
                                    # let LLM attempt semantic recovery from context
                                    # don't blanket-return
                                    
                                if prev_task and not prev_task.done():
                                    tmark("BARGE_IN_DETECTED", turn=turn.get("turn"))
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
                                tmark("TURN_DECISION", turn=turn.get("turn"),
                                      decision=action, reason=ctrl_reason)
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
                                if turn.get("dropped_reason") or turn.get("stt_valid") is False:
                                    tmark("TURN_DROPPED", turn=turn.get("turn"),
                                          reason=turn.get("dropped_reason") or turn.get("stt_rejection_reason"))
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
                                endpoint_info=endpoint_info, premature_resume=premature_resume,
                                raw_chunks=raw_pcm_chunks if 'raw_pcm_chunks' in dir() else None
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
    async def _dump_telemetry():
        tl_dump()

    async def _commit_session_memory():
        if engine and engine.get("sess"):
            try:
                engine["sess"].end_session(keep_pending=True)
                print("[StateEngine] memory committed at session end")
            except Exception as e:
                print(f"[StateEngine] memory commit failed: {type(e).__name__}: {e}")
    if hasattr(ctx, "add_shutdown_callback"):
        ctx.add_shutdown_callback(_commit_session_memory)
        ctx.add_shutdown_callback(_dump_telemetry)
        print("[Telemetry] shutdown hooks registered (summary written at session end)")

    # ---- Startup quota check ----
    async def _quota_check():
        from agent.fused_turn import _all_keys, MODEL_POOL
        keys = _all_keys()
        print(f"[Quota] checking {len(keys)} keys × {len(MODEL_POOL)} models...")
        for k_idx, k in enumerate(keys):
            for m_idx, m in enumerate(MODEL_POOL):
                label = f"key{k_idx+1}/{m}"
                try:
                    from google import genai
                    client = genai.Client(api_key=k)
                    resp = await asyncio.wait_for(
                        client.aio.models.generate_content(model=m, contents="hi"), timeout=15)
                    print(f"[Quota] ✅ {label}: OK")
                except asyncio.TimeoutError:
                    print(f"[Quota] ⏱ {label}: TIMEOUT")
                except Exception as e:
                    err = str(e)[:120]
                    if "429" in err:
                        print(f"[Quota] ❌ {label}: 429 QUOTA EXHAUSTED")
                    elif "404" in err or "not found" in err.lower():
                        print(f"[Quota] ❌ {label}: MODEL NOT FOUND")
                    elif "API_KEY" in err.upper() or "invalid" in err.lower():
                        print(f"[Quota] ❌ {label}: INVALID API KEY")
                    else:
                        print(f"[Quota] ⚠️ {label}: {err}")

    if engine:
        asyncio.create_task(_quota_check())
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
