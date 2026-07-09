#!/bin/bash
# Q2 Status Script -- shows whether Q2 is running and recent log lines

SESSION="q2"
IMQD="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGFILE="$IMQD/logs/imq2.log"

echo "=== Q2 STATUS ==="
echo ""

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "  tmux session:  RUNNING (session '$SESSION')"
else
  echo "  tmux session:  NOT RUNNING"
fi

if pgrep -f "python.*main.py" > /dev/null; then
  PID=$(pgrep -f "python.*main.py" | head -1)
  echo "  main.py PID:   $PID"
else
  echo "  main.py PID:   not found"
fi

echo ""
echo "=== LAST 20 LOG LINES ==="
if [ -f "$LOGFILE" ]; then
  tail -20 "$LOGFILE"
else
  echo "  Log file not found: $LOGFILE"
fi
