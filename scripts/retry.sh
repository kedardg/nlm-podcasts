#!/bin/bash
# Generic retry wrapper with exponential backoff.
# Usage: ./retry.sh <max_retries> <command...>
# Example: ./retry.sh 3 ~/.local/bin/nlm source add research --url "https://..." --wait
#
# Backoff schedule: 5s, 15s, 45s (base * 3^attempt)
# Exit code: last command's exit code (0 = success)

set -euo pipefail

if [ $# -lt 2 ]; then
  echo "Usage: retry.sh <max_retries> <command...>" >&2
  exit 1
fi

MAX_RETRIES="$1"
shift
CMD=("$@")

BASE_DELAY=5
attempt=0

while [ $attempt -le "$MAX_RETRIES" ]; do
  if [ $attempt -gt 0 ]; then
    # Exponential backoff: 5s, 15s, 45s, ...
    delay=$(( BASE_DELAY * (3 ** (attempt - 1)) ))
    echo "retry: attempt $attempt/$MAX_RETRIES, waiting ${delay}s..." >&2
    sleep "$delay"
  fi

  if "${CMD[@]}"; then
    exit 0
  fi

  attempt=$((attempt + 1))
done

echo "retry: all $MAX_RETRIES retries exhausted for: ${CMD[*]}" >&2
exit 1
