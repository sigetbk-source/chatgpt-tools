import datetime as dt
import sqlite3
import tempfile
import unittest
from pathlib import Path

from forwarded import record_reviews, review_forwards
from forward_ocr import conversation_messages, file_fingerprints, screenshot_lines


BASE = dt.datetime(2026, 11, 27, 12, tzinfo=dt.timezone.utc)


def at(second):
    return BASE + dt.timedelta(seconds=second)


def receipt(identifier, second, text='', **overrides):
    return {'受信ID': identifier, '経路': 'LINE', '環境': 'test', '受信経路詳細': '専用グループ転送',
            'LINE送信元種別': 'group', 'LINEグループID': 'C' + 'a' * 32, '転送受付': '認可済み',
            '送信者ID': 'U' + 'b' * 32, 'イベント種別': 'message', 'メッセージ種別': 'text', '本文': text,
            '受信日時': at(second).isoformat(), 'LINE発生日時': at(second).isoformat(),
            '原文ファイルID': 'archive-' + identifier, '保存状態': '保存済み', '元ファイル名': '',
            'DriveファイルID': '', **overrides}


class ForwardingTest(unittest.TestCase):
    def test_front_and_back_declarations_without_end(self):
        for rows in ([receipt('a', 0, '佐藤さんから'), receipt('b', 4, '日程変更をお願いします')],
                     [receipt('b', 0, '日程変更をお願いします'), receipt('a', 4, '転送元：佐藤さん')]):
            pending, = review_forwards(rows, at(123))
            settled, = review_forwards(rows, at(124))
            self.assertEqual(pending['state'], 'collecting')
            self.assertEqual(settled['state'], 'review')
            statement, = settled['statements']
            self.assertEqual(statement['original_sender'], '佐藤さん')
            self.assertEqual(statement['sender_evidence_ids'], ['a'])
            self.assertIsNone(statement['original_at'])
            self.assertIsNone(statement['original_contact_id'])
            self.assertEqual(settled['preview']['operations'], [])

    def test_boundary_candidates_and_unrelated_group_members(self):
        rows = [receipt('a', 0, '佐藤さんから'), receipt('b', 2, '仮押さえ'),
                receipt('other', 3, '転送終了', 転送受付='未認可'),
                receipt('general', 4, '転送元：田中さん', 受信経路詳細='一般グループ'),
                receipt('c', 5, '鈴木さんから'), receipt('d', 6, '「以上です」という引用'),
                receipt('e', 7, '続報'), receipt('f', 8, '転送終了')]
        first, second = review_forwards(rows + [rows[0]], at(128))
        self.assertEqual(first['source_ids'], ['a', 'b'])
        self.assertEqual(second['source_ids'], ['c', 'd', 'e', 'f'])
        self.assertEqual([s['receipt_id'] for s in second['statements']], ['d', 'e'])
        self.assertTrue(all(s['original_sender'] is None for s in second['statements']))
        with self.assertRaises(ValueError):
            review_forwards(rows + [receipt('b', 3, 'conflict')], at(128))

    def test_attachment_wait_failure_and_image_role(self):
        image = receipt('image', 4, メッセージ種別='image', DriveファイルID='image-id')
        first = receipt('one', 6, メッセージ種別='file', DriveファイルID='one-id', 元ファイル名='台本.pdf')
        pending = receipt('two', 8, メッセージ種別='file', 保存状態='添付取得待ち', 元ファイル名='台本.pdf')
        end = receipt('end', 10, '以上です')
        waiting, = review_forwards([image, first, pending, end], at(130))
        self.assertEqual(waiting['state'], 'collecting')
        stale, = review_forwards([image, first, pending, end], at(1805))
        self.assertIn('long_open_unit', stale['uncertainties'])
        failed, = review_forwards([image, first, {**pending, '保存状態': '保存失敗'}, end], at(130))
        self.assertEqual(failed['state'], 'review')
        self.assertIsNone(failed['decision'])
        self.assertIsNone(failed['preview'])
        saved, = review_forwards([image, first, {**pending, '保存状態': '保存済み', 'DriveファイルID': 'two-id'}, end], at(130))
        self.assertEqual([a['file_id'] for a in saved['attachments']], ['image-id', 'one-id', 'two-id'])
        self.assertEqual(saved['attachments'][0]['role'], 'unconfirmed')
        image_only, = review_forwards([image], at(130))
        self.assertEqual(image_only['decision']['classification'], 'unknown')

    def test_retransmission_and_late_correction_do_not_replace_record(self):
        text = receipt('first', 0, '仮押さえ')
        again = receipt('again', 150, '仮押さえ')
        first, repeated = review_forwards([text, again], at(280))
        self.assertEqual(repeated['possible_retransmission_of'], first['unit_id'])
        self.assertNotEqual(repeated['unit_id'], first['unit_id'])
        late = receipt('late', 151, '鈴木さんから', LINE発生日時=at(2).isoformat())
        corrected, = review_forwards([text, late], at(300))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'reviews.db'
            record_reviews(path, [first])
            record_reviews(path, [corrected])
            record_reviews(path, [corrected])
            self.assertEqual(corrected['correction_candidates_for'], [first['unit_id']])
            self.assertIsNone(corrected['preview'])
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM forward_reviews').fetchone()[0], 2)
                self.assertEqual(db.execute('SELECT unit_id FROM forward_sources').fetchone()[0], first['unit_id'])

    def test_ocr_layout_and_same_named_files_are_only_candidates(self):
        rows = [receipt('image', 1, メッセージ種別='image', DriveファイルID='image-id'),
                receipt('one', 2, メッセージ種別='file', DriveファイルID='one-id', 元ファイル名='台本.pdf'),
                receipt('two', 3, メッセージ種別='file', DriveファイルID='two-id', 元ファイル名='台本.pdf')]
        positions = [dict(text='14:30', x=0.81, y=0.5, width=0.1, height=0.05),
                     dict(text='11月27日', x=0.4, y=0.2, width=0.2, height=0.05),
                     dict(text='台本.pdf', x=0.15, y=0.4, width=0.2, height=0.05)]
        with tempfile.TemporaryDirectory() as folder:
            copy = Path(folder) / 'copy.pdf'
            copy.write_bytes(b'same file fixture')
            fingerprints = file_fingerprints({'one-id': copy, 'two-id': copy})
        report, = review_forwards(rows, at(124), layouts={'image-id': positions}, fingerprints=fingerprints)
        image, = report['screenshot_evidence']
        day, file, time = image['lines']
        self.assertIsNone(day['visible_date'])
        self.assertEqual(day['time_precision'], 'month_and_day')
        self.assertEqual(file['candidate_receipt_ids'], ['one', 'two'])
        self.assertEqual(time['side'], 'right')
        self.assertEqual(time['speaker_role'], 'unknown')
        self.assertTrue(all(s['original_at'] is None for s in report['statements']))
        self.assertEqual(report['attachments'][2]['possible_same_file_as'], 'one')
        self.assertEqual(report['preview']['operations'], [])

    def test_attachment_display_size_distinguishes_local_candidates_without_confirming(self):
        rows = [receipt('image', 1, メッセージ種別='image', DriveファイルID='image-id'),
                receipt('one', 2, メッセージ種別='file', DriveファイルID='one-id', 元ファイル名='検証.pdf'),
                receipt('two', 3, メッセージ種別='file', DriveファイルID='two-id', 元ファイル名='検証.pdf'),
                receipt('three', 4, メッセージ種別='file', DriveファイルID='three-id', 元ファイル名='検証.pdf')]
        layout = {'region': [.1, .1, .8, .8], 'lines': [
            dict(text='検証.pdf', x=.15, y=.4, width=.16, height=.025),
            dict(text='2.0 KB', x=.15, y=.43, width=.1, height=.02)]}
        fingerprints = {'one-id': {'sha256': 'one', 'bytes': 2048},
                        'two-id': {'sha256': 'two', 'bytes': 8024}}
        report, = review_forwards(rows, at(130), layouts={'image-id': layout}, fingerprints=fingerprints)
        image, = report['screenshot_evidence']
        name, size = image['lines']
        self.assertEqual(name['candidate_receipt_ids'], ['one', 'two', 'three'])
        self.assertEqual([c['status'] for c in name['size_comparisons']], ['consistent', 'different', 'unknown'])
        self.assertEqual(size['kind'], 'attachment_size')
        self.assertEqual([m['text'] for m in image['messages']], ['検証.pdf'])
        self.assertTrue(all(a['matching_status'] == 'unconfirmed' for a in report['attachments']))
        self.assertEqual(report['preview']['operations'], [])

    def test_line_desktop_layout_ignores_sidebar_and_pairs_only_visible_times(self):
        def line(text, x, y, width):
            return dict(text=text, x=x, y=y, width=width, height=.023)

        layout = [line('11月27日（金）23:58', .875, .009, .113),
                  line('トークルームとメッセージ検索', .07, .14, .18),
                  line('別の会話', .12, .43, .08), line('午後 4:22', .25, .22, .05),
                  line('メッセージを入力', .33, .854, .09), line('今日', .64, .26, .02),
                  line('制作日程の確認', .379, .185, .26), line('午後1:37', .655, .199, .045),
                  line('作業メモです', .79, .317, .172), line('次回に続きます', .79, .342, .113),
                  line('既読', .70, .548, .022), line('午前 10:08', .677, .570, .050),
                  line('2件分で処理します', .741, .557, .225),
                  line('ご連絡ありがとうございます。', .379, .704, .493),
                  line('午前 11:12', .889, .717, .05)]
        observations = screenshot_lines(layout)
        messages = conversation_messages(observations)
        self.assertEqual(len(messages), 4)
        self.assertEqual([m['side'] for m in messages], ['left', 'right', 'right', 'left'])
        self.assertEqual([m['visible_time'] for m in messages], ['13:37', None, '10:08', '11:12'])
        self.assertEqual(messages[1]['text'], '作業メモです\n次回に続きます')
        self.assertEqual(messages[1]['date_label'], '今日')
        self.assertIsNone(messages[1]['visible_date'])
        self.assertTrue(all(m['speaker_role'] == 'unknown' for m in messages))
        self.assertNotIn('別の会話', [l['text'] for l in observations])
        self.assertNotIn('11月27日（金）23:58', [l['text'] for l in observations])
        dated = conversation_messages(screenshot_lines({'lines': layout, 'captured_at': '2026-11-27T23:58:00+09:00'}))
        self.assertEqual(dated[1]['visible_date'], '2026-11-27')

    def test_ambiguous_clocks_are_not_assigned(self):
        lines = [{'text': text, 'x': x, 'y': y, 'width': width, 'height': .025}
                 for text, x, y, width in [('今日', .5, .21, .03), ('依頼内容', .22, .4, .13),
                                           ('午前12:03', .37, .401, .04), ('午後12:04', .37, .414, .04)]]
        ocr = screenshot_lines({'region': [.2, .2, .7, .65], 'lines': lines})
        self.assertEqual([l['visible_time'] for l in ocr if l['kind'] == 'time_marker'], ['00:03', '12:04'])
        message, = conversation_messages(ocr)
        self.assertIsNone(message['visible_time'])


if __name__ == '__main__':
    unittest.main()
