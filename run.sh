#!/usr/bin/env bash
cd "$(dirname "$0")"

if [ ! -d .venv ] || [ ! -f .env ]; then
    echo "[ERROR] Setup hasn't been run yet. Run ./setup.sh first."
    exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate
echo "Press Ctrl+C to stop the bot."
exec python bot.py
