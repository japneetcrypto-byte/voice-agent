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
import collections
import numpy as np
from livekit import rtc
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli

from .session import ConversationSession
from providers.vad import get_vad_provider, VADEvent
from providers.stt import get_stt_provider, devanagari_to_roman
from providers.stt_router import STTRouter
from providers.speaker_signature import echo_score
from agent.layered_context import LayeredContextManager
from agent.turn_controller import decide as turn_controller_decide, GREETING_MARKERS
from agent.transcript_router import route_transcript
from agent.response_state import classify as response_state_classify, \
     reconcile_payload as response_reconcile_payload, \
     FULLY_PLAYED, PARTIALLY_PLAYED, UNHEARD
from agent.call_supervisor import CallSupervisor, build_snapshot, RESCUE_GRACE_S
from agent.reply_guard import (feminine_self_reference, strip_tag_leak, fix_merged_words,
                               clean_specials, is_confirm_echo, devanagari_present,
                               shape_signature, is_challenge, is_detail_request,
                               is_repeat_of, cap_for, remaining_text, SENT_END_RE)
from agent.prompt_fragments import FILLER_LINES, pick_line, PROMPT_VERSION
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
    stt_router = STTRouter()
    print(f"[STT] Provider: {type(stt_router.primary).__name__ if stt_router.primary else 'GroqSTT'} (fallback: Groq)")
    llm_provider = get_llm_provider()
    tts_provider = get_tts_provider()
    session = ConversationSession()

    # ---- Phase 5 state engine (5.1/5.2/5.3/5.4/5.6) — flag-gated, falls back ----
    state_engine_on = os.getenv("AIVA_STATE_ENGINE", "1") == "1"
    engine = None
    if state_engine_on:
        try:
            from agent.fused_turn import FusedLLM
            from agent.memory_store import MemoryStore
            lcm = LayeredContextManager(log_dir="logs")
            lcm.recover_from_checkpoint()
            engine = {"fused": FusedLLM(), "store": MemoryStore(), "sess": None, "lcm": lcm}
            try:
                from providers.stt_gemini_live import GeminiLiveSTT
                # AUDIT 2026-08-29: only construct the per-session streaming
                # STT when Gemini Live is ALSO the router primary. Otherwise
                # the SAME audio was double-sent (session WS stream + a second
                # per-turn Live connection in the router). With
                # AIVA_STT_PRIMARY=groq (current recommendation) this stays off.
                if os.getenv("AIVA_STT_PRIMARY", "gemini_live") == "gemini_live":
                    engine["gemini_stt"] = GeminiLiveSTT()
                    print("[STT] Gemini Live Transcribe active")
                else:
                    engine["gemini_stt"] = None
                    print("[STT] session streaming off (router primary is not gemini_live)")
            except Exception as e:
                # Provider init failure must NEVER kill the state engine binding
                engine["gemini_stt"] = None
                print(f"[STT] Gemini Live unavailable: {type(e).__name__}: {e}")
            print(f"[StateEngine] on (persona {PROMPT_VERSION}) — components: "
                  f"fused=ON store=ON lcm=ON sess=BINDS-ON-PARTICIPANT-JOIN "
                  f"gemini_stt={'ON' if engine.get('gemini_stt') else 'off'}")
        except Exception as e:
            print(f"[StateEngine] INIT FAILED: {type(e).__name__}: {e} — "
                  f"turns will speak a deterministic filler (legacy brain disabled)")
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
    try:
        import subprocess as _sp
        _build = _sp.run(["git", "log", "-1", "--format=%h %s"], capture_output=True,
                          text=True, timeout=5, cwd=os.getcwd()).stdout.strip()
    except Exception:
        _build = "unknown"
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

    log_event("WORKER_BUILD", details={"commit": _build, "pid": os.getpid()})

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

    def sess_owner_id() -> str | None:
        try:
            return engine["sess"].owner_id if engine and engine.get("sess") else None
        except Exception:
            return None

    async def run_agent_response(user_text: str, turn: dict):
        if agent_task and not agent_task.done() and agent_task != asyncio.current_task():
            # A newer response task owns the floor. NEVER silent about it —
            # silent skips surfaced as 'no reply generated' mysteries in
            # session 133659 (t2/t3/t22: user left hanging).
            turn["response_skipped"] = "newer_task_active"
            print(f"[Agent] response SKIPPED turn={turn.get('turn')} "
                  f"(newer task active; user_text={user_text[:40]!r})")
            log_event("RESPONSE_SKIPPED", turn_id=turn.get("turn"),
                      details={"reason": "newer_task_active", "user_text": user_text[:60]})
            tmark("RESPONSE_SKIPPED", turn=turn.get("turn"))
            return
        
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
            turn["engine_path"] = ("supervisor" if turn.get("turn_type") == "supervisor_rescue"
                                    else "fused")
            turn["owner"] = (sess_owner_id() or "")[:8]
            sess = engine["sess"]
            lcm = engine.get("lcm")
            if user_text and turn.get("turn_type", "speech") == "speech":
                lcm.add_turn("user", user_text)
            if lcm.needs_compression():
                overflow = lcm.get_overflow_turns()
                if overflow:
                    prompt = lcm.get_compression_prompt(overflow)
                    asyncio.create_task(_compress_layer2(lcm, prompt, overflow))
            turn["policy"] = sess.policy_for_turn()
            # DETAILED MODE (directive synthesis): explicit detail request
            # latches chunked delivery for the next N turns; continuation
            # cues ('haan/aage/phir') keep it alive. Policy marker
            # policy.delivery drives persona V1.11 rule 1b.
            if user_text and is_detail_request(user_text):
                detail_mode["turns_left"] = 6
            if detail_mode["turns_left"] > 0 and isinstance(turn["policy"], dict):
                detail_mode["turns_left"] -= 1
                turn["detail_mode"] = True
                if user_text and any(w in user_text.lower() for w in
                                     ("haan", "aage", "phir", "हाँ", "आगे", "और")):
                    turn["policy"]["delivery"] = "continue_detail"
                else:
                    turn["policy"]["delivery"] = "chunked_detail"
            # Anti-parrot nudge (application layer, deterministic): when the
            # parrot-streak detector fired, extend the avoid list for this call.
            # The mutated object goes to the LLM AND the turn log — one truth.
            if int(turn.get("turn", 0)) < _stuck_nudged["until_turn"] and isinstance(turn["policy"], dict):
                turn["policy"]["avoid"] = list(turn["policy"].get("avoid") or []) + ["echo_confirm_parroting"]
                turn["policy"]["response_goal"] = "substantive_reaction"
            # Challenge reconciliation (evidence 185741 t20-22: user challenged
            # the 5-10 figure; model flip-flopped twice instead of reconciling
            # with its own history). When the user challenges a previous claim,
            # the model must CHECK history and reconcile — own the error or
            # explain the difference. Never blind-agree.
            if user_text and is_challenge(user_text) and isinstance(turn["policy"], dict):
                turn["policy"]["avoid"] = list(turn["policy"].get("avoid") or []) + ["flip_flop_agreeing"]
                turn["policy"]["response_goal"] = "reconcile_claim"
                turn["challenge_detected"] = True
                log_event("CHALLENGE_DETECTED", turn_id=turn.get("turn"),
                          details={"user_text": user_text[:80]})
                print(f"[ReplyGuard] challenge detected — reconcile-claim nudge active")
            # Recovery turns (routing contract): meaningful-but-rejected
            # transcripts get BOUNDED recovery — short + checkpoint-oriented,
            # never a substantive wall on a shaky transcript.
            if turn.get("route_action") == "contextual_recovery":
                turn["recovery_mode"] = "contextual_recovery"
                if isinstance(turn["policy"], dict):
                    turn["policy"]["response_goal"] = "checkpoint_recovery"
                    turn["policy"]["avoid"] = list(turn["policy"].get("avoid") or []) + ["long_monologue_on_shaky_transcript"]
            # Response reconciliation (directive fix 2): if the PREVIOUS reply
            # was interrupted (unheard / partially heard), tell this call —
            # popped, so it applies only to the immediately-following turn.
            _prev_response = None
            if engine is not None:
                _prev_response = response_reconcile_payload(
                    engine.pop("last_response", None))
                if _prev_response:
                    turn["reconciles_previous"] = _prev_response
            text_stream = engine["fused"].stream_prose(
                user_text=user_text,
                turn_type=turn.get("turn_type", "speech"),
                policy=turn["policy"],
                memory_view=sess.memory_view(),
                threads=sess.thread_summaries(),
                history=lcm.get_layer1() if lcm else [m for m in messages if m.get("role") != "system"][-6:],
                layer2=lcm.get_layer2() if lcm else None,
                previous_response=_prev_response,
                turn_no=int(turn.get("turn", 0)),
                degraded=bool(sess.state.get("degraded_perception")),
                key=os.getenv("GEMINI_API_KEY", ""),
            )
        elif state_engine_on:
            # INCIDENT GUARD 2026-08-29: the state engine is expected to be
            # live but the session brain never bound (init failure or binding
            # bug). The legacy assistant prompt (session.py) is FORBIDDEN here:
            # it has no persona (C2), no memory (C5), no perception head (C1),
            # and its canned line 'Mujhe is baare mein pata nahi, kuch aur
            # poochh sakte ho?' dominated an entire live session (evidence:
            # session_20260829_083519, 6 verbatim repeats). Contract C7
            # behavior instead: deterministic filler + loud telemetry.
            turn["engine_path"] = "unbound_filler"
            turn["llm_called"] = False
            print(f"[StateEngine] CRITICAL: engine on but sess unbound — "
                  f"D4 filler instead of legacy brain (turn {turn.get('turn')})")
            log_event("ENGINE_UNBOUND", turn_id=turn.get("turn"), response_id=response_id)
            tmark("ENGINE_UNBOUND", turn=turn.get("turn"))

            async def _deterministic_filler():
                yield pick_line(FILLER_LINES, int(turn.get("turn", 0)))
            text_stream = _deterministic_filler()
        else:
            # Legacy brain only when the state engine is EXPLICITLY disabled
            # (AIVA_STATE_ENGINE=0) — a dev mode, never a silent fallback.
            turn["engine_path"] = "legacy"
            text_stream = llm_provider.generate_response_stream(messages)
        
        spoken_text = []   # what is actually spoken (post length-guard)
        full_text = []     # everything the model generated (diagnostics)
        ttft_logged = False
        # Spoken-chunk cap: detail mode delivers ONE coherent thought per turn
        # (~110 chars); normal mode's ceiling is higher. Recovery turns are
        # ALWAYS chunk-capped (bounded blast radius on shaky transcripts).
        # Semantic length is achieved across turns — never a 10-15s monologue.
        active_cap = cap_for(detail_mode["turns_left"] > 0)
        if turn.get("route_action") == "contextual_recovery":
            active_cap = min(active_cap, 110)
        fused_ref = engine.get("fused") if engine else None
        # Deterministic length guard state (safety net; the prompt is the primary
        # length lever). Prose is released sentence-by-sentence until the cap.
        trim = {"pending": "", "emitted": 0, "done": False}
        
        async def text_stream_tee():
            nonlocal ttft_logged
            try:
                async for chunk in text_stream:
                    if not ttft_logged:
                        ttft_s = time.time() - llm_start
                        turn["llm_ttft_s"] = round(ttft_s, 3)
                        print(f"[Metrics] LLM Time to First Token: {ttft_s:.2f}s")
                        ttft_logged = True
                        log_event("LLM_FIRST_TOKEN", turn_id=turn.get("turn"), response_id=response_id)
                        tmark("LLM_FIRST_TOKEN", turn=turn.get("turn"))
                        # Capture the EXACT context this call is using, at the
                        # moment the call is live (race-proof: fused meta is
                        # reset at the start of every stream_prose call, so a
                        # post-playback read can miss or read the next turn's
                        # meta — observed as 'context: NOT CAPTURED' in logs).
                        if fused_ref is not None:
                            turn["llm_context"] = fused_ref.meta.get("context")
                            turn["llm_called"] = fused_ref.meta.get("llm_called", True)
                            turn["spoke_because"] = fused_ref.meta.get("spoke_because", "llm")
                            turn["degradation"] = fused_ref.meta.get("degradation")
                            # AUDIT FIX 2026-08-29: the epoch snapshot MUST be
                            # taken here, after stream_prose's body has run
                            # (generators only execute on first consume — a
                            # pre-stream snapshot is always off-by-one and
                            # invalidates every end-of-turn read).
                            turn["fused_epoch"] = fused_ref.epoch
                    full_text.append(chunk)
                    if trim["done"]:
                        continue  # keep consuming so head/meta finalize, but speak no more
                    trim["pending"] += chunk
                    # Release complete sentences that fit under the cap.
                    piece = ""
                    while True:
                        m = SENT_END_RE.search(trim["pending"])
                        if not m:
                            break
                        sentence = trim["pending"][:m.end()]
                        if trim["emitted"] + len(sentence) > active_cap and trim["emitted"] > 0:
                            trim["done"] = True
                            break
                        piece += sentence
                        trim["pending"] = trim["pending"][m.end():]
                        trim["emitted"] += len(sentence)
                    # Pathological single unbroken sentence: cut at a word
                    # boundary so audio can start at all.
                    if not piece and trim["emitted"] == 0 and len(trim["pending"]) > active_cap:
                        cut = trim["pending"][:active_cap]
                        sp = cut.rfind(" ")
                        if sp > 40:
                            piece = trim["pending"][:sp + 1]
                            trim["pending"] = trim["pending"][sp + 1:]
                            trim["emitted"] = sp + 1
                    if trim["done"] and trim["pending"].strip():
                        turn["reply_trimmed"] = True
                    if piece:
                        piece, leaked = strip_tag_leak(piece)
                        piece = clean_specials(piece)
                        piece = fix_merged_words(piece)
                        # GUARDRAIL: script enforcement — persona says Roman;
                        # code enforces it (transliterate instead of trust).
                        if devanagari_present(piece):
                            try:
                                piece = devanagari_to_roman(piece)
                                turn["script_transliterated"] = True
                                log_event("SCRIPT_TRANSLITERATED", turn_id=turn.get("turn"))
                            except Exception as _te:
                                print(f"[ScriptGuard] transliteration failed: {_te}")
                        if leaked:
                            turn["tag_leak_stripped"] = True
                            log_event("TAG_LEAK_STRIPPED", turn_id=turn.get("turn"))
                        print(piece, end="", flush=True)
                        spoken_text.append(piece)
                        session.recent_agent_text += piece
                        yield piece
                # Stream finished: release the tail after the last sentence
                # boundary (most replies end without trailing punctuation).
                # Done inside the body — a finally cannot yield.
                if not trim["done"] and trim["pending"]:
                    piece = trim["pending"]
                    if trim["emitted"] + len(piece) > active_cap and trim["emitted"] > 0:
                        cut = piece[:active_cap - trim["emitted"]]
                        sp = cut.rfind(" ")
                        piece = cut[:sp].rstrip() if sp > 20 else cut.rstrip()
                        turn["reply_trimmed"] = True
                    trim["pending"] = ""
                    if piece.strip():
                        piece, leaked = strip_tag_leak(piece)
                        piece = clean_specials(piece)
                        piece = fix_merged_words(piece)
                        # GUARDRAIL: script enforcement — persona says Roman;
                        # code enforces it (transliterate instead of trust).
                        if devanagari_present(piece):
                            try:
                                piece = devanagari_to_roman(piece)
                                turn["script_transliterated"] = True
                                log_event("SCRIPT_TRANSLITERATED", turn_id=turn.get("turn"))
                            except Exception as _te:
                                print(f"[ScriptGuard] transliteration failed: {_te}")
                        if leaked:
                            turn["tag_leak_stripped"] = True
                            log_event("TAG_LEAK_STRIPPED", turn_id=turn.get("turn"))
                        print(piece, end="", flush=True)
                        spoken_text.append(piece)
                        session.recent_agent_text += piece
                        trim["emitted"] += len(piece)
                        yield piece
            finally:
                print(f"\n[Metrics] LLM Total Generation Time: {time.time() - llm_start:.2f}s")
                if not turn.get("reply_trimmed") and trim["pending"].strip():
                    turn["reply_trimmed"] = True  # cap hit; leftover never released
                turn["llm_response"] = "".join(spoken_text)
                turn["llm_response_full"] = "".join(full_text)
                turn["reply_words"] = len((turn["llm_response"] or "").split())
                turn["reply_chars"] = len(turn["llm_response"] or "")
                log_event("LLM_COMPLETED", turn_id=turn.get("turn"), response_id=response_id)
                if turn.get("reply_trimmed"):
                    print(f"[ReplyGuard] TRIMMED spoken={turn['reply_chars']} chars "
                          f"(full={len(turn['llm_response_full'])} chars, cap={active_cap})")
                    log_event("REPLY_TRIMMED", turn_id=turn.get("turn"), details={
                        "spoken_chars": turn["reply_chars"],
                        "full_chars": len(turn["llm_response_full"]),
                        "full_text": turn["llm_response_full"][:400],
                    })
                gv = feminine_self_reference(turn["llm_response"] or "")
                if gv:
                    turn["gender_violation"] = gv
                    print(f"[PersonaGuard] feminine self-reference: {gv!r}")
                    log_event("GENDER_VIOLATION", turn_id=turn.get("turn"), details={
                        "form": gv, "reply": (turn["llm_response"] or "")[:120]})
            
        print("Agent speaking...")
        agent_speaking_event.set()
        tts_start = time.time()
        ttfa_logged = False
        tts_audio_start = None
        tts_total_samples = 0
        tts_capture = []  # raw PCM frames, for AIVA_TTS_DUMP=1 voice auditing
        turn["tts_text"] = ""  # final text spoken to TTS (spoken_text snapshot at end)
        log_event("TTS_STARTED", turn_id=turn.get("turn"), response_id=response_id)
        tmark("TTS_STARTED", turn=turn.get("turn"))
        try:
            audio_stream = tts_provider.synthesize_stream(text_stream_tee())
            async for audio_chunk in audio_stream:
                if tts_audio_start is None:
                    tts_audio_start = time.time()
                tts_total_samples += getattr(audio_chunk.frame, "samples_per_channel", 0)
                _frame_pcm = np.frombuffer(audio_chunk.frame.data, dtype=np.int16)
                if tts_capture is not None:
                    tts_capture.append(_frame_pcm.copy())
                try:
                    # 48k -> 16k for the echo-correlation ring (group mean)
                    played_ring.extend(
                        _frame_pcm.reshape(-1, 3).mean(axis=1).astype(np.int16).tolist())
                except Exception:
                    pass
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

            # Voice-audit dump (AIVA_TTS_DUMP=1): WAV per turn + manifest line.
            # Enables: listening drills, chars/sec outlier scan, ASR round-trip
            # (phase5/tts_audit.py) — the way to analyze SPOKEN quality.
            if os.getenv("AIVA_TTS_DUMP") == "1" and tts_capture:
                try:
                    import wave
                    dump_dir = os.path.join(log_dir, "tts")
                    os.makedirs(dump_dir, exist_ok=True)
                    wav_path = os.path.join(
                        dump_dir, f"turn_{turn.get('turn')}_{session_start.strftime('%Y%m%d_%H%M%S')}.wav")
                    with wave.open(wav_path, "wb") as w:
                        w.setnchannels(1)
                        w.setsampwidth(2)
                        w.setframerate(48000)
                        w.writeframes(np.concatenate(tts_capture).tobytes())
                    turn["tts_audio_path"] = wav_path
                    manifest = {"ts": datetime.now(timezone.utc).isoformat(),
                                "turn": turn.get("turn"),
                                "owner": turn.get("owner"),
                                "provider": turn["tts"]["provider"],
                                "text": turn.get("tts_text"),
                                "chars": len(turn.get("tts_text") or ""),
                                "duration_s": turn["tts"]["audio_duration_s"],
                                "ttfa_s": turn.get("tts_first_audio_s"),
                                "path": wav_path}
                    with open(os.path.join(dump_dir, "manifest.jsonl"), "a") as mf:
                        mf.write(json.dumps(manifest, ensure_ascii=False) + "\n")
                except Exception as e:
                    print(f"[TTSDump] failed: {e}")

            print("Agent finished speaking.")
            turn["response_state"] = FULLY_PLAYED
            if engine is not None:
                engine["last_response"] = {"status": FULLY_PLAYED,
                                            "turn": turn.get("turn"),
                                            "heard_text": "".join(spoken_text)}
            turn["response_trigger_reason"] = "completed"
            log_event("PLAYBACK_COMPLETED", turn_id=turn.get("turn"), response_id=response_id)
            tmark("TURN_COMPLETED", turn=turn.get("turn"))
            log_event("AGENT_TASK_COMPLETED", turn_id=turn.get("turn"), details={"task_id": str(id(asyncio.current_task()))})
            
            # Finished naturally without interruption
            session.add_agent_message("".join(spoken_text))
            # Parrot-streak tracking: if >=3 of last 4 replies were echo-back
            # confirmations, nudge the NEXT fused call's policy 'avoid' list.
            # Deterministic, application-layer (updater untouched); turn-logged.
            try:
                _now_spoken = "".join(spoken_text)
                reply_shapes.append(shape_signature(_now_spoken))
                confirms = sum(1 for s in reply_shapes if "confirm" in s)
                # Repetition detection: last 3 assistant replies, DETECTION
                # ONLY (directive: no blind suppression — reconciliation/
                # persona decide legitimacy).
                rep_kind = ""
                if recent_reply_texts:
                    rep, rep_kind = is_repeat_of(_now_spoken, list(recent_reply_texts))
                    if rep:
                        turn["repeat_detected"] = rep_kind
                        log_event("REPEAT_DETECTED", turn_id=turn.get("turn"),
                                  details={"kind": rep_kind})
                recent_reply_texts.append(_now_spoken)
                verbatim = rep_kind in ("verbatim", "extension", "near_identical")
                if ((confirms >= 3 or verbatim) and
                        int(turn.get("turn", 0)) >= _stuck_nudged["until_turn"]):
                    _stuck_nudged["until_turn"] = int(turn.get("turn", 0)) + 4
                    log_event("RESPONSE_PATTERN_STUCK", turn_id=turn.get("turn"),
                              details={"confirm_ratio": confirms / len(reply_shapes)})
                    tmark("RESPONSE_PATTERN_STUCK", turn=turn.get("turn"))
                    print(f"[ReplyGuard] pattern stuck ({confirms}/{len(reply_shapes)} "
                          f"echo-confirms) — anti-parrot nudge for next turns")
            except Exception as e:
                print(f"[ReplyGuard] shape tracking failed: {e}")
            if engine and engine.get("lcm") and spoken_text:
                engine["lcm"].add_turn("assistant", "".join(spoken_text))
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
                    # Epoch guard: only trust fused meta if no newer
                    # stream_prose call (barge-in, idle turn) has reset it
                    # since this turn's stream produced its first token.
                    if turn.get("fused_epoch") is not None and \
                            getattr(engine["fused"], "epoch", None) == turn["fused_epoch"]:
                        turn["perception_head"] = engine["fused"].head
                        turn["degradation"] = turn.get("degradation") or engine["fused"].meta.get("degradation")
                        turn["active_model"] = turn.get("active_model") or engine["fused"].meta.get("active_model")
                        if not turn.get("llm_context"):
                            turn["llm_context"] = engine["fused"].meta.get("context")
                    else:
                        turn["stale_meta_read"] = True
                    policy = engine["sess"].apply_turn(tr, engine["fused"].head)
                    engine["policy"] = policy
                    turn["policy_next"] = policy
                    reply_text = "".join(spoken_text) if spoken_text else ""
                    if reply_text:
                        from agent.entity_extractor import extract_entities_from_reply
                        for ent in extract_entities_from_reply(reply_text):
                            if ent.get("relation") and ent.get("name"):
                                asyncio.create_task(_promote_relationship(
                                    engine, turn.get("turn"), ent["name"], ent["relation"]))
                    if engine["fused"].meta.get("head_raw_snippet"):
                        print(f"[StateEngine] PARSE-FAIL raw head: {engine['fused'].meta['head_raw_snippet']}")
                except Exception as e:
                    print(f"[StateEngine] apply failed: {type(e).__name__}: {e}")
            
        except asyncio.CancelledError:
            print("\n[Agent was interrupted]")
            turn["interrupted"] = True
            if not ttfa_logged:
                # AUDIT FIX (session 163907 t1/t5/t15): reply cancelled before
                # ANY audio played — user speaks faster than TTS first-audio.
                # These were invisible (interrupted=True, no interrupted_at_ms),
                # reading as 'silent TTS mysteries'. Now explicit.
                turn["cancel_pre_audio"] = True
            # Directive fix 2 + 4: Generated ≠ Spoken ≠ Heard. Explicit
            # playback state + BOTH halves for the NEXT fused call:
            # heard_text (never repeat) + remaining_text (semantic remainder).
            _spoken = "".join(spoken_text)
            _state = response_state_classify(True, ttfa_logged, len(_spoken))
            turn["response_state"] = _state
            turn["heard_text"] = _spoken[:200]
            _remainder = remaining_text("".join(full_text), _spoken)
            turn["remaining_text"] = _remainder[:300]
            if engine is not None:
                engine["last_response"] = {"status": _state,
                                            "turn": turn.get("turn"),
                                            "heard_text": _spoken,
                                            "remaining_text": _remainder}
            turn["tts_text"] = _spoken[:600]
            if engine and engine.get("fused"):
                turn["prompt_version"] = engine["fused"].meta.get("prompt_version")
                turn["system_sha1"] = engine["fused"].meta.get("system_sha1")
                if turn.get("fused_epoch") is not None and \
                        getattr(engine["fused"], "epoch", None) == turn["fused_epoch"]:
                    turn["perception_head"] = engine["fused"].head
                    turn["degradation"] = turn.get("degradation") or engine["fused"].meta.get("degradation")
                    if not turn.get("llm_context"):
                        turn["llm_context"] = engine["fused"].meta.get("context")
                else:
                    turn["stale_meta_read"] = True
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
        except Exception as e:
            # Keep the failure INSIDE the session log. Without this, an error
            # between LLM start and first TTS audio shows up downstream as
            # 'empty reply / no TTS' with no visible cause (evidence: session
            # 222656 turn 1 — decision=respond but zero output, no reason).
            turn["pipeline_error"] = f"{type(e).__name__}: {str(e)[:200]}"
            turn["tts"] = {
                "provider": getattr(tts_provider, "last_provider", "unknown"),
                "fallback_reason": getattr(tts_provider, "last_fallback_reason", None),
                "audio_duration_s": None,
                "playback_duration_s": None,
            }
            print(f"[Agent] RESPONSE_FAILED turn={turn.get('turn')}: {turn['pipeline_error']}")
            log_event("RESPONSE_FAILED", turn_id=turn.get("turn"), response_id=response_id,
                      details={"error": turn["pipeline_error"]})
            tmark("RESPONSE_FAILED", turn=turn.get("turn"))
            try:
                await flush_audio_source(agent_source)
            except Exception:
                pass
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
        raw_pcm_chunks = None  # raw 16k PCM bytes of the current utterance

        print("Listening for user speech...")

        # C5: bind memory ownership to the participant identity (device-scoped UUID)
        # INCIDENT FIX 2026-08-29: this block previously only PRINTED the owner —
        # SessionState was never constructed, engine["sess"] stayed None forever,
        # and every session silently ran the legacy assistant brain (no persona,
        # no memory, no perception head). The binding below is THE fix.
        if engine and engine.get("sess") is None:
            try:
                from agent.session_state import SessionState
                owner = (getattr(participant, "identity", None) or "ephemeral-unknown")
                engine["sess"] = SessionState(owner_id=owner, store=engine["store"])
                n_mem = len(engine["sess"].memory_view())
                print(f"[StateEngine] SESSION BOUND owner={owner} memory_items={n_mem}")
                log_event("SESSION_BOUND", details={"owner": owner, "memory_items": n_mem})
            except Exception as e:
                # Deliberately NOT swallowed: without sess the turn layer must
                # refuse the legacy brain (see run_agent_response unbound guard).
                print(f"[StateEngine] BIND FAILED: {type(e).__name__}: {e}")

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
                    gemini_fwd = engine.get("gemini_stt") if engine else None
                    if gemini_fwd and getattr(gemini_fwd, '_stream_active', False):
                        asyncio.create_task(gemini_fwd.send_chunk(audio_np.tobytes()))
                    if raw_pcm_chunks is not None:
                        raw_pcm_chunks.append(audio_np.tobytes())

                for vad_event in vad_events:
                    if vad_event == VADEvent.SPEECH_STARTED:
                        is_speaking = True
                        gemini_stt_s = engine.get("gemini_stt") if engine else None
                        if gemini_stt_s:
                            asyncio.create_task(gemini_stt_s.start_stream())
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
                        gemini_end = engine.get("gemini_stt") if engine else None
                        if gemini_end and getattr(gemini_end, '_stream_active', False):
                            asyncio.create_task(gemini_end.end_stream())
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
                                # Use router: Gemini Live streaming primary, Groq fallback
                                raw_chunks = raw_chunks or []
                                if raw_chunks and hasattr(stt_router, 'primary'):
                                    transcript = await stt_router.transcribe(audio_data, raw_chunks=raw_chunks)
                                    turn["stt_provider"] = getattr(stt_router, "last_provider", "unknown")
                                    turn["stt_provider_reason"] = getattr(stt_router, "last_provider_reason", None)
                                else:
                                    gstt = engine.get("gemini_stt") if engine else None
                                    if gstt and gstt._final_text:
                                        from providers.stt import Transcript as T
                                        # AUDIT: honest None — a fake avg_logprob
                                        # would blind the validity gates.
                                        transcript = T(text=gstt._final_text, language="hi",
                                                        no_speech_prob=0.0, avg_logprob=None,
                                                        compression_ratio=None)
                                        gstt._final_text = None
                                        turn["stt_provider"] = "gemini_live_session"
                                        m = gstt._last_metrics or {}
                                        print(f"[GeminiSTT] {m.get('duration_s','?')}s | {m.get('word_count','?')}w | {m.get('words_per_second','?')} w/s")
                                    else:
                                        transcript = await asyncio.to_thread(stt_provider.transcribe, audio_data)
                                        turn["stt_provider"] = "groq_batch"
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
                                # SHADOW: acoustic echo correlation (never decides yet)
                                corr_score = None
                                try:
                                    if len(played_ring) >= 16000:
                                        corr_score = echo_score(
                                            audio_data, np.asarray(list(played_ring), dtype=np.float32))
                                except Exception as ee:
                                    print(f"[EchoCorr] failed: {ee}")
                                turn["echo_corr_score"] = corr_score
                                turn["echo_shadow"] = {
                                    "corr": corr_score,
                                    "text_sim": round(similarity, 3),
                                    "text_echo": bool(is_echo_detected),
                                    "speech_ms": round(duration_ms, 1),
                                    "ms_since_playback_end": ms_since_agent_audio_end,
                                    "played_ring_s": round(len(played_ring) / 16000, 2),
                                    "decision": ("dropped_echo" if is_echo_detected
                                                 else "kept"),
                                }
                                if corr_score is not None:
                                    if is_echo_detected and corr_score >= ECHO_SHADOW_AGREE:
                                        log_event("ECHO_MULTI_AGREE", turn_id=turn.get("turn"),
                                                  details={"corr": corr_score, "text_sim": round(similarity, 2)})
                                    elif is_echo_detected and corr_score < ECHO_SHADOW_FLOOR:
                                        log_event("ECHO_TEXT_ONLY", turn_id=turn.get("turn"),
                                                  details={"corr": corr_score, "text_sim": round(similarity, 2),
                                                           "note": "possible eaten user turn"})
                                        print(f"[EchoShadow] TEXT-ONLY echo (corr={corr_score}) — "
                                              f"possible eaten user turn")
                                    elif not is_echo_detected and corr_score >= ECHO_SHADOW_MISS:
                                        log_event("ECHO_CORR_ONLY", turn_id=turn.get("turn"),
                                                  details={"corr": corr_score,
                                                           "note": "echo the text filter missed"})
                                # Late-echo guard: room echo decays in well
                                # under 1.5s. If the user's speech began >1.5s
                                # after Aiva's audio ended, an "echo" match is
                                # almost certainly the USER REPEATING Aiva's
                                # words (evidence 141753 t47 'kharbuja' repeat
                                # was eaten). Real speech wins.
                                late_real_speech = (
                                    ms_since_agent_audio_end is not None
                                    and ms_since_agent_audio_end > 1500)
                                if is_echo_detected and late_real_speech:
                                    turn["echo_overridden"] = True
                                    is_echo_detected = False
                                    print(f"[EchoGuard] late repeat kept as real speech "
                                          f"(+{ms_since_agent_audio_end}ms, sim={similarity:.2f})")
                                if is_echo_detected:
                                    turn["echo_dropped"] = True
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

                                # P0 contract (directive 2026-08-29): invalid
                                # transcripts get an EXPLICIT route — never a
                                # silent fall-through. Actions: acoustic_only /
                                # clarify (deterministic) / contextual_recovery
                                # (fused LLM, turn marked) / normal.
                                action, route_reason = route_transcript(
                                    transcript.text, is_valid, rejection_reason,
                                    transcript.avg_logprob,
                                    is_repetition=is_repetition_loop(transcript.text),
                                    is_catastrophic=(rejection_reason == "catastrophic_low_confidence"))
                                turn["route_action"] = action
                                turn["route_reason"] = route_reason
                                if not is_valid:
                                    print(f"[STT Rejected] reason={rejection_reason} "
                                          f"-> route={action} ({route_reason})")
                                    if action == "acoustic_only":
                                        if (engine and engine.get("sess")
                                                and not agent_was_speaking_at_detection):
                                            turn["turn_type"] = "acoustic_only"
                                            turn["response_trigger_reason"] = "acoustic_only_presence"
                                            await run_agent_response(transcript.text, turn)
                                            return
                                    elif action == "clarify":
                                        if (engine and engine.get("sess")
                                                and not agent_was_speaking_at_detection):
                                            turn["turn_type"] = "unclear_speech"
                                            turn["response_trigger_reason"] = "unclear_stt_clarify"
                                            await run_agent_response(transcript.text, turn)
                                            return
                                    elif action == "contextual_recovery":
                                        turn["recovery_mode"] = "contextual_recovery"
                                        tmark("CONTEXTUAL_RECOVERY", turn=turn.get("turn"))
                                        log_event("CONTEXTUAL_RECOVERY", turn_id=turn.get("turn"),
                                                  details={"reason": route_reason})
                                    # action == "normal": impossible when is_valid
                                    # is False, kept for exhaustiveness
                                else:
                                    # Deterministic capture of relationship facts the USER
                                    # stated ('नीतु बहन एक टीचर है...' -> Neetu/behen).
                                    # Evidence 2026-08-29: relations told in-session were
                                    # lost because capture only ran on Aiva's replies.
                                    # Zero LLM calls; store dedups by content.
                                    try:
                                        # Confidence floor: never mine relationships
                                        # from low-confidence transcripts.
                                        lp = transcript.avg_logprob
                                        if lp is not None and lp < -0.6:
                                            print(f"[EntityCapture] skipped (logprob {lp:.2f} < -0.6)")
                                        else:
                                            from agent.entity_extractor import extract_entities_from_user_text
                                            for ent in extract_entities_from_user_text(transcript.text):
                                                if ent.get("relation") and ent.get("name"):
                                                    asyncio.create_task(_promote_relationship(
                                                        engine, turn.get("turn"), ent["name"], ent["relation"]))
                                                    turn.setdefault("user_relations", []).append(ent)
                                    except Exception as ee:
                                        print(f"[EntityCapture] user-text extraction failed: {ee}")
                                    
                                if prev_task and not prev_task.done():
                                    tmark("BARGE_IN_DETECTED", turn=turn.get("turn"))
                                    print("AGENT_INTERRUPTED_BY_USER")
                                    log_event("AGENT_TASK_CANCEL_REQUESTED", turn_id=turn.get("turn"), details={"task_id": str(id(asyncio.current_task())), "previous_task_id": str(id(prev_task))})
                                    prev_task.cancel()
                                    # AUDIT-FIX 133659: cancel() is async — the
                                    # old task can still be mid-teardown when
                                    # run_agent_response's guard looks at it,
                                    # which silently skipped the new response
                                    # (t2/t3/t22 'no reply generated'). Await
                                    # the teardown so the floor is truly free.
                                    try:
                                        await asyncio.wait_for(prev_task, timeout=1.5)
                                    except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                                        pass

                                if not (speech_start_ts and speech_end_ts and transcript.text.strip() and turn_number):
                                    print("[Turn Gate Rejected] Missing valid turn lifecycle state")
                                    return

                                # ---- Conversation Turn Controller (locked brief):
                                # a VAD speech-end is only a POSSIBLE handoff.
                                prev_wait = int(engine.get("wait_streak", 0)) if engine else 0
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
                                        engine["wait_streak"] = prev_wait + 1
                                        engine["last_turn_wait"] = True
                                    session.add_user_message(transcript.text)  # context preserved
                                    print(f"[TurnController] WAIT ({ctrl_reason}) — silent, "
                                          f"context kept: {transcript.text[:80]!r}")
                                    return
                                if engine:
                                    engine["wait_streak"] = 0
                                    engine["last_turn_wait"] = False
                                turn["response_trigger_reason"] = "user_speech_ended"
                                await run_agent_response(transcript.text, turn)

                            except asyncio.CancelledError:
                                turn["interrupted"] = True
                                turn["interruption_timestamp"] = datetime.now(timezone.utc).isoformat()
                                print("[STT cancelled]")
                            except Exception as e:
                                turn["pipeline_error"] = f"{type(e).__name__}: {str(e)[:200]}"
                                print(f"Pipeline Error: {turn['pipeline_error']}")
                                log_event("PIPELINE_ERROR", turn_id=turn.get("turn"),
                                          details={"error": turn["pipeline_error"]})
                            finally:
                                if turn.get("dropped_reason") or turn.get("stt_valid") is False:
                                    tmark("TURN_DROPPED", turn=turn.get("turn"),
                                          reason=turn.get("dropped_reason") or turn.get("stt_rejection_reason"))
                                try:
                                    supervisor_check_after_turn(turn, transcript.text or "")
                                except Exception as e:
                                    print(f"[Supervisor] check failed: {e}")
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
                                raw_chunks=raw_pcm_chunks
                            )
                        )
                        speech_buffer = []

    # ---- C7/D8 idle watcher: 45s without speech or agent audio -> one open-door line ----
    idle_state = {"last_activity": time.monotonic(), "line_sent": False, "seq": 1000}

    # ---- Stage-1 speaker attribution (SHADOW MODE, owner brief 2026-08-29) ----
    # Rolling buffer of what Aiva actually PLAYED (48k->16k), used to score each
    # captured utterance against recent playback (multi-band echo correlation).
    # Telemetry-only: turn["echo_corr_score"] + shadow events. It does NOT drop
    # anything yet — the text-level filter still owns decisions until live data
    # proves the acoustic gate (docs/SPEAKER_ATTRIBUTION_DESIGN.md).
    played_ring = collections.deque(maxlen=12 * 16000)
    # Review-directive D: thresholds configurable — current values are
    # SYNTHETIC-VALIDATED ONLY (fixtures), pending real-session calibration
    # via phase5/echo_shadow_report.py (Stage 1 exit criterion).
    ECHO_SHADOW_AGREE = float(os.getenv("AIVA_ECHO_AGREE", "0.45"))
    ECHO_SHADOW_MISS = float(os.getenv("AIVA_ECHO_MISS", "0.55"))
    ECHO_SHADOW_FLOOR = float(os.getenv("AIVA_ECHO_FLOOR", "0.30"))

    # ---- Call Supervisor (owner brief: the "senior jumping in") ----
    # Dormant watcher: when a user turn ends with NO agent audio (skipped /
    # errored / unanswered / user calling out), it waits RESCUE_GRACE_S and —
    # if Aiva still isn't audible — speaks one deterministic recovery line and
    # writes a SUPERVISOR_ENGAGED snapshot. Repeat engagements escalate.
    supervisor = CallSupervisor()
    # Response-variety guard (owner brief 2026-08-29: 'quality of response gone
    # bad' — sessions drifted into echo-confirm parroting, 36% of one session).
    reply_shapes = collections.deque(maxlen=4)
    recent_reply_texts = collections.deque(maxlen=3)
    _stuck_nudged = {"until_turn": 0}
    # DETAILED MODE (directive 2026-08-29, synthesis): explicit detail request
    # ('detail mein samjhao', 'poora batao', 'ek-ek point') latches chunked
    # delivery for the next N turns; 'haan/aage/phir' continues the next chunk.
    detail_mode = {"turns_left": 0}
    _last_engine_path = {"v": None}

    async def _send_supervisor_alert(snapshot: dict):
        """Prod hook: POST the snapshot to the paging/webhook endpoint.
        With no AIVA_ALERT_WEBHOOK set, the event log IS the report."""
        url = os.getenv("AIVA_ALERT_WEBHOOK", "")
        if not url:
            return
        try:
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                async with sess.post(url, json=snapshot, timeout=aiohttp.ClientTimeout(total=5)):
                    pass
            print("[Supervisor] alert delivered")
        except Exception as e:
            print(f"[Supervisor] alert delivery failed: {type(e).__name__}: {e}")

    def schedule_supervisor_rescue(outcome: dict):
        async def _rescue():
            await asyncio.sleep(RESCUE_GRACE_S)
            # Stand down if the primary pipeline became audible meanwhile OR
            # a newer user turn is already being processed (its reply will
            # answer the user — never race it).
            if agent_speaking_event.is_set() or (agent_task and not agent_task.done()):
                supervisor.stand_down()
                return
            decision = supervisor.evaluate(outcome)
            if not decision:
                return
            idle_state["seq"] += 1
            rescue_turn = {"turn": idle_state["seq"],
                            "turn_type": "supervisor_rescue",
                            "response_trigger_reason": f"supervisor_{decision['reason']}",
                            "engine_path": "supervisor"}
            snapshot = build_snapshot(decision["reason"], outcome, {
                "engine_bound": bool(engine and engine.get("sess")),
                "last_engine_path": _last_engine_path["v"],
                "last_tts_provider": getattr(tts_provider, "last_provider", None),
                "last_llm_ttft_s": None,
                "wait_streak": engine.get("wait_streak") if engine else None,
            })
            snapshot["engagement_no"] = decision["engagement_no"]
            print(f"[Supervisor] ENGAGED (#{decision['engagement_no']}) reason={decision['reason']} "
                  f"— speaking recovery line, snapshot logged")
            log_event("SUPERVISOR_ENGAGED", turn_id=rescue_turn["turn"], details=snapshot)
            tmark("SUPERVISOR_ENGAGED", turn=rescue_turn["turn"])
            if decision.get("escalate"):
                esc = {**snapshot, "note": "repeat engagement — systemic issue, page a human"}
                log_event("SUPERVISOR_ESCALATE", turn_id=rescue_turn["turn"], details=esc)
                print("[Supervisor] ESCALATED — repeat engagement, paging channel fired")
                asyncio.create_task(_send_supervisor_alert(esc))
            await run_agent_response("", rescue_turn)
        asyncio.create_task(_rescue())

    def supervisor_check_after_turn(turn: dict, user_text: str):
        """Called at user-turn completion. Decides whether THIS turn left the
        user without an answer and schedules the rescue grace timer."""
        if engine is None or turn.get("turn_type") in ("idle", "supervisor_rescue"):
            return
        if turn.get("response_suppressed"):
            return  # deliberate WAIT — the controller owns that silence
        if turn.get("echo_dropped"):
            return  # Aiva hearing itself — no user question was left hanging
        replied = bool(turn.get("llm_response")) and             (turn.get("tts") or {}).get("audio_duration_s") not in (None, 0)
        if replied:
            supervisor.stand_down()
            return
        first = user_text.split()[0].lower() if user_text.split() else ""
        if turn.get("response_skipped"):
            reason = "skipped"
        elif turn.get("pipeline_error"):
            reason = "pipeline_error"
        elif first in GREETING_MARKERS:
            reason = "reachout_unanswered"
        else:
            reason = "unanswered"
        _last_engine_path["v"] = turn.get("engine_path")
        schedule_supervisor_rescue({"reason": reason, "turn": turn.get("turn"),
                                     "user_text": user_text})

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

    async def _compress_layer2(lcm, prompt, overflow):
        """Separate compression LLM call — isolated from response generation."""
        try:
            key = os.getenv("GEMINI_API_KEY", "")
            if not key or key.startswith(("your_", "<<<")):
                return
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=key)
            config = types.GenerateContentConfig(temperature=0.3)
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model="gemini-3.5-flash-lite", contents=prompt, config=config,
                ), timeout=15)
            raw = response.text.strip()
            raw = raw.replace("```json", "").replace("```", "").strip()
            new_state = json.loads(raw)
            lcm.set_layer2(new_state)
            lcm.remove_overflow(overflow)
            lcm.save_checkpoint()
            print(f"[Layer2] compressed {len(overflow)} turns -> new state")
        except Exception as e:
            print(f"[Layer2] compression failed: {type(e).__name__}: {str(e)[:100]}")

    async def _promote_relationship(engine, turn_no, name, relation):
        if not (engine and engine.get("store") and engine.get("sess")):
            return
        try:
            content = f"{name} — user's {relation}"
            store = engine["store"]
            owner = engine["sess"].owner_id
            # Garble containment (evidence t3 2026-08-29): a stated relation is
            # committed PENDING on first sighting (promoted at session end) and
            # IMMEDIATELY only when the store has already seen it before — a
            # repeated fact is real, a one-off garble waits and stays out of
            # the live context.
            already = any(content in line for line in store.view(owner))
            # BUGFIX 182736: criterion="explicit" made commit() treat EVERY
            # sighting as immediate-commit (explicit short-circuits the
            # pending branch), so the pending-until-confirmed rule never
            # engaged and 'गए — user's bhai' went live mid-session.
            # First sighting -> pending (promoted at session end); repeat ->
            # explicit + immediate (a repeated fact is real).
            store.commit(owner,
                {"type": "relationship", "content": content,
                 "criterion": ("explicit" if already else "salient")},
                immediate=bool(already))
            print(f"[Relationship] {'committed' if already else 'pending'}: {name} ({relation})")
        except Exception as e:
            print(f"[Relationship] promotion failed: {e}")

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
        if engine and engine.get("lcm"):
            # Clean end -> DISCARD (checkpoint is for crash recovery only).
            # Saving at clean shutdown leaked the prior session's raw turns
            # into the next session's Layer-1 context (hist=106 at turn 1).
            ctx.add_shutdown_callback(lambda: engine["lcm"].discard_checkpoint())
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
