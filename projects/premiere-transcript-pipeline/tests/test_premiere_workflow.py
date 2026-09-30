import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from premiere_workflow import BridgeClient, BridgeError, BridgeTimeout, CaptionBridgeClient


class BridgeClientTest(unittest.TestCase):
    def test_roundtrip_and_nonce(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'inbox').mkdir()
            (root / 'outbox').mkdir()
            client = BridgeClient(root, poll_seconds=.005)

            def worker():
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    files = list((root / 'inbox').glob('*.json'))
                    if files:
                        request = json.loads(files[0].read_text())
                        (root / 'outbox' / f"{request['nonce']}.json").write_text(
                            json.dumps({'nonce': request['nonce'], 'ok': True,
                                        'result': {'operation': request['operation']}}))
                        return
                    time.sleep(.005)
            thread = threading.Thread(target=worker)
            thread.start()
            self.assertEqual(client.request('capabilities', timeout=1), {'operation': 'capabilities'})
            thread.join()
            self.assertEqual(list((root / 'inbox').glob('*.json')), [])
            self.assertEqual(list((root / 'outbox').glob('*.json')), [])

    def test_timeout_is_indeterminate_and_recoverable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'inbox').mkdir()
            (root / 'outbox').mkdir()
            client = BridgeClient(root, poll_seconds=.005)
            with self.assertRaises(BridgeTimeout) as caught:
                client.request('snapshot', {'target': {}}, timeout=.02)
            nonce = caught.exception.nonce
            self.assertEqual(list((root / 'inbox').glob('*.json')), [])
            (root / 'outbox' / f'{nonce}.json').write_text(
                json.dumps({'nonce': nonce, 'ok': True, 'result': {'late': True}}))
            self.assertEqual(client.read_response(nonce, consume=True), {'late': True})

    def test_rejects_bad_operation_and_response(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'outbox').mkdir()
            client = BridgeClient(root)
            with self.assertRaises(ValueError):
                client.request('run_js')
            nonce = 'a' * 32
            (root / 'outbox' / f'{nonce}.json').write_text(
                json.dumps({'nonce': 'b' * 32, 'ok': True, 'result': {}}))
            with self.assertRaises(BridgeError):
                client.read_response(nonce)

    def test_caption_brackets_placement_with_track_readback(self):
        with tempfile.TemporaryDirectory() as temp:
            srt = Path(temp) / 'captions.srt'
            srt.write_text('1\n00:00:00,000 --> 00:00:01,000\nHi\n', encoding='utf-8')
            class UXP:
                count = 0
                def request(self, operation, payload):
                    self_outer.assertEqual(operation, 'caption_track_snapshot')
                    return {'projectPath': '/project.prproj', 'sequenceGuid': 'seq',
                            'sequenceName': 'source', 'captionTrackCount': self.count}
            self_outer = self
            uxp = UXP()
            caption = CaptionBridgeClient(temp)
            def place(operation, payload, *, timeout):
                self.assertEqual(operation, 'place_srt')
                self.assertEqual(payload['expectedCaptionTrackCount'], 0)
                self.assertTrue(payload['sourceTimeVerified'])
                uxp.count = 1
                return {'created': True, 'saved': True, 'backupPath': '/backup'}
            caption.request = place
            result = caption.place_srt(uxp, project_path='/project.prproj',
                                       srt_path=srt, source_time_verified=True)
            self.assertEqual(result['state'], 'placement_created_pending_visual')
            self.assertEqual(result['captionTrackCount'], 1)
            with self.assertRaises(BridgeError):
                caption.place_srt(uxp, project_path='/project.prproj',
                                  srt_path=srt, source_time_verified=True)


if __name__ == '__main__':
    unittest.main()
