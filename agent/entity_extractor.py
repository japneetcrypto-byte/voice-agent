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

# PLACE mention-key vocabulary — SEED (episode-memory slice 2026-09-02,
# docs/EPISODE_MEMORY_SLICE_LOCK.md). Powers deterministic place-mention keys
# only: a recall ACCELERATOR. Keys never fire capture, never gate recall,
# never interpret, and never enter live context. Both scripts map to one
# Roman canonical key so cross-session restatements key-match regardless of
# how the STT wrote the city. Extend deterministically; capture is untouched.
_PLACE_ALIASES = {
    "kanpur": ("kanpur", "कानपुर", "कानपूर"),
    "jaipur": ("jaipur", "जयपुर"),
    "delhi": ("delhi", "दिल्ली", "dilli"),
    "nainital": ("nainital", "नैनीताल"),
    "pune": ("pune", "पुणे"),
    "mumbai": ("mumbai", "मुंबई", "bombay"),
    "lucknow": ("lucknow", "लखनऊ"),
    "gurgaon": ("gurgaon", "गुड़गांव", "गुडगांव"),
    "bangalore": ("bangalore", "bengaluru", "बेंगलुरु", "बैंगलोर"),
    "uttarakhand": ("uttarakhand", "उत्तराखंड"),
    "kashmir": ("kashmir", "कश्मीर"),
    "goa": ("goa", "गोवा"),
    "agra": ("agra", "आगरा"),
    "varanasi": ("varanasi", "वाराणसी", "banaras", "बनारस"),
    "rishikesh": ("rishikesh", "ऋषिकेश"),
    "dehradun": ("dehradun", "देहरादून"),
    "chandigarh": ("chandigarh", "चंडीगढ़"),
    "hyderabad": ("hyderabad", "हैदराबाद"),
    "kolkata": ("kolkata", "कोलकाता", "calcutta"),
    "amritsar": ("amritsar", "अमृतसर"),
    "shimla": ("shimla", "शिमला"),
    "mussoorie": ("mussoorie", "मसूरी"),
}
_PLACE_LOOKUP = {variant.lower(): canon
                 for canon, variants in _PLACE_ALIASES.items()
                 for variant in variants}


def extract_place_mentions(text: str) -> list[str]:
    """Deterministic place-mention keys (Roman canonical, lowercased).

    Reuses the place alias vocabulary above; a token is a key ONLY if it is
    in that vocabulary (unknown places -> no key -> fact still stored and
    recallable by content). Never used for capture — annotation only."""
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for tok in _WORD_RE.findall(text):
        canon = _PLACE_LOOKUP.get(tok.lower())
        if canon and canon not in seen:
            seen.add(canon)
            out.append(canon)
    return out

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
    # relative pronouns / connectives that precede relation words
    # (evidence session 200615 t11: 'जो बेटा, तुमसे नहीं हो पाएगा' captured
    # 'जो'='who/that' as a person)
    "जो", "जो कि", "जब", "तब", "कि वो", "jo", "jab",
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

# ---------------------------------------------------------------------------
# PLACE / TRAVEL FACT capture (owner smoke-13 follow-up: "agent is not able
# to retrieve from memory, is hallucinating — it shared wrong places from
# Uttarakhand"). Root cause: the compact perception head hardcodes
# head["memory_candidates"] = [], so nothing about the user's places/trips
# was ever stored — cross-session recall had nothing to retrieve and the
# LLM fabricated. This extractor captures LOCATION clauses deterministically
# (no LLM) as episodic candidates; main.py promotes them like relationships
# (pending-first, repeat-confirm) into the memory store.
# ---------------------------------------------------------------------------
_PLACE_SIGNAL_RES = [
    re.compile(r"गया\s+था|गई\s+थी|गए\s+थे|गये\s+थे|गयी\s+थी|गया\s+हूं|गया\s+हूँ|गई\s+हूं|गए\s+हैं"),
    re.compile(r"जाता\s+हूं|जाता\s+हूँ|जाती\s+हूं|जाते\s+हैं|जा\s+रहा|जा\s+रही"),
    re.compile(r"आया\s+था|आई\s+थी|आए\s+थे|आया\s+हूं|आया\s+हूँ|आई\s+हूं|आए\s+हैं"),
    re.compile(r"घूमने|घूमा\s+था|घूमी\s+थी|घूमे\s+थे|घूमा\s+हूं|घूमा\s+हूँ"),
    re.compile(r"रहता\s+हूं|रहता\s+हूँ|रहती\s+हूं|रहते\s+हैं|बसता\s+हूं|बसता\s+हूँ|बसती\s+हूं"),
    re.compile(r"[\w\u0900-\u097f]{2,}\s+से\s+(?:हूं|हूँ|हैं)"),  # origin: 'कानपुर से हूँ'
    re.compile(r"\b(?:went|visited?|visit(?:ed)?|travell?ed?|traveled|trip|tour|stayed?|"
               r"live[sd]?\s+in|living\s+in|from)\b", re.IGNORECASE),
]
_PLACE_VERB_SET = {
    "गया", "गई", "गए", "गये", "गयी", "था", "थी", "थे", "हूं", "हूँ", "है", "हैं",
    "जाता", "जाती", "जाते", "रहता", "रहती", "रहते", "बसता", "बसती", "आया", "आई",
    "आए", "घूमने", "घूमा", "घूमी", "घूमे", "से", "में", "को", "से", "कर", "की",
    "का", "के", "और", "या", "visited", "visit", "went", "trip", "tour", "from",
    "live", "lived", "living", "stay", "stayed", "travel", "travelled", "traveled",
}
_PLACE_PRONOUNS = {
    "मैं", "मैने", "मैंने", "मेरा", "मेरी", "मेरे", "हम", "हमने", "हमारा", "हमारी",
    "तुम", "तुमने", "तुम्हारा", "तुम्हारी", "आप", "आपने", "वो", "वह", "यह", "वे",
    "ये", "उस", "उन", "main", "mai", "hum", "tum", "aap", "wo", "woh", "ye",
    "i", "me", "my", "we", "our", "you", "your",
}
_PLACE_QUESTION_WORDS = ("कहां", "कहाँ", "क्या", "कौन", "कौनसा", "कौन सा", "किस", "कहा")
_PLACE_SENT_SPLIT_RE = re.compile(r"[।.!?\n]+")


def extract_place_facts(text: str) -> list[dict]:
    """Deterministic episodic capture of the user's PLACE/TRAVEL statements.

    Splits the transcript into clauses (। . ? ! newline), keeps clauses that
    contain a location/travel signal, and returns:
        {"type": "episodic", "content": "user: <verbatim clause>",
         "criterion": "salient"}
    Conservative by design (the memory gate is the last line of defense):
      - no signal (गया/घूमने/रहता/से हूँ/went/visited/...) -> no capture
      - question words ('कहां से हो') -> no capture (it is addressed TO us)
      - bare verb with no content word ('मैं गया था') -> no capture
      - digit-heavy dictation turns -> no capture
    """
    if not text:
        return []
    out = []
    seen = set()
    for clause in _PLACE_SENT_SPLIT_RE.split(text):
        clause = clause.strip().strip(" ,;:،")
        if len(clause) < 10:
            continue
        low = clause.lower()
        if any(w in low for w in _PLACE_QUESTION_WORDS):
            continue
        if not any(r.search(low) for r in _PLACE_SIGNAL_RES):
            continue
        toks = _WORD_RE.findall(clause)
        if len(toks) < 3:
            continue
        digits = sum(1 for t in toks if t.isdigit())
        if digits > len(toks) // 2:
            continue
        content = [t for t in toks if t.lower() not in USER_STOPWORDS
                   and t.lower() not in _PLACE_VERB_SET
                   and t.lower() not in _PLACE_PRONOUNS]
        if not content:
            continue
        key = clause.lower()
        if key not in seen:
            seen.add(key)
            out.append({"type": "episodic", "content": f"user: {clause}",
                        "criterion": "explicit"})
    return out


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


# ---------------------------------------------------------------------------
# EXPLICIT FACTS + PREFERENCES (memory continuity slice #2, owner 2026-08-31):
# "name, job, 'no advice', etc. Use the same pending -> confirm -> commit
# pattern. No LLM free-form memory writes." Deterministic first-person
# statement capture only; question words and pronouns are never captured;
# the caller routes candidates through the MemoryGate (explicit -> commit).
# ---------------------------------------------------------------------------
_NAME_FACT_RE = re.compile(
    r"(?:मेरा नाम|mera naam|my name is)\s+([\w\u0900-\u097F]{2,25})",
    re.IGNORECASE)
_JOB_WORDS = {
    "engineer", "doctor", "teacher", "manager", "businessman", "business",
    "student", "lawyer", "accountant", "consultant", "designer", "developer",
    "driver", "officer", "employee", "trader", "इंजीनियर", "डॉक्टर", "डाक्टर",
    "टीचर", "मैनेजर", "व्यापारी", "वकील", "लेखाकार", "छात्र", "छात्रा",
}
_JOB_RE = re.compile(
    r"\b(?:मैं|main)\s+(?:एक\s+)?([\w\u0900-\u097F]{2,25})", re.IGNORECASE)
_LIKE_RE = re.compile(
    r"(?:मुझे|mujhe)\s+([\w\u0900-\u097F]{2,25})\s+(?:बहुत\s+)?पसंद\s+(?:है|हैं)",
    re.IGNORECASE)
_NO_ADVICE_RE = re.compile(
    r"(?:सलाह|advice).{0,12}(?:मत|नहीं|nahi|no|mat)", re.IGNORECASE)
_FACT_QUESTION_WORDS = {
    "क्या", "कौन", "कौनसा", "कौन सा", "किस", "कहां", "कहाँ", "क्यों", "कैसे",
    "what", "who", "which", "where", "why", "how",
}
_FACT_PRONOUN_STOP = {
    "वह", "यह", "वो", "ये", "उस", "उन", "यहाँ", "यहीं", "वहाँ", "वहां",
    "he", "she", "it", "that", "this", "there", "here",
}


def extract_fact_candidates(text: str) -> list[dict]:
    """Deterministic capture of EXPLICIT first-person facts/preferences:
    name, job (allowlist), likes, no-advice. Returns
        {"type": "semantic"|"preference", "content": <readable fact>,
         "criterion": "explicit"}
    Conservative: questions ('मेरा नाम क्या है'), pronouns ('मुझे वह पसंद
    है'), and non-fact speech never fire. The caller commits via the memory
    gate (explicit -> commit immediately, per STATE_MODEL 4.5)."""
    if not text:
        return []
    out: list[dict] = []
    seen = set()

    def add(typ: str, content: str) -> None:
        key = (typ, content.lower())
        if key not in seen:
            seen.add(key)
            out.append({"type": typ, "content": content, "criterion": "explicit"})

    m = _NAME_FACT_RE.search(text)
    if m and m.group(1).lower() not in _FACT_QUESTION_WORDS:
        add("semantic", f"user's name is {m.group(1)}")
    m = _JOB_RE.search(text)
    if m and m.group(1).lower() in _JOB_WORDS:
        add("semantic", f"user is a {m.group(1).lower()}")
    for m in _LIKE_RE.finditer(text):
        tok = m.group(1)
        if tok.lower() not in _FACT_QUESTION_WORDS and tok.lower() not in _FACT_PRONOUN_STOP:
            add("preference", f"user likes {tok}")
    if _NO_ADVICE_RE.search(text):
        add("preference", "no advice unless explicitly asked")
    return out
