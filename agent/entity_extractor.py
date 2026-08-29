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
    (re.compile(r"(\w+)\s+(?:teri|tumhari|apni)?\s*(behen|behan|bahan|bahen|behena|भैन|भैना|बहन)\s+hai", re.IGNORECASE), "behen"),
    (re.compile(r"(\w+)\s+(?:teri|tumhari|apni)\s+(behen|behan|bahan|bahen|behena|भैन|भैना|बहन)", re.IGNORECASE), "behen"),
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
    "behen": ["behen", "behan", "bhen", "behena", "bahen", "bahin", "ben", "bain",
              "sister", "सिस्टर",
              "बहन", "बहिन", "बहेन", "बेन", "भैन", "भैना", "भाइन", "भाइनों"],
    "bhai": ["bhai", "bhaiya", "bhaiyya", "brother", "भाई", "भैया", "भइया"],
    "beta": ["beta", "bete", "son", "बेटा", "बेटे"],
    "beti": ["beti", "daughter", "बेटी"],
    "wife": ["wife", "biwi", "वाइफ", "बीवी", "पत्नी", "patni"],
    "pati": ["pati", "husband", "पति", "पती"],
    "maa": ["maa", "mummy", "mom", "mother", "माँ", "मां", "मम्मी"],
    "papa": ["papa", "dad", "father", "पापा", "डैड", "पिता"],
    "dost": ["dost", "friend", "दोस्त"],
    "manager": ["manager", "boss", "मैनेजर"],
    "bhatiji": ["bhatiji", "भतीजी", "niece"],
}

# FIRST-PERSON possessives that anchor a relation to the USER (evidence
# 094645 t18/t21: 'नीतु मेरी भैना', 'नीतु मेरी सिस्टर है'). Third-person
# possessives (उसकी/uski) are deliberately excluded — that is someone else's
# relation, not the user's.
FIRST_PERSON_POSSESSIVES = {
    "mera", "meri", "mere", "hamara", "hamari", "hamare",
    "मेरा", "मेरी", "मेरे", "हमारा", "हमारी", "हमारे",
}

# Tokens that are NEVER a name (pronouns/possessives/postpositions/grammar).
USER_STOPWORDS = {
    "mera", "meri", "mere", "tera", "teri", "tere", "tumhara", "tumhari",
    "apna", "apni", "apne", "uska", "uski", "uske", "usne", "usko", "us",
    "unka", "unki", "unke", "unhone", "unki", "vo", "wo", "woh", "vah",
    "yeh", "ye", "yah", "kisi", "kisi", "kiska", "kiski", "mera",
    "ka", "ki", "ke", "ko", "se", "ne", "mein", "me", "bhi", "to", "toh",
    "hai", "hain", "thi", "tha", "the", "hu", "hoon", "hun", "na", "nahi",
    # common VERB forms — a verb before/after a relation word is grammar,
    # not a name (evidence session 182736 t7: 'कहां गए भाई' captured गए='went'
    # as a person). Extend as observed.
    "गए", "गया", "गई", "गये", "आए", "आया", "आई", "कर", "करें", "करो", "करना",
    "देख", "देखो", "जा", "जाओ", "आ", "आओ", "रहा", "रहे", "रही", "हो", "होगा",
    "gaya", "gayi", "gayo", "gae", "aaya", "aayi", "karo", "karen", "karna",
    "dekho", "jao", "aao", "raha", "rahe", "rahi", "hoga",
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
    # Garble guard (evidence 2026-08-29 t3: 'काब बेटे' — a 2-word STT garble of
    # 'kahan' — was captured as a relationship and polluted memory). A real
    # relationship statement carries context around the name+relation pair.
    if len(words) < 3:
        return []
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
        # Orientation 2: FIRST-PERSON possessive + relation + NAME ('meri behen Neetu')
        prev = words[i - 1].lower() if i > 0 else ""
        next_word = words[i + 1].lower() if i + 1 < len(words) else ""
        if next_word and next_word not in USER_STOPWORDS and next_word not in USER_REL_WORDS:
            # Orientation 2: FIRST-PERSON possessive + relation + NAME
            if prev in FIRST_PERSON_POSSESSIVES:
                candidates.append(words[i + 1])
        # Orientation 3 (evidence t18/t21: 'नीतु मेरी भैना', 'नीतु मेरी सिस्टर है'):
        # NAME + FIRST-PERSON possessive + relation — used when orientation 2
        # has no usable name after the relation word.
        if prev in FIRST_PERSON_POSSESSIVES and i >= 2 and not (
                next_word and next_word not in USER_STOPWORDS
                and next_word not in USER_REL_WORDS
                and prev in FIRST_PERSON_POSSESSIVES):
            before = words[i - 2].lower()
            if before not in USER_STOPWORDS and before not in USER_REL_WORDS:
                candidates.append(words[i - 2])
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
