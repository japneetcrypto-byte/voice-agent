#!/usr/bin/env python3
"""Tier infrastructure pins (2026-09-05) — the test workflow itself is under
test. Nothing here exercises product behaviour; it proves the TIERS are
honest:

  A. FROZEN INPUTS  — the frozen projection is exactly what the replay gate
     consumes: replay(project(archive)) == replay(archive) on every fixture,
     byte-for-byte on every compared field; the manifest digests match disk.
  B. IMMUTABILITY   — a frozen numeric_observation record that changes in
     place is detected (verify fails); the observation is oracle-only, never
     an input key.
  C. SUBSET         — TARGETED's selected turns are archived turns of the same
     file; the prefix replay reproduces, turn for turn, the same diffs the
     full replay produces for those turns (derivative, not a re-computation
     on different inputs); stop_after=None is the unchanged full gate.
  D. QUICK ⊆ FULL   — every QUICK suite is one of FULL's suites; the QUICK
     fixture is one of FULL's fixtures; selection is monotone (more changed
     modules never select fewer suites/turns).
  E. GATE PRESERVED — FULL still runs the standing command verbatim; the
     accepted divergence profile equals the current replay profile (t1/t20
     hard, t11 note); a NEW divergence would fail FULL (negative control).
  F. LIVE           — live-path modules are declared, they are exactly the
     ones that cannot run offline, and a change there flags LIVE required.
  G. NO PRODUCT LOGIC — the tier modules import no agent module at import
     time (selection is over the frozen archive + the code graph only).
  H. ECHO-DROPPED SKIP — a turn main.py's text echo filter ate never reached
     the pipeline (no archived decision); the gate skips it like the WAIT
     turns instead of reporting a false divergence, and the carrier still
     reaches the turns after it (owner session 102221 t15).
"""
from __future__ import annotations

import ast
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from phase5.harness import frozen_inputs as fz          # noqa: E402
from phase5.harness import tiers_manifest as tm         # noqa: E402
from phase5.harness import test_tiers as tt             # noqa: E402
from phase5.harness.replay import replay_session  # noqa: E402

fails = 0


def check(label, got, want=True):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f"  (got {got!r}, want {want!r})"))
    return ok


FIXTURES = [(d, s) for d, s in tt.frozen_fixtures()]
SESSIONS = [s for _, ss in FIXTURES for s in ss]
check("fixtures with archived turns discovered", len(SESSIONS) >= 3)

# ---------------------------------------------------------------------------
print("== A. frozen projection == what the gate consumes ==")
tmpdir = Path(tempfile.mkdtemp())
for s in SESSIONS:
    full_diffs, full_checked, full_skipped = replay_session(s)
    n, text = fz.emit_projection(s)
    proj = tmpdir / s.name
    proj.write_text(text, encoding="utf-8")
    p_diffs, p_checked, p_skipped = replay_session(proj)
    same = (json.dumps(full_diffs, sort_keys=True, default=str) == json.dumps(p_diffs, sort_keys=True, default=str)
            and (full_checked, full_skipped) == (p_checked, p_skipped))
    check(f"{s.parent.name}: replay(project(archive)) == replay(archive) "
          f"[{full_checked} turns, {sum(1 for d in full_diffs.values() if any(f != 'notes' for f in d))} hard]", same)
    man = fz.load_manifest(s.parent)
    rec = next(x for x in man["sessions"] if x["session"] == s.name)
    cur = fz.freeze_session(s)
    check(f"{s.parent.name}: manifest digests match disk (inputs/oracle/projection)",
          all(cur[k] == rec[k] for k in ("inputs_sha256", "oracle_sha256", "projection_sha256", "turns")))
    check(f"{s.parent.name}: projection smaller than the raw archive "
          f"({cur['projection_bytes']}B vs {cur['raw_bytes']}B)", cur["projection_bytes"] < cur["raw_bytes"])

# every key context_from_archived / _compare / the skip predicate read is frozen
replay_src = (ROOT / "phase5" / "harness" / "replay.py").read_text(encoding="utf-8")
import re
_code_only = "\n".join(l for l in replay_src.splitlines() if not l.lstrip().startswith("#"))
_code_only = re.sub(r'"""[\s\S]*?"""', "", _code_only)          # drop docstrings (prose mentions)
read_keys = set(re.findall(r'(?:turn|archived|prior_state)\.get\("([a-z_]+)"\)', _code_only))
read_keys |= set(re.findall(r'(?:turn|archived|prior_state)\["([a-z_]+)"\]', _code_only))
read_keys |= set(re.findall(r'_exact(?:_if_present)?\(diffs, replay, archived, "([a-z_]+)"\)', _code_only))
read_keys |= set(re.findall(r'"([a-z_]+)" in archived', _code_only))
missing = sorted(read_keys - set(fz.FROZEN_KEYS))
check(f"every archive key replay.py reads is in FROZEN_KEYS ({len(read_keys)} keys read)", missing, [])
check("FROZEN_KEYS ⊇ the old GATE_KEYS projection",
      set(__import__("phase5.harness.project_session_log", fromlist=["GATE_KEYS"]).GATE_KEYS) <= set(fz.FROZEN_KEYS))

# ---------------------------------------------------------------------------
print("== B. immutability of the frozen NumericObservation ==")
rail = next(s for s in SESSIONS if s.parent.name == "session_103339_rail")
turns = [t for _, _, t in fz.iter_turns(rail)]
victim = next(t for t in turns if (t.get("numeric_observation") or {}).get("certainty") == "COMPLETE")
mut = copy.deepcopy(turns)
for t in mut:
    if t["turn"] == victim["turn"]:
        t["numeric_observation"]["items"][0]["slots"][0]["digit"] = "9"     # in-place edit
tampered_dir = tmpdir / "tampered"
tampered_dir.mkdir()
tampered = tampered_dir / rail.name
tampered.write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in mut), encoding="utf-8")
import shutil
shutil.copyfile(fz.manifest_path(rail.parent), fz.manifest_path(tampered_dir))
problems = fz.verify_fixture(tampered_dir, [tampered])
check(f"in-place mutation of an archived observation (t{victim['turn']}) fails frozen verify",
      any("oracle_sha256" in p for p in problems))
check("...and the mutated record no longer matches the pure observe() replay (gate catches it too)",
      any(f != "notes" for f in replay_session(tampered)[0].get(victim["turn"], {})))
check("numeric_observation is ORACLE-only (never an input key)",
      "numeric_observation" in fz.ORACLE_KEYS and "numeric_observation" not in fz.INPUT_KEYS)
check("numeric_audit (derived view) is not frozen as an input either", "numeric_audit" not in fz.INPUT_KEYS)
# a byte change OUTSIDE FROZEN_KEYS is a note, not a mismatch
mut2 = copy.deepcopy(turns)
mut2[0]["llm_input"] = "edited prompt text that no tier reads"
outside_dir = tmpdir / "outside"
outside_dir.mkdir()
outside = outside_dir / rail.name
outside.write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in mut2), encoding="utf-8")
shutil.copyfile(fz.manifest_path(rail.parent), fz.manifest_path(outside_dir))
probs2 = fz.verify_fixture(outside_dir, [outside])
check("a change outside FROZEN_KEYS is reported as a NOTE only (cannot influence a tier)",
      len(probs2) == 1 and ": NOTE " in probs2[0])

# ---------------------------------------------------------------------------
print("== C. TARGETED prefix replay is a derivative of the FULL replay ==")
for s in SESSIONS:
    full_diffs, full_checked, _ = replay_session(s)
    tns = [int(t["turn"]) for _, _, t in fz.iter_turns(s)]
    for stop in (tns[len(tns) // 3], tns[len(tns) // 2], tns[-1]):
        part_diffs, part_checked, _ = replay_session(s, stop_after=stop)
        expect = {tn: d for tn, d in full_diffs.items() if int(tn) <= stop}
        check(f"{s.parent.name}: prefix through t{stop} reproduces FULL's diffs for t<=t{stop} "
              f"({part_checked}/{full_checked} turns)",
              json.dumps(part_diffs, sort_keys=True, default=str) == json.dumps(expect, sort_keys=True, default=str)
              and part_checked <= full_checked)
    check(f"{s.parent.name}: stop_after=None == standing full gate",
          replay_session(s, stop_after=None)[0] == full_diffs)

plan = tt.build_plan(type("A", (), {"changed": ["agent/numeric_observation.py"], "since": None, "area": None})())
sel = plan["targeted"]
# Since the N6 gate (2026-09-05) the controller CONSUMES the observation
# (agent/conversation_controller.py imports agent.numeric_observation), so a
# change to the observation propagates to the rail area by reverse dependency:
# every rail turn of the rail fixture is selected, not only the digit-bearing
# ones. The digit-bearing turns must still be inside the selection.
check("observation change propagates to the rail (the controller consumes it): areas = observation + rail",
      set(plan["areas"]) >= {"observation", "rail"})
check("observation change selects every digit-bearing turn of the rail fixture (and the rail turns around them)",
      set([5, 8, 9, 16, 17, 18, 19]) <= set(sel[tt.rel(rail)]["selected"]))
check("every selected turn is an archived turn of the same file (subset)",
      all(set(t["selected"]) <= set(t["all"]) for t in sel.values()))
check("subset proof passes on the frozen fixtures", tt.subset_proof(sel) == [])
# selection is a pure function of the FROZEN projection: hide all non-frozen keys and reselect
for s in SESSIONS:
    frozen_only = [fz.project(t) for _, _, t in fz.iter_turns(s)]
    raw = [t for _, _, t in fz.iter_turns(s)]
    for preds in (["observation"], ["rail"], ["delivery", "detail", "greeting"], ["routing"], ["fused"]):
        check(f"{s.parent.name}: selection {preds} identical on frozen projection vs raw archive",
              tm.select_turns(frozen_only, preds) == tm.select_turns(raw, preds))

# ---------------------------------------------------------------------------
print("== D. QUICK ⊆ FULL, monotone selection ==")
all_suites = set(tm.all_suites())
for changed in (["agent/numeric_observation.py"], ["agent/precision_rail.py"], ["agent/reply_guard.py"],
                ["agent/memory_store.py"], ["providers/stt.py"]):
    p = tt.build_plan(type("A", (), {"changed": changed, "since": None, "area": None})())
    check(f"{changed[0]}: QUICK suites ⊆ FULL suites ({len(p['suites'])}/{len(all_suites)})",
          set(p["suites"]) - {tm.IDENTITY_TEST} <= all_suites)
    check(f"{changed[0]}: QUICK fixtures ⊆ FULL fixtures",
          set(p["quick_fixtures"]) <= {d.name for d, _ in FIXTURES})
p1 = tt.build_plan(type("A", (), {"changed": ["agent/numeric_observation.py"], "since": None, "area": None})())
p2 = tt.build_plan(type("A", (), {"changed": ["agent/numeric_observation.py", "agent/precision_rail.py"],
                                   "since": None, "area": None})())
check("monotone: adding a changed module never drops a suite", set(p1["suites"]) <= set(p2["suites"]))
check("monotone: adding a changed module never drops a selected turn",
      all(set(p1["targeted"][k]["selected"]) <= set(p2["targeted"][k]["selected"]) for k in p1["targeted"]))
p3 = tt.build_plan(type("A", (), {"changed": ["agent/response_pipeline.py"], "since": None, "area": None})())
check("pipeline change escalates TARGETED to every archived turn",
      p3["escalated"] and all(t["selected"] == t["all"] for t in p3["targeted"].values()))
p4 = tt.build_plan(type("A", (), {"changed": ["agent/brand_new_module.py"], "since": None, "area": None})())
check("unmapped agent module fails closed (escalated, all turns)", p4["escalated"])
p5 = tt.build_plan(type("A", (), {"changed": ["docs/README.md"], "since": None, "area": None})())
check("docs-only change selects nothing and requires no LIVE",
      p5["suites"] == [] and p5["predicates"] == [] and not p5["live_required"])
# reverse dependency: a value_transaction change reaches the rail area
p6 = tt.build_plan(type("A", (), {"changed": ["agent/value_transaction.py"], "since": None, "area": None})())
check("reverse dependency: value_transaction change selects the rail turns",
      "rail" in p6["areas"] and sel[tt.rel(rail)]["all"] == p6["targeted"][tt.rel(rail)]["selected"])

# ---------------------------------------------------------------------------
print("== E. FULL gate preserved ==")
src = (ROOT / "phase5" / "harness" / "test_tiers.py").read_text(encoding="utf-8")
check("FULL runs the standing replay command verbatim", "str(ROOT / tm.REPLAY_GATE), tm.REPLAY_GLOB" in src)
check("standing glob unchanged", tm.REPLAY_GLOB == "phase5/harness/fixtures/*/session_*.log")
check("FULL runs every suite (all_suites) — not a selection", "run_suites(tm.all_suites()" in src)
sessions = {tt.rel(s): None for s in SESSIONS}
prof = tt.structured_profile(sessions)
accepted = tt.load_accepted()
check("accepted divergence profile file present", accepted is not None)
check("current replay profile == accepted standing profile", tt.compare_profile(prof, accepted) == [])
base_key = next(k for k in prof if "baseline_20260830_1" in k)
check("standing profile = t1 + t20 hard, t11 note (documented boundaries)",
      sorted(prof[base_key]["hard"]) == ["1", "20"] and sorted(prof[base_key]["notes"]) == ["11"])
check("no other fixture has a hard divergence",
      all(not v["hard"] for k, v in prof.items() if k != base_key))
# negative control: a NEW divergence must be reported
bad = copy.deepcopy(prof)
bad[base_key]["hard"]["7"] = {"route_action": ["clarify", "normal"]}
check("negative control: a new hard divergence fails the profile check",
      any("t7" in p and "NEW" in p for p in tt.compare_profile(bad, accepted)))
gone = copy.deepcopy(prof)
gone[base_key]["hard"].pop("20")
check("negative control: a vanished divergence also fails (re-pin must be deliberate)",
      any("t20" in p and "VANISHED" in p for p in tt.compare_profile(gone, accepted)))
scoped = {base_key: dict(prof[base_key], stop_after=5)}
scoped[base_key]["hard"] = {tn: v for tn, v in prof[base_key]["hard"].items() if int(tn) <= 5}
scoped[base_key]["notes"] = {}
check("prefix replay compares the profile only inside the replayed prefix",
      tt.compare_profile(scoped, accepted) == [])
check("replay.py still gates the numeric observation (pure view) when archived",
      "obs_pure_view(archived.get(\"numeric_observation\"))" in replay_src)
check("replay.py _compare untouched by the tiers (curated field list intact)",
      all(f'"{k}"' in replay_src for k in ("route_action", "precise_detail", "detail_state", "control_shadow",
                                           "reply_trimmed", "heard_text", "remaining_text")))

# ---------------------------------------------------------------------------
print("== F. LIVE tier ==")
rp = tm.replay_path_modules()
offline_impossible = {"agent/main.py", "providers/vad.py", "providers/stt_router.py", "providers/tts.py",
                      "providers/llm.py", "agent/session.py", "agent/token_server.py"}
check("modules that cannot run offline are declared live_path", offline_impossible <= tm.LIVE_PATH_MODULES)
check("agent/main.py is NOT on the replay path (only source-pinned)", "agent/main.py" not in rp)
pl = tt.build_plan(type("A", (), {"changed": ["agent/main.py"], "since": None, "area": None})())
check("a main.py change flags LIVE required", pl["live_required"])
check("...and still runs the source-pin suites in QUICK",
      {"phase5/tests/test_numeric_chain.py", "phase5/tests/test_delivery_gate.py",
       "phase5/tests/test_response_supersession.py"} <= set(pl["suites"]))
pr = tt.build_plan(type("A", (), {"changed": ["agent/precision_rail.py"], "since": None, "area": None})())
check("a rail change does not require LIVE", not pr["live_required"])
mapped = {m for a in tm.AREAS.values() for m in a["modules"]}
code = {str(p.relative_to(ROOT)).replace(os.sep, "/") for d in ("agent", "providers")
        for p in (ROOT / d).glob("*.py") if p.name != "__init__.py"}
check(f"every agent/providers module is mapped to an area ({len(code)} modules)", sorted(code - mapped), [])
check("every replay-path module is in an area with turn predicates or escalation",
      all(tm.AREAS[tm.area_of(m)]["turns"] or tm.area_of(m) in tm.ESCALATE_TO_FULL or m in tm.LIVE_PATH_MODULES
          for m in rp if tm.area_of(m)))

# ---------------------------------------------------------------------------
print("== G. no product logic in the tier modules ==")
for mod in ("phase5/harness/frozen_inputs.py", "phase5/harness/tiers_manifest.py", "phase5/harness/test_tiers.py"):
    tree = ast.parse((ROOT / mod).read_text(encoding="utf-8"))
    top_imports = set()
    for n in tree.body:
        if isinstance(n, ast.Import):
            top_imports |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            top_imports.add(n.module)
    check(f"{mod}: no agent/providers import at module import time",
          not any(m.split(".")[0] in ("agent", "providers") for m in top_imports))
check("agent code never imports the tier modules",
      not any("tiers_manifest" in (ROOT / d / f).read_text(encoding="utf-8")
              or "frozen_inputs" in (ROOT / d / f).read_text(encoding="utf-8")
              for d in ("agent", "providers") for f in os.listdir(ROOT / d) if f.endswith(".py")))

# ---------------------------------------------------------------------------
print("== H. echo-dropped turns are a documented SKIP of the gate (never a false divergence) ==")
# main.py's TEXT echo filter drops a turn BEFORE the pipeline (no route / rail
# decision is archived — only the observation + echo evidence). The offline
# gate cannot reproduce a decision that was never made: such a turn must be
# skipped exactly like the turn-controller WAIT, not compared against an
# empty archive (which would report route_action/response_state divergences
# that have nothing to do with the rules). First real case: owner session
# 102221 t15 ('दिख रहा है?' eaten by the text filter, corr 0.264).
from agent.numeric_observation import build_record as _build_record  # noqa: E402


def _ghost(tn, text):
    return {"turn": tn, "stt_transcript": text, "stt_valid": True, "stt_language": "hi",
            "stt_provider": "groq", "agent_was_speaking": True, "ms_since_agent_audio_end": 210,
            "echo_shadow": {"corr": 0.26, "text_sim": 0.69, "text_echo": True, "decision": "dropped_echo"},
            "echo_dropped": True,
            "numeric_observation": _build_record(text, tn, source={"provider": "groq"})}


base_diffs, base_checked, base_skipped = replay_session(rail)
ghosted = copy.deepcopy(turns)
_i9 = next(i for i, t in enumerate(ghosted) if t["turn"] == 9)
ghosted.insert(_i9 + 1, _ghost(9, "ठीक है"))                       # confirm word eaten mid-session
ghosted.append(_ghost(ghosted[-1]["turn"] + 1, "हाँ ठीक है 026900"))  # digit-bearing eaten turn at the end
ghost_dir = tmpdir / "ghosted"
ghost_dir.mkdir()
ghost_path = ghost_dir / rail.name
ghost_path.write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in ghosted), encoding="utf-8")
g_diffs, g_checked, g_skipped = replay_session(ghost_path)
check("echo-dropped turns add NO divergence (same diffs as the untouched fixture)",
      json.dumps(g_diffs, sort_keys=True, default=str) == json.dumps(base_diffs, sort_keys=True, default=str))
check("echo-dropped turns are counted as skipped, not checked",
      (g_checked, g_skipped) == (base_checked, base_skipped + 2))
check("the carrier state still reaches the turns after the eaten one (identical downstream replay)",
      all(tn not in g_diffs for tn in (10, 11, 12)))
check("echo_dropped is a frozen input key (the skip predicate is visible to the tiers)",
      "echo_dropped" in fz.INPUT_KEYS)
check("an eaten turn still carries its observation for the E1 audit (not consumed by the gate)",
      ghosted[_i9 + 1]["numeric_observation"]["certainty"] == "EMPTY"
      and ghosted[-1]["numeric_observation"]["certainty"] == "COMPLETE")

shutil.rmtree(tmpdir, ignore_errors=True)
print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("ALL PASS")
