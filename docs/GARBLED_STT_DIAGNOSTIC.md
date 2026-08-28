# Diagnostic Report — "Thoda Cut Gaya" Recovery Behavior
**Date:** 2026-08-27 · **Mode:** investigation only — no code changed.

---

## A. Root Cause

There are **THREE separate code paths** that produce "thoda cut gaya"-type responses:

### Path 1 — Deterministic `unclear_speech` routing (main.py)
When STT confidence is poor (logprob < −0.85, no_speech > 0.6, or repetition-loop detected), the turn is classified `unclear_speech` **before the LLM ever sees it**. The system speaks a hardcoded line from `CLARIFY_LINES`:
- "haan? ek baar phir bol."
- "yeh wala part miss ho gaya — phir se bol na."
- "sun nahi paya, dobara bol na."

**The LLM never sees these transcripts.** No semantic recovery is possible.

### Path 2 — Ultra-short fragment routing (main.py)
When a *valid-confidence* transcript has ≤2 words and <1.2s audio, it's also routed to `unclear_speech` — same deterministic lines. **Again, the LLM never sees it.**

### Path 3 — The LLM persona instruction (prompt_fragments.py)
When the transcript DOES reach the fused LLM (i.e., it passed confidence gates), the persona tells the model:
> "If you did not catch something, say so naturally ('yeh wala thoda cut gaya, phir se')"

So even when the LLM **could** infer meaning from context + a partially garbled transcript, the prompt instructs it to declare the input unclear instead.

### Why "thoda cut gaya" dominates

The combination of these three paths means:
1. **Any** short utterance (<1.2s, <2 words) → deterministic clarify (Path 2) — LLM never sees it
2. **Any** low-confidence utterance → deterministic clarify (Path 1) — LLM never sees it
3. **Any** utterance the LLM can't fully understand → persona says "ask to repeat" (Path 3) — even if partial recovery is possible

The result: the system **never attempts semantic recovery** because every layer is designed to *give up* rather than *try*.

---

## B. Case-by-Case Analysis

| Transcript | STT Confidence | Reached LLM? | Current Path | Recoverability | Ideal Response |
|---|---|---|---|---|---|
| `कि यह पिछल चल रही है क्या कोई शीन नहीं तुम बना रहा है क्या आज अच्छे बनने में अ` | unknown | **YES** (passed gates) | LLM persona (Path 3) | **RECOVERABLE** — "पिछल" ≈ "पिछले", context = talking about something ongoing | "Achha, toh pichle dinodon se ye chal raha hai? Kya hua exactly?" |
| `आप तरह यह बता रक्षा बनना ना` | unknown | unclear | Likely Path 1/2 | **RECOVERABLE** — "रक्षा बनना" ≈ context about Rakhi; "बता रहा" = telling | "Rakhi ke baare mein bata rahe ho na?" |
| `आयो रक्षा बनना ना` | unknown | unclear | Likely Path 1/2 | **RECOVERABLE** — "रक्षा" = Rakhi (the festival) | "Rakhi ki baat kar rahe ho?" |
| `कि अब राखिमाल से टैक करते पहन लो` | unknown | unclear | Likely Path 1 | **RECOVERABLE** — "राखिमाल" ≈ Rakhi + something; "टैक" ≈ टेक (tech) | "Rakhi pe tech gift dena hai?" |
| `बिलाइगला` | unknown | unclear | Likely Path 1/2 | **UNRECOVERABLE** — single nonsense word | "Ye word clear nahi aaya — kya bola?" |
| `बार?` | unknown | unclear | Likely Path 2 | **SHORT but MEANINGFUL** — could be "baat?" or "bar?" | "Haan? Kya baat?" |
| `इस वर्ड नेम` | unknown | unclear | Likely Path 1/2 | **RECOVERABLE** — "word name" in Hinglish = asking about a name | "Kaunsa naam?" |
| `और और लाइन से मेरे पास ब्लैंकेट से` | unknown | unclear | Likely Path 1 | **RECOVERABLE** — "online" ≈ "लाइन से"; "ब्लैंकेट" = blanket; context = online shopping | "Online blanket mangwana hai?" |
| `कि मैं गरीब ब्लैंकेट से अपने पॉजिश आप` | unknown | unclear | Likely Path 1 | **PARTIALLY RECOVERABLE** — blanket + position, but unclear intent | "Blanket ke baare mein bol rahe ho?" |
| `ब्रैंकिट से ला देगा` | unknown | unclear | Likely Path 1 | **RECOVERABLE** — "ब्रैंकिट" ≈ Blinkit; "ला देगा" = will bring | "Blinkit se mangwa dein?" |

**Key observation:** 7 out of 10 cases are **semantically recoverable** — the transcripts contain recognizable concepts (Rakhi, blanket, online, tech, name) that a human would immediately understand from context. Only 1 is truly unrecoverable.

---

## C. Recoverability Model

| Level | Definition | STT Signal | Response |
|---|---|---|---|
| **ACCEPT** | Transcript is clear and complete | High confidence, normal length | Normal fused LLM response |
| **RECOVERABLE_UNCLEAR** | Partially garbled but recognizable concepts present | Medium confidence OR short OR some garbled words mixed with recognizable ones | LLM attempts semantic recovery using conversation context; reflects interpretation conversationally |
| **UNRECOVERABLE** | No recognizable concepts; pure noise or single nonsense word | Very low confidence OR single token with no semantic content | Targeted clarification ("ye word clear nahi aaya") |
| **DROP** | Hallucination, echo, punctuation-only, no-speech | Known patterns | Deterministic silence |

**The key insight:** the LLM already has everything needed to make this classification — it sees the transcript, the conversation history, and the memory. It doesn't need a separate recovery LLM call.

---

## D. Where the Decision Belongs

**In the existing fused LLM call.** No new LLM call needed.

The current prompt tells the model: "if you didn't catch something, say so."

**Change to:** "If you can partially understand from context, reflect your interpretation conversationally. If you truly can't understand anything, ask naturally."

This is a **persona prompt change**, not an architecture change. The perception head already has an `emotion.confidence` field that correlates with how well the model understood the input.

Additionally, the `unclear_speech` deterministic routing should be **narrowed** to only fire for truly unrecoverable input (single nonsense word, empty after cleanup, extreme confidence failure) — not for short or medium-confidence turns that the LLM might understand.

---

## E. Existing LLM Reuse

**The fused Gemini call can perform semantic recovery without any additional LLM call.**

Evidence: in the Task 1 validation, the model demonstrated it can:
- Detect emotion from garbled Hinglish ("anger_frustration" from partial text)
- Identify thread topics from imperfect input
- Generate contextually appropriate responses

The model has:
- The garbled transcript
- Last 6 turns of conversation history
- Memory (topics, people, preferences)
- Thread summaries

A human conversational partner uses exactly these same signals to recover meaning from partially heard speech. The model is capable of it — the prompt just doesn't ask it to try.

---

## F. Example Outputs

| # | STT Transcript | Classification | Inferred Intent | Ideal Aiva Response |
|---|---|---|---|---|
| 1 | `आज राखी का दिन है ना` | **ACCEPT** | Asking about Rakhi festival | "Haan, Rakhi hai aaj. Kya plan hai?" |
| 2 | `और और लाइन से मेरे पास ब्लैंकेट से` | **RECOVERABLE_UNCLEAR** | Talking about buying blanket online | "Online blanket mangwana hai? Kaunsi dekhi?" |
| 3 | `ब्रैंकिट से ला देगा` | **RECOVERABLE_UNCLEAR** | Blinkit will deliver something | "Blinkit se mangwa dein? Kya chahiye?" |
| 4 | `कि अब राखिमाल से टैक करते पहन लो` | **RECOVERABLE_UNCLEAR** | Something about Rakhi and tech | "Rakhi pe tech gift ki baat kar rahe ho?" |
| 5 | `बिलाइगला` | **UNRECOVERABLE** | Unknown single word | "Ye word clear nahi aaya — kya bola tha?" |
| 6 | `हाँ` | **ACCEPT** (short but complete) | Acknowledgment | Backchannel or continue naturally |
| 7 | `आप तरह यह बता रक्षा बनना ना` | **RECOVERABLE_UNCLEAR** | Telling about Rakhi | "Rakhi ki baat kar rahe ho na? Batao." |
| 8 | `मुझे रोटी मेकर चाहिए` | **ACCEPT** | Wants a roti maker | "Achha, roti maker? Kaunsi wali dekh rahe ho?" |
| 9 | `इस वर्ड नेम` | **RECOVERABLE_UNCLEAR** | Asking about a name | "Kaunsa naam? Kiska?" |
| 10 | `क्या कर रहे हो` | **ACCEPT** | Standard greeting question | Normal conversational response |

---

## G. Metrics

| Metric | How to measure | Target |
|---|---|---|
| **False clarification rate** | Understandable turns that got "thoda cut gaya" / total understandable turns | < 10% |
| **Recovery rate** | Unclear turns where Aiva correctly inferred meaning / total unclear turns | > 50% |
| **False interpretation rate** | Aiva guessed wrong and user corrected / total recovery attempts | < 15% |
| **Clarification quality** | Targeted (mentions what was heard) vs generic ("thoda cut gaya") | > 70% targeted |
| **Latency delta** | New avg response latency − old avg | < 100ms increase |
| **LLM cost delta** | Same (no additional calls — just prompt change) | 0 |

---

## H. Minimal Implementation Plan (NOT YET — awaiting approval)

1. **Persona prompt update** (TRANSPORT_V1.3): Replace "if you didn't catch something, ask to repeat" with "if you can partially understand, reflect your interpretation conversationally ('tu blanket ki baat kar raha hai na?') and let them correct you. If truly nothing is understandable, ask naturally."
2. **Narrow `unclear_speech` routing**: Only fire for truly unrecoverable input (empty, single nonsense token, extreme confidence failure). Short/medium-confidence turns go to the LLM.
3. **Optional**: Add a `recoverability` field to the perception head (`"recoverability": "full|partial|unclear"`) so the updater can track recovery rates.
4. **Evaluation set**: Build the 10-item dataset from §F and measure before/after.

**No architecture change. No new LLM call. No new state dimension. Persona prompt + routing scope change only.**

---

## The Core Principle

> **Don't teach Aiva to say "I didn't hear you." Teach Aiva to determine whether it actually heard enough to understand you — and if so, to say what it understood and let the user correct it.**

This is how humans converse. We don't say "I didn't hear you" every time someone mumbles — we infer from context and check our understanding.
