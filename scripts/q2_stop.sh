#!/bin/bash
# Q2 Stop Script
SESSION="q2"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "Stopping Q2..."
  tmux kill-session -t "$SESSION"
  echo "Q2 stopped."
else
  echo "Q2 is not running (no tmux session '$SESSION' found)."
fi
