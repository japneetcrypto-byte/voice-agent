"""disclosure_capture — topic-blind DISCLOSURE detection (capture-confirm v2
feeder, owner-approved architecture 2026-09-02).

Design (docs/CAPTURE_CONFIRM_DESIGN_LOCK.md v2 + EPISODE_MEMORY_SLICE_LOCK.md):
  Conversation -> Disclosure Detection -> Atomic Verbatim Fact ->
  Confirmation/Trust Gate -> Episode grouping (the foundation) -> ...

This module is the DISCLOSURE DETECTION layer: pure, deterministic, stdlib.
It detects the STRUCTURE of a durable first-person self-disclosure — never
the topic — so "something else" topics need no new extractors. It returns the
VERBATIM clause (no LLM rewrite, no 5W1H interpretation). Capture is NOT
changed by this module: detection feeds a confirmation gate, never a silent
auto-write.

Boundaries (topic-blind by construction):
  - frames are discourse shapes (plan / like / identity / possession /
    ability / new-start / durable-past / origin), not topic vocabulary
  - negatives: questions, agent-directed speech, third-person statements,
    emotion/state-of-the-moment, digit-bearing utterances (PII: number
    dictation is the rail's job), backchannels, rail talk
  - no place/name gazetteer needed here (mention keys live in the episode
    foundation); the place-agnostic clause is what gets stored verbatim
"""
from __future__ import annotations

import re

# Clause splitter — same sentence-boundary style as extract_place_facts.
_SENT_RE = re.compile(r"[।.!?\n]+")

# Agent-directed / second person (word-boundary). 'तुने'/'तूने' included.
_SECOND_PERSON_RE = re.compile(
    r"(?:^|[^\w])(?:तू|तुम|आप|तेरा|तेरी|तुम्हारा|तुम्हारी|तुने|तूने|आपका|आपकी)"
    r"(?:$|[^\w])")

# Question words -> an inquiry, not a disclosure.
_QUESTION_RE = re.compile(
    r"कहां|कहाँ|कौन|कौनसा|कौन सा|किस|क्या|कैसे|कैसा|कब|कितने|कितना|क्यों|क्यूं|"
    r"who|what|when|where|why|how\b|kaun|kya|kahan|kab\b", re.IGNORECASE)

# Emotion / state-of-the-moment (Tier C: venting/moment, NOT a durable fact).
_STATE_WORDS = re.compile(
    r"पागल|थक|उदास|परेशान|खुश|बुरा|बुरी|बुरे|गुस्सा|गुस्से|ठीक|अच्छा|अच्छी|अच्छे|"
    r"तंग|दुखी|अकेला|अकेली|बोर|प्यास|भूख|नींद|काम\s+नहीं", re.IGNORECASE)

# Third-person subject markers (a statement about someone else, not the user).
_THIRD_PERSON_RE = re.compile(
    r"(?:^|[^\w])(?:राहुल|नीतु|रोहन|वो|वह|वे|वहां|वहाँ|उसका|उसकी|उसके|इसका|"
    r"उनका|उनकी|mera\s+dost|my\s+friend)\b", re.IGNORECASE)

# Digit-bearing utterance -> dictation/rail talk, never a disclosure (PII).
_DIGIT_RE = re.compile(r"\d")

# Number/mobile/account vocabulary -> rail domain, never a disclosure capture.
_NUMBER_TALK_RE = re.compile(
    r"नंबर|नमबर|number|अकाउंट|account|मोबाइल|mobile|खाता|डिजिट", re.IGNORECASE)

# ---- Topic-blind DISCLOSURE FRAMES (discourse shape, not topic) ----------
# plan/future (जाने वाला, जा रहा, प्लान, shift, ... वाला with a verb stem;
# roman Hinglish included: 'ja raha', 'jane wala', 'going to')
_PLAN_RE = re.compile(
    r"जाने?\s*वाला|जा\s*रहा|जा\s*रही|जाना\s+(?:है|था|होगा)|जाऊंगा|जाऊँगा|जाउंगा|"
    r"जाएंगे|जाएँगे|प्लान|प्लैन|घूमने|घुमने|जाने\s*का|सोच\s*रहा|"
    r"सोच\s*रही|करने\s*वाला|लेने\s*वाला|खरीदने\s*वाला|बनाने\s*वाला|होने\s*वाला|"
    r"\bja\s+raha|\bja\s+rha|jaane\s+wala|jane\s+wala|going\s+to|shift\b",
    re.IGNORECASE)
# like / dislike (मुझे X पसंद है, X अच्छा लगता है; roman 'pasand')
_LIKE_RE = re.compile(r"पसंद|अच्छा\s+लगता|अच्छी\s+लगती|पसंदीदा|\bpasand\b|\blike\b",
                      re.IGNORECASE)
# possession with content (मेरा/मेरी/मेरे X है / था)
_POSSESS_RE = re.compile(r"मेरा|मेरी|मेरे")
_POSSESS_COPULA_RE = re.compile(r"(?:है|था|थी|थे|हैं|होगा)\s*[।!?]?$|है\s+ना")
# ability / desire (मुझे X आता/आती/सीखना/चाहिए/करना है)
_ABILITY_RE = re.compile(
    r"मुझे|मुझको", re.IGNORECASE)
_ABILITY_FRAME_RE = re.compile(
    r"आता\s+है|आती\s+है|आता\s+था|सीखना\s+है|सीखना\s+चाहता|चाहिए|करना\s+है|"
    r"बनना\s+है|जाना\s+है|पढ़ना\s+है", re.IGNORECASE)
# new-start (मैंने X शुरू किया / नया X)
_START_RE = re.compile(
    r"(?:मैंने|मै|हमने)\s+.{0,30}?शुरू\s+किया|नया\s+\w+\s+शुरू|नई\s+\w+\s+शुरू|"
    r"started|शुरू\s+किया\s+है", re.IGNORECASE)
# durable past (मैं X गया/गई/गए था/थी/थे — a past trip/event, not the moment)
_PAST_RE = re.compile(
    r"(?:मैं|हम|मैंने|हमने).{0,35}?(?:गया|गई|गए|गये)\s+था|"
    r"(?:मैं|हम).{0,35}?(?:गया|गई|गए|गये)\s+हूं", re.IGNORECASE)
# origin (मैं X से हूँ) — X = a place-ish token (≥2 chars, not a stopword)
_ORIGIN_RE = re.compile(
    r"(?:मैं|मै|हम)\s+([\w\u0900-\u097F]{2,})\s+से\s+(?:हूं|हूँ|हैं)", re.IGNORECASE)
# durable residence/relation-ish (मेरी X Y में रहती है / रहता हूँ)
_RESIDE_RE = re.compile(
    r"रहत[ीा]\s+(?:है|हैं)|रहता\s+हूं|रहता\s+हूँ|रहती\s+हूं|रहती\s+हूँ|"
    r"बसता\s+हूं|बसती\s+हूं", re.IGNORECASE)

# Identity copula frame: मैं <attribute> हूँ — attribute NOT an emotion/state
_IDENTITY_RE = re.compile(r"(?:मैं|मै)\s+([\w\u0900-\u097F]{2,30}?)\s+(?:हूं|हूँ)\s*$", re.IGNORECASE)

# Confirm / reject answers to the capture question (short utterances only).
# Full-string anchored — a trailing \b is unreliable for Devanagari because
# vowel signs (ा, ी, ो, ँ ...) are NOT \w in Python regex.
_ANSWER_CONFIRM_RE = re.compile(
    r"^(?:हाँ|हां|haan|ha|yes|जी)\s*(?:हाँ|हां|जी|bilkul|बिल्कुल|note\s+kar\s+lo|"
    r"नोट\s+कर\s+लो|कर\s+लो|कर\s+दो|याद\s+रख\s+लो|रख\s+लो|सेव\s+कर\s+लो)?"
    r"[\s।!?]*$|"
    r"^(?:theek\s+hai|ठीक\s+है|हो\s+गया|हो\s+गई|हो\s+गयी|kar\s+diya|कर\s+दिया|"
    r"note\s+kar\s+liya|नोट\s+कर\s+लिया|note\s+kar\s+lo|नोट\s+कर\s+लो|kar\s+lo|"
    r"कर\s+लो|याद\s+रख\s+लो|रख\s+लो|save\s+kar\s+lo|सेव\s+कर\s+लो)"
    r"(?:\s+(?:na|ना|जी))?[\s।!?]*$",
    re.IGNORECASE)
_ANSWER_REJECT_RE = re.compile(
    r"^(?:नहीं|nahi|nhi|no|na)\s*(?:नहीं|nahi|nhi|no|नहीं\s+नहीं)?[\s।!?]*$|"
    r"^(?:मत\s+करो|मत\s+करना|न\s+करो|नहीं\s+करना|कोई\s+ज़रूरत\s+नहीं|"
    r"जरूरत\s+नहीं|कोई\s+बात\s+नहीं|drop\s+it)[\s।!?]*$",
    re.IGNORECASE)


def _has_second_person(clause: str) -> bool:
    return bool(_SECOND_PERSON_RE.search(clause))


def _has_third_person(clause: str) -> bool:
    return bool(_THIRD_PERSON_RE.search(clause))


def extract_disclosure_frames(text: str | None) -> list[dict]:
    """Topic-blind disclosure detection.

    Returns [{"content": "user: <verbatim clause>", "criterion": "explicit"}]
    for clauses that look like durable FIRST-PERSON self-disclosures. The
    content is the user's OWN clause, verbatim (no rewrite). Negatives are
    excluded above (questions / agent-directed / third-person / emotion /
    digits / number-talk). Nothing is written by this function — it only
    DETECTS, for the confirmation gate.
    """
    t = (text or "").strip()
    if len(t) < 8:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for clause in _SENT_RE.split(t):
        clause = clause.strip().strip(" ,;:،")
        if len(clause) < 6:
            continue
        if _DIGIT_RE.search(clause):
            continue                       # dictation/rail domain (PII guard)
        if _NUMBER_TALK_RE.search(clause):
            continue                       # rail domain, never a disclosure
        if _QUESTION_RE.search(clause):
            continue
        if _has_second_person(clause):
            continue                       # addressed TO the agent
        if _has_third_person(clause):
            continue                       # about someone else
        if _STATE_WORDS.search(clause):
            continue                       # emotion / moment, not durable
        low = clause.lower()
        frame = (
            bool(_PLAN_RE.search(low))
            or bool(_LIKE_RE.search(low))
            or bool(_START_RE.search(low))
            or bool(_PAST_RE.search(low))
            or bool(_ORIGIN_RE.search(low))
            or bool(_RESIDE_RE.search(low))
            or bool(_IDENTITY_RE.search(low))
            or (bool(_ABILITY_FRAME_RE.search(low))
                and bool(_ABILITY_RE.search(low)))
            or (bool(_POSSESS_RE.search(low))
                and bool(_POSSESS_COPULA_RE.search(low))
                and len(clause) >= 10)
        )
        if not frame:
            continue
        key = clause.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"content": f"user: {clause}", "criterion": "explicit"})
    return out


def capture_answer(text: str | None) -> str | None:
    """Classify a short user reply to the capture question: 'confirm' |
    'reject' | None (not an answer). Bounded vocabulary — deterministic."""
    t = (text or "").strip()
    if not t or len(t) > 60:
        return None
    if _ANSWER_CONFIRM_RE.search(t):
        return "confirm"
    if _ANSWER_REJECT_RE.search(t):
        return "reject"
    return None
