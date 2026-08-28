# Aiva — Layered Context Architecture (LOCKED DESIGN)

## What this replaces

The current fixed 6-turn history window + compact {m,c,s} head approach.
This does NOT replace the STT, VAD, TTS, barge-in, safety, or turn controller.
It replaces ONLY how conversation context is managed and fed to the LLM.

## The Three Layers

```
LAYER 1 — RAW (last few turns, verbatim)
  Budget: ~800 tokens (hard cap)
  Content: actual user/assistant messages
  Trust: highest (this IS the conversation)
  When full: overflow turns are compressed into Layer 2

LAYER 2 — COMPRESSED STATE (rolling JSON)
  Budget: ~100-150 tokens
  Content: entities, relationships, emotional context, open topics
  Update: LLM compression call (Layer 2 old + Layer 1 overflow → new Layer 2)
  Trust: medium (summary — Layer 1 always wins on contradiction)
  Format: JSON with explicit fields (not paragraph)

LAYER 3 — PERMANENT MEMORY (from previous sessions)
  Budget: ~100 tokens (top N most relevant to current topic)
  Content: relationships, facts, preferences from past sessions
  Update: at session end + async promotion of explicit/important facts
  Trust: lowest (oldest context)
  Never overrides Layer 1 or Layer 2
```

## What the LLM sees on every call

```
[SYSTEM PROMPT — persona + rules]
[LAYER 3: Memory]         ← ~100 tokens
[LAYER 2: State JSON]     ← ~100-150 tokens
[LAYER 1: Recent turns]   ← ~800 tokens
[USER TURN]
Total: bounded ~1200-1400 tokens regardless of conversation length
```

## Design Rules (from owner feedback)

1. Layer 1 is token-budgeted, not turn-counted
2. Layer 2 compression takes EXISTING Layer 2 + overflowed Layer 1 (rolling, not isolated chunks)
3. Compression is lossy — these must NEVER be lost: people, relationships, explicit facts, active goals, open topics
4. Precedence: recent raw > Layer 2 > Layer 3
5. Layer 3 retrieval is relevance-based (topically matched to current conversation)
6. Compression triggers at ~650-700 tokens (safety margin before the 800 cap)
7. Layer 2 schema is minimal: people + relationships + active_topic + open_items + emotional_context
8. Compression is a SEPARATE LLM call, not the response-generation call
9. Layer 2 facts have provenance: user_explicit > inferred
10. Relationships explicitly stated by the user are promoted to Layer 3 immediately/async (crash resilience)

## Checkpoint System

After every successful compression, save a checkpoint:
```json
{
  "checkpoint_id": "<uuid>",
  "last_processed_turn": 42,
  "layer2_state": { "people": {...}, "active_topic": "...", ... },
  "last_raw_turns": [...],
  "timestamp": "..."
}
```
On crash/restart: recover from last checkpoint, replay turns after checkpoint.

## Entities

Active entities are tracked in Layer 2. Entity corrections (Ram→Shyam) are
handled by the compression LLM which sees both names in context and resolves
them. The state delta compiler (agent/state_delta_compiler.py) provides
deterministic entity tracking as a complement.

## Relationship Promotion

When the user explicitly states a relationship ("मेरा बेटा है", "meri wife hai"),
it is promoted to Layer 3 IMMEDIATELY (async, after the turn completes).
This ensures relationships survive crashes and session disconnects.

## What Does NOT Change

- STT (Groq / Gemini Live)
- VAD / Endpointing (adaptive)
- Turn Controller (respond vs WAIT)
- Compact head {m,c,s} → replaced by Layer 2, but safety field preserved
- TTS (Fish Audio + Edge fallback)
- Barge-in mechanism
- Memory store (SQLite)
- Device identity (C5)
- Safety gates (D5)
- Degradation paths (D1-D9, minus D7/D8 which use Layer 2 now)
- Multi-key rotation
- Entity extractor (still extracts from replies as a complement)
- State delta compiler (still processes explicit deltas)

## Implementation Scope

| # | Component | File | Change |
|---|---|---|---|
| 1 | Layer 2 compression call | New: `agent/context_compressor.py` | One LLM call to compress overflow turns into JSON state |
| 2 | Layer management | Modified: `agent/session_state.py` | Track Layer 1 token count, trigger compression, manage checkpoint |
| 3 | Context builder | Modified: `agent/fused_turn.py` or `agent/session_state.py` | Build 3-layer context instead of flat 6-turn history |
| 4 | Immediate relationship promotion | Modified: `agent/session_state.py` | On explicit relationship statement, async write to Layer 3 |
| 5 | Checkpoint save/load | New or in `session_state.py` | Save after compression, recover on restart |
| 6 | Relevance-based Layer 3 retrieval | Modified: `agent/memory_store.py` | Match memory entries to current topic/entities |
| 7 | Turn controller integration | Modified: `agent/turn_controller.py` | Layer 2 context informs WAIT/RESPOND decisions |
| 8 | Diagnostic tools | Modified: `phase5/stage_diagnostic.py` | Show layer contents per turn |

## What NOT to Build

- A separate "state service" — the state lives in SessionState (in-process)
- A vector database — Layer 3 retrieval uses simple keyword/topic matching
- A streaming STT rewrite — Gemini Live STT is already wired
- A new LLM provider — same Gemini flash-lite models
- A graph database — entities are a simple dict

## Testing

1. Unit test: compression produces valid JSON with required fields
2. Unit test: checkpoint save/load roundtrip
3. Integration test: 30-turn simulated conversation, verify Layer 2 captures entities
4. Regression: Batch-2 20/20, controller 12/12, endpointing tests
5. Live test: "Neetu meri behen hai" → 15+ turns → "Neetu meri kya hai?" → Aiva remembers
6. Crash test: kill worker mid-conversation, restart, verify checkpoint recovery
