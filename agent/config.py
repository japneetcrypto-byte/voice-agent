import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # LiveKit Cloud
    LIVEKIT_CLOUD_URL = os.getenv("LIVEKIT_CLOUD_URL")
    LIVEKIT_CLOUD_API_KEY = os.getenv("LIVEKIT_CLOUD_API_KEY")
    LIVEKIT_CLOUD_API_SECRET = os.getenv("LIVEKIT_CLOUD_API_SECRET")
    
    # LiveKit Local
    LIVEKIT_LOCAL_URL = os.getenv("LIVEKIT_LOCAL_URL", "ws://127.0.0.1:7880")
    LIVEKIT_LOCAL_API_KEY = os.getenv("LIVEKIT_LOCAL_API_KEY", "devkey")
    LIVEKIT_LOCAL_API_SECRET = os.getenv("LIVEKIT_LOCAL_API_SECRET", "secret")
    
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    FISH_AUDIO_API_KEY = os.getenv("FISH_AUDIO_API_KEY")
    FISH_AUDIO_REFERENCE_ID = os.getenv("FISH_AUDIO_REFERENCE_ID")
    
    NO_SPEECH_THRESHOLD = float(os.getenv("NO_SPEECH_THRESHOLD", "0.6"))
    # Suspicious no-speech band (sign-off CA6, 2026-08-30): Whisper is
    # ~50/50 on whether this is speech. Reject -> route_transcript bounds
    # it (>=4 words -> contextual_recovery with checkpoint policy, else
    # clarify). Never a substantive LLM answer on an uncertain transcript.
    # Conservative default; tune via phase5/stt_rejection_report.py data.
    SUSPICIOUS_NSP_MIN = float(os.getenv("AIVA_STT_SUSPICIOUS_BAND_MIN", "0.5"))
    AVG_LOGPROB_THRESHOLD = float(os.getenv("AVG_LOGPROB_THRESHOLD", "-1.0"))
    # Catastrophic-confidence floor (deterministic clarify, no LLM). Kept
    # STRICTLY below AVG_LOGPROB_THRESHOLD so the low_avg_logprob branch is
    # reachable (bug fix 2026-08-30: was -0.85 > -1.0, making 'low' dead).
    CATASTROPHIC_LOGPROB = float(os.getenv("AIVA_STT_CATASTROPHIC_LOGPROB", "-1.2"))
