from typing import AsyncGenerator
import os
import asyncio
from livekit.plugins.fishaudio import TTS as FishTTS
from livekit.agents import tts

class TTSProvider:
    async def synthesize_stream(self, text_stream: AsyncGenerator[str, None]) -> AsyncGenerator[tts.SynthesizedAudio, None]:
        """
        Takes an asynchronous stream of text chunks and yields an asynchronous stream of audio chunks.
        """
        raise NotImplementedError

class FishAudioTTSProvider(TTSProvider):
    def __init__(self, model: str = "s2.1-pro-free", voice_id: str = None):
        api_key = os.getenv("FISH_AUDIO_API_KEY")
        if not api_key:
            raise ValueError("FISH_AUDIO_API_KEY is not set.")
            
        self.voice_id = voice_id or os.getenv("FISH_AUDIO_REFERENCE_ID")
        
        kwargs = {
            "api_key": api_key,
            "model": model,
        }
        if self.voice_id:
            kwargs["voice_id"] = self.voice_id
            
        self.engine = FishTTS(**kwargs)

    async def synthesize_stream(self, text_stream: AsyncGenerator[str, None]) -> AsyncGenerator[tts.SynthesizedAudio, None]:
        # LiveKit's TTS stream inherently handles buffering sentences and converting them to audio
        tts_stream = self.engine.stream()
        
        async def push_text_task():
            async for chunk in text_stream:
                tts_stream.push_text(chunk)
            tts_stream.flush()
            tts_stream.end_input()
            
        # Push text in the background while yielding audio frames to the caller
        task = asyncio.create_task(push_text_task())
        
        async for audio_chunk in tts_stream:
            yield audio_chunk
            
        await task

def get_tts_provider() -> TTSProvider:
    return FishAudioTTSProvider()
