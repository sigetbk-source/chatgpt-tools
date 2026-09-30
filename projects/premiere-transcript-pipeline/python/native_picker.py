"""macOS file choices for the local review UI; no media is copied or uploaded."""
import argparse
import json
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from uuid import uuid4

_PICKER_LOCK = threading.Lock()
MEDIA_SUFFIXES = {'.mov', '.wav', '.mp4', '.m4a', '.mp3'}
REFERENCE_SUFFIXES = {'.txt', '.md', '.csv', '.docx', '.pdf'}


def pick_paths(kind):
    scripts = {
        'media': 'JSON.stringify([String(app.chooseFile({withPrompt:"文字起こしする音声・動画を選択"}))]);',
        'references': 'JSON.stringify(app.chooseFile({withPrompt:"資料を選択（PDF・Word・テキスト、複数選択可）",multipleSelectionsAllowed:true}).map(String));',
        'workspace': 'JSON.stringify([String(app.chooseFolder({withPrompt:"新規: 案件フォルダを選択 ／ 再開: state.jsonのある作業フォルダを選択"}))]);',
    }
    if kind not in scripts:
        raise ValueError('選択項目が不正です')
    if not _PICKER_LOCK.acquire(blocking=False):
        raise ValueError('別の選択画面が開いています。完了してから選択してください')
    try:
        result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', '-e',
            'var app=Application.currentApplication();app.includeStandardAdditions=true;' + scripts[kind]],
            capture_output=True, text=True, timeout=600)
        if result.returncode:
            if '-128' in result.stderr:
                return None
            raise ValueError('ファイル選択画面を開けませんでした。Macの画面と許可を確認してください')
        selected = json.loads(result.stdout)
        if not isinstance(selected, list) or not selected or not all(isinstance(x, str) for x in selected):
            raise ValueError('選択されたファイルを取得できません')
        paths = list(dict.fromkeys(Path(x).expanduser().resolve(strict=True) for x in selected))
        if kind == 'workspace':
            if len(paths) != 1 or not paths[0].is_dir():
                raise ValueError('フォルダを一つ選んでください')
        elif kind == 'media':
            if len(paths) != 1 or not paths[0].is_file() or paths[0].suffix.lower() not in MEDIA_SUFFIXES:
                raise ValueError('MOV・MP4・WAV・MP3・M4Aから素材を一つ選んでください')
        elif len(paths) > 20 or any(not x.is_file() or x.suffix.lower() not in REFERENCE_SUFFIXES for x in paths):
            raise ValueError('資料はPDF・DOCX・TXT・MD・CSVを20件以内で選んでください')
        return list(map(str, paths))
    except (json.JSONDecodeError, subprocess.TimeoutExpired):
        raise ValueError('選択を完了できませんでした。もう一度選んでください') from None
    finally:
        _PICKER_LOCK.release()


def workspace_from_folder(folder):
    folder = Path(folder).resolve(strict=True)
    if not folder.is_dir():
        raise ValueError('案件フォルダを選んでください')
    if (folder / 'state.json').is_file():
        return folder
    return folder / '文字起こし作業' / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:6])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', action='store_true')
    args = parser.parse_args()
    if not args.workspace:
        parser.error('--workspaceを指定してください')
    selected = pick_paths('workspace')
    if selected:
        print(workspace_from_folder(selected[0]))


if __name__ == '__main__':
    main()
