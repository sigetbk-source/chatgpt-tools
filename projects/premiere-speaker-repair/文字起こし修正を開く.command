#!/bin/bash
set -e
cd "$(dirname "$0")"

if [ "$#" -ne 1 ]; then
  echo "使い方: $0 <案件設定JSONのパス>"
  exit 2
fi

if curl -fsS http://127.0.0.1:8877/api/health >/dev/null 2>&1; then
  open http://127.0.0.1:8877/
  exit 0
fi

open http://127.0.0.1:8877/
.venv/bin/python -m speaker_repair.transcript_server "$1"
