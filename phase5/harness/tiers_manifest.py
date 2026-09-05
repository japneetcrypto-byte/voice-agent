#!/usr/bin/env python3
"""Tier manifest — WHAT each test tier consists of, as data (2026-09-05).

Four tiers (owner definition):

  QUICK     unit / property / adversarial suites whose import closure touches
            the changed modules + the DIRECTLY affected rail fixture(s).
  TARGETED  the same suites + replay of only the affected turns/scenarios of
            every archived fixture (prefix replay through the last affected
            turn — the carrier is threaded turn to turn, so a mid-file start
            is impossible) + the replay self-consistency test.
  FULL      every suite + the COMPLETE archive replay (phase5/harness/replay.py
            over all fixtures — the standing acceptance / merge gate, never
            removed or weakened) + frozen-input verification + accepted
            divergence profile check.
  LIVE      audio -> STT -> pipeline on the deployed worker. Required only
            when the LIVE_PATH modules change; it cannot run offline. Its
            output (logs/session_*.log) is what gets FROZEN into a fixture.

Everything here is derived from two things and nothing else:
  1. the code graph — a suite is selected when its transitive local import
     closure (AST) or a literal source pin (".../agent/main.py") touches a
     changed module; the replay path is the closure of agent.response_pipeline
  2. the FROZEN archive — turn selection is a predicate over the frozen
     projection of each archived turn (frozen_inputs.FROZEN_KEYS), so a
     targeted replay is provably a subset of the full replay's inputs.

No product logic lives here. stdlib only.
"""
from __future__ import annotations

import ast
import os
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = ROOT / "phase5" / "tests"
FIXTURES_DIR = ROOT / "phase5" / "harness" / "fixtures"
HARNESS_DIR = ROOT / "phase5" / "harness"

REPLAY_GATE = "phase5/harness/replay.py"
REPLAY_GLOB = "phase5/harness/fixtures/*/session_*.log"
IDENTITY_TEST = "phase5/harness/test_replay_identity.py"

# Suites that regenerate a committed fixture when they run (deterministic;
# frozen verification after FULL proves the regeneration is byte-identical).
FIXTURE_WRITERS = {
    "phase5/tests/test_session_103339_trace.py": "session_103339_rail",
    IDENTITY_TEST: "synthetic_slice1",
}

# ---------------------------------------------------------------------------
# Turn predicates over the FROZEN projection of an archived turn
# ---------------------------------------------------------------------------
_DIGIT_RE = re.compile(r"[0-9\u0966-\u096f]")


def _p_rail(t: dict) -> bool:
    return t.get("engine_path") == "precision_rail" or "precise_detail" in t


def _p_observation(t: dict) -> bool:
    """Digit-bearing turns. Archived observation certainty when the fixture
    carries it; otherwise the pure observe() over the frozen transcript
    (covers Devanagari digit words the regex cannot); regex as last resort.
    Selection only — never an input to any decision."""
    obs = t.get("numeric_observation")
    if isinstance(obs, dict) and obs.get("certainty"):
        return obs.get("certainty") != "EMPTY"
    text = t.get("stt_transcript") or ""
    try:
        from agent.numeric_observation import observe, EMPTY  # lazy: agent code
        return observe(text, int(t.get("turn") or 0)).get("certainty") != EMPTY
    except Exception:
        return bool(_DIGIT_RE.search(text))


def _p_routing(t: dict) -> bool:
    return (t.get("route_action") not in (None, "normal") or bool(t.get("dropped_reason"))
            or t.get("stt_valid") is False or t.get("turn_type") not in (None, "speech"))


def _p_delivery(t: dict) -> bool:
    return bool(t.get("interrupted") or t.get("response_state") not in (None, "FULLY_PLAYED")
                or t.get("reply_trimmed") or t.get("repeat_guarded")
                or t.get("contract_block_count") or t.get("response_suppressed")
                or t.get("script_transliterated") or t.get("tag_leak_stripped"))


def _p_detail(t: dict) -> bool:
    return bool(t.get("detail_mode") or t.get("detail_state") or t.get("head_plan")
                or t.get("challenge_detected"))


def _p_greeting(t: dict) -> bool:
    return t.get("engine_path") == "greeting"


def _p_fused(t: dict) -> bool:
    return t.get("engine_path") == "fused"


def _p_echo(t: dict) -> bool:
    shadow = t.get("echo_shadow") if isinstance(t.get("echo_shadow"), dict) else {}
    return bool(t.get("echo_dropped") or shadow.get("text_echo"))


def _p_all(t: dict) -> bool:
    return True


TURN_PREDICATES = {
    "rail": _p_rail,
    "observation": _p_observation,
    "routing": _p_routing,
    "delivery": _p_delivery,
    "detail": _p_detail,
    "greeting": _p_greeting,
    "fused": _p_fused,
    "echo": _p_echo,
    "all": _p_all,
}

# ---------------------------------------------------------------------------
# Change areas: modules -> which turns are "affected" -> which fixtures QUICK
# replays directly -> whether LIVE is required
# ---------------------------------------------------------------------------
AREAS: dict[str, dict] = {
    "observation": {
        "modules": ["agent/numeric_observation.py", "agent/numeric_chain.py"],
        "turns": ["observation"],
        "quick_fixtures": ["session_103339_rail"],
        "why": "NumericObservation record + audit chain (Phase 1); compared on every "
               "archived turn that carries the key, behaviour-relevant on digit turns",
    },
    "rail": {
        "modules": ["agent/precision_rail.py", "agent/value_transaction.py",
                    "agent/conversation_controller.py", "agent/control_plane.py"],
        "turns": ["rail"],
        "quick_fixtures": ["session_103339_rail"],
        "why": "dictation rail / L1-L6 value transaction / controller table",
    },
    "routing": {
        "modules": ["agent/turn_router.py", "agent/transcript_router.py",
                    "agent/stt_validation.py", "providers/segment_metrics.py"],
        "turns": ["routing"],
        "quick_fixtures": [],
        "why": "accept / drop / clarify / recovery decisions from the frozen STT evidence",
    },
    "delivery": {
        "modules": ["agent/response_state.py", "agent/response_supersession.py",
                    "agent/reply_guard.py", "agent/response_contract.py",
                    "agent/turn_controller.py", "agent/prompt_fragments.py"],
        "turns": ["delivery", "detail", "greeting"],
        "quick_fixtures": [],
        "why": "piece stream / guards / caps / completion halves / greeting rail",
    },
    "policy": {
        "modules": ["agent/fused_turn.py", "agent/state_updater.py", "agent/entity_extractor.py"],
        "turns": ["fused"],
        "quick_fixtures": [],
        "why": "policy deltas of fused turns (base policy values are runtime state)",
    },
    "pipeline": {
        "modules": ["agent/response_pipeline.py", "agent/run_turn.py"],
        "turns": ["all"],
        "quick_fixtures": ["session_103339_rail"],
        "why": "the §6 interface itself — every archived turn is affected",
    },
    "harness": {
        "modules": ["phase5/harness/replay.py", "phase5/harness/frozen_inputs.py",
                    "phase5/harness/tiers_manifest.py", "phase5/harness/test_tiers.py",
                    "phase5/harness/project_session_log.py"],
        "turns": ["all"],
        "quick_fixtures": ["session_103339_rail"],
        "why": "the gate changed — only the complete gate proves the gate",
    },
    "live_path": {
        "modules": ["agent/main.py", "providers/stt.py", "providers/stt_gemini_live.py",
                    "providers/stt_router.py", "providers/vad.py", "providers/endpointing.py",
                    "providers/speaker_signature.py", "providers/tts.py", "providers/llm.py",
                    "agent/session.py", "agent/token_server.py", "agent/tts_warmup.py",
                    "agent/ack_bridge.py", "agent/call_supervisor.py"],
        "turns": [],
        "quick_fixtures": [],
        "live_required": True,
        "why": "audio / STT / VAD / TTS / worker wiring — not reproducible offline; "
               "source-pin suites still run in QUICK",
    },
    "memory": {
        "modules": ["agent/memory_store.py", "agent/memory_units.py", "agent/memory_gate.py",
                    "agent/session_consolidation.py", "agent/layered_context.py",
                    "agent/session_state.py", "agent/disclosure_capture.py",
                    "agent/state_delta_compiler.py", "agent/config.py"],
        "turns": [],
        "quick_fixtures": [],
        "why": "off the replay path (not in the closure of agent.response_pipeline)",
    },
}

LIVE_PATH_MODULES = set(AREAS["live_path"]["modules"])

# Escalation: a change in these areas makes TARGETED == FULL.
ESCALATE_TO_FULL = {"pipeline", "harness"}


def affected_areas(changed_modules: set[str]) -> tuple[set[str], list[str]]:
    """Areas whose replay-path code can execute changed code = the areas of
    the changed modules themselves + the areas of every replay-path module
    whose import closure contains a changed module (reverse dependency,
    e.g. precision_rail -> value_transaction / numeric_observation).
    The pipeline root and the harness are excluded from PROPAGATION (they
    import everything; only a direct change to them escalates).
    Returns (areas, escalation_reasons)."""
    areas: set[str] = set()
    reasons: list[str] = []
    changed = {m.replace(os.sep, "/") for m in changed_modules}
    rp = replay_path_modules()
    for m in changed:
        a = area_of(m)
        if a:
            areas.add(a)
            if m in rp and not AREAS[a]["turns"] and a not in ESCALATE_TO_FULL:
                reasons.append(f"{m} is on the replay path but area '{a}' declares no turn "
                               f"predicate -> every archived turn")
    for r in rp:
        a = area_of(r)
        if not a or a in ESCALATE_TO_FULL or r in changed:
            continue
        if changed & (closure(r) - {r}):
            areas.add(a)
    return areas, reasons


def area_of(path: str) -> str | None:
    p = path.replace(os.sep, "/")
    for name, spec in AREAS.items():
        if p in spec["modules"]:
            return name
    return None


def classify_change(paths: list[str]) -> dict:
    """Map changed repo paths -> areas / unmapped code / tests / docs."""
    out = {"areas": set(), "unmapped_code": [], "tests": [], "fixtures": [],
           "docs_or_other": [], "live_required": False}
    for raw in paths:
        p = raw.replace(os.sep, "/")
        a = area_of(p)
        if a:
            out["areas"].add(a)
            if AREAS[a].get("live_required"):
                out["live_required"] = True
        elif p.startswith("phase5/tests/") and p.endswith(".py"):
            out["tests"].append(p)
        elif p.startswith("phase5/harness/fixtures/"):
            out["fixtures"].append(p)
        elif p.endswith(".py") and (p.startswith("agent/") or p.startswith("providers/")):
            out["unmapped_code"].append(p)   # fail-closed: treated as pipeline
        else:
            out["docs_or_other"].append(p)
    if out["unmapped_code"]:
        out["areas"].add("pipeline")
    if out["fixtures"]:
        out["areas"].add("harness")
    out["areas"] = sorted(out["areas"])
    return out


# ---------------------------------------------------------------------------
# Code graph (AST): local import closure + literal source pins
# ---------------------------------------------------------------------------
_LOCAL_PKGS = ("agent", "providers", "phase5", "phase4", "phase3", "prompts")


def module_to_path(mod: str) -> str | None:
    p = mod.replace(".", "/") + ".py"
    if (ROOT / p).is_file():
        return p
    q = mod.replace(".", "/") + "/__init__.py"
    if (ROOT / q).is_file():
        return q
    return None


def direct_imports(path: str) -> set[str]:
    try:
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    except (SyntaxError, OSError, UnicodeDecodeError):
        return set()
    mods: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                mods.add(a.name)
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module)
    return {m for m in mods if m.split(".")[0] in _LOCAL_PKGS}


_PIN_RE = re.compile(r"[\"']([A-Za-z0-9_]+\.py)[\"']")


def source_pins(path: str) -> set[str]:
    """Repo modules a test reads as TEXT (source-inspection pins such as
    open(os.path.join(ROOT, "agent", "main.py"))). Matched by basename."""
    try:
        src = (ROOT / path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    names = set(_PIN_RE.findall(src))
    out: set[str] = set()
    if not names:
        return out
    for sub in ("agent", "providers", "phase5", "phase5/harness"):
        d = ROOT / sub
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.suffix == ".py" and f.name in names:
                out.add(str(f.relative_to(ROOT)).replace(os.sep, "/"))
    return out


@lru_cache(maxsize=None)
def _closure_frozen(path: str) -> frozenset[str]:
    seen: set[str] = set()
    stack = [path]
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        for m in direct_imports(p):
            mp = module_to_path(m)
            if mp and mp not in seen:
                stack.append(mp)
    return frozenset(seen)


def closure(path: str) -> set[str]:
    """Transitive local import closure of one file, as repo-relative paths
    (the file itself included). Memoised per process (the code graph does
    not change while a tier runs)."""
    return set(_closure_frozen(path))


def replay_path_modules() -> set[str]:
    """Modules the deterministic replay path can execute = closure of the
    §6 interface (what run_turn imports)."""
    return closure("agent/response_pipeline.py")


def all_suites() -> list[str]:
    return sorted(str(p.relative_to(ROOT)).replace(os.sep, "/")
                  for p in TESTS_DIR.glob("test_*.py"))


@lru_cache(maxsize=None)
def _touch_map_frozen() -> tuple[tuple[str, frozenset[str]], ...]:
    out = []
    for s in all_suites() + [IDENTITY_TEST]:
        out.append((s, frozenset((closure(s) | source_pins(s)) - {s})))
    return tuple(out)


def suite_touch_map() -> dict[str, set[str]]:
    """suite -> set of repo modules it exercises (closure ∪ source pins)."""
    return {s: set(m) for s, m in _touch_map_frozen()}


def suites_for(changed_modules: set[str], touch: dict[str, set[str]] | None = None) -> list[str]:
    touch = touch or suite_touch_map()
    picked = []
    for s, mods in touch.items():
        if mods & changed_modules or s in changed_modules:
            picked.append(s)
    return sorted(picked)


# ---------------------------------------------------------------------------
# Fixture discovery
# ---------------------------------------------------------------------------
def fixture_dirs() -> list[Path]:
    return sorted(p for p in FIXTURES_DIR.iterdir() if p.is_dir())


def fixture_sessions(d: Path) -> list[Path]:
    return sorted(d.glob("session_*.log"))


def select_turns(turns: list[dict], predicates: list[str]) -> list[int]:
    """Turn numbers (from the frozen projection) matched by ANY predicate."""
    preds = [TURN_PREDICATES[p] for p in predicates]
    out = []
    for t in turns:
        try:
            tn = int(t.get("turn"))
        except (TypeError, ValueError):
            continue
        if any(p(t) for p in preds):
            out.append(tn)
    return out
