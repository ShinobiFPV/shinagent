#!/bin/bash
# Attach to Q2's tmux session (or show log if not running)
SESSION="q2"
IMQD="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "Attaching to Q2 session. Press Ctrl+B then D to detach."
  sleep 1
  tmux attach -t "$SESSION"
else
  echo "Q2 is not running. Start it with: bash scripts/q2_start.sh"
  echo ""
  echo "Showing recent log instead:"
  tail -30 "$IMQD/logs/imq2.log"
fi
