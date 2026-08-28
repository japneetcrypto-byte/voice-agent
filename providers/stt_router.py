"""Multi-provider STT router with streaming + batch interface.

Providers are tried in order: primary → fallback_chain.
Each provider implements either:
  - transcribe_stream(chunks) → Transcript  (streaming, preferred)
  - transcribe(audio_data) → Transcript     (batch, fallback)

The router normalizes the interface: callers always get a Transcript.
"""
from __future__ import annotations

import os
from typing import AsyncGenerator

import numpy as np

from providers.stt import Transcript, STTProvider, GroqSTT


class StreamingSTTProvider(STTProvider):
    """Base class for providers that support streaming transcription."""

    async def transcribe_stream(self, chunks: list[bytes]) -> Transcript:
        """Receive a list of raw PCM byte chunks (16kHz mono int16)."""
        raise NotImplementedError


class GeminiLiveStreamingSTT(StreamingSTTProvider):
    """Gemini 3.5 Transcribe Live — streaming WebSocket, unlimited quota."""

    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY", "")
        if not self.api_key or self.api_key.startswith(("your_", "<<<")):
            raise ValueError("GEMINI_API_KEY required for GeminiLiveSTT")
        self.model = "gemini-3.5-transcribe-live"

    async def transcribe_stream(self, chunks: list[bytes]) -> Transcript:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        text_parts = []

        config = types.LiveConnectConfig(
            response_modalities=["TEXT"],
            input_audio_transcription=types.AudioTranscriptionConfig(
                language_codes=[],
            ),
        )

        async with asyncio.timeout(30):
            async with client.aio.live.connect(
                model=self.model, config=config
            ) as session:
                # Send all audio chunks
                for chunk in chunks:
                    await session.send_realtime_input(
                        audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                    )
                await session.send_realtime_input(audio_stream_end=True)

                # Receive transcription
                async for response in session.receive():
                    sc = response.server_content
                    if sc and sc.input_transcription:
                        text_parts.append(sc.input_transcription.text)

        full_text = " ".join(text_parts).strip()
        if not full_text:
            raise ValueError("empty transcription from Gemini Live")

        return Transcript(
            text=full_text,
            language="auto",
            no_speech_prob=0.0,
            avg_logprob=-0.2,
            compression_ratio=1.0,
        )


class STTRouter:
    """Routes STT requests through a provider chain with automatic fallback.

    Primary provider is tried first. On failure, falls back to the next
    provider in the chain until one succeeds or all fail.
    """

    def __init__(self):
        self.primary_name = os.getenv("AIVA_STT_PRIMARY", "gemini_live")
        self.fallback_name = os.getenv("AIVA_STT_FALLBACK", "groq")
        self.primary = None
        self.fallback = None
        self._init_providers()

    def _init_providers(self):
        # Try to init primary
        if self.primary_name == "gemini_live":
            try:
                self.primary = GeminiLiveStreamingSTT()
                print("[STT Router] primary: Gemini Live")
            except (ValueError, ImportError) as e:
                print(f"[STT Router] Gemini Live unavailable: {e}")
        elif self.primary_name == "groq":
            self.primary = GroqSTT()
            print("[STT Router] primary: Groq")

        # Fallback is always Groq (unless primary IS Groq)
        if self.fallback_name == "groq" and not isinstance(self.primary, GroqSTT):
            self.fallback = GroqSTT()
            print("[STT Router] fallback: Groq")

    async def transcribe(self, audio_data: np.ndarray, raw_chunks: list[bytes] = None) -> Transcript:
        """Transcribe audio. Tries streaming providers first, then batch.

        audio_data: float32 numpy array (16kHz mono) — for batch providers
        raw_chunks: list of raw PCM byte chunks — for streaming providers
        """
        errors = []

        # Try streaming primary
        if isinstance(self.primary, StreamingSTTProvider) and raw_chunks:
            try:
                result = await self.primary.transcribe_stream(raw_chunks)
                return result
            except Exception as e:
                errors.append(f"{type(self.primary).__name__}: {str(e)[:100]}")
                print(f"[STT Router] {type(self.primary).__name__} failed: {str(e)[:80]}")

        # Try batch primary (if it's batch-type)
        if isinstance(self.primary, GroqSTT):
            try:
                return self.primary.transcribe(audio_data)
            except Exception as e:
                errors.append(f"Groq: {str(e)[:100]}")

        # Fallback
        if self.fallback:
            try:
                return self.fallback.transcribe(audio_data)
            except Exception as e:
                errors.append(f"Fallback {type(self.fallback).__name__}: {str(e)[:100]}")

        # All failed
        for err in errors:
            print(f"[STT Router] {err}")
        return Transcript(text="", language="auto", no_speech_prob=1.0)

    def transcribe_sync(self, audio_data: np.ndarray) -> Transcript:
        """Sync batch transcription — delegates to Groq (used by non-async callers)."""
        if self.fallback:
            return self.fallback.transcribe(audio_data)
        if self.primary and isinstance(self.primary, GroqSTT):
            return self.primary.transcribe(audio_data)
        return Transcript(text="", language="auto")
