#!/bin/bash

# Error logging script for Claude Code sessions
# Usage: ~/repos/pm/log-error.sh "Tool" "error_type" "error_message" ["context"] ["skill_involved"] ["recovery"]
# Example: ~/repos/pm/log-error.sh "Edit" "not_found" "String to replace not found in file"

set -e

QUEUE_FILE="$HOME/repos/pm/error-queue.json"
TOOL="${1:-Unknown}"
ERROR_TYPE="${2:-other}"
ERROR_MESSAGE="${3:-}"
CONTEXT="${4:-}"
SKILL_INVOLVED="${5:-}"
RECOVERY="${6:-}"

# Create error-queue.json if it doesn't exist
if [ ! -f "$QUEUE_FILE" ]; then
  echo "[]" > "$QUEUE_FILE"
fi

# Get the last ID and increment
LAST_ID=$(jq 'if length > 0 then .[-1].id else 0 end' "$QUEUE_FILE")
NEW_ID=$((LAST_ID + 1))

# Get current timestamp in ISO 8601 format
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%S-07:00")

# Create new error entry
NEW_ERROR=$(cat <<EOF
{
  "id": $NEW_ID,
  "timestamp": "$TIMESTAMP",
  "tool": "$TOOL",
  "error_type": "$ERROR_TYPE",
  "error_message": "$ERROR_MESSAGE",
  "context": "$CONTEXT",
  "skill_involved": "$SKILL_INVOLVED",
  "recovery": "$RECOVERY",
  "status": "open"
}
EOF
)

# Append to error-queue.json
jq ". += [$NEW_ERROR]" "$QUEUE_FILE" > "$QUEUE_FILE.tmp"
mv "$QUEUE_FILE.tmp" "$QUEUE_FILE"

echo "✓ Error #$NEW_ID logged: $TOOL - $ERROR_TYPE"
