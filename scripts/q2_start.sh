#!/bin/bash
# Q2 Start Script
# Usage: bash scripts/q2_start.sh [--text] [--llm claude]
# Starts Q2 in a tmux session named 'q2' so it persists across SSH connections.

SESSION="q2"
IMQD="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Prefer the project-local venv (what setup.sh/README create), but fall
# back to a home-directory-level one if that's what actually exists on
# this machine -- checking both is more robust than hardcoding either,
# since which one is real varies per install.
if [ -d "$IMQD/.venv" ]; then
  VENV="$IMQD/.venv"
elif [ -d "$HOME/.venv" ]; then
  VENV="$HOME/.venv"
else
  echo "ERROR: No virtual environment found at $IMQD/.venv or $HOME/.venv"
  echo "Create one with: python3 -m venv $IMQD/.venv"
  exit 1
fi
ARGS="${@:---face}"   # default to --face if no args given

# Kill existing session if running
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "Stopping existing Q2 session..."
  tmux kill-session -t "$SESSION"
  sleep 1
fi

# Start new session
echo "Starting Q2 with args: $ARGS"
tmux new-session -d -s "$SESSION" \
  "cd $IMQD && source $VENV/bin/activate && python3 main.py $ARGS; \
   echo 'Q2 exited. Press Enter to close.'; read"

echo ""
echo "Q2 is running in tmux session '$SESSION'"
echo ""
echo "To watch Q2:    tmux attach -t $SESSION"
echo "To detach:      Ctrl+B then D"
echo "To stop Q2:     bash scripts/q2_stop.sh"
echo "To see logs:    tail -f $IMQD/logs/imq2.log"
