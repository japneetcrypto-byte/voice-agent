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
    AVG_LOGPROB_THRESHOLD = float(os.getenv("AVG_LOGPROB_THRESHOLD", "-1.0"))
