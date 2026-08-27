# Gemini Quota Reference (owner-provided, 2026-08-27)

## LLM Models (fused perception + response)

| Model | RPM | TPM | RPD |
|---|---|---|---|
| Gemini 3.5 Flash Lite | 15 | 250K | 500 |
| Gemini 3.1 Flash Lite | 15 | 250K | 500 |

With 3 API keys: **45 RPM / 750K TPM / 1500 RPD** total capacity.

## Per-turn usage
- Prompt: ~800-1200 tokens (system + policy + memory + history + user turn)
- Response: ~200-400 tokens (perception head + reply)
- Total: ~1000-1600 tokens per turn

## Capacity math
- 3 keys × 15 RPM = 45 turns/min (a real conversation is 4-6 turns/min)
- 3 keys × 500 RPD = 1500 turns/day = ~15-25 sessions/day
- 3 keys × 250K TPM = never binds (we use ~2K/turn)

## Verdict
Free tier with 3-key rotation is MORE than enough for development
and single-user MVP. No paid tier needed for current use case.

## STT (Groq Whisper)
| Model | RPM | RPD |
|---|---|---|
| whisper-large-v3 | 20 | 2000 |
| whisper-large-v3-turbo | 20 | 2000 |

STT is not the bottleneck (20 RPM >> 6 turns/min).
