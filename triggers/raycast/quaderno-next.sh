#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Quaderno Next Page
# @raycast.mode compact

# Optional parameters:
# @raycast.icon 📖
# @raycast.packageName Quaderno Companion

API_KEY="${QUADERNO_API_KEY:-$(grep '^QUADERNO_API_KEY=' "$HOME/.config/quaderno/.env" 2>/dev/null | cut -d '=' -f2-)}"

curl -s -X POST http://localhost:5000/api/viewer/page \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"action":"next"}' | grep -o '"message":[^,]*' || echo "Failed to connect to Quaderno daemon"
