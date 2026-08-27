#!/usr/bin/env python3
"""Offline pipeline check: SessionState -> policy -> fused turn -> updater -> JSONL -> memory.
Uses a dummy key (exercises the D4 filler path — no real call needed). Mirrors the
worker wiring in agent/main.py run_agent_response."""
import asyncio, os, sys, json
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))
from agent.session_state import SessionState
from agent.memory_store import MemoryStore
from agent.fused_turn import FusedLLM

async def main():
    os.chdir(ROOT)
    store = MemoryStore(os.path.join("logs", "aiva_memory_offline.db"))
    sess = SessionState("offline-test-owner", store)
    fused = FusedLLM()
    print("policy_for_turn:", json.dumps(sess.policy_for_turn())[:120])

    for turn_no, user_text in enumerate(
            ["yaar aaj bahut thak gaya hu, sab kuch galat ho raha hai",
             "ab main kya karun? kuch plan batao na",
             "bas suno yaar, koi solution mat do"], start=1):
        policy = sess.policy_for_turn()
        messages = [{"role": "system", "content": "x"}] + [{"role": "user" if turn_no == 1 else "assistant" if i % 2 else "user", "content": user_text} for i in range(1)]
        history = [m for m in messages if m.get("role") != "system"][-6:]
        chunks = []
        async for c in fused.stream_prose(user_text=user_text, turn_type="speech", policy=policy,
                                           memory_view=sess.memory_view(), threads=sess.thread_summaries(),
                                           history=history, turn_no=turn_no,
                                           degraded=bool(sess.state.get("degraded_perception")),
                                           key=os.getenv("GEMINI_API_KEY", "dummy-offline")):
            chunks.append(c)
        reply = "".join(chunks)
        tr = {"turn": turn_no, "response_completed": True, "interrupted": False,
              "policy_derived": {"mode": policy.get("mode")}}
        new_policy = sess.apply_turn(tr, fused.head)
        print(f"turn {turn_no}: mode={policy.get('mode')} reply={reply[:70]!r}")
    sess.end_session(keep_pending=True)
    print("state log:", sess.log_path)
    print(json.dumps(json.loads(open(sess.log_path).readlines()[-1]), indent=1)[:400])
    print("memory rows for owner:")
    for r in store.db.execute("select type,content,criterion,status,occurrences from memory where owner_id='offline-test-owner'"):
        print("  ", r)
    print("RESULT: PIPELINE OK")

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
