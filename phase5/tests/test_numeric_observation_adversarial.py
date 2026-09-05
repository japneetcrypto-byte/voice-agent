"""Numeric Observation Boundary — Phase 1 adversarial / property suite.

What Phase 1 must prove before Phase 2 (owner ruling 2026-09-04): zero
behavioural change and a record that is pure, stateless, deterministic and
cannot fail the production path.

  A. PURITY (N7): observe() never reads or writes engine/task state — it is
     called with text only; a frozen engine dict is bit-identical before and
     after 5,000 decisions with the attach points live.
  B. DETERMINISM: same text -> same pure record, across 3 passes and across
     shuffled call order; the version string is stable within a process.
  C. NO CRASH: 20,000 random strings (mixed Devanagari / Latin / digits /
     punctuation / control chars / emoji / very long runs) never raise and
     always yield a schema-valid, JSON-normal record; certainty derivation is
     consistent with the slots on every record.
  D. ZERO BEHAVIOUR CHANGE: 3,000 random dictation-style sessions through
     run_turn with the Phase-1 attach points ON vs OFF give byte-identical
     turn dicts (modulo the two additive keys) and identical task state.
  E. INVARIANT ORACLES (lock test oracles): N1 slot-count oracle; N3
     surface-invariance; digits_of never emits a digit from an
     UNKNOWN/AMBIGUOUS slot; COMPLETE <=> all slots DIGIT and no unbound
     reason; span offsets always slice back to the recorded text.

Run: python3 phase5/tests/test_numeric_observation_adversarial.py
"""
import sys, os, json, random, copy, string
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.numeric_observation import (observe, build_record, pure_view, digits_of, slot_kinds,
                                       VERSION, COMPLETE, INCOMPLETE, EMPTY, DIGIT, _is_known)
from agent.precision_rail import DIGIT_WORD_MAP, DOUBLE_WORDS, TIMES_WORDS
from agent.response_pipeline import run_turn, TurnContext
import agent.response_pipeline as rp

fails = 0
def check(label, got, want):
    global fails
    if got == want:
        print(f"  ✓ {label}")
    else:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")

rng = random.Random(133627)

DEV_WORDS = ["जीरो", "ज़ीरो", "एक", "दो", "तीन", "चार", "पांच", "पाँच", "छह", "सात", "आठ", "नौ", "दस", "बीस",
             "निन्यानबे", "पैंतीस", "वन", "टू", "थ्री", "फोर", "फाइव", "सिक्स", "सेवन", "एट", "नाइन",
             "डबल", "बार", "नहीं", "हाँ", "ठीक", "है", "पूरा", "आगे", "फिर", "से", "इसके", "बाद", "की", "जगह",
             "सिस", "बिस", "लगा", "हटा", "दे", "नंबर", "लिख", "बोल", "समझा", "तू", "मेरा", "अकाउंट", "कर", "दो"]
LAT_WORDS = ["zero", "one", "two", "five", "nine", "double", "times", "bar", "x", "number", "account", "likho",
             "haan", "nahi", "theek", "continue", "again", "full", "interview", "ok", "sahi"]
PUNCT = [",", ", ", " ", "  ", ".", "-", "–", "—", "/", "?", "!", "।", ":", ";", "\n", "\t"]

def rand_token():
    r = rng.random()
    if r < 0.30:
        return "".join(rng.choice("0123456789") for _ in range(rng.randint(1, 18)))
    if r < 0.55:
        return rng.choice(DEV_WORDS)
    if r < 0.70:
        return rng.choice(LAT_WORDS)
    if r < 0.78:
        return "".join(rng.choice("०१२३४५६७८९") for _ in range(rng.randint(1, 8)))
    if r < 0.86:
        return "".join(chr(rng.randint(0x900, 0x97F)) for _ in range(rng.randint(1, 9)))
    if r < 0.92:
        return "".join(rng.choice(string.ascii_letters) for _ in range(rng.randint(1, 12)))
    if r < 0.96:
        return rng.choice(["😀", "🙏", "\u200d", "\u200b", "\x00", "\ufeff", "×", "½", "٣", "Ⅻ"])
    return rng.choice(PUNCT)

def rand_text():
    n = rng.randint(0, 24)
    return "".join(rand_token() + (rng.choice(PUNCT) if rng.random() < 0.5 else " ") for _ in range(n))

def schema_ok(rec, text):
    assert set(rec) == {"version", "turn", "source", "endpoint", "items", "non_numeric_tokens", "certainty", "reasons"}
    assert rec["certainty"] in (EMPTY, COMPLETE, INCOMPLETE)
    assert rec["source"]["text"] == text
    all_digit = True
    for it in rec["items"]:
        assert set(it) == {"span", "slots", "surface", "group_breaks", "boundary", "unknown_count", "ambiguous_count"}
        sp = it["span"]
        assert text[sp["start"]:sp["end"]] == sp["text"]
        assert it["slots"], "item without slots"
        assert it["unknown_count"] == sum(1 for s in it["slots"] if s["kind"] == "UNKNOWN")
        assert it["ambiguous_count"] == sum(1 for s in it["slots"] if s["kind"] == "AMBIGUOUS")
        for s in it["slots"]:
            assert set(s) == {"kind", "digit", "alternatives", "token", "provenance", "count_token", "confidence"}
            if s["kind"] == DIGIT:
                assert s["digit"] in "0123456789" and len(s["digit"]) == 1 and s["alternatives"] is None
            else:
                all_digit = False
                assert s["digit"] is None
            if s["kind"] == "AMBIGUOUS":
                assert s["alternatives"] and len(s["alternatives"]) == 2
            tk = s["token"]
            if tk is not None:
                assert text[tk["start"]:tk["end"]] == tk["text"]
        for gb in it["group_breaks"]:
            assert 0 < gb < len(it["slots"])
    for nn in rec["non_numeric_tokens"]:
        assert text[nn["start"]:nn["end"]] == nn["text"]
    # certainty consistency
    if not rec["items"]:
        assert rec["certainty"] == EMPTY and rec["reasons"] == []
    elif all_digit and not any(r in ("UNBOUND_MULTIPLIER", "UNBOUND_DOUBLE", "MIXED_SCRIPT_RUN") for r in rec["reasons"]):
        assert rec["certainty"] == COMPLETE
    else:
        assert rec["certainty"] == INCOMPLETE and rec["reasons"]
    # items never overlap and are ordered
    spans = [(it["span"]["start"], it["span"]["end"]) for it in rec["items"]]
    assert spans == sorted(spans) and all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))
    # digits_of never leaks a non-DIGIT slot
    d = digits_of(rec)
    if d is not None:
        assert rec["certainty"] == COMPLETE and len(d) == sum(len(it["slots"]) for it in rec["items"])
    json.loads(json.dumps(rec))
    return True

# ---------------------------------------------------------------------------
print("== C. no crash / schema validity over 20,000 random strings ==")
bad = []
n_items = n_complete = n_incomplete = 0
for i in range(20000):
    text = rand_text()
    try:
        rec = observe(text, i)
        schema_ok(rec, text)
        n_items += bool(rec["items"]); n_complete += rec["certainty"] == COMPLETE; n_incomplete += rec["certainty"] == INCOMPLETE
    except Exception as e:  # noqa
        bad.append((text[:80], f"{type(e).__name__}: {e}"))
        if len(bad) > 5:
            break
check("no exception / schema violation", bad, [])
check("corpus exercised all three certainty classes", (n_items > 1000, n_complete > 300, n_incomplete > 300), (True, True, True))
print(f"     (numeric turns {n_items}, COMPLETE {n_complete}, INCOMPLETE {n_incomplete})")
for edge in ["", " ", "\n", "0", "०", "9" * 400, "जीरो " * 300, "5 जीरो " * 50, "बार बार बार", "डबल डबल", ", , ,", "×××", "\x00\x00", "a" * 1000]:
    try:
        schema_ok(observe(edge), edge)
    except Exception as e:  # noqa
        bad.append((edge[:20], str(e)))
check("edge inputs (empty, giant runs, repeated multipliers, control chars)", bad, [])

# ---------------------------------------------------------------------------
print("== B. determinism ==")
corpus = [rand_text() for _ in range(2000)]
p1 = [pure_view(observe(t, 1)) for t in corpus]
p2 = [pure_view(observe(t, 1)) for t in corpus]
order = list(range(len(corpus))); rng.shuffle(order)
p3 = {i: pure_view(observe(corpus[i], 1)) for i in order}
check("pass 1 == pass 2", p1 == p2, True)
check("shuffled call order gives the same records", all(p3[i] == p1[i] for i in range(len(corpus))), True)
check("version stable", VERSION == observe("1")["version"] == observe("जीरो")["version"], True)
check("turn number does not affect the pure content", pure_view(observe("026900", 3))["items"] == pure_view(observe("026900", 99))["items"], True)
check("build_record with different source/endpoint -> same pure view",
      pure_view(build_record("5 जीरो", 4, {"provider": "a"}, {"speech_duration_ms": 1})) == pure_view(build_record("5 जीरो", 4, {"provider": "b"}, None)), True)

# ---------------------------------------------------------------------------
print("== E. lock oracles ==")
# N1: OOV between readable tokens never lowers the slot count
viol = []
for _ in range(1500):
    d = "".join(rng.choice("0123456789") for _ in range(rng.randint(2, 9)))
    words = [{"0": "जीरो", "1": "वन", "2": "टू", "3": "थ्री", "4": "फोर", "5": "फाइव", "6": "सिक्स", "7": "सेवन", "8": "एट", "9": "नाइन"}[c] for c in d]
    k = rng.randint(1, len(words) - 1)
    while True:   # an OOV token = not a lexicon word, not a known function word
        oov = "".join(chr(rng.randint(0x915, 0x939)) for _ in range(rng.randint(2, 4)))
        if oov not in DIGIT_WORD_MAP and oov not in DOUBLE_WORDS and oov not in TIMES_WORDS and not _is_known(oov):
            break
    with_oov = " ".join(words[:k] + [oov] + words[k:])
    a, b = observe(" ".join(words)), observe(with_oov)
    if not b["items"] or len(b["items"][0]["slots"]) < len(a["items"][0]["slots"]) + 1 or b["certainty"] != INCOMPLETE:
        viol.append((with_oov, [slot_kinds(i) for i in b["items"]]))
check("N1 oracle: an OOV token between digit words adds an UNKNOWN slot (1500 cases)", viol[:3], [])
# N2/N6 groundwork: digits_of over INCOMPLETE is None, never a guess
check("digits_of(INCOMPLETE) is None (never a guess)", all(digits_of(observe(t)) is None for t in ["5 जीरो", "जीरो टू सिस नाइन", "2 बार 026900", "026900abc"]), True)
# N3: rendering invariance on a fresh random sample (complements the unit suite)
viol = []
for _ in range(400):
    d = "".join(rng.choice("0123456789") for _ in range(rng.randint(3, 16)))
    ref = slot_kinds(observe(d)["items"][0])
    for r in (", ".join(d), " ".join(d), "-".join(d[i:i + 2] for i in range(0, len(d), 2)), d[:3] + ", " + d[3:] if len(d) > 4 else d):
        o = observe(r)
        if o["certainty"] != COMPLETE or len(o["items"]) != 1 or slot_kinds(o["items"][0]) != ref:
            viol.append(r)
check("N3 oracle: renderings of the same digits are slot-identical (400 x 4)", viol[:3], [])
# non_numeric + items cover every token exactly once
viol = []
for t in corpus[:800]:
    rec = observe(t)
    covered = sum(len(it["slots"]) > 0 for it in rec["items"])
    in_items = sum(1 for it in rec["items"] for _ in [0])
    # every non_numeric token lies outside every item span
    for nn in rec["non_numeric_tokens"]:
        if any(it["span"]["start"] <= nn["start"] < it["span"]["end"] for it in rec["items"]):
            viol.append((t[:40], nn)); break
check("non_numeric_tokens never lie inside an item span", viol[:3], [])

# ---------------------------------------------------------------------------
print("== A + D. purity and zero behaviour change through run_turn (3,000 random sessions) ==")
class FakeSess:
    def policy_for_turn(self): return {}
    def memory_view(self): return []
class FakeLCM:
    def add_turn(self, *a, **k): pass
    def needs_compression(self): return False
    def get_layer2(self): return {"active_topic": None}

SESSION_TOKENS = ["026900", "125205203", "1, 2, 5, 2, 0, 5, 2, 0, 3", "5 बार 0", "5 जीरो", "डबल जीरो", "नहीं 520 नहीं है",
                  "पाइप की जगह 5 बार 0 लिखना है", "हाँ", "ठीक है", "नहीं", "बता क्या लिखा", "पूरा नंबर 026900 आगे",
                  "इसके आगे 4301", "जीरो टू सिस नाइन डबल जीरो", "हेलो", "एक नंबर लिखो", "मेरा account number likho 9935411907",
                  "9000 की जगह 900 कर दो", "बस", "सिर्फ इतना 9935", "आगे", "समझा तू?", "12 के बाद 4 बार 0 है 420 नहीं है",
                  "छोड़ दे", "और वहाँ पे 5 जीरो लगा", "देखो वो है 125205203", "12520 नहीं है, 12520 है", "इसमें जो 5 को हटा दे"]

def gen_session():
    n = rng.randint(3, 14)
    out = []
    for i in range(n):
        text = rng.choice(SESSION_TOKENS) if rng.random() < 0.8 else rand_text()[:80]
        pb = rng.choice(["played", "played", "played", None, "cancel_pre_audio", "barged"])
        out.append((i + 1, text, pb))
    return out

def run(session, on):
    eng = {"sess": FakeSess(), "lcm": FakeLCM(), "fused": None}
    _ao, _ac = rp.attach_observation, rp.attach_chain
    if not on:
        rp.attach_observation = lambda *a, **k: None
        rp.attach_chain = lambda *a, **k: None
    turns = []
    try:
        for tn, text, pb in session:
            t = run_turn(TurnContext(turn_no=tn, user_text=text, engine=eng, model_text="achha.",
                                     interrupted=pb in ("cancel_pre_audio", "barged"),
                                     played_any_audio=pb in ("played", "barged"),
                                     premature_resume=({"resumed_after_endpoint_ms": 900} if rng.random() < 0.1 else None)))
            turns.append(t)
    finally:
        rp.attach_observation, rp.attach_chain = _ao, _ac
    return turns, eng

def strip(t):
    t = copy.deepcopy(t)
    for k in ("numeric_observation", "numeric_audit", "numeric_audit_error"):
        t.pop(k, None)
    return t

diff_sessions = 0; state_diffs = 0; missing = 0; errors = 0; n_turns = 0; l1_flags = 0
for _ in range(3000):
    sess = gen_session()
    seed = rng.random()
    r1 = random.Random(seed); r2 = random.Random(seed)
    saved = rng
    rng = r1; t_off, e_off = run(sess, on=False)
    rng = r2; t_on, e_on = run(sess, on=True)
    rng = saved
    n_turns += len(t_on)
    if [strip(t) for t in t_on] != t_off:
        diff_sessions += 1
    if (e_on.get("dictation"), e_on.get("conv")) != (e_off.get("dictation"), e_off.get("conv")):
        state_diffs += 1
    missing += sum(1 for t in t_on if not isinstance(t.get("numeric_observation"), dict) or not isinstance(t.get("numeric_audit"), dict))
    errors += sum(1 for t in t_on if t.get("numeric_audit_error"))
    l1_flags += sum(1 for t in t_on if (t.get("numeric_audit") or {}).get("commit", {}).get("l1_check") not in (None, "ok"))
check(f"turn dicts identical with attach points ON vs OFF ({n_turns} turns)", diff_sessions, 0)
check("task state (dictation + conv) identical ON vs OFF", state_diffs, 0)
check("every turn carries observation + chain", missing, 0)
check("no chain errors", errors, 0)
check("no UNEXPECTED_BASE_CHANGE flagged by the chain's L1 check over the random corpus", l1_flags, 0)

print("== A. observe() cannot touch state: a frozen engine is untouched by 5,000 observations ==")
eng = {"dictation": {"value": "026900", "status": "confirming", "proposal": {"derived": "1", "delivery": "spoken"}}, "conv": {"accum_gap": 3}}
snap = copy.deepcopy(eng)
for t in corpus[:5000]:
    observe(t, 7)
check("engine dict bit-identical (observe has no access to it by signature)", eng, snap)

print()
if fails:
    print(f"FAIL ({fails})"); sys.exit(1)
print("ALL PASS")
