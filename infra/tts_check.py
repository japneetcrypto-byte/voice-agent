#!/usr/bin/env python3
"""TTS provider diagnostic — verifies the Fish Audio clone path end-to-end.

Run from the repo root (uses your local .env, never prints secrets):

    uv run python infra/tts_check.py

Exit 0 = Fish Audio path OK · Exit 1 = would fall back to EdgeTTS (reason printed).
Writes tts_check_output.wav so you can listen and confirm it's YOUR clone.
"""
import asyncio
import os
import sys
import time
import wave

TEXT_PARTS = [
    "Hey, this is my cloned voice going through Fish Audio. ",
    "If you can hear this clearly, the provider swap is done and we are back on the real voice.",
]


async def main() -> int:
    from dotenv import load_dotenv

    load_dotenv()
    api_key = os.getenv("FISH_AUDIO_API_KEY", "")
    ref = os.getenv("FISH_AUDIO_REFERENCE_ID", "")
    key_ok = bool(api_key) and not api_key.startswith(("<<<", "your_"))

    print("== TTS diagnostic ==")
    print("FISH_AUDIO_API_KEY     :", f"<set, len={len(api_key)}>" if key_ok else "<MISSING/placeholder>")
    print("FISH_AUDIO_REFERENCE_ID:", ref or "<MISSING>")
    if not key_ok:
        print("RESULT: worker would fall back to EdgeTTS — set the real API key in .env")
        return 1

    from providers.tts import FishAudioTTSProvider

    # Outside the agent worker, plugins require an explicit http session context.
    try:
        from livekit.agents.utils import http_context
        ctx = http_context.open()
    except ImportError:
        import contextlib
        ctx = contextlib.nullcontext()

    try:
        provider = FishAudioTTSProvider()
    except ValueError as e:
        print(f"RESULT: init failed -> {e}")
        return 1

    o = provider.engine._opts
    print(f"engine                 : model={o.model}  voice={str(o.voice_id)[:12]}…  {o.sample_rate}Hz {o.output_format}")

    async def text_stream():
        for part in TEXT_PARTS:
            yield part

    frames, first_at = [], None
    t0 = time.perf_counter()
    try:
        async with ctx, asyncio.timeout(30):
            async for a in provider.synthesize_stream(text_stream()):
                if first_at is None:
                    first_at = time.perf_counter() - t0
                frames.append(a.frame)
    except TimeoutError:
        if not frames:
            print("RESULT: server connected but sent NO audio in 30s (silent stall).")
            print("Next: uv run python infra/fish_raw_probe.py — it prints the server's raw response.")
            return 1
    except Exception as e:
        print(f"RESULT: Fish Audio FAILED -> {type(e).__name__}: {str(e)[:300]}")
        print("Worker will log [TTS Fallback] and EdgeTTS would speak instead.")
        print("If this says 401/unauthorized -> check the API key.")
        print("If it mentions model -> 's2.1-pro-free' rejected; check the exact model name in the fish.audio dashboard.")
        return 1

    if not frames:
        print("RESULT: Fish Audio returned no audio.")
        return 1

    total = time.perf_counter() - t0
    sr, ch = frames[0].sample_rate, frames[0].num_channels
    pcm = b"".join(bytes(f.data) for f in frames)
    dur = len(pcm) / (sr * ch * 2)
    with wave.open("tts_check_output.wav", "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    print(f"audio                  : {dur:.1f}s @ {sr}Hz mono")
    print(f"latency                : first audio {first_at:.2f}s · total {total:.2f}s")
    print("saved                  : tts_check_output.wav  <-- play it: must be YOUR clone, not Neerja")
    print("RESULT: Fish Audio path OK — worker will log [TTS] Primary: Fish Audio")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
