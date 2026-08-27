#!/bin/bash
# Aiva — one command to start everything
# Usage:  bash start_aiva.sh
# Stop:   Ctrl+C (kills everything)

cd "$(dirname "$0")"

echo "Updating code..."
git pull origin arena/01a03e6f-voice-agent 2>/dev/null

echo "Killing old processes..."
kill -9 $(lsof -ti:3001) 2>/dev/null
kill -9 $(lsof -ti:8081) 2>/dev/null
kill -9 $(lsof -ti:5173) 2>/dev/null
sleep 1

echo "Starting token server..."
uv run python -m agent.token_server &
T1=$!

sleep 1
echo "Starting worker..."
AIVA_STATE_ENGINE=1 WORKER_TARGET=cloud uv run python -m agent.main start &
T2=$!

sleep 2
echo "Starting frontend..."
cd frontend && npm run dev &
T3=$!
cd ..

echo ""
echo "=========================================="
echo "  Aiva is starting..."
echo "  Frontend: http://localhost:5173"
echo ""
echo "  Press Ctrl+C to stop everything"
echo "=========================================="
echo ""

# Wait for Ctrl+C, then kill all children
trap "kill -9 $T1 $T2 $T3 2>/dev/null; echo; echo 'Aiva stopped.'; exit 0" INT TERM
wait
