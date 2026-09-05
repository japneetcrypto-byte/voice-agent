"""Numeric Observation Boundary — Phase 1 (docs/NUMERIC_OBSERVATION_LOCK.md,
owner-approved 2026-09-04): the observation RECORD.

UNIT/INVARIANT coverage (not a fixture). Phase 1 produces the record and
archives it; NOTHING consumes it yet (Q11 build order (i)). These tests pin
the record's semantics so Phase 3 consumes a stable, versioned object:

  §2  schema shape, per-position slots, descriptive-only surface
  N1  OOV inside a numeric span -> UNKNOWN slot, item kept, INCOMPLETE
  N3  same digits, different surface -> same slots, COMPLETE (property test)
  N4  '5 जीरो' -> AMBIGUOUS {50 | 00000}; explicit forms stay DIGIT
  N5  instruction words are non_numeric_tokens, never slots
  N7  pure, stateless, JSON-normal, versioned, written once
  E   the 133627 turns the lock enumerates (§13 E)

Run: python3 phase5/tests/test_numeric_observation.py
"""
import sys, os, json, random, inspect, hashlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.numeric_observation import (observe, build_record, attach_observation, pure_view,
                                       digits_of, reading, slot_kinds, VERSION, lexicon_hash,
                                       LEXICON_PARTS, _HINDI_COUNT_WORDS, OOV_MAX_LEN, OOV_MAX_RUN)
from agent.precision_rail import DIGIT_WORD_MAP, DOUBLE_WORDS, TIMES_WORDS

fails = 0
def check(label, got, want):
    global fails
    if got == want:
        print(f"  ✓ {label}")
    else:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")

def kinds(rec, i=0):
    return slot_kinds(rec["items"][i])

def provs(rec, i=0):
    return [s["provenance"] for s in rec["items"][i]["slots"]]

print("== §2 schema ==")
r = observe("026900", turn_no=3)
check("t3 '026900' COMPLETE", r["certainty"], "COMPLETE")
check("one item, six LITERAL slots", (len(r["items"]), provs(r)), (1, ["LITERAL"] * 6))
check("digits", digits_of(r), "026900")
check("surface RUN (descriptive)", r["items"][0]["surface"], "RUN")
check("boundary flush both ends", r["items"][0]["boundary"], {"starts_at_turn_start": True, "ends_at_turn_end": True})
check("span verbatim", r["items"][0]["span"], {"start": 0, "end": 6, "text": "026900"})
check("no non-numeric tokens", r["non_numeric_tokens"], [])
check("top-level keys", sorted(r.keys()),
      sorted(["version", "turn", "source", "endpoint", "items", "non_numeric_tokens", "certainty", "reasons"]))
check("item keys", sorted(r["items"][0].keys()),
      sorted(["span", "slots", "surface", "group_breaks", "boundary", "unknown_count", "ambiguous_count"]))
check("slot keys", sorted(r["items"][0]["slots"][0].keys()),
      sorted(["kind", "digit", "alternatives", "token", "provenance", "count_token", "confidence"]))
check("confidence null today (no per-token STT confidence)", r["items"][0]["slots"][0]["confidence"], None)
check("turn recorded", r["turn"], 3)
check("source carries text + sha1", (r["source"]["text"], r["source"]["text_sha1"]),
      ("026900", hashlib.sha1("026900".encode("utf-8")).hexdigest()))

r = observe("1, 2, 5, 8, 0, 1, 2, 0, 3")
check("t4 comma list: 9 LITERAL slots, COMPLETE (confidently-wrong '8' is a DIGIT — honest limit)",
      (r["certainty"], provs(r), digits_of(r)), ("COMPLETE", ["LITERAL"] * 9, "125801203"))
check("surface SEPARATED, group_breaks at every token boundary", (r["items"][0]["surface"], r["items"][0]["group_breaks"]),
      ("SEPARATED", [1, 2, 3, 4, 5, 6, 7, 8]))
r = observe("0269 0012420")
check("grouped literal -> GROUPED, same digits", (r["items"][0]["surface"], digits_of(r)), ("GROUPED", "02690012420"))
r = observe("०२६९००")
check("Devanagari digits are LITERAL slots normalised to ASCII", digits_of(r), "026900")

print("== EMPTY ==")
for t in ("", "हेलो", "बोल", "समझा तू?", "एक बार फिर से", "एक बार फिर से बोल दूने क्या लिखा है?"):
    r = observe(t)
    check(f"EMPTY for {t!r}", (r["certainty"], r["items"]), ("EMPTY", []))
r = observe("एक बार फिर से")
check("adverbial 'एक बार' (count+times, no digit target) is NOT numeric content",
      [x["text"] for x in r["non_numeric_tokens"]], ["एक", "बार", "फिर", "से"])

print("== N1: unknown is a first-class state ==")
T17 = "देख जीरो टू सिस नाइन डबल जीरो ये ठीक है इसको लिख कर रख लिया "
r = observe(T17, turn_no=17)
check("t17: ONE item kept", len(r["items"]), 1)
check("t17: 6 slots  0 2 ? 9 0 0", kinds(r),
      [("DIGIT", "0"), ("DIGIT", "2"), ("UNKNOWN", None), ("DIGIT", "9"), ("DIGIT", "0"), ("DIGIT", "0")])
check("t17: unknown slot is the OOV token 'सिस' (position, not lexicon)",
      (r["items"][0]["slots"][2]["provenance"], r["items"][0]["slots"][2]["token"]["text"]), ("OOV", "सिस"))
check("t17: provenance WORD WORD OOV WORD DOUBLE DOUBLE", provs(r), ["WORD", "WORD", "OOV", "WORD", "DOUBLE", "DOUBLE"])
check("t17: INCOMPLETE with reason OOV_TOKEN", (r["certainty"], r["reasons"]), ("INCOMPLETE", ["OOV_TOKEN"]))
check("t17: unknown_count 1, digits_of None", (r["items"][0]["unknown_count"], digits_of(r)), (1, None))
check("t17: reading 02?900", reading(r["items"][0]), "02?900")
check("t17: instruction/other words are non-numeric tokens",
      [x["text"] for x in r["non_numeric_tokens"]], ["देख", "ये", "ठीक", "है", "इसको", "लिख", "कर", "रख", "लिया"])
r2 = observe("देख जीरो टू नाइन डबल जीरो ये ठीक है")
check("without the OOV token the same span is COMPLETE 02900", (r2["certainty"], digits_of(r2)), ("COMPLETE", "02900"))
check("oracle: an OOV token never lowers the slot count", len(r["items"][0]["slots"]) >= len(r2["items"][0]["slots"]) + 1, True)
r = observe("जीरो टू नाइन सिस")
check("edge OOV needs digit-word SHAPE (edit distance), 'सिस' at the edge is not captured -> COMPLETE 029",
      (r["certainty"], digits_of(r), [x["text"] for x in r["non_numeric_tokens"]]), ("COMPLETE", "029", ["सिस"]))
r = observe("जीरो टू नाईन")
check("edge near-miss of a lexicon word ('नाईन' ~ 'नाइन') -> UNKNOWN edge slot, OOV_EDGE",
      (kinds(r), r["reasons"]), ([("DIGIT", "0"), ("DIGIT", "2"), ("UNKNOWN", None)], ["OOV_EDGE"]))
r = observe("9000 rupaye")
check("'rupaye' is not digit-word shaped -> no UNKNOWN, COMPLETE 9000", (r["certainty"], digits_of(r)), ("COMPLETE", "9000"))
r = observe("12520 नहीं है, 12520 है")
check("a KNOWN instruction word between digits splits items (never OOV): 2 items, both 12520",
      (len(r["items"]), [reading(i) for i in r["items"]], r["certainty"]), (2, ["12520", "12520"], "COMPLETE"))
r = observe("जीरो टू interviewer नाइन फोर")
check("long unknown token (> OOV_MAX_LEN) between digits splits items", (len(r["items"]), [reading(i) for i in r["items"]], r["certainty"]), (2, ["02", "94"], "COMPLETE"))
r = observe("एक interview में दो")
check("lone Hindi number words among other words are NOT asserted as numeric content (conversational 'एक'/'दो')", (r["certainty"], r["items"]), ("EMPTY", []))
r = observe("जीरो टू सिस बिस नाइन")
check("up to OOV_MAX_RUN consecutive unreadable tokens between digits are UNKNOWN slots",
      (OOV_MAX_RUN, reading(r["items"][0])), (2, "02??9"))
r = observe("026900abc")
check("letters glued to a digit run -> MIXED_SCRIPT_RUN, INCOMPLETE", (r["certainty"], "MIXED_SCRIPT_RUN" in r["reasons"]), ("INCOMPLETE", True))

print("== N3: surface form has zero semantic authority (property) ==")
EN = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine"}
DV = {"0": "जीरो", "1": "वन", "2": "टू", "3": "थ्री", "4": "फोर", "5": "फाइव", "6": "सिक्स", "7": "सेवन", "8": "एट", "9": "नाइन"}
def renderings(d):
    yield "run", d
    yield "comma", ", ".join(d)
    yield "space", " ".join(d)
    yield "grouped", " ".join(d[i:i + 3] for i in range(0, len(d), 3))
    yield "english", " ".join(EN[c] for c in d)
    yield "devanagari-english", " ".join(DV[c] for c in d)
    yield "mixed-words", " ".join((EN if i % 2 else DV)[c] for i, c in enumerate(d))
    yield "hyphen", "-".join(d[i:i + 4] for i in range(0, len(d), 4))
rng = random.Random(20260904)
bad = []
for _ in range(150):
    d = "".join(rng.choice("0123456789") for _ in range(rng.randint(3, 17)))
    ref = observe(d)
    for name, txt in renderings(d):
        o = observe(txt)
        if not (o["certainty"] == "COMPLETE" and len(o["items"]) == 1
                and slot_kinds(o["items"][0]) == slot_kinds(ref["items"][0])):
            bad.append((d, name, txt, o["certainty"], reading(o["items"][0]) if o["items"] else None))
check("150 random sequences x 8 renderings -> identical (kind, digit) slots and COMPLETE", bad[:5], [])
r = observe("नौ नौ तीन पांच")
check("Hindi words 'नौ नौ तीन पांच' == 9935", (digits_of(r), r["items"][0]["surface"]), ("9935", "WORDS"))
r = observe("निन्यानबे पैंतीस")
check("compound words: slots carry COMPOUND provenance, digits 9935", (digits_of(r), provs(r)), ("9935", ["COMPOUND"] * 4))
check("compound slots share one token", r["items"][0]["slots"][0]["token"], r["items"][0]["slots"][1]["token"])
a, b = observe("125801203"), observe("1, 2, 5, 8, 0, 1, 2, 0, 3")
check("t4 run vs comma list: equal slots, only surface differs",
      (slot_kinds(a["items"][0]) == slot_kinds(b["items"][0]), a["items"][0]["surface"], b["items"][0]["surface"]),
      (True, "RUN", "SEPARATED"))

print("== N4: digit vs count ==")
r = observe("5 जीरो")
check("'5 जीरो' INCOMPLETE, one AMBIGUOUS slot", (r["certainty"], kinds(r)), ("INCOMPLETE", [("AMBIGUOUS", None)]))
s = r["items"][0]["slots"][0]
check("alternatives {50 | 00000}", [x["digits"] for x in s["alternatives"]], ["50", "00000"])
check("provenance COUNT_OR_DIGIT, count_token '5', token spans the pair",
      (s["provenance"], s["count_token"]["text"], s["token"]["text"]), ("COUNT_OR_DIGIT", "5", "5 जीरो"))
check("reasons COUNT_OR_DIGIT, ambiguous_count 1", (r["reasons"], r["items"][0]["ambiguous_count"]), (["COUNT_OR_DIGIT"], 1))
check("reading {50|00000}", reading(r["items"][0]), "{50|00000}")
r = observe("पाँच जीरो")
check("'पाँच जीरो' behaves as '5 जीरो'", [x["digits"] for x in r["items"][0]["slots"][0]["alternatives"]], ["50", "00000"])
r = observe("पांच 0")
check("Hindi count word + literal 0 is the same ambiguity", [x["digits"] for x in r["items"][0]["slots"][0]["alternatives"]], ["50", "00000"])
r = observe("एक जीरो")
check("'एक जीरो' = {10 | 0}", [x["digits"] for x in r["items"][0]["slots"][0]["alternatives"]], ["10", "0"])
r = observe("5 बार जीरो")
check("explicit multiplier: COMPLETE, five MULTIPLIER zeros", (r["certainty"], digits_of(r), provs(r)), ("COMPLETE", "00000", ["MULTIPLIER"] * 5))
check("multiplier slots carry the count token", r["items"][0]["slots"][0]["count_token"]["text"], "5")
r = observe("4 बार ज़ीरो")
check("nuqta variant from the existing lexicon", digits_of(r), "0000")
r = observe("5 x 0, 1, 2, 0, 3")
check("'5 x 0, 1, 2, 0, 3' (smoke-12 t12) = 000001203 COMPLETE", (r["certainty"], digits_of(r)), ("COMPLETE", "000001203"))
r = observe("डबल जीरो")
check("'डबल जीरो' = two DOUBLE slots", (digits_of(r), provs(r)), ("00", ["DOUBLE", "DOUBLE"]))
r = observe("डबल जीरो, वन, तू, चार बार जीरो, पाइट सेविन, जीरो त्री")
check("smoke-2 t34 spelled dictation = 00120000570 3", digits_of(r), "001200005703")
r = observe("टू जीरो")
check("English-phonetic digit words are never counts: 'टू जीरो' = 20 COMPLETE", (r["certainty"], digits_of(r)), ("COMPLETE", "20"))
r = observe("वन जीरो")
check("'वन जीरो' = 10 COMPLETE", (r["certainty"], digits_of(r)), ("COMPLETE", "10"))
r = observe("5 0")
check("literal + literal is a digit sequence ('5 0' = 50) — STT already rendered digits", (r["certainty"], digits_of(r)), ("COMPLETE", "50"))
r = observe("जीरो जीरो")
check("'जीरो जीरो' = 00 (zero is never a count)", (r["certainty"], digits_of(r)), ("COMPLETE", "00"))
r = observe("सात आठ")
check("N4 is scoped to zero groups: 'सात आठ' = 78 COMPLETE (a Hindi-word dictation stays readable)", (r["certainty"], digits_of(r)), ("COMPLETE", "78"))
r = observe("2 बार 026900")
check("count+times before a multi-digit run: UNBOUND_MULTIPLIER, INCOMPLETE, run kept as LITERAL slots",
      (r["certainty"], "UNBOUND_MULTIPLIER" in r["reasons"], kinds(r)[1:]), ("INCOMPLETE", True, [("DIGIT", c) for c in "026900"]))
check("the unbound count is an AMBIGUOUS slot (digit 2 | repeat count)", kinds(r)[0], ("AMBIGUOUS", None))
r = observe("डबल 026900")
check("'डबल' before a multi-digit run: UNBOUND_DOUBLE, INCOMPLETE", (r["certainty"], r["reasons"]), ("INCOMPLETE", ["UNBOUND_DOUBLE"]))
check("count words are existing lexicon entries only (no new lexicon)", set(_HINDI_COUNT_WORDS) <= set(DIGIT_WORD_MAP), True)
check("count words are Hindi cardinals 1-9", sorted({DIGIT_WORD_MAP[w] for w in _HINDI_COUNT_WORDS}), list("123456789"))

print("== N5: continuation is instruction evidence ==")
a, b, c = observe("इसके आगे 125205203"), observe("125205203"), observe("पूरा नंबर 125205203")
check("identical slots across cue framings", (slot_kinds(a["items"][0]) == slot_kinds(b["items"][0]) == slot_kinds(c["items"][0])), True)
check("cues land in non_numeric_tokens", ([x["text"] for x in a["non_numeric_tokens"]], [x["text"] for x in c["non_numeric_tokens"]]),
      (["इसके", "आगे"], ["पूरा", "नंबर"]))
check("boundary reflects the framing", (a["items"][0]["boundary"]["starts_at_turn_start"], b["items"][0]["boundary"]["starts_at_turn_start"]), (False, True))
r = observe("पूरा बोल पूरा नंबर 026900 आगे")
check("t10 cue conflict: observation is COMPLETE 026900, both cues recorded as non-numeric",
      (r["certainty"], digits_of(r), [x["text"] for x in r["non_numeric_tokens"]]),
      ("COMPLETE", "026900", ["पूरा", "बोल", "पूरा", "नंबर", "आगे"]))
r = observe("अब इसके आगे और नंबर continue होगा, आगे है 1, 2, 5, 2, 0, 5, 2, 0, 3")
check("t12 COMPLETE 125205203, span flush with the utterance END", (r["certainty"], digits_of(r), r["items"][0]["boundary"]["ends_at_turn_end"]),
      ("COMPLETE", "125205203", True))
r = observe("और वहाँ पे 5 जीरो लगा")
check("t15: INCOMPLETE via COUNT_OR_DIGIT only; 'पे'/'लगा' are ordinary words, not UNKNOWN slots",
      (r["certainty"], r["reasons"], r["items"][0]["unknown_count"]), ("INCOMPLETE", ["COUNT_OR_DIGIT"], 0))
r = observe("देखो वो है 125205203")
check("t19 COMPLETE (stopwords/connectors before the number are known words)", (r["certainty"], digits_of(r)), ("COMPLETE", "125205203"))
r = observe("इसमें जो 5 को हटा दे")
check("t14 '5' alone is a one-slot COMPLETE item (numeric content), instruction words outside", (r["certainty"], digits_of(r)), ("COMPLETE", "5"))
r = observe("नहीं पांच जीरो")
check("103339 t18 'नहीं पांच जीरो' -> AMBIGUOUS, reject word outside", (r["certainty"], reading(r["items"][0]), r["non_numeric_tokens"][0]["text"]),
      ("INCOMPLETE", "{50|00000}", "नहीं"))

print("== N7: pure, stateless, JSON-normal, versioned, written once ==")
sig = inspect.signature(observe)
check("observe() takes only text + turn number (no engine, no task state)", sorted(sig.parameters), ["text", "turn_no"])
check("deterministic: same input -> equal record", observe(T17, turn_no=17), observe(T17, turn_no=17))
rec = observe(T17)
check("JSON-normal (round-trips byte-equal)", json.loads(json.dumps(rec)), rec)
check("version pinned to rules + lexicon hash", VERSION.startswith("obs-1.0+lex-"), True)
h0 = lexicon_hash(*LEXICON_PARTS)
h1 = lexicon_hash(dict(DIGIT_WORD_MAP, **{"foo": "1"}), *LEXICON_PARTS[1:])
check("a lexicon change changes the version hash", h0 != h1 and VERSION.endswith(h0), True)
check("the version pins the shipped digit lexicon + multiplier words (imported, not copied)", LEXICON_PARTS[0] is DIGIT_WORD_MAP and LEXICON_PARTS[1] is DOUBLE_WORDS and LEXICON_PARTS[2] is TIMES_WORDS, True)
turn = {"turn": 5, "stt_transcript": "026900"}
rec1 = attach_observation(turn, "026900", turn_no=5, source={"provider": "groq_batch", "model": "whisper-large-v3"},
                          endpoint={"speech_duration_ms": 900.0})
rec2 = attach_observation(turn, "999", turn_no=5)
check("written once: a second attach never overwrites", (turn["numeric_observation"] is rec1, rec2 is rec1, digits_of(turn["numeric_observation"])),
      (True, True, "026900"))
check("record carries STT source + endpoint evidence", (turn["numeric_observation"]["source"]["provider"],
                                                        turn["numeric_observation"]["source"]["model"],
                                                        turn["numeric_observation"]["endpoint"]), ("groq_batch", "whisper-large-v3", {"speech_duration_ms": 900.0}))
long_text = "नंबर है " + ", ".join("0123456789") + " और फिर " + "x" * 120
rec = build_record(long_text, 9, source={"provider": "p"})
check("source.text is the FULL text (never truncated)", (rec["source"]["text"] == long_text, len(rec["source"]["text"]) > 60), (True, True))
pv = pure_view(rec)
check("pure_view drops measurement (source/endpoint) and keeps the text hash", (sorted(pv), pv["text_sha1"] == rec["source"]["text_sha1"]),
      (sorted(["version", "turn", "items", "non_numeric_tokens", "certainty", "reasons", "text_sha1"]), True))
check("pure_view is invariant to source/endpoint content", pure_view(build_record(long_text, 9, source={"provider": "q", "avg_logprob": -0.3}, endpoint={"a": 1})), pv)
check("OOV shape cap constant", OOV_MAX_LEN, 6)

print()
if fails:
    print(f"FAIL ({fails})"); sys.exit(1)
print("ALL PASS")
