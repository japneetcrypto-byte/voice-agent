#!/usr/bin/env python3
"""Episode-memory foundation — tests (owner-approved 2026-09-02,
docs/EPISODE_MEMORY_SLICE_LOCK.md §D).

Independence (owner decision #3): every matrix row drives the pure rules
from synthetic (session_id, session_start, kind, keys, text, episode
snapshots) inputs. NOTHING depends on any disclosure-frame detector (the
capture-confirm v2 feeder does not exist yet and is never imported).

Run: python3 phase5/tests/test_memory_units.py
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent.memory_units import (EpisodeInfo, FactInfo, mention_keys,
                                time_mark_of, decide_membership,
                                decide_supersede_target,
                                GROUPABLE_KINDS, SINGLETON_KINDS)
from agent.memory_store import MemoryStore

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {label}")

S0 = "2026-09-02T10:00:00+00:00"
def E(eid, sid, touched, marks=frozenset(), keys=frozenset()):
    return EpisodeInfo(episode_id=eid, session_id=sid, created_at=touched,
                       last_touched_at=touched, time_marks=frozenset(marks),
                       keys=frozenset(keys))

print("== key derivation (existing extractors only) ==")
check("person+place keys", set(mention_keys("मेरी बहन नीतू दिल्ली में रहती है")),
      {("p", "neetu"), ("l", "delhi")})
check("roman place", set(mention_keys("main agle mahine Kanpur jaa raha hun")),
      {("l", "kanpur")})
check("garble/no keys -> empty", mention_keys("मैं पागल"), [])
check("empty text", mention_keys(""), [])

print("== time-mark buckets (never calendar dates) ==")
check("agle mahine -> next_month", time_mark_of("agle mahine kanpur jaunga"), "next_month")
check("कल -> near", time_mark_of("मैं कल कानपुर जा रहा हूँ"), "near")
check("none", time_mark_of("main kanpur jaa raha hun"), None)

print("== R1: singleton kinds always standalone ==")
for kind in SINGLETON_KINDS:
    d, eid = decide_membership(owner_id="o", session_id=1, session_start=S0,
                               kind=kind, text="main kanpur ja raha hun",
                               episodes=[E(1, 1, S0, marks={"next_month"})])
    check(f"{kind} standalone", (d, eid), ("standalone", None))

print("== R2: explicit topic switch / conflicting time bucket -> new ==")
d, eid = decide_membership(owner_id="o", session_id=1, session_start=S0,
                           kind="episodic",
                           text="अब voice agent के बारे में बताओ",
                           episodes=[E(1, 1, S0, keys={("l", "kanpur")})])
check("topic-switch -> new", (d, eid), ("new", None))
d, eid = decide_membership(owner_id="o", session_id=1, session_start=S0,
                           kind="episodic", text="main kal kanpur jaa raha hun",
                           episodes=[E(1, 1, S0, marks={"next_month"},
                                       keys={("l", "kanpur")})])
check("conflicting time bucket -> new", (d, eid), ("new", None))

print("== R3: same-session attach (cue / key overlap) ==")
d, eid = decide_membership(owner_id="o", session_id=1, session_start=S0,
                           kind="episodic", text="aur Rahul bhi chalega",
                           episodes=[E(1, 1, S0, keys={("l", "kanpur")})])
check("additive cue -> attach active", (d, eid), ("attach", 1))
d, eid = decide_membership(owner_id="o", session_id=1, session_start=S0,
                           kind="episodic", text="main Kanpur hi jaa raha hun",
                           episodes=[E(1, 1, S0, keys={("l", "kanpur")})])
check("same-key restatement -> attach active", (d, eid), ("attach", 1))
d, eid = decide_membership(owner_id="o", session_id=1, session_start=S0,
                           kind="episodic", text="main Jaipur bhi jaa raha hun",
                           episodes=[E(1, 1, S0, keys={("l", "kanpur")})])
check("distinct second plan -> new (no cue/overlap)", (d, eid), ("new", None))
d, eid = decide_membership(owner_id="o", session_id=1, session_start=S0,
                           kind="episodic", text="ja raha hun",
                           episodes=[E(1, 1, S0, keys={("l", "kanpur")})])
check("no key/no cue second capture -> standalone", (d, eid), ("standalone", None))

print("== R4: cross-session attach within W_cross (30d), single match ==")
d, eid = decide_membership(owner_id="o", session_id=2, session_start=S0,
                           kind="episodic", text="agle mahine Kanpur jaunga",
                           episodes=[E(7, 1, "2026-08-20T00:00:00+00:00",
                                       keys={("l", "kanpur")})])
check("within 30d -> attach cross-session ep 7", (d, eid), ("attach", 7))
d, eid = decide_membership(owner_id="o", session_id=2, session_start=S0,
                           kind="episodic", text="agle mahine Kanpur jaunga",
                           episodes=[E(7, 1, "2026-07-01T00:00:00+00:00",
                                       keys={("l", "kanpur")})])
check("beyond 30d -> new (not attach)", (d, eid), ("new", None))
d, eid = decide_membership(owner_id="o", session_id=2, session_start=S0,
                           kind="episodic", text="agle mahine Kanpur jaunga",
                           episodes=[E(7, 1, "2026-08-20T00:00:00+00:00",
                                       keys={("l", "kanpur")}),
                                     E(8, 1, "2026-08-21T00:00:00+00:00",
                                       keys={("l", "kanpur")})])
check("ambiguous (2 matches) -> new", (d, eid), ("new", None))
d, eid = decide_membership(owner_id="o", session_id=2, session_start=S0,
                           kind="episodic", text="kal Kanpur jaa raha hun",
                           episodes=[E(7, 1, "2026-08-20T00:00:00+00:00",
                                       marks={"next_month"},
                                       keys={("l", "kanpur")})])
check("cross-session conflicting time -> new", (d, eid), ("new", None))

print("== R5 fallbacks + degenerate inputs ==")
d, eid = decide_membership(owner_id="o", session_id=1, session_start=S0,
                           kind="episodic", text="main agle mahine Kanpur jaunga",
                           episodes=[])
check("no episodes, has key -> new", (d, eid), ("new", None))
d, eid = decide_membership(owner_id="o", session_id=1, session_start=S0,
                           kind="episodic", text="bas main ja raha hun",
                           episodes=[])
check("no episodes, no key/cue -> standalone", (d, eid), ("standalone", None))
d, eid = decide_membership(owner_id="o", session_id=None, session_start=S0,
                           kind="episodic", text="main kanpur jaa raha hun",
                           episodes=[])
check("no session id -> standalone (never guess)", (d, eid), ("standalone", None))
d1, e1 = decide_membership(owner_id="o", session_id=1, session_start=S0,
                           kind="episodic", text="agle mahine Kanpur jaunga",
                           episodes=[E(7, 1, "2026-08-20T00:00:00+00:00",
                                       keys={("l", "kanpur")})])
d2, e2 = decide_membership(owner_id="o", session_id=1, session_start=S0,
                           kind="episodic", text="agle mahine Kanpur jaunga",
                           episodes=[E(7, 1, "2026-08-20T00:00:00+00:00",
                                       keys={("l", "kanpur")})])
check("determinism (same inputs -> same decision)", (d1, e1), (d2, e2))

print("== supersede target selection ==")
facts = [FactInfo(fact_id=1, kind="episodic", content="user: kanpur trip",
                  keys={("l", "kanpur")}, last_seen="2026-08-01T00:00:00+00:00"),
         FactInfo(fact_id=2, kind="episodic", content="user: jaipur trip",
                  keys={("l", "jaipur")}, last_seen="2026-08-10T00:00:00+00:00")]
check("correction + key overlap -> newest match",
      decide_supersede_target(kind="episodic", text="nahi, wo Kanpur wali baat galat hai",
                              keys={("l", "kanpur")}, facts=facts, exclude_id=9),
      1)
check("no correction frame -> None",
      decide_supersede_target(kind="episodic", text="Kanpur wali baat yaad hai?",
                              keys={("l", "kanpur")}, facts=facts, exclude_id=9),
      None)
check("no key overlap -> None (coexist, recency resolves)",
      decide_supersede_target(kind="episodic", text="nahi, mumbai galat hai",
                              keys={("l", "mumbai")}, facts=facts, exclude_id=9),
      None)
check("non-groupable kind -> None",
      decide_supersede_target(kind="relationship", text="nahi galat",
                              keys={("p", "neetu")}, facts=[], exclude_id=9),
      None)

print("== STORE: start_session monotonic, per-owner ==")
tmp = tempfile.mkdtemp()
s = MemoryStore(os.path.join(tmp, "m.db"))
check("owner A s1", s.start_session("A"), 1)
check("owner A s2", s.start_session("A"), 2)
check("owner B s1 (isolated)", s.start_session("B"), 1)

print("== STORE: commit-with-context creates one episode + mentions ==")
s = MemoryStore(os.path.join(tmp, "m2.db"))
ctx = {"session_id": 1, "session_start": S0, "text": "main agle mahine Kanpur jane wala hun"}
rid = s.commit("A", {"type": "episodic", "content": "user: main agle mahine Kanpur jane wala hun",
                     "criterion": "explicit"}, immediate=True, context=ctx)
check("commit returns new row id", isinstance(rid, int) and rid > 0, True)
eps = s.episode_snapshot("A")
check("one episode created", len(eps), 1)
check("fact attached to episode", s.db.execute(
    "SELECT episode_id FROM memory WHERE id=?", (rid,)).fetchone()[0], eps[0].episode_id)
check("place mention stored", ("l", "kanpur") in eps[0].keys, True)
check("view unchanged (fact visible)", any("Kanpur" in l for l in s.view("A")), True)

print("== STORE: dedup bump never re-runs membership ==")
s.commit("A", {"type": "episodic", "content": "user: main agle mahine Kanpur jane wala hun",
               "criterion": "explicit"}, immediate=True, context=ctx)
row = s.db.execute("SELECT occurrences, episode_id FROM memory WHERE id=?",
                   (rid,)).fetchone()
check("same content: 1 row, occurrences=2, same episode", (row[0], row[1]),
      (2, eps[0].episode_id))
check("still exactly one episode", len(s.episode_snapshot("A")), 1)

print("== STORE: pending rows invisible; promote-with-context finalizes ==")
s = MemoryStore(os.path.join(tmp, "m3.db"))
s.commit("A", {"type": "episodic", "content": "user: agle saal Kashmir jane wala hun",
               "criterion": "salient"}, immediate=False,
         context={"session_id": 1, "session_start": S0,
                  "text": "agle saal Kashmir jane wala hun"})
check("pending not in view", s.view("A"), [])
check("no episode for pending row", len(s.episode_snapshot("A")), 0)
n_men = s.db.execute("SELECT COUNT(*) FROM memory_mentions").fetchone()[0]
check("pending row still got mention keys (annotate only)", n_men, 1)
s.commit("A", {"type": "episodic", "content": "user: agle saal Kashmir jane wala hun",
               "criterion": "explicit"}, immediate=True,
         context={"session_id": 1, "session_start": S0,
                  "text": "agle saal Kashmir jane wala hun"})
check("promoted fact now in view", len(s.view("A")), 1)
check("promotion created episode", len(s.episode_snapshot("A")), 1)

print("== STORE: singleton kinds never join an episode ==")
s = MemoryStore(os.path.join(tmp, "m4.db"))
ctx2 = {"session_id": 1, "session_start": S0, "text": "mujhe coffee pasand hai"}
s.commit("A", {"type": "preference", "content": "user likes coffee",
               "criterion": "explicit"}, immediate=True, context=ctx2)
check("preference: no episode", s.episode_snapshot("A"), [])
check("preference: committed + visible", len(s.view("A")), 1)

print("== STORE: supersede — old row untouched, leaves view, chain works ==")
s = MemoryStore(os.path.join(tmp, "m5.db"))
k1 = {"session_id": 1, "session_start": S0, "text": "agle mahine Kanpur jaa raha hun"}
rid1 = s.commit("A", {"type": "episodic",
                      "content": "user: agle mahine Kanpur jane wala hun",
                      "criterion": "explicit"}, immediate=True, context=k1)
content1 = s.db.execute("SELECT content FROM memory WHERE id=?", (rid1,)).fetchone()[0]
k2 = {"session_id": 1, "session_start": S0,
      "text": "nahi, wo Kanpur wala plan galat hai, main kal jaa raha hun"}
rid2 = s.commit("A", {"type": "episodic",
                      "content": "user: nahi Kanpur wala plan galat hai kal jaunga",
                      "criterion": "explicit"}, immediate=True, context=k2)
st1 = s.db.execute("SELECT status FROM memory WHERE id=?", (rid1,)).fetchone()[0]
check("old fact superseded", st1, "superseded")
check("old content byte-unchanged", s.db.execute(
    "SELECT content FROM memory WHERE id=?", (rid1,)).fetchone()[0], content1)
check("superseded row leaves view()", s.view("A"), [
    "episodic: user: nahi Kanpur wala plan galat hai kal jaunga (explicit)"])
check("new row points at old", s.db.execute(
    "SELECT supersedes_id FROM memory WHERE id=?", (rid2,)).fetchone()[0], rid1)
k3 = {"session_id": 1, "session_start": S0,
      "text": "nahi, kal nahi, agle mahine hi Kanpur jaunga"}
rid3 = s.commit("A", {"type": "episodic",
                      "content": "user: nahi kal nahi agle mahine hi Kanpur jaunga",
                      "criterion": "explicit"}, immediate=True, context=k3)
st2 = s.db.execute("SELECT status FROM memory WHERE id=?", (rid2,)).fetchone()[0]
check("chain: second correction supersedes the first correction", st2, "superseded")
check("chain: newest points at second", s.db.execute(
    "SELECT supersedes_id FROM memory WHERE id=?", (rid3,)).fetchone()[0], rid2)

print("== STORE: cross-session attach (R4) — the §F.5 foundational smoke ==")
s = MemoryStore(os.path.join(tmp, "m7.db"))
# Session 1: Kanpur trip committed (episode E1 created at real "now").
r1 = s.commit("A", {"type": "episodic",
                    "content": "user: agle mahine Kanpur jane wala hun",
                    "criterion": "explicit"}, immediate=True,
              context={"session_id": 1, "session_start": S0,
                       "text": "agle mahine Kanpur jane wala hun"})
e1 = s.db.execute("SELECT episode_id FROM memory WHERE id=?", (r1,)).fetchone()[0]
# Rebase E1's touch time to 13 days before session 2 (within W_cross=30).
s.db.execute("UPDATE episodes SET last_touched_at=? WHERE id=?",
             ("2026-08-20T00:00:00+00:00", e1))
s.db.commit()
# Session 2: user restates the trip (fresh session_id=2) -> R4 attach to E1.
r2 = s.commit("A", {"type": "episodic",
                    "content": "user: haan Kanpur wale plan pe hi hoon",
                    "criterion": "explicit"}, immediate=True,
              context={"session_id": 2, "session_start": S0,
                       "text": "haan Kanpur wale plan pe hi hoon"})
e2 = s.db.execute("SELECT episode_id FROM memory WHERE id=?", (r2,)).fetchone()[0]
check("cross-session restatement attaches to episode 1", e2, e1)
check("still exactly one episode for owner", len(s.episode_snapshot("A")), 1)
check("both facts recalled in view (cross-session recall path)",
      len(s.view("A")), 2)
# Rebase E1 to beyond the window -> a THIRD session restatement starts fresh.
s.db.execute("UPDATE episodes SET last_touched_at=? WHERE id=?",
             ("2026-06-01T00:00:00+00:00", e1))
s.db.commit()
r3 = s.commit("A", {"type": "episodic",
                    "content": "user: dobara Kanpur jane ka plan hai",
                    "criterion": "explicit"}, immediate=True,
              context={"session_id": 3, "session_start": S0,
                       "text": "dobara Kanpur jane ka plan hai"})
e3 = s.db.execute("SELECT episode_id FROM memory WHERE id=?", (r3,)).fetchone()[0]
check("restatement beyond W_cross -> new episode (not attach)", e3 != e1, True)
check("two episodes now", len(s.episode_snapshot("A")), 2)

print("== STORE: owner isolation ==")
s = MemoryStore(os.path.join(tmp, "m6.db"))
sa = {"session_id": 1, "session_start": S0, "text": "agle mahine Kanpur jaunga"}
s.commit("A", {"type": "episodic", "content": "user: A kanpur trip",
               "criterion": "explicit"}, immediate=True, context=sa)
sb = {"session_id": 1, "session_start": S0, "text": "agle mahine Kanpur jaunga"}
s.commit("B", {"type": "episodic", "content": "user: B kanpur trip",
               "criterion": "explicit"}, immediate=True, context=sb)
check("owner A snapshot excludes B", len(s.episode_snapshot("A")), 1)
check("owner B snapshot excludes A", len(s.episode_snapshot("B")), 1)
check("owner A episodes only A", all(e.episode_id for e in s.episode_snapshot("A")), True)

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("ALL PASS")
