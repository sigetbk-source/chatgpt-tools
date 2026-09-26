#!/bin/bash
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
if [ "$#" -lt 1 ]; then
  default_root="${PREMIERE_REVIEW_WORKSPACES:-$HOME/Documents/Premiere Transcript Reviews}"
  printf '作業フォルダを入力してください（空欄で新規作成）: '
  IFS= read -r entered
  workspace="${entered:-$default_root/$(date +%Y%m%d-%H%M%S)}"
else
  workspace="$1"
fi
port="${2:-8892}"
media="${3:-}"
python_bin="${PREMIERE_REVIEW_PYTHON:-}"
if [ -z "$python_bin" ]; then
  runtime="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
  if [ -x "$runtime" ]; then python_bin="$runtime"; else python_bin="$(command -v python3)"; fi
fi
args=(--workspace "$workspace" --port "$port")
if [ -n "$media" ]; then args+=(--media "$media"); fi
if [ -n "${PREMIERE_JAPANESE_URL:-}" ]; then args+=(--japanese-url "$PREMIERE_JAPANESE_URL"); fi
if curl -fsS "http://127.0.0.1:$port/api/project" >/dev/null 2>&1; then
  printf 'ポート %s は使用中です。別の番号を指定してください。\n' "$port" >&2
  exit 1
fi
"$python_bin" "$here/python/multilingual_review.py" "${args[@]}" &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do
  if ! kill -0 "$server_pid" 2>/dev/null; then wait "$server_pid"; fi
  if curl -fsS "http://127.0.0.1:$port/api/project" >/dev/null 2>&1; then
    open "http://127.0.0.1:$port/"
    wait "$server_pid"
    exit $?
  fi
  sleep 1
done
printf '画面を起動できませんでした。\n' >&2
exit 1
