#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Quaderno Status
# @raycast.mode fullOutput

# Optional parameters:
# @raycast.icon 🔋
# @raycast.packageName Quaderno Companion

curl -s http://localhost:5000/api/device/status
