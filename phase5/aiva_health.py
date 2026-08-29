#!/usr/bin/env python3
"""ONE-COMMAND session health check — owner brief 2026-08-29.

  python3 phase5/aiva_health.py           # latest session
  python3 phase5/aiva_health.py --all     # all sessions + full shadow data

Runs every diagnostic we have and assembles ONE markdown file to share:

  logs/health_<session>.md

Contents: identity/memory binding, stage diagnostic (per-turn), TTS voice
audit, self-diagnosis (failure classes + WHY), voice-key shadow calibration,
supervisor/safety events, memory store summary, latency, config fingerprint.

Deterministic orchestration only — each section is the existing tool's own
output (single source of truth), captured verbatim.
"""
import json, glob, os, sys, sqlite3, subprocess
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
LOGS = "logs"

all_mode = "--all" in sys.argv
pos = [a for a in sys.argv[1:] if not a.startswith("--")]
if pos:
    sp = pos[0]
else:
    cands = sorted(glob.glob(f"{LOGS}/session_*.log"), key=os.path.getmtime)
    if not cands:
        print("NO SESSION LOGS FOUND"); sys.exit(1)
    sp = cands[-1]
sess = os.path.basename(sp).replace("session_", "").replace(".log", "")

def run(cmd, timeout=90):
    """Run a diagnostic script (prefixed with this python), return (ok, output)."""
    return _run([sys.executable] + cmd, timeout)

def _run(cmd, timeout=90):
    """Run a raw command list."""
    try:
        r = subprocess.run(cmd, capture_output=True,
                           text=True, timeout=timeout, cwd=ROOT)
        out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr.strip() else "")
        return r.returncode == 0, out.strip() or "(no output)"
    except Exception as e:
        return False, f"(failed to run: {type(e).__name__}: {e})"

def section(title, body, ok=True):
    flag = "" if ok else " ⚠ (section failed — see below)"
    return f"\n## {title}{flag}\n\n```\n{body}\n```\n"

L = []
L.append(f"# AIVA SESSION HEALTH — {sess}")
L.append(f"_generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · "
         f"mode: {'ALL sessions' if all_mode else 'latest session'}_\n")

# ---- config fingerprint ----
cfg = {}
try:
    if os.path.exists(".env"):
        for line in open(".env"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
except Exception:
    pass
commit = _run(["git", "log", "-1", "--format=%h %s"], timeout=15)[1]
L.append("## Config fingerprint\n")
L.append(f"- code: `{commit}`")
L.append(f"- AIVA_STT_PRIMARY: `{cfg.get('AIVA_STT_PRIMARY', '(default: gemini_live!)')}` · "
         f"AIVA_STT_LANGUAGE: `{cfg.get('AIVA_STT_LANGUAGE', '(default hi)')}`")
L.append(f"- AIVA_STATE_ENGINE: `{cfg.get('AIVA_STATE_ENGINE', '1')}` · "
         f"AIVA_TTS_DUMP: `{cfg.get('AIVA_TTS_DUMP', 'not set — voice audit unavailable')}` · "
         f"AIVA_ALERT_WEBHOOK: `{'set' if cfg.get('AIVA_ALERT_WEBHOOK') else 'not set'}`")
L.append(f"- prompt: TRANSPORT version per turn in log; model pool: gemini flash-lite × keys\n")

# ---- identity + memory binding ----
L.append("## Identity & memory binding\n")
ep = f"{LOGS}/events_{sess}.log"
bound = []
events = []
for ef in (glob.glob(f"{LOGS}/events_*.log") if all_mode else [ep]):
    if not os.path.exists(ef):
        continue
    for line in open(ef):
        try:
            e = json.loads(line)
        except Exception:
            continue
        events.append(e)
        if e.get("event") == "SESSION_BOUND":
            bound.append(e)
if bound:
    for b in bound:
        d = b.get("details", b)
        L.append(f"- ✅ SESSION BOUND owner=`{d.get('owner', '?')}` memory_items={d.get('memory_items', '?')}")
else:
    L.append("- ❌ NO `SESSION BOUND` event — the state engine brain did NOT bind this session. "
             "Do not evaluate conversation quality; investigate the worker console first.")
sup = [e for e in events if e.get("event") == "SUPERVISOR_ENGAGED"]
esc = [e for e in events if e.get("event") == "SUPERVISOR_ESCALATE"]
skips = [e for e in events if e.get("event") == "RESPONSE_SKIPPED"]
echo_ev = [e for e in events if str(e.get("event", "")).startswith("ECHO_")]
n429 = [e for e in events if "429" in json.dumps(e)]
L.append(f"- supervisor: engaged ×{len(sup)}, escalated ×{len(esc)}"
         + (f" (turns {[e.get('turn_id') for e in sup][:6]})" if sup else ""))
L.append(f"- response skips ×{len(skips)} · echo-shadow events ×{len(echo_ev)} · 429 fingerprints ×{len(n429)}")
L.append("")

# ---- memory store summary ----
db = f"{LOGS}/aiva_memory.db"
if os.path.exists(db):
    try:
        con = sqlite3.connect(db)
        rows = con.execute("SELECT owner_id, status, COUNT(*) FROM memory GROUP BY owner_id, status").fetchall()
        recent = con.execute("SELECT type, content, status FROM memory ORDER BY last_seen DESC LIMIT 8").fetchall()
        con.close()
        L.append("## Memory store\n")
        for owner, status, n in rows:
            L.append(f"- `{owner[:8]}`… {status}: {n} items")
        L.append("")
        L.append("Recent items:")
        for typ, content, status in recent:
            L.append(f"  - [{status}] {typ}: {content[:70]}")
        L.append("")
    except Exception as e:
        L.append(f"## Memory store\n\n(read failed: {e})\n")

# ---- STAGE VERDICT (the owner decision rule, top of the report) ----
ok, out = run(["phase5/stage_verdict.py", sp])
L.append(section("STAGE VERDICT — per-stage proof + final call", out, ok))

# ---- full diagnostics (verbatim from their own tools) ----
ok, out = run(["phase5/stage_diagnostic.py", sp])
L.append(section(f"Stage diagnostic — {os.path.basename(sp)}", out, ok))

audit_file = f"{LOGS}/tts/manifest.jsonl"
if os.path.exists(audit_file):
    ok, out = run(["phase5/tts_audit.py"])
    L.append(section("TTS voice audit (spoken output)", out, ok))
else:
    L.append("## TTS voice audit\n\nSKIPPED — no logs/tts/manifest.jsonl. "
             "Set `AIVA_TTS_DUMP=1` in .env and restart; then this section auto-appears.\n")

ok, out = run(["phase5/self_diagnose.py", sp])
L.append(section("Self-diagnosis (failure classes + WHY)", out, ok))

ok, out = run(["phase5/echo_shadow_report.py"] + (["--latest"] if not all_mode else []))
L.append(section("Voice-key shadow calibration (speaker attribution stage 1)", out, ok))

# ---- lifecycle telemetry summary, if present ----
tl = sorted(glob.glob(f"{LOGS}/turn_lifecycle_*.jsonl"), key=os.path.getmtime)
if tl:
    summ = None
    for line in open(tl[-1]):
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("ev") == "SESSION_SUMMARY":
            summ = e
    if summ:
        L.append(section("Barge-in / endpointing telemetry", json.dumps(summ, indent=1)))

# ---- write + report ----
out_path = f"{LOGS}/health_{sess}.md"
with open(out_path, "w") as f:
    f.write("\n".join(L) + "\n")

# compact console verdict
nflags = sum(L.count(marker) for marker in ("❌", "⚠"))
print(f"HEALTH REPORT WRITTEN: {out_path}")
print(f"share this file — it contains identity, per-turn detail, voice audit, "
      f"self-diagnosis, voice-key calibration, memory, telemetry")
if bound:
    print(f"quick verdict: brain BOUND ({bound[-1].get('details', {}).get('owner', '?')[:8]}), "
          f"supervisor ×{len(sup)}, skips ×{len(skips)}, 429 ×{len(n429)}")
else:
    print("quick verdict: ❌ brain NOT BOUND — check worker console for the binding error")
