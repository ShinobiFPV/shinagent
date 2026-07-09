#!/bin/bash
# ShinAgent Setup Bootstrap
# Run this once after cloning: bash setup.sh
set -e

IMQD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$IMQD"

echo ""
echo "  =================================="
echo "   ShinAgent Setup"
echo "   ShinTech Electronics"
echo "  =================================="
echo ""

# --- Platform detection ---
# Windows here means Git Bash/MSYS/Cygwin (this script needs *some* bash),
# not native cmd.exe/PowerShell -- uname -s reports MINGW64_NT-*/MSYS_NT-*/
# CYGWIN_NT-* in those environments, never on real Linux or macOS.
IS_WINDOWS=false
case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*) IS_WINDOWS=true ;;
esac

# --- Idempotency: already set up? ---
if [ -f ".env" ] && [ -f "config/config.yaml" ]; then
  echo "It looks like ShinAgent has already been set up (.env exists)."
  echo ""
  echo "  1) Re-run the setup wizard (review/change existing config)"
  echo "  2) Skip setup and start ShinAgent now"
  echo "  3) Exit"
  echo ""
  read -rp "Choose [1/2/3]: " CHOICE
  case "$CHOICE" in
    2)
      exec bash scripts/q2_start.sh
      ;;
    3)
      echo "Exiting."
      exit 0
      ;;
    *)
      echo "Re-running setup wizard..."
      ;;
  esac
fi

# --- Check Python 3.11+ ---
python3 --version >/dev/null 2>&1 || { echo "ERROR: Python 3 not found."; exit 1; }
PYVER=$(python3 -c "import sys; print(sys.version_info >= (3,11))")
if [ "$PYVER" != "True" ]; then
  echo "ERROR: Python 3.11 or higher required."
  echo "Install: sudo apt install python3.11"
  exit 1
fi

# --- Install system dependencies (Raspberry Pi OS / Debian only) ---
if [ "$IS_WINDOWS" = true ]; then
  echo "Windows detected -- skipping apt-get (there is no equivalent here)."
  echo "  Voice/audio and kiosk-display features (portaudio, ffmpeg,"
  echo "  chromium, tmux) are Pi/Linux-specific; on Windows you'll typically"
  echo "  run ShinAgent's Windows-side pieces instead -- see windows/ and hud/."
elif command -v apt-get >/dev/null 2>&1; then
  echo "Installing system dependencies..."
  sudo apt-get update -qq
  sudo apt-get install -y -qq \
    portaudio19-dev \
    ffmpeg \
    chromium-browser \
    tmux \
    v4l-utils \
    python3-pip \
    python3-venv
else
  echo "  (apt-get not found -- skipping system package install."
  echo "   Install portaudio, ffmpeg, chromium, tmux, v4l-utils manually if needed.)"
fi

# --- Create virtual environment if not exists ---
if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi

# --- Activate and install minimal deps for the wizard itself ---
if [ "$IS_WINDOWS" = true ]; then
  source .venv/Scripts/activate
else
  source .venv/bin/activate
fi
pip install --quiet flask requests pyyaml

# --- Launch the setup wizard ---
if [ "$IS_WINDOWS" = true ]; then
  LOCAL_IP=$(ipconfig 2>/dev/null | grep -m1 "IPv4" | awk -F': ' '{print $2}' | tr -d '\r')
else
  LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
fi
LOCAL_IP="${LOCAL_IP:-localhost}"

echo ""
echo "Starting setup wizard..."
echo "Open your browser to: http://$LOCAL_IP:8080/setup"
echo "Or on this machine:   http://localhost:8080/setup"
echo ""

# Best-effort auto-open a browser on this machine (headless Pi over SSH
# has no display, so failure here is silent and expected).
if [ "$IS_WINDOWS" = true ]; then
  ( sleep 1.5; start "" "http://localhost:8080/setup" >/dev/null 2>&1 ) &
elif [ -n "$DISPLAY" ]; then
  ( sleep 1.5
    if command -v xdg-open >/dev/null 2>&1; then
      xdg-open "http://localhost:8080/setup" >/dev/null 2>&1
    elif command -v chromium-browser >/dev/null 2>&1; then
      chromium-browser --new-window "http://localhost:8080/setup" >/dev/null 2>&1 &
    fi
  ) &
fi

python3 setup_wizard.py

# --- Wizard has shut itself down (either on completion, or Ctrl+C) ---
echo ""
echo "  =================================="
echo "   Setup wizard closed."
echo "   Web app:  http://$LOCAL_IP:8766"
echo "   Settings: http://$LOCAL_IP:8766/settings"
echo "   Start:    bash scripts/q2_start.sh"
echo "  =================================="
echo ""
