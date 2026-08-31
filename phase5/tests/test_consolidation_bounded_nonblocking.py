#!/usr/bin/env python3
"""ACCEPTANCE 10 — the pass is bounded and can never block worker teardown.

consolidate_bounded() enforces the budget: a stalling LLM is cut off by
asyncio.wait_for within the defined timeout (worker teardown proceeds), there
is NO retry loop (one attempt), and a healthy fast call returns a summary.

Locked in docs/SESSION_END_CONSOLIDATION_V1.md §7, §10 test 10.
Run: python3 phase5/tests/test_consolidation_bounded_nonblocking.py
"""
import sys, os, tempfile, time, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.memory_store import MemoryStore
from agent.session_consolidation import consolidate_bounded

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {label}")

db = os.path.join(tempfile.mkdtemp(), "m.db")
store = MemoryStore(db)
OWNER = "owner-b10"
TURNS = [(1, "मुझे चाय पसंद है")]

# --- stall: a 4s LLM under a 0.5s budget -> TimeoutError AT the budget, not
# at stall end. We drive the loop manually so we measure the exception
# arrival time (asyncio.run would join the executor thread and hide it). The
# production path (main.py shutdown hook) awaits consolidate_bounded with the
# 15s budget, gets TimeoutError, and proceeds to end_session regardless. ---
def slow(prompt, end=4.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < end:
        time.sleep(0.05)
    return "{}"

loop = asyncio.new_event_loop()
t0 = time.monotonic()
timed_out = False
try:
    loop.run_until_complete(consolidate_bounded(owner_id=OWNER, store=store,
                                                session_turns=TURNS,
                                                llm_call=slow, timeout=0.5))
except asyncio.TimeoutError:
    timed_out = True
elapsed = time.monotonic() - t0
loop.close()
check("stalling LLM -> timeout", timed_out, True)
check("bounded: cut off at the budget, not at stall end", elapsed < 2.0, True)

# --- healthy fast call returns a summary ---
def fast(prompt):
    return '{"bullets": [], "nothing_important_missed": true}'

s = asyncio.run(consolidate_bounded(owner_id=OWNER, store=store, session_turns=TURNS,
                                    llm_call=fast, timeout=1.0))
check("fast pass returns ok summary", s["status"], "ok")

# --- no retry: one attempt, failure surfaces ---
calls = []
def once(prompt):
    calls.append(prompt)
    raise RuntimeError("boom")

s2 = asyncio.run(consolidate_bounded(owner_id=OWNER, store=store, session_turns=TURNS,
                                     llm_call=once, timeout=1.0))
check("no retry loop (single attempt)", len(calls), 1)
check("failure surfaced as status failed", s2["status"], "failed")

print("\n" + ("ALL PASS" if fails == 0 else f"{fails} FAILURE(S)"))
sys.exit(1 if fails else 0)
