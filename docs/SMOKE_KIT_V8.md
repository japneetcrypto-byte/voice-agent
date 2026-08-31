# SMOKE KIT — v8 clean dictation contract

Purpose: **prove the basic contract** — *dictate → capture exactly → confirm → preserve* —
on a known build, with zero ambiguity about which code the workers ran.

---

## 0. Build verification (THE gate — do this first, every time)

**Use the updated launcher — it locks AND verifies for you:**

```bash
bash start_aiva.sh                    # 1 worker (default)
WORKER_COUNT=2 bash start_aiva.sh     # your normal "both workers" setup
```

`start_aiva.sh` (updated 2026-08-31) does the whole gate automatically:
1. **LOCK** — deterministic checkout: `git fetch` + dirty-guard + `git reset --hard origin/arena/01a05304-voice-agent` (the old script's plain `git pull` was how stale/mixed builds kept slipping in — smokes 4–9).
2. **SANITY** — runs the precision-rail offline tests; aborts if they fail.
3. **START** — token server + N workers + frontend; each worker's output goes to `logs/worker_<id>.out`.
4. **VERIFY** — greps the `[BUILD] git=<sha>` stamp out of **every worker's own log file**, compares it to the locked commit, and prints `DEPLOY VERIFIED` or `DEPLOY FAILED` (exit 1). **Do not smoke until it says VERIFIED.**

Manual equivalent (only if you cannot use the script):

```bash
git fetch origin
git checkout arena/01a05304-voice-agent
git reset --hard origin/arena/01a05304-voice-agent
git log --oneline -1     # the script prints the expected sha — match it
```

Check an already-running deployment without restarting:

```bash
bash start_aiva.sh --check
```

**Paste back:**
1. The `DEPLOY VERIFIED — all N worker(s) on <sha>` line (or the `--check` output)
2. `git log --oneline -1` output
3. The first `[Greeting]` / `[StateEngine]` lines (proves the rail path is live)

If the `[BUILD]` line says `git=unknown` → the worker is not running from this checkout; stop and fix that before smoking.

---

## 1. The basic contract — one scripted session

Read the script; the agent should do exactly this. Fill the Observed column.

| # | Say | Expected | Observed |
|---|-----|----------|----------|
| 1 | "अच्छा एक account number बोल रहा हूँ, लिख ले" | arm line: "haan, bol number — main sun raha hoon." (one of the ARM lines) | |
| 2 | "026" | **SILENCE** (no audio; rail accumulating) | |
| 3 | "9000" | **SILENCE** | |
| 4 | "124205703" | **SILENCE** | |
| 5 | "बस" | full echo, digit-by-digit English: "...zero two six nine zero zero zero one two four two zero five seven zero three. ... sahi hai na?" | |
| 6 | "हाँ" | ack: "...note ho gaya." (one of the ACK lines) | |
| 7 | "क्या लिखा?" | recall: "yeh raha number — zero two six nine zero zero zero one two four two zero five seven zero three." | |

**Pass criteria:**
- Steps 2–4: zero agent speech (the "speaks while I'm dictating" bug is gone).
- Step 5: the spoken digits match **exactly** what you said in steps 2–4, in order.
- Step 7: recall matches step 5 exactly.
- No LLM turns anywhere: every rail turn shows `context: deterministic (no LLM call)` and `engine: precision_rail`.

---

## 2. Three one-shot variations (each a fresh session)

| # | Say | Expected |
|---|-----|----------|
| A | "एक नंबर लिखो 026900124205703" (announcement + number in one turn) | immediate echo of the full number (v8 t5 fix — never "bol number" + lose it) |
| B | "026900124205703" (fresh, no announcement) | immediate echo of the full number |
| C | "एक नंबर लिखो 026900124205703" then after the echo: "12 के बाद 4 बार 0 है, 420 नहीं" | repaired echo: "zero two six nine zero zero one two **zero zero zero zero** five seven zero three" (420/42 replaced with 0000 after 12) |

**Pass criteria for C:** the repaired value is `0269001200005703` — not `12`, not `0000`, not a doubled number.

---

## 3. What to send back

1. Build lines (section 0)
2. The stage diagnostic of the session (or the Observed column filled)
3. Any place where the spoken number differed from what you said — **exact turn + what you said + what it said**

---

## 4. What this smoke is NOT

- Not a correction/query/complaint torture test (those are rail v6–v8 edge cases — deliberately out of scope).
- Not a latency test (TTFA track ④).
- If the basic contract passes, the next step is the Conversation Controller design implementation (track ⑤) — we stop adding rail patterns.
- If it FAILS, the failure is in the primitives (capture/normalize/echo/confirm/preserve), and we fix the primitives — still within the same state model.
