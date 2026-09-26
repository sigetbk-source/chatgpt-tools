#!/usr/bin/env python3
"""Trusted local file-IPC launcher for one transcript review workspace.

The Premiere panel may request only status or start/resume. This process owns
the review server; request files cannot supply commands, ports or Python paths.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


NONCE = re.compile(r'^[a-f0-9]{32}$')
MEDIA_SUFFIXES = {'.mov', '.wav', '.mp4', '.m4a', '.mp3'}


def atomic_response(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.reply-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


class Runner:
    def __init__(self, workspace, ipc_root, review_port, *, python=None, japanese_project=None, legacy_root=None):
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.ipc_root = Path(ipc_root).expanduser().resolve(strict=True)
        os.environ['PREMIERE_TRANSCRIPT_IPC_ROOT'] = str(self.ipc_root)
        self.port = int(review_port)
        if not 1024 <= self.port <= 65535: raise ValueError('review port is invalid')
        self.url = f'http://127.0.0.1:{self.port}/'
        executable = Path(python or sys.executable).expanduser().absolute()
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError('review Python executable is unavailable')
        # Keep the venv path: resolving its symlink would launch the base Python.
        self.python = str(executable)
        self.japanese_project = Path(japanese_project).expanduser().resolve(strict=True) if japanese_project else None
        self.legacy_root = Path(legacy_root).expanduser().resolve(strict=True) if legacy_root else None
        for name in ('launcher-inbox', 'launcher-outbox', 'launcher-claims'):
            (self.ipc_root / name).mkdir(exist_ok=True)
        self.lock = (self.ipc_root / 'launcher.lock').open('a')
        try: fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise ValueError('launcher is already running for this IPC root') from None
        self.child = None
        self.japanese_child = None

    def _probe(self):
        try:
            with urllib.request.urlopen(self.url + 'api/project', timeout=1) as response:
                project = json.load(response)
            if not project.get('workspace_root') or Path(project['workspace_root']).resolve() != self.workspace:
                raise ValueError('review port belongs to another workspace')
            return project
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            return None

    def _status(self):
        project = self._probe()
        bridge = {'uxpBridgeConfigured': (self.ipc_root / 'inbox').is_dir() and (self.ipc_root / 'outbox').is_dir(),
                  'cepBridgeConfigured': (self.ipc_root / 'caption-inbox').is_dir() and (self.ipc_root / 'caption-outbox').is_dir()}
        return {'state': 'ready' if project else 'stopped', 'workspace': str(self.workspace),
                'url': self.url if project else None, 'serverRunning': bool(project),
                'detail': '確認画面へ戻れます' if project else '確認画面は停止中です', **bridge}

    def _target(self, value):
        if not isinstance(value, dict) or not all(isinstance(value.get(key), str) and value[key]
            for key in ('projectPath', 'clipName', 'mediaPath')):
            raise ValueError('selected Premiere target is incomplete')
        project = Path(value['projectPath']).expanduser().resolve(strict=True)
        media = Path(value['mediaPath']).expanduser().resolve(strict=True)
        if not project.is_file() or project.suffix.lower() != '.prproj':
            raise ValueError('selected Premiere project is not saved')
        if not media.is_file() or media.suffix.lower() not in MEDIA_SUFFIXES:
            raise ValueError('selected source media is unavailable')
        return media

    def _start_multilingual(self, media):
        state_path = self.workspace / 'state.json'
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding='utf-8'))
            stored = state.get('media')
            if stored and Path(stored).resolve(strict=True) != media:
                raise ValueError('workspace belongs to a different source media')
        running = self._probe()
        if running:
            attached_media = (running.get('workflow') or {}).get('media_path')
            if attached_media and Path(attached_media).resolve(strict=True) != media:
                raise ValueError('running review server belongs to another source media')
            if not attached_media:
                body = json.dumps({'revision': running['revision'], 'media_path': str(media)}).encode()
                request = urllib.request.Request(self.url + 'api/workflow/bind-media', body,
                    {'Content-Type': 'application/json', 'X-Review-Token': running['token']})
                with urllib.request.urlopen(request, timeout=5): pass
            return self._status()
        if self.child and self.child.poll() is None:
            raise ValueError('review server is starting; try again shortly')
        server = Path(__file__).with_name('multilingual_review.py')
        command = [self.python, str(server), '--workspace', str(self.workspace),
                   '--media', str(media), '--port', str(self.port)]
        japanese_url = os.environ.get('PREMIERE_JAPANESE_URL', '')
        if japanese_url: command.extend(['--japanese-url', japanese_url])
        log = (self.workspace / 'review-server.log').open('ab')
        try:
            environment = dict(os.environ, PREMIERE_TRANSCRIPT_IPC_ROOT=str(self.ipc_root))
            self.child = subprocess.Popen(command, cwd=str(server.parent), env=environment,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        finally: log.close()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.child.poll() is not None:
                raise ValueError('review server exited during startup; inspect review-server.log')
            project = self._probe()
            if project:
                attached_media = (project.get('workflow') or {}).get('media_path')
                if not attached_media or Path(attached_media).resolve(strict=True) != media:
                    raise ValueError('review server opened the wrong source media')
                return self._status()
            time.sleep(.2)
        raise ValueError('review server did not become ready; inspect review-server.log')

    def _start_japanese(self, media):
        if not self.japanese_project or not self.legacy_root:
            raise ValueError('日本語の既存案件JSONが未設定です。日本語のみを開く.commandで対象を選んでください')
        data = json.loads(self.japanese_project.read_text(encoding='utf-8'))
        source_dir = Path(data['source_dir']).expanduser()
        if not source_dir.is_absolute(): source_dir = self.legacy_root / source_dir
        source = source_dir / data.get('source_media', data.get('audio', ''))
        if source.resolve(strict=True) != media:
            raise ValueError('日本語案件JSONの素材がPremiereの選択素材と一致しません')
        japanese_url = 'http://127.0.0.1:8877/setup'
        if self.japanese_child and self.japanese_child.poll() is None:
            return {'state': 'ready', 'workspace': str(self.workspace), 'url': japanese_url,
                    'serverRunning': True, 'detail': '日本語の既存画面へ戻れます'}
        # An unowned listener cannot be tied to the selected project identity.
        try:
            with urllib.request.urlopen(japanese_url, timeout=1): pass
            raise ValueError('8877番は別の日本語作業で使用中です。対象を確認してください')
        except urllib.error.URLError:
            pass
        python = self.legacy_root / '.venv/bin/python'
        if not python.is_file(): raise ValueError('日本語処理のPython環境が見つかりません')
        log = (self.workspace / 'japanese-server.log').open('ab')
        try:
            self.japanese_child = subprocess.Popen([str(python), '-m', 'speaker_repair.transcription_intake',
                str(self.japanese_project), '--port', '8877'], cwd=str(self.legacy_root),
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        finally: log.close()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.japanese_child.poll() is not None:
                raise ValueError('日本語の設定画面が起動時に終了しました。japanese-server.logを確認してください')
            try:
                with urllib.request.urlopen(japanese_url, timeout=1) as response:
                    if response.status == 200:
                        return {'state': 'ready', 'workspace': str(self.workspace), 'url': japanese_url,
                                'serverRunning': True, 'detail': '日本語の既存画面を開けます'}
            except urllib.error.URLError:
                pass
            time.sleep(.2)
        raise ValueError('日本語の設定画面が応答しません。japanese-server.logを確認してください')

    def handle(self, request):
        if not isinstance(request, dict) or request.get('version') != 1 or not NONCE.fullmatch(str(request.get('nonce', ''))):
            raise ValueError('invalid launcher request')
        now_ms = int(time.time() * 1000)
        deadline = request.get('deadlineMs')
        if type(deadline) is not int or not now_ms <= deadline <= now_ms + 120000:
            raise ValueError('expired launcher request')
        operation = request.get('operation')
        if operation not in ('status', 'start_or_resume'):
            raise ValueError('unsupported launcher operation')
        payload = request.get('payload')
        if (not isinstance(payload, dict) or not isinstance(payload.get('workspace'), str) or
                Path(payload['workspace']).expanduser().resolve() != self.workspace):
            raise ValueError('request workspace differs from launcher workspace')
        if operation == 'status': return self._status()
        media = self._target(payload.get('target'))
        mode = payload.get('mode')
        if mode == 'multilingual': return self._start_multilingual(media)
        if mode == 'ja-jp': return self._start_japanese(media)
        raise ValueError('unsupported review mode')

    def serve(self):
        inbox = self.ipc_root / 'launcher-inbox'; claims = self.ipc_root / 'launcher-claims'
        outbox = self.ipc_root / 'launcher-outbox'
        while True:
            for path in sorted(inbox.glob('*.json')):
                if not NONCE.fullmatch(path.stem):
                    path.unlink(missing_ok=True); continue
                claimed = claims / path.name
                try: os.replace(path, claimed)
                except FileNotFoundError: continue
                try:
                    request = json.loads(claimed.read_text(encoding='utf-8'))
                    if request.get('nonce') != path.stem: raise ValueError('nonce mismatch')
                    result = self.handle(request)
                    response = {'version': 1, 'nonce': path.stem, 'ok': True, 'result': result}
                except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as error:
                    response = {'version': 1, 'nonce': path.stem, 'ok': False, 'error': str(error)[:500]}
                atomic_response(outbox / path.name, response)
                claimed.unlink(missing_ok=True)
            time.sleep(.1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--ipc-root', type=Path, required=True)
    parser.add_argument('--review-port', type=int, required=True)
    parser.add_argument('--python', type=Path)
    parser.add_argument('--japanese-project', type=Path)
    parser.add_argument('--legacy-root', type=Path)
    args = parser.parse_args()
    runner = Runner(args.workspace, args.ipc_root, args.review_port,
        python=args.python, japanese_project=args.japanese_project, legacy_root=args.legacy_root)
    print('Workflow runner ready for ' + str(runner.workspace), flush=True)
    runner.serve()


if __name__ == '__main__': main()
