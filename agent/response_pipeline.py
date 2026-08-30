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

main.py calls these instead of the inline blocks. Slice 2 (the async
LLM->TTS playback loop + LiveKit seam) remains in main.py until baseline
logs exist and the replay-identity gate + owner smoke pass.

Pure module: no livekit imports. Callbacks injected for side effects.
"""
from __future__ import annotations

from typing import Callable

from agent.reply_guard import (strip_tag_leak, clean_specials, fix_merged_words,
                               devanagari_present,
                               is_detail_request, is_challenge,
                               SENT_END_RE, repeat_break_for)
from providers.stt import devanagari_to_roman
from agent.response_contract import build_contract, gate_reply
from agent.response_state import reconcile_payload as response_reconcile_payload
from agent.turn_controller import continues_or_asks


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

    # DETAILED MODE latch (verbatim semantics).
    if user_text and is_detail_request(user_text):
        detail_mode["turns_left"] = 6
    elif (detail_mode["turns_left"] > 0 and user_text
          and continues_or_asks(user_text)):
        _prev_plan2 = (engine.get("last_head_plan") if engine else None)
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
    if detail_mode["turns_left"] > 0 and isinstance(turn["policy"], dict):
        detail_mode["turns_left"] -= 1
        turn["detail_mode"] = True
        if user_text and any(w in user_text.lower() for w in
                             ("haan", "aage", "phir", "हाँ", "आगे", "और")):
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
