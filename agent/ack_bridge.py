"""Ack Bridge — fills the speech→reply latency gap with natural vocal cues.

Owner: "timing of speaking does not look natural." The 2-3s of dead silence
between the user stopping and Aiva starting is the #1 unnaturalness driver.
Human friends fill that gap with a sound ("achha", "hmm", "haan") almost
instantly — acknowledging receipt, then thinking.

Mechanism: at worker startup, pre-synthesize 4-5 short acknowledgment clips
(EdgeTTS, one-time cost). At play time, the cached PCM is written directly
to the AudioSource — ZERO latency, no TTS call. The user hears:

  user stops → 0.1s → "achha" (ack) → brief gap → full reply

instead of:

  user stops → 2.5s DEAD SILENCE → full reply

The acknowledgment makes the gap feel like "thinking time" rather than
"system latency."
"""
from __future__ import annotations

import io
import random

import numpy as np

ACK_TEXTS = ["achha", "haan bol", "hmm", "theek hai"]


class AckBridge:
    """Pre-synthesized acknowledgment clips, played to fill latency gaps."""

    def __init__(self):
        self._clips: list[np.ndarray] = []  # int16 PCM @48kHz mono
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready and len(self._clips) > 0

    async def pregenerate(self) -> None:
        """One-time: synthesize short acknowledgment clips via EdgeTTS."""
        import edge_tts
        import av as pyav

        for text in ACK_TEXTS:
            try:
                communicate = edge_tts.Communicate(text, "hi-IN-MadhurNeural")
                buf = io.BytesIO()
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        buf.write(chunk["data"])
                buf.seek(0)
                container = pyav.open(buf, format="mp3")
                resampler = pyav.AudioResampler(format="s16", layout="mono", rate=48000)
                pcm = []
                for packet in container.demux():
                    for frame in packet.decode():
                        for r in resampler.resample(frame):
                            pcm.append(np.frombuffer(bytes(r.planes[0]), dtype=np.int16))
                if pcm:
                    clip = np.concatenate(pcm)
                    self._clips.append(clip)
                    print(f"[AckBridge] cached {text!r} ({len(clip)/48:.0f}ms)")
            except Exception as e:
                print(f"[AckBridge] {text}: {type(e).__name__}: {e}")
        self._ready = len(self._clips) > 0
        print(f"[AckBridge] ready with {len(self._clips)} clips")

    def get_clip(self) -> np.ndarray:
        """Random acknowledgment clip (int16 PCM @48kHz)."""
        return random.choice(self._clips)
