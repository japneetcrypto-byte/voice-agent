#!/usr/bin/env python3
"""Stage-1 shadow calibration report — echo-correlation voice key.

Owner brief 2026-08-29 (speaker attribution): Stage 1 collects acoustic echo
scores in shadow mode; THIS tool is the gate check to Stage 2 (gate activation).

  python3 phase5/echo_shadow_report.py          # across all sessions
  python3 phase5/echo_shadow_report.py --latest # latest session only

Reads turn["echo_corr_score"] from session logs (split by what the text-level
echo filter did) + the shadow events. Applies the locked calibration logic:
  - real-speech floor: p90/p95 of scores on turns the text filter KEPT
  - true-echo band: distribution of scores on turns the text filter DROPPED
  - separable iff echo band sits clearly above the speech floor
Deterministic, stdlib-only.
"""
import json, glob, os, sys
from collections import Counter

latest = "--latest" in sys.argv
pattern = "logs/session_*.log"
files = sorted(glob.glob(pattern), key=os.path.getmtime)
if latest and files:
    files = files[-1:]
if not files:
    print("NO SESSION LOGS FOUND"); sys.exit(1)

echo_scores, keep_scores = [], []       # by text-filter verdict
corr_only_hits, text_only_hits, agree_hits = 0, 0, 0
sessions_with_data = set()
none_reason = Counter()

for sp in files:
    sess = os.path.basename(sp).replace("session_", "").replace(".log", "")
    for line in open(sp):
        try:
            t = json.loads(line)
        except Exception:
            continue
        if not t.get("turn"):
            continue
        s = t.get("echo_corr_score")
        if s is None:
            continue
        sessions_with_data.add(sess)
        if t.get("echo_dropped"):
            echo_scores.append((s, sess, t.get("turn")))
        else:
            keep_scores.append((s, sess, t.get("turn")))

for ep in glob.glob("logs/events_*.log"):
    for line in open(ep):
        if "ECHO_MULTI_AGREE" in line:
            agree_hits += 1
        elif "ECHO_TEXT_ONLY" in line:
            text_only_hits += 1
        elif "ECHO_CORR_ONLY" in line:
            corr_only_hits += 1

def stats(xs):
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    def pct(p):
        return s[min(n - 1, int(n * p))]
    return {"n": n, "min": s[0], "p25": pct(0.25), "med": pct(0.5),
            "p75": pct(0.75), "p90": pct(0.90), "p95": pct(0.95), "max": s[-1]}

print(f"=== ECHO SHADOW REPORT — {len(files)} session file(s), "
      f"{len(sessions_with_data)} with corr data ===\n")

S_keep = stats([s for s, *_ in keep_scores])
S_echo = stats([s for s, *_ in echo_scores])
print(f"KEPT turns  (text filter said real speech): {S_keep}")
print(f"DROPPED turns (text filter said echo)    : {S_echo}\n")
print(f"shadow events: agree={agree_hits}  text_only(eaten-user suspects)={text_only_hits}  "
      f"corr_only(missed echoes)={corr_only_hits}\n")

if S_keep is None or S_keep["n"] < 30:
    print(f"VERDICT: KEEP SHADOWING — need ≥30 scored KEEP turns for a stable speech "
          f"floor (have {S_keep['n'] if S_keep else 0}). Have a few more normal sessions.")
    sys.exit(0)

# Calibration (locked rule): speech floor = p95 of KEPT scores. Gate candidate:
#   drop  when corr >= T_high (echo band's low end, if separable)
#   keep  when corr <= T_low  (= speech p95)
#   between -> defer to the text filter (tie goes to the user)
t_low = round(S_keep["p95"], 3)
if S_echo is None or S_echo["n"] < 8:
    print(f"VERDICT: KEEP SHADOWING — speech floor ready (p95={t_low}) but only "
          f"{S_echo['n'] if S_echo else 0} true-echo samples (need ≥8). Talk right after "
          "Aiva speaks a few times to generate echo candidates, then rerun.")
    sys.exit(0)

t_high = round(max(S_echo["p25"], t_low + 0.03), 3)
separable = S_echo["p25"] > t_low and S_echo["min"] > S_keep["med"]
print("CALIBRATION:")
print(f"  speech floor (keep if below)      : T_low  = {t_low}")
print(f"  echo band (drop if above)         : T_high = {t_high}")
print(f"  echo p25 vs speech p95            : {S_echo['p25']} vs {t_low}")
print(f"  ambiguous zone                    : {t_low} – {t_high} → defer to text filter")
if separable:
    print("\nVERDICT: SEPARABLE ✅ — ready for Stage 2 (gate activation) on owner sign-off.")
    print("  Proposed rule: corr >= T_high → drop without STT echo check; "
          "corr <= T_low → keep regardless; between → text filter decides.")
else:
    print("\nVERDICT: KEEP SHADOWING — bands overlap so far; the acoustic gate would "
          "risk eating real speech. More sessions (and real-echo candidates) needed.")
