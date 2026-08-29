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
            state["degraded_turns"] = 0
            _log(log, "DEGRADED-PERCEPTION-ENTER")
        if state["degraded_perception"]:
            state["degraded_turns"] = int(state.get("degraded_turns", 0)) + 1
            if state["degraded_turns"] >= 3:
                # U6 cooldown exit (owner-approved fix): in degraded mode the
                # plain prompt never requests a head, so the success-based
                # exit below can never fire — the original implementation
                # spiraled forever (evidence: 40+ consecutive PARSE-FAILs,
                # session 2026-08-28). After 3 degraded turns, re-enable the
                # fused prompt and let the next turn attempt a real head.
                state["degraded_perception"] = False
                state["parse_fail_streak"] = 0
                state["degraded_turns"] = 0
                _log(log, "DEGRADED-PERCEPTION-EXIT-COOLDOWN")
        policy = _derive_policy(state, turn_record, head=None)
        return state, policy, log

    # ---- Compact head mapping (owner-approved optimization) ----
    # New compact format: {"m":"C|R|U","c":0.8,"s":"SAFE"}
    # Map to existing fields so the rest of the updater is unchanged.
    if "m" in head and "emotion" not in head:
        mode = head["m"]  # C=clear, R=recoverable, U=unclear
        conf = float(head.get("c", 0.5))
        safe = head.get("s", "SAFE")
        # Infer emotion as neutral (compact head doesn't carry emotion detail)
        head["emotion"] = {
            "primary": "neutral_unclear" if mode == "U" else "neutral_unclear",
            "valence": "neutral",
            "intensity": {"ordinal": 2},
            "confidence": conf,
        }
        head["safety"] = {
            "risk_level": "elevated_distress" if safe == "UNSAFE" else "none",
            "self_harm": safe == "UNSAFE",
            "harm_to_others": False,
            "other_flagged": safe == "UNSAFE",
            "confidence": conf,
        }
        head["thread"] = {"action": "continue", "gist": "", "entities": []}
        head["user_need"] = "be_heard"
        head["advice_requested"] = False
        head["memory_candidates"] = []

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
        state["degraded_turns"] = 0
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
