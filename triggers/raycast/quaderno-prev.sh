#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Quaderno Previous Page
# @raycast.mode compact

# Optional parameters:
# @raycast.icon ◀️
# @raycast.packageName Quaderno Companion

curl -s -X POST http://localhost:5000/api/viewer/page \
  -H "Content-Type: application/json" \
  -d '{"action":"prev"}' | grep -o '"message":[^,]*' || echo "Failed to connect to Quaderno daemon"
