#!/usr/bin/env bash
# Batman Bot - first-time setup for macOS / Linux.
set -e
cd "$(dirname "$0")"

echo
echo "===================================================================="
echo " BATMAN BOT - first-time setup"
echo "===================================================================="
echo

if command -v python3.12 >/dev/null 2>&1; then
    PY=python3.12
elif command -v python3.11 >/dev/null 2>&1; then
    PY=python3.11
elif command -v python3 >/dev/null 2>&1; then
    PY=python3
else
    echo "[ERROR] python3 is not installed."
    echo "Install Python 3.11 or newer from your package manager"
    echo "  macOS:    brew install python@3.12"
    echo "  Ubuntu:   sudo apt install python3.12 python3.12-venv"
    exit 1
fi

if [ ! -d .venv ]; then
    echo "Creating virtual environment in .venv ..."
    "$PY" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "Installing dependencies (first time may take a minute) ..."
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt

python setup.py

chmod +x run.sh 2>/dev/null || true

echo
echo "===================================================================="
echo " All set. Use these next time:"
echo "   ./run.sh  - start the bot and admin dashboard"
echo "===================================================================="
echo

read -r -p "Start the bot now? [Y/n] " ans
case "$ans" in
    ""|[Yy]*)
        echo "Press Ctrl+C to stop the bot."
        python bot.py
        ;;
esac
