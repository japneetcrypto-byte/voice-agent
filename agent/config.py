import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    LIVEKIT_URL = os.getenv("LIVEKIT_URL", "ws://127.0.0.1:7880")
    LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "devkey")
    LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "secret")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    FISH_AUDIO_API_KEY = os.getenv("FISH_AUDIO_API_KEY")
    FISH_AUDIO_REFERENCE_ID = os.getenv("FISH_AUDIO_REFERENCE_ID")
