"""L6 — response supersession at the delivery boundary
(docs/VALUE_TRANSACTION_LOCK.md §7; owner terminology 2026-09-04).

UNIT/INVARIANT coverage: authority rank user reply > supervisor rescue >
idle; a pending rescue is superseded by a user turn created after it was
scheduled — at grace end AND at its first-audio boundary (the 103339 t17
race: rescue #1002 and the t17 reply overlapped 99.2–105.1 s). Pure logic in
agent/response_supersession.py; main.py's execution (cancellable rescue task,
cancel at user-turn creation, boundary re-check before the first audio frame)
is pinned by source inspection (main.py cannot import here — no livekit) and
exercised by an asyncio simulation of the same wiring.

Run: python3 phase5/tests/test_response_supersession.py
"""
import sys, os, asyncio, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.response_supersession import (rank_of, supersedes, should_stand_down,
                                         should_stand_down_at_boundary, decide_at_boundary)
from agent.call_supervisor import CallSupervisor, RESCUE_GRACE_S

fails = 0
def check(label, got, want):
    global fails
    if got == want:
        print(f"  ✓ {label}")
    else:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_main = open(os.path.join(ROOT, "agent", "main.py"), encoding="utf-8").read()

print("== authority rank ==")
check("user reply > rescue > idle", (rank_of("speech") > rank_of("supervisor_rescue") > rank_of("idle")), True)
check("user turn supersedes a pending rescue", supersedes("speech", "supervisor_rescue"), True)
check("user turn supersedes a pending idle line", supersedes("speech", "idle"), True)
check("rescue supersedes idle", supersedes("supervisor_rescue", "idle"), True)
check("rescue never supersedes a user reply", supersedes("supervisor_rescue", "speech"), False)
check("idle never supersedes a rescue", supersedes("idle", "supervisor_rescue"), False)
check("a newer user turn supersedes an older user reply (existing barge rule)", supersedes("speech", "speech"), True)

print("== stand-down predicate: all three evidence sources ==")
check("agent audible -> stand down", should_stand_down(agent_speaking=True, user_turn_in_flight=False, newer_user_turn_since=False), True)
check("user turn in flight -> stand down", should_stand_down(agent_speaking=False, user_turn_in_flight=True, newer_user_turn_since=False), True)
check("user turn created since scheduling -> stand down (the t17 race)", should_stand_down(agent_speaking=False, user_turn_in_flight=False, newer_user_turn_since=True), True)
check("nothing newer -> play", should_stand_down(agent_speaking=False, user_turn_in_flight=False, newer_user_turn_since=False), False)
check("boundary decision for a rescue with a newer user turn", decide_at_boundary(pending_type="supervisor_rescue", agent_speaking=False, user_turn_in_flight=False, newer_user_turn_since=True), "supersede")
check("boundary decision for a rescue with nothing newer", decide_at_boundary(pending_type="supervisor_rescue", agent_speaking=False, user_turn_in_flight=False, newer_user_turn_since=False), "play")
check("a user reply is never displaced at its boundary", decide_at_boundary(pending_type="speech", agent_speaking=True, user_turn_in_flight=True, newer_user_turn_since=True), "play")

print("== boundary predicate ignores the speaking event (the rescue holds it itself at that point) ==")
# main.py sets agent_speaking_event at "Agent speaking..." BEFORE the first
# audio frame. At the rescue's own boundary the event is therefore NOT
# evidence of the primary pipeline — including it made every rescue supersede
# itself (runtime-path review 2026-09-04).
check("speaking alone at the boundary -> play (it is the rescue's own event)",
      decide_at_boundary(pending_type="supervisor_rescue", agent_speaking=True, user_turn_in_flight=False, newer_user_turn_since=False), "play")
check("user turn in flight at the boundary -> supersede", should_stand_down_at_boundary(user_turn_in_flight=True, newer_user_turn_since=False), True)
check("newer user turn at the boundary -> supersede", should_stand_down_at_boundary(user_turn_in_flight=False, newer_user_turn_since=True), True)
check("grace-end predicate still uses the speaking event", should_stand_down(agent_speaking=True, user_turn_in_flight=False, newer_user_turn_since=False), True)

print("== asyncio simulation of main.py's wiring: the t17 race ==")
# Timeline (compressed): user turn t16 completes unanswered -> rescue scheduled
# (grace G). At G - d the user speaks t17 -> user turn task created. Pre-lock:
# the rescue's single stand-down check at grace end could pass (t17's task not
# yet 'in flight' when checked / created just after) and BOTH played. Under L6
# the rescue is (a) cancelled at t17's creation and (b) re-checks at its first
# audio frame — either gate alone stops the overlap.
async def simulate(*, cancel_on_create: bool, boundary_check: bool, user_turn_at: float, grace: float = 0.05):
    played = []            # (who, start, end)
    speaking = {"on": False}
    state = {"rescue_task": None, "user_turn_seq": 0, "agent_task": None}
    loop = asyncio.get_running_loop()
    t0 = loop.time()

    async def play(who, dur):
        speaking["on"] = True
        s = loop.time() - t0
        await asyncio.sleep(dur)
        played.append((who, round(s, 3), round(loop.time() - t0, 3)))
        speaking["on"] = False

    def schedule_rescue():
        scheduled_seq = state["user_turn_seq"]
        def stand_down_now():
            return should_stand_down(agent_speaking=speaking["on"],
                                     user_turn_in_flight=bool(state["agent_task"] and not state["agent_task"].done()),
                                     newer_user_turn_since=state["user_turn_seq"] != scheduled_seq)
        def superseded_at_boundary():
            return should_stand_down_at_boundary(
                user_turn_in_flight=bool(state["agent_task"] and not state["agent_task"].done()),
                newer_user_turn_since=state["user_turn_seq"] != scheduled_seq)
        async def _rescue():
            await asyncio.sleep(grace)
            if stand_down_now():
                return
            speaking["on"] = True           # main.py: "Agent speaking..." precedes the first frame
            # "TTS synthesis" latency before the first audio frame:
            await asyncio.sleep(0.02)
            if boundary_check and superseded_at_boundary():
                speaking["on"] = False
                return                      # superseded at the delivery boundary
            speaking["on"] = False
            await play("rescue", 0.06)
        state["rescue_task"] = asyncio.create_task(_rescue())

    async def user_turn():
        await asyncio.sleep(0.03)          # STT + decide + TTS first audio
        await play("user_reply", 0.06)

    schedule_rescue()
    await asyncio.sleep(user_turn_at)
    state["user_turn_seq"] += 1
    if cancel_on_create and state["rescue_task"] and not state["rescue_task"].done() and not speaking["on"]:
        state["rescue_task"].cancel()
    state["agent_task"] = asyncio.create_task(user_turn())
    await asyncio.gather(state["agent_task"], state["rescue_task"], return_exceptions=True)
    return played

def overlap(played):
    for i in range(len(played)):
        for j in range(i + 1, len(played)):
            a, b = played[i], played[j]
            if a[1] < b[2] and b[1] < a[2]:
                return True
    return False

RACE_AT = 0.055   # user speaks just AFTER the grace-end check (the live pattern)
pre = asyncio.run(simulate(cancel_on_create=False, boundary_check=False, user_turn_at=RACE_AT))
check("pre-lock wiring reproduces the overlap (rescue + user reply both play)",
      (sorted(w for w, _, _ in pre), overlap(pre)), (["rescue", "user_reply"], True))
post = asyncio.run(simulate(cancel_on_create=True, boundary_check=True, user_turn_at=RACE_AT))
check("L6: only the user reply plays", [w for w, _, _ in post], ["user_reply"])
only_boundary = asyncio.run(simulate(cancel_on_create=False, boundary_check=True, user_turn_at=RACE_AT))
check("boundary re-check ALONE stops the overlap (rescue already past grace end)", [w for w, _, _ in only_boundary], ["user_reply"])
only_cancel = asyncio.run(simulate(cancel_on_create=True, boundary_check=False, user_turn_at=0.02))
check("cancel-at-creation ALONE stops a rescue still in grace", [w for w, _, _ in only_cancel], ["user_reply"])
quiet = asyncio.run(simulate(cancel_on_create=True, boundary_check=True, user_turn_at=0.5))
check("no user turn in time -> the rescue still plays (rescue is not disabled)", [w for w, _, _ in quiet][:1], ["rescue"])

print("== CallSupervisor semantics unchanged (one rescue per turn, cooldown, escalate on 2nd) ==")
sup = CallSupervisor()
d1 = sup.evaluate({"reason": "unanswered", "turn": 16, "user_text": "x"}, now=100.0)
check("first engagement", (d1["engagement_no"], d1["escalate"]), (1, False))
check("same turn never twice", sup.evaluate({"reason": "unanswered", "turn": 16}, now=101.0), None)
check("cooldown holds", sup.evaluate({"reason": "unanswered", "turn": 17}, now=105.0), None)
d2 = sup.evaluate({"reason": "unanswered", "turn": 18}, now=120.0)
check("second engagement escalates", (d2["engagement_no"], d2["escalate"]), (2, True))
check("RESCUE_GRACE_S unchanged (policy, not touched by L6)", RESCUE_GRACE_S, 4.0)

print("== structural pins: main.py executes L6 ==")
check("rescue task is kept as a cancellable handle", 'supersession["rescue_task"] = asyncio.create_task(_rescue())' in _main, True)
check("user-turn creation bumps the sequence and cancels a pending rescue",
      ('supersession["user_turn_seq"] += 1' in _main and "_rt.cancel()" in _main), True)
i_seq = _main.index('supersession["user_turn_seq"] += 1')
i_task = _main.index("agent_task = asyncio.create_task(\n                            transcribe_and_respond(")
check("supersession happens BEFORE the user turn task is created", i_seq < i_task, True)
check("rescue stand-down uses response_supersession.should_stand_down (grace end)", "rsup.should_stand_down(" in _main, True)
check("boundary check uses the speaking-agnostic predicate", ("rsup.should_stand_down_at_boundary(" in _main and '"_boundary_check": _superseded_at_boundary' in _main), True)
i_speak = _main.index('agent_speaking_event.set()\n        tts_start = time.time()')
i_bc0 = _main.index('_bc = turn.get("_boundary_check")')
check("the speaking event is set BEFORE the boundary check site (why the boundary predicate must ignore it)", i_speak < i_bc0, True)
i_ttfa = _main.index("if not ttfa_logged:\n")
i_bc = _main.index('_bc = turn.get("_boundary_check")')
check("boundary re-check sits at the first-audio site (before TTFA is logged)", 0 < i_bc - i_ttfa < 400, True)
check("boundary supersession raises CancelledError (no audio frame is captured)",
      "raise asyncio.CancelledError()" in _main[i_bc:i_bc + 400], True)
check("superseded rescue is logged as RESPONSE_SUPERSEDED", 'log_event("RESPONSE_SUPERSEDED"' in _main, True)
check("supervisor_check_after_turn / RESCUE_GRACE_S untouched", ("await asyncio.sleep(RESCUE_GRACE_S)" in _main), True)

print()
if fails:
    print(f"FAIL ({fails})"); sys.exit(1)
print("ALL PASS")
