"""Deterministic reply-side guards (Phase 5 behavioral tuning).

Two jobs, both ZERO-LLM and deterministic (same discipline as the turn
controller and validity gates):

1. trim_reply(text)        — hard safety net for reply length. The persona
   (C2) already asks for <=2 short sentences, but live sessions (2026-08-28,
   sessions 222656/224509) still produced 5-7.2s TTS replies. The prompt fix
   is the primary lever; this guard only catches pathological monologues
   (> REPLY_MAX_CHARS) by cutting at the last clean sentence boundary.

2. feminine_self_reference(text) — telemetry-only detector for the known
   persona violation (masculine self-reference). It does NOT rewrite the
   reply: a regex cannot reliably tell self-reference from third-person
   references to a female ("Rimmi so rahi thi" is correct speech), and an
   LLM rewrite pass is forbidden by the one-call contract. It only flags the
   turn in the session log so the prompt change can be measured.
"""
from __future__ import annotations

import re

# Safety-net cap. Owner directive 2026-08-29: length must follow content —
# substantive answers (costs, comparisons) need room, small talk stays short
# (persona V1.10 drives that). 240 chars ~ 15s spoken ceiling; the trim still
# cuts at sentence boundaries and keeps the full text in the log.
REPLY_MAX_CHARS = 240          # default (small talk / normal turns)
DETAIL_CHUNK_CAP = 110         # detailed answers: SMALL chunks (directive
                               # 2026-08-29 192439: no 10-15s monologues —
                               # detail is delivered across turns with
                               # checkpoints, not in one breath)


def cap_for(detail_mode: bool) -> int:
    """Spoken-chunk cap: detail mode delivers in small chunks; the semantic
    length is achieved across turns (persona V1.11 rule 1)."""
    return DETAIL_CHUNK_CAP if detail_mode else REPLY_MAX_CHARS


# explicit requests for detail ("detail mein samjhao", "poora batao",
# "ek-ek point", "step by step") — evidence: session 192439 t14-t28
DETAIL_RE = re.compile(
    r"detail|डिटेल|poora|poori|पूरा|पूरी"
    r"|ek[\s\-]?ek|एक\s+एक|step[\s\-]?by[\s\-]?step|point[\s\-]?by[\s\-]?point"
    r"|samjhao|समझाओ|explain|deeply|elaborate"
    r"|खुल\s*के|khul\s*ke|कैसे\s+काम",
    re.IGNORECASE)


def is_detail_request(text: str) -> bool:
    """True when the user explicitly asks for detail/depth."""
    return bool(DETAIL_RE.search(text or ""))

# Sentence end: Latin . ! ? or Devanagari danda, optionally followed by a
# closing quote/bracket, then whitespace or end-of-string.
SENT_END_RE = re.compile(r"[.!?।]+[\"')\]]*(\s+|$)")


def trim_reply(text: str, max_chars: int = REPLY_MAX_CHARS) -> tuple[str, bool]:
    """Return (spoken_text, trimmed). Cuts at the last sentence boundary that
    fits under max_chars; if the text has no boundary at all and exceeds the
    cap, cuts at the last word boundary under the cap."""
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text, False

    kept = ""
    for m in SENT_END_RE.finditer(text):
        sentence = text[:m.end()].rstrip()
        if len(sentence) > max_chars:
            break
        kept = sentence
    if kept:
        return kept, True

    # No usable sentence boundary — hard cut at last space.
    cut = text[:max_chars]
    sp = cut.rfind(" ")
    if sp > 40:
        return cut[:sp].rstrip(), True
    return cut.rstrip(), True


# Feminine FIRST-PERSON forms, telemetry-only. Precision rules:
#   - "rahi hoon / hain", "gayi hoon", future "-ungi" (rahungi, jaungi,
#     bataungi...) are ALWAYS first person -> flag unconditionally.
#   - "rahi thi" is ambiguous ("Rimmi so rahi thi" is correct speech about a
#     third person) -> flag ONLY in a sentence that has a first-person cue
#     (main/mein/I) and NO third-person cue (woh/usne/relation noun/proper
#     noun subject).
FEMININE_FIRST_RE = re.compile(
    r"\brahi\s+(?:hoon|hun|hain|hein)\b"
    r"|\b(?:gayi|aayi)\s+(?:hoon|hun)\b"
    r"|\brahi\s+hui\b"
    # 'sakna' constructions: 'main kar sakta hoon' (M) vs 'sakti' (F)
    r"|\bsakti\s+(?:hoon|hun|hain|thi)\b"
    r"|\b(?:jaanti|maanti|pehchaanti|chahti)\s+(?:hoon|hun)\b",
    re.IGNORECASE,
)
FEMININE_FUT_RE = re.compile(r"\b[a-z]{2,}ungi\b", re.IGNORECASE)
# Ambiguous forms: feminine ONLY in a first-person sentence without a
# third-person subject ("main ... kar sakti" = flag; "woh kar sakti hai" /
# "Rimmi so rahi thi" = correct speech, no flag).
FEMININE_AMBIG_RE = re.compile(r"\brahi\s+thi\b|\bsakti\b|\bchahti\b", re.IGNORECASE)
FIRST_PERSON_CUE = re.compile(r"\b(?:main|mein|i)\b|मैं", re.IGNORECASE)
THIRD_PERSON_CUE = re.compile(
    r"\b(?:woh|wo|vo|wah|usne|usko|uska|uski|unhone|unka|unki|behen|bahen|maa|mummy|"
    r"wife|patni|didi|bhabhi|beti|ladki|girlfriend|bestie)\b"
    r"|वो|वह|उसने|उसका|उसकी|उन्होंने|उनकी|आपकी|तुम्हारी",
    re.IGNORECASE,
)
PROPER_NOUN = re.compile(r"\b[A-Z][a-z]{1,15}\b")


def feminine_self_reference(text: str) -> str | None:
    """Return the first matching feminine self-form, or None. Telemetry only —
    never used to alter the spoken reply."""
    t = text or ""
    m = FEMININE_FIRST_RE.search(t)
    if m:
        return m.group(0)
    m = FEMININE_FUT_RE.search(t)
    if m:
        return m.group(0)
    # split sentences AND clauses (commas) — a feminine form agreeing with a
    # female ADDRESSEE in another clause ('Main yahin hoon, batao kya keh rahi
    # thi?') is correct speech, not a self-reference violation
    # Sentence-level check with an addressee-imperative escape: when the
    # feminine form sits in a clause commanded at the listener ('batao kya
    # keh rahi thi?'), the feminine agrees with the ADDRESSEE (correct
    # mirroring), not with the speaker. Evidence: t13 2026-08-29.
    for sent in re.split(r"[.!?।\n]+", t):
        m = FEMININE_AMBIG_RE.search(sent)
        if not m:
            continue
        if not FIRST_PERSON_CUE.search(sent):
            continue
        if THIRD_PERSON_CUE.search(sent):
            continue
        for clause in re.split(r"[,;]", sent):
            if not FEMININE_AMBIG_RE.search(clause):
                continue
            if re.search(r"\b(?:batao|bata|bolo|bol|sunao|dekho|sun)\b", clause, re.IGNORECASE):
                m = None
                break
        if m is None:
            continue
        # Proper-noun subject ("Rimmi intezaar kar rahi thi") — drop the
        # sentence-initial capitalized word before scanning.
        body = sent.split(None, 1)[1] if len(sent.split(None, 1)) > 1 else sent
        if PROPER_NOUN.search(body):
            continue
        return m.group(0)
    return None

# Stray transport-tag fragments that must never be spoken (belt-and-braces on
# top of fused_turn's head handling — evidence: session 091548 t30/t33 spoke
# '<perception>{...}' and '</p>' aloud when the model mis-closed the tag).
# t28 (2026-08-29): model also emitted misspelled closers (</parception>)
# CLASS-LEVEL (evidence t8 103824: '</s:perception>' — 5th variant): strip ANY
# XML-ish tag token. Spoken prose never legitimately contains <...> (persona
# forbids special characters), so this is safe and ends the variant whack-a-mole.
TAG_LEAK_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9_:.-]{0,24}>", re.IGNORECASE)
TAG_BLOCK_RE = re.compile(r"<perception>.*?(?:</perception>|</p>)",
                          re.DOTALL | re.IGNORECASE)
TAG_OPEN_TAIL_RE = re.compile(r"<perception>.*$", re.DOTALL | re.IGNORECASE)


def strip_tag_leak(piece: str) -> tuple[str, bool]:
    """Remove any transport-tag residue from a prose piece: a complete head
    block, an unclosed '<perception>...' tail, or stray tag tokens.
    Returns (clean, stripped?)."""
    clean = TAG_BLOCK_RE.sub("", piece)
    clean = TAG_OPEN_TAIL_RE.sub("", clean)
    clean = TAG_LEAK_RE.sub("", clean)
    return clean, clean != piece

def clean_specials(text: str) -> str:
    """Scrub characters that are never legitimate in spoken Hinglish prose
    (persona bans special characters). Evidence 141753 t65: 'j}}\n\njuis...'
    was SPOKEN — braces and raw newlines reached TTS."""
    import re as _re
    out = _re.sub(r"[{}<>\\^~|`*_#@]", "", text or "")
    return _re.sub(r"\s{2,}", " ", out)

# Model-EMITTED merged words (distinct from the chunk-boundary loss that
# smart_join fixes — evidence session 133659 t13 'sahikaam', t17 'baaremein'
# came out of the LLM already merged; flash-lite does this in romanized
# Hinglish). Deterministic lexicon: ONLY exact listed merges are split, so
# valid words can never be damaged. Extend as new cases are observed.
MERGE_SPLIT_LEXICON = {
    # smart_join-era splits observed in session 141753 (the join heuristic is
    # gone; these repair any text that still carries the damage)
    "th ik": "theek",
    "nah in": "nahin",
    "k ela": "kela",
    # model-emitted merges (103824 / 133659 / 141753)
    "sebaithne": "se baithne",
    "saathchalna": "saath chalna",
    "juis": "juice",
    "baaremein": "baare mein",
    "baremein": "bare mein",
    "sahikaam": "sahi kaam",
    "kartahoon": "karta hoon",
    "kartihu": "karti hoon",
    "rahahoon": "raha hoon",
    "rahihu": "rahi hoon",
    "kaisahai": "kaisa hai",
    "kyahal": "kya haal",
    "kyahaal": "kya haal",
    "nahiyaar": "nahi yaar",
    "yaarkya": "yaar kya",
    "chaltahai": "chalta hai",
    # session 182736 evidence
    "rahahai": "raha hai",
    "kyakar": "kya kar",
    "haidost": "hai dost",
    "sunraha": "sun raha",
    "dekhraha": "dekh raha",
    "batana": "bata na",  # only when intended; 'batana' is also valid ('tell me later')
}
# 'batana' is ambiguous — drop it to stay safe.
MERGE_SPLIT_LEXICON.pop("batana", None)


def fix_merged_words(text: str) -> str:
    """Split known merged Hinglish tokens (exact, word-boundary, case-insensitive).
    Lexicon-only: zero risk to unlisted words."""
    if not text:
        return text
    out = text
    for merged, split in MERGE_SPLIT_LEXICON.items():
        if merged in out.lower():
            out = re.sub(re.escape(merged), split, out, flags=re.IGNORECASE)
    return out

# ---- response-quality detectors (telemetry + variety guard) ----
# Evidence: sessions drifted into echo-confirm mode — 36% of one session's
# replies were 'X ki baat kar raha hai na?' style parrots.
CONFIRM_ECHO_RE = re.compile(
    r"\b(?:raha|rahe|rahi|hua|ho)\s+(?:hai|hain|he|ho)\s*(?:na|kya|toh)\s*\?*$"
    r"|\b\w+\s+(?:mangwane|mangvana|mangwana)\s+hain?\s*kya\s*\?*$", re.IGNORECASE)
DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


def is_confirm_echo(reply: str) -> bool:
    """True when a reply is mostly a parroted-back confirmation question."""
    r = (reply or "").strip()
    return bool(r) and len(r) < 70 and bool(CONFIRM_ECHO_RE.search(r))


def devanagari_present(reply: str) -> bool:
    return bool(DEVANAGARI_RE.search(reply or ""))


def shape_signature(reply: str) -> str:
    """Coarse shape of a reply (word-count bucket + ending), for repetition
    tracking across consecutive replies."""
    r = (reply or "").strip()
    n = len(r.split())
    bucket = "s" if n <= 4 else ("m" if n <= 9 else "l")
    if is_confirm_echo(r):
        bucket += "+confirm"
    elif r.endswith("?"):
        bucket += "+q"
    return bucket

# ---- challenge detector (user challenging a previous statement) ----
# Evidence: session 185741 t20-22 — Aiva said 5-10/min, user challenged,
# model flip-flopped twice instead of reconciling. When this fires, the next
# fused call gets a reconcile-claim nudge (see main.py parrot tracker).
CHALLENGE_RE = re.compile(
    "तूने|तुने|तुमने|tune\\s|tumne\\s"
    "|you\\s+(?:said|told)\\b"
    "|क्यों?\\s*बोला|kyon?\\s*bola"
    "|झूठ|jhoot|jhut|galat\\s*(?:bola|kaha)|\\bwrong\\b|contradict"
    "|(?:बता|बोल|bata|bol)\\s+"
    "(?:रहे|रहा|रही|rahe|raha|rahi)\\s+"
    "(?:थे|था|थी|the|tha|thi)(?=[\\s,.!?;:]|$)",
    re.IGNORECASE)


def is_challenge(text: str) -> bool:
    """True when the user is challenging/questioning a previous statement."""
    return bool(CHALLENGE_RE.search(text or ""))
