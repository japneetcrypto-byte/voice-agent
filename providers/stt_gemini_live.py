"""Gemini 3.5 Transcribe Live STT provider — streaming, primary.

Uses the Gemini Live API (gemini-3.5-transcribe-live) for real-time
speech-to-text. Falls back to GroqSTT on any failure.

Output format matches providers/stt.py Transcript so nothing downstream changes.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import AsyncGenerator

from providers.stt import Transcript, STTProvider, GroqSTT


class GeminiLiveSTT(STTProvider):
    """Primary STT: Gemini 3.5 Transcribe Live (streaming WebSocket).
    Fallback: Groq whisper-large-v3 (batch, proven reliable)."""

    def __init__(self):
        self.groq = GroqSTT()  # fallback
        self.api_key = os.getenv("GEMINI_API_KEY", "")
        if not self.api_key or self.api_key.startswith(("your_", "<<<")):
            raise ValueError("GEMINI_API_KEY required for GeminiLiveSTT")
        self.model = "gemini-3.5-transcribe-live"
        # Same locked language pin as GroqSTT / router (owner ruling: hi default)
        self.language = os.getenv("AIVA_STT_LANGUAGE", "hi").strip().lower()
        self._connect_cm = None

    async def transcribe_stream(
        self, audio_chunks: AsyncGenerator[bytes, None]
    ) -> Transcript:
        """Streaming transcription via Gemini Live API WebSocket.

        audio_chunks: async generator of 16-bit PCM bytes (16kHz mono).
        Returns a single Transcript with the final text.
        Falls back to Groq batch STT on any error.
        """
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        text_parts = []
        detected_lang = None

        config = types.LiveConnectConfig(
            response_modalities=["TEXT"],
            input_audio_transcription=types.AudioTranscriptionConfig(
                language_codes=[self.language] if self.language else [],
            ),
        )

        try:
            async with asyncio.timeout(30):
                async with client.aio.live.connect(
                    model=self.model, config=config
                ) as session:
                    async for chunk in audio_chunks:
                        await session.send_realtime_input(
                            audio=types.Blob(
                                data=chunk, mime_type="audio/pcm;rate=16000"
                            )
                        )

                    await session.send_realtime_input(audio_stream_end=True)

                    async for response in session.receive():
                        sc = response.server_content
                        if sc and sc.input_transcription:
                            text_parts.append(sc.input_transcription.text)

        except (asyncio.TimeoutError, Exception):
            # fallback to Groq batch STT
            return await self._groq_fallback(audio_chunks)

        full_text = " ".join(text_parts).strip()
        if not full_text:
            return await self._groq_fallback(audio_chunks)

        return Transcript(
            text=full_text,
            language=self.language or "auto",
            no_speech_prob=0.0,
            # AUDIT: honest None — a fake value blinds the validity gates.
            avg_logprob=None,
            compression_ratio=None,
        )

    async def _groq_fallback(self, audio_chunks) -> Transcript:
        """Collect chunks into a buffer and use Groq batch STT."""
        audio_data = b"".join([c async for c in audio_chunks])
        import numpy as np
        arr = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
        return self.groq.transcribe(arr)

    def transcribe(self, audio_data) -> Transcript:
        """Sync batch mode — delegates to Groq (Gemini Live is streaming-only)."""
        return self.groq.transcribe(audio_data)

    # ---- Streaming session interface (called by main.py per turn) ----

    _stream_active: bool = False
    _ws_session = None
    _text_parts: list = None
    _final_text: str | None = None
    _client_instance = None

    async def start_stream(self):
        """Open a Gemini Live WS session and start accepting audio chunks."""
        if self._stream_active:
            return
        from google import genai
        from google.genai import types

        self._text_parts = []
        self._final_text = None
        self._open_time = time.monotonic()
        self._client_instance = genai.Client(api_key=self.api_key)
        self._ws_config = types.LiveConnectConfig(
            response_modalities=["TEXT"],
            input_audio_transcription=types.AudioTranscriptionConfig(
                language_codes=[self.language] if self.language else [],
            ),
        )
        try:
            cm = self._client_instance.aio.live.connect(
                model=self.model, config=self._ws_config
            )
            self._connect_cm = cm
            self._ws_session = await asyncio.wait_for(
                cm.__aenter__(), timeout=10)
            self._stream_active = True
            print("[GeminiSTT] stream opened")
        except asyncio.TimeoutError:
            print("[GeminiSTT] stream open timed out")
            self._stream_active = False
        except Exception as e:
            print(f"[GeminiSTT] stream open failed: {type(e).__name__}: {e}")
            self._stream_active = False

    async def send_chunk(self, pcm_bytes: bytes):
        """Send a raw PCM audio chunk to the active stream."""
        if not self._stream_active or not self._ws_session:
            return
        try:
            from google.genai import types
            await self._ws_session.send_realtime_input(
                audio=types.Blob(data=pcm_bytes, mime_type="audio/pcm;rate=16000")
            )
        except Exception:
            self._stream_active = False

    async def end_stream(self):
        """Close the stream and extract the final transcription."""
        if not self._stream_active or not self._ws_session:
            return
        try:
            await self._ws_session.send_realtime_input(audio_stream_end=True)
            # Collect final transcription events
            async for response in self._ws_session.receive():
                sc = response.server_content
                if sc and sc.input_transcription:
                    self._text_parts.append(sc.input_transcription.text)
        except Exception:
            pass
        finally:
            self._stream_active = False
            try:
                if hasattr(self, '_connect_cm') and self._connect_cm:
                    await self._connect_cm.__aexit__(None, None, None)
                elif self._ws_session:
                    await self._ws_session.close()
            except Exception:
                pass
            self._ws_session = None
            self._connect_cm = None

        end_time = time.monotonic()
        duration_s = round(end_time - getattr(self, "_open_time", end_time), 2)
        full_text = " ".join(self._text_parts).strip()
        word_count = len(full_text.split()) if full_text else 0

        self._last_metrics = {
            "provider": "gemini_transcribe_live",
            "duration_s": duration_s,
            "word_count": word_count,
        }

        if not hasattr(self, "stt_metrics"):
            self.stt_metrics = []
        self.stt_metrics.append(self._last_metrics)

        if full_text:
            self._final_text = full_text
            print(f"[GeminiSTT] ok {duration_s}s | {word_count}w | {full_text[:80]}")
        else:
            self._final_text = None
            print(f"[GeminiSTT] empty after {duration_s}s")
