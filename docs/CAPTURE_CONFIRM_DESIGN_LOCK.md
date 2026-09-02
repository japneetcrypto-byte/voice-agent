# Capture Design Lock v2 — topic-blind disclosure capture (owner 2026-09-02)

Status: **DETECTION + GATE SHIPPED (2026-09-02, safe half — no behavior
change).** `agent/disclosure_capture.py` (topic-blind detector + bounded
confirm/reject answers) and the confirmation-gate write semantics are live
and tested (`phase5/tests/test_capture_confirm.py`, 47/47 suites green).
NOT yet wired: the SPOKEN same-turn ask ("note kar loon?") + its fused-seam
wiring in main.py / run_turn (rail/greeting/dictation/vent precedence +
capture_pending lifecycle) + the integration suite
(disclosure → confirm → fact → episode → cross-session recall).
Supersedes v1's घुमने-widening-only approach after the owner's challenge:
*"we talked about ghumna — what if user talks something else — this shall
fail. what to do to avoid this?"*

## The failure v1 would have shipped
Probe across 8 topics through today's extractors (entity_extractor.py):

    MISSED [travel/plan ] मैं कानपुर घुमने जाने वाला हूँ
    MISSED [event/plan  ] अगले हफ्ते मेरा interview है
    CAUGHT [preference  ] मुझे कॉफ़ी पसंद है          (lucky pattern hit)
    MISSED [diet        ] मैं शाकाहारी हूँ
    CAUGHT [family/place] मेरी बहन दिल्ली में रहती है   (relationship pattern)
    MISSED [work        ] मैंने एक नया काम शुरू किया है
    MISSED [hobby       ] मुझे अखबार पढ़ना पसंद है
    MISSED [move/plan   ] मैं अगले महीने पुणे shift हो रहा हूँ

`extract_fact_candidates` is an allowlist (name + allowlisted jobs + likes +
no-advice). Widening for घुमने only relocates the miss. **A per-topic
dictionary is an unbounded game of whack-a-mole and is REJECTED.**

## Principle
**Never make capture depend on understanding the topic. Detect the
disclosure STRUCTURE (topic-blind), confirm, and let the session-end pass be
the general catch-all.**

Why it is safe: the confirm question is topic-independent ("note kar loon?"),
the stored content is the verbatim first-person clause (deterministic, no LLM
rewrite — standing rule), and unconfirmed candidates park PENDING (never in
live context) and expire into the archive (owner's cycle).

## The capture pyramid
1. **Topic extractors (existing — kept, demoted):** name/job/likes/relations/
   place/saved-number. High precision, narrow recall. Never the whole story.
2. **Topic-blind self-disclosure FRAME layer (new — the general capture).**
   Discourse patterns, not topic words. A first-person declarative turn with
   a durable frame:
     - identity      : मैं <X> हूँ / हूं, <X> से हूँ (origin), बन गया/बनूँगा
     - plan/future   : <X> जाने वाला हूँ, जा रहा हूँ, का प्लान है, जाना है/
                       था, <X> shift हो रहा हूँ, वाला हूँ (general future)
     - like/dislike  : <X> पसंद है / पसंद नहीं, अच्छा लगता है
     - possession    : मेरा/मेरी/मेरे <X> है / है ना
     - ability/desire: मुझे <X> आता है, सीखना है, चाहिए, करना है
     - durable past  : <X> में गया था / किया था (strong content only)
   Negatives: questions (कहाँ/क्या/कौन/किस), third-person, agent-directed
   (तू/तुम/आप), vent markers (बहुत बुरा/परेशान/तंग = moment, not durable),
   rail vocabulary, filler. Content = the verbatim clause.
3. **Tiered asking (never nag):**
     - Tier A — clear durable disclosure + NEW content (not already in
       memory view) → ask "note kar loon?" the SAME turn (owner decision).
     - Tier B — plausible but weak / mid-vent / unsure → silent-park
       PENDING, no question.
     - Tier C — venting/emotion-of-the-moment → nothing turn-time.
   One pending question at a time; a disclosure during an active question is
   parked, not stacked.
4. **Session-end consolidation (the general backstop).** It already reads the
   WHOLE session and answers nothing_important_missed. Anything layers 1-2
   missed — ANY topic — may be PROPOSED there for later confirmation.
   Propose only; never silently commit. Topic-general by construction.
5. **Expiry → Archive (owner's Hindi cycle, canonical).**
   Active Memory → Expiry → Compressed Archive → Relevance Retrieval → वापस
   Context में. expire = leave ACTIVE memory (the live view), NOT deletion —
   archived rows stay retrievable. Unconfirmed pending facts expire after N
   sessions; confirmed facts age out of active context the same way on low
   relevance. Gap-R honest recall stays ("yaad nahi, archive se laa doon?").

## Boundary / disciplines (unchanged standing rules)
- No LLM free-form writes; the LLM proposes, never commits.
- Deterministic detector + explicit user confirm commit; nothing else.
- Pending is invisible to live context (memory_view shows committed only).
- PII: digit-dictation and sensitive content still never enter memory
  (saved-number path is separate and confirm-gated already).

## Acceptance (tests-first, RED now in phase5/tests/test_capture_confirm.py)
- [ ] Layer-2 frame detector catches EVERY topic in the probe above
      (8/8), plus negatives (questions/third-person/agent-directed/vent/rail).
- [ ] Same-turn confirm gate: disclosure → ask; yes → commit; no/ignore →
      pending; pending never in view.
- [ ] Consolidation backstop proposes (never auto-commits) a missed fact.
- [ ] Expiry-archive: pending rows leave active view after N sessions,
      archived rows still exist (not deleted).
- [ ] Full 45-suite gate + replay identity (synthetic EMPTY DIFF, real
      baseline unchanged) after implementation.
