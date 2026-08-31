from typing import AsyncGenerator
import os
import asyncio
import logging
import time
import numpy as np
from livekit import rtc
from livekit.agents import tts

from agent.tts_warmup import WarmupPolicy

logger = logging.getLogger(__name__)

# Warmup text: one short word already in the ack vocabulary — minimal
# audio, minimal quota burn, still exercises the voice model.
WARMUP_TEXT = "haan"

class TTSProvider:
    async def synthesize_stream(
        self, text_stream: AsyncGenerator[str, None]
    ) -> AsyncGenerator[tts.SynthesizedAudio, None]:
        raise NotImplementedError


class FishAudioTTSProvider(TTSProvider):
    def __init__(self):
        from livekit.plugins.fishaudio import TTS as FishTTS
        api_key = os.getenv("FISH_AUDIO_API_KEY")
        if not api_key:
            raise ValueError("FISH_AUDIO_API_KEY is not set.")
        voice_id = os.getenv("FISH_AUDIO_REFERENCE_ID")
        if not voice_id:
            raise ValueError("FISH_AUDIO_REFERENCE_ID is not set.")
        kwargs = {
            "api_key": api_key,
            "model": "s2.1-pro-free",
            "voice_id": voice_id,
            # s2.1-pro-free supports max 44100 Hz for wav (server-verified 2026-08-26);
            # synthesize_stream resamples to 48 kHz for AudioSource(48000, 1).
            "sample_rate": 44100,
        }
        self.engine = FishTTS(**kwargs)
        self.last_provider = "fish"
        self.warmup_policy = WarmupPolicy()

    def note_synthesis_end(self) -> None:
        """Call after a reply finishes (feeds the pre-warm decision)."""
        self.warmup_policy.note_synthesis_end(time.monotonic())

    async def warmup(self) -> bool:
        """Best-effort background warmup of the cloned voice model.

        CA3-approved add-on (2026-08-30). Policy-bounded (idle gap, per-
        session quota budget) — see agent/tts_warmup.py. Never raises.
        """
        import time
        if os.getenv("AIVA_TTS_PREWARM", "1") != "1":
            return False
        now = time.monotonic()
        if not self.warmup_policy.should_warm(now):
            return False
        try:
            async def _warm_text():
                yield WARMUP_TEXT
            async for _ in self.synthesize_stream(_warm_text()):
                pass  # discard; we only need the model warm
            self.warmup_policy.on_warmup_done(time.monotonic())
            print("[TTS PreWarm] voice model warmed "
                  f"({self.warmup_policy._warm_calls}/{self.warmup_policy.max_per_session})")
            return True
        except Exception as e:
            logger.warning(f"[TTS PreWarm] failed (harmless): {type(e).__name__}: {e}")
            return False

    async def synthesize_stream(
        self, text_stream: AsyncGenerator[str, None]
    ) -> AsyncGenerator[tts.SynthesizedAudio, None]:
        tts_stream = self.engine.stream()

        async def push_text():
            async for chunk in text_stream:
                tts_stream.push_text(chunk)
            tts_stream.flush()
            tts_stream.end_input()

        push_task = asyncio.create_task(push_text())
        try:
            resampler = None
            async for audio_chunk in tts_stream:
                if (resampler is None and 
                        audio_chunk.frame.sample_rate != 48000):
                    resampler = rtc.AudioResampler(
                        input_rate=audio_chunk.frame.sample_rate,
                        output_rate=48000
                    )
                if resampler:
                    for r_frame in resampler.push(audio_chunk.frame):
                        yield tts.SynthesizedAudio(
                            request_id=audio_chunk.request_id,
                            frame=r_frame
                        )
                else:
                    yield audio_chunk
            if resampler:
                for r_frame in resampler.flush():
                    yield tts.SynthesizedAudio(request_id="", frame=r_frame)
        finally:
            if not push_task.done():
                push_task.cancel()
            await tts_stream.aclose()


class EdgeTTSProvider(TTSProvider):
    def __init__(self, voice: str = "en-IN-NeerjaNeural"):
        self.voice = voice

    async def synthesize_stream(
        self, text_stream: AsyncGenerator[str, None]
    ) -> AsyncGenerator[tts.SynthesizedAudio, None]:
        import edge_tts
        import av as pyav
        import io

        self.last_provider = "edge"
        chunks = []
        async for chunk in text_stream:
            chunks.append(chunk)
        full_text = "".join(chunks).strip()
        if not full_text:
            return

        def _sync_work():
            communicate = edge_tts.Communicate(full_text, self.voice)
            mp3_data = bytearray()
            for event in communicate.stream_sync():
                if event["type"] == "audio":
                    mp3_data.extend(event["data"])
            if not mp3_data:
                return []
            buf = io.BytesIO(bytes(mp3_data))
            container = pyav.open(buf, format="mp3")
            audio_stream = container.streams.audio[0]
            resampler = pyav.AudioResampler(
                format="s16", layout="mono", rate=48000
            )
            out_chunks = []
            for packet in container.demux(audio_stream):
                for frame in packet.decode():
                    for r_frame in resampler.resample(frame):
                        pcm = bytes(r_frame.planes[0])
                        audio_np = np.frombuffer(pcm, dtype=np.int16)
                        chunk_size = 960
                        for i in range(0, len(audio_np), chunk_size):
                            piece = audio_np[i:i + chunk_size]
                            lk_frame = rtc.AudioFrame(
                                data=piece.tobytes(),
                                sample_rate=48000,
                                num_channels=1,
                                samples_per_channel=len(piece),
                            )
                            out_chunks.append(
                                tts.SynthesizedAudio(
                                    request_id="edge-tts",
                                    frame=lk_frame
                                )
                            )
            return out_chunks

        for chunk in await asyncio.to_thread(_sync_work):
            yield chunk


class FallbackTTSProvider(TTSProvider):
    def __init__(self):
        try:
            self.primary = FishAudioTTSProvider()
            logger.info("[TTS] Primary: Fish Audio "
                       f"(voice: {os.getenv('FISH_AUDIO_REFERENCE_ID')})")
        except ValueError as e:
            logger.warning(f"[TTS] Fish Audio unavailable: {e}. "
                          "Using EdgeTTS only.")
            self.primary = None
        self.fallback = EdgeTTSProvider()
        self.last_provider = None
        self.last_fallback_reason = None
        self.warm_hit = None  # per-turn: was the voice model warm at TTS start?

    async def warmup(self) -> bool:
        """Background pre-warm of the Fish voice model (CA3 add-on)."""
        if self.primary is None:
            return False
        return await self.primary.warmup()

    async def synthesize_stream(
        self, text_stream: AsyncGenerator[str, None]
    ) -> AsyncGenerator[tts.SynthesizedAudio, None]:
        # Anticipatory pipeline fix (evidence: sessions 2026-08-28 — TTFA
        # 1.3-3.7s included the FULL LLM generation time because this provider
        # buffered all text before Fish started). Now: text is fanned out LIVE
        # — Fish receives chunks as the LLM emits them (first audio races the
        # LLM stream), and a parallel buffer keeps the full text for an
        # EdgeTTS replay if Fish fails.
        text_chunks: list[str] = []
        queue: asyncio.Queue = asyncio.Queue()
        src_done = asyncio.Event()

        async def _tee():
            try:
                async for chunk in text_stream:
                    text_chunks.append(chunk)
                    await queue.put(chunk)
            finally:
                await queue.put(None)
                src_done.set()

        async def _live_reader():
            while True:
                item = await queue.get()
                if item is None:
                    return
                yield item

        tee_task = asyncio.create_task(_tee())

        async def make_stream():
            for chunk in list(text_chunks):
                yield chunk

        self.warm_hit = (self.primary.warmup_policy.is_warm(time.monotonic())
                         if self.primary else None)

        try:
            if not self.primary:
                await tee_task
                try:
                    async for chunk in self.fallback.synthesize_stream(make_stream()):
                        yield chunk
                except Exception as e:
                    # Owner nuance (2026-08-30): if Edge ALSO fails, speaking
                    # makes no sense — silence-with-log, never garbled audio.
                    self.last_provider = "none"
                    self.last_fallback_reason = f"edge_failed: {type(e).__name__}: {str(e)[:120]}"
                    logger.error("[TTS] Edge fallback failed too — silent turn "
                                 f"(logged): {e}")
                return

            self.last_provider = "fish"
            primary_stream = self.primary.synthesize_stream(_live_reader())
            # First-audio timeout. Fish TTFA baseline is 1.5-1.9s; degradation
            # episodes (sessions 155556: 2.9-4.05s) still play the clone, but a
            # HANG fails over to Edge this many seconds after TTS start.
            first_timeout = float(os.getenv("AIVA_TTS_FIRST_TIMEOUT", "5.0"))
            first_chunk = await asyncio.wait_for(
                primary_stream.__anext__(), timeout=first_timeout
            )
            yield first_chunk
            # Self-healing failover (evidence 141753: 6 turns where Fish
            # 'succeeded' with zero/trace audio). Counted in SAMPLES, not
            # chunks — the server may emit a few frames of near-silence.
            samples = first_chunk.frame.samples_per_channel
            async for audio in primary_stream:
                samples += audio.frame.samples_per_channel
                yield audio
            await tee_task
            if samples < 4800:  # <0.1s of audio = silence
                raise ValueError(f"fish_silent_stream ({samples} samples)")
            self.primary.note_synthesis_end()  # feed the pre-warm decision
        except (asyncio.TimeoutError, StopAsyncIteration, Exception) as e:
            self.last_provider = "edge (fallback)"
            self.last_fallback_reason = f"{type(e).__name__}: {str(e)[:150]}"
            if not isinstance(e, StopAsyncIteration):
                logger.error(
                    f"[TTS Fallback] Fish Audio failed: {e}. "
                    "Switching to EdgeTTS."
                )
            await src_done.wait()  # drain remaining text so Edge gets it all
            try:
                async for audio in self.fallback.synthesize_stream(make_stream()):
                    yield audio
            except Exception as e2:
                # Owner nuance (2026-08-30): Edge failing too -> silence-with-
                # log. "Edge saying also does not make sense" when it fails.
                self.last_provider = "none"
                self.last_fallback_reason = f"edge_failed: {type(e2).__name__}: {str(e2)[:120]}"
                logger.error("[TTS] Edge fallback failed too — silent turn "
                             f"(logged): {e2}")
        finally:
            if not tee_task.done():
                tee_task.cancel()


def get_tts_provider() -> TTSProvider:
    return FallbackTTSProvider()
