#!/bin/bash
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
if [ "$#" -lt 3 ]; then
  printf '使い方: %s 作業フォルダ IPCフォルダ 確認画面ポート [日本語案件JSON 日本語ツールフォルダ]\n' "$0" >&2
  exit 2
fi
python_bin="${PREMIERE_REVIEW_PYTHON:-$(command -v python3)}"
args=(--workspace "$1" --ipc-root "$2" --review-port "$3" --python "$python_bin")
if [ "$#" -ge 5 ]; then args+=(--japanese-project "$4" --legacy-root "$5"); fi
"$python_bin" "$here/python/workflow_runner.py" "${args[@]}"
