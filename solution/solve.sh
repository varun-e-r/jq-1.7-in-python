#!/usr/bin/env bash
# Reference solution: install the full Python implementation over the starter.
set -euo pipefail
rm -rf /app/pyjq
cp -R /solution/pyjq /app/pyjq
cp /solution/jq /app/jq
chmod +x /app/jq
find /app -name __pycache__ -prune -exec rm -rf {} +
