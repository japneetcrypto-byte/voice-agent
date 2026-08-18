import asyncio
import numpy as np
from livekit import rtc
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli

from .session import ConversationSession
from providers.vad import get_vad_provider, VADEvent
from providers.stt import get_stt_provider
from providers.llm import get_llm_provider
from providers.tts import get_tts_provider

async def entrypoint(ctx: JobContext):
    print("Connecting to room...")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    print(f"Agent connected to room: {ctx.room.name}")

    vad_provider = get_vad_provider()
    stt_provider = get_stt_provider()
    llm_provider = get_llm_provider()
    tts_provider = get_tts_provider()
    session = ConversationSession()

    agent_source = None
    agent_track = None

    async def handle_conversation_turn(user_text: str):
        session.add_user_message(user_text)
        
        messages = session.get_context()
        print("Agent thinking...")
        text_stream = llm_provider.generate_response_stream(messages)
        
        async def text_stream_tee():
            full_text = []
            async for chunk in text_stream:
                print(chunk, end="", flush=True)
                full_text.append(chunk)
                yield chunk
            print()
            session.add_agent_message("".join(full_text))
            
        audio_stream = tts_provider.synthesize_stream(text_stream_tee())
        
        print("Agent speaking...")
        async for audio_chunk in audio_stream:
            if agent_source is not None:
                await agent_source.capture_frame(audio_chunk.frame)
        print("Agent finished speaking.")

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            asyncio.create_task(process_user_audio(track))

    async def process_user_audio(track: rtc.RemoteAudioTrack):
        nonlocal agent_source, agent_track
        
        audio_stream = rtc.AudioStream(track)
        resampler = None
        
        is_speaking = False
        speech_buffer = []

        print("Listening for user speech...")
        
        async for event in audio_stream:
            frame = event.frame
            
            if agent_source is None:
                agent_source = rtc.AudioSource(48000, 1)
                agent_track = rtc.LocalAudioTrack.create_audio_track("agent-mic", agent_source)
                await ctx.room.local_participant.publish_track(agent_track)
                
            if resampler is None or getattr(resampler, "input_rate", 0) != frame.sample_rate:
                resampler = rtc.AudioResampler(input_rate=frame.sample_rate, output_rate=16000)
                
            resampled_frames = resampler.push(frame)
            for r_frame in resampled_frames:
                audio_np = np.frombuffer(r_frame.data, dtype=np.int16)
                
                vad_events = vad_provider.process_audio(audio_np)
                
                if is_speaking:
                    speech_buffer.append(audio_np)
                
                for vad_event in vad_events:
                    if vad_event == VADEvent.SPEECH_STARTED:
                        is_speaking = True
                        speech_buffer = [audio_np]
                        print("User started speaking...")
                        
                    elif vad_event == VADEvent.SPEECH_ENDED:
                        is_speaking = False
                        print("User stopped speaking. Transcribing...")
                        if not speech_buffer:
                            continue
                            
                        full_audio = np.concatenate(speech_buffer)
                        float_audio = full_audio.astype(np.float32) / 32768.0
                        
                        try:
                            transcript = stt_provider.transcribe(float_audio)
                            print(f"User: {transcript.text}")
                            
                            if transcript.text.strip():
                                await handle_conversation_turn(transcript.text)
                        except Exception as e:
                            print(f"STT Error: {e}")
                            
                        speech_buffer = []

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
        )
    )
