#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Quaderno Status
# @raycast.mode fullOutput

# Optional parameters:
# @raycast.icon 🔋
# @raycast.packageName Quaderno Companion

API_KEY="${QUADERNO_API_KEY:-$(grep '^QUADERNO_API_KEY=' "$HOME/.config/quaderno/.env" 2>/dev/null | cut -d '=' -f2-)}"

curl -s -H "X-API-Key: $API_KEY" http://localhost:5000/api/device/status
