"""Entity extractor — pulls structured data from LLM prose replies.

Deterministic pattern matching only. No LLM calls. No interpretation.
Extracts: names, relationships, preferences mentioned in Aiva's response.

The LLM's conversational reply already contains the semantic information
we need (e.g. "achha, Gaggu tera beta hai" → entity=Gaggu, relation=beta).
This module extracts it so the state compiler can track it.
"""
from __future__ import annotations

import re

# Relationship patterns: Aiva's confirmations reveal what the user said
RELATION_PATTERNS = [
    # "achha, X tera beta hai" / "X teri behen hai"
    (re.compile(r"(\w+)\s+(?:tera|tumhara|apna)?\s*(beta|बेटा)\s+hai", re.IGNORECASE), "beta"),
    (re.compile(r"(\w+)\s+(?:tera|tumhara|apna)\s+(beta|बेटा)", re.IGNORECASE), "beta"),
    (re.compile(r"(\w+)\s+(?:tera|tumhara|apna)?\s*(bhai|भाई)\s+hai", re.IGNORECASE), "bhai"),
    (re.compile(r"(\w+)\s+(?:tera|tumhara|apna)\s+(bhai|भाई)", re.IGNORECASE), "bhai"),
    (re.compile(r"(\w+)\s+(?:teri|tumhari|apni)?\s*(behen|बहन|behena|भैन|भैरन)\s+hai", re.IGNORECASE), "behen"),
    (re.compile(r"(\w+)\s+(?:teri|tumhari|apni)\s+(behen|बहन|behena|भैन)", re.IGNORECASE), "behen"),
    (re.compile(r"(\w+)\s+(?:teri|tumhari|apni)?\s*(wife|वाइफ|biwi|बीवी)\s+hai", re.IGNORECASE), "wife"),
    (re.compile(r"(\w+)\s+(?:tera|tumhara|apna)?\s*(pati|पति|पती)\s+hai", re.IGNORECASE), "pati"),
    (re.compile(r"(\w+)\s+(?:tera|tumhara|apna)?\s*(pati|पति|पती)", re.IGNORECASE), "pati"),
    (re.compile(r"(\w+)\s+(?:tera|tumhara|apna)\s+(dost|friend|दोस्त)", re.IGNORECASE), "dost"),
    (re.compile(r"(\w+)\s+(?:tera|tumhara|apna)\s+(manager|boss)", re.IGNORECASE), "manager"),
    (re.compile(r"(\w+)\s+(?:teri|tumhari|apni)\s+(bhatiji|भतीजी|niece)", re.IGNORECASE), "bhatiji"),
    (re.compile(r"(\w+)\s+(?:tera|tumhara|apna)\s+(beta|son)", re.IGNORECASE), "beta"),
]

# Preference patterns
PREF_PATTERNS = [
    (re.compile(r"(?:wants? to|chahiye|mangwana)\s+(?:play\s+)?(\w+)", re.IGNORECASE), "wants_to_play"),
    (re.compile(r"(?:name is|naam hai)\s+(\w+)", re.IGNORECASE), "has_name"),
]


# Known ASR variant groups — map variants to canonical name
ALIAS_GROUPS = {
    "gaggu": ["gaggu", "gagu", "गग्गू", "गगू", "गगु", "gagoo", "gggu"],
    "neetu": ["neetu", "nittu", "नीतू", "नित्तु", "netu", "nitu", "नितु",
              "नीटु", "नीतु", "nitoo"],
    "rimi": ["rimi", "rimmi", "रिमी", "रिम्मी", "rimmee"],
}

# Relation words as USERS say them (broader than RELATION_PATTERNS, which
# match Aiva's replies). "ben/बेन" = Gujarati for behen (evidence: session
# 20260829_083519 — 'नीतु बेन', 'Neetu Ben', 'नीतु भाइनों' garble).
USER_REL_WORDS = {
    "behen": ["behen", "bhen", "behena", "bahen", "ben", "bain",
              "बहन", "बहिन", "बहेन", "बेन", "भैन", "भाइन", "भाइनों"],
    "bhai": ["bhai", "bhaiya", "bhaiyya", "भाई", "भैया", "भइया"],
    "beta": ["beta", "बेटा", "बेटा"],
    "beti": ["beti", "बेटी"],
    "wife": ["wife", "biwi", "वाइफ", "बीवी", "पत्नी", "patni"],
    "pati": ["pati", "पति", "पती"],
    "dost": ["dost", "friend", "दोस्त"],
    "manager": ["manager", "boss", "मैनेजर"],
    "bhatiji": ["bhatiji", "भतीजी", "niece"],
}

# Tokens that are NEVER a name (pronouns/possessives/postpositions/grammar).
USER_STOPWORDS = {
    "mera", "meri", "mere", "tera", "teri", "tere", "tumhara", "tumhari",
    "apna", "apni", "apne", "uska", "uski", "uske", "usne", "usko", "us",
    "unka", "unki", "unke", "unhone", "unki", "vo", "wo", "woh", "vah",
    "yeh", "ye", "yah", "kisi", "kisi", "kiska", "kiski", "mera",
    "ka", "ki", "ke", "ko", "se", "ne", "mein", "me", "bhi", "to", "toh",
    "hai", "hain", "thi", "tha", "the", "hu", "hoon", "hun", "na", "nahi",
    "एक", "वो", "वह", "यह", "उसका", "उसकी", "उसके", "उसने", "उसको", "उनका",
    "उनकी", "उन्होंने", "किसी", "किसका", "किसकी", "मेरा", "मेरी", "मेरे",
    "तेरा", "तेरी", "तुम्हारा", "तुम्हारी", "अपना", "अपनी", "अपने",
    "का", "की", "के", "को", "से", "ने", "में", "भी", "तो", "है", "हैं",
    "था", "थी", "थे", "ना", "नहीं", "और", "bata", "बता", "बोलो", "bolna",
}

_WORD_RE = re.compile(r"[\w\u0900-\u097F]{2,25}", re.UNICODE)


def extract_entities_from_user_text(text: str) -> list[dict]:
    """Extract relationship facts the USER states about themselves.

    Deterministic (C6 discipline: no LLM call). Catches patterns ASR actually
    produces, e.g. evidence session 20260829_083519:
      'नीतु बहन एक टीचर है...'            -> Neetu / behen
      'हाँ, नीतु बेन के बारे में...'        -> Neetu / behen (ben->behen)
      'mera dost Rohan...'                -> Rohan / dost
    Skips pronoun/possessive 'names' (उसकी बहन, uski behen) — only explicit
    name+relation pairs are returned. Caller commits via the memory store
    (dedup by content is handled there).
    """
    if not text:
        return []
    words = _WORD_RE.findall(text)
    entities = []
    seen = set()
    for i, w in enumerate(words):
        low = w.lower().strip()
        relation = None
        for rel, variants in USER_REL_WORDS.items():
            if low in variants:
                relation = rel
                break
        if not relation:
            continue
        # Orientation 1: NAME before relation word ('नीतु बहन एक टीचर है')
        candidates = []
        if i > 0 and words[i - 1].lower() not in USER_STOPWORDS \
                and words[i - 1].lower() not in USER_REL_WORDS:
            candidates.append(words[i - 1])
        # Orientation 2: possessive + relation + NAME ('meri behen Neetu')
        if i + 1 < len(words):
            nxt = words[i + 1].lower()
            if nxt not in USER_STOPWORDS and nxt not in USER_REL_WORDS:
                # only if the previous word WAS a possessive (first-person
                # anchor), else 'relation ke baare mein' style would misfire
                prev = words[i - 1].lower() if i > 0 else ""
                if prev in ("mera", "meri", "mere", "मेरा", "मेरी", "मेरे"):
                    candidates.append(words[i + 1])
        for name in candidates:
            canonical = normalize_entity(name)
            key = (canonical.lower(), relation)
            if key not in seen and len(canonical) >= 2 \
                    and canonical.lower() not in USER_STOPWORDS:
                seen.add(key)
                entities.append({"name": canonical, "relation": relation})
    return entities

def normalize_entity(name: str) -> str:
    """Map ASR variants to a canonical (Capitalized) form."""
    low = name.lower().strip()
    for canonical, variants in ALIAS_GROUPS.items():
        if low in variants:
            return canonical.capitalize()
    return name.strip()


def extract_entities_from_reply(reply: str) -> list[dict]:
    """Extract entity-relation pairs from Aiva's conversational reply.

    When Aiva says 'achha, Gaggu tera beta hai', it confirms the user
    mentioned Gaggu as their beta. We extract this for the state compiler.
    """
    if not reply:
        return []
    entities = []
    seen = set()
    for pattern, relation in RELATION_PATTERNS:
        for match in pattern.finditer(reply):
            name = match.group(1).strip()
            norm = name.lower()
            canonical = normalize_entity(name)
            cnorm = canonical.lower()
            if cnorm not in seen and len(canonical) >= 2:
                seen.add(cnorm)
                entities.append({"name": canonical, "relation": relation})
    return entities


def extract_preferences_from_reply(reply: str) -> list[dict]:
    """Extract preference statements from Aiva's reply."""
    if not reply:
        return []
    prefs = []
    for pattern, pref_type in PREF_PATTERNS:
        for match in pattern.finditer(reply):
            value = match.group(1).strip()
            if len(value) >= 2:
                prefs.append({"type": pref_type, "value": value})
    return prefs
