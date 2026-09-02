# Episode-Memory Slice — Design Lock (minimum next slice)

Status: **FOUNDATION IMPLEMENTED (2026-09-02, owner: "you decide — move").**
Design lock approved; the foundation slice (§F stages 1-4 + §D tests) shipped.
Still NOT built (separate, later): capture-confirm v2 feeder + the deferred
integration suite (§D, `test_capture_recall_integration.py`), expiry/archive,
relevance retrieval. The intentionally-RED `phase5/tests/test_capture_confirm.py`
remains the feeder's spec.
Companion doc: `docs/CAPTURE_CONFIRM_DESIGN_LOCK.md` (v2, topic-blind
disclosure capture — the FUTURE feeder; NOT built this slice).

## Owner decisions (2026-09-02, resolved)
1. **`W_cross` = 30 days — CONFIRMED.** Configurable as policy; 30 is the
   initial default.
2. **`session_ref` = a monotonic per-session ID.** The session-start
   timestamp is metadata only, never the identity token. Cleaner identity
   semantics + replay/testing behavior.
3. **Land the FOUNDATION first.** Schema + deterministic episode attachment
   rules land BEFORE capture-confirm v2. The foundation must be
   independently testable from synthetic/fixture facts and turns — its
   correctness must NOT depend on the disclosure detector existing.
   Then capture-confirm v2 becomes the feeder into this foundation, followed
   by integration tests proving:
   `disclosure → confirmation → atomic fact → episode assignment →
   cross-session recall`.

Locked architecture (owner, 2026-09-02):
`Conversation → Disclosure Detection → Atomic Verbatim Fact → Confirmation/
Trust Gate → Episode grouping or standalone fact → Active Memory → Expiry →
Compressed Archive → Relevance Retrieval → Read-time Semantic Assembly`

**Ground truth this slice builds on (verified in code):**
- A "memory" today = one row in `memory(owner_id, type, content, criterion,
  status, created_at, last_seen, occurrences, sessions)`; dedup = exact
  `(owner_id, type, content)` → bumps `occurrences` on the SAME row.
- `view()` returns committed rows only → pending/quarantined invisible to the
  LLM. `explicit → immediate commit`; otherwise pending; session-end promotes
  only `occurrences>=2`.
- Existing deterministic extractors: `extract_entities_from_user_text`
  (person name+relation, with `normalize_entity` canonical aliasing),
  `extract_place_facts` (travel clauses, verbatim), `extract_fact_candidates`,
  `_persist_number` (saved numbers, confirm-gated). No episode/unit/5W1H
  concept exists anywhere (only unrelated doc mentions of an STT bug and a
  TTS outage).

---

## 1. What is the exact `AtomicFact` representation?

**An AtomicFact is NOT a new table — it is the existing committed `memory`
row, plus three nullable columns.** We do not introduce a second fact
record; that would fork provenance and dedup.

```
AtomicFact := memory row where status='committed'
  owner_id      TEXT      (isolation — unchanged)
  type          TEXT      preference|relationship|semantic|episodic|saved_number
  content       TEXT      VERBATIM user clause. IMMUTABLE after insert.
  criterion     TEXT      explicit|salient  (provenance marker — unchanged)
  status        TEXT      committed | pending | quarantined | superseded
  created_at, last_seen, occurrences, sessions   (unchanged semantics)
  episode_id    INTEGER NULL   -> episodes.id  (NEW)
  supersedes_id INTEGER NULL   -> memory.id    (NEW)
  time_mark     TEXT    NULL    raw time token, NOT interpreted (NEW)
                               e.g. 'agle_mahine', 'kal' — regex-captured,
                               never parsed into a date. Merge-guard + future
                               read-time anchor only.
```

Rules that make it an AtomicFact:
- `content` is the user's words verbatim; no LLM rewrite at write time (locked).
- No `who/what/when/where/why/how` columns. Ever. 5W1H is a READ-time lens.
- Dedup stays exact-content: an identical `(owner,type,content)` bumps
  `occurrences` on the existing row — it does NOT create a new fact and does
  NOT re-run episode assignment.

## 2. What is the minimal `Episode` representation?

A container, not a semantic record.

```
episodes(
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_id      TEXT NOT NULL,
  session_id    INTEGER NOT NULL,      -- the monotonic session that OPENED it
                                       -- (owner decision #2): identity
  created_at    TEXT NOT NULL,         -- session-start timestamp is METADATA,
                                       -- never the identity token
  last_touched_at TEXT NOT NULL,       -- bumped on every attach
  archived_at   TEXT NULL              -- set by the (later) expiry slice
)
```
- **No** What/When/Where/Why/How fields. **No** title/label that paraphrases
  content. The episode's "meaning" is entirely its member facts.
- Facts link via `memory.episode_id`; mention keys link via `memory_mentions`
  (below) and are the cross-episode recall path.
- Groupable kinds today: **`episodic` only** (plans/trips/events). Singleton
  kinds — `preference`, `relationship`, `saved_number`, and `semantic`
  name/job facts — are **always standalone** (`episode_id NULL`). This
  removes a whole class of mis-merge risk before it exists.

## 3. How is `episode_id` assigned deterministically?

Two parts, deliberately split:
- **The id VALUE is an opaque AUTOINCREMENT** (never serialized into turn
  logs, memory content, or context). Value determinism across environments is
  not required — memory writes never happen inside the replay harness (see §E).
- **The ASSIGNMENT is a pure deterministic decision** in a new module
  (`agent/memory_units.py`): given `(owner_id, session_id, session_start,
  fact kind, keys, time_mark, existing episode metadata)`, it returns
  "attach to episode E | create new episode | standalone". Same inputs →
  same output, every time, no LLM, no hidden clocks. Testable as a pure
  matrix over synthetic fixtures.

**Session identity (owner decision #2):**
- Each live session is issued a **per-owner monotonic `session_id`** (integer,
  strictly increasing) at session start — persisted by `MemoryStore`
  (`start_session(owner_id) -> session_id`, a small per-owner counter table).
- The session-start timestamp is stored as **metadata** (`session_start`)
  and used ONLY for `W_cross` calendar-window arithmetic — never as the
  identity token.
- Synthetic/testing sessions pass integers (1, 2, 3…) — no timestamp, no
  clocks, fully deterministic.

**`W_cross` (owner decision #1):** the cross-session attach window is a
parameter to the pure rules — `w_cross_days: int = 30`. The wiring layer
reads the policy default (module constant `W_CROSS_DAYS = 30`, overridable
via `AIVA_W_CROSS_DAYS` env at the config seam) and passes it in. The pure
functions never read config or env themselves.

## 4. How are entity/place mention keys derived?

Only by reusing existing deterministic extractors — **no new patterns for the
slice**, and never fuzzy:

```
memory_mentions(fact_id INTEGER, kind TEXT, key TEXT,
                UNIQUE(fact_id, kind, key))
  kind 'p'  person : normalize_entity(name).lower()  from
                    extract_entities_from_user_text (name+relation pairs)
  kind 'l'  place  : canonical place token lowercased, from the disclosure
                    frame's location slot or the place-fact clause, matched
                    against the existing place vocabulary + a small static
                    gazetteer (reused lists, no new regex family)
```
- Keys are derived at FIRST WRITE (even for pending rows — they annotate
  verbatim content only; they never affect `view()`).
- No key detected (garble, unknown place) → no mention rows → the fact is
  still stored verbatim and still recallable by content scan. Keys are a
  recall ACCELERATOR, never a recall GATE.
- No cross-episode fuzzy resolution yet (locked). `p:rahul` in episode A and
  `p:rahul` in episode B are the *seed* of the future graph; nothing joins
  them this slice.

## 5. What exact conditions create a new episode vs attach to an existing one?

Evaluated at the moment a fact becomes `committed` (immediate-explicit commit
OR pending→committed promotion that carries context), in this ORDER — the
first matching rule wins:

| # | Condition | Outcome |
|---|---|---|
| R1 | Fact kind is a singleton (`preference/relationship/saved_number`/name-job `semantic`) | **STANDALONE** (`episode_id NULL`) — always |
| R2 | The turn is an explicit topic switch (existing `TOPIC_SWITCH_RE`, minus number-topic) OR the current session already has an episode whose *last fact's time_mark differs* from this fact's (conflicting plans) | **NEW episode** |
| R3 | An episode of THIS owner was opened by the CURRENT `session_id` AND the turn continues its thread — continuation cue (`aur / aur bhi / wahan / phir / aur haan`, existing `CONTINUE_CUE_RE`) OR turn adjacency (no intervening unrelated disclosure) | **ATTACH** to that session's episode |
| R4 | No session episode, but the fact carries a place/person key that matches a key in exactly ONE episode of this owner whose `last_touched_at` is within `w_cross_days` of `session_start`, and that episode holds no fact with a differing `time_mark` | **ATTACH** (cross-session restatement: "wo Kanpur wala plan") |
| R5 | Otherwise — including a key matching TWO+ episodes, or any ambiguity | **NEW episode** for groupable kinds; **STANDALONE** if the fact has no key and no continuation context |

Membership decisions are pure functions of `(owner_id, session_id,
session_start, kind, keys, time_mark, episodes-snapshot, w_cross_days)` —
synthetic fixtures exercise the whole matrix without any detector.

- Under-merge and over-merge are both NON-DESTRUCTIVE: a wrongly-standalone
  fact is still found by key/content recall; a wrongly-attached fact stays
  verbatim inside a container. Episodes never gate recall.
- New episodes are only created by R2/R5 (never by R1), so singleton kinds
  can never spawn containers.

## 6. How do corrections work without mutating the original fact?

Corrections create a NEW confirmed fact and mark the old one superseded —
nothing is edited in place:

- When a committed fact F1 exists AND a later confirmed fact F2 has: an
  explicit correction frame ("नहीं / गलत / बदल / change / सही नहीं", reuse the
  rail's correction vocabulary) AND the same kind AND key-overlap with F1 →
  `F2.supersedes_id = F1.id`, and `F1.status → 'superseded'`.
- `F1.content` is never touched. A `superseded` row leaves `view()` (same
  filter as pending) and is never the target of a further supersede (only
  `status='committed'` rows can be superseded → single chain).
- Read-time assembly prefers the non-superseded fact; the superseded fact
  remains in the store for audit.
- If no F1 matches → F2 is a plain new fact (recency handles it at read time).
- Corrections flow through the SAME confirmation gate as any fact: the
  supersede only fires on a user-confirmed correction, never on a raw
  disclosure.

## 7. How does this preserve current confirmation-gated behavior?

- The gate is untouched and runs FIRST. Episode assignment, mention keys, and
  supersede all happen only after a row is `committed` (or at first write for
  keys). Pending/quarantined rows never enter an episode and never enter
  `view()`.
- `_promote_memory`'s deterministic/verbatim/MemoryGate path is unchanged; it
  gains an optional `context` argument (turn text + `session_id` +
  `session_start`) consumed by the new pure module.
- `view()` output is byte-identical — episodes/mentions are internal layers,
  invisible to the LLM context (pins B10, tests).
- The confirm question itself ("note kar loon?") is out of this slice — it
  belongs to the capture-confirm design (v2 doc). This slice assumes a
  confirmed fact arrives with its disclosure context.

## 8. What happens when episode assignment is uncertain?

Rule R5 — never guess, never merge into multiple episodes:
- Ambiguous key (matches 2+ episodes) or missing signal → **standalone fact**
  (`episode_id NULL`) for key-less facts; **new episode** only when the fact
  is clearly groupable but merely conflicts.
- Consequence accepted and stated: a fact that *should* have joined an
  episode may end up standalone. Recall is unaffected (key/content search
  spans standalone facts); the container is a recall accelerator, not a
  membership gate. Correctness of recall never depends on perfect grouping.

## 9. What existing code/files will change?

| File | Change |
|---|---|
| `agent/memory_store.py` | Idempotent schema: `CREATE TABLE episodes`, `CREATE TABLE memory_mentions`, guarded `ALTER TABLE memory ADD COLUMN episode_id/supersedes_id/time_mark`. New methods: `start_session(owner_id) -> session_id` (per-owner monotonic counter), `ensure_episode`, `attach_fact_to_episode`, `add_mentions`, `supersede_fact`; `commit()` gains optional context `(session_id, session_start, turn_text)`; `view()` untouched. |
| `agent/memory_units.py` (NEW, pure) | Key derivation (wraps existing extractors), ordered membership rule (R1–R5), time-mark regex capture, correction-frame + key-overlap → supersede decision. Signature takes `session_id`/`session_start`/`w_cross_days` as parameters — no store imports, no LLM, no config/env reads. |
| `agent/config.py` | Add `W_CROSS_DAYS` (default 30, env `AIVA_W_CROSS_DAYS`) read at the config seam. |
| `agent/entity_extractor.py` | Reuse only: export a small deterministic place-key helper over the existing vocabulary; `normalize_entity` already exists. No new pattern family. |
| `agent/main.py` | Issue `session_id` at session start (`store.start_session(owner_id)`); `_promote_memory` (+ `_persist_number`) pass context `(session_id, session_start, turn text)` into the new module post-commit. Logic otherwise unchanged. |
| `phase5/tests/test_memory_units.py` (NEW) | The R1–R5 + key + supersede matrix over SYNTHETIC fixtures (see §D). |
| `RUN_ALL.sh` | Append the new suite. |

**Foundation independence (owner decision #3):** this slice's tests feed
`memory_units` synthetic `(session_id, session_start, kind, keys, time_mark)`
tuples and canned text through the EXISTING extractors only. Nothing in the
foundation imports or depends on `extract_disclosure_frames` (which does not
exist this slice). The disclosure detector is the capture-confirm v2 feeder —
a later, additive layer.

## 10. What will explicitly NOT change?

- The `memory` row's core columns, dedup key, criterion semantics, or the
  verbatim-content rule. Content is never `UPDATE`d.
- `view()` / LLM context output (byte-identical — protected by existing
  context tests + a new pin).
- MemoryGate, ownership isolation, PII/saved-number handling.
- The capture detectors, the rail/conversation controller, the control-plane
  module, and their tests.
- Replay harness + fixtures; no new per-turn log fields (see §E).
- No 5W1H fields, no graph DB, no typed edges, no fuzzy entity resolution,
  no numeric confidence, no read-time clustering engine.
- No expiry/archive/retrieval yet (separate, later slice — this slice only
  adds the `archived_at` column so the container is ready).

---

## A. Current → Proposed architecture diff

| Layer | Today | After this slice |
|---|---|---|
| Fact | verbatim string row | same row + `episode_id/supersedes_id/time_mark` (3 nullable cols) |
| Grouping | none (every fact isolated) | `episodes` container (episodic kinds only) + deterministic membership |
| Keys | implicit in content, unqueryable | `memory_mentions(fact_id, kind, key)` from existing extractors |
| Corrections | impossible (only occurrence bumps) | new confirmed fact + supersede pointer; old row untouched |
| Context | committed-only view | unchanged (byte-identical) |
| Recall | exact-ish content scan | key-indexed scan (faster, cross-session) — episodes as scope, not gate |
| Lifecycle | n/a | episodes carry `archived_at` (ready for the expiry slice) |
| Write discipline | deterministic verbatim + gate | unchanged |

## B. Exact invariants (each testable)

- **B1** Content immutable: no code path ever `UPDATE`s `memory.content`.
- **B2** Scope: every episode and mention belongs to the fact's owner; a
  cross-owner query returns nothing.
- **B3** Dedup precedes membership: duplicate `(owner,type,content)` bumps
  `occurrences` on the existing row; never a new fact/episode/standalone.
- **B4** Only `status='committed'` rows carry `episode_id` / participate in
  supersede; pending/quarantined never do.
- **B5** A superseded row: content untouched, leaves `view()`, never target of
  a second supersede (single chain).
- **B6** Membership/supersede decisions are pure deterministic functions —
  no LLM, no hidden clock; same inputs → same output.
- **B7** Singleton kinds (preference/relationship/saved_number/name-job
  semantic) never join an episode.
- **B8** An episode row stores only id/owner/timestamps/archive — zero
  interpreted fields.
- **B9** Recall by key/content never depends on episode membership.
- **B10** `view()` / context strings unchanged (byte pin).
- **B11** Every committed fact of an episodic disclosure has at least its
  kind + key/time-mark derivations attempted at write; failure → NULLs, fact
  still committed.

## C. Failure / adversarial cases

1. **ASR garble of a place** ("कांग्रिक") → no gazetteer hit → no key →
   standalone. Later correct spelling ("Kanpur") won't key-match the garble
   fact. Accepted: the garble fact is low-value; honest no-record elsewhere.
2. **Same-session topic ping-pong** with continuation cues ("aur ek baat,
   ...") → R3 may attach a genuinely new topic into the session episode.
   Non-destructive: facts stay verbatim; assembly answers per fact; a
   later explicit topic switch (R2) still splits cleanly.
3. **Two trips to the same city** (Kanpur this month vs next year) → same
   `l:kanpur`. If time marks differ → R2/R4 block merge → separate episodes.
   If neither carries a time mark → they share a container; the read-time
   question "Kanpur wali trip?" may be ambiguous → the agent asks which one /
   lists both. Facts intact; acceptable conversational ambiguity.
4. **Contradiction without a correction frame** ("nahi, maine Kanpur nahi
   Jaipur kaha tha" — but no F1 match because prior fact verbatim differs) →
   both facts coexist; recency at assembly. No silent mutation.
5. **Cross-session restatement beyond `W_cross`** → new episode (not attach);
   key recall still surfaces both. Accepted drift.
6. **Replay determinism** → memory writes never run in the harness (engines
   carry no `store`); §E.
7. **PII** → saved-number facts are singleton/standalone; digits never become
   mention keys.
8. **Episodic overflow** — a huge vent session could open one big episode via
   R3. Harmless: container only; archive slice will bound it.

## D. Tests to add

`phase5/tests/test_memory_units.py` (NEW, foundation — synthetic fixtures only):
- **Fixture independence (owner decision #3):** every matrix row feeds
  `memory_units` explicit `(owner_id, session_id, session_start, kind, keys,
  time_mark)` inputs — NO call to any disclosure detector (it does not exist
  this slice). Canned-text paths call only the EXISTING
  `extract_entities_from_user_text`/place-key helper to prove the wrapper.
- Key derivation: person from "meri behen Neetu...", place from canned
  clauses, alias normalization, garble → no key.
- Membership matrix (R1–R5): R1 singleton kinds always standalone; R2
  topic-switch → new; R2/R4 conflicting time-mark → new; R3 same `session_id`
  continuation cue + adjacency → attach; R4 cross-session key overlap within
  `w_cross_days` → attach; R4 beyond `w_cross_days` → new; R5 ambiguous key
  (2+ E) → standalone/new; no key + no cue → standalone.
- Session-id semantics: distinct monotonic ids are distinct sessions even
  with identical `session_start` metadata; same id + different metadata is
  still one session.
- Determinism: same input twice → identical outcome; `w_cross_days`
  parameterized (30 default, 0 disables cross-session attach).
- Supersede: correction frame + key match → new fact with `supersedes_id`,
  old row `status='superseded'`, content byte-unchanged, excluded from
  `view()`, no double-supersede.
- Dedup-before-membership: duplicate content bumps occurrences, no new row.
- Isolation: owner A's episode never matches owner B's key overlap.
- View pin: after any episode/mention/supersede operations, `view()` output
  equals the pre-op output for the same committed rows.
- Regression: existing 45 suites stay green (B-invariants via new tests only).

`phase5/tests/test_capture_recall_integration.py` (LATER — NOT this slice):
the end-to-end `disclosure → confirmation → atomic fact → episode assignment
→ cross-session recall` suite. Lands WITH capture-confirm v2 (the feeder) and
exercises the full path through `_promote_memory` wiring. Out of scope now
because the feeder does not exist yet (owner decision #3: foundation first,
independently testable).

## E. Replay-identity impact

**None — by construction:**
- This slice adds NO per-turn log/telemetry field; episodes/mentions live
  only in the memory store.
- Memory side effects require a real `store`+`sess`; the replay harness
  engines carry neither (verified: `_persist_number` and `_promote_memory`
  early-return on `store is None`), so replay archives regenerate
  byte-identically → synthetic EMPTY DIFF holds.
- Real baseline (23 turns, saved-number flow) produces no general-fact
  disclosure writes through `run_turn`; unchanged.
- The capture-confirm feeder (when it lands) writes memory ONLY on live
  confirmed turns — memory is out-of-replay-scope by design today (same as
  the existing saved-number persist).
- RUN_ALL gains one additive suite; nothing existing is edited except
  schema/store/module files listed in §9.

## F. Minimal implementation plan (ordered, gated; no code yet)

1. **Session identity + config** — `start_session(owner_id)` per-owner
   monotonic counter in `memory_store`; `W_CROSS_DAYS` at the config seam.
   Gate: counter increments per owner; isolation holds; env override read.
2. **Schema + store** — idempotent migration (`CREATE TABLE IF NOT EXISTS`
   episodes/memory_mentions; guarded `ALTER TABLE memory ADD COLUMN`), new
   store methods (`ensure_episode`, `attach_fact_to_episode`, `add_mentions`,
   `supersede_fact`). Gate: store-only unit tests pass; `view()` pin green.
3. **`agent/memory_units.py` (pure)** — key derivation + R1–R5 rule +
   time-mark + supersede decision, all parameterized. Gate: full §D matrix
   green over SYNTHETIC fixtures with ZERO wiring (pure functions) and no
   dependency on any disclosure detector.
4. **Wire `main.py`** — issue `session_id` at session start; `_promote_memory`/
   `_persist_number` pass context; post-commit attach/keys/supersede. Gate:
   45 existing suites green + new suite green + replay identity (synthetic
   EMPTY DIFF, real baseline unchanged).
5. **Foundational live smoke (owner, Mac)** — scripted cross-session recall
   WITHOUT the feeder: manually confirm a fixture fact path (a saved-number
   session + a follow-up session that restates it within `W_cross`) →
   correct attach/recall from one episode. Proves the foundation end-to-end
   before the feeder exists.
6. **Then capture-confirm v2 (feeder)** lands on top, followed by the
   integration suite (§D, deferred). Ship each stage as its own commit
   behind this approval.

---

## Challenge — is an episode system even needed for the minimum goal?

**Honest answer: for the Kanpur smoke ALONE, no — deterministic keys on
verbatim facts would suffice**, and if the ONLY goal were one cross-session
place recall, I would tell you to skip episodes entirely and add
`memory_mentions` only.

Episodes earn their keep at exactly three points, all of which you have
locked into the roadmap:
1. **Companion accumulation** — "…aur Rahul bhi aa raha hai" carries no place
   key; only thread-continuity attach (R3) groups it to the trip. Keys alone
   fragment it.
2. **Expiry → Archive** — your locked lifecycle needs a container to move;
   archiving 40 standalone rows cannot express "this trip is stale".
3. **Read-time assembly scope** — "Kanpur wali trip mein kaun aaya?" needs the
   unit to bound which facts to assemble.

So: keys are the recall floor, episodes are the lifecycle/assembly ceiling.
The slice builds both because the floor alone fails points 1–3, and the cost
of the container is ~one small table + five deterministic rules — small next
to the mis-merge risk it lets us avoid by scoping episodes to `episodic`
kinds only and defaulting to standalone on any doubt.

**Second challenge (self-applied):** I scoped grouping to `episodic` kinds
only and made preference/relationship/saved-number always standalone. If you
disagree — e.g., you want a "person-thread" episode around Rahul that
accumulates every relationship disclosure — say so now; it changes R1 and
nothing else.

---

## Open questions — RESOLVED (owner 2026-09-02)

1. ~~`W_cross`: propose 30 days~~ → **CONFIRMED**, configurable policy default
   30 (`W_CROSS_DAYS` / `AIVA_W_CROSS_DAYS`), passed as a pure parameter.
2. ~~`session_ref` token~~ → **per-owner monotonic per-session ID**; session
   start timestamp is metadata only.
3. ~~Sequencing~~ → **foundation first** (this slice, fixture-tested), then
   capture-confirm v2 as the feeder, then the integration suite.

Remaining non-blocking confirmations before implementation:
- `session_id` scope: **per-owner** monotonic (my proposal — owner isolation;
   a global counter would interleave owners and complicate isolation tests).
   Object if you want a global monotonic instead.
