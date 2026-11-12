#!/usr/bin/env bash
set -e

echo "Starting Relay Backtester FastAPI Backend on http://localhost:8000 ..."
(cd backend && python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload) &
BACKEND_PID=$!

echo "Starting Relay Backtester Frontend on http://localhost:5173 ..."
(cd frontend && python3 -m http.server 5173) &
FRONTEND_PID=$!

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true" EXIT
wait
