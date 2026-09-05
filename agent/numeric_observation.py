"""Numeric Observation Boundary — the observation RECORD (Phase 1).

docs/NUMERIC_OBSERVATION_LOCK.md (owner-approved 2026-09-04), Q11 build
order step (i): produce one `NumericObservation` per turn and archive it.
NOTHING consumes it yet — the rail's Signals still come from
precision_rail's detectors byte-for-byte. This module only OBSERVES the
transcript so the audit chain

    STT -> observation -> operation -> proposal -> delivery -> confirm -> commit

is inspectable per turn (lock §11). Phase 3 rewires Signals to read it.

Contract (lock §1, N7)
  observe(text, turn_no) is a PURE function of the transcript text: it reads
  no task state, no engine, no history; it never decides append/replace/
  anything; the record is JSON-normal (lists/dicts/str/int/float/None only),
  versioned, and written ONCE per turn (attach_observation).

What the record says (lock §2)
  items[]            contiguous numeric spans, each a list of SLOTS — one
                     entry per digit POSITION with kind DIGIT | UNKNOWN |
                     AMBIGUOUS and a provenance
  non_numeric_tokens everything outside the items (the instruction layer's
                     input: cues, confirm/reject words, function words)
  certainty          EMPTY | COMPLETE | INCOMPLETE  (COMPLETE = every slot is
                     syntactically a digit; it is NOT "correct" — a confidently
                     wrong STT digit is a DIGIT slot, second look's problem)
  surface / group_breaks  descriptive ONLY (N3) — no rule may branch on them

Structural rules (no new lexicon — every vocabulary below is imported from
the shipped detectors; the version hash pins them):
  N1  an unreadable token BETWEEN two readable numeric tokens (<= OOV_MAX_RUN
      of them, each <= OOV_MAX_LEN chars, none a known function/instruction
      word) is an UNKNOWN slot — the item is kept, certainty INCOMPLETE.
      At an item EDGE only a near-miss of a lexicon digit word (edit
      distance 1, both >= 3 chars) is captured, and only when the item
      already has >= 2 readable tokens (OOV_EDGE). Letters glued to a digit
      run ('026900abc') are an UNKNOWN slot with reason MIXED_SCRIPT_RUN.
  N4  a count candidate (literal 1-9, or a Hindi cardinal 1-9) immediately
      followed by a ZERO token (zero word, or literal '0' when the count is
      a word) with no explicit multiplier/double is ONE AMBIGUOUS slot with
      alternatives {digit+0 | count zeros}. Scope pin: zero groups only —
      English-phonetic digit words (वन/टू/फाइव...) are never counts, and two
      literals ('5 0') are a rendered digit sequence (N3 property).
  N5  cues (आगे/पूरा/नहीं/...) and function words never enter slots.
  Lone-word pin: a single digit WORD embedded among other words ('समझा तू?',
      'एक interview') is NOT asserted as numeric content (Hindi cardinals and
      the phonetic English words double as function words); a lone LITERAL,
      a DOUBLE/TIMES-bound word, >= 2 readable tokens, or a pure utterance
      ('जीरो') is.
  Multiplier syntax mirrors normalize_span: single-digit count + बार/x/times
      + single digit -> MULTIPLIER slots; 'डबल' + single digit -> DOUBLE
      slots. A count/double whose target is a multi-digit run is UNBOUND
      (INCOMPLETE); a dangling 'एक बार' with no target is adverbial ('once')
      and stays non-numeric.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

from agent.precision_rail import (
    DIGIT_WORD_MAP, DOUBLE_WORDS, TIMES_WORDS, _SPAN_CONNECTORS,
    CONFIRM_DEV_WORDS, REJECT_DEV_WORDS,
    RESTART_RE, CONTINUE_CUE_RE, _CHANGE_FRAME_RE, ANNOUNCE_RE, WRITE_INTENT_RE,
    DICTATION_KEYWORDS, ONLY_THIS_RE, QUESTIONISH_RE, ABANDON_RE, DEARM_DETAIL_RE,
    RECALL_RE, STATUS_RE, TOPIC_SWITCH_RE, CONFIRM_EN_RE, REJECT_EN_RE,
    WRITE_COMMAND_RE, _NUMBER_TOPIC_RE, CLAIM_RE, COMPLAINT_RE,
)
from agent.entity_extractor import USER_STOPWORDS

# ---------------------------------------------------------------------------
# Constants (declared pins)
# ---------------------------------------------------------------------------
RULES_VERSION = "obs-1.0"
OOV_MAX_LEN = 6      # lock §3: digit-word SHAPE = Devanagari/Latin token of <= 6 chars
OOV_MAX_RUN = 2      # consecutive unreadable tokens that may bridge two readable ones
EMPTY, COMPLETE, INCOMPLETE = "EMPTY", "COMPLETE", "INCOMPLETE"
DIGIT, UNKNOWN, AMBIGUOUS = "DIGIT", "UNKNOWN", "AMBIGUOUS"
_UNCERTAIN_REASONS = ("UNBOUND_MULTIPLIER", "UNBOUND_DOUBLE", "MIXED_SCRIPT_RUN")

# Hindi cardinals 1-9 — the words that can be COUNTS ('पांच जीरो' = five
# zeros). A subset of the shipped lexicon (test-pinned); no new entries.
_HINDI_COUNT_WORDS = frozenset({"एक", "दो", "तीन", "चार", "पांच", "पाँच",
                                "छह", "छै", "छे", "सात", "आठ", "नौ"})
_ZERO_WORDS = frozenset(w for w, d in DIGIT_WORD_MAP.items() if d == "0")

# Known NON-numeric vocabulary = the function/instruction words the shipped
# detectors already know (stopwords, connectors, confirm/reject words, and
# every plain word inside the cue/intent patterns — 'फिर से' contributes
# 'फिर' and 'से'). Used only to (a) break an OOV bridge and (b) keep
# instruction words out of edge capture. Derived, never extended here.
KNOWN_WORD_PATTERNS = (
    RESTART_RE, CONTINUE_CUE_RE, _CHANGE_FRAME_RE, ANNOUNCE_RE, WRITE_INTENT_RE,
    DICTATION_KEYWORDS, ONLY_THIS_RE, QUESTIONISH_RE, ABANDON_RE, DEARM_DETAIL_RE,
    RECALL_RE, STATUS_RE, TOPIC_SWITCH_RE, CONFIRM_EN_RE, REJECT_EN_RE,
    WRITE_COMMAND_RE, _NUMBER_TOPIC_RE, CLAIM_RE, COMPLAINT_RE,
)
_PLAIN_ALT_RE = re.compile(r"^[\w\u0900-\u097F' ]+$")


def _pattern_words(patterns) -> frozenset:
    """Plain words of the literal alternatives of the detector regexes
    (regex-syntax alternatives are skipped — they are matched by fullmatch)."""
    out: set[str] = set()
    for p in patterns:
        body = p.pattern.replace("\\b", "").replace("(?:", "(")
        for alt in re.split(r"\||\(|\)", body):
            alt = alt.strip()
            if alt and _PLAIN_ALT_RE.match(alt):
                for w in alt.split():
                    if len(w) >= 2:
                        out.add(w.lower())
    return frozenset(out)


KNOWN_WORD_SET = frozenset(
    {w.lower() for w in USER_STOPWORDS} | set(_SPAN_CONNECTORS)
    | set(CONFIRM_DEV_WORDS) | set(REJECT_DEV_WORDS)) | _pattern_words(KNOWN_WORD_PATTERNS)

# Devanagari digits U+0966-096F are matched by \d (placed first); the letter
# class excludes them (and the dandas U+0964/0965) so a Devanagari digit run
# is a LITERAL token and punctuation never glues onto a word.
_TOKEN_RE = re.compile(r"\d+|[\u0900-\u0963\u0970-\u097F]+|[A-Za-z]+|×")
_PUNCT_RE = re.compile(r"[,.;:\-–—/|]")
_NEAR_MISS_WORDS = tuple(w for w in DIGIT_WORD_MAP if len(w) >= 3)


def lexicon_hash(*parts) -> str:
    """Stable 8-hex digest of the vocabularies the observation depends on."""
    h = hashlib.sha1()
    for p in parts:
        if isinstance(p, dict):
            items = sorted(f"{k}={v}" for k, v in p.items())
        elif isinstance(p, (set, frozenset)):
            items = sorted(str(x) for x in p)
        else:
            items = [getattr(x, "pattern", str(x)) for x in p]
        h.update(("\x1f".join(items) + "\x1e").encode("utf-8"))
    return h.hexdigest()[:8]


LEXICON_PARTS = (DIGIT_WORD_MAP, DOUBLE_WORDS, TIMES_WORDS, KNOWN_WORD_SET,
                 KNOWN_WORD_PATTERNS, _HINDI_COUNT_WORDS)
VERSION = f"{RULES_VERSION}+lex-{lexicon_hash(*LEXICON_PARTS)}"


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
LIT, WORD, TIMES, DOUBLE, KNOWN, OTHER = "LIT", "WORD", "TIMES", "DOUBLE", "KNOWN", "OTHER"


class _Tok:
    __slots__ = ("text", "start", "end", "cls", "digits", "idx")

    def __init__(self, text, start, end, cls, digits, idx):
        self.text, self.start, self.end, self.cls, self.digits, self.idx = \
            text, start, end, cls, digits, idx

    def ref(self) -> dict:
        return {"text": self.text, "start": self.start, "end": self.end}


def _ascii_digits(run: str) -> str:
    return "".join(str(unicodedata.digit(ch)) for ch in run)


def _is_known(low: str) -> bool:
    """Exact-word membership (never substring search: 'घट' inside 'घटटड'
    must not make a garbled token a 'known' word)."""
    if low in KNOWN_WORD_SET:
        return True
    return any(p.fullmatch(low) for p in KNOWN_WORD_PATTERNS)


def _tokenize(text: str) -> list[_Tok]:
    toks: list[_Tok] = []
    for m in _TOKEN_RE.finditer(text or ""):
        raw = m.group(0)
        low = raw.lower()
        if raw[0].isdigit():
            toks.append(_Tok(raw, m.start(), m.end(), LIT, _ascii_digits(raw), len(toks)))
            continue
        d = DIGIT_WORD_MAP.get(raw) or DIGIT_WORD_MAP.get(low)
        if d is not None:
            cls, digits = WORD, d
        elif low in TIMES_WORDS:
            cls, digits = TIMES, None
        elif low in DOUBLE_WORDS:
            cls, digits = DOUBLE, None
        elif _is_known(low):
            cls, digits = KNOWN, None
        else:
            cls, digits = OTHER, None
        toks.append(_Tok(raw, m.start(), m.end(), cls, digits, len(toks)))
    return toks


def _edit_distance_le1(a: str, b: str) -> bool:
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(1 for x, y in zip(a, b) if x != y) == 1
    if len(a) > len(b):
        a, b = b, a
    i = j = 0
    skipped = False
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1; j += 1
        elif skipped:
            return False
        else:
            skipped = True; j += 1
    return True


def _near_miss(tok: _Tok) -> bool:
    if len(tok.text) < 3 or len(tok.text) > OOV_MAX_LEN:
        return False
    low = tok.text.lower()
    return any(_edit_distance_le1(low, w) for w in _NEAR_MISS_WORDS)


def _oov_candidate(tok: _Tok) -> bool:
    return tok.cls == OTHER and len(tok.text) <= OOV_MAX_LEN


def _glued_to_lit(tok: _Tok, toks: list[_Tok]) -> bool:
    """A letter token with ZERO gap to a neighbouring literal digit run
    ('026900abc') — used for tokens BETWEEN two readable tokens."""
    if tok.cls not in (OTHER, KNOWN):
        return False
    prev = toks[tok.idx - 1] if tok.idx > 0 else None
    nxt = toks[tok.idx + 1] if tok.idx + 1 < len(toks) else None
    return bool((prev is not None and prev.cls == LIT and prev.end == tok.start)
                or (nxt is not None and nxt.cls == LIT and tok.end == nxt.start))


def _glued_pair(letters: _Tok, lit: _Tok) -> bool:
    """`letters` touches `lit` with zero gap and `lit` is a literal run."""
    return (letters.cls in (OTHER, KNOWN) and lit.cls == LIT
            and (letters.end == lit.start or lit.end == letters.start))


# ---------------------------------------------------------------------------
# Items — contiguous token ranges
# ---------------------------------------------------------------------------
def _bridge_ok(between: list[_Tok], toks: list[_Tok]) -> bool:
    """May the tokens strictly between two readable numeric tokens sit inside
    one item?  Empty; or all multiplier syntax (TIMES/DOUBLE); or an OOV run
    (N1) — never a known function/instruction word, never a mix."""
    if not between:
        return True
    if all(t.cls in (TIMES, DOUBLE) for t in between):
        return True
    if len(between) <= OOV_MAX_RUN and all(
            _oov_candidate(t) or _glued_to_lit(t, toks) for t in between):
        return True
    return False


def _group_readable(toks: list[_Tok]) -> list[tuple[int, int]]:
    """(first_idx, last_idx) ranges over READABLE tokens joined by allowed
    bridges. Edges are extended afterwards."""
    readable = [t for t in toks if t.cls in (LIT, WORD)]
    ranges: list[tuple[int, int]] = []
    for t in readable:
        if ranges:
            lo, hi = ranges[-1]
            between = toks[hi + 1:t.idx]
            if _bridge_ok(between, toks):
                ranges[-1] = (lo, t.idx)
                continue
        ranges.append((t.idx, t.idx))
    return ranges


def _single_digit(tok: _Tok) -> bool:
    return tok.cls in (LIT, WORD) and tok.digits is not None and len(tok.digits) == 1


def _extend_edges(lo: int, hi: int, toks: list[_Tok], used: set[int]) -> tuple[int, int, dict[int, str]]:
    """Extend a readable range over its edges: a leading DOUBLE with a
    target ('डबल जीरो'), letters glued to THIS item's edge digit run
    (MIXED_SCRIPT_RUN), a dangling '<count> बार', and — for an already
    multi-token span — a near-miss of a lexicon digit word (OOV_EDGE).
    Never extends into a token another item already owns.
    Returns (lo, hi, {token idx: reason})."""
    tags: dict[int, str] = {}
    n_readable = sum(1 for t in toks[lo:hi + 1] if t.cls in (LIT, WORD))
    if lo > 0 and toks[lo - 1].cls == DOUBLE and toks[lo].cls in (LIT, WORD) \
            and (lo - 1) not in used:
        lo -= 1
    if hi + 1 < len(toks) and toks[hi + 1].cls == TIMES and _single_digit(toks[hi]):
        hi += 1          # dangling 'एक बार': count | adverb — carried as AMBIGUOUS
    for side in ("left", "right"):
        if side == "left":
            cand = toks[lo - 1] if lo > 0 else None
            edge = toks[lo]
        else:
            cand = toks[hi + 1] if hi + 1 < len(toks) else None
            edge = toks[hi]
        if cand is None or cand.idx in used:
            continue
        if _glued_pair(cand, edge):
            tags[cand.idx] = "MIXED_SCRIPT_RUN"
        elif n_readable >= 2 and _oov_candidate(cand) and _near_miss(cand):
            tags[cand.idx] = "OOV_EDGE"
        else:
            continue
        if side == "left":
            lo -= 1
        else:
            hi += 1
    return lo, hi, tags


def _bound_multiplier(span: list[_Tok]) -> bool:
    """'डबल <digit>' or '<digit> बार <digit>' present — explicit dictation
    syntax with a target."""
    for i, t in enumerate(span):
        if t.cls == DOUBLE and i + 1 < len(span) and _single_digit(span[i + 1]):
            return True
        if t.cls == TIMES and 0 < i < len(span) - 1 and _single_digit(span[i - 1]) \
                and _single_digit(span[i + 1]):
            return True
    return False


def _asserts_numeric(lo: int, hi: int, toks: list[_Tok]) -> bool:
    """Lone-word pin (module docstring)."""
    span = toks[lo:hi + 1]
    readable = [t for t in span if t.cls in (LIT, WORD)]
    if len(readable) >= 2 or any(t.cls == LIT for t in readable):
        return True
    if _bound_multiplier(span):
        return True
    if any(t.cls == TIMES for t in span):
        return False                   # a lone '<count> बार' is the adverb 'once/twice'
    return len(span) == len(toks)      # the whole utterance is the number


# ---------------------------------------------------------------------------
# Slots
# ---------------------------------------------------------------------------
def _slot(kind, digit, token: _Tok | None, provenance, *, alternatives=None,
          count_token: _Tok | None = None, token_ref: dict | None = None) -> dict:
    return {"kind": kind, "digit": digit, "alternatives": alternatives,
            "token": token_ref if token_ref is not None else (token.ref() if token else None),
            "provenance": provenance,
            "count_token": count_token.ref() if count_token else None,
            "confidence": None}


def _literal_slots(tok: _Tok, provenance="LITERAL") -> list[dict]:
    return [_slot(DIGIT, ch, tok, provenance) for ch in tok.digits]


def _word_slots(tok: _Tok) -> list[dict]:
    prov = "COMPOUND" if len(tok.digits) > 1 else "WORD"
    return [_slot(DIGIT, ch, tok, prov) for ch in tok.digits]


def _is_count_candidate(tok: _Tok) -> bool:
    if tok.cls == LIT:
        return len(tok.digits) == 1 and tok.digits != "0"
    return tok.cls == WORD and tok.text in _HINDI_COUNT_WORDS


def _is_zero_token(tok: _Tok, count: _Tok) -> bool:
    if tok.cls == WORD:
        return tok.text in _ZERO_WORDS or tok.text.lower() in _ZERO_WORDS
    if tok.cls == LIT and tok.digits == "0":
        return count.cls != LIT          # '5 0' is a rendered digit sequence
    return False


def _build_slots(span: list[_Tok], text: str, toks: list[_Tok],
                 tags: dict[int, str]) -> tuple[list[dict], list[str], list]:
    """Slots for one item + reasons + (slot index, token) per slot-producing
    token (for group_breaks). `tags` carries the edge-pass reason for a
    token index; an untagged unreadable token is an OOV between readable
    ones (OOV_TOKEN) or glued letters (MIXED_SCRIPT_RUN)."""
    slots: list[dict] = []
    reasons: list[str] = []
    starts: list[tuple[int, _Tok]] = []      # (slot index, token) per slot-producing token
    i = 0
    n = len(span)

    def add(reason):
        if reason not in reasons:
            reasons.append(reason)

    while i < n:
        t = span[i]
        nxt = span[i + 1] if i + 1 < n else None
        nxt2 = span[i + 2] if i + 2 < n else None
        if t.cls == DOUBLE:
            if nxt is not None and _single_digit(nxt):
                starts.append((len(slots), nxt))
                slots += [_slot(DIGIT, nxt.digits, nxt, "DOUBLE", count_token=t) for _ in range(2)]
                i += 2
                continue
            add("UNBOUND_DOUBLE")
            i += 1
            continue
        if t.cls == TIMES:
            add("UNBOUND_MULTIPLIER")     # no bindable count before it
            i += 1
            continue
        if t.cls == OTHER or t.cls == KNOWN:
            starts.append((len(slots), t))
            slots.append(_slot(UNKNOWN, None, t, "OOV"))
            add(tags.get(t.idx) or ("MIXED_SCRIPT_RUN" if _glued_to_lit(t, toks) else "OOV_TOKEN"))
            i += 1
            continue
        # readable token
        if nxt is not None and nxt.cls == TIMES and _single_digit(t) and t.digits != "0":
            if nxt2 is not None and _single_digit(nxt2):
                starts.append((len(slots), t))
                slots += [_slot(DIGIT, nxt2.digits, nxt2, "MULTIPLIER", count_token=t)
                          for _ in range(int(t.digits))]
                i += 3
                continue
            # count + times + multi-digit run (or nothing bindable): unbound
            starts.append((len(slots), t))
            slots.append(_slot(AMBIGUOUS, None, t, "COUNT_OR_DIGIT",
                               alternatives=[{"digits": t.digits, "reading": f"digit {t.digits}"},
                                             {"digits": "", "reading": f"repeat count {t.digits} for the following run (not a digit)"}],
                               count_token=t))
            add("UNBOUND_MULTIPLIER")
            i += 2
            continue
        if nxt is not None and nxt.cls == TIMES:
            add("UNBOUND_MULTIPLIER")     # multi-digit count: 'दस बार' cannot bind
            starts.append((len(slots), t))
            slots += _literal_slots(t) if t.cls == LIT else _word_slots(t)
            i += 2
            continue
        if nxt is not None and _is_count_candidate(t) and _is_zero_token(nxt, t):
            k = int(t.digits)
            starts.append((len(slots), t))
            slots.append(_slot(AMBIGUOUS, None, None, "COUNT_OR_DIGIT",
                               alternatives=[{"digits": t.digits + "0", "reading": f"digit {t.digits} then digit 0"},
                                             {"digits": "0" * k, "reading": f"{t.digits} zeros"}],
                               count_token=t,
                               token_ref={"text": text[t.start:nxt.end], "start": t.start, "end": nxt.end}))
            add("COUNT_OR_DIGIT")
            i += 2
            continue
        starts.append((len(slots), t))
        slots += _literal_slots(t) if t.cls == LIT else _word_slots(t)
        i += 1
    return slots, reasons, starts


def _surface(span: list[_Tok]) -> str:
    lits = [t for t in span if t.cls == LIT]
    words = [t for t in span if t.cls == WORD]
    others = [t for t in span if t.cls not in (LIT, WORD)]
    if lits and not words and not others:
        if len(lits) == 1:
            return "RUN"
        if all(len(t.text) == 1 for t in lits):
            return "SEPARATED"
        return "GROUPED"
    if words and not lits:
        return "WORDS"
    return "MIXED"


def _group_breaks(starts: list[tuple[int, _Tok]], text: str) -> list[int]:
    out: list[int] = []
    prev: _Tok | None = None
    for slot_idx, tok in starts:
        if prev is not None and slot_idx > 0:
            sep = text[prev.end:tok.start]
            if _PUNCT_RE.search(sep) or (prev.cls == LIT and tok.cls == LIT):
                if not out or out[-1] != slot_idx:
                    out.append(slot_idx)
        prev = tok
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def observe(text: str, turn_no: int = 0) -> dict:
    """PURE: transcript text -> NumericObservation (lock §2). No task state,
    no history, no decision. `source` carries only text + sha1 here; the
    archive wrapper (build_record) adds the STT/endpoint evidence."""
    text = text or ""
    toks = _tokenize(text)
    items: list[dict] = []
    reasons: list[str] = []
    used: set[int] = set()
    for lo, hi in _group_readable(toks):
        lo2, hi2, tags = _extend_edges(lo, hi, toks, used)
        if not _asserts_numeric(lo2, hi2, toks):
            continue
        span = toks[lo2:hi2 + 1]
        slots, item_reasons, starts = _build_slots(span, text, toks, tags)
        if not slots:
            continue
        for r in item_reasons:
            if r not in reasons:
                reasons.append(r)
        items.append({
            "span": {"start": span[0].start, "end": span[-1].end,
                     "text": text[span[0].start:span[-1].end]},
            "slots": slots,
            "surface": _surface(span),
            "group_breaks": _group_breaks(starts, text),
            "boundary": {"starts_at_turn_start": span[0].idx == 0,
                         "ends_at_turn_end": span[-1].idx == len(toks) - 1},
            "unknown_count": sum(1 for s in slots if s["kind"] == UNKNOWN),
            "ambiguous_count": sum(1 for s in slots if s["kind"] == AMBIGUOUS),
        })
        used.update(range(lo2, hi2 + 1))
    non_numeric = [t.ref() for t in toks if t.idx not in used]
    if not items:
        certainty = EMPTY
    elif any(s["kind"] != DIGIT for it in items for s in it["slots"]) \
            or any(r in _UNCERTAIN_REASONS for r in reasons):
        certainty = INCOMPLETE
    else:
        certainty = COMPLETE
    return {
        "version": VERSION,
        "turn": int(turn_no or 0),
        "source": {"text": text, "text_sha1": _sha1(text)},
        "endpoint": None,
        "items": items,
        "non_numeric_tokens": non_numeric,
        "certainty": certainty,
        "reasons": reasons,
    }


def _sha1(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def endpoint_evidence(vad_endpoint: dict | None, premature_resume: dict | None = None,
                      **extra) -> dict:
    """Flatten the transport's endpoint evidence verbatim (lock §2 endpoint):
    the VAD last_endpoint dict + premature_resume_ms + caller extras."""
    ep = dict(vad_endpoint) if isinstance(vad_endpoint, dict) else {}
    pr_ms = None
    if isinstance(premature_resume, dict):
        pr_ms = premature_resume.get("resumed_after_endpoint_ms")
    ep["premature_resume_ms"] = pr_ms
    for k, v in extra.items():
        ep[k] = v
    return ep


def build_record(text: str, turn_no: int, source: dict | None = None,
                 endpoint: dict | None = None) -> dict:
    """observe() + the STT/endpoint evidence the archive must carry (lock
    §13 D: untruncated text, provider/model/language, segment metrics when
    the provider returns them, endpoint evidence)."""
    rec = observe(text, turn_no)
    src = dict(source or {})
    src["text"] = text or ""
    src["text_sha1"] = rec["source"]["text_sha1"]
    rec["source"] = src
    rec["endpoint"] = dict(endpoint) if isinstance(endpoint, dict) else None
    return rec


def attach_observation(turn: dict, text: str, turn_no: int, source: dict | None = None,
                       endpoint: dict | None = None) -> dict:
    """Write the record ONCE onto the turn dict (N7: immutable). A second
    call returns the existing record untouched."""
    existing = turn.get("numeric_observation")
    if isinstance(existing, dict):
        return existing
    rec = build_record(text, turn_no, source=source, endpoint=endpoint)
    turn["numeric_observation"] = rec
    return rec


def pure_view(rec: dict | None) -> dict:
    """The replay-comparable part of a record: everything that is a pure
    function of the text (measurements — source/endpoint — excluded)."""
    rec = rec or {}
    return {"version": rec.get("version"), "turn": rec.get("turn"),
            "items": rec.get("items"), "non_numeric_tokens": rec.get("non_numeric_tokens"),
            "certainty": rec.get("certainty"), "reasons": rec.get("reasons"),
            "text_sha1": (rec.get("source") or {}).get("text_sha1")}


def slot_kinds(item: dict) -> list[tuple[str, str | None]]:
    return [(s["kind"], s["digit"]) for s in item["slots"]]


def reading(item: dict) -> str:
    """Human-readable slot string: digits, '?' for UNKNOWN, '{a|b}' for
    AMBIGUOUS. Audit display only."""
    out = []
    for s in item["slots"]:
        if s["kind"] == DIGIT:
            out.append(s["digit"])
        elif s["kind"] == UNKNOWN:
            out.append("?")
        else:
            out.append("{" + "|".join(a["digits"] or "∅" for a in (s["alternatives"] or [])) + "}")
    return "".join(out)


def digits_of(rec_or_item: dict | None) -> str | None:
    """Digits of a COMPLETE record (all items concatenated) or of an item
    whose slots are all DIGIT; None otherwise. Audit helper — the operation
    layer (Phase 3) reads slots, not this string."""
    if not rec_or_item:
        return None
    if "slots" in rec_or_item:
        sl = rec_or_item["slots"]
        if all(s["kind"] == DIGIT for s in sl):
            return "".join(s["digit"] for s in sl)
        return None
    if rec_or_item.get("certainty") != COMPLETE:
        return None
    return "".join(s["digit"] for it in rec_or_item.get("items") or [] for s in it["slots"])


def summary(rec: dict | None) -> str:
    """One-line audit form: 'COMPLETE 026900 (RUN)' / 'INCOMPLETE 02?900 [OOV_TOKEN]'."""
    if not rec:
        return "-"
    items = rec.get("items") or []
    parts = " + ".join(f"{reading(it)} ({it['surface']})" for it in items)
    s = rec.get("certainty", "?")
    if parts:
        s += " " + parts
    if rec.get("reasons"):
        s += " [" + ",".join(rec["reasons"]) + "]"
    return s
