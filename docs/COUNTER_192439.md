# Counter-Arguments to Directive 192439 — proposed synthesis (2026-08-29 night)

Owner asked for counter-arguments before implementing. Each point: the
directive's claim → my position → evidence → what I propose instead.
Nothing disputed is implemented; the one staged piece (detail detector) is
inert until a variant is approved.

---

## P3 — "invalid STT must NEVER reach the LLM; deterministic clarify only"
**This is the one I push back on hardest. Implementing it literally would
have made THIS session worse, and it contradicts your own 181237 directive.**

The evidence chain:
1. Your 181237 directive defined the contract: *"recoverable/meaningful
   transcript → allow fused LLM contextual recovery."* We shipped exactly
   that (`transcript_router.py`): invalid-but-meaningful (≥4 words, logprob
   ≥ −0.5) → recovery; unusable short garble → clarify.
2. Turns 15/16/20 of this session are that path working:
   - t15: 'कि और क्या क्या कंपनेंट होते हैं' — 7 words, a real question
   - t16: 'कौन-कौन से चीजों के ऊपर धिखान रखना पड़ता है...' — 11 words
   - t20: 'नहीं है मेरी बात को समझो मुझे डिटेल का मतलब है' — 10 words
   All three were rejected ONLY by `high_no_speech_prob` — the least
   reliable number in the pipeline, which false-fired 3× in one session on
   clearly-spoken sentences. The LLM answered them correctly. You continued
   the conversation happily for 13 more turns. **No wrong recovery occurred
   in the entire session.**
3. Literal P3 would have converted those three turns into "phir se bol na"
   clarification loops — mid-detail-mode. That is precisely the failure that
   makes voice agents feel stupid, and it directly breaks your P1 (detail
   mode with checkpoint continuation requires tolerating brief garbled
   follow-ups; hard-gating turns every one into a clarify request).
4. The problems you actually flagged in 15/16/20 were LENGTH (10-15s
   monologues) — that's P1/P2 territory, not routing. Killing recovery
   doesn't shorten answers; the length fix does.

**The legitimate kernel in P3:** a wrong recovery gives a confident wrong
answer. My synthesis — bounded blast radius instead of a ban:
- Recovery replies are ALWAYS checkpoint-short (one chunk + "sahi? aage
  bataun?") regardless of mode → a wrong recovery costs ~3s, never 13s.
- Recovery turns stay loudly instrumented (CONTEXTUAL_RECOVERY event,
  `route_action` field, counted in health) so the rate is measurable.
- `AIVA_STRICT_STT_GATE=1` env flag implements your literal version for a
  live A/B if you want the comparison. My prediction, falsifiable: strict
  gate produces more clarification loops and worse detail-mode flow.

**Rule I propose: gate on meaningfulness, not on the validity flag.**
Validity flags are evidence, not verdicts.

---

## P1/P2 — detail mode + chunking. Agree with the goal; correcting the mechanism.

"Small conversational chunks with natural checkpoints" in a VOICE product
means one thing structurally: **the model must END ITS TURN at each
checkpoint and yield the floor.** There is no TTS-side trick that converts a
14s generated monologue into a conversation — slicing the audio still
delivers 14s of speech and wastes synthesis on unheard chunks while making
barge-in messier. So P2 collapses into P1's real mechanism:

- Detail mode (detected from explicit asks: "detail mein samjhao", "poora
  batao", "ek-ek point") → persona instructs: deliver ONE chunk (≤110 chars,
  ~5-6s max) ending at a natural checkpoint; the user's "haan / aage / phir?"
  continues to the next chunk. Continuation state rides the existing context
  (the model sees its own last chunk in history) — zero new LLM calls, zero
  new pipeline.
- Cap becomes mode-dependent: `cap_for(detail)=110` vs `cap_for(normal)=240`.
  Session 192439's 9.8–14.2s monologues become structurally impossible.
- Staged already (inert until wired): `is_detail_request()` + `cap_for()` in
  reply_guard.py.

---

## P4 — interruption statefulness. Core shipped last round; one real gap.

Proof it works (from your own paste): `response states: {'UNHEARD': 1,
'FULLY_PLAYED': 20, 'PARTIALLY_PLAYED': 7}` — every response is classified,
the next call receives reconciliation, persona V1.9 handles it.

**Your correct catch:** the unspoken REMAINDER is not retained. Adding:
PARTIALLY_PLAYED payloads will carry `remaining_text` (capped) alongside
heard_text, so the model can resume precisely instead of re-deriving.

One nuance your directive misses: t2→t3's verbatim repeat was arguably
CORRECT — t3 you asked "which topic were we on?", so naming it again was the
answer. This is why I'm against hard verbatim suppression (point 5): the
model needs to know WHAT was heard and WHY it's restating, not be blindly
blocked.

## P5 — repetition. Agree; mechanism correction.

The t11→t13 verbatim repeat slipped because the tripwire window was
consecutive-only (t12 intervened). Widening to last-3 responses as DETECTION
is fine. But the fix layer should be the reconciliation payload + persona
("user already heard X; do not repeat X; answer the new question"), not a
deterministic similarity suppressor — which, as t2→t3 shows, would
sometimes suppress legitimate answers.

## P6 — topic continuity. DEFER with evidence.

This session's recovery worked emergently (t7: "voice agent ki baat ho rahi
thi na? haan yaad aa gaya"; t8 continues). A formal topic tracker needs
either LLM calls (banned by your constraint) or fragile keyword heuristics.
History + Layer-2 active_topic already carry the thread. Defer until a real
recovery failure is on record.

## P7 — VAD smoothing. Already shipped last round.

Hard-cut hangover (+250ms, conditional) + natural decay unchanged; zero
fragmentation complaints this session; `energy_profile` telemetry now
accumulating to verify.

## P8 — latency. Provider decision, still yours.

TTFA floor is Fish's. Code cannot go below it; failover only prevents
silence. Fish paid tier / ElevenLabs remains the lever.

---

## Proposed synthesis (what I'll implement on your go)

1. **P1/P2:** detail mode = one chunk per turn + checkpoint endings;
   `cap_for(detail)=110`; continuation via context. (Detector staged.)
2. **P3 (synthesis, not literal):** recovery stays meaningfulness-gated;
   recovery replies always checkpoint-short; loud per-turn instrumentation;
   `AIVA_STRICT_STT_GATE=1` env flag for the literal variant → live A/B.
3. **P4:** payload gains `remaining_text` (PARTIAL only).
4. **P5:** payload gains explicit heard/do-not-repeat text; detection window
   widened to last 3 replies (detection only, no suppression).
5. **P6:** deferred with evidence. **P7:** done. **P8:** your call.

**One decision needed from you:** P3 — my synthesis (meaningfulness gate +
short recovery + optional strict A/B flag), or your literal hard gate? My
strong recommendation is the synthesis; the hard gate would have failed this
very session's best conversation.
