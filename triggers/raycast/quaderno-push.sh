#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Push URL to Quaderno
# @raycast.mode compact

# Optional parameters:
# @raycast.icon 🚀
# @raycast.argument1 { "type": "text", "placeholder": "URL or local PDF path" }
# @raycast.packageName Quaderno Companion

TARGET_URL="$1"

if [ -z "$TARGET_URL" ]; then
  echo "Please provide a valid URL or path"
  exit 1
fi

API_KEY="${QUADERNO_API_KEY:-$(grep '^QUADERNO_API_KEY=' "$HOME/.config/quaderno/.env" 2>/dev/null | cut -d '=' -f2-)}"

curl -s -X POST http://localhost:5000/api/agent/push \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d "{\"url\":\"$TARGET_URL\"}" | grep -o '"message":[^,]*' || echo "Pushed to Quaderno"
