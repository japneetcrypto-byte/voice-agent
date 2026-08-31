# Memory Boundaries V1 — History / Working State / Session Memory / Long-term Memory

**Date:** 2026-08-31 · **Status:** LOCKED conceptual boundary (owner: "state,
memory, context — everything should be called, retrieved, ranked and
utilised as required"; "make the boundaries explicit rather than adding more
logic into the rail").

The live architecture has four distinct stores that were partially blurred.
This doc fixes the boundary; the code changes for #1/#2 (below) implement it.

---

## The four stores — ownership, persistence, ranking, call-trigger

| Store | What it holds | Owned by | Persists? | Ranked by | Called when |
|---|---|---|---|---|---|
| **History** | Verbatim turns (this session) | `ConversationSession.history` + `LayeredContextManager.layer1` | No — dies with session; crash-checkpoint only | recency (token-budgeted ~800) | every LLM call (Layer 1) |
| **Working State** | What is happening right now: conversation state, task, topic, emotion, mode, policy, delivery | `ConversationController` (`engine["conv"]`) + `SessionState` (emotion/mode/policy) | No — never persisted (checkpoint discarded at clean shutdown by design) | n/a (single current value) | every turn — decides behavior; folded into policy payload |
| **Session Memory** | Candidates gathered during THIS session, not yet confirmed durable: `write_candidates`, store rows `status='pending'`, Layer-2 compressed people/facts | `SessionState.state["memory"]` + `MemoryStore` pending rows + `LayeredContextManager.layer2` | Partial — pending rows survive in SQLite but are INVISIBLE to context until committed | n/a (awaiting confirmation) | promotion to long-term at session end (or on repeat sighting) |
| **Long-term Memory** | Committed cross-session facts: relationships, episodic (places/trips), semantic (name/job), preferences | `MemoryStore` (`status='committed'`), owner-keyed | **Yes** — SQLite, survives restart | relevance + recency + salience (**ranking = #3, next slice**; today: recency last-40) | session start (seed) + every LLM call (Layer 3 `memory_view`) |

## Boundary rules (non-negotiable)

1. **History is never a source of truth for memory.** Only the deterministic
   extractors / compression write INTO session memory. The LLM never writes
   memory candidates (compact head hardcodes `memory_candidates = []` — kept).
2. **Working State is never persisted.** The Conversation Controller owns it;
   it dies with the session. Corrections to it never write to memory unless
   they are also explicit user facts.
3. **Session Memory is the only bridge to Long-term.** Promotion is
   deterministic + gated (`MemoryGate`: reject / quarantine / pending /
   commit). Explicit user statements → immediate commit (per §4.5); inferred
   → pending → confirm (repeat sighting or session-end occurrences≥2).
4. **Long-term is the only thing that survives.** Retrieval is ranked; the
   ranker (relevance + recency + salience) is slice #3.
5. **No fabrication at the boundary.** When a recall query has nothing in
   Long-term (and today's History has nothing), the LLM must say
   "yaad nahi hai" (prompt rule 14) — never invent (Uttarakhand incident).

## The call matrix (owner: "called, retrieved, ranked, utilised as required")

| Situation | Called | Retrieved | Ranked | Utilised |
|---|---|---|---|---|
| Every LLM turn | History (L1) + Long-term (L3) + Working State (policy) | L1 verbatim + L3 committed | L1 token-budget; L3 relevance/recency (#3) | policy shapes behavior; memory is background knowledge |
| History over budget | → Session Memory: L2 compression (separate LLM call) | overflow turns | token-budget | L2 rolled state; **promotable people → Long-term (#1)** |
| Recall query ("कौन सी जगह बताई थी?") | History + Long-term | committed rows (ranked) | relevance first (#3) | answer from fact; else rule-14 honesty |
| Repeat/correction ("नहीं, X है") | Long-term | matching row | exact match | supersede/update (preference supersession = future) |
| Session end | Session Memory | pending rows | occurrences≥2 | promote → Long-term |

## Slice scope (this round, owner-directed: no rail/controller/TTS changes)

1. **L2 → L3 promotion** — L2 people with relations → `_promote_memory`
   (pending-first; repeat confirms). Closes "L2 dies at clean shutdown".
2. **Deterministic write path for explicit facts + preferences** — name /
   job / likes / no-advice from user speech → store (explicit → commit).
   Same gate, same lifecycle, no LLM writes.
3. *(next)* Relevance-ranked L3 retrieval + salience.
4. *(next)* L2 compression failure visibility.
5. *(next)* wire or remove `state_delta_compiler`.
