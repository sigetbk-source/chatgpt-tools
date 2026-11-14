#!/bin/bash
set -e
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then
  exec .venv/bin/python -m speaker_repair.uxp_bridge
fi
exec python3 -m speaker_repair.uxp_bridge
