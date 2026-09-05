#!/usr/bin/env python3
"""Memory diagnostic — READ-ONLY. Run from the repo root:  python3 mem_report.py

Answers "why does Aiva not remember across sessions?" from the files that are
already on this machine (logs/aiva_memory.db, logs/state_*.jsonl,
logs/session_*.log, logs/events_*.log, logs/worker_*.out, logs/token_server.out).

It never writes, never changes anything, needs no packages, and hides long
digit runs (numbers) so the output is safe to paste into chat.
"""
import glob
import json
import os
import re
import sqlite3

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

_DIGITS = re.compile(r"[0-9\u0966-\u096F]{4,}")


def hide(s) -> str:
    """Replace any digit run of 4+ with [digits xN] — numbers never leave the machine."""
    return _DIGITS.sub(lambda m: f"[digits x{len(m.group(0))}]", str(s))


def jl(path):
    out = []
    for line in open(path, errors="replace"):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def short(owner) -> str:
    owner = str(owner or "?")
    return owner if owner.startswith("ephemeral") else owner[:8]


print("=" * 64)
print("AIVA MEMORY REPORT (read-only)   folder:", ROOT)
print("=" * 64)

# ---- 1. the memory file itself --------------------------------------------
db = os.path.join("logs", "aiva_memory.db")
print("\n[1] MEMORY FILE  logs/aiva_memory.db")
if not os.path.exists(db):
    print("    MISSING — nothing has ever been saved from this folder.")
else:
    print(f"    exists, {os.path.getsize(db)} bytes")
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        rows = c.execute("SELECT owner_id, status, COUNT(*) FROM memory "
                         "GROUP BY 1, 2 ORDER BY 1, 2").fetchall()
    except Exception as e:
        print(f"    could not read it ({e}) — is a worker writing right now? try again.")
        rows = []
    if not rows:
        print("    table is EMPTY — nothing has ever been saved.")
    owners = sorted({r[0] for r in rows})
    print(f"    identities in the file: {len(owners)}")
    for o in owners:
        parts = ", ".join(f"{st}={n}" for (oo, st, n) in rows if oo == o)
        try:
            sess = c.execute("SELECT last_session_id FROM session_seq WHERE owner_id=?",
                             (o,)).fetchone()
            sess = sess[0] if sess else "?"
        except Exception:
            sess = "?"
        print(f"      - {short(o)}  sessions={sess}  {parts}")
    print("    (only 'committed' rows are ever recalled; 'pending' = unconfirmed, invisible;")
    print("     'quarantined' = rejected by the safety gate, invisible)")
    print("\n    last 30 saved rows (newest first):")
    for r in c.execute("SELECT id, owner_id, status, type, content, occurrences, sessions, "
                       "substr(created_at, 1, 16) FROM memory ORDER BY id DESC LIMIT 30"):
        rid, o, st, typ, content, occ, sess, ts = r
        print(f"      #{rid} {ts} owner={short(o)} {st:<11} {typ:<13} "
              f"seen={occ} sessions={sess}  {hide(content)[:70]!r}")

# ---- 2. per-session view ----------------------------------------------------
print("\n[2] SESSIONS (newest last) — who Aiva thought you were, what was loaded, "
      "what was caught, clean end?")
sessions = sorted(glob.glob(os.path.join("logs", "session_*.log")))[-12:]
if not sessions:
    print("    no logs/session_*.log files found")
seen_owners = set()
for sp in sessions:
    ts = os.path.basename(sp)[len("session_"):-len(".log")]
    turns = [t for t in jl(sp) if t.get("turn")]
    owners = {t.get("owner") for t in turns if t.get("owner")}
    seen_owners |= owners
    caught = sum(len(t.get("fact_candidates") or []) + len(t.get("place_facts") or [])
                 + len(t.get("user_relations") or []) for t in turns)
    ev_path = os.path.join("logs", f"events_{ts}.log")
    bound = consolidated = None
    if os.path.exists(ev_path):
        for e in jl(ev_path):
            if e.get("event") == "SESSION_BOUND":
                bound = e
            if e.get("event") == "SESSION_CONSOLIDATION":
                consolidated = e
    owner = bound.get("owner") if bound else (next(iter(owners)) if owners else None)
    loaded = bound.get("memory_items") if bound else "?"
    print(f"    {ts}: identity={short(owner)}  facts_loaded_at_start={loaded}  "
          f"turns={len(turns)}  facts_caught={caught}  "
          f"clean_end={'YES' if consolidated else 'NO/unknown'}")
    for t in turns:
        for k in ("fact_candidates", "place_facts"):
            for x in t.get(k) or []:
                print(f"        turn {t['turn']}: caught {hide(x)[:70]!r}")
        for x in t.get("user_relations") or []:
            print(f"        turn {t['turn']}: caught relation {hide(x)}")
if len(seen_owners) > 1:
    print(f"    !! {len(seen_owners)} DIFFERENT identities across these sessions — memory is "
          "per identity, so facts saved under one are invisible to the other.")

# ---- 3. state files: what memory looked like at each start / clean end ------
print("\n[3] STATE FILES logs/state_*.jsonl (identity, facts visible at start, clean end)")
states = sorted(glob.glob(os.path.join("logs", "state_*.jsonl")))[-12:]
if not states:
    print("    none found")
for f in states:
    start = end = None
    for d in jl(f):
        if d.get("event") == "SESSION_START":
            start = d
        elif d.get("event") == "SESSION_END":
            end = True
    o = (start or {}).get("owner_id")
    n = len((start or {}).get("memory_view") or [])
    print(f"    {os.path.basename(f)}: identity={short(o)}  facts_at_start={n}  "
          f"clean_end={'YES' if end else 'NO'}")

# ---- 4. the exact log lines that explain memory ------------------------------
print("\n[4] MEMORY LINES from logs/worker_*.out and logs/token_server.out")
pat = re.compile(r"SESSION BOUND|\[Memory\]|MemoryGate|SessionConsolidation|memory commit|"
                 r"ephemeral|EntityCapture|BIND FAILED|L2Promote|BUILD|build:")
found = 0
for f in sorted(glob.glob(os.path.join("logs", "worker_*.out"))
                + glob.glob(os.path.join("logs", "token_server.out"))):
    for line in open(f, errors="replace"):
        if pat.search(line):
            found += 1
            if found <= 80:
                print(f"    {os.path.basename(f)}: {hide(line.rstrip())[:170]}")
if not found:
    print("    none found (were the servers started with start_aiva.sh?)")
elif found > 80:
    print(f"    ... {found - 80} more lines not shown")

print("\n" + "=" * 64)
print("Paste this whole output into the chat.")
