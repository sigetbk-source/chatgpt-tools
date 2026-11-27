"""Start the local test OCR watcher without keeping a terminal attached."""

import argparse
import fcntl
import json
import os
import plistlib
import subprocess
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('configuration', type=Path)
    args = parser.parse_args()
    config = args.configuration.resolve()
    base = config.parent
    os.umask(0o077)

    with (base / 'watch.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('LINE OCRの監視はすでに起動しています。')
            return 0

        with config.open('rb') as source:
            settings = plistlib.load(source)
        arguments = settings['ProgramArguments']
        report_folder = settings.get('OcrReportFolder', arguments[4])
        if '--watch' not in arguments:
            arguments.append('--watch')
        with (base / 'status.log').open('a') as status, (base / 'error.log').open('a') as errors:
            worker = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=status, stderr=errors,
                                      start_new_session=True, pass_fds=(lock.fileno(),))

        heartbeat = Path(report_folder) / 'watch-status.json'
        for _ in range(30):
            if worker.poll() is not None:
                print('LINE OCRの監視を起動できませんでした。error.logを確認してください。')
                return 1
            try:
                result = json.loads(heartbeat.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                result = {}
            if result.get('pid') == worker.pid:
                print('LINE OCRのバックグラウンド監視を起動しました。')
                print(json.dumps({key: value for key, value in result.items() if key not in {'pid', 'checked_at'}}))
                return int(bool(result.get('ocr_unavailable')))
            time.sleep(0.5)
        print('LINE OCRの監視を開始しました。Driveの同期を待っています。')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
