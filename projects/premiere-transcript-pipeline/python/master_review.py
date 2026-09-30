#!/usr/bin/env python3
"""Local canonical master review. Never mutates Premiere or source files."""
import argparse
from copy import deepcopy
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import tempfile
import threading
from urllib.parse import urlsplit
import uuid
from master_edit import edit
from premiere_export import convert


def atomic_json(path, value):
    fd, name = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def utterance_text(utterance):
    return ''.join(w['text'] for w in utterance['words'])


def boundaries(utterance):
    result, offset = [], 0
    for word in utterance['words'][:-1]:
        offset += len(word['text'].encode('utf-16-le')) // 2
        result.append(offset)
    return result


class Conflict(ValueError):
    pass


class Workspace:
    def __init__(self, master, workspace, media=None, speaker_ids=None):
        self.root = Path(workspace).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock_file = (self.root / '.lock').open('a')
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock_file.close()
            raise ValueError('workspace is already open') from None
        self.lock = threading.RLock()
        self.token = secrets.token_urlsafe(32)
        self.path = self.root / 'state.json'
        try:
            source = Path(master).resolve(strict=True)
            if self.root in source.parents:
                raise ValueError('source master must be outside workspace')
            raw = source.read_bytes()
            identity = {'path': str(source), 'sha256': hashlib.sha256(raw).hexdigest()}
            original = json.loads(raw)
            converted = convert(original, speaker_ids)
            names = [s['name'] for s in original['speakers']]
            if len(names) != len(set(names)):
                raise ValueError('speaker names must be unique')
            self.media = Path(media).resolve(strict=True) if media else None
            if self.media and (not self.media.is_file() or self.root in self.media.parents):
                raise ValueError('media must be a file outside workspace')
            if self.path.exists():
                self.state = json.loads(self.path.read_text(encoding='utf-8'))
                if self.state['source'] != identity:
                    raise ValueError('workspace source differs or was modified')
                if speaker_ids is not None and self.state['speaker_ids'] != {k: str(uuid.UUID(v)) for k, v in speaker_ids.items()}:
                    raise ValueError('workspace speaker IDs cannot change')
                stored_media = self.state.get('media')
                if self.media is not None and str(self.media) != stored_media:
                    raise ValueError('workspace media cannot change')
                self.media = Path(stored_media).resolve(strict=True) if stored_media else None
                convert(self.state['master'], self.state['speaker_ids'])
            else:
                self.state = {'source': identity, 'master': original,
                              'speaker_ids': {s['key']: p['id'] for s, p in zip(original['speakers'], converted['speakers'])},
                              'history': [], 'future': [], 'revision': 0, 'media': str(self.media) if self.media else None}
                atomic_json(self.path, self.state)
        except Exception:
            self.close()
            raise

    def close(self):
        self.lock_file.close()

    def project(self):
        with self.lock:
            state = self.state
            names = {s['key']: s['name'] for s in state['master']['speakers']}
            items = []
            for index, utterance in enumerate(state['master']['utterances']):
                value = utterance_text(utterance)
                start = utterance['words'][0]['start']
                items.append({'segment_index': index, 'speaker_name': names[utterance['speaker']],
                              'original_text': value, 'whisper_text': value, 'suggested_text': value,
                              'start_seconds': start, 'end_seconds': max(w['end'] for w in utterance['words']),
                              'premiere_start': start, 'change_score': 0, 'needs_review': False,
                              'word_boundaries': boundaries(utterance)})
            return {'project_name': Path(state['source']['path']).stem,
                    'source_media_name': self.media.name if self.media else '', 'has_media': bool(self.media),
                    'has_video': bool(self.media and self.media.suffix.lower() in ('.mov', '.mp4', '.m4v', '.webm')),
                    'candidates': {'items': items}, 'decisions': {}, 'speaker_options': list(names.values()),
                    'revision': state['revision'], 'token': self.token,
                    'edit_history': {'can_undo': bool(state['history']), 'can_redo': bool(state['future']), 'undo_count': len(state['history']), 'redo_count': len(state['future'])}}

    def mutate(self, route, payload):
        with self.lock:
            route = {'/api/segments/undo': '/api/undo', '/api/segments/redo': '/api/redo'}.get(route, route)
            if type(payload.get('revision')) is not int or payload['revision'] != self.state['revision']:
                raise Conflict('stale or missing revision; reload project')
            if route == '/api/export':
                return self.export()
            state = deepcopy(self.state)
            if route in ('/api/undo', '/api/redo'):
                source, dest = ('history', 'future') if route == '/api/undo' else ('future', 'history')
                if not state[source]:
                    raise ValueError('no edit to undo or redo')
                state[dest].append(state['master'])
                state['master'] = state[source].pop()
            elif route in ('/api/speaker', '/api/segments/split', '/api/segments/merge-next'):
                index = payload.get('segment_index')
                utterances = state['master']['utterances']
                if type(index) is not int or not 0 <= index < len(utterances):
                    raise ValueError('invalid segment index')
                current = utterances[index]
                key, word_index = current['speaker'], None
                if route == '/api/speaker':
                    if payload.get('scope', 'segment') != 'segment':
                        raise ValueError('only segment edits supported')
                    keys = [s['key'] for s in state['master']['speakers'] if s['name'] == payload.get('speaker_name')]
                    if len(keys) != 1:
                        raise ValueError('select an existing unique speaker')
                    key, operation = keys[0], 'assign'
                else:
                    if payload.get('current_text') != utterance_text(current):
                        raise Conflict('segment text changed')
                    if route.endswith('split'):
                        caret, choices = payload.get('caret'), boundaries(current)
                        if type(caret) is not int or caret not in choices:
                            raise ValueError('split must be at an exact word boundary')
                        operation, word_index = 'split', choices.index(caret) + 1
                    else:
                        if index + 1 >= len(utterances) or payload.get('next_text') != utterance_text(utterances[index + 1]):
                            raise Conflict('adjacent segment changed')
                        operation = 'merge'
                changed = edit(state['master'], operation, index, key, word_index)
                state['history'].append(state['master'])
                state['master'], state['future'] = changed, []
            else:
                raise ValueError('unsupported endpoint')
            state['revision'] += 1
            atomic_json(self.path, state)
            self.state = state
            return {'ok': True, 'revision': state['revision'], 'project': self.project()}

    def export(self):
        exports = self.root / 'exports'
        exports.mkdir(exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.pending-', dir=exports))
        try:
            for name, value in [('master.json', self.state['master']), ('speaker-ids.json', self.state['speaker_ids']),
                                ('premiere.json', convert(self.state['master'], self.state['speaker_ids']))]:
                atomic_json(staging / name, value)
            target = exports / ('export-' + uuid.uuid4().hex)
            staging.rename(target)
        except Exception:
            for item in staging.iterdir():
                item.unlink()
            staging.rmdir()
            raise
        return {'ok': True, 'json': str(target / 'premiere.json'), 'json_path': str(target / 'premiere.json'), 'master_path': str(target / 'master.json'),
                'speaker_ids_path': str(target / 'speaker-ids.json'), 'patch_count': len(self.state['history']),
                'revision': self.state['revision']}


def make_server(workspace, port=8891, static=None):
    static = Path(static or Path(__file__).resolve().parent.parent / 'review_static').resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, value):
            body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def trusted(self):
            port = self.server.server_port
            if self.headers.get('Host') not in {f'127.0.0.1:{port}', f'localhost:{port}'}:
                self.reply(403, {'error': 'invalid host'})
                return False
            origin = self.headers.get('Origin')
            if origin is not None and origin != 'http://' + self.headers.get('Host'):
                self.reply(403, {'error': 'invalid origin'})
                return False
            return True

        def do_GET(self):
            if not self.trusted():
                return
            route = urlsplit(self.path).path
            if route == '/api/project':
                self.reply(200, workspace.project())
                return
            media = route in ('/media', '/audio.wav')
            filename = {'/': 'index.html', '/index.html': 'index.html', '/app.js': 'app.js', '/styles.css': 'styles.css'}.get(route)
            path = workspace.media if media else static / filename if filename else None
            if path is None or not path.is_file():
                self.reply(404, {'error': 'not found'})
                return
            size = path.stat().st_size
            start, end = 0, size - 1
            range_header = self.headers.get('Range') if media else None
            if range_header:
                try:
                    unit, span = range_header.split('=')
                    left, right = span.split('-')
                    if unit != 'bytes' or (not left and not right):
                        raise ValueError()
                    if left:
                        start = int(left)
                        end = min(int(right), end) if right else end
                    else:
                        start = max(0, size - int(right))
                    if start < 0 or start > end or start >= size:
                        raise ValueError()
                except ValueError:
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{size}')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
            self.send_response(206 if range_header else 200)
            self.send_header('Content-Type', mimetypes.guess_type(path)[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(max(0, end - start + 1)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            if media:
                self.send_header('Accept-Ranges', 'bytes')
            if range_header:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.end_headers()
            with path.open('rb') as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = stream.read(min(65536, remaining))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        # Seeking/reloading cancels the browser's previous media request.
                        return
                    remaining -= len(chunk)

        def do_POST(self):
            if not self.trusted():
                return
            if self.headers.get('X-Review-Token') != workspace.token:
                self.reply(403, {'error': 'invalid review token'})
                return
            if self.headers.get_content_type() != 'application/json' or self.headers.get('Transfer-Encoding'):
                self.reply(415, {'error': 'application/json body required'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 1024 * 1024:
                    self.reply(413, {'error': 'invalid body size'})
                    return
                self.connection.settimeout(10)
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError('incomplete body')
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError('body must be an object')
                self.reply(200, workspace.mutate(urlsplit(self.path).path, payload))
            except Conflict as error:
                self.reply(409, {'error': str(error)})
            except (ValueError, TypeError, KeyError, UnicodeError) as error:
                self.reply(400, {'error': str(error)})
            except OSError:
                self.reply(500, {'error': 'workspace I/O failed; edit was not confirmed'})

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--master', required=True, type=Path)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--media', type=Path)
    parser.add_argument('--speaker-ids', type=Path)
    parser.add_argument('--port', default=8891, type=int)
    args = parser.parse_args()
    ids = json.loads(args.speaker_ids.read_text(encoding='utf-8')) if args.speaker_ids else None
    workspace = Workspace(args.master, args.workspace, args.media, ids)
    try:
        server = make_server(workspace, args.port)
        print(f'Review: http://127.0.0.1:{server.server_port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    finally:
        workspace.close()


if __name__ == '__main__':
    main()
