#!/bin/sh
cd "$(dirname "$0")"
PORT="${1:-8787}"
export PYTHONPATH="$(pwd)${PYTHONPATH:+:$PYTHONPATH}"
echo "FireMind: http://127.0.0.1:${PORT}"
echo "LLM: ${OPENAI_API_KEY:+enabled}${OPENAI_API_KEY:-disabled (rule engine only)}"
python3 app.py
