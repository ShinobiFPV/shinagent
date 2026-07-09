#!/bin/bash
# Watch Q2's log with colour highlighting
# Usage: bash scripts/q2_log.sh [lines]
LINES="${1:-50}"
IMQD="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGFILE="$IMQD/logs/imq2.log"

if [ ! -f "$LOGFILE" ]; then
  echo "Log file not found: $LOGFILE"
  exit 1
fi

echo "Watching Q2 log (Ctrl+C to stop)..."
echo ""

tail -n "$LINES" -f "$LOGFILE" | while IFS= read -r line; do
  if echo "$line" | grep -q "ERROR"; then
    echo -e "\033[31m$line\033[0m"      # red for errors
  elif echo "$line" | grep -q "WARNING"; then
    echo -e "\033[33m$line\033[0m"      # yellow for warnings
  elif echo "$line" | grep -q "Tool call"; then
    echo -e "\033[35m$line\033[0m"      # magenta for tool calls
  elif echo "$line" | grep -q "Tool result"; then
    echo -e "\033[36m$line\033[0m"      # cyan for tool results
  elif echo "$line" | grep -q "HTTP Request"; then
    echo -e "\033[2m$line\033[0m"       # dim for HTTP noise
  else
    echo "$line"
  fi
done
