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
    "gaggu": ["gaggu", "gagu", "गग्गू", "गगू", "gagoo", "gggu"],
    "neetu": ["neetu", "nittu", "नीतू", "नित्तु", "netu"],
    "rimi": ["rimi", "rimmi", "रिमी", "रिम्मी", "rimmee"],
}

def normalize_entity(name: str) -> str:
    """Map ASR variants to a canonical form."""
    low = name.lower().strip()
    for canonical, variants in ALIAS_GROUPS.items():
        if low in variants:
            return canonical
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
