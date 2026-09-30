import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from master_review import Workspace, make_server


def fixture():
    return {'schema': 'bk-master-transcript/v0.1', 'source_id': 'test', 'language': 'ja-jp',
            'speakers': [{'key': 'a', 'name': 'A'}, {'key': 'b', 'name': 'B'}],
            'annotations': {'keep': True}, 'utterances': [
                {'speaker': 'a', 'words': [{'text': '😀', 'start': 0, 'end': 1, 'custom': 'kept'},
                                          {'text': '日', 'start': 1, 'end': 2},
                                          {'text': '本', 'start': 2, 'end': 3}]},
                {'speaker': 'b', 'words': [{'text': 'English', 'start': 4, 'end': 5}]}]}


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.master = self.root / 'input.json'
        self.master.write_text(json.dumps(fixture()), encoding='utf-8')
        self.original = self.master.read_bytes()
        self.media = self.root / 'test.wav'
        self.media.write_bytes(b'0123456789')
        self.workspace = Workspace(self.master, self.root / 'workspace', self.media)
        self.server = make_server(self.workspace, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.workspace.close()
        self.temp.cleanup()

    def request(self, route, payload=None, headers=None, raw=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        defaults = {}
        method = 'GET' if payload is None and raw is None else 'POST'
        body = raw if raw is not None else json.dumps(payload) if payload is not None else None
        if method == 'POST':
            defaults = {'Content-Type': 'application/json', 'X-Review-Token': self.workspace.token}
        defaults.update(headers or {})
        connection.request(method, route, body, defaults)
        response = connection.getresponse()
        data = response.read()
        status = response.status
        connection.close()
        return status, data

    def post(self, route, **data):
        data.setdefault('revision', self.workspace.state['revision'])
        status, body = self.request(route, data)
        return status, json.loads(body)

    def test_master_roundtrip_reopen_and_stable_exports(self):
        project = json.loads(self.request('/api/project')[1])
        self.assertEqual(project['candidates']['items'][0]['word_boundaries'], [2, 3])
        self.assertEqual(self.post('/api/segments/split', segment_index=0, caret=1, current_text='😀日本')[0], 400)
        self.assertEqual(self.post('/api/segments/split', segment_index=0, caret=2, current_text='😀日本')[0], 200)
        self.assertEqual(self.post('/api/speaker', segment_index=1, speaker_name='B')[0], 200)
        self.assertEqual(self.workspace.state['master']['utterances'][1]['speaker'], 'b')
        self.assertEqual(self.post('/api/undo')[0], 200)
        self.assertEqual(self.post('/api/redo')[0], 200)
        self.assertEqual(self.post('/api/segments/merge-next', segment_index=0, current_text='😀', next_text='日本')[0], 200)
        self.assertEqual(self.workspace.state['master'], fixture())
        first, second = self.post('/api/export')[1], self.post('/api/export')[1]
        self.assertNotEqual(first['json'], second['json'])
        self.assertEqual(Path(first['json']).read_bytes(), Path(second['json']).read_bytes())
        self.assertEqual(json.loads(Path(first['master_path']).read_text()), fixture())
        ids = self.workspace.state['speaker_ids'].copy()
        revision = self.workspace.state['revision']
        with self.assertRaises(ValueError):
            Workspace(self.master, self.root / 'workspace')
        self.workspace.close()
        self.workspace = Workspace(self.master, self.root / 'workspace')
        self.assertEqual(self.workspace.state['speaker_ids'], ids)
        self.assertEqual(self.workspace.state['revision'], revision)
        self.assertEqual(self.workspace.media, self.media.resolve())
        self.workspace.close()
        other_media = self.root / 'other.wav'
        other_media.write_bytes(b'other')
        with self.assertRaises(ValueError):
            Workspace(self.master, self.root / 'workspace', other_media)
        self.workspace = Workspace(self.master, self.root / 'workspace')
        self.assertEqual(self.master.read_bytes(), self.original)
        self.workspace.close()
        self.master.write_text(json.dumps(dict(fixture(), source_id='different')))
        with self.assertRaises(ValueError):
            Workspace(self.master, self.root / 'workspace')

    def test_security_and_invalid_mutations(self):
        good = {'segment_index': 0, 'speaker_name': 'B', 'revision': 0}
        self.assertEqual(self.request('/api/project', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('/api/speaker', good, {'Origin': 'http://evil.example'})[0], 403)
        self.assertEqual(self.request('/api/speaker', good, {'X-Review-Token': ''})[0], 403)
        self.assertEqual(self.request('/api/speaker', good, {'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request('/api/speaker', raw='{')[0], 400)
        self.assertEqual(self.request('/api/speaker', raw='[]')[0], 400)
        self.assertEqual(self.request('/api/speaker', good, {'Content-Length': str(1024 * 1024 + 1)})[0], 413)
        self.assertEqual(self.post('/api/speaker', segment_index=0, speaker_name='B', scope='all')[0], 400)
        self.assertEqual(self.post('/api/speaker', segment_index=0, speaker_name='unknown')[0], 400)
        self.assertEqual(self.post('/api/speaker', segment_index=0, speaker_name='B')[0], 200)
        self.assertEqual(self.request('/api/speaker', good)[0], 409)
        self.assertEqual(self.post('/api/segments/split', segment_index=0, caret=2, current_text='wrong')[0], 409)
        self.assertEqual(self.post('/api/decision')[0], 400)
        self.assertEqual(self.workspace.state['revision'], 1)
        self.assertEqual(self.request('/../../input.json')[0], 404)

    def test_concurrent_revision_and_failed_persist(self):
        payload = {'segment_index': 0, 'speaker_name': 'B', 'revision': 0}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.request('/api/speaker', payload)[0], range(2)))
        self.assertEqual(sorted(results), [200, 409])
        before = self.workspace.path.read_bytes()
        with patch('master_review.os.replace', side_effect=OSError('injected disk failure')):
            self.assertEqual(self.post('/api/segments/undo')[0], 500)
        self.assertEqual(self.workspace.state['revision'], 1)
        self.assertEqual(self.workspace.path.read_bytes(), before)
        self.assertEqual(self.post('/api/segments/undo')[0], 200)
        self.assertEqual(self.post('/api/segments/redo')[0], 200)

    def test_cancelled_media_stream_only_suppresses_disconnects(self):
        handler = object.__new__(self.server.RequestHandlerClass)
        handler.path = '/media'
        handler.headers = {}
        handler.trusted = lambda: True
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.wfile = Mock()
        for error in (BrokenPipeError(), ConnectionResetError()):
            handler.wfile.write.side_effect = error
            handler.do_GET()
        handler.wfile.write.side_effect = OSError('unrelated I/O failure')
        with self.assertRaisesRegex(OSError, 'unrelated'):
            handler.do_GET()

    def test_media_ranges(self):
        self.assertEqual(self.request('/media', headers={'Range': 'bytes=2-4'}), (206, b'234'))
        self.assertEqual(self.request('/audio.wav', headers={'Range': 'bytes=-2'}), (206, b'89'))
        self.assertEqual(self.request('/media', headers={'Range': 'bytes=20-30'})[0], 416)
        self.assertEqual(self.request('/media', headers={'Range': 'bytes=0-1,3-4'})[0], 416)


if __name__ == '__main__':
    unittest.main()
