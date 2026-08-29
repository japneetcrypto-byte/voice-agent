#!/usr/bin/env python3
"""A-P1 revalidation harness — head parse rate + plan compliance (real calls).

Owner approved A-P1 (head-carried chunk plan). C1 requires revalidating the
parse rate with the extended spec before it becomes the primary chunking
mechanism.

Run on the Mac (needs GEMINI_API_KEY in .env):
    python3 phase5/head_parse_check.py            # 10 detail-mode turns
    python3 phase5/head_parse_check.py --n 20

Reports: parse rate (C1 gate: >=90% of the 30/30 baseline), plan compliance
rate, tag-fail classes. A-P1 passes iff parse >= 90% AND plan >= 70%.
"""
import json, os, sys, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

N = 10
if "--n" in sys.argv:
    N = int(sys.argv[sys.argv.index("--n") + 1])

from agent.prompt_fragments import SYSTEM_FUSED_V11, PROMPT_VERSION
from agent.fused_turn import TAG_RE, salvage_unclosed_head

PROMPTS = [
    ("detail mein samjhao voice agent kaise kaam karta hai", {"delivery": "chunked_detail"}),
    ("poora batao, cost kya hogi isko", {"delivery": "chunked_detail"}),
    ("ek-ek point batao interview ke liye", {"delivery": "chunked_detail"}),
    ("aage", {"delivery": "continue_detail"}),
    ("architecture ka poora flow samjhao", {"delivery": "chunked_detail"}),
    ("haan aage", {"delivery": "continue_detail"}),
    ("step by step explain karo TTS kya hai", {"delivery": "chunked_detail"}),
    ("poori detail chahiye latency ki", {"delivery": "chunked_detail"}),
    ("aage batao", {"delivery": "continue_detail"}),
    ("detail mein metrics batao quality ke", {"delivery": "chunked_detail"}),
][:N]

async def main():
    from google import genai
    key = os.getenv("GEMINI_API_KEY", "")
    if not key or key.startswith(("your_", "<<<")):
        print("GEMINI_API_KEY not set"); sys.exit(1)
    client = genai.Client(api_key=key)

    parsed = plan_ok = tag_fail = 0
    fails = []
    for i, (user_text, policy) in enumerate(PROMPTS, 1):
        contents = json.dumps({
            "policy": {"mode": "VENT", "response_goal": "encourage_continuation", **policy},
            "memory": [], "threads": [], "history": [], "user_turn": user_text,
        }, ensure_ascii=False)
        try:
            resp = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=os.getenv("AIVA_LLM_MODEL", "gemini-3.5-flash-lite"),
                    contents=contents,
                    config={"temperature": 0.7, "system_instruction": SYSTEM_FUSED_V11},
                ), timeout=30)
            buf = (resp.text or "").strip()
            m = TAG_RE.search(buf)
            head = None
            if m:
                try:
                    head = json.loads(m.group(1).strip())
                except json.JSONDecodeError:
                    try:
                        obj, _ = json.JSONDecoder().raw_decode(m.group(1).strip())
                        head = obj if isinstance(obj, dict) else None
                    except json.JSONDecodeError:
                        pass
            else:
                head, _ = salvage_unclosed_head(buf)
            if isinstance(head, dict):
                parsed += 1
                plan = head.get("plan")
                if isinstance(plan, dict) and "total" in plan and "current" in plan:
                    plan_ok += 1
                    print(f"[{i:02d}] PARSED + PLAN {plan} | {buf[m.end():m.end()+60]!r}")
                else:
                    print(f"[{i:02d}] parsed, NO plan | prose: {buf[:70]!r}")
            else:
                tag_fail += 1
                fails.append((i, buf[:100]))
                print(f"[{i:02d}] HEAD FAIL: {buf[:90]!r}")
        except Exception as e:
            tag_fail += 1
            fails.append((i, str(e)[:80]))
            print(f"[{i:02d}] CALL FAIL: {e}")

    n = len(PROMPTS)
    pr, plr = parsed / n * 100, plan_ok / n * 100
    print(f"\n=== A-P1 REVALIDATION (spec {PROMPT_VERSION}, n={n}) ===")
    print(f"head parse rate : {pr:.0f}%  (C1 baseline 100%, gate >=90%)")
    print(f"plan compliance : {plr:.0f}%  (gate >=70%)")
    print(f"tag fails       : {tag_fail} {fails[:3]}")
    verdict = "PASS" if pr >= 90 and plr >= 70 else "FAIL"
    print(f"A-P1 verdict: {verdict}")
    sys.exit(0 if verdict == "PASS" else 1)

asyncio.run(main())
