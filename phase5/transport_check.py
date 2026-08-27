#!/usr/bin/env python3
"""Day-one transport check: one fused turn with your real key.

Run from the repo root:   uv run python phase5/transport_check.py
Prints transport health (reply/meta) and a verdict line.
"""
import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))  # explicit path — stdin-safe

from agent.fused_turn import FusedLLM  # noqa: E402


async def main() -> int:
    keys = []
    primary = os.getenv("GEMINI_API_KEY", "")
    if primary and not primary.startswith(("your_", "<<<")):
        keys.append(primary)
    for i in (2, 3):
        k = os.getenv(f"GEMINI_API_KEY_{i}", "")
        if k and not k.startswith(("your_", "<<<")):
            keys.append(k)
    if not keys:
        print("RESULT: no GEMINI_API_KEY found in .env")
        return 2
    key = keys[0]
    print(f"keys available: {len(keys)}")
    if len(keys) > 1:
        print(f"rotation: key_1 -> key_2 -> ... on 429")
    f = FusedLLM()
    out = []
    async for c in f.stream_prose(
        user_text="yaar aaj bahut thak gaya hu, bas suno thoda",
        turn_type="speech",
        policy={"mode": "VENT", "response_goal": "encourage_continuation"},
        memory_view=[],
        threads=[],
        history=[],
        turn_no=1,
        degraded=False,
        key=key,
    ):
        out.append(c)
    reply = "".join(out).strip()
    print("meta      :", json.dumps(f.meta))
    print("head      :", "parsed" if f.head else "none")
    print("reply     :", reply[:160])
    if f.meta.get("llm_failed"):
        err = f.meta.get("llm_error", "")
        if "429" in err:
            print("RESULT: QUOTA/429 — wait for reset (midnight PT) or check plan limits")
        else:
            print(f"RESULT: LLM ERROR — {err}")
        return 1
    if not reply:
        print("RESULT: empty reply — paste this output back")
        return 1
    print("RESULT: TRANSPORT OK — fused head parsed and reply produced")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
