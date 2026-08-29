# Aiva — Live Conversation Test Runbook

## Setup — 3 terminals (all from repo root)

**Terminal 1 — token server:**
```bash
cd "/Users/japneetsingh/Desktop/Voice Agent/voice-agent"
uv run python -m agent.token_server
```

**Terminal 2 — the worker (the brain):**
```bash
cd "/Users/japneetsingh/Desktop/Voice Agent/voice-agent"
AIVA_STATE_ENGINE=1 WORKER_TARGET=cloud uv run python -m agent.main start
```
(If port errors: `lsof -ti:3001 | xargs kill -9; lsof -ti:8081 | xargs kill -9` and retry.)

**Terminal 3 — frontend:**
```bash
cd "/Users/japneetsingh/Desktop/Voice Agent/voice-agent/frontend"
npm run dev
```

Open **http://localhost:5173** → Start Conversation.

## The conversation script (test ALL of these)

| # | Do this | Expect |
|---|---|---|
| 1 | Just say **"Hello, kya kar rahe ho?"** | Simple greeting, NO old-session references |
| 2 | **Vent in Hindi/Hinglish for 30–60s continuously** — pause mid-thought, resume; say "अच्छा... अच्छा... ठीक है..." | Agent stays SILENT during pauses/stories; replies only at real turn ends |
| 3 | Say a fragment: **"मामला ये है कि..."** then pause, continue | Silent between fragments; final reply sees the whole story |
| 4 | Say just **"haan"** or **"achha"** alone | 1–3 word acknowledgment or silence |
| 5 | Say **"chup yaar, pehle meri baat sun"** | One short line, then quiet |
| 6 | **Interrupt Aiva mid-sentence** | Stops immediately; your speech gets priority |
| 7 | Ask **"Are you a boy or a girl?"** | Honest, gentle answer |
| 8 | Stay silent ~45 seconds | One open-door line, then quiet |
| 9 | Emotionally heavy content (frustration with a person, by name) | Emotion-aware, specific, no therapy-speak, masculine self-reference |

## Terminal 2 lines to watch

```
[STT] session language learned: hi      ← language auto-detected
[Endpoint] turn-complete decision:...   ← why a turn ended
[TurnController] WAIT (reason)          ← pause correctly not treated as turn end
[TurnEval] turn=N lang=... rel=...      ← per-turn summary
[StateEngine] session bound to owner=.. ← memory identity bound
[TTS Fallback]                          ← ⚠️ note if seen
```

## After the conversation — collect evidence

```bash
cd "/Users/japneetsingh/Desktop/Voice Agent/voice-agent"
cat "$(ls -t logs/turn_lifecycle_*.jsonl | head -1)"
cat "$(ls -t logs/state_*.jsonl | head -1)"
cat "$(ls -t logs/session_*.log | head -1)"
python3 -c "import sqlite3;[print(r) for r in sqlite3.connect('logs/aiva_memory.db').execute('select type,content,criterion,status,occurrences from memory order by id desc limit 12')]"
python3 phase5/baseline_report.py
```

## Save everything to one file

```bash
L=$(ls -t logs/state_*.jsonl | head -1); S=$(ls -t logs/session_*.log | head -1); T=$(ls -t logs/turn_lifecycle_*.jsonl | head -1)
{ echo "=== TURN LIFECYCLE: $T ==="; cat "$T"; echo; echo "=== STATE: $L ==="; cat "$L"; echo; echo "=== SESSION: $S ==="; cat "$S"; } > run_export.txt
```

---

## Pre-flight checklist (added 2026-08-29 — after the unbound-brain incident)

1. `git pull` and confirm `git log --oneline -1` shows the incident fix.
2. `.env`: set `AIVA_STT_PRIMARY=groq` (recommended — see .env.example).
3. Start a session and watch the console for EXACTLY these lines:
   - `[StateEngine] on (persona TRANSPORT_V1.4) — components: ...`
   - `[StateEngine] SESSION BOUND owner=<uuid> memory_items=N`  ← **the brain is in**
   - `[STT Router] primary: Groq`
   If `SESSION BOUND` never appears, STOP — the session will only speak
   filler lines ([StateEngine] CRITICAL … ENGINE_UNBOUND) by design.
4. After the call: `python3 phase5/stage_diagnostic.py` — check
   `engine paths: {'fused': N}` (NOT 'legacy'/'unbound_filler'),
   `heads=N`, `ctx_captured=N`, and the flags line.
