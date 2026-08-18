# Voice Agent MVP

A browser-based realtime voice agent MVP using LiveKit, TEN VAD, faster-whisper, Gemini Flash, and Fish Audio.

## Setup

1. Copy `.env.example` to `.env` and fill in your API keys.
2. Install Python dependencies using `uv sync`.
3. Install frontend dependencies in `frontend/` using `npm install`.
4. Run the local LiveKit server:
   ```bash
   docker run --rm -p 7880:7880 -p 7881:7881 -p 7882:7882/udp livekit/livekit-server --dev
   ```
