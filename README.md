# Voice Agent MVP

A browser-based realtime voice agent MVP using LiveKit, TEN VAD, faster-whisper, Gemini Flash, and Fish Audio.

## Architecture

This project is built to scale via an automatic session-start fallback. 
The `token_server` will automatically try to create the room in **LiveKit Cloud**. If your cloud quota is exhausted or billing fails, it will seamlessly fall back to your standby **LiveKit Local Docker instance**. 

Your frontend connects to whichever URL the token server hands back.

## Setup

1. Copy `.env.example` to `.env` and fill in your API keys (both Cloud and Local).
2. Install Python dependencies using `uv sync`.
3. Install frontend dependencies in `frontend/` using `npm install`.

## Running the Project

You will need four terminals to run the system with local standby fallback:

**1. Standby Local LiveKit Server (Docker)**
```bash
docker run --rm -p 7880:7880 -p 7881:7881 -p 7882:7882/udp livekit/livekit-server --dev
```

**2. Token/Routing Server**
```bash
uv run python -m agent.token_server
```

**3. Concurrent Agent Workers**
Since we are using routing, you need to run TWO agent workers side-by-side so an agent is available no matter where the user lands. They will read their respective credentials directly from your `.env` file.

*Terminal 3A (Cloud Worker):*
```bash
WORKER_TARGET=cloud uv run python -m agent.main start
```

*Terminal 3B (Local Standby Worker):*
```bash
WORKER_TARGET=local uv run python -m agent.main start
```

**4. React Frontend**
```bash
cd frontend && npm run dev
```

Open `http://localhost:5173` and click Start Conversation!
