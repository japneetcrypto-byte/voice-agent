# Solana-Type Anticipatory Pipeline — Status Audit
**Date:** 2026-08-28 · What's done, what's leftover, what each gap costs in latency.

## The Solana Principle Applied to Aiva

```
SOLANA: Next block leader is known BEFORE the slot → pre-builds → executes instantly
AIVA:   User is SPEAKING → pipeline knows what's coming → pre-builds → executes instantly
```

## What's Already Done vs Leftover

| # | Anticipation Step | Status | Evidence | Latency Cost if Missing |
|---|---|---|---|---|
| 1 | STT streams during speech (Gemini Live WS) | DONE | main.py:584,593,643 | -300 to -800ms |
| 2 | Context pre-assembled while user speaks | DONE (partial) | main.py:320-326 | -50 to -100ms |
| 3 | LLM connection pre-warmed | DONE | fused_turn.py:48-56 cached | -100 to -200ms |
| 4 | TTS connection pre-warmed | NOT DONE | tts.py has no pre-warm or connection pool | -200 to -500ms |
| 5 | Layer 2 compression in background | DONE | main.py:319 async create_task | Zero (background) |
| 6 | Entity extraction in parallel with response | DONE | main.py:443-444 | Zero (parallel) |
| 7 | Turn controller during speech | NOT DONE | Controller fires AFTER STT completes | -500 to -900ms |
| 8 | Interim transcript to early LLM start | NOT DONE (deferred) | Would be biggest single latency win | -500 to -900ms |
| 9 | Checkpoint save in background | Partial | Fires on shutdown only | Crash risk |
| 10 | Relationship promotion async | DONE | main.py:443-444 | Zero |

## Summary: 6 of 10 done, 4 leftover

| Leftover Gap | Latency Impact | Complexity | Priority |
|---|---|---|---|
| TTS connection not pre-warmed | -200 to -500ms per turn | Low | HIGH |
| Turn controller during speech | -500 to -900ms | Medium (needs interim transcripts) | HIGH but deferred |
| Interim to early LLM start | -500 to -900ms | High (speculative execution) | DEFERRED |
| Checkpoint not saved after compression | Crash resilience | Low | MEDIUM |

## Latency Projection

| Configuration | Speech to Audio |
|---|---|
| Current measured | 2.1-2.5s |
| After TTS pre-warm | 1.6-2.0s |
| + Gemini Live STT working properly | 1.2-1.6s |
| + Turn controller on interim | 800-1200ms (deferred) |
| + Interim to early LLM | 500-800ms (deferred) |

## What's NOT Leftover
No architecture changes needed. No new services. No provider changes. No state dimension changes. All contracts, safety, degradation paths, persona, memory scoping intact.
