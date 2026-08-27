"""Gemini 3.5 Transcribe Live STT provider — streaming, primary.

Uses the Gemini Live API (gemini-3.5-transcribe-live) for real-time
speech-to-text. Falls back to GroqSTT on any failure.

Output format matches providers/stt.py Transcript so nothing downstream changes.
"""
from __future__ import annotations

import asyncio
import os
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
                language_codes=[],  # auto-detect
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
            language="auto",  # Gemini handles internally
            no_speech_prob=0.0,
            avg_logprob=-0.2,  # Gemini doesn't expose this; use good default
            compression_ratio=1.0,
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
