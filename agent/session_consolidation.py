"""Session-End Consolidation V1 (Phase B) — see docs/SESSION_END_CONSOLIDATION_V1.md.

Locked pipeline:
    LLM (proposal, untrusted)
      -> candidate (schema-validated; criterion is ALWAYS "salient")
      -> evidence/anchor validation  (fail -> QUARANTINE, degenerate -> REJECT)
      -> MemoryGate (gate_candidate — last line of defense, unchanged)
      -> pending / quarantined
      -> later confirmation (DETERMINISTIC repeat sighting ONLY) -> commit

Locked principles (doc §2):
- LLM output is ALWAYS a candidate, never trusted memory.
- The LLM cannot set criterion/status: those keys are STRIPPED if present; the
  pipeline hardcodes criterion="salient" for every LLM bullet. Any other key
  outside the whitelist rejects the whole pass (strict fail > partial trust).
- LLM proposals never confirm LLM proposals and never bump `occurrences`.
- Numbers/PII are redacted before the prompt ([REDACTED]); saved numbers never
  enter the prompt and "saved_number" is not a valid bullet type.
- Previous-session memory is never input to the prompt (per-content store
  lookups for dedupe are the only store reads, and they never reach the LLM).
- The pass is purely additive: on any LLM failure/timeout/invalid JSON the
  deterministic captures are untouched and end_session() still runs.
- Bounded: one call, no retry, budget enforced by consolidate_bounded().

Phase-B footprint (locked): this module + MemoryStore.lookup/quarantine
(read/insert helpers) + main.py _commit_session_memory wiring + 10 suites.
No changes to: precision_rail, conversation_controller, fused_turn,
state_updater, prompt_fragments, memory_gate behavior, response path,
retrieval/indexing, state_delta_compiler.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone

from agent.entity_extractor import USER_STOPWORDS
from agent.memory_gate import gate_candidate

CONSOLIDATION_BUDGET_S = 15.0
CONSOLIDATION_MODEL = "gemini-3.5-flash-lite"
CONSOLIDATION_TEMPERATURE = 0.3
REDACT = "[REDACTED]"
MAX_BULLETS = 8
MAX_TURNS = 25
MAX_PROMPT_CHARS = 3000
CONTENT_MIN = 6
CONTENT_MAX = 200
ALLOWED_TYPES = ("preference", "relationship", "episodic", "semantic")
CONFIDENCES = ("low", "med", "high")
# The pipeline owns criterion/status — the LLM cannot. If the model echoes
# them (schema-injection attempt), they are STRIPPED, never honored.
_PIPELINE_OWNED_KEYS = ("criterion", "status")
_BULLET_KEYS = frozenset(("type", "content", "turn_ref", "confidence")) | frozenset(_PIPELINE_OWNED_KEYS)
_TOP_KEYS = frozenset(("bullets", "nothing_important_missed")) | frozenset(_PIPELINE_OWNED_KEYS)

_WORD_RE = re.compile(r"[\w\u0900-\u097F]{2,}", re.UNICODE)
_ASCII_DIGITS = re.compile(r"\d{4,}")
_DEV_DIGITS = re.compile(r"[\u0966-\u096F]{4,}")
_DIGIT_SEP_PUNCT = re.compile(r"(\d)\s*[.\-,–]+\s*(?=\d)")
_DIGIT_SEP_SPACE = re.compile(r"(\d)\s+(?=\d)")

_QUESTION_RE = re.compile(
    r"क्या|कौन|कौनसा|कौन सा|कहाँ|कहां|कब|कैसे|क्यों|why|how|where|when|what|which",
    re.IGNORECASE)
_FIRST_PERSON_RE = re.compile(
    r"मैं|मुझे|मेरा|मेरी|मेरे|मैंने|मुझको|मुझसे|\bI\b|\bmy\b|\bme\b|\bmine\b|i'",
    re.IGNORECASE)
_TRAVEL_RE = re.compile(
    r"गया|गई|गए|गये|गयी|घूम|रहता|रहती|रहते|जाता|जाती|जाते|आया|आई|आए|बसता|"
    r"से हूं|से हूँ|went|visit|visited|trip|tour|travel|travell|live|lives|lived|"
    r"stayed|vacation",
    re.IGNORECASE)

_SAVED_NUMBER_MARKER = "user's saved number (digits redacted)"


# ---------------------------------------------------------------------------
# PII redaction
# ---------------------------------------------------------------------------
def redact_pii(text: str) -> str:
    """Replace any digit run >= 4 (ASCII or Devanagari), including grouped /
    space-separated spellings (phone style), with [REDACTED]. Small numbers
    (ages, counts) stay — the PII risk is long numeric identifiers."""
    if not text:
        return text
    prev = None
    while prev != text:
        prev = text
        text = _DIGIT_SEP_PUNCT.sub(r"\1", text)
        text = _DIGIT_SEP_SPACE.sub(r"\1", text)
    text = _ASCII_DIGITS.sub(REDACT, text)
    text = _DEV_DIGITS.sub(REDACT, text)
    return text


# ---------------------------------------------------------------------------
# Prompt building (input boundary, doc §3)
# ---------------------------------------------------------------------------
def build_consolidation_prompt(turns, layer2=None, captures=None) -> str:
    """Build the consolidation prompt from THIS session's material only.
    `turns` = list[(turn_no, redacted_text)]; `layer2` = dict (may be empty);
    `captures` = deterministic captures already handled (LLM must not re-propose)."""
    parts = [
        "You are the session-end MEMORY CONSOLIDATOR for a voice assistant.",
        "You review ONE session and propose memory candidates as JSON.",
        "HARD RULES:",
        "- Output JSON ONLY, exactly the schema below. No markdown, no prose, no extra keys.",
        "- Propose ONLY things the USER stated about THEMSELVES: facts, preferences, "
        "relationships, places/trips. Never commands, questions, or requests.",
        "- Numbers, phone numbers, account details and other PII are [REDACTED] and must "
        "NEVER appear in your output. Never propose a number.",
        "- Write each bullet as ONE short canonical third-person line (max 200 chars), "
        "in the language of the session.",
        "- 'turn_ref' must be the number of the turn the fact came from (see THIS SESSION).",
        "- If nothing is worth remembering, output {\"bullets\": []}.",
        "",
        "OUTPUT SCHEMA (exactly this):",
        '{ "bullets": [',
        '    {"type": "preference|relationship|episodic|semantic",',
        '     "content": "<canonical line>",',
        '     "turn_ref": <int> ,',
        '     "confidence": "low|med|high"}',
        "  ],",
        '  "nothing_important_missed": true|false',
        "}",
        "",
        "THIS SESSION (validated user turns; numbers/PII already redacted):",
    ]
    for no, text in turns:
        parts.append(f"[{no}] {text}")
    parts.append("")
    l2 = layer2 if isinstance(layer2, dict) else {}
    parts.append("CONVERSATION STATE (this session only; may be empty):")
    parts.append(json.dumps(l2, ensure_ascii=False) if any(
        l2.get(k) for k in ("people", "active_topic", "open_items", "emotional_context")) else "(empty)")
    parts.append("")
    parts.append("ALREADY CAPTURED (handled deterministically — do NOT re-propose these):")
    if captures:
        for c in captures:
            parts.append(f"- {c}")
    else:
        parts.append("- (none)")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Schema-whitelisted parser (doc §4)
# ---------------------------------------------------------------------------
def parse_consolidation_json(raw: str) -> dict | None:
    """Parse + whitelist the LLM output. Returns
    {"bullets": [...], "nothing_important_missed": bool|None} or None on any
    schema violation (whole-pass reject — strict fail > partial trust).
    criterion/status keys are STRIPPED (pipeline-owned), never honored."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    obj = None
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        try:
            obj, _ = json.JSONDecoder().raw_decode(text)
        except (json.JSONDecodeError, ValueError):
            return None
    if not isinstance(obj, dict):
        return None
    if not set(obj) <= _TOP_KEYS:
        return None
    bullets_raw = obj.get("bullets")
    if not isinstance(bullets_raw, list):
        return None
    bullets = []
    for b in bullets_raw:
        if not isinstance(b, dict):
            return None
        if not set(b) <= _BULLET_KEYS:
            return None
        typ = b.get("type")
        content = b.get("content")
        turn_ref = b.get("turn_ref")
        confidence = b.get("confidence")
        if typ not in ALLOWED_TYPES:
            return None
        if not isinstance(content, str) or not (CONTENT_MIN <= len(content) <= CONTENT_MAX):
            return None
        if re.search(r"\d", content) or REDACT in content:
            return None
        if not isinstance(turn_ref, int) or isinstance(turn_ref, bool):
            return None
        if confidence not in CONFIDENCES:
            return None
        # criterion/status keys are dropped here — the pipeline owns them.
        bullets.append({"type": typ, "content": content.strip(),
                        "turn_ref": turn_ref, "confidence": confidence})
    nim = obj.get("nothing_important_missed")
    if nim is not None and not isinstance(nim, bool):
        nim = None  # telemetry only; unparsed is logged
    return {"bullets": bullets, "nothing_important_missed": nim}


# ---------------------------------------------------------------------------
# Evidence / anchor validation (doc §5) — deterministic, before MemoryGate
# ---------------------------------------------------------------------------
def _tokens(text: str) -> set:
    return {w for w in _WORD_RE.findall(text.lower()) if w not in USER_STOPWORDS}


# Canonical-format artifact ("user: ...", "user को ...") — not a content word.
# Excluded from anchor overlap and the completeness diff so it can never
# create a false anchor/coverage match on its own.
_CANON_STOP = frozenset({"user"})


def _content_tokens(text: str) -> set:
    return _tokens(text) - _CANON_STOP


def _looks_like_name(word: str) -> bool:
    return len(word) >= 2 and any(c.isalpha() for c in word) and word not in USER_STOPWORDS


def validate_anchors(bullets: list[dict], turns: dict) -> tuple[list, list]:
    """Return (anchored, unanchored). A bullet is unanchored when its
    turn_ref is missing from the session, or no content word appears in the
    referenced turn, or the type-specific consistency check fails."""
    anchored, unanchored = [], []
    for b in bullets:
        text = turns.get(b.get("turn_ref")) if turns else None
        if not text:
            unanchored.append(b)
            continue
        overlap = _content_tokens(b["content"]) & _content_tokens(text)
        ok = bool(overlap)
        typ = b["type"]
        if ok and typ == "relationship":
            # the overlapping word(s) must look like a name/relation mention
            # (MemoryGate R2 name checks remain the backstop)
            ok = any(_looks_like_name(w) for w in overlap)
        elif ok and typ == "episodic":
            ok = bool(_TRAVEL_RE.search(text))
        elif ok and typ in ("preference", "semantic"):
            ok = bool(_FIRST_PERSON_RE.search(text)) and not _QUESTION_RE.search(text)
        (anchored if ok else unanchored).append(b)
    return anchored, unanchored


# ---------------------------------------------------------------------------
# Completeness diff (doc §8) — read-only; telemetry, never authority
# ---------------------------------------------------------------------------
def completeness_diff(captures: list[str], bullet_contents: list[str]) -> dict:
    """D (deterministic captures) minus B (bullet contents the LLM proposed).
    Saved-number markers are covered-by-design (numbers never enter the pass)
    and are excluded from not_covered noise."""
    covered, not_covered = [], []
    for c in captures or []:
        if c.startswith(_SAVED_NUMBER_MARKER):
            covered.append(c)
            continue
        ct = _content_tokens(c)
        hit = any(ct & _content_tokens(b) for b in bullet_contents or [])
        (covered if hit else not_covered).append(c)
    return {"covered": covered, "not_covered": not_covered}


# ---------------------------------------------------------------------------
# Session-log readers (the wiring's deterministic side)
# ---------------------------------------------------------------------------
def collect_validated_turns(log_path: str, max_turns: int = MAX_TURNS,
                            max_chars: int = MAX_PROMPT_CHARS) -> list:
    """Read this session's turn log and return [(turn_no, text)] for turns
    that qualify as §3(1) input: valid, not echo/dropped/suppressed, not
    rail-owned, not greeting, turn_type speech. Oldest-first, capped."""
    out = []
    try:
        with open(log_path) as f:
            for line in f:
                try:
                    t = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(t, dict) or not isinstance(t.get("turn"), int):
                    continue
                text = t.get("stt_transcript")
                if not isinstance(text, str) or not text.strip():
                    continue
                if t.get("stt_valid") is not True:
                    continue
                if t.get("echo_dropped") or t.get("dropped_reason"):
                    continue
                if t.get("response_suppressed") or t.get("suppression_reason"):
                    continue
                if t.get("precise_detail") or t.get("engine_path") == "precision_rail":
                    continue
                if t.get("engine_path") == "greeting":
                    continue
                if t.get("turn_type") not in (None, "speech"):
                    continue
                out.append((t["turn"], text.strip()))
    except FileNotFoundError:
        return []
    out = out[-max_turns:]
    total, kept = 0, []
    for no, x in reversed(out):
        total += len(x) + 8
        if total > max_chars:
            break
        kept.append((no, x))
    return list(reversed(kept))


def collect_deterministic_captures(log_path: str) -> list:
    """This session's deterministic captures, reconstructed from the turn log:
    place facts, fact candidates, relationships, saved-number confirms
    (digits redacted). Used for dedupe + the completeness diff."""
    caps = []
    if not log_path:
        return caps
    try:
        with open(log_path) as f:
            for line in f:
                try:
                    t = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(t, dict):
                    continue
                for pf in t.get("place_facts") or []:
                    if isinstance(pf, str):
                        caps.append(pf)
                for fc in t.get("fact_candidates") or []:
                    if isinstance(fc, str):
                        caps.append(fc)
                for rel in t.get("user_relations") or []:
                    if isinstance(rel, dict) and rel.get("name") and rel.get("relation"):
                        caps.append(f"{rel['name']} — user's {rel['relation']}")
                pd = t.get("precise_detail")
                if isinstance(pd, dict):
                    v = pd.get("value")
                    if isinstance(v, str) and v.isdigit() and len(v) >= 6:
                        caps.append(_SAVED_NUMBER_MARKER)
    except FileNotFoundError:
        return []
    return caps


# ---------------------------------------------------------------------------
# Default LLM call (production) — same model/budget discipline as L2 compression
# ---------------------------------------------------------------------------
def _default_gemini_call(prompt: str) -> str:
    key = os.getenv("GEMINI_API_KEY", "")
    if not key or key.startswith(("your_", "<<<")):
        raise RuntimeError("no GEMINI_API_KEY configured")
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=key)
    config = types.GenerateContentConfig(temperature=CONSOLIDATION_TEMPERATURE)
    resp = client.models.generate_content(model=CONSOLIDATION_MODEL,
                                          contents=prompt, config=config)
    return (resp.text or "").strip()


# ---------------------------------------------------------------------------
# Orchestrator (sync core — fully unit-testable; bounding lives in
# consolidate_bounded so worker teardown is never blocked)
# ---------------------------------------------------------------------------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _log_line(log, base: dict) -> None:
    nim = base.get("nothing_missed")
    log(
        "[SessionConsolidation] "
        f"owner={base.get('owner')} "
        f"bullets_proposed={base.get('bullets_proposed')} "
        f"anchored={base.get('anchored')} "
        f"quarantined={base.get('unanchored_quarantined')} "
        f"rejected={base.get('rejected')} "
        f"deduped={base.get('deduped')} "
        f"pending={base.get('pending')} "
        f"nothing_missed={'unparsed' if nim is None else str(nim)} "
        f"L2_state={base.get('l2_state')} "
        f"status={base.get('status')} reason={base.get('reason') or '-'} "
        f"duration_ms={base.get('duration_ms')}"
    )


def _write_sinks(base: dict, session_log_path: str | None,
                 state_log_path: str | None, log) -> None:
    rec = {"timestamp": datetime.now(timezone.utc).isoformat(),
           "event": "SESSION_CONSOLIDATION", **base}
    for path in (session_log_path, state_log_path):
        if not path:
            continue
        try:
            with open(path, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            log(f"[SessionConsolidation] log write failed ({path}): {e}")


def consolidate(*, owner_id: str, store, session_log_path: str | None = None,
                state_log_path: str | None = None, session_turns=None,
                captures=None, layer2=None, llm_call=None, log=print,
                now=None, max_turns: int = MAX_TURNS,
                max_chars: int = MAX_PROMPT_CHARS, max_bullets: int = MAX_BULLETS) -> dict:
    """Run the session-end consolidation pass (additive, best-effort).

    Returns a summary dict; never raises for LLM/parse failures (they are
    'failed' summaries). The deterministic memory path is untouched either way.
    """
    t0 = (now() if now else time.monotonic())

    def dur() -> int:
        return int(((now() if now else time.monotonic()) - t0) * 1000)

    base = {"owner": (owner_id or "?")[:8], "turns": 0, "l2_state": "empty",
            "bullets_proposed": 0, "anchored": 0, "unanchored_quarantined": 0,
            "rejected": 0, "deduped": 0, "pending": 0, "nothing_missed": None,
            "diff": {"covered": [], "not_covered": []}}
    try:
        if session_turns is None:
            session_turns = (collect_validated_turns(session_log_path, max_turns, max_chars)
                             if session_log_path else [])
        if captures is None:
            captures = collect_deterministic_captures(session_log_path) if session_log_path else []
        # PII redaction is the LAST gate before the LLM sees anything.
        turns = [(no, redact_pii(text)) for no, text in session_turns]
        turns = turns[-max_turns:]
        total, kept = 0, []
        for no, text in reversed(turns):
            total += len(text) + 8
            if total > max_chars:
                break
            kept.append((no, text))
        kept = list(reversed(kept))
        if not kept:
            base.update(status="skipped", reason="no_turns", duration_ms=dur())
            _log_line(log, base)
            return base

        l2 = layer2 if isinstance(layer2, dict) else {}
        l2_state = ("present" if any(
            l2.get(k) for k in ("people", "active_topic", "open_items", "emotional_context"))
            else "empty")
        base["turns"] = len(kept)
        base["l2_state"] = l2_state

        prompt = build_consolidation_prompt(kept, l2, captures)
        fn = llm_call or _default_gemini_call
        raw = fn(prompt)  # may raise -> caught below (additive-only)

        parsed = parse_consolidation_json(raw)
        if parsed is None:
            base.update(status="failed", reason="invalid_json", duration_ms=dur())
            _log_line(log, base)
            _write_sinks(base, session_log_path, state_log_path, log)
            return base

        bullets = parsed["bullets"][:max_bullets]
        base["bullets_proposed"] = len(bullets)
        base["nothing_missed"] = parsed.get("nothing_important_missed")

        anchored, unanchored = validate_anchors(bullets, dict(kept))
        base["anchored"] = len(anchored)
        for b in unanchored:
            store.quarantine(owner_id, {"type": b["type"], "content": b["content"]})
            base["unanchored_quarantined"] += 1
            log(f"[SessionConsolidation] bullet type={b['type']} status=quarantine "
                f"turn_ref={b['turn_ref']} content={b['content'][:40]!r}")

        seen: set = set()
        cap_norms = {_norm(c) for c in (captures or [])}
        for b in anchored:
            typ, content = b["type"], b["content"]
            key = (typ, _norm(content))
            if key in seen or _norm(content) in cap_norms:
                base["deduped"] += 1
                status = "dedupe"
            elif store.lookup(owner_id, content) is not None:
                base["deduped"] += 1
                status = "dedupe"
            else:
                seen.add(key)
                cand = {"type": typ, "content": content, "criterion": "salient"}
                # Every write still routes through MemoryGate (last line of
                # defense — unchanged). We re-query for the ground-truth
                # status (reject rows never touch the DB).
                store.commit(owner_id, cand, immediate=False)
                row = store.lookup(owner_id, content)
                st = row["status"] if row else "reject"
                status = st
                if st == "pending":
                    base["pending"] += 1
                elif st == "quarantined":
                    base["unanchored_quarantined"] += 1
                else:
                    base["rejected"] += 1
            log(f"[SessionConsolidation] bullet type={typ} status={status} "
                f"turn_ref={b['turn_ref']} content={content[:40]!r}")

        proposed = [b["content"] for b in bullets]
        base["diff"] = completeness_diff(captures or [], proposed)
        if parsed.get("nothing_important_missed") is True and base["diff"]["not_covered"]:
            log(f"[SessionConsolidation] coverage warning: LLM claims nothing missed "
                f"but {len(base['diff']['not_covered'])} deterministic capture(s) not covered")
        for c in base["diff"]["not_covered"]:
            log(f"[SessionConsolidation] diff not_covered: {c[:60]!r}")

        base.update(status="ok", duration_ms=dur())
        _log_line(log, base)
        _write_sinks(base, session_log_path, state_log_path, log)
        return base
    except Exception as e:
        base.update(status="failed",
                    reason=f"{type(e).__name__}: {str(e)[:120]}",
                    duration_ms=dur())
        _log_line(log, base)
        _write_sinks(base, session_log_path, state_log_path, log)
        return base


async def consolidate_bounded(*, timeout: float = CONSOLIDATION_BUDGET_S, **kw) -> dict:
    """Run consolidate() inside a hard time budget. The worker's shutdown
    path awaits this; a stalling LLM is cancelled at `timeout` and teardown
    proceeds (acceptance test 10). One attempt — no retry loop."""
    return await asyncio.wait_for(asyncio.to_thread(consolidate, **kw), timeout=timeout)
