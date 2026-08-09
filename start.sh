#!/usr/bin/env bash

# LifeTrace AI One-Command Concurrent Launcher Script

echo "🌱 Starting LifeTrace AI System..."

# Determine host and port
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
STREAMLIT_PORT="${STREAMLIT_PORT:-8501}"

export API_HOST="${API_HOST:-127.0.0.1}"
export API_PORT="${API_PORT:-8000}"

# Activate virtual environment if present
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# 1. Start FastAPI Backend in background
echo "⚡ Starting FastAPI Backend on http://${HOST}:${PORT}..."
uvicorn src.backend.main:app --host "${HOST}" --port "${PORT}" &
BACKEND_PID=$!

# Trap signals to clean up background backend process when script exits
trap "echo '🛑 Stopping LifeTrace AI...'; kill $BACKEND_PID 2>/dev/null; exit 0" INT TERM EXIT

# Wait briefly for backend to spin up
sleep 2

# 2. Start Streamlit Frontend in foreground
echo "🎨 Starting Streamlit Frontend Dashboard on http://localhost:${STREAMLIT_PORT}..."
streamlit run src/frontend/app.py --server.port "${STREAMLIT_PORT}" --server.address "${HOST}"
