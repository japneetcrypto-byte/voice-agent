# LLM/API Integration Audit (Phase 3 pre-validation)

**Date:** 2026-08-26 · **Scope:** audit only — no architecture changes, no provider switch. Labels per protocol.

---

## 1. Current model

- **[FACT]** Provider/SDK: **Google Gemini via `google-genai`** Python SDK (`from google import genai`).
- **[FACT]** Model: **`gemini-3.5-flash-lite`** — hardcoded default in `GeminiLLM.__init__` (`providers/llm.py`); no env override exists (minor finding: model name should become env-configurable in Phase 3 contracts to enable a fallback ladder).
- **[FACT]** Call shape: `client.aio.models.generate_content_stream(...)`, temperature 0.7, system via `system_instruction`, **no max_output_tokens set**, usage metadata not captured in production.
- **[FACT]** Key: `GEMINI_API_KEY` via `agent/config.py` ← `.env`.
- **[FACT]** Same model for perception + response: there is **no perception call in production today** (one response call only). The fused prototype uses the same single model for head + response in one call — so yes, one model does both, in one request.

## 2. Call pattern per user turn

| Stage | Today (production) | After fused change (if validated) | Notes |
|---|---|---|---|
| **LLM (Gemini)** | **1** streaming call | **still 1** (that is the point of fusion) | serial fallback design = 2 |
| **STT (Groq whisper-large-v3-turbo)** | **1 per speech segment** | unchanged | rejected turns (echo/noise) also consume STT — they happen *before* validity filters |
| **TTS (Fish Audio hosted)** | 1 streaming WS session per response (+ EdgeTTS fallback call if Fish fails) | unchanged | Fish API credit is separate from platform credit `[live]` |
| **Deterministic ops (VAD, energy gate, echo filter, validity gates, metrics, and the future state updater)** | **0 API calls** | **0 API calls** | **Confirmed: the deterministic state updater requires zero LLM calls by design** (locked model P1 — it consumes the perception JSON; it never calls a model) |
| LiveKit room create (token server) | 1 per page load | unchanged | not per turn |

**Phase 3 validation workload:** 60 calls per probe sweep (10 cases × 3 runs × 2 arms) + dev iteration. Per-turn token estimate (fused): ~1–1.5K prompt + ~200–350 completion ≈ **~1.5–2K tokens/turn**.

## 3. Free-tier limits (documented — aggregator sources vary slightly; verify exact numbers in your Google/Groq consoles)

**Gemini free tier (flash/flash-lite class)** — sources converge on:
- **RPM: ~15–30** (rolling window, per model, per project)
- **TPM: ~250K–1M**
- **RPD: ~1,000–1,500**, resets midnight Pacific
- 429 on exceed; no SLA; free-tier data may be used for training; per-minute limits are a *rolling* window (bursts hurt)
- Sources: tokenmix.ai (1,500 RPD / 15 RPM / 1M TPM) · aifreeapi.com (flash-lite 15 RPM / 1,000 RPD / 250K TPM) · tinkerllm.com (15 RPM / 1,500 RPD, rolling-window behavior)

**Groq free tier — STT (our current STT):** `whisper-large-v3-turbo`: **20 RPM, 2,000 RPD, 7,200 audio-sec/hour, 28,800 audio-sec/day (~8 h audio/day)** (console.groq.com/docs/rate-limits via apio.sh).

**Groq free tier — LLM (alternative candidate):** llama-3.3-70b: 30 RPM / **1,000 RPD / 12K TPM**; llama-3.1-8b-instant: 30 RPM / 14,400 RPD / 6K TPM. **TPM caps of 6–12K/min are the binding constraint** (~3–6 of our turns/min); limits are per-organization, not per-key.

**DeepSeek:** **no permanent free tier**; V4-Flash ~$0.22/$0.66 per 1M in/out; peak-hour limits reportedly tighter.

**Implication math (our workload):** TPM never binds (2K/turn vs 250K+/min). RPM binds only above ~15–30 turns/min (a real conversation runs ~4–6/min — headroom ~3–5×). **RPD is the real ceiling: ~10–15 venting sessions/day** (60–100 turns each) — comfortable for personal/portfolio MVP, not for multi-user.

## 4. What happens when a limit is hit today

- **[FACT]** `GeminiLLM.generate_response_stream` has **no try/except, no retry, no timeout config**. A 429/quota/timeout exception propagates to `transcribe_and_respond`'s generic handler (`except Exception` → `"Pipeline Error"` print) → the turn is logged (`finally: log_turn`) and **dropped**.
- **[FACT]** Net behavior: **the worker survives and the next user turn works — but the failed turn gets silence** (no audio, no user-facing feedback, no retry). The conversation does not crash; it degrades one turn at a time.
- **[FACT]** STT path: identical pattern (no retry; turn dropped on Groq error).
- **[FACT]** TTS path: Fish failure → per-turn EdgeTTS fallback (works); Edge failure → dropped turn, same as above.
- **[FACT]** There is **no distinction anywhere between transient 429 and exhausted daily quota**.
- **[FACT]** Malformed model output cannot crash production today (raw text is streamed straight to TTS); the fused prototype counts head-validity failures instead.
- **[ASSUMPTION]** The `google-genai` SDK may apply limited internal retries for some error classes — unverified for streaming calls; the probe run will surface real 429 behavior.

## 5. Graceful recovery — what is possible within the current architecture (assessment only; not implemented)

| Capability | Feasible? | Where it lives |
|---|---|---|
| Retry with exponential backoff | **Yes** — single call site in `providers/llm.py` | provider layer |
| Bounded attempts (2–3) | **Yes** | same |
| No duplicate responses during retries | **Yes — with one rule:** retry only if **zero tokens streamed**; after first token, never restart (speech already began) — degrade instead. The existing `run_agent_response` guard already prevents overlapping response tasks | provider + existing guard |
| Fallback model if configured | **Yes** — second model name via env, same SDK, trivial ladder (e.g., flash-lite → flash) | provider layer |
| Cross-provider fallback (e.g., Groq LLM) | Yes, later — new client needed | Phase 5 |
| Distinguish transient 429 vs daily quota | **Yes** — error status/details/message ("per day" vs per-minute); enables correct user messaging and backoff windows | provider layer |
| Graceful degrade if perception fails | **Yes** — invalid/missing head → use the prose as a plain reply, skip state update for that turn (P7); the probe already implements exactly this parse path | production contract reuses it |
| Zero-LLM last resort (deterministic supportive filler) | Possible — **Phase 5 decision, flagged, not designed here** | session layer |

All of the above is **provider-layer hardening** — zero locked-architecture changes.

## 6. Alternatives (criteria: latency, JSON reliability, Hinglish, free tier, fused-experiment fit, integration ease)

| Option | Latency | Free tier | Hinglish | JSON mode | Integration | Verdict for us |
|---|---|---|---|---|---|---|
| **Gemini flash-lite (current)** | good | **RPD 1,000–1,500, TPM 250K–1M — largest free allowance found** | good, prompt-tested `[ASSUMPTION beyond prototype]` | via prompt+parse (probe measures) | already live | **keep (A)** |
| **Gemini flash (stronger sibling)** | good | tighter RPD per 2026 reports (250–1,500) | expected better on sarcasm `[ASSUMPTION]` | same | same SDK, env-level switch | **retry-ladder model, not a switch** |
| **Groq LLM (llama-3.3-70b / gpt-oss-120b)** | best-in-class | 30 RPM but **TPM 6–12K/min binding; RPD 1,000** | good-unverified `[ASSUMPTION]` | yes (json_object) | new client (OpenAI-compatible); same org as our STT key | cross-provider fallback candidate, not primary |
| **DeepSeek V4-Flash** | decent | **none** ($0.22/$0.66 per M) | strong `[ASSUMPTION]` | yes | OpenAI-compatible | cost fallback only; not for free-tier validation |
| OpenRouter/Cerebras free pools | varies | small, shared, unreliable | varies | varies | easy | exploratory only |

**[FINDING]:** no alternative offers a free-tier *quota* upgrade that matters at our scale; Gemini's free RPD/TPM is the largest of the lot. Switching costs (re-validating Hinglish/sarcasm/JSON behavior) exceed the quota gains.

## 7. Recommendation

## **A — Keep current provider/model for Phase 3 validation**, with two non-architectural accompaniments:

1. **[DONE this pass — tooling, not architecture]** Probe pacing added (`--pace-sec`, default 4.5 s ≈ ≤13 RPM) so a 60-call sweep cannot burst into self-inflicted 429s and corrupt latency measurements.
2. **[SCHEDULED — Phase 5 pre-implementation hardening]** Provider-layer retry/backoff (bounded, no-restart-after-first-token), 429-vs-RPD classification, env-configured fallback model, degrade-on-head-failure path. Rationale: the codebase currently has **zero retry anywhere** — at our volumes that is a bigger operational risk than the quotas themselves, and it must exist before any real user does.

**Why not B (switch):** workload math says the free tier covers validation (a few hundred calls/day) and single-user MVP (~60–120 calls/session, ≤ ~15 sessions/day) with headroom; a provider switch would force re-validating everything this phase exists to measure.
**Why not C-first (retry infra before validation):** validation correctness needs *pacing*, not retries — and retries during the probe would mask the very 429 behavior we may want to observe once, deliberately.
**Revisit trigger for B/C upgrade:** a multi-user pilot (>~10 users or sustained ~1,000+ LLM calls/day), or Gemini quality-gate failures in the probe that a stronger/other model would plausibly fix.
