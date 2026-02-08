#!/bin/bash
set -uo pipefail

PROMPT="${1:-}"
MODEL="${2:-gpt-5.2-codex}"
EFFORT="${3:-xhigh}"
TIMEOUT="${4:-600}"
VERBOSE="${5:-brief}"  # brief|full

if [[ -z "$PROMPT" ]]; then
  echo "Usage: ask_codex.sh <prompt> [model] [effort] [timeout] [verbose]" >&2
  exit 1
fi

# Capture output even on timeout (exit 124) or error
OUTPUT=$(timeout "$TIMEOUT" codex exec \
  --model "$MODEL" \
  --sandbox read-only \
  -c model_reasoning_effort="$EFFORT" \
  -c approval_policy="never" \
  -- "$PROMPT" 2>&1) || true

if [[ "$VERBOSE" == "brief" ]]; then
  # Extract just the final response
  echo "$OUTPUT" | awk '
    /^codex$/ { capture=1; next }
    /^tokens used$/ { exit }
    capture { print }
  '
else
  echo "$OUTPUT"
fi
