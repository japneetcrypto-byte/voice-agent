"""Response pipeline — deterministic decision core, extracted from
agent/main.py (Phase 0, Slice 1, 2026-08-30).

Goal: make the response path's DETERMINISTIC decisions replayable and
unit-testable offline (they were previously inline in the 1,851-line
LiveKit closure — untestable, and every bug was found live).

Extracted VERBATIM (no logic change) into pure functions:
  1. build_policy_and_contract()  — contract + detail latch + anti-parrot
     nudge + challenge reconciliation + recovery + reconcile payloads.
  2. process_piece()              — per-piece enforcement chain: tag-strip,
     specials, merged-words, hard gate + events, repeat guard, script.
  3. release_from() / release_tail() — the sentence-boundary trim/cap
     release loop (was inline in text_stream_tee): stream-chunk release +
     thin-output guard + pathological-cut + tail release. Pure over a
     mutable trim state, so the replay harness feeds llm_response_full and
     gets the EXACT pieces the live tee would have spoken.

main.py calls these instead of the inline blocks. Slice 2 (the async
LLM->TTS playback loop + LiveKit seam) remains in main.py until baseline
logs exist and the replay-identity gate + owner smoke pass.

Pure module: no livekit imports. Callbacks injected for side effects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from agent.reply_guard import (strip_tag_leak, clean_specials, fix_merged_words,
                               devanagari_present,
                               is_detail_request, is_challenge,
                               SENT_END_RE, repeat_break_for, cap_for,
                               PLAN_CHUNK_CAP, remaining_text)
from providers.stt import devanagari_to_roman
from agent.response_contract import build_contract, gate_reply
from agent.response_state import (reconcile_payload as response_reconcile_payload,
                                  classify as response_state_classify,
                                  FULLY_PLAYED)
from agent.turn_controller import (continues_or_asks, delivery_cue_present,
                                   greeting_line_for)
from agent.turn_router import route_decision
from agent.stt_validation import is_repetition_loop
from agent.prompt_fragments import FILLER_LINES, pick_line
from agent.precision_rail import decide as precision_rail_decide


# ---------------------------------------------------------------------------
# 1. Policy + contract construction (verbatim from main.py run_agent_response)
# ---------------------------------------------------------------------------
def build_policy_and_contract(
    *,
    user_text: str,
    turn: dict,
    engine: dict | None,
    sess,
    lcm,
    recent_reply_texts: list,
    detail_mode: dict,
    stuck_nudged: dict,
    log_event: Callable,
    owner_id_fn: Callable = lambda: None,
    schedule_compress: Callable = None,
) -> tuple[dict | None, dict | None]:
    """Build the per-turn policy object + response contract, and apply the
    deterministic per-turn nudges (detail latch, anti-parrot, challenge,
    recovery, reconcile). Returns (previous_response, previous_plan) for
    the stream_prose call — same contract as the extracted block.

    MUTATES: turn (engine_path, owner, policy, detail flags, contract,
    challenge_detected, recovery_mode, reconciles_previous, previous_plan,
    detail_complete), detail_mode, stuck_nudged (via caller's dict).
    """
    prev_response = None
    prev_plan = None
    if not (engine and engine.get("sess")):
        return prev_response, prev_plan

    turn["engine_path"] = ("supervisor" if turn.get("turn_type") == "supervisor_rescue"
                           else "fused")
    turn["owner"] = (owner_id_fn() or "")[:8]
    if user_text and turn.get("turn_type", "speech") == "speech":
        lcm.add_turn("user", user_text)
    if lcm.needs_compression():
        overflow = lcm.get_overflow_turns()
        if overflow:
            prompt = lcm.get_compression_prompt(overflow)
            if schedule_compress is not None:
                schedule_compress(lcm, prompt, overflow)
    turn["policy"] = sess.policy_for_turn()
    # Response Contract (boundaries in code, LLM inside them).
    try:
        _last_claim = None
        if recent_reply_texts:
            _m = SENT_END_RE.search(recent_reply_texts[-1])
            _last_claim = (recent_reply_texts[-1][:_m.end()] if _m
                           else recent_reply_texts[-1])
        _active_topic = ((lcm.get_layer2() or {}).get("active_topic")
                         if lcm else None)
        _contract = build_contract(
            policy=turn["policy"],
            active_topic=_active_topic,
            last_reply=(recent_reply_texts[-1] if recent_reply_texts else None),
            last_claim=_last_claim,
            detail_mode=detail_mode["turns_left"] > 0,
            is_recovery=turn.get("route_action") == "contextual_recovery",
            memory_count=len(sess.memory_view()) if sess else 0,
            route_action=turn.get("route_action"),
        )
        turn["policy"]["contract"] = _contract
    except Exception as e:
        print(f"[Contract] build failed: {e}")

    # SYSTEM-OWNED DETAIL DELIVERY STATE (approved fix ③, 2026-08-31): the
    # model's A-P1 head plan is optional — the SYSTEM owns the multi-part
    # delivery, so a detail request is sustained across turns even when the
    # model never emits a plan and even after the 6-turn latch expires (the
    # reported bug: 'aage' after expiry was treated as a fresh request).
    # Continuation cues ('aage'/'haan'/'bolte jao'/'roko mat'/'phir'...)
    # extend the plan and build a delivery_state resume payload; a
    # non-detail non-continuation turn ends it (conversation moved on).
    # engine["detail"] is session task state — mutated here for BOTH the
    # live path (main.py) and the replay path (run_turn) identically.
    # Non-conversational turns (acoustic_only presence, unclear_speech
    # clarify, supervisor_rescue) never touch the plan: an audio blip or a
    # clarify line must not advance or end an active explanation.
    det = engine.setdefault("detail", {"active": False, "step": 0,
                                       "last_chunk": "", "resume": None})
    if turn.get("turn_type", "speech") == "speech":
        if user_text and is_detail_request(user_text):
            detail_mode["turns_left"] = 6
            det["active"] = True
            det["step"] = max(det["step"], 1)
        elif (user_text and continues_or_asks(user_text)
              and (det["active"] or detail_mode["turns_left"] > 0)):
            # Continuation of an active explanation. Latch renewal is the
            # ORIGINAL verbatim rule (runs whether or not the system detail
            # state is active, so the pre-fix latch path is unchanged); the
            # SYSTEM-owned state additionally advances the step + builds the
            # delivery_state resume payload — and, crucially, keeps
            # continuation alive after the 6-turn latch expires (the reported
            # 'aage' bug).
            _prev_plan2 = engine.get("last_head_plan")
            _plan_done = bool(isinstance(_prev_plan2, dict)
                              and isinstance(_prev_plan2.get("total"), int)
                              and isinstance(_prev_plan2.get("current"), int)
                              and _prev_plan2["current"] >= _prev_plan2["total"])
            if _plan_done:
                turn["detail_complete"] = True
                log_event("DETAIL_PLAN_DONE", turn_id=turn.get("turn"),
                          details={"total": _prev_plan2["total"]})
                print(f"[Detail] plan done (chunk {_prev_plan2['current']}/"
                      f"{_prev_plan2['total']}) — next cue wraps up")
            detail_mode["turns_left"] = max(detail_mode["turns_left"], 1 if _plan_done else 4)
            if det["active"]:
                det["step"] += 1
                turn["detail_continue"] = True
                if det.get("last_chunk"):
                    det["resume"] = {"step": det["step"], "last_chunk": det["last_chunk"]}
        elif det["active"] and detail_mode["turns_left"] <= 0:
            # Detail plan fully idle: the latch has expired AND this turn is
            # neither a detail request nor a continuation — the conversation
            # moved on. (Within the latch window the plan is conservatively
            # kept alive — a soft 'theek hai' must not kill an explanation.)
            det["active"] = False
            det["step"] = 0
            det["resume"] = None
    # Archive the plan's liveness on EVERY turn (incl. gated non-speech
    # turns): an acoustic blip / clarify does not end an active plan, and
    # the replay harness rebuilds engine["detail"] from this key.
    if det["active"]:
        turn["detail_state"] = {"active": True, "step": det["step"]}
    if detail_mode["turns_left"] > 0 and isinstance(turn["policy"], dict):
        detail_mode["turns_left"] -= 1
        turn["detail_mode"] = True
        if delivery_cue_present(user_text):
            turn["policy"]["delivery"] = "continue_detail"
        else:
            turn["policy"]["delivery"] = "chunked_detail"
    # Anti-parrot nudge (application layer, deterministic).
    if int(turn.get("turn", 0)) < stuck_nudged["until_turn"] and isinstance(turn["policy"], dict):
        turn["policy"]["avoid"] = list(turn["policy"].get("avoid") or []) + ["echo_confirm_parroting"]
        turn["policy"]["response_goal"] = "substantive_reaction"
    # Challenge reconciliation (evidence 185741 t20-22).
    if user_text and is_challenge(user_text) and isinstance(turn["policy"], dict):
        turn["policy"]["avoid"] = list(turn["policy"].get("avoid") or []) + ["flip_flop_agreeing"]
        turn["policy"]["response_goal"] = "reconcile_claim"
        turn["challenge_detected"] = True
        log_event("CHALLENGE_DETECTED", turn_id=turn.get("turn"),
                  details={"user_text": user_text[:80]})
        print(f"[ReplyGuard] challenge detected — reconcile-claim nudge active")
    # Recovery turns: bounded — short + checkpoint-oriented.
    if turn.get("route_action") == "contextual_recovery":
        turn["recovery_mode"] = "contextual_recovery"
        if isinstance(turn["policy"], dict):
            turn["policy"]["response_goal"] = "checkpoint_recovery"
            turn["policy"]["avoid"] = list(turn["policy"].get("avoid") or []) + ["long_monologue_on_shaky_transcript"]
    # Response reconciliation (Generated != Spoken != Heard).
    if engine is not None:
        prev_response = response_reconcile_payload(
            engine.pop("last_response", None))
        if prev_response:
            turn["reconciles_previous"] = prev_response
        # A-P1: prior chunk plan -> model advances current+1
        prev_plan = engine.pop("last_head_plan", None)
        if prev_plan:
            turn["previous_plan"] = prev_plan
    return prev_response, prev_plan


# ---------------------------------------------------------------------------
# 2. Per-piece enforcement chain (verbatim from main.py text_stream_tee)
# ---------------------------------------------------------------------------
def process_piece(
    piece: str,
    turn: dict,
    *,
    recent_reply_texts: list,
    user_text: str,
    turn_number: int,
    guard_state: dict | None = None,
    log_event: Callable,
    run_repeat_guard: bool = True,
) -> str:
    """Run the deterministic enforcement chain on one prose piece (the
    exact sequence main.py used inline): tag-strip -> specials -> merged
    words -> hard gate (+ contract events) -> script -> tag-leak event ->
    near-repeat guard (stream body only, not the tail release).

    guard_state: mutable {"guarded": bool, "trim": dict} — the repeat
    guard reads/writes it (sets trim["done"] to stop the rest).

    Returns the final piece to speak. Mutates turn (contract_violations,
    contract_block_count, repeat_guarded, script_transliterated,
    tag_leak_stripped).
    """
    piece, leaked = strip_tag_leak(piece)
    piece = clean_specials(piece)
    piece = fix_merged_words(piece)
    piece, _gv = gate_reply(piece, turn_no=turn_number)
    if _gv:
        turn.setdefault("contract_violations", []).extend(
            [{"type": v["type"], "detail": v["detail"],
              "action": v.get("action", "flag")} for v in _gv])
        for v in _gv:
            if v.get("action") == "block":
                turn["contract_block_count"] = turn.get("contract_block_count", 0) + 1
                log_event("CONTRACT_BLOCKED", turn_id=turn.get("turn"),
                          details={"type": v["type"], "detail": v["detail"],
                                   "stage": "pre_tts"})
            else:
                log_event("CONTRACT_VIOLATION", turn_id=turn.get("turn"),
                          details={"type": v["type"], "detail": v["detail"],
                                   "stage": "pre_tts"})
    # GUARDRAIL: script enforcement — persona says Roman; code enforces it.
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
    # Near-repeat guard (owner: 'it is repeating', 2026-08-30). Stream body
    # only — the tail release (post-stream) never ran it in main.py.
    if run_repeat_guard and guard_state is not None and not guard_state["guarded"]:
        _rep_line, _rep_kind = repeat_break_for(
            piece,
            (recent_reply_texts[-1] if recent_reply_texts else None),
            user_text, turn_number)
        if _rep_line:
            turn["repeat_guarded"] = _rep_kind
            log_event("REPEAT_GUARDED", turn_id=turn.get("turn"),
                      details={"kind": _rep_kind,
                               "prev": (recent_reply_texts[-1] if recent_reply_texts else "")[:60]})
            print(f"[RepeatGuard] near-repeat ({_rep_kind}) -> {piece!r}")
            piece = _rep_line
            guard_state["guarded"] = True
            guard_state["trim"]["done"] = True
    return piece


# ---------------------------------------------------------------------------
# 3. Sentence-release loop (verbatim from main.py text_stream_tee)
# ---------------------------------------------------------------------------
def release_from(trim: dict, chunk: str, *, cap: int) -> str:
    """One stream-chunk step of the sentence-release loop (verbatim from
    main.py's text_stream_tee, Phase-0 Slice-1 extraction 2026-08-30).

    Mutates trim {"pending", "emitted", "done"} in place — the same object
    main.py passes. Rules preserved byte-for-byte:
      - release complete sentences (SENT_END_RE) while emitted stays under cap
      - thin-output guard: if kept-so-far is <50% of cap, FILL the remaining
        budget with a word-boundary cut instead of dropping the sentence
      - pathological single unbroken sentence > cap: cut at a word boundary
        so audio can start at all
    Returns the piece to speak ("" when nothing to release this chunk).
    Piece boundaries are chunking-invariant: feeding the full text in one
    chunk yields the same piece stream as the live incremental tee, so the
    replay harness can feed llm_response_full offline.
    """
    if trim["done"]:
        return ""
    trim["pending"] += chunk
    piece = ""
    while True:
        m = SENT_END_RE.search(trim["pending"])
        if not m:
            break
        sentence = trim["pending"][:m.end()]
        if trim["emitted"] + len(sentence) > cap and trim["emitted"] > 0:
            # THIN-OUTPUT GUARD (evidence 200615 t3: model wrote 167c; first
            # boundary at 16c; rest dropped -> uselessly short reply).
            if trim["emitted"] < cap * 0.5:
                budget = cap - trim["emitted"]
                fill = sentence[:budget]
                sp = fill.rfind(" ")
                if sp > 15:
                    fill = fill[:sp + 1]
                piece += fill
                trim["emitted"] += len(fill)
            trim["done"] = True
            break
        piece += sentence
        trim["pending"] = trim["pending"][m.end():]
        trim["emitted"] += len(sentence)
    # Pathological single unbroken sentence: cut at a word boundary so audio
    # can start at all.
    if not piece and trim["emitted"] == 0 and len(trim["pending"]) > cap:
        cut = trim["pending"][:cap]
        sp = cut.rfind(" ")
        if sp > 40:
            piece = trim["pending"][:sp + 1]
            trim["pending"] = trim["pending"][sp + 1:]
            trim["emitted"] = sp + 1
    return piece


def release_tail(trim: dict, *, cap: int) -> str:
    """Tail release after the stream ends (verbatim from text_stream_tee):
    most replies end without trailing punctuation, so the remaining pending
    is released (word-boundary-capped). Clears trim["pending"]. Returns the
    final piece to speak ("" if nothing left / already done)."""
    if trim["done"] or not trim["pending"]:
        return ""
    piece = trim["pending"]
    if trim["emitted"] + len(piece) > cap and trim["emitted"] > 0:
        cut = piece[:cap - trim["emitted"]]
        sp = cut.rfind(" ")
        piece = cut[:sp].rstrip() if sp > 20 else cut.rstrip()
    trim["pending"] = ""
    return piece


# ---------------------------------------------------------------------------
# 4. run_turn(context) -> turn_dict — the approved §6 unified interface
# ---------------------------------------------------------------------------
@dataclass
class TurnContext:
    """Everything one turn's deterministic critical path needs — injected,
    never imported. The replay harness builds this from an archived turn +
    prior turn state; Slice-2 (after the baseline gate) builds it from live
    runtime objects in main.py. The same code path both ways."""
    turn_no: int
    user_text: str
    turn_type: str = "speech"
    is_valid: bool = True
    rejection_reason: str | None = None
    avg_logprob: float | None = None
    agent_was_speaking: bool = False
    engine_bound: bool = True
    # engine {"sess","lcm","fused",...} — MUTATED in place (last_response /
    # last_head_plan popped by build_policy_and_contract, set at completion),
    # exactly like main.py's closure dict.
    engine: dict | None = None
    recent_reply_texts: list = field(default_factory=list)  # prior replies
    detail_mode: dict = field(default_factory=lambda: {"turns_left": 0})
    stuck_nudged: dict = field(default_factory=lambda: {"until_turn": 0})
    model_text: str = ""          # LLM output; Slice-2 wires an async stream
    head_plan: dict | None = None
    interrupted: bool = False
    played_any_audio: bool = True  # for response_state classification
    acoustic: dict | None = None
    user_speech_start: str | None = None
    user_speech_end: str | None = None
    log_event: Callable = lambda *a, **k: None
    owner_id_fn: Callable = lambda: None
    schedule_compress: Callable | None = None


def run_turn(ctx: TurnContext) -> dict:
    """Execute ONE turn through the deterministic critical path and return the
    turn dict — the same shape main.py's log_turn() archives (session_*.log).

    Wiring order mirrors main.py byte-for-byte: routing (route_decision) ->
    policy+contract+nudges (build_policy_and_contract) -> caps -> release
    (release_from/release_tail) -> enforcement (process_piece) -> completion
    (response_state / engine.last_response / reply_trimmed). Pure: no livekit,
    no I/O; side effects go through injected ctx callbacks + the ctx.engine
    dict. Deterministic: same context -> same turn dict (the replay-identity
    premise).

    Paths: drop (never a substantive reply — CA6), respond_now (acoustic_only
    / unclear_speech), normal response (engine-bound fused), unbound filler
    (engine on, sess None — INCIDENT GUARD, deterministic pick_line), and the
    interrupted completion (PARTIALLY_PLAYED/UNHEARD + reconcile halves).
    """
    turn = {
        "turn": ctx.turn_no,
        "turn_type": ctx.turn_type,
        "acoustic": ctx.acoustic,
        "user_speech_start": ctx.user_speech_start,
        "user_speech_end": ctx.user_speech_end,
        "agent_was_speaking": ctx.agent_was_speaking,
        "stt_transcript": ctx.user_text,
        "stt_valid": ctx.is_valid,
        "stt_rejection_reason": ctx.rejection_reason,
        "stt_avg_logprob": ctx.avg_logprob,
        "response_state": None,
        # Mirrors main.py's per-turn dict init (2026-08-30 real-log gate):
        # these keys are archived on EVERY turn (even dropped / canceled),
        # so run_turn must emit them identically for replay identity.
        "response_trigger_reason": None,
        "interrupted": False,
        "llm_response": None,
    }
    r = route_decision(
        transcript_text=ctx.user_text, is_valid=ctx.is_valid,
        rejection_reason=ctx.rejection_reason, avg_logprob=ctx.avg_logprob,
        is_repetition=is_repetition_loop(ctx.user_text),
        is_catastrophic=(ctx.rejection_reason == "catastrophic_low_confidence"),
        agent_was_speaking=ctx.agent_was_speaking, engine_bound=ctx.engine_bound)
    turn["route_action"] = r["action"]
    turn["route_reason"] = r["reason"]
    if r["drop"]:
        turn["dropped_reason"] = r["drop_reason"]
        return turn
    if r["respond_now"]:
        turn["turn_type"] = r["turn_type"]
        turn["response_trigger_reason"] = r["trigger"]
    if r["recovery"]:
        turn["recovery_mode"] = "contextual_recovery"

    # ---- response path ----
    # PRECISION-DETAIL RAIL (approved fix ①, 2026-08-31): dictated structured
    # details are SYSTEM-owned — echo the STT transcript VERBATIM and confirm
    # with the user, deterministically. The LLM never sees the dictation
    # (build_policy_and_contract, which feeds lcm history, is skipped) and
    # never re-encodes it. Identical call in main.py's run_agent_response.
    rail_active = False
    if ctx.engine is not None:
        _rail = precision_rail_decide(ctx.user_text, ctx.engine, ctx.turn_no)
        if _rail is not None:
            rail_active = True
            turn["engine_path"] = "precision_rail"
            turn["llm_called"] = False
            turn["precise_detail"] = {"action": _rail["action"],
                                      "value": _rail["value"],
                                      "status": _rail["status"]}
            if _rail.get("raw"):
                turn["precise_detail"]["raw"] = _rail["raw"]
    sess_bound = bool(ctx.engine and ctx.engine.get("sess"))
    model_text = ctx.model_text
    # GREETING RAIL (owner smoke 3, 2026-08-31): a first-word greeting gets a
    # deterministic greeting reply, not the LLM's 'bas yahin hoon, bol kya
    # scene hai?' drift. Deterministic on (text, turn_no) — identical in the
    # live path — so replay identity holds. NO sess gate (fix 2026-08-31,
    # owner smokes 4+5: the live t1 'हेलो...' kept running the LLM even
    # though greeting_line_for is verified; sess binding is environment-
    # timing dependent and greeting is deterministic + persona-safe, so a
    # bound session is not required for it).
    greeting = greeting_line_for(ctx.user_text, ctx.turn_no) if ctx.engine is not None else None
    if rail_active:
        if _rail.get("line") is None:
            # SILENT rail decision (accumulating / staying quiet while the
            # user dictates): the turn is suppressed — no speech, no LLM,
            # dictation state already advanced in engine["dictation"].
            turn["response_suppressed"] = True
            return turn
        # deterministic rail line; policy/contract/lcm intentionally skipped
        model_text = _rail["line"]
    elif greeting:
        turn["engine_path"] = "greeting"
        turn["llm_called"] = False
        model_text = greeting
    elif sess_bound:
        turn["engine_path"] = ("supervisor" if ctx.turn_type == "supervisor_rescue"
                               else "fused")
        build_policy_and_contract(
            user_text=ctx.user_text, turn=turn, engine=ctx.engine,
            sess=ctx.engine["sess"], lcm=ctx.engine.get("lcm"),
            recent_reply_texts=ctx.recent_reply_texts,
            detail_mode=ctx.detail_mode, stuck_nudged=ctx.stuck_nudged,
            log_event=ctx.log_event, owner_id_fn=ctx.owner_id_fn,
            schedule_compress=ctx.schedule_compress)
        turn["head_plan"] = ctx.head_plan
    elif ctx.engine is not None:
        # INCIDENT GUARD (2026-08-29): engine on but sess unbound -> the
        # legacy assistant brain is FORBIDDEN; speak a deterministic filler.
        turn["engine_path"] = "unbound_filler"
        turn["llm_called"] = False
        model_text = pick_line(FILLER_LINES, ctx.turn_no)

    # Mirrors main.py line 403 (fused_ref.meta.get("llm_called", True)): the
    # live path archives llm_called=True as soon as the fused LLM produces
    # output; turns canceled before any output (e.g. the UNHEARD t9 in the
    # owner baseline) archive NO llm_called key — kept absent here too.
    # Rail/greeting turns never call the LLM -> llm_called stays False.
    if sess_bound and model_text and not rail_active and not greeting:
        turn["llm_called"] = True

    # caps (mirrors main.py wiring order: computed AFTER policy construction,
    # which decremented the detail latch; A-P1 plan lift at first token).
    caps = {"cap": cap_for(ctx.detail_mode["turns_left"] > 0)}
    # Telemetry-only (Phase-0 2026-08-30): the post-decrement latch value is
    # NOT otherwise archived, and the harness needs it to rebuild the exact
    # cap for detail turns (cap_for(True)=110 vs cap_for(False)=240). No
    # decision-path effect — a logged dict key only.
    turn["detail_latch_after"] = ctx.detail_mode["turns_left"]
    if turn.get("route_action") == "contextual_recovery":
        caps["cap"] = min(caps["cap"], 110)
    plan = turn.get("head_plan")
    if isinstance(plan, dict) and isinstance(plan.get("total"), int) and plan["total"] > 1:
        caps["cap"] = max(caps["cap"], PLAN_CHUNK_CAP)
        ctx.detail_mode["turns_left"] = max(ctx.detail_mode["turns_left"], 3)
    # SYSTEM-OWNED DETAIL PLAN (fix ③): a system-active detail delivery gets
    # the same generous ceiling as a model-announced plan — a chunk is a
    # substantive thought, not 1-2 lines — and the latch is renewed.
    det = (ctx.engine or {}).get("detail")
    if det and det.get("active"):
        caps["cap"] = max(caps["cap"], PLAN_CHUNK_CAP)
        ctx.detail_mode["turns_left"] = max(ctx.detail_mode["turns_left"], 3)

    # release + enforce (chunking-invariant piece stream)
    trim = {"pending": "", "emitted": 0, "done": False}
    guard_state = {"guarded": False, "trim": trim}
    pieces = []
    # REAL-LOG GATE 2026-08-30 (owner baseline t20/t11/t22/t23): the original
    # 17-char slice feed was NOT chunking-invariant. release_from's
    # pathological word-boundary cut fires whenever pending crosses the cap
    # before a sentence boundary arrives (t20: first boundary at 219c > cap
    # 110 -> 17-char feed cut at 110, live released 219), and the piece-level
    # repeat guard fires per release_from CALL, so the piece segmentation —
    # and thus the guard outcome — depends on the chunk feed (live stream
    # chunks split t11's " aage?" into its own piece but coalesced t22/t23's;
    # the archive records neither the chunk sizes nor the pieces). Feeding
    # the FULL text in one call reproduces the live outcome for every turn
    # except the stream-chunk-dependent guard cases (t11), which replay.py
    # downgrades to a documented note rather than a hard diff.
    p = release_from(trim, model_text, cap=caps["cap"])
    if p:
        pieces.append(p)
    tail_trimmed = False
    if not trim["done"] and trim["pending"]:
        if trim["emitted"] + len(trim["pending"]) > caps["cap"] and trim["emitted"] > 0:
            tail_trimmed = True
        t = release_tail(trim, cap=caps["cap"])
        if t:
            pieces.append(t)
    spoken = []
    for p in pieces:
        spoken.append(process_piece(
            p, turn,
            recent_reply_texts=ctx.recent_reply_texts,
            user_text=ctx.user_text,
            turn_number=ctx.turn_no,
            guard_state=guard_state,
            log_event=ctx.log_event,
            # Deterministic rail/greeting lines are SYSTEM-owned — the value
            # is deliberately re-stated for confirmation (echo -> ack -> full
            # echo). The near-repeat guard is for LLM repetition only; it
            # must never rewrite a rail line (smoke-3 bug: the confirm ack
            # got replaced by a repeat-break line).
            run_repeat_guard=not (rail_active or greeting)))
    if model_text:
        turn["llm_response_full"] = model_text
    if spoken:
        turn["llm_response"] = "".join(spoken)
    if trim["done"] or tail_trimmed:
        turn["reply_trimmed"] = True

    # ---- completion (mirrors main.py completion + CancelledError paths) ----
    if ctx.interrupted:
        _spoken = turn["llm_response"] or ""
        _state = response_state_classify(True, ctx.played_any_audio, len(_spoken))
        turn["interrupted"] = True
        # Mirrors main.py:1310 — in the barge-cancel flow the trigger was set
        # to "user_speech_ended" BEFORE the response ran; turns canceled at
        # other points keep whatever routing set (never clobber it).
        if not turn.get("response_trigger_reason"):
            turn["response_trigger_reason"] = "user_speech_ended"
        turn["response_state"] = _state
        turn["heard_text"] = _spoken[:200]
        _remainder = remaining_text(model_text, _spoken)
        turn["remaining_text"] = _remainder[:300]
        if ctx.engine is not None:
            ctx.engine["last_response"] = {
                "status": _state, "turn": ctx.turn_no,
                "heard_text": _spoken, "remaining_text": _remainder}
    else:
        turn["response_state"] = FULLY_PLAYED
        turn["response_trigger_reason"] = "completed"
        if ctx.engine is not None:
            ctx.engine["last_response"] = {
                "status": FULLY_PLAYED, "turn": ctx.turn_no,
                "heard_text": turn["llm_response"]}
            if plan:
                ctx.engine["last_head_plan"] = plan
            # System-owned detail state (fix ③): remember the spoken chunk as
            # the resume point for the next continuation; the per-turn resume
            # payload is rebuilt fresh at the next build — clear it now.
            _det = ctx.engine.get("detail")
            if _det and _det.get("active"):
                if turn.get("llm_response"):
                    _det["last_chunk"] = turn["llm_response"]
                _det.pop("resume", None)
    return turn
