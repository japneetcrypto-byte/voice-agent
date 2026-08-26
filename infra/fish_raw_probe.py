#!/usr/bin/env python3
"""Raw Fish Audio probe — prints exactly what the server sends back.

Run from repo root:  uv run python infra/fish_raw_probe.py
Tests HTTP /v1/tts and streaming WS /v1/tts/live with model variants
(s2.1-pro-free / s2.1-pro / no header) and dumps every event received.
This pinpoints: invalid model vs quota/no-API-access vs bad reference_id.
"""
import asyncio
import os
import time

import aiohttp
import msgpack
from dotenv import load_dotenv

WS_URL = "wss://api.fish.audio/v1/tts/live"
HTTP_URL = "https://api.fish.audio/v1/tts"
MODELS = ["s2.1-pro-free", "s2.1-pro", None]
TEXT = "Hello, this is a quick test of my cloned voice."


def build_request(ref: str) -> dict:
    # mirrors the livekit plugin's _build_tts_request exactly
    return {
        "text": TEXT,
        "chunk_length": 100,
        "format": "wav",
        "sample_rate": 48000,
        "mp3_bitrate": 64,
        "opus_bitrate": 64000,
        "references": [],
        "reference_id": ref or None,
        "normalize": True,
        "latency": "balanced",
        "prosody": None,
        "top_p": 0.7,
        "temperature": 0.7,
        "features": ["quality-guard"],
    }


async def http_probe(key: str, ref: str, model: str | None) -> None:
    tag = f"HTTP model={model or '<none>'}"
    t0 = time.perf_counter()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/msgpack",
               "User-Agent": "tts-probe/0.1"}
    if model:
        headers["model"] = model
    async with aiohttp.ClientSession() as s:
        try:
            async with s.post(HTTP_URL, headers=headers,
                              data=msgpack.packb(build_request(ref), use_bin_type=True),
                              timeout=aiohttp.ClientTimeout(total=30)) as r:
                body = await r.content.read(400)
                ct = r.headers.get("Content-Type", "?")
                print(f"[{tag}] status={r.status} content-type={ct} ({time.perf_counter()-t0:.2f}s)")
                if r.status == 200:
                    print(f"[{tag}] 200 AUDIO — first bytes: {body[:16]!r}")
                else:
                    print(f"[{tag}] body: {body.decode(errors='replace')[:400]}")
        except Exception as e:
            print(f"[{tag}] EXC {type(e).__name__}: {str(e)[:200]}")


async def ws_probe(key: str, ref: str, model: str | None) -> None:
    tag = f"WS   model={model or '<none>'}"
    t0 = time.perf_counter()
    headers = {"Authorization": f"Bearer {key}", "User-Agent": "tts-probe/0.1"}
    if model:
        headers["model"] = model
    try:
        async with asyncio.timeout(30):
            async with aiohttp.ClientSession() as s:
                async with s.ws_connect(WS_URL, headers=headers) as ws:
                    print(f"[{tag}] connected ({time.perf_counter()-t0:.2f}s)")
                    await ws.send_bytes(msgpack.packb(
                        {"event": "start", "request": build_request(ref)}, use_bin_type=True))
                    await ws.send_bytes(msgpack.packb({"event": "text", "text": TEXT}, use_bin_type=True))
                    await ws.send_bytes(msgpack.packb({"event": "flush"}, use_bin_type=True))
                    print(f"[{tag}] start+text+flush sent ({time.perf_counter()-t0:.2f}s)")
                    audio = 0
                    while True:
                        msg = await ws.receive()
                        el = time.perf_counter() - t0
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            data = msgpack.unpackb(msg.data, raw=False)
                            ev = data.get("event")
                            if ev == "audio":
                                n = len(data.get("audio") or b"")
                                audio += n
                                print(f"[{tag}] +audio chunk {n}B (total {audio}B) at {el:.2f}s")
                            else:
                                print(f"[{tag}] event={ev} data={str(data)[:300]} at {el:.2f}s")
                                if ev == "finish":
                                    break
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE,
                                          aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR):
                            print(f"[{tag}] ws closed/error type={msg.type} code={msg.data} "
                                  f"extra={msg.extra!r} at {el:.2f}s")
                            break
                        else:
                            print(f"[{tag}] msg type={msg.type} data={str(msg.data)[:200]} at {el:.2f}s")
                    print(f"[{tag}] done — total audio bytes: {audio}")
    except asyncio.TimeoutError:
        print(f"[{tag}] TIMEOUT — connected but no reply within 30s → silent stall for this model")
    except Exception as e:
        print(f"[{tag}] EXC {type(e).__name__}: {str(e)[:200]}")


async def main() -> int:
    load_dotenv()
    key = os.getenv("FISH_AUDIO_API_KEY", "")
    ref = os.getenv("FISH_AUDIO_REFERENCE_ID", "")
    key_ok = bool(key) and not key.startswith(("<<<", "your_"))
    print("== fish_raw_probe ==")
    print("key:", f"<set len={len(key)}>" if key_ok else "<MISSING>", "| ref:", (ref or "<MISSING>")[:12] + "…")
    if not key_ok:
        return 1
    print("\n--- HTTP endpoint ---")
    for m in MODELS:
        await http_probe(key, ref, m)
    print("\n--- streaming websocket ---")
    for m in MODELS:
        await ws_probe(key, ref, m)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
