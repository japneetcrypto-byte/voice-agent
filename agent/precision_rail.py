"""PRECISION-DETAIL RAIL — deterministic owner of dictated structured data
(owner directive 2026-08-30; approved fix ①, implemented 2026-08-31, v2
2026-08-31 after owner smoke 2).

Failure class (smoke 1): the LLM fabricated three different account numbers
while STT was correct on every dictation, refused ("note nahi kar sakta") at
t21/t22 while claiming success at t27. Root: the LLM was the source of truth
for dictated digits. Owner: "for these details we need policy no llm action;
and confirmation should be done with user."

Smoke 2 (owner, 2026-08-31) exposed three rail gaps, fixed here:
  1. DIGITS, not garbage: the verbatim echo carried Devanagari digit words
     and the script guard transliterated them to ITRANS ('जीरो' -> 'jIro') —
     unpronounceable. The system now NORMALIZES dictated digits
     deterministically (digit words -> digits) and echoes/stores DIGITS.
  2. SEGMENT ACCUMULATION: digit-by-digit dictation ('6,9,00,1,2' then
     '5,7,0,3') REPLACED the pending value instead of appending. The rail
     now accumulates segments silently and confirms the FULL number once.
  3. STAY QUIET WHILE DICTATING: the user said the agent "continuously
     speaks in between" — while a dictation is pending, non-dictation
     filler is suppressed (silent), so the user can finish dictating.
  Plus: RECALL — 'kya likha / repeat karo / number bata' re-speaks the
  stored value deterministically (no LLM).

Smoke 5 (owner, 2026-08-31: 'not able to speak full no.' + 'ha bolo wahi
hu while i am speaking') — the rail went APPEND-FIRST (v5):
  - A mid-dictation single run ('124205703' / '5703') is a CONTINUATION
    segment, not a fresh number: it APPENDS silently instead of replacing
    (the old rule replaced anything >=3 digits, so the user's accumulated
    prefix was thrown away and they could never finish the number).
  - REPLACE only on explicit whole-number re-dictations: reject+digits,
    restart phrases ('पूरा नंबर...'), separated re-spells >=8 digits,
    compound words (निन्यानबे पैंतीस), or 4+ digit-word signals.
  - While a dictation is armed/pending the rail HOLDS THE FLOOR: fillers
    ('यह कर लेते हैं' — 4 words), plain rejects ('जे गलत है') and re-
    announcements are SILENT and keep the state — they never fall to the
    LLM (the LLM used to nag 'haan bol, kya number hai?' mid-dictation).
    Only an explicit abandon ('छोड़ दे / भूल जा') or a long (>6 words)
    non-digit turn releases the rail back to the LLM.
  - Greeting rail no longer gated on live sess binding (owner smokes 4+5:
    the live t1 'हेलो...' kept running the LLM; greeting_line_for itself
    is verified — the sess gate was the only live blocker).

Smoke 6 (owner, 2026-08-31: 'stil not able to correctly get no.',
'hallucinates in between', 'ha btao — speaking in between', 'it stopped
speaking at last') — v6:
  - Fresh SHORT digit runs ('026', '9000', '9935') are dictation even
    unarmed (pure digit utterance, >=3 normalized digits); '9000 rupaye'
    / 'एक बार मैंने सोचा' are not pure -> never fire.
  - While ARMED nothing non-digit falls to the LLM: complaints/rejects
    -> retry, status queries ('लिख लिया?', 'क्या लिखा?') -> STATUS lines,
    long fillers -> silent stay (the >6-word discard is gone while armed).
    While CONFIRMING: status queries -> RECALL (re-speak the value);
    'तुने लिखा नहीं किया' (CLAIM) -> RECALL as proof, not a clear.
  - Continuation cues ('आगे', 'इसके बाद क्या है') -> short HOLD lines;
    a new non-number detail request ('एक address लिखो') releases the
    rail to the LLM (DEARM_DETAIL_RE).

Smoke 7 (owner, 2026-08-31: 'why does it stop speaking at last?', 'i was
saying 4 bar zero - it write 420', 'should not speak while i am speaking')
— v7 is STATE-AWARE:
  - After an announcement arm the FIRST digit segment accumulates
    SILENTLY — while the user dictates the rail listens, it does not talk
    (the old first-echo was always CANCELLED BEFORE AUDIO). Confirmation
    happens at 'bas' (echo_full) or on a query (recall).
  - STRUCTURED CORRECTIONS are parsed and applied ('12 के बाद 4 बार 0
    है 420 नहीं' -> the stored value is REPAIRED: 02690012425703 ->
    0269001200005703). Unresolvable corrections get a SPOKEN ack
    ("theek, samajh gaya — 420 nahi, 0000 hai...") instead of silence or
    a wrong replace; question-guarded (an inquiry is never a spec).
  - No black hole: continuation cues (incl. 'आँगे', 'बोल दो') -> hold
    lines; corrections -> ack lines; only short garbage stays silent.

Smoke 8 (owner, 2026-08-31: 'why is it not speaking?', 'whole experience
is deteriorating') — v8 is CONVERSATIONALLY RESPONSIVE:
  - A turn that ANNOUNCES and DICTATES the number in one go captures it
    (digits are checked before the announcement — t5's number was lost).
  - QUERIES always get answers: question-tag 'की नहीं'/'किनहीं' are not
    rejections; 'क्या लिखे हो'/'बताओ' recall the stored value; a
    re-announcement must be a WRITE COMMAND, so 'क्या लिखा...नंबर' is a
    recall, not a silent re-arm.
  - WRITING-COMPLAINTS ('तुने लिखा किनहीं', 'लिख नहीं पा रहा') -> recall
    the stored value as PROOF (never clear, never 'phir se bol na');
    armed-empty -> a spoken apology+ask.
  - Re-stating the stored number EXACTLY does not double it ('...बस' ->
    echo_full).

Flow (all zero-LLM, pure, deterministic — same discipline as reply_guard):
    DETECT   dictation_value(text)  — dictated number/ID span (verbatim)
    NORMALIZE normalize_span(span)  — digit words -> digits (system-owned)
    ECHO     first dictation: "main suna: <digits>. sahi hai na?"
    ACCUM    further segments while pending: silent, value appends
    FINALIZE "bas / haan / theek"  -> "poora number: <digits>. sahi hai na?"
    CONFIRM  "haan"                -> deterministic ack (value confirmed in
             session task state engine['dictation'] — never long-term
             memory, PII no-store)
    REJECT   "nahi / galat"        -> retry, value cleared
    RECALL   "kya likha / repeat"  -> "jo likha hai: <digits>."

decide() is stateful through the engine dict ({"dictation": {...}}) exactly
like detail_mode/other per-session mutable state; run_turn and main.py both
call it, so the replay harness reproduces rail turns byte-for-byte. When the
decision is SILENT (line is None) the turn is suppressed (response_suppressed)
in both paths.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Detection — raw STT span (verbatim; echo/storage use the normalized digits)
# ---------------------------------------------------------------------------
DICTATION_KEYWORDS = re.compile(
    r"\b(?:account|acct|number|num|id|code|pin|otp|aadhaar|aadhar|pan|card|"
    r"bank|khata|khaata)\b"
    r"|खाता|खाते|नंबर|नम्बर|आईडी|कोड|ओटीपी|पिन|कार्ड|बैंक|अकाउंट",
    re.IGNORECASE)

# Grouped digit number: two+ runs of 2+ digits separated by short separators
# ('0269 0012420' -> the WHOLE thing, not just the last run — smoke-2 t29 bug).
GROUPED_DIGITS_RE = re.compile(r"(?<!\d)\d{2,}(?:[\s,.\-–—]+\d{2,})+(?!\d)")

# Long consecutive digit run (e.g. "0269001200005703").
DIGIT_RUN_RE = re.compile(r"(?<!\d)\d{6,}(?!\d)")

# Digit-by-digit dictation with separators (smoke 1 t21: "0,2,8,9,7,0,1,2,4"
# "0,5,7,0,3") — 6+ digits separated by commas/spaces/dots/dashes.
SEPARATED_DIGITS_RE = re.compile(
    r"(?<!\d)(?:\d[\s,.\-–—]{1,3}){4,}\d(?!\d)")

# Digit words (Hindi + English + the Devanagari phonetics of English digits
# as STT heard them — smoke 2 t34: 'डबल जीरो, वन, तू, चार बार जीरो, पाइट
# सेविन, जीरो त्री'). Built from the normalization map (single source).
DIGIT_WORD_MAP = {
    # Hindi digits (incl. nuqta variants STT actually emits — owner smoke 3:
    # '4 बार ज़ीरो' with ज़ broke both detection and normalization)
    "जीरो": "0", "ज़ीरो": "0", "ज़िरो": "0", "शून्य": "0", "सुन्य": "0",
    "एक": "1", "दो": "2", "तीन": "3", "चार": "4",
    "पांच": "5", "पाँच": "5", "छह": "6", "छै": "6", "छे": "6",
    "सात": "7", "आठ": "8", "नौ": "9",
    # Hindi compound number words 10-99 (owner smoke 4: "i am saying 9935 in
    # hindi- ninyanbe panteen" = निन्यानबे पैंतीस). Each word is a COMPLETE
    # number; dictation concatenates the groups (निन्यानबे पैंतीस = 99 35).
    "दस": "10", "ग्यारह": "11", "बारह": "12", "तेरह": "13", "चौदह": "14",
    "पंद्रह": "15", "पन्द्रह": "15", "सोलह": "16", "सत्रह": "17",
    "अठारह": "18", "उन्नीस": "19", "उनिस": "19",
    "बीस": "20", "इक्कीस": "21", "इकीस": "21", "बाईस": "22", "तेईस": "23",
    "चौबीस": "24", "पच्चीस": "25", "छब्बीस": "26", "सत्ताईस": "27",
    "अट्ठाईस": "28", "उनतीस": "29",
    "तीस": "30", "इकतीस": "31", "बत्तीस": "32", "तैंतीस": "33",
    "चौंतीस": "34", "पैंतीस": "35", "पैंतिश": "35", "पैतिश": "35",
    "छत्तीस": "36", "सैंतीस": "37", "अड़तीस": "38", "उनतालीस": "39",
    "चालीस": "40", "इकतालीस": "41", "बयालीस": "42", "तैंतालीस": "43",
    "चवालीस": "44", "पैंतालीस": "45", "छियालीस": "46", "सैंतालीस": "47",
    "अड़तालीस": "48", "उनचास": "49",
    "पचास": "50", "इक्यावन": "51", "इक्याबन": "51", "बावन": "52",
    "तिरपन": "53", "चौवन": "54", "पचपन": "55", "छप्पन": "56",
    "सत्तावन": "57", "अट्ठावन": "58", "उनसठ": "59",
    "साठ": "60", "इकसठ": "61", "बासठ": "62", "तिरसठ": "63", "चौंसठ": "64",
    "पैंसठ": "65", "छियासठ": "66", "सड़सठ": "67", "अड़सठ": "68",
    "उनहत्तर": "69",
    "सत्तर": "70", "इकहत्तर": "71", "बहत्तर": "72", "तिहत्तर": "73",
    "चौहत्तर": "74", "पचहत्तर": "75", "छिहत्तर": "76", "सतहत्तर": "77",
    "अठहत्तर": "78", "उन्यासी": "79",
    "अस्सी": "80", "इक्यासी": "81", "बयासी": "82", "तिरासी": "83",
    "चौरासी": "84", "पचासी": "85", "छियासी": "86", "सत्तासी": "87",
    "अट्ठासी": "88", "नवासी": "89",
    "नब्बे": "90", "इक्यानवे": "91", "बानवे": "92", "तिरानवे": "93",
    "चौरानवे": "94", "पंचानवे": "95", "छियानवे": "96", "सत्तानवे": "97",
    "अट्ठानवे": "98", "निन्यानवे": "99", "निन्यानबे": "99", "नियान": "99",
    # English digits (as typed in Roman)
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    # Devanagari phonetics of English digits (Hindi STT transcribes them)
    "वन": "1", "वान": "1", "टू": "2", "तू": "2", "दू": "2",
    "थ्री": "3", "त्री": "3",
    "फोर": "4", "फाइव": "5", "पाइव": "5", "पाइट": "5", "फाइ": "5",
    "पाँव": "5", "पांव": "5",
    "सिक्स": "6", "सेवन": "7", "सेविन": "7", "सेवें": "7", "सैवन": "7",
    "एट": "8", "एइट": "8", "नाइन": "9",
}
# 'डबल जीरो' = 00; 'चार बार जीरो' = 0000 (multiplier). Roman 'bar' included
# (owner said '4 bar zero' — Hinglish STT may romanize बार as bar).
DOUBLE_WORDS = {"डबल", "double", "दुगना", "दोहरा"}
TIMES_WORDS = {"बार", "बारा", "times", "bar", "x", "×", "बट"}
# 'x'/'×'/'बट' are STT transcriptions of 'बार' heard live (smoke-12 t12:
# user said '5 बार 0', STT wrote '5 x 0, 1, 2, 0, 3,' and the multiplier was
# DROPPED -> '01203' instead of '000001203'). Safe additions: the cluster
# rules still require 2+ signals + times for dictation to fire, and any
# contamination pulls in a non-digit word that _cluster_fires rejects.

# Detection token = any digit word / double / times word (longest first so
# 'पाइव' beats 'पाइ', 'सेविन' beats 'सेव', etc.).
_DETECT_TOKENS = sorted(set(DIGIT_WORD_MAP) | DOUBLE_WORDS | TIMES_WORDS,
                        key=len, reverse=True)
DIGIT_TOKEN_RE = re.compile("|".join(re.escape(t) for t in _DETECT_TOKENS),
                            re.IGNORECASE)

# Max gap between digit-ish tokens still considered ONE dictation phrase.
# Dictation is DENSE (digits read back to back); conversational digit words
# ('एक interview ... एक बार ...') are far apart — real-baseline gate t20.
MERGE_GAP = 12

# A dictation of >= this many digits while a value is pending is treated as
# a FRESH full re-dictation (replace), not a continuation segment (append).
# Continuation segments are short ('5, 7, 0, 3'); a full number is long
# (smoke-2 t34 re-spoke the whole 17-digit number -> must replace, never
# append). Account numbers are 10-18 digits; a mid-dictation append of 8+
# digits is far rarer than a re-dictation.
FRESH_DICTATION_MIN = 8

# Words that may appear INSIDE a dictation span without breaking it
# (Hindi postpositions/particles STT inserts — owner smoke 4 t20:
# 'नियान ने पैंतिश' with the 'ने' postposition must still be one dictation).
_SPAN_CONNECTORS = {"ने", "का", "की", "के", "और", "है", "हैं", "में", "से", "को"}


def _cluster_fires(span: str) -> bool:
    """Decide whether a compact digit-token span is a real dictation (vs
    conversational digit words). Signals = digit runs + digit words + double
    words (times words excluded — 'एक बार' = 'once' is not dictation).
      - >=3 signals                               -> dictation
      - 2 signals + times/double/compound/run     -> dictation
      - 1 signal + double ('डबल जीरो' = 00)       -> dictation
      - otherwise ('तू एक' = 'you a', 'एक बार')   -> NOT dictation
    Any non-digit word inside the span ('एक दिन एक') -> NOT dictation.
    """
    words = re.findall(r"[\u0900-\u097F]+|[A-Za-z]+|\d+|×", span)
    signals = 0
    has_times = has_double = has_compound = has_run = False
    for w in words:
        low = w.lower()
        if low in TIMES_WORDS:
            has_times = True
        elif low in DOUBLE_WORDS:
            has_double = True
            signals += 1
        elif w.isdigit():
            has_run = True
            signals += 1
        elif low in DIGIT_WORD_MAP:
            if len(DIGIT_WORD_MAP[low]) > 1:
                has_compound = True
            signals += 1
        elif low in _SPAN_CONNECTORS:
            continue
        else:
            return False  # non-digit word inside the span -> conversational
    if signals >= 3:
        return True
    if signals == 2 and (has_times or has_double or has_compound or has_run):
        return True
    if signals == 1 and has_double:
        return True
    return False


def _is_pure_digit_utterance(text: str) -> bool:
    """True when the turn is a PURE digit signal: every word is a digit /
    digit word / double / times token ('जीरो', 'तू', '5', '6, 9, 00').
    While a dictation is pending, a lone 'जीरो' is the user still dictating —
    but 'टू रियल्ड बात सुनना वो टू डू डू एक चार' (digit words embedded in
    conversation, real-baseline t23) must NOT accumulate."""
    t = (text or "").strip()
    if not t:
        return False
    words = re.findall(r"[\u0900-\u097F]+|[A-Za-z]+|\d+", t)
    if not words:
        return False
    for w in words:
        low = w.lower()
        if w.isdigit() or low in DIGIT_WORD_MAP or low in DOUBLE_WORDS or low in TIMES_WORDS:
            continue
        return False
    return True


def _is_full_restatement(text: str, seg_len: int) -> bool:
    """True when a digit utterance while a value is pending is a whole-number
    RE-DICTATION (REPLACE the pending value) vs a continuation segment
    (APPEND). Append-first is the default — the user is BUILDING the number
    (owner smoke-5: 'not able to speak full no.' — a 9-digit single run
    '124205703' was a continuation, not a fresh number). REPLACE only on
    explicit whole-number signals:
      - reject-word in the turn ("नहीं, मेरा नंबर 9935 है" -> the real number)
      - restart phrase ("पूरा नंबर...", "phir se...", "shuru se...")
      - separated re-spell of >= FRESH_DICTATION_MIN digits (the user
        re-spells the ENTIRE number digit-by-digit, smoke-2 t34)
      - 2+ compound number words (निन्यानबे पैंतीस = a complete number in
        words, smoke-4 t20)
      - 4+ single-digit WORDS (a long spelled-out re-dictation: 'डबल जीरो,
        वन, तू, चार बार ज़ीरो, पाइट सेविन, जीरो त्री')
    Everything else (single runs, short separated lists, lone digit words)
    continues the number -> APPEND silently.
    """
    t = text or ""
    if _is_reject(t):
        return True
    if RESTART_RE.search(t):
        return True
    if seg_len >= FRESH_DICTATION_MIN and SEPARATED_DIGITS_RE.search(t) \
            and not any(w.lower() in TIMES_WORDS or w.lower() in DOUBLE_WORDS
                        for w in re.findall(r"[\u0900-\u097F]+|[A-Za-z]+", t)):
        # A multiplier word (बार/x/बट/डबल) marks a SEGMENT of a number being
        # built, not a whole-number re-spell: smoke-12 t12 '5 x 0, 1, 2, 0, 3'
        # continues the accumulation (012+120+12+000001203); it must NOT trip
        # the separated re-spell rule and become a REPLACE.
        return True
    words = re.findall(r"[\u0900-\u097F]+|[A-Za-z]+|\d+", t)
    compounds = 0
    word_signals = 0
    for w in words:
        low = w.lower()
        if low in DIGIT_WORD_MAP:
            if len(DIGIT_WORD_MAP[low]) > 1:
                compounds += 1
            else:
                word_signals += 1
    if compounds >= 2:
        return True
    if word_signals >= 4:
        return True
    return False


def _digit_tokens(text: str) -> list[tuple[int, int]]:
    """(start, end) of every digit-ish token (digit runs + digit words +
    double/times words), sorted by position. Used ONLY to find compact
    clusters — never to reconstruct a value."""
    toks = [(m.start(), m.end()) for m in re.finditer(r"\d+", text)]
    toks += [(m.start(), m.end()) for m in DIGIT_TOKEN_RE.finditer(text)]
    return sorted(set(toks))


def _first_compact_cluster(text: str, toks: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The first run of digit tokens whose gaps are all <= MERGE_GAP."""
    if not toks:
        return []
    cluster = [toks[0]]
    for s, e in toks[1:]:
        if s - cluster[-1][1] <= MERGE_GAP:
            cluster.append((s, e))
        else:
            break
    return cluster


def dictation_value(text: str) -> str | None:
    """Return the VERBATIM STT span when the turn dictates a structured
    detail (number/ID), else None. Conservative: plain amounts ("9000
    rupaye"), years, small counts and conversational digit words never fire —
    only identifier-like dictation does. The span is always a raw slice of
    the transcript; normalization to digits happens separately."""
    t = text or ""
    # A बार/डबल word is the definitive "number read aloud" signal, and the
    # multiplier needs the WHOLE span to expand: '026900 4 बार 0 4301' must
    # capture the full span (-> 02690000004301), not just the first run
    # '026900' (owner smoke-10 correction: "it is 4 bar zero not 420" — STT
    # collapsed '4 बार 0' into the run '420'). Tried BEFORE the run/grouped
    # returns so a multiplier + its continuation are never dropped;
    # _cluster_fires still rejects conversational mixes ('एक बार मैंने
    # सोचा 9000' has a non-digit word inside -> no fire; 'एक बार' alone ->
    # 1 signal -> no fire).
    if any(w in TIMES_WORDS or w in DOUBLE_WORDS
           for w in re.findall(r"[A-Za-z\u0900-\u097F]+", t)):
        cluster = _first_compact_cluster(t, _digit_tokens(t))
        if len(cluster) >= 2:
            span = t[cluster[0][0]:cluster[-1][1]].strip()
            if _cluster_fires(span):
                return span
    m = GROUPED_DIGITS_RE.search(t)
    if m:
        return m.group(0).strip()
    m = DIGIT_RUN_RE.search(t)
    if m:
        return m.group(0)
    m = SEPARATED_DIGITS_RE.search(t)
    if m:
        return m.group(0).strip()
    cluster = _first_compact_cluster(t, _digit_tokens(t))
    if len(cluster) >= 2:
        span = t[cluster[0][0]:cluster[-1][1]].strip()
        if _cluster_fires(span):
            # compact digit-token span with enough dictation signal =
            # number read aloud ('निन्यानबे पैंतीस' = 99 35, 'डबल जीरो,
            # वन...', '4 बार ज़ीरो'). Conversational digit words ('एक दिन
            # एक', 'तू एक', 'एक बार') never fire.
            return span
    if DICTATION_KEYWORDS.search(t) and len(re.findall(r"\d", t)) >= 3:
        m = re.search(r"(?<!\d)\d[\d\s,.\-–—]{0,24}\d(?!\d)", t)
        if m:
            return m.group(0).strip()
    if _is_pure_digit_utterance(t) and len(normalize_span(t)) >= 3:
        # v6 (owner smoke-6 t12/t13): a STANDALONE number read aloud —
        # '026', '9000', '9935', '5, 7, 0, 3', 'नाइन जीरो जीरो' — is
        # dictation even unarmed. DIGIT_RUN_RE needs 6+ digits and the
        # cluster rule needs 2+ tokens, so the owner's short segments fell
        # through to the LLM. '9000 rupaye' / 'एक बार मैंने सोचा' are NOT
        # pure -> still never fire; 'एक' (1) / 'डबल जीरो' (2) stay below
        # the 3-digit floor.
        return t.strip()
    return None


# ---------------------------------------------------------------------------
# Normalization — digit words -> digits (deterministic, system-owned; the
# value echoed/stored is ALWAYS digits, so TTS speaks it correctly and the
# user can verify it. Never LLM re-encoding.)
# ---------------------------------------------------------------------------
def normalize_span(span: str) -> str:
    """Deterministically convert a dictation span to digits.
    Rules: 'डबल जीरो' -> 00; 'चार बार जीरो' -> 0000 (multiplier only when
    the count is a single digit); unknown words are dropped (noise)."""
    toks = re.findall(r"[\u0900-\u097F]+|[A-Za-z]+|\d+|×", span or "")
    out: list[str] = []
    mult: int | None = None      # set by 'बार' / 'times' (repeat next)
    double = False               # set by 'डबल' / 'double'
    for tok in toks:
        low = tok.lower()
        if low in TIMES_WORDS:
            if out and len(out[-1]) == 1 and out[-1].isdigit():
                mult = int(out.pop())
            continue
        if low in DOUBLE_WORDS:
            double = True
            continue
        d = DIGIT_WORD_MAP.get(tok) or DIGIT_WORD_MAP.get(low)
        if d is None:
            if tok.isdigit():
                d = tok
            else:
                continue  # unknown word = STT noise; dropped
        if double:
            # 'डबल' applies only to a single following digit ('डबल जीरो' =
            # 00). A multi-digit run after it ('डबल 026900') is the number
            # itself — never duplicate it.
            out.append(d * 2 if len(d) == 1 else d)
            double = False
        elif mult is not None:
            # 'बार' applies only to a single following digit ('4 बार 0' =
            # 0000). A multi-digit run after a times word ('2 बार 026900...'
            # = "said the number twice") is the number itself — never
            # duplicate the whole run.
            out.append(d * mult if len(d) == 1 else d)
            mult = None
        else:
            out.append(d)
    return "".join(out)


# ---------------------------------------------------------------------------
# Confirmation / rejection / recall (word-exact — real-baseline gate t23:
# bare 'ना' inside 'सुनना' must never reject)
# ---------------------------------------------------------------------------
_DEVANAGARI_WORD_RE = re.compile(r"[\u0900-\u097F]+")

CONFIRM_EN_RE = re.compile(
    r"\b(?:haan|han|hann|yes|yep|yeah|sahi|theek|thik|correct|confirm|"
    r"confirmed|ok|okay|done|bas|ho gaya|ho gya|itna hi|itna hai|"
    r"note kar lo|rakh lo|rakh do)\b", re.IGNORECASE)
CONFIRM_DEV_WORDS = {"हाँ", "हां", "सही", "ठीक", "थीक", "कन्फर्म", "बस",
                     "हो गया", "इतना"}

REJECT_EN_RE = re.compile(
    r"\b(?:nahi|nhi|no|galat|galt|wrong|nope)\b", re.IGNORECASE)
REJECT_DEV_WORDS = {"नहीं", "नही", "गलत"}

# A CHANGE FRAME: conversational markers that mean the user is EDITING an
# already-stored value, not confirming it (owner session 20260902_184247:
# t11 '...9000...900...बस एक जीरो कम हो ज' — the confirm word "बस" next to a
# change frame hijacked the turn into echo_full, and the change never
# applied). A confirm word inside a change frame is NOT a confirm; and a
# stored value is only cleared on a PLAIN whole-turn rejection (no digits,
# no change frame).
_CHANGE_FRAME_RE = re.compile(
    r"कम|घट|बदल|बदला|बदलो|चेंज|change|जगह|सुधार|हटा|हटाओ", re.IGNORECASE)


def _is_change_frame(text: str | None) -> bool:
    return bool(_CHANGE_FRAME_RE.search(text or ""))


def _is_plain_reject(text: str | None) -> bool:
    """A whole-turn rejection that MAY clear the stored value: no digits, no
    digit-words, no change frame — 'नहीं, गलत है'. An edit-intent turn
    ('1242 नहीं है', '9000 कम करो', '9934 नहीं 9935 है') is NOT plain: the
    stored value must survive so the correction can repair it. M6 (owner
    session 20260905_102221 t21 'ठीक है, ओके, चलो कोई नहीं'): a turn that
    ALSO carries a confirm word is not a whole-turn rejection either — it is
    unclear, and unclear never clears."""
    t = text or ""
    if not t.strip():
        return False
    if _digit_tokens(t):
        return False
    if _is_change_frame(t):
        return False
    if _is_confirm(t):
        return False
    return True

RECALL_RE = re.compile(
    r"repeat|kya likha|kya number|number kya|number bata|bol kya|"
    r"dobara bata|dubara bata|phir se bata|wapis bata|"
    r"फिर से बोल|पिर से बोल|फिरसे बोल|पिरसे बोल|फिरशे बोल|पिरशे बोल|फिर बोल|पिर बोल|दोहरा|दोहराओ|"
    r"दोबारा बोल|दुबारा बोल|दोबारा रिपीट|दुबारा रिपीट|"
    r"नंबर क्या है|number क्या है|मुझे बता क्या|बता क्या है|"
    r"रिपीट|क्या लिखा|क्या नंबर|नंबर क्या|नंबर बता|बोल क्या|दोबारा बता|"
    r"दुबारा बता|फिर से बता|वापिस बता|वापस बता", re.IGNORECASE)

# ROW 51 (owner session_20260831_192745 "memory is not saving"): a query
# that references a number the user claims to have SAVED with us earlier
# ('मैंने तुझे अपना नंबर शेप करवाया था' / 'मोबाइल नंबर सेव करवाया था').
# Deterministic recall from the long-term store — never LLM, never invented.
# PAST-TENSE ONLY: 'करवाया/बताया था/दिया था' = recall; an imperative
# 'नंबर सेव कर लो' (save it now) must stay an announcement, not a query.
SAVED_NUMBER_QUERY_RE = re.compile(
    r"(?:(?:नंबर|नमबर|number|अकाउंट|account|खाता|मोबाइल|mobile|phone)"
    r".{0,28}(?:करवाया|कराया|रखवाया|लिखवाया|बताया था|दिया था|पता दे|बता दे|याद है|याद रख))"
    r"|(?:(?:क्या लिखा|लिखा था|लिखा है).{0,12}(?:नंबर|नमबर|number|अकाउंट|account|मोबाइल|mobile))",
    re.IGNORECASE)

# 'सिर्फ इतना / बस इतना / only this / just this' + a number = "THE number
# is only this one" — the user states the canonical value and trims away
# everything else (owner smoke-11 t11: 'इसमें जो 9935411907 है, सिर्फ इतना
# नमबर, ठीक है?' -> replace the stored value with 9935411907, never append).
ONLY_THIS_RE = re.compile(
    r"सिर्फ इतना|बस इतना|इतना ही|सिर्फ यही|बस यही|only this|just this",
    re.IGNORECASE)

# A question carrying 'नहीं' is an INQUIRY, NOT a rejection of the pending
# value. Two shapes:
#   - interrogative words ("क्यों नहीं बोला" — smoke-4 t20; "क्या...")
#   - QUESTION TAGS ("लिखा तूने की नहीं?" = did you write or not — smoke-8
#     t13; 'किनहीं' is STT's garble of 'कि नहीं' — smoke-8 t6)
# Only a plain negation ('नहीं, गलत है', 'यह है ही नहीं...') rejects.
QUESTIONISH_RE = re.compile(
    r"क्यों|क्यूं|क्युं|क्या|why|कौन|कब|कहाँ|कहां|कहा\s|\?|"
    r"की नहीं|कि नहीं|किन्हीं|किनहीं|या नहीं", re.IGNORECASE)

# Explicit whole-number RESTART signals: the user says they will state the
# number again from the start ("पूरा नंबर...", "phir se...", "shuru se...")
# -> REPLACE the pending value (smoke-5 t25/t33 taught us single runs are
# CONTINUATION segments — a restart is only when the user SAYS so).
RESTART_RE = re.compile(
    r"पूरा|पुरा|poora|फिर से|फिरसे|phir se|phirse|दोबारा|दुबारा|dubara|"
    r"wapis|wapas|वापिस|वापस|शुरू से|shuru se|shuruse|from start|again|"
    r"whole|full", re.IGNORECASE)

# Explicit dictation ABANDON ("छोड़ दे", "भूल जा", "रहने दे") -> release the
# rail back to the LLM (state discarded). The user changed topic; the LLM
# hears the phrase and responds naturally.
ABANDON_RE = re.compile(
    r"छोड़|छोड|भूल|रहने दे|रद्द|cancel|drop it|ignore|भूल जा|जरूरत नहीं|"
    r"no need|need nahi|कोई बात नहीं|बात छोड़|मत लिख", re.IGNORECASE)

# A NEW non-number detail request ("एक address लिखो" — owner smoke-6 t29):
# the rail only owns NUMBER dictation; an address/email/name request means
# the user moved on -> release to the LLM (was: swallowed silent — "it
# stopped speaking at last").
DEARM_DETAIL_RE = re.compile(
    r"address|पता|पते|email|ईमेल|mail|नाम|name|appointment|अपॉइंटमेंट|"
    r"काम|date|डेट", re.IGNORECASE)

# Continuation / status cues while dictating ("आगे", "इसके बाद क्या है",
# "लिख लिया?") — the user prods for progress; a short rail line answers
# instead of silence ("it stopped speaking at last"). Includes the
# candrabindu variants STT actually emits (smoke-7 t19 'आँगे भाईया कुछ तो
# बोल दो' — 'आँगे' with ँ never matched plain 'आगे').
CONTINUE_CUE_RE = re.compile(
    r"आगे|आँगे|aage|इसके बाद|iske baad|what'?s next|next|आगे क्या|फिर क्या|"
    r"बोल दो|बोलो|कुछ तो बोल|कुछ बोल|क्यों नहीं बोल|सुनाओ क्या|बताओ क्या|"
    r"क्या हुआ|kya hua", re.IGNORECASE)

# Structured dictation CORRECTION: the user fixes a digit-group of the
# stored number ("12 के बाद 4 बार 0 है 420 नहीं है" = after 12 come four
# zeros, not 420 — smoke-7 t14; "4 बार 0 मैंने बोला है 1, 2 के बाद" — t9).
# The rail REPAIRS the stored value instead of silently appending (which
# put 0000 at the END — the "it write 420" complaint) or adopting the spec
# digits as a fresh value (which overwrote the number with '12'/'0000').
_CORR_SPLIT_GAP = 2  # gap > 2 chars between digit tokens -> separate group


def _digit_groups(text: str) -> list[tuple[str, int, int]]:
    """(digits, start, end) of each digit group in text: digit runs +
    digit-words, split where the gap between tokens exceeds _CORR_SPLIT_GAP
    (so '12 के बाद 4 बार 0' -> ['12', '0000'], not one blob). Also split a
    digit WORD followed by a digit RUN across a comma ('जीरो, 420' -> two
    groups — smoke-7 t12) while keeping separated runs merged ('1, 2' and
    '6,9,00,1,2' stay one '12'/'690012' group)."""
    toks = sorted(_digit_tokens(text))
    groups: list[list[tuple[int, int]]] = []
    cur: list[tuple[int, int]] = []
    prev_end = None
    prev_is_run = None
    for s, e in toks:
        tok = text[s:e]
        is_run = bool(tok and tok[0].isdigit())
        split = False
        if prev_end is not None:
            gap = s - prev_end
            if gap > _CORR_SPLIT_GAP:
                split = True
            elif "," in text[prev_end:s] and prev_is_run is False and is_run:
                split = True  # 'जीरो, 420' -> word then run after a comma
        if split and cur:
            groups.append(cur)
            cur = []
        cur.append((s, e))
        prev_end = e
        prev_is_run = is_run
    if cur:
        groups.append(cur)
    out = []
    for g in groups:
        d = normalize_span(text[g[0][0]:g[-1][1]])
        if d:
            out.append((d, g[0][0], g[-1][1]))
    return out


def _parse_correction(text: str) -> tuple[str | None, str | None, str | None] | None:
    """Detect a structured dictation CORRECTION. Returns
    (anchor, wrong, correct) digit strings (None = absent), or None when the
    utterance is not a resolvable correction spec:
      - requires a correction signal (नहीं/गलत/correct or 'के बाद'/'बाद'/after)
      - must NOT be a question (smoke-4 t20 'क्यों नहीं बोला' is an inquiry
        about the number, not a correction)
      - needs a 'correct' group distinct from the 'wrong' group and any
        'anchor' group ('X के बाद' = the position anchor)
    Single-group rejections ('नहीं, मेरा नंबर 02690001245703 है') are NOT
    correction specs -> None (they stay full-restatement replaces)."""
    t = text or ""
    if not re.search(r"नहीं|nahi|गलत|galat|correct|के बाद|केबाद|बाद|after|replace|रिप्लेस",
                     t, re.IGNORECASE):
        return None
    if QUESTIONISH_RE.search(t):
        return None  # an inquiry about the number, not a correction
    groups = _digit_groups(t)
    if not groups:
        return None
    wrong: str | None = None
    negs = [m.start() for m in re.finditer(r"नहीं|nahi|गलत|galat", t, re.IGNORECASE)]
    if negs:
        # the wrong group is the one whose END is nearest ITS nearest
        # negation ('जीरो नहीं' -> the '0' ends 1 char before नहीं, so it is
        # the wrong group; smoke-6 t18 '900 है, 0 नहीं')
        wrong = min(groups, key=lambda g: min(abs(g[2] - n) for n in negs))[0]
    # Smoke-13 t29/t30: '6 को replace करो 6 बार 0 से' — the replace TARGET is
    # the wrong value when no negation names one. It must NEVER be counted as
    # a 'correct' group (that is why t29's parse failed: the stray '6' made
    # rest = {'000000','6'} instead of {'000000'}).
    replace_m = re.search(r"(\d+)\s*को\s*(?:replace|रिप्लेस)", t, re.IGNORECASE)
    replace_target = replace_m.group(1) if replace_m else None
    if wrong is None and replace_target and any(g[0] == replace_target for g in groups):
        wrong = replace_target
    anchor: str | None = None
    baad = re.search(r"के बाद|केबाद|बाद|after", t, re.IGNORECASE)
    if baad:
        before = [g for g in groups if g[2] <= baad.start()]
        if before:
            anchor = max(before, key=lambda g: g[2])[0]
    rest = [g[0] for g in groups if g[0] != wrong and g[0] != anchor]
    if replace_target:
        # the replace target is the wrong value, never a 'correct' candidate
        rest = [x for x in rest if x != replace_target]
    if not rest:
        # no distinct 'correct' group: the anchor itself is the correct value
        # ('900 है, 0 नहीं' — smoke-6 t18) — usable for the ack line, but
        # _apply_correction refuses it (can't safely apply)
        if anchor:
            return (anchor, wrong, anchor)
        return None
    # Stray conversational single-digit tokens never poison the parse
    # (owner session 20260902_184247 t40: 'इसमें एक चेंज नाइन नाइन थ्री फोन
    # नहीं नाइन नाइन थ्री फाइव है' — the 'एक'->'1' group blocked 993->9935).
    if len(rest) > 1 and any(len(r) >= 2 for r in rest):
        rest = [r for r in rest if len(r) >= 2]
    if not rest:
        return None
    if len(set(rest)) == 1:
        if wrong is not None and rest[0] == wrong:
            return None  # 'wrong' repeated ('नाइन नाइन थी ... नाइन नाइन थी'
            # garbled t41): no distinct correct value -> not resolvable
        return (anchor, wrong, rest[0])
    return None


def _apply_correction(val: str, corr: tuple) -> str | None:
    """Repair the stored value from a correction spec. Prefers replacing the
    wrong substring; falls back to the longest prefix of the wrong substring
    that IS present (smoke-7 t14: wrong '420' -> the stored value has '42' ->
    replace '42' with '0000'); then to inserting the correct digits right
    after the anchor (t9: '1, 2 के बाद' -> insert 0000 after 12). A removal
    spec (correct=None, from _val_aware_correction: '1242 नहीं है' when 1242
    is in the value) removes the wrong substring. Returns None when nothing
    can be applied deterministically."""
    anchor, wrong, correct = corr
    if not val:
        return None
    if correct is None:
        # val-aware REMOVAL spec ('1242 नहीं है भाईया' with 1242 stored —
        # owner session 20260902_184247 t26): remove the named group.
        if wrong and wrong in val:
            return val.replace(wrong, "", 1)
        return None
    if correct == anchor:
        return None  # 'anchor is correct' -> remove-only correction, unsafe to
        # apply automatically (the owner re-dictates; the ack line covers it)
    if wrong:
        if wrong in val:
            return val.replace(wrong, correct, 1)
        for L in range(len(wrong) - 1, 1, -1):
            w = wrong[:L]
            if w in val:
                return val.replace(w, correct, 1)
    if anchor and anchor in val:
        return val.replace(anchor, anchor + correct, 1)
    return None


def _val_aware_correction(text: str, val: str) -> tuple | None:
    """Val-aware repair spec for edit-intent turns the text-only
    _parse_correction cannot resolve. Runs ONLY against a stored value:

      1. removal: a negation-named group that IS in the stored value and has
         no distinct 'correct' partner -> (None, wrong, None)  ('1242 नहीं
         है भाईया' with 1242 stored -> remove it, never wipe the whole value)
      2. change-frame pair: a change frame (कम/जगह/बदल/चेंज) mentioning an
         old value that IS stored followed by its replacement -> (None, old,
         new)  (t11 '...9000...900...बस एक जीरो कम हो ज' -> 9000->900)
    Returns None when nothing applies deterministically (the caller keeps the
    value and asks — an edit never wipes)."""
    t = text or ""
    if not val:
        return None
    groups = _digit_groups(t)          # [(digits, start, end)] — digit words
    # included with their real spans (never re-located by literal search)
    if not groups:
        return None
    negs = [m.start() for m in re.finditer(r"नहीं|nahi|गलत|galat", t, re.IGNORECASE)]
    if negs:
        # nearest-negation OCCURRENCE: min distance from that occurrence's END
        # to any negation (two occurrences of the SAME digits are distinct —
        # 'नाइन नाइन थी ... नहीं ... नाइन नाइन थी' has two '99' groups)
        wrong = min(groups, key=lambda g: min(abs(g[2] - n) for n in negs))
        wrong_digits = wrong[0]
        others = [g[0] for g in groups
                  if not (g[0] == wrong_digits and g[1] == wrong[1]
                          and g[2] == wrong[2])]
        # drop stray conversational singles
        if len(others) > 1 and any(len(o) >= 2 for o in others):
            others = [o for o in others if len(o) >= 2]
        if not others:
            if wrong_digits in val and wrong_digits != val:
                return (None, wrong_digits, None)      # removal
            return None
        if len(set(others)) == 1:
            if others[0] == wrong_digits:
                return None
            return (None, wrong_digits, others[0])
        return None
    # no negation -> change-frame old->new pair (mention order)
    if not _is_change_frame(t):
        return None
    mention = [g[0] for g in groups]
    for i, a in enumerate(mention):
        if len(a) < 2 or a not in val:
            continue
        for b in mention[i + 1:]:
            if b != a and len(b) >= 1:
                return (None, a, b)
    return None
STATUS_RE = re.compile(
    r"लिख लिया|लिख दिया|लिखा है|लिखा क्या|क्या लिखा|क्या लिखिया|क्या लिखी|"
    r"क्या लिखे|लिखे हो|लिखा तूने|लिखा तुमने|"
    r"note kiya|noted|ho gaya|हो गया|clear|क्लियर|क्लिअर|सुना कुछ|सुन कुछ|"
    r"क्या सुना|कहां गए|कहाँ गए|कहा गए|बताओ|बता दे|बोलो क्या|बोल क्या", re.IGNORECASE)

# 'तुने लिखा नहीं किया' — the user CLAIMS nothing was written while a value
# IS stored (owner smoke-6 t10). Re-speak the value (proof) instead of
# retrying/clearing. A plain rejection ('नहीं, गलत है') stays a retry.
CLAIM_RE = re.compile(r"लिखा नहीं|नहीं लिखा|लिखा ही नहीं", re.IGNORECASE)

# WRITING-COMPLAINT: the user says the agent did NOT / could NOT write it
# ("मैंने पूरा नंबर बोल दिया तुने लिखा किनहीं", "लिख नहीं पा रहा" — smoke-8
# t6/t14/t16/t17). NOT a rejection of a heard value — the user is angry the
# agent seems deaf. When a value IS stored -> RECALL it (proof); when the
# rail is still armed-empty -> a SPOKEN apology+ask (never silence, never
# the dismissive 'phir se bol na').
COMPLAINT_RE = re.compile(
    r"लिखा नहीं|नहीं लिखा|लिख नहीं|नहीं लिख|लिखा कि नहीं|लिखा की नहीं|किनहीं|"
    r"लिख पा रहा|लिख नहीं पा|लिखा तूने|लिखा तुमने|पूरा नंबर बोल|पूरा नंबर दे|"
    r"बोल दिया|बता दिया", re.IGNORECASE)

# Smoke-12 t14: a QUERY about the stored value that ALSO contains digit-ish
# words ('मैंने बोला था 5 बट 0 उसका क्या किया तुमने') must recall-as-proof,
# NEVER silently append the digit span. Requires the past-tense claim + a
# follow-up question in the same turn so mid-dictation talk never matches.
QUERY_STORED_RE = re.compile(
    r"(?:(?:बोला था|बोले थे|कहा था|कहे थे).{0,28}"
    r"(?:क्या किया|क्या हुआ|उसका क्या|कहाँ है|कहाँ गया))"
    r"|(?:लिखा है ना|लिखा ना|लिखा कि नहीं|लिखा के नहीं|लिखा की नहीं)",
    re.IGNORECASE)

# Smoke-12 t29/t32: an explicit TOPIC-SWITCH while a value is stored
# ('ज़िन के बारे में बताओ', 'वॉइस एजेंट के बारे में बताओ') releases the
# turn to the LLM instead of being stuck re-echoing the number. Guarded by
# _NUMBER_TOPIC_RE: when the topic IS the number itself ('अकाउंट नंबर के
# बारे में'), it stays a recall/status query.
TOPIC_SWITCH_RE = re.compile(r"के बारे में|बारे में|की बात कर", re.IGNORECASE)
_NUMBER_TOPIC_RE = re.compile(
    r"नंबर|नमबर|number|अकाउंट|खाता|मोबाइल|mobile|phone", re.IGNORECASE)

# A RE-ANNOUNCEMENT while armed must be a WRITE COMMAND ('लिख ले', 'लिखो',
# 'रख ले' — smoke-5 t20) — a past-tense query like 'क्या लिखा... नंबर'
# (smoke-8 t15) is a RECALL request, not a re-announcement, and must be
# ANSWERED, not silenced.
WRITE_COMMAND_RE = re.compile(
    r"लिख ले|लिखो|लिख दे|लिख दो|लिखना|लिख लेगा|लिख लूं|नोट कर|note कर|"
    r"रख ले|रख दे|रख दो", re.IGNORECASE)


def _dev_words(text: str) -> set[str]:
    return set(_DEVANAGARI_WORD_RE.findall(text or ""))


def _is_confirm(text: str) -> bool:
    t = text or ""
    if CONFIRM_EN_RE.search(t):
        return True
    return bool(_dev_words(t) & CONFIRM_DEV_WORDS)


def _is_reject(text: str) -> bool:
    t = text or ""
    if REJECT_EN_RE.search(t):
        return True
    if _dev_words(t) & REJECT_DEV_WORDS:
        if QUESTIONISH_RE.search(t):
            return False  # "क्यों नहीं बोला" — an inquiry, not a reject
        return True
    return False


# Dictation ANNOUNCEMENT ("एक मोबाइल नंबर है वो लिख ले" — owner smoke-4
# t15): the user tells the agent to WRITE a number. Arm the rail so the
# following dictation stays rail-owned (no LLM leak, no ack chatter).
WRITE_INTENT_RE = re.compile(
    r"लिख|write|note|record|रख ले|रख दे|सेव कर|save|दर्ज", re.IGNORECASE)
ANNOUNCE_RE = re.compile(
    r"मोबाइल|mobile|नंबर|नम्बर|number|अकाउंट|account|खाता|khata|आईडी|"
    r"\bid\b|code|कोड|otp|पिन|pin|aadhaar|आधार|pan|पैन|कार्ड|card",
    re.IGNORECASE)


def is_dictation_announcement(text: str) -> bool:
    """True when the user announces they will dictate a number/ID
    ('मोबाइल नंबर लिख ले'). Arms the rail (decide returns action='arm')."""
    t = text or ""
    return bool(WRITE_INTENT_RE.search(t) and ANNOUNCE_RE.search(t))


def _fragment_length(text: str) -> int:
    return len(re.findall(r"[\w\u0900-\u097F]+", text or ""))


# ---------------------------------------------------------------------------
# Speech formatting — the owner heard "not clear at all" when TTS read a
# dense digit string (Fish reads '001200005703' as one Indian-grouped NUMBER,
# dropping leading zeros). The rail ALWAYS speaks the value digit-by-digit as
# English digit words (crystal-clear, and Fish TTS handles them natively).
# ---------------------------------------------------------------------------
DIGIT_NAMES = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
               "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine"}


def speak_value(value: str) -> str:
    """Render a digit string for SPEECH: each digit as an English word.
    '026900' -> 'zero two six nine zero zero'."""
    return " ".join(DIGIT_NAMES.get(ch, ch) for ch in (value or ""))


# ---------------------------------------------------------------------------
# Deterministic lines (persona-consistent: masculine, Roman, <=2 sentences;
# rotation by turn_no — pick_line discipline, no randomness). {spoken} is
# speak_value(value) — always digit-by-digit, never a raw digit string.
# ---------------------------------------------------------------------------
ECHO_LINES = [                      # first dictation, single shot
    "haan, main suna: {spoken}. sahi hai na?",
    "theek hai, yeh note kiya: {spoken}. confirm kar de?",
    "maine suna: {spoken}. yeh hi na, confirm karo.",
]
FULL_LINES = [                      # finalize after silent accumulation
    "theek hai, poora number: {spoken}. sahi hai na?",
    "main suna: {spoken}. yeh hi hai na?",
]
ACK_LINES = [
    "haan, ho gaya — {spoken} note ho gaya.",
    "theek hai, {spoken} rakh liya.",
    "done — {spoken} confirm ho gaya.",
]
RETRY_LINES = [
    "haan? sahi number phir se bol na.",
    "theek, ek baar aur bolo — main sun raha hoon.",
]
RECALL_LINES = [
    "haan, jo likha hai: {spoken}.",
    "yeh raha number — {spoken}.",
    "note kiya hua hai: {spoken}.",
]
ARM_LINES = [                       # dictation announcement ("number likh le")
    "haan, bolo — number note karta hoon.",
    "theek hai, bol — main likh raha hoon.",
    "haan, bol number — main sun raha hoon.",
]
STATUS_LINES = [                    # armed but nothing stored yet; user asks
    "abhi kuch nahi likha hai — number bolo, note karta hoon.",   # if it was written / what was written
    "abhi kuch nahi aaya — number bolo, main sun raha hoon.",
]
HOLD_LINES = [                      # continuation cue mid-dictation ("आगे")
    "haan, sun raha hoon — bolo.",
    "haan, note kar raha hoon — bolo.",
]
CORRECTION_LINES = [                # structured correction acknowledged but
    # not deterministically applicable -> ask for a clean full re-dictation
    "theek, samajh gaya — {wrong} nahi, {correct} hai. poora number ek baar bol de, note kar loon.",
    "haan, {correct} theek hai. poori number phir se bolo — main note kar raha hoon.",
]
COMPLAINT_EMPTY_LINES = [           # writing-complaint while armed-empty
    "haan, sorry — abhi kuch nahi likha. phir se bol de na, main note kar loon.",
    "theek, main sun raha hoon — phir se bolo, note kar loon.",
]

CLARIFY_LINES = [                   # low-corr, no intent -> deterministic
    # "didn't catch that" prompt (owner T10: never silence on unclear speech)
    "nhi samjha — phir se bolo, main sun raha hoon.",
    "nhi suna sahi se — number bolo, phir se.",
    "didn't catch that — bolo, number kya hai?",
]
NUDGE_LINES = [                     # armed-empty, no digits for 2+ turns
    "sun raha hoon — number bolo, main note kar raha hoon.",
    "haan, bol — number kya hai?",
    "ready hoon — bolo number.",
]
CORRECT_ALREADY_LINES = [           # correction already satisfied by stored
    # value -> confirm, never wipe (smoke-12 t15)
    "haan, {correct} theek hai — poora number: {spoken}.",
    "{correct} sahi hai — poora number: {spoken}.",
]

# --- VALUE TRANSACTION LOCK line pools (docs/VALUE_TRANSACTION_LOCK.md) ---
HOLD_EDIT_LINES = [                 # L3: edit fragment parsed to nothing yet —
    # hold the instruction open, ask for the rest (never mutate, never silent)
    "haan — kya badalna hai? poora bolo.",
    "haan, bol — kaunsa hissa badalna hai?",
]
HOLD_REMOVAL_LINES = [              # L3: removal-only fragment ('520 नहीं है') —
    # the prefix of a replace; ask whether something goes in its place
    "{wrong} — hata doon, ya uski jagah kuch aayega?",
    "theek, {wrong} nahi — uski jagah kuch hai, ya bas hataana hai?",
]
REECHO_LINES = [                    # L1/L2: speak the PROPOSAL (unheard echo,
    # recall while a proposal is open, L4 status with a proposal open)
    "maine yeh samjha: {spoken}. sahi hai na?",
    "yeh samjha maine: {spoken} — sahi hai na?",
]
PROPOSAL_RECALL_LINES = [           # L1: recall distinguishes proposal from base
    "maine yeh samjha: {spoken} — pehle wala {base} tha. sahi hai na?",
    "naya: {spoken} — pehle wala {base} tha. yeh naya sahi hai na?",
]
REVERT_LINES = [                    # L1: plain reject of a proposal -> back to base
    "theek hai, pehle wala rakha: {spoken}. kya badalna hai?",
    "achha, wapas pehle wala: {spoken}. bolo kya galat hai?",
]
STATUS_ACTIVE_LINES = [             # L4: bounded silence -> status + escape
    "abhi mere paas {spoken} hai — aage bolo, ya 'bas' bolo.",
    "abhi yeh hai: {spoken}. aage bolo, ya 'bas' bol do.",
]
EDIT_CLARIFY_LINES = [              # L3: buffer closed with nothing usable
    "samjha nahi kya badalna hai — abhi yeh hai: {spoken}. kaunsa hissa galat hai, uski jagah kya?",
    "abhi yeh hai: {spoken}. isme kya badalna hai — kaunsa hissa, uski jagah kya?",
]
MIXED_CLARIFY_LINES = [             # M6: confirm + reject in ONE breath ('ठीक है,
    # ओके, चलो कोई नहीं' — owner session 20260905_102221 t21) -> the value
    # stays; ask which it was instead of wiping on the 'नहीं'
    "haan ya nahi? abhi yeh hai: {spoken}. sahi hai, ya kuch badalna hai?",
    "ek baar saaf bolo — abhi likha hai: {spoken}. rakhun, ya galat hai?",
]


def _line(lines: list[str], turn_no: int) -> str:
    return lines[turn_no % len(lines)]


def _correction_line(turn_no: int, wrong: str, correct: str) -> str:
    """Deterministic correction-ack line. When no wrong value was named
    (anchor-only correction), use the simpler template."""
    if wrong:
        tmpl = CORRECTION_LINES[turn_no % len(CORRECTION_LINES)]
        return tmpl.format(wrong=wrong, correct=correct)
    tmpl = "theek, samajh gaya — {correct} aage hai. poora number ek baar bol de, note kar loon."
    return tmpl.format(correct=correct)


def _already_correct_line(turn_no: int, correct: str, spoken: str) -> str:
    """Correction that the stored value ALREADY satisfies (smoke-12 t15:
    '5 बार 0' correction when 00000 is already in the value) -> confirm the
    whole number, never wipe it."""
    tmpl = CORRECT_ALREADY_LINES[turn_no % len(CORRECT_ALREADY_LINES)]
    return tmpl.format(correct=correct, spoken=spoken)


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------
def decide(user_text: str, engine: dict | None, turn_no: int,
           turn_meta: dict | None = None) -> dict | None:
    """One turn's rail decision. Pure + deterministic over (user_text, engine,
    turn_no). Returns None (normal LLM flow) or a dict:
        {"action": "echo_confirm"|"silent_accumulate"|"echo_full"|
                   "confirm_ack"|"retry"|"recall"|"silent"|"hold"|"status"|
                   "greet"|"arm",
         "value": <normalized digits>|"",
         "status": "pending"|"confirming"|"confirmed"|"discarded",
         "line": <deterministic reply or None for SILENT>,
         "raw": <verbatim STT span if this turn dictated one>}
    Mutates engine["dictation"] (session task state — never long-term memory)
    and engine["conv"] (the Conversation Controller's explicit state).

    Since track-5 (2026-08-31, owner smoke-11) the decision is owned by the
    Conversation Controller (agent/conversation_controller.py): the dictation
    task is an explicit state object and the transition an explicit table
    (rows 1-43, docs/CONVERSATION_CONTROLLER_DESIGN.md section 4). This
    function delegates; this module stays the SIGNAL layer (detectors,
    normalizers, deterministic line pools) + the rail's enforcement entry.
    """
    from agent.conversation_controller import controller_decide
    return controller_decide(user_text, engine, turn_no, turn_meta)

