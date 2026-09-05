#!/usr/bin/env python3
"""Tiered test workflow — ONE entry point, four tiers (2026-09-05).

    python3 phase5/harness/test_tiers.py plan      [--changed P ...|--since REF|--area A ...]
    python3 phase5/harness/test_tiers.py quick     [same selectors] [--jobs N]
    python3 phase5/harness/test_tiers.py targeted  [same selectors] [--jobs N]
    python3 phase5/harness/test_tiers.py full      [--jobs N] [--no-suites]
    python3 phase5/harness/test_tiers.py live      [--session logs/session_X.log]
    python3 phase5/harness/test_tiers.py freeze    [--update] [--pin-divergence]
                                                   [--import LOG --name FIXTURE]

QUICK     suites whose import closure / source pins touch the changed modules
          + the directly affected rail fixture(s) replayed end to end.
TARGETED  QUICK's suites + replay of ONLY the affected turns of every archived
          fixture (prefix replay through the last affected turn; the carrier
          is threaded turn to turn so a mid-file start is impossible) + the
          replay self-consistency test. Per-turn frozen-input digests are
          checked against frozen_manifest.json: TARGETED consumes a SUBSET of
          the very bytes FULL consumes.
FULL      every suite + the COMPLETE archive replay (the standing acceptance
          / merge gate — phase5/harness/replay.py over all fixtures, output
          shown verbatim, exit code preserved) + frozen-input verification +
          the pinned accepted-divergence profile (any new OR vanished
          divergence fails). This tier is the merge gate; QUICK/TARGETED are
          developer feedback and never replace it.
LIVE      audio -> STT -> pipeline on the deployed worker. Required only when
          a LIVE_PATH module changed. It cannot run here: this command prints
          the runbook + runs the offline pre-checks (pyflakes / source pins)
          and, with --session, turns a captured session log into frozen
          evidence (verify + replay) — the only way LIVE output enters the
          deterministic tiers.

No product logic. The selectors (tiers_manifest.py) and the frozen-input
contract (frozen_inputs.py) are stdlib-only; agent code is imported only when
a replay actually runs.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from phase5.harness import frozen_inputs as fz      # noqa: E402
from phase5.harness import tiers_manifest as tm     # noqa: E402

ACCEPTED_PROFILE = ROOT / "phase5" / "harness" / "fixtures" / "accepted_divergence.json"


def say(msg: str = "") -> None:
    print(f"[tiers] {msg}" if msg else "")


def rel(p) -> str:
    return str(Path(p).resolve().relative_to(ROOT)).replace(os.sep, "/")


# ---------------------------------------------------------------------------
# change detection + selection
# ---------------------------------------------------------------------------
def _git(*args) -> list[str]:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                             timeout=30, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [l.strip() for l in out.splitlines() if l.strip()]


def changed_paths(args) -> tuple[list[str], str]:
    if getattr(args, "changed", None):
        return list(args.changed), "explicit --changed"
    if getattr(args, "since", None):
        return _git("diff", "--name-only", args.since), f"git diff {args.since}"
    if getattr(args, "area", None):
        return [], "explicit --area"
    wt = _git("diff", "--name-only", "HEAD") + _git("ls-files", "--others", "--exclude-standard")
    if wt:
        return sorted(set(wt)), "working tree vs HEAD (+untracked)"
    last = _git("diff", "--name-only", "HEAD~1", "HEAD")
    if last:
        return last, "last commit (HEAD~1..HEAD)"
    return [], "no change detected"


def build_plan(args) -> dict:
    paths, how = changed_paths(args)
    cls = tm.classify_change(paths)
    changed_modules = {p.replace(os.sep, "/") for p in paths}
    for a in (getattr(args, "area", None) or []):
        changed_modules |= set(tm.AREAS[a]["modules"])
    prop_areas, esc_reasons = tm.affected_areas(changed_modules)
    areas = set(cls["areas"]) | set(getattr(args, "area", None) or []) | prop_areas
    touch = tm.suite_touch_map()
    suites = set(tm.suites_for(changed_modules, touch)) | set(cls["tests"])
    suites = {s for s in suites if (ROOT / s).is_file()}
    if cls["unmapped_code"]:
        esc_reasons.append("unmapped agent/providers module(s) changed: " + ", ".join(cls["unmapped_code"]))
    if areas & tm.ESCALATE_TO_FULL:
        esc_reasons.append("pipeline/harness area changed")
    escalated = bool(esc_reasons)
    predicates: list[str] = []
    for a in sorted(areas):
        for p in tm.AREAS[a]["turns"]:
            if p not in predicates:
                predicates.append(p)
    if escalated:
        predicates = ["all"]
    quick_fixtures: list[str] = []
    for a in sorted(areas):
        for f in tm.AREAS[a]["quick_fixtures"]:
            if f not in quick_fixtures:
                quick_fixtures.append(f)
    live_required = cls["live_required"] or bool(areas & {"live_path"})
    # per-fixture targeted turn selection from the FROZEN projection
    targeted: dict[str, dict] = {}
    for d in tm.fixture_dirs():
        for s in tm.fixture_sessions(d):
            turns = [fz.project(t) for _, _, t in fz.iter_turns(s)]
            if not turns:
                continue
            all_tn = [int(t["turn"]) for t in turns]
            sel = tm.select_turns(turns, predicates) if predicates else []
            targeted[rel(s)] = {"fixture": d.name, "all": all_tn, "selected": sel,
                                "stop_after": max(sel) if sel else None}
    return {"changed": paths, "how": how, "classification": cls, "areas": sorted(areas),
            "changed_modules": sorted(changed_modules), "suites": sorted(suites),
            "escalated": escalated, "escalation_reasons": esc_reasons, "predicates": predicates,
            "quick_fixtures": quick_fixtures, "live_required": live_required,
            "targeted": targeted}


def print_plan(plan: dict) -> None:
    say(f"changed ({plan['how']}): " + (", ".join(plan["changed"]) or "-"))
    cls = plan["classification"]
    say(f"areas: {', '.join(plan['areas']) or '-'}"
        + (f"  | unmapped code -> pipeline: {', '.join(cls['unmapped_code'])}" if cls["unmapped_code"] else "")
        + (f"  | docs/other ignored: {len(cls['docs_or_other'])}" if cls["docs_or_other"] else ""))
    say(f"QUICK    suites ({len(plan['suites'])}): "
        + (", ".join(Path(s).name for s in plan["suites"]) or "none"))
    say(f"QUICK    fixture replay: {', '.join(plan['quick_fixtures']) or 'none'}")
    if plan["predicates"]:
        say(f"TARGETED turn predicates: {', '.join(plan['predicates'])}"
            + (f"  (ESCALATED to the full archive: {'; '.join(plan['escalation_reasons'])})"
               if plan["escalated"] else ""))
        for s, t in plan["targeted"].items():
            if t["selected"]:
                say(f"         {t['fixture']}: {len(t['selected'])}/{len(t['all'])} turns "
                    f"{_fmt_turns(t['selected'])} -> prefix replay through t{t['stop_after']}")
            else:
                say(f"         {t['fixture']}: 0/{len(t['all'])} turns -> skipped")
    else:
        say("TARGETED turn predicates: none (no replay-path change)")
    say(f"FULL     {len(tm.all_suites())} suites + complete replay ({tm.REPLAY_GLOB}) "
        f"+ frozen verify + accepted-divergence profile")
    say(f"LIVE     {'REQUIRED (live-path module changed)' if plan['live_required'] else 'not required'}")


def _fmt_turns(tns: list[int]) -> str:
    if len(tns) <= 8:
        return "t" + ",".join(str(t) for t in tns)
    return f"t{tns[0]}..t{tns[-1]} ({len(tns)})"


# ---------------------------------------------------------------------------
# suite execution
# ---------------------------------------------------------------------------
def run_suite(path: str, timeout: int = 900) -> dict:
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable, str(ROOT / path)], cwd=ROOT, capture_output=True,
                           text=True, timeout=timeout)
        rc, out = p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        rc, out = 124, f"TIMEOUT after {timeout}s"
    return {"suite": path, "rc": rc, "secs": round(time.time() - t0, 2), "tail": out[-2000:]}


def run_suites(suites: list[str], jobs: int) -> list[dict]:
    writers = [s for s in suites if s in tm.FIXTURE_WRITERS]
    others = [s for s in suites if s not in tm.FIXTURE_WRITERS]
    results: list[dict] = []
    # fixture writers first and serially (they rewrite committed fixtures that
    # the replay steps read afterwards)
    for s in writers:
        results.append(run_suite(s))
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        results.extend(ex.map(run_suite, others))
    results.sort(key=lambda r: r["suite"])
    fails = 0
    for r in results:
        ok = r["rc"] == 0
        fails += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'} {r['secs']:6.2f}s  {Path(r['suite']).name}")
        if not ok:
            for line in r["tail"].splitlines()[-25:]:
                print(f"        | {line}")
    say(f"suites: {len(results) - fails}/{len(results)} passed")
    return results


# ---------------------------------------------------------------------------
# replay steps (import agent code lazily)
# ---------------------------------------------------------------------------
def _replay_session(path, stop_after=None):
    from phase5.harness.replay import replay_session
    return replay_session(path, stop_after=stop_after)


def structured_profile(sessions: dict[str, int | None]) -> dict:
    """{rel_session: {"hard": {tn: {field: [got, want]}}, "notes": {tn: [..]},
    "checked": n, "skipped": n, "stop_after": k}} — replay diffs as data."""
    prof: dict = {}
    for s, stop in sessions.items():
        diffs, checked, skipped = _replay_session(ROOT / s, stop_after=stop)
        hard: dict = {}
        notes: dict = {}
        for tn, d in diffs.items():
            h = {f: [json.loads(json.dumps(v[0], default=str)), json.loads(json.dumps(v[1], default=str))]
                 for f, v in d.items() if f != "notes"}
            if h:
                hard[str(tn)] = h
            if d.get("notes"):
                notes[str(tn)] = list(d["notes"])
        prof[s] = {"hard": hard, "notes": notes, "checked": checked, "skipped": skipped,
                   "stop_after": stop}
    return prof


def load_accepted() -> dict | None:
    if not ACCEPTED_PROFILE.is_file():
        return None
    with open(ACCEPTED_PROFILE, encoding="utf-8") as f:
        return json.load(f)


def compare_profile(observed: dict, accepted: dict | None) -> list[str]:
    """Observed replay divergences must EQUAL the pinned accepted profile on
    every replayed turn (new divergence = fail; vanished divergence = fail —
    the profile must then be re-pinned deliberately). No profile file = the
    raw gate semantics (any hard divergence fails)."""
    problems: list[str] = []
    acc = (accepted or {}).get("profile", {})
    for s, obs in observed.items():
        stop = obs.get("stop_after")
        a = acc.get(s, {"hard": {}, "notes": {}})

        def _in_scope(tn: str) -> bool:
            return stop is None or int(tn) <= int(stop)

        a_hard = {tn: v for tn, v in a.get("hard", {}).items() if _in_scope(tn)}
        a_notes = {tn: v for tn, v in a.get("notes", {}).items() if _in_scope(tn)}
        if obs["hard"] != a_hard:
            for tn in sorted(set(obs["hard"]) | set(a_hard), key=int):
                if obs["hard"].get(tn) != a_hard.get(tn):
                    problems.append(
                        f"{s} t{tn}: hard divergence {'NEW' if tn not in a_hard else ('VANISHED' if tn not in obs['hard'] else 'CHANGED')}: "
                        f"observed={json.dumps(obs['hard'].get(tn), ensure_ascii=False)[:300]} "
                        f"accepted={json.dumps(a_hard.get(tn), ensure_ascii=False)[:300]}")
        if obs["notes"] != a_notes:
            for tn in sorted(set(obs["notes"]) | set(a_notes), key=int):
                if obs["notes"].get(tn) != a_notes.get(tn):
                    problems.append(f"{s} t{tn}: documented note set changed: "
                                    f"observed={obs['notes'].get(tn)} accepted={a_notes.get(tn)}")
    return problems


def all_sessions() -> list[Path]:
    out: list[Path] = []
    for d in tm.fixture_dirs():
        out.extend(tm.fixture_sessions(d))
    return out


def print_avoided(sessions: dict[str, int | None]) -> dict:
    tot = {"turns": 0, "stt": 0, "vad": 0, "llm": 0, "tts": 0, "echo": 0}
    for s, stop in sessions.items():
        c = fz.avoided_calls(ROOT / s, upto=stop)
        for k in tot:
            tot[k] += c[k]
    say(f"expensive live calls replaced by frozen evidence: {tot['stt']} STT, {tot['vad']} VAD/endpoint, "
        f"{tot['llm']} LLM, {tot['tts']} TTS/playback, {tot['echo']} echo-corr "
        f"(over {tot['turns']} archived turns) — 0 network calls made")
    return tot


def frozen_fixtures() -> list[tuple[Path, list[Path]]]:
    """Fixture dirs that carry at least one archived TURN (smoke5-13 hold
    prose smoke notes only — nothing to freeze, nothing replayed)."""
    out = []
    for d in tm.fixture_dirs():
        sess = [s for s in tm.fixture_sessions(d) if any(True for _ in fz.iter_turns(s))]
        if sess:
            out.append((d, sess))
    return out


def frozen_verify() -> list[str]:
    problems: list[str] = []
    for d, sess in frozen_fixtures():
        problems.extend(fz.verify_fixture(d, sess))
    notes = [p for p in problems if ": NOTE " in p]
    hard = [p for p in problems if ": NOTE " not in p]
    for n in notes:
        say(f"frozen note: {n}")
    if hard:
        for h in hard:
            say(f"FROZEN MISMATCH: {h}")
    else:
        say(f"frozen inputs verified: {len(frozen_fixtures())} fixture manifest(s) match the "
            f"archive bytes (FROZEN_KEYS contract {fz.FROZEN_VERSION})")
    return hard


def subset_proof(targeted: dict) -> list[str]:
    """TARGETED consumes a subset of FULL's frozen inputs: every selected turn
    is an archived turn of the same file, and its per-turn inputs digest
    equals the digest recorded in frozen_manifest.json (the record FULL is
    verified against)."""
    problems: list[str] = []
    n_sel = n_all = 0
    for s, t in targeted.items():
        n_all += len(t["all"])
        if not t["selected"]:
            continue
        n_sel += len(t["selected"])
        if not set(t["selected"]) <= set(t["all"]):
            problems.append(f"{s}: selected turns not a subset of the archive")
        man = fz.load_manifest(ROOT / s.rsplit("/", 1)[0])
        rec = None
        if man:
            rec = next((x for x in man.get("sessions", []) if x["session"] == Path(s).name), None)
        if rec is None:
            problems.append(f"{s}: no frozen manifest record (run freeze --update)")
            continue
        recorded = {pt["turn"]: pt["inputs_sha256"] for pt in rec["per_turn"]}
        cur = {pt["turn"]: pt["inputs_sha256"] for pt in fz.freeze_session(ROOT / s)["per_turn"]}
        for tn in t["selected"]:
            if recorded.get(tn) != cur.get(tn) or cur.get(tn) is None:
                problems.append(f"{s} t{tn}: archive bytes changed since freeze (a fixture writer "
                                f"regenerated a different archive = behaviour change, or the file was "
                                f"edited) — review, then re-freeze deliberately")
    if not problems:
        say(f"subset proof: {n_sel}/{n_all} archived turns selected; every selected turn's "
            f"inputs digest == frozen_manifest.json record (same frozen bytes FULL replays)")
    return problems


# ---------------------------------------------------------------------------
# tiers
# ---------------------------------------------------------------------------
def cmd_plan(args) -> int:
    print_plan(build_plan(args))
    return 0


def cmd_quick(args) -> int:
    t0 = time.time()
    plan = build_plan(args)
    say("QUICK")
    print_plan(plan)
    fails = 0
    if plan["suites"]:
        res = run_suites(plan["suites"], args.jobs)
        fails += sum(1 for r in res if r["rc"] != 0)
    else:
        say("no suite touches the change")
    if plan["quick_fixtures"]:
        sessions = {rel(s): None for d in tm.fixture_dirs() if d.name in plan["quick_fixtures"]
                    for s in tm.fixture_sessions(d)}
        prof = structured_profile(sessions)
        probs = compare_profile(prof, load_accepted())
        for s, p in prof.items():
            say(f"replay {s.split('/')[-2]}: {p['checked']} turns, {len(p['hard'])} hard, {len(p['notes'])} note(s)")
        for pr in probs:
            say(f"REPLAY PROBLEM: {pr}")
        fails += len(probs)
        print_avoided(sessions)
    say(f"QUICK {'PASS' if not fails else 'FAIL'} in {time.time() - t0:.1f}s"
        f"{'' if not plan['live_required'] else '  — LIVE tier REQUIRED for this change'}")
    return 1 if fails else 0


def cmd_targeted(args) -> int:
    t0 = time.time()
    plan = build_plan(args)
    say("TARGETED")
    print_plan(plan)
    fails = 0
    suites = set(plan["suites"]) | {tm.IDENTITY_TEST}
    res = run_suites(sorted(suites), args.jobs)
    fails += sum(1 for r in res if r["rc"] != 0)
    sessions = {s: t["stop_after"] for s, t in plan["targeted"].items() if t["selected"]}
    if sessions:
        prof = structured_profile(sessions)
        probs = compare_profile(prof, load_accepted())
        for s, p in prof.items():
            say(f"replay {s.split('/')[-2]}: prefix through t{p['stop_after']} — {p['checked']} turns "
                f"checked, {len(p['hard'])} hard, {len(p['notes'])} note(s)")
        for pr in probs:
            say(f"REPLAY PROBLEM: {pr}")
        fails += len(probs)
        fails += len(subset_proof(plan["targeted"]))
        print_avoided(sessions)
    else:
        say("no archived turn is affected by this change — replay skipped")
    say(f"TARGETED {'PASS' if not fails else 'FAIL'} in {time.time() - t0:.1f}s — not a merge gate; run FULL before merge"
        f"{'' if not plan['live_required'] else '  — LIVE tier REQUIRED for this change'}")
    return 1 if fails else 0


def cmd_full(args) -> int:
    t0 = time.time()
    say("FULL — standing acceptance / merge gate")
    fails = 0
    if not args.no_suites:
        res = run_suites(tm.all_suites(), args.jobs)
        fails += sum(1 for r in res if r["rc"] != 0)
    # 1) the standing gate command, verbatim, exit code preserved
    cmd = [sys.executable, str(ROOT / tm.REPLAY_GATE), tm.REPLAY_GLOB]
    say(f"$ python3 {tm.REPLAY_GATE} '{tm.REPLAY_GLOB}'")
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    for line in (p.stdout or "").splitlines():
        if line.startswith("[replay]") or line.startswith("    t"):
            print(f"  {line}")
    say(f"replay.py exit code {p.returncode} (0 = empty diff; 1 = standing divergence profile present)")
    # 2) the same replay as data, against the pinned accepted profile
    sessions = {rel(s): None for s in all_sessions()}
    prof = structured_profile(sessions)
    accepted = load_accepted()
    probs = compare_profile(prof, accepted)
    n_hard = sum(len(v["hard"]) for v in prof.values())
    n_notes = sum(len(v["notes"]) for v in prof.values())
    n_turns = sum(v["checked"] for v in prof.values())
    if accepted is None:
        say(f"no accepted_divergence.json — raw gate semantics: {n_hard} hard divergence(s)")
        fails += 1 if n_hard else 0
    elif probs:
        for pr in probs:
            say(f"GATE: {pr}")
        fails += len(probs)
    else:
        say(f"replay gate: {n_turns} turns; divergence profile == accepted standing profile "
            f"({n_hard} hard turn(s), {n_notes} note(s); pinned {accepted.get('pinned_at')})")
    # 3) frozen inputs still describe the fixtures (also proves the fixture
    #    writers regenerated byte-identical archives)
    fails += len(frozen_verify())
    print_avoided(sessions)
    say(f"FULL {'PASS' if not fails else 'FAIL'} in {time.time() - t0:.1f}s")
    return 1 if fails else 0


def cmd_live(args) -> int:
    plan = build_plan(args)
    say("LIVE — audio -> STT -> pipeline on the deployed worker (cannot execute here)")
    say(f"required for the current change: {'YES' if plan['live_required'] else 'no'} "
        f"(areas: {', '.join(plan['areas']) or '-'})")
    fails = 0
    # offline pre-checks that ARE possible for live-path modules
    live_mods = [m for m in sorted(tm.LIVE_PATH_MODULES) if (ROOT / m).is_file()]
    try:
        p = subprocess.run([sys.executable, "-m", "pyflakes", *live_mods], cwd=ROOT,
                           capture_output=True, text=True)
        warn = [l for l in (p.stdout or "").splitlines() if "undefined name" in l or "syntax" in l.lower()]
        say(f"pyflakes over {len(live_mods)} live-path modules: {len(warn)} undefined-name/syntax finding(s)")
        for w in warn:
            say(f"  {w}")
        fails += len(warn)
    except OSError:
        say("pyflakes not available — skipped")
    touch = tm.suite_touch_map()
    pin_suites = sorted(s for s, mods in touch.items() if "agent/main.py" in mods)
    say(f"source-pin suites for agent/main.py ({len(pin_suites)}): "
        + ", ".join(Path(s).name for s in pin_suites))
    res = run_suites(pin_suites, args.jobs)
    fails += sum(1 for r in res if r["rc"] != 0)
    sha = (_git("rev-parse", "--short=12", "HEAD") or ["<sha>"])[0]
    branch = (_git("rev-parse", "--abbrev-ref", "HEAD") or ["<branch>"])[0]
    print()
    say("RUNBOOK (owner machine):")
    say(f"  1. AIVA_BRANCH={branch} AIVA_EXPECTED={sha} WORKER_COUNT=2 bash start_aiva.sh   # wait for DEPLOY VERIFIED")
    say("  2. speak the scenario (docs/SMOKE_KIT_V8.md / LIVE_TEST.md); every turn archives to logs/session_<ts>.log")
    say("  3. python3 phase5/harness/test_tiers.py live --session logs/session_<ts>.log     # verify + replay the capture")
    say("  4. python3 phase5/harness/test_tiers.py freeze --import logs/session_<ts>.log --name <fixture>  # freeze it")
    say("  LIVE evidence enters the deterministic tiers ONLY through step 4 (no fixture is ever hand-edited).")
    if args.session:
        s = Path(args.session)
        if not s.is_file():
            say(f"no such session log: {s}")
            return 1
        m = fz.freeze_session(s)
        say(f"session {s.name}: {m['turns']} turns; avoided calls if frozen: {m['avoided_calls']}")
        obs = sum(1 for t in m["per_turn"] if t["observation_version"])
        say(f"  numeric_observation present on {obs}/{m['turns']} turns; "
            f"providers: {sorted({t['stt_provider'] for t in m['per_turn'] if t['stt_provider']})}")
        diffs, checked, skipped = _replay_session(s)
        hard = [tn for tn, d in diffs.items() if any(f != 'notes' for f in d)]
        say(f"  replay: {checked} turns checked, {skipped} skipped, {len(hard)} hard divergence(s)"
            + (f" at t{','.join(str(t) for t in hard)}" if hard else ""))
        for tn in hard:
            for f, v in diffs[tn].items():
                if f != "notes":
                    say(f"    t{tn}.{f}: replay={v[0]!r} archived={v[1]!r}")
        fails += len(hard)
    say(f"LIVE pre-checks {'PASS' if not fails else 'FAIL'} — live execution itself is NOT performed here")
    return 1 if fails else 0


def cmd_freeze(args) -> int:
    fails = 0
    if args.import_log:
        src = Path(args.import_log)
        if not src.is_file() or not args.name:
            say("freeze --import needs an existing LOG and --name FIXTURE")
            return 2
        dest = tm.FIXTURES_DIR / args.name
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / src.name
        if target.exists():
            say(f"refusing to overwrite {rel(target)} (frozen archives are never rewritten)")
            return 1
        shutil.copyfile(src, target)   # raw bytes; the manifest documents the projection
        say(f"imported {src} -> {rel(target)} ({target.stat().st_size} bytes, raw archive kept)")
        fz.write_manifest(dest, tm.fixture_sessions(dest))
        say(f"wrote {rel(fz.manifest_path(dest))}")
        return 0
    if args.update:
        for d, sess in frozen_fixtures():
            man = fz.write_manifest(d, sess)
            for s in man["sessions"]:
                say(f"froze {d.name}/{s['session']}: {s['turns']} turns, inputs {s['inputs_sha256'][:12]}…, "
                    f"oracle {s['oracle_sha256'][:12]}…, raw {s['raw_bytes']}B -> projection {s['projection_bytes']}B")
    if args.pin_divergence:
        sessions = {rel(s): None for s in all_sessions()}
        prof = structured_profile(sessions)
        pinned = {"version": 1,
                  "pinned_at": time.strftime("%Y-%m-%d"),
                  "build": (_git("rev-parse", "--short=12", "HEAD") or ["unknown"])[0],
                  "note": ("Accepted standing divergence profile of the FULL replay gate. Every hard "
                           "divergence and documented note listed here is a known, reviewed live-vs-"
                           "offline boundary (see docs/VALUE_TRANSACTION_LOCK.md: t1 greeting rail "
                           "archived before the rail existed, t20 live cap-trim point, t11 stream-"
                           "chunk repeat-guard note). FULL fails on ANY difference from this profile "
                           "— new or vanished. Re-pin only deliberately: test_tiers.py freeze "
                           "--pin-divergence, and say why in the commit."),
                  "profile": {s: {"hard": v["hard"], "notes": v["notes"], "checked": v["checked"]}
                              for s, v in prof.items() if v["hard"] or v["notes"]}}
        with open(ACCEPTED_PROFILE, "w", encoding="utf-8") as f:
            json.dump(pinned, f, ensure_ascii=False, indent=1)
            f.write("\n")
        n_hard = sum(len(v["hard"]) for v in prof.values())
        say(f"pinned accepted divergence profile -> {rel(ACCEPTED_PROFILE)} ({n_hard} hard turn(s), "
            f"{sum(len(v['notes']) for v in prof.values())} note(s))")
    if not (args.update or args.pin_divergence):
        fails += len(frozen_verify())
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def selectors(sp):
        sp.add_argument("--changed", nargs="*", help="changed repo paths (default: git working tree vs HEAD)")
        sp.add_argument("--since", help="git ref to diff against instead of the working tree")
        sp.add_argument("--area", nargs="*", choices=sorted(tm.AREAS), help="force change areas")
        sp.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1))

    for name, fn in (("plan", cmd_plan), ("quick", cmd_quick), ("targeted", cmd_targeted), ("live", cmd_live)):
        sp = sub.add_parser(name)
        selectors(sp)
        sp.set_defaults(fn=fn)
        if name == "live":
            sp.add_argument("--session", help="captured logs/session_*.log to verify + replay")
    sp = sub.add_parser("full")
    sp.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1))
    sp.add_argument("--no-suites", action="store_true", help="replay gate + frozen verify only")
    sp.set_defaults(fn=cmd_full)
    sp = sub.add_parser("freeze")
    sp.add_argument("--update", action="store_true", help="(re)write frozen_manifest.json for every fixture")
    sp.add_argument("--pin-divergence", action="store_true", help="re-pin the accepted divergence profile")
    sp.add_argument("--import", dest="import_log", help="copy a raw logs/session_*.log into a new fixture")
    sp.add_argument("--name", help="fixture directory name for --import")
    sp.set_defaults(fn=cmd_freeze)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
