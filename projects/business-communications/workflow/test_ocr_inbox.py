import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ocr_inbox import SCHEMA, SNAPSHOT, TEST_ROOT, process
from test_forwarded import at, receipt


class OcrInboxTest(unittest.TestCase):
    def test_wait_for_sync_then_create_one_report_and_cache_ocr(self):
        with tempfile.TemporaryDirectory() as folder:
            working = Path(folder)
            root = working / TEST_ROOT
            root.mkdir()
            out = working / 'reviews'
            tool = working / 'ocr'
            tool.write_text('test executable fingerprint', encoding='utf-8')
            image = receipt('sample', 2, メッセージ種別='image', DriveファイルID='image-id')
            other = receipt('other', 3, 転送受付='未認可')
            parts = ['受信ファイル', '2026-11', 'sample_image']
            snapshot = {'schema': SCHEMA, 'entries': [{'record': image, 'attachment_path': parts}, {'record': other}]}
            (root / SNAPSHOT).write_text(json.dumps(snapshot, ensure_ascii=False), encoding='utf-8')
            image_path = root.joinpath(*parts)
            layout = [{'text': '合成テスト', 'x': .2, 'y': .4, 'width': .08, 'height': .03}]
            with patch('ocr_inbox.recognize_screenshot', return_value=layout) as recognize:
                result = process(root, out, tool, at(130))
                self.assertEqual(result['waiting_for_drive'], 1)
                self.assertEqual(result['review_reports'], 0)
                image_path.parent.mkdir(parents=True)
                image_path.write_bytes(b'synced fixture')
                self.assertEqual(process(root, out, tool, at(131))['review_reports'], 1)
                self.assertEqual(process(root, out, tool, at(132))['review_reports'], 1)
                recognize.assert_called_once_with(image_path.resolve(), tool)
            files = list(out.glob('forward-*.json'))
            self.assertEqual(len(files), 1)
            report = json.loads(files[0].read_text(encoding='utf-8'))
            self.assertEqual(report['source_ids'], ['sample'])
            self.assertEqual(report['preview']['operations'], [])
            self.assertEqual(report['screenshot_evidence'][0]['lines'][0]['text'], '合成テスト')
            with sqlite3.connect(out / 'review.sqlite3') as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM forward_reviews').fetchone(), (1,))

    def test_attachment_cannot_point_outside_drive_test_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            working = Path(folder)
            root = working / TEST_ROOT
            root.mkdir()
            tool = working / 'ocr'
            tool.write_text('test executable fingerprint', encoding='utf-8')
            row = receipt('sample', 2, メッセージ種別='image', DriveファイルID='image-id')
            snapshot = {'schema': SCHEMA, 'entries': [{'record': row, 'attachment_path': ['..', 'other', 'sample_image']}]}
            (root / SNAPSHOT).write_text(json.dumps(snapshot, ensure_ascii=False), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'invalid attachment path'):
                process(root, working / 'reviews', tool, at(130))

    def test_first_start_catches_up_saved_images_from_multiple_days(self):
        with tempfile.TemporaryDirectory() as folder:
            working = Path(folder)
            root = working / TEST_ROOT
            root.mkdir()
            tool = working / 'ocr'
            tool.write_bytes(b'test executable')
            saved = []
            for name, second in [('early', 1), ('late', 172801)]:
                row = receipt(name, second, メッセージ種別='image', DriveファイルID=name + '-file')
                parts = ['受信ファイル', '2026-11', name + '_image']
                path = root.joinpath(*parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(name.encode())
                saved.append({'record': row, 'attachment_path': parts})
            (root / SNAPSHOT).write_text(json.dumps({'schema': SCHEMA, 'entries': saved}), encoding='utf-8')
            out = working / 'reviews'
            with patch('ocr_inbox.recognize_screenshot', return_value=[
                    {'text': '過去の画像', 'x': .2, 'y': .4, 'width': .1, 'height': .03}]) as recognize:
                for now in [at(173000), at(173100)]:
                    status = process(root, out, tool, now)
                    self.assertEqual(status, {'review_reports': 2, 'waiting_for_drive': 0, 'ocr_unavailable': 0})
                self.assertEqual(recognize.call_count, 2)


if __name__ == '__main__':
    unittest.main()
