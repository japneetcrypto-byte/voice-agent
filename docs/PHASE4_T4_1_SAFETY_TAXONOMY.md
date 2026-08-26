# T4.1 — Safety Taxonomy Investigation (LOCKED by owner — 2026-08-26)

**Status:** TAXONOMY ADOPTED (owner approval). G16/G17 provisional flags lifted; D-C authoring unblocked.

**Date:** 2026-08-26 · **Research-only task** (green-lit D-4b). No code, architecture, provider, or production changes.
**Outcome sought:** a proposed safety taxonomy + signal lexicon + escalation script for the locked v1.1 scaffold, and the D-C authoring guide. **Nothing here is implemented; G16/G17 stay provisional until you lock this taxonomy.**

---

## 1. Findings from the literature (what evidence supports)

- **[FACT]** Standard practice grades suicidal-risk screening on **escalating ideation-severity levels** — the Columbia–Suicide Severity Rating Scale (C-SSRS) is the established plain-language screening structure, and a 2025 Nature study used C-SSRS-derived escalating prompt sets to grade AI chatbots' safety responses (Nature s41598-025-17242-4). Notably: **none of the tested mental-health chatbots met adequate-response criteria** — our recall-first design is justified.
- **[FACT]** A clinically-informed evaluation framework (SciDirect S2772598726000206) defines response quality in 3 dimensions: **connect & listen → assess risk → explore coping** — maps 1:1 onto our policy layers (VENT acknowledgment → safety screen → advice-gating).
- **[FACT]** An LLM red-team pilot (EA Forum, 2025-10) found Gemini-class models **miss indirect/passive ideation** while handling explicit statements well — and recommends exactly our architecture: **rule-based safety net + LLM relational support**.
- **[FINDING]** Our two-tier design (explicit → immediate `high_risk`; ambiguous → clarify-first `elevated_distress`) is consistent with all three sources. The documented gap (v1.1): indirect ideation detection remains the weak point → mitigated by lexicon breadth (below) + Phase-4 labeled set.

## 2. Verified India crisis resources (voice-only delivery, O1)

| Resource | Number | Hours | Role for Aiva |
|---|---|---|---|
| **Tele-MANAS (Govt of India, MoHFW)** | **14416** (also 1800-891-4416) | 24×7, multilingual, toll-free | **PRIMARY spoken resource** — 5 digits, uniquely speakable/memorable; govt-backed nationwide |
| **KIRAN (MoSJE)** | 1800-599-0019 | 24×7, multilingual | secondary mention |
| **Vandrevala Foundation** | +91-9999666555 (call/WhatsApp) | 24×7 | tertiary (WhatsApp option useful for silent users) |
| AASRA (NGO) | 9820466728 | 24×7 | reference list only |

Sources: depwd.gov.in official helpline listing; telemanas.mohfw.gov.in; Amaha/patientsengage compilations (numbers cross-checked across ≥2 sources each — **re-verify at implementation time; numbers drift**).
**[FINDING]** For a *spoken* MVP: say **one** resource ("Tele-MANAS helpline — one four four one six, free, twenty-four by seven") + "kisi bharose wale insaan se abhi baat karo". Never recite a list.

## 3. Proposed taxonomy (maps 1:1 onto the locked 4-level scaffold — no schema change)

| risk_level | Definition (proposed) | Behavioral contract |
|---|---|---|
| `none` | Normal venting; no distress markers beyond topic frustration | standard VENT policy |
| `low` | Generic negativity/self-deprecation without despair ("main bekaar hu") | standard VENT; nothing special |
| `elevated_distress` | Sustained strong despair/crying cues/hopelessness **without self-harm content**; OR **ambiguous** self-harm-adjacent phrase (single ambiguous hit, e.g. "sab khatam kar dunga" with no clearer context) | **clarify-first tier**: gentle check-in ("tum theek ho? main yahin hoon"), increased presence, no advice, no interrogation; resource mention only if sustained |
| `high_risk` | **Explicit/implicit suicidal ideation or self-harm intent** (clear statements or ≥1 unambiguous lexicon hit), OR explicit threat/harm toward another person | **fixed supportive response**: presence + worth + spoken resource (§2) + encourage contacting now; zero advice; no secrecy promises; repeat calmly every turn |

Categories unchanged (`self_harm`, `harm_to_others`, `other_flagged` per locked contracts).

### Signal lexicon (starter — English + Roman-Hinglish; the D-C set tests it, not this list verbatim)

- **Unambiguous → `high_risk`:** "kill myself" / "end my life" / "want to die" / "suicide" · "khudkushi" · "marna chahta/chahti hoon" · "jeene ka man nahi hai" (combined with despair) · "khud ko nuksan pahunchana" · "sab khatam kar dunga/jaungi" **when combined with despair/hopelessness context**
- **Ambiguous → clarify-first `elevated_distress`:** single isolated "sab khatam kar dunga" / "gaya bhad mein" about a *situation* with no self-direction · "bas ab nahi sah pa raha" · "himmat tut gayi" · crying cues ("ro raha/ro nahi raha") · "neend nahi aa rahi dinon se" · "sab chhod ke kahin chala jaun"
- **Figurative hard-negatives (must NOT escalate):** "kaam mujhe kha ja raha hai" · "maar daalega yeh deadline" · "pagal kar dega yeh internet" · "goli dena padega is router ko"
- **Third-party disclosures:** "mera dost marna chahta hai" → `other_flagged=true`, level per severity (usually `elevated_distress`); respond with care + encourage the user to help them reach support; **documented limitation: Aiva cannot contact anyone.**
- **Minors:** no age gate exists in the system (v1 limitation, recorded). Tele-MANAS/KIRAN serve all ages; no adult-specific resources in the spoken list. Revisit as product decision.

### Deterministic rules (updater-owned; the LLM classifies, these rules fire)

1. Any `self_harm=true` or `harm_to_others=true` from the head → `high_risk` regardless of head confidence (recall-first).
2. `risk_level=high_risk` from head → override as-is.
3. Ambiguous-lexicon hit with head risk < elevated → `elevated_distress` + clarify-first flag (`SAFE-CLARIFY`).
4. De-escalation only after 3 consecutive `none/low` turns (SAFE-HYSTERESIS, locked).
5. **Never** map an invalid/garbage safety enum to `none` (locked D3: `low` + `other_flagged`).

## 4. Escalation script skeleton (spoken; O1 voice-only) — `high_risk` template

1. **Presence:** "Main yahin hoon, tumhare saath hoon. Sun raha hoon."
2. **Worth:** "Yeh dard bahut zyada hai — aur tumhare akele jhelne ki zaroorat nahi."
3. **Resource (once per turn, calmly):** "Abhi, abhi kisi bharose wale insaan se baat karo. Aur Tele-MANAS helpline — ek chaar chaar ek solah — free hai, din raat chalti hai. Wahan trained log hain."
4. **One gentle question max:** "Abhi kisi ko phone kar sakte ho?"
- Never: advice, diagnosis, guilt ("soch lo tumhari maa ka"), secrecy promises, long lists.
- Across turns: repeat calmly with varied phrasing; never audit "did you call?"

## 5. D-C safety dataset authoring guide (for T4.2/T4.3)

- 100–120 items, ≥40% Hinglish, using C-SSRS-style **escalating severity ladders** (same-turn variants: euphemistic → explicit) per the Nature methodology:
  - explicit ideation/self-harm: 20 · implicit/euphemistic ideation: 15 · **figurative hard-negatives: 25** · elevated-distress-no-risk: 20 · third-party: 10 · normal venting hard-negatives: 20
- Every item labeled `risk_level` + category + `clarify_first_expected` flag; 3 raters; disagreement adjudicated.
- Items are **synthetic/consented only**; never harvested real crises.

## 6. Requires owner lock

1. Adopt proposed taxonomy definitions + lexicon approach (§3) → then G16/G17 provisional flags lift.
2. Adopt Tele-MANAS-14416 as primary spoken resource (§2) — wording final at implementation (per U1).
3. Note (amendment proposal, needs your approval — **not applied to locked contracts**): optional head field `correction {present, about}` so the LLM flags user-corrections and the updater applies CORR-OVERRIDE deterministically — avoids hidden interpretation in the updater (alternative would violate the locked interpretation boundary).

## 7. Sources

- depwd.gov.in official helpline list (Tele-MANAS 14416; KIRAN 1800-599-0019; Vandrevala 9999 666 555; AASRA 9820466728)
- telemanas.mohfw.gov.in (14416 / 1800-891-4416, 24×7)
- Nature Sci Reports s41598-025-17242-4 (C-SSRS-graded chatbot safety evaluation; adequacy failures justify recall-first)
- ScienceDirect S2772598726000206 (connect-listen/assess/coping response framework)
- EA Forum LLM suicide-risk detection pilot (indirect-ideation blind spot; hybrid rule+LLM design)
