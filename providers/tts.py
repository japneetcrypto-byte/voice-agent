from typing import AsyncGenerator
import os
import io
import asyncio
import logging
import numpy as np
from livekit import rtc
from livekit.agents import tts

logger = logging.getLogger(__name__)

class TTSProvider:
    async def synthesize_stream(self, text_stream: AsyncGenerator[str, None]) -> AsyncGenerator[tts.SynthesizedAudio, None]:
        raise NotImplementedError


class EdgeTTSProvider(TTSProvider):
    def __init__(self, voice: str = "en-IN-NeerjaNeural"):
        self.voice = voice

    async def synthesize_stream(self, text_stream: AsyncGenerator[str, None]) -> AsyncGenerator[tts.SynthesizedAudio, None]:
        import edge_tts
        import av as pyav
        import re

        queue = asyncio.Queue(maxsize=1000)

        def _sync_work(text):
            if not text.strip(): return []
            communicate = edge_tts.Communicate(text, self.voice)
            mp3_data = bytearray()
            for event in communicate.stream_sync():
                if event["type"] == "audio":
                    mp3_data.extend(event["data"])
            
            if not mp3_data:
                print("EdgeTTS: no audio data returned")
                return []
            
            buf = io.BytesIO(bytes(mp3_data))
            container = pyav.open(buf, format="mp3")
            audio_stream = container.streams.audio[0]
            resampler = pyav.AudioResampler(format="s16", layout="mono", rate=48000)

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
                            out_chunks.append(tts.SynthesizedAudio(request_id="edge-tts", frame=lk_frame))
            return out_chunks

        async def producer():
            print("TTS_PRODUCER_STARTED")
            try:
                buffer = ""
                async for chunk in text_stream:
                    buffer += chunk
                    # Only split on end of sentence marks, not commas, to avoid tiny chunks
                    parts = re.split(r'([.!?।](?:\s+|$))', buffer)
                    if len(parts) > 1:
                        buffer = parts[-1]
                        for i in range(0, len(parts)-1, 2):
                            sentence = parts[i] + parts[i+1]
                            sentence = sentence.strip()
                            if sentence:
                                print(f"TTS_SENTENCE_STARTED: {sentence[:60]}")
                                audio_frames = await asyncio.to_thread(_sync_work, sentence)
                                print(f"TTS_SENTENCE_COMPLETED: {len(audio_frames)} frames")
                                for f in audio_frames:
                                    await queue.put(f)
                                    print("TTS_FRAME_ENQUEUED")
                
                if buffer.strip():
                    print(f"TTS_SENTENCE_STARTED (final): {buffer.strip()[:60]}")
                    audio_frames = await asyncio.to_thread(_sync_work, buffer.strip())
                    print(f"TTS_SENTENCE_COMPLETED: {len(audio_frames)} frames")
                    for f in audio_frames:
                        await queue.put(f)
                        print("TTS_FRAME_ENQUEUED")
                        
            except asyncio.CancelledError:
                print("TTS_PRODUCER_CANCELLED")
                raise
            except Exception as e:
                print(f"TTS_PRODUCER_ERROR: {e}")
            finally:
                await queue.put(None) # Sentinel
                print("TTS_PRODUCER_COMPLETED")

        asyncio.create_task(producer())

        print("TTS_CONSUMER_STARTED")
        while True:
            audio_chunk = await queue.get()
            if audio_chunk is None:
                break
            print("TTS_FRAME_CONSUMED")
            yield audio_chunk

        print("TTS_CONSUMER_COMPLETED")
        print("EdgeTTS: done")


def get_tts_provider() -> TTSProvider:
    return EdgeTTSProvider()
