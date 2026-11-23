#!/bin/bash
set -eu

update_dir="$(cd "$(dirname "$0")" && pwd)"
extension="$HOME/Library/Application Support/Adobe/CEP/extensions/jp.tbk.transcript.caption"
if [[ ! -f "$extension/panel.js" || ! -f "$extension/jsx/host.jsx" ]]; then
  printf '%s\n' "Transcript Caption Placement が見つかりません。" >&2
  exit 1
fi

backup="$(mktemp -d "$extension/../.caption-placement-backup.XXXXXX")"
cp -p "$extension/panel.js" "$extension/jsx/host.jsx" "$backup/"
panel_stage="$(mktemp "$extension/.panel.XXXXXX")"
host_stage="$(mktemp "$extension/jsx/.host.XXXXXX")"
trap 'rm -f "$panel_stage" "$host_stage"' EXIT

cp "$update_dir/panel.js" "$panel_stage"
cp "$update_dir/jsx/host.jsx" "$host_stage"
chmod 644 "$panel_stage" "$host_stage"
mv -f "$host_stage" "$extension/jsx/host.jsx"
if ! mv -f "$panel_stage" "$extension/panel.js"; then
  cp -p "$backup/host.jsx" "$extension/jsx/host.jsx"
  exit 1
fi
printf 'CEPを更新しました。バックアップ: %s\n' "$backup"
printf '%s\n' 'Premiereでプロジェクトを保存し、Premiereを再起動してから Transcript Caption Placement を開いてください。'
