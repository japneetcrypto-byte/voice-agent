# Architecture Gap Analysis — What We Broke and What's Actually Missing

**Date:** 2026-08-28 · **Trigger:** Owner correctly identified that the compact head broke multiple locked systems.

---

## The Honest Truth

When we shrank the perception head from 400 tokens to {m,c,s} to fix PARSE-FAIL, we solved one problem and created five others. The conversation **sounds better** (prose quality was always the model's strength), but the **state engine went blind** — it receives almost none of the data it was designed to process.

**We traded invisible internal state for visible conversational quality. The user hears better replies but the system forgot how to remember, track emotions, detect safety risks beyond the extreme, track topics, or classify intent.**

---

## Complete Gap Map: Updater Needs vs Compact Head Provides

| # | Field the updater expects | Purpose | Status | Impact of loss |
|---|---|---|---|---|
| 1 | `emotion.primary` | Emotion label | ❌ Missing | No emotion state tracking |
| 2 | `emotion.valence` | Positive/negative/neutral | ❌ Missing | No emotional trajectory |
| 3 | `emotion.intensity.ordinal` | 1-5 intensity | ❌ Missing | No escalation/de-escalation detection |
| 4 | `emotion.confidence` | Confidence in emotion estimate | ❌ Missing | No calibration, no "ok_to_name" gate |
| 5 | `thread.action` | new/continue/switch/return | ❌ Missing | No topic/thread tracking |
| 6 | `thread.gist` | Topic summary | ❌ Missing | No thread context for LLM |
| 7 | `thread.entities` | People involved | ❌ Missing | No relationship tracking in-session |
| 8 | `safety.risk_level` | none/low/elevated/high | ❌ Missing | Safety gate blind (only compact `s` field) |
| 9 | `safety.self_harm` | Boolean flag | ❌ Missing | Self-harm detection degraded |
| 10 | `safety.harm_to_others` | Boolean flag | ❌ Missing | Third-party risk blind |
| 11 | `user_need` | be_heard/advice/clarify | ❌ Missing | ADVICE mode can't trigger |
| 12 | `advice_requested` | Boolean | ❌ Missing | VENT→ADVICE transition broken |
| 13 | `memory_candidates[].type` | Relationship/fact/preference | ❌ Missing | No new memory creation |
| 14 | `memory_candidates[].content` | What to remember | ❌ Missing | Memory store gets nothing |
| 15 | `memory_candidates[].criterion` | explicit/salient/recurrent | ❌ Missing | Commit rules can't fire |
| 16 | `correction.present` | User corrected Aiva | ❌ Missing | CORR-OVERRIDE can't fire |
| 17 | `correction.about` | What was corrected | ❌ Missing | No correction tracking |
| 18 | `delta.entities` | Entity-relation deltas | ✅ Present | But LLM doesn't emit it reliably |
| 19 | `delta.fact` | Fact deltas | ✅ Present | Same issue |
| 20 | `mem` (optional string) | Quick memory line | ✅ Present | Same issue |

**Score: 6 provided / 22 expected. The updater is running on 27% of its designed input.**

---

## What This Means Per System

### Safety — SEVERELY DEGRADED 🔴
- The compact `s: "SAFE|UNSAFE"` field is a binary flag
- The old head had `risk_level` (4 levels), `self_harm` (bool), `harm_to_others` (bool), `confidence`
- The updater's rule "any self_harm=true → high_risk" can't fire because `self_harm` is never in the head
- The `clarify-first` tier can't distinguish figurative from explicit
- **We reverted to a less nuanced safety system without formally acknowledging it**

### Memory — SEVERELY DEGRADED 🔴
- No `memory_candidates` → nothing committed during conversation
- The `mem` and `delta` fields exist but the LLM rarely emits them (80% PARSE-FAIL)
- Session-end batch extraction (proposed but not built) would fix this
- **After 2-3 sessions, memory becomes stale**

### ADVICE Mode — BROKEN 🟡
- No `user_need` or `advice_requested` → VENT→ADVICE transition can't fire
- The user asks for advice → the policy stays in VENT → "avoid: advice" is active
- **User-visible: asking for advice gets generic empathy instead of actual help**

### Emotion — DEGRADED 🟡
- No `emotion.primary` or `intensity` → no trajectory, no escalation detection
- The updater's EMOTION-CARRY rule defaults to `neutral_unclear`
- `emotion_reflection.ok_to_name` always false (confidence defaults to 0)
- **User-visible: Aiva doesn't acknowledge emotional context anymore**

### Thread/Topic — DEGRADED 🟡
- No `thread.action`/`gist`/`entities` → no topic tracking
- Thread summaries sent to LLM are empty
- **User-visible: Aiva doesn't track topic switches or returns**

---

## What's Actually Working

| System | Status | Why |
|---|---|---|
| Conversational replies | ✅ Good | Prose quality is the model's strength |
| Session language pin (`hi`) | ✅ | Devanagari transcripts now |
| Adaptive endpointing | ✅ (recalibrated) | Premature interrupts reduced |
| Turn controller (WAIT/respond) | ✅ | Trail-off connectors, fragments |
| Backchannel suppression | ✅ | Exact-token matching |
| Safety (extreme) | 🟡 | The compact `s` field catches extreme cases but misses nuance |
| Device identity (C5) | ✅ | UUID → participant → memory owner |
| Multi-key rotation | ✅ | 3 keys × 2 models = 6 attempts |
| Stale-turn guard | ✅ | Prevents out-of-order state mutations |
| Telemetry | ✅ | Full lifecycle tracking |

---

## The Two Architectures We Need to Choose Between

### Architecture A — Compact head + session-end extraction
```
Per-turn: {m,c,s} → safety gate only → prose reply
Session end: full transcript → LLM extraction → memory/facts/relationships
```
**Pros:** Minimal per-turn burden, best prose quality
**Cons:** No real-time emotion/safety nuance, no in-session thread tracking, memory delayed to session end

### Architecture B — Medium head (the middle ground)
```
Per-turn: {m, c, s, e (emotion ordinal 1-5), n (user_need), t (thread gist)} 
→ ~50 tokens instead of 400
→ safety gate + emotion tracking + advice trigger + thread context
Session end: full transcript → memory extraction (same as A)
```
**Pros:** Real-time emotion/ADVICE/safety, session-end handles detailed extraction
**Cons:** Higher token cost per head, risk of PARSE-FAIL increases slightly

### Architecture C — Hybrid (what I'd recommend)
```
Per-turn: compact {m,c,s} → safety gate → prose reply (as-is, proven)
Thread tracking: from the turn_controller's continuation/switch detection (already exists)
Emotion: inferred from the PROSE reply (not the head) using deterministic patterns
ADVICE: triggered by turn_controller detecting question patterns (already partially exists)
Session end: full transcript → LLM extraction → memory/facts/relationships/entities
```
**Pros:** Zero additional head burden, all proven mechanisms, session-end fills the gaps
**Cons:** Emotion is inferred (less precise), ADVICE trigger is pattern-based (less accurate)

---

## Recommendation: Architecture C

**Why C over B:** We've proven that asking the LLM for more structured output increases PARSE-FAIL. Architecture B risks re-introducing the problem we just solved. Architecture C uses only deterministic signals that are already computed — zero additional burden on the LLM.

**What Architecture C requires:**

| # | Component | What | Where | Already exists? |
|---|---|---|---|---|
| 1 | **Thread tracking from controller** | `turn_relation` already detects backchannel/content — extend to detect topic shift patterns | `turn_controller.py` | 🟡 Partial (relation exists, topic shift doesn't) |
| 2 | **Emotion from prose** | Pattern-match emotional keywords in Aiva's own reply (she mirrors the user's emotion) | New module (deterministic, no LLM) | ❌ New |
| 3 | **ADVICE trigger from question patterns** | "kya karun", "batao na", "kya plan" → set advice_requested | `turn_controller.py` extension | ❌ New |
| 4 | **Session-end extraction** | Full transcript → Gemini → structured memory candidates | New function at session end | ❌ New (designed in the earlier plan) |
| 5 | **Entity extraction from replies** | Already built | `entity_extractor.py` | ✅ Done |

---

## What the Owner Should Decide

1. **Approve Architecture C** (or B, or A)
2. **Prioritize:** Which degraded system to restore first (safety nuance, memory, emotion, ADVICE, threads)
3. **Session-end extraction:** One LLM call per session — approve this cost?

Do NOT start implementation until these decisions are made.
