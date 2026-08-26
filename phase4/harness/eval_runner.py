#!/usr/bin/env python3
"""Phase 4 evaluation harness (T4.5).

Modes:
  --batch2        replay updater_batch2.json through the reference updater
                  (pure function, offline) + determinism check (G-DET)
  --golden        live fused-call run of golden suite speech fixtures
                  (perception bands + derived-policy assertions); degradation
                  fixtures (acoustic_only/idle) run offline through the updater
  --dc            live fused-call run of the D-C safety set (G-SAFE baseline)
  --determinism K replay each batch-2 fixture K times, require byte-identical
                  outputs (default 3; used with --batch2)

Offline by default for --batch2. --golden/--dc need GEMINI_API_KEY in .env.
Reference updater: phase4/harness/reference_updater.py (evaluation tooling).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from phase4.harness.reference_updater import update, merge_state, default_state  # noqa: E402


def dig(d, path: str):
    cur = d
    for part in path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
                continue
            except (ValueError, IndexError):
                return None
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def check_state_assertions(state: dict, assertions: dict) -> list[str]:
    fails = []
    for path, want in assertions.items():
        if path.endswith("_lte"):
            got = dig(state, path[:-4])
            if got is None or not (got <= want):
                fails.append(f"{path}={got} !<= {want}")
        else:
            got = dig(state, path)
            if got != want:
                fails.append(f"{path}={got!r} != {want!r}")
    return fails


def check_policy_assertions(policy: dict, assertions: dict) -> list[str]:
    return [f"policy.{k}={policy.get(k)!r} != {want!r}" for k, want in assertions.items()
            if policy.get(k) != want]


def replay_batch2(k: int, verbose: bool) -> tuple[int, int, list[str]]:
    suite = json.load(open(os.path.join(HERE, "..", "golden", "updater_batch2.json")))
    fixtures = suite["fixtures"]
    passed, failed, failures = 0, 0, []

    for fx in fixtures:
        results = []
        for _ in range(k):
            state = merge_state(fx.get("initial_state"))
            log_all, policies = [], []
            steps = fx["steps"]
            step_fails = []
            for i, step in enumerate(steps, start=1):
                tr = {"turn": step.get("turn", i)}
                tr.update(step.get("turn_record", {}) or {})
                if step.get("policy_derived"):
                    tr["policy_derived"] = step["policy_derived"]
                head = step.get("head")
                state, policy, log = update(state, tr, head)
                log_all.extend(log)
                policies.append(policy)
                want = fx.get("expect", {}).get("after_step", {}).get(str(i))
                if want:
                    for sub in want.get("state", {}):
                        pass  # handled below via per-step state snapshots
                    # per-step assertions evaluated on the state AT this step
                # immediate evaluation for after_step (needs current state)
                if want:
                    sf = check_state_assertions(state, want.get("state", {}))
                    pf = check_policy_assertions(policy, want.get("policy", {}))
                    lc = [c for c in want.get("log_contains", [])
                          if not any(c in entry for entry in log_all)]
                    step_fails.extend([f"step{i}: {m}" for m in sf + pf + lc])
            # final assertions
            fin = fx.get("expect", {})
            sf = check_state_assertions(state, fin.get("state", {}))
            lc = [c for c in fin.get("log_contains", [])
                  if not any(c in entry for entry in log_all)]
            step_fails.extend([f"final: {m}" for m in sf + lc])
            pol = fin.get("policy")
            if pol:
                step_fails.extend([f"final: {m}" for m in check_policy_assertions(policies[-1], pol)])
            results.append((state, policy, log_all, step_fails))

        # determinism (G-DET): all k replays byte-identical
        canon = [json.dumps([r[0], r[1], r[2]], sort_keys=True, ensure_ascii=False) for r in results]
        det_ok = len(set(canon)) == 1

        fx_fails = results[0][3] + ([] if det_ok else ["DETERMINISM: replays differ"])
        if not fx_fails:
            passed += 1
            if verbose:
                print(f"  PASS {fx['id']}: {fx['purpose']}")
        else:
            failed += 1
            failures.append((fx["id"], fx_fails))
            print(f"  FAIL {fx['id']}: {fx['purpose']}")
            for m in fx_fails[:6]:
                print(f"        - {m}")
    return passed, failed, failures


def run_batch2(args) -> int:
    print(f"== Batch-2 updater replay (determinism k={args.determinism}) ==")
    p, f, failures = replay_batch2(args.determinism, verbose=True)
    print(f"\n{p} passed, {f} failed  (G-DET determinism included)")
    if failures:
        print("\nFailures:")
        for fid, msgs in failures:
            for m in msgs:
                print(f"  {fid}: {m}")
    return 0 if f == 0 else 1


# ---------------------------------------------------------------------------
# Live modes (--golden, --dc) — fused fused-call evaluation
# ---------------------------------------------------------------------------

async def _live_stream(key: str, model: str, system: str, contents: str, temperature: float):
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=key)
    config = types.GenerateContentConfig(temperature=temperature, system_instruction=system)
    parts = []
    t0 = time.perf_counter()
    stream = await client.aio.models.generate_content_stream(
        model=model, contents=contents, config=config)
    async for chunk in stream:
        if chunk.text:
            parts.append(chunk.text)
    return "".join(parts), time.perf_counter() - t0


def parse_head(full: str):
    import re
    m = re.search(r"<perception>(.*?)</perception>", full, re.DOTALL)
    if not m:
        return None, full.strip(), "missing <perception> tags"
    raw = m.group(1).strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        return json.loads(raw), full[m.end():].strip(), ""
    except json.JSONDecodeError as e:
        return None, full[m.end():].strip(), f"invalid JSON: {e}"


def perception_band_fails(head: dict | None, bands: dict) -> list[str]:
    if head is None:
        return ["no head parsed"]
    fails = []
    emo = head.get("emotion", {})
    if "emotion_primary_in" in bands and emo.get("primary") not in bands["emotion_primary_in"]:
        fails.append(f"primary={emo.get('primary')} not in {bands['emotion_primary_in']}")
    if "emotion_primary_not" in bands and emo.get("primary") in bands["emotion_primary_not"]:
        fails.append(f"primary={emo.get('primary')} is forbidden")
    if "valence" in bands and emo.get("valence") != bands["valence"]:
        fails.append(f"valence={emo.get('valence')} != {bands['valence']}")
    if "valence_in" in bands and emo.get("valence") not in bands["valence_in"]:
        fails.append(f"valence={emo.get('valence')} not in {bands['valence_in']}")
    if "intensity_range" in bands:
        lo, hi = bands["intensity_range"]
        got = (emo.get("intensity", {}) or {}).get("ordinal")
        if not isinstance(got, int) or not lo <= got <= hi:
            fails.append(f"intensity={got} outside [{lo},{hi}]")
    if "risk_level" in bands and (head.get("safety", {}) or {}).get("risk_level") != bands["risk_level"]:
        fails.append(f"risk={head.get('safety', {}).get('risk_level')} != {bands['risk_level']}")
    if "risk_level_not" in bands and (head.get("safety", {}) or {}).get("risk_level") in bands["risk_level_not"]:
        fails.append(f"risk={head.get('safety', {}).get('risk_level')} is forbidden (over-escalation)")
    if "thread_action_in" in bands and (head.get("thread", {}) or {}).get("action") not in bands["thread_action_in"]:
        fails.append(f"thread.action={head.get('thread', {}).get('action')} not in {bands['thread_action_in']}")
    if "entities_mention_any" in bands:
        ents = [str(e).lower() for e in (head.get("thread", {}) or {}).get("entities", [])]
        joined = " ".join(ents)
        if not any(w.lower() in joined for w in bands["entities_mention_any"]):
            fails.append(f"no entity from {bands['entities_mention_any']} in {ents}")
    if "user_need" in bands and head.get("user_need") != bands["user_need"]:
        fails.append(f"user_need={head.get('user_need')} != {bands['user_need']}")
    if "advice_requested" in bands and head.get("advice_requested") != bands["advice_requested"]:
        fails.append(f"advice_requested={head.get('advice_requested')} != {bands['advice_requested']}")
    if "safety_self_harm" in bands and (head.get("safety", {}) or {}).get("self_harm") != bands["safety_self_harm"]:
        fails.append("safety.self_harm mismatch")
    return fails


async def run_golden(args, key: str) -> int:
    from phase4.harness.reference_updater import update
    suite = json.load(open(os.path.join(HERE, "..", "golden", "suite_v1.json")))
    n_pass = n_fail = 0
    for fx in suite["fixtures"]:
        tt = fx.get("turn_type", "speech")
        tr = {"turn": 1, "turn_type": tt}
        if fx["context"].get("interrupted_agent_response"):
            tr["interrupted_agent_response"] = fx["context"]["interrupted_agent_response"]
        if tt == "speech" and fx["user_turn"]:
            print(f"\n[{fx['id']} {fx['scenario']}] {fx['title']}")
            system_args = ""
            contents = json.dumps({"policy": fx["context"].get("policy"), "memory": fx["context"].get("memory_view"),
                                    "threads": fx["context"].get("threads"), "history": fx["context"].get("history"),
                                    "user_turn": fx["user_turn"]}, ensure_ascii=False)
            from phase3.fused_perception_probe import SYSTEM_FUSED  # validated prompt contract
            try:
                full, e2e = await _live_stream(key, args.model, SYSTEM_FUSED, contents, args.temperature)
            except Exception as e:
                print(f"  LIVE ERROR: {type(e).__name__}: {str(e)[:150]}")
                n_fail += 1
                continue
            head, reply, err = parse_head(full)
            band_fails = perception_band_fails(head, fx["expected"].get("perception", {})) if head else [f"head: {err}"]
            # derived-policy assertions through the reference updater (deterministic)
            state, policy, _log = update(None, tr, head)
            pol_fails = check_policy_assertions(policy, fx["expected"].get("policy", {}))
            ok = not band_fails and not pol_fails
            n_pass, n_fail = (n_pass + 1, n_fail) if ok else (n_pass, n_fail + 1)
            print(f"  {'PASS' if ok else 'FAIL'} (e2e={e2e:.2f}s) reply: {reply[:90]}")
            for m in band_fails + pol_fails:
                print(f"        - {m}")
            print(f"  rubric (human): {'; '.join(fx['rubric'])}")
        else:
            # degradation fixture — offline through the updater (C7)
            state, policy, _log = update(None, tr, None)
            pol_fails = check_policy_assertions(policy, fx["expected"].get("policy", {}))
            deg = fx["expected"].get("degradation")
            if deg and policy.get("degradation") != deg:
                pol_fails.append(f"degradation={policy.get('degradation')} != {deg}")
            ok = not pol_fails
            n_pass, n_fail = (n_pass + 1, n_fail) if ok else (n_pass, n_fail + 1)
            print(f"\n[{fx['id']} {fx['scenario']}] {fx['title']}  (offline C7 path)")
            print(f"  {'PASS' if ok else 'FAIL'} policy={ {k: policy.get(k) for k in ('degradation', 'response_suppressed', 'mode')} }")
    print(f"\nGOLDEN: {n_pass} passed, {n_fail} failed (rubric items pending human review)")
    return 0 if n_fail == 0 else 1


async def run_dc(args, key: str) -> int:
    from phase3.fused_perception_probe import SYSTEM_FUSED
    ds = json.load(open(os.path.join(HERE, "..", "datasets", "safety_dc_v1.json")))
    tp = fp = fn = tn = 0
    fails = []
    for item in ds["items"]:
        contents = json.dumps({"policy": {"mode": "VENT"}, "memory": [], "threads": [],
                                "history": [], "user_turn": item["turn"]}, ensure_ascii=False)
        try:
            full, _ = await _live_stream(key, args.model, SYSTEM_FUSED, contents, args.temperature)
        except Exception as e:
            print(f"  {item['id']} LIVE ERROR {type(e).__name__}: {str(e)[:100]}")
            fails.append(item["id"])
            continue
        head, _, err = parse_head(full)
        got = (head or {}).get("safety", {}).get("risk_level", "unparsed")
        want = item["expected"]["risk_level"]
        highish = {"elevated_distress", "high_risk"}
        got_high, want_high = got in highish, want in highish
        if want_high and got_high:
            tp += 1
        elif want_high and not got_high:
            fn += 1
            fails.append(f"{item['id']} MISSED: want {want}, got {got} :: {item['turn'][:60]}")
        elif not want_high and got_high:
            fp += 1
            fails.append(f"{item['id']} OVER-ESCALATED: want {want}, got {got} :: {item['turn'][:60]}")
        else:
            tn += 1
        await asyncio.sleep(args.pace_sec)
    print(f"\nD-C baseline: TP={tp} TN={tn} FN={fn} FP={fp}  (recall-first: FN is the critical number)")
    for f in fails:
        print(f"  - {f}")
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch2", action="store_true")
    ap.add_argument("--golden", action="store_true")
    ap.add_argument("--dc", action="store_true")
    ap.add_argument("--determinism", type=int, default=3)
    ap.add_argument("--model", default="gemini-3.5-flash-lite")
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--pace-sec", type=float, default=7.0)
    args = ap.parse_args()
    if not (args.batch2 or args.golden or args.dc):
        ap.print_help()
        return 2
    key = None
    if args.golden or args.dc:
        from dotenv import load_dotenv
        load_dotenv()
        key = os.getenv("GEMINI_API_KEY", "")
        if not key or key.startswith(("your_", "<<<")):
            print("ERROR: GEMINI_API_KEY required for --golden/--dc")
            return 2
    if args.batch2:
        rc = run_batch2(args)
    else:
        rc = 0
    if args.golden:
        rc = max(rc, await run_golden(args, key))
    if args.dc:
        rc = max(rc, await run_dc(args, key))
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
