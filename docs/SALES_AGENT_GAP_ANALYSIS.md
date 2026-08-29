# Sales Voice AI Agent — Gap Analysis (SquadStack-style product)

**Date:** 2026-08-29 · **Question:** "If we release this as a sales voice AI agent like
SquadStack — what's still needed, where are we, what needs improvement?"

**SquadStack's model, concretely:** AI agents making **outbound phone calls (PSTN)** to
leads — pitch, qualify, handle objections, book appointments/demos — with per-call
analytics, human escalation, and outcome-based reporting at scale (thousands of
concurrent calls).

---

## 1. Where we are (one paragraph, honest)

We have **proven the hardest 20%**: a real-time, sub-2.5s, barge-in-capable,
emotionally-stable Hinglish voice loop with per-user memory, deterministic state
tracking, safety gates, and exceptional observability — running on free tiers. What we
have NOT built is everything that turns a conversation engine into a phone-based sales
product: telephony, a sales brain, compliance, scale infrastructure, and outcome
analytics. As a **companion product**, we're ~85% of MVP. As a **sales product**, we're
at roughly **35–40%** — but the 35–40% we have is the part that takes the longest to
get right, which is why the engine-first path was correct.

## 2. What transfers as-is (evidence-backed)

| Asset | Status | Evidence |
|---|---|---|
| Realtime loop (STT→LLM→TTS streaming) | ✅ works | speech→audio avg 2.0–2.4s, 0 errors across 5 sessions |
| Turn-taking (endpointing, barge-in, WAIT/suppress, backchannel) | ✅ works | session 094645/103824 telemetry |
| Deterministic state updater + perception heads | ✅ works | heads 20–21/22 last 2 sessions |
| Per-user persistent memory (SQLite, device-scoped) | ✅ works | mem=21 restored, cross-session recall |
| Degradation paths D1–D9 + recovery behavior | ✅ works | cooldown exit, clarify paths |
| Observability (per-turn telemetry, stage diagnostic) | ✅ strong | this is genuinely better than most MVPs |
| Hinglish/Hindi/English handling | ✅ works | `hi` pin, register mirroring |
| Free-tier cost structure | ✅ (validation only) | — |
| Incident discipline (evidence → fix → test) | ✅ proven | 6 rounds in 2 days |

## 3. Gap matrix

### A. Telephony — **the biggest gap, entirely net-new (L)**
We run on LiveKit over **WebRTC (browser app)**. Sales calls happen on **phone
numbers**. Needed: SIP/PSTN integration (Twilio / Exotel / Knowlarity / LiveKit
Telephony), number provisioning, inbound+outbound call handling, call recording,
ring/no-answer/voicemail detection, call transfer to humans. **Effort: 2–4 weeks.**
Nothing in the conversation engine changes — this is a transport layer around it.

### B. The sales brain — persona + policy + playbook (M–L)
Today's brain is emotion-first (VENT mode, encourage_continuation). A sales agent needs:
- **Sales persona & playbook prompts**: discovery → pitch → objection handling → close;
  objection library; competitor handling; pricing talk tracks
- **Lead context injection**: name, product interest, past interactions from CRM → our
  memory bridge already has the shape for this (owner-keyed context) but the source
  becomes CRM/webhook, not conversation
- **Stage machine**: policy derivation currently tracks emotion/phase; sales needs
  `stage: intro|discover|pitch|objection|close|callback` with deterministic transitions
  — our updater architecture extends directly (add dimensions via the locked
  amendment process)
- **Goal-state persistence across calls**: "call back Thursday" → next-call context
  (memory store already supports this)
**Effort: 1–2 weeks of prompt/policy work + evaluations (we already have the eval
harness pattern).**

### C. Knowledge grounding / RAG (M)
Sales claims must be accurate (product specs, pricing, offers). Currently: memory-only,
no product KB. Needed: retrieval into the fused call's context (the `build_contents`
payload has a slot-ready shape). Hallucinated pricing = legal/brand risk.
**Effort: 1 week.**

### D. Compliance — India (M, non-negotiable)
- **TRAI telemarketing rules**: DND/NDNC scrubbing, permitted calling hours (9am–9pm),
  consent management, header/registration requirements for promotional calls
- **Call recording disclosure** ("this call may be recorded")
- **DPDP Act 2023**: purpose limitation, consent, data retention/deletion — our memory
  store has 90-day purge (U2) but needs policy-level review for PII in conversations
- **Escalation & abort rules**: "remove me from your list" → deterministic compliance
  path (our deterministic-first architecture is actually an advantage here)
**Effort: 1–2 weeks (mostly process + config, some code).**

### E. Latency tier (M — we know exactly where the ms are)
Sales tolerates ~1.5s speech→audio; we're at 2.0–2.4s avg, 4s tail. Levers, in order:
1. **TTS TTFA** (Fish ~0.6–1.0s after text): faster Fish tier or ElevenLabs Flash
   (~75ms) — **owner decision + voice evaluation required**
2. **LLM TTFT** (~1.0–1.2s on flash-lite): Gemini Flash standard / other providers
   (Deepgram+GPT-4o-mini-class stacks run ~300–500ms TTFT) — provider decision
3. **STT** (Groq ~300–500ms batch): streaming STT (Deepgram ~150ms interim) enables
   earlier LLM start — the "anticipatory pipeline" item already designed
4. Barge-in cancel at VAD (+400ms confirm hybrid) — designed, awaiting owner call
**Effort: each is a provider/config decision; combined 1–2 weeks incl. evaluation.**

### F. Scale & ops (L)
- **Quota math (free tiers)**: ~500 RPD/key × 3 keys ÷ ~100 LLM turns/call ≈ **~15
  calls/day** — fine for validation, needs paid tiers or more keys for pilots (a
  50-call/day pilot ≈ ₹ modest monthly on Groq+Gemini paid; Fish/ElevenLabs extra)
- **Concurrency**: single worker per room today; LiveKit agents support horizontal
  workers — needs load testing, worker autoscaling, Redis-free state check (our state
  is per-call in-memory + per-owner SQLite → already horizontally clean)
- **Central observability**: logs are local files; pilots need aggregated dashboards +
  alerting (per-call SLOs: error rate, latency percentiles, escalation rate)
**Effort: 2–3 weeks to pilot-grade.**

### G. Voice quality (M — ties to the spoken-words question)
The "doesn't sound real" issue decomposes into: merged-word text glitches (fixed,
`smart_join`), Fish prosody on romanized Hinglish (test Fish text-normalization /
Devanagari input comparison), and tier (see E1). A sales product's voice must inspire
confidence — this is a gating quality bar. **Effort: evaluation + tuning, 1 week.**

### H. Outcome analytics (M)
SquadStack sells **outcomes** (appointments, conversions), not conversations. Needed:
session-end summary call (one LLM call: outcome, lead score, next action — our heads
+state make this a small feature), outcome dashboards, A/B prompt experimentation,
per-call transcript+recording retention. **Effort: 1–2 weeks.**

## 4. Phased path (proposal — owner decides)

| Phase | Scope | Exit criteria | Rough effort |
|---|---|---|---|
| P-A: Pilot-ready loop | AIVA_TTS_DUMP voice audit pass; latency levers 1+2; session-end summary; paid quotas | 20-call internal pilot on browser, SLOs met | 1–2 wks |
| P-B: Telephony | SIP/PSTN via Exotel/Twilio; recording + disclosure; voicemail detect | 10 real phone calls, end-to-end | 2–4 wks |
| P-C: Sales brain | Playbook prompts + stage machine + lead-context injection + KB/RAG | Role-play script passes; objection handling eval | 1–2 wks |
| P-D: Compliance + scale | TRAI/DND/DPDP review; worker scaling; central logs; outcome dashboard | 100-call pilot, metrics live | 2–3 wks |

**Total to a defensible pilot: ~6–10 weeks of focused work** — with the caveat that
telephony (P-B) is the schedule risk and compliance (P-D) is the launch risk.

## 5. Key decisions the owner must make (in order)

1. **Voice provider**: stay Fish (current clone) vs ElevenLabs Flash vs faster Fish
   tier — quality/cost/latency trade (use the new `tts_audit.py` MOS+round-trip data)
2. **Telephony vendor**: Exotel (India-native, TRAI-handling) vs Twilio (global,
   more code) — depends on GTM geography
3. **LLM tier for sales**: free flash-lite (validation) vs paid standard tier (pilot)
4. **Scope of MVP**: one vertical/script (e.g., appointment-setting for one industry)
   — vertical-first is how SquadStack-class products launch
5. **Compliance ownership**: a human/process owner for TRAI + DPDP (agent can build
   the technical pieces, but accountability is a business decision)

## 6. What NOT to do (scope discipline)

- Don't rebuild the engine for sales — the heads/updater/policy architecture extends;
  it was designed for this exact extension (adds, not rewrites)
- Don't launch sales calls without TRAI compliance — one DND violation can kill the
  number/business
- Don't chase <1s latency before pilot data says it matters — 1.5–2s converts fine on
  warm outbound leads; obsess over *reliability* and *outcome tracking* first
