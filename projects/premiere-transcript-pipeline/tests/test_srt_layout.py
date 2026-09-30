import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from multilingual_review import Workspace
from srt_layout import (LayoutError, LayoutLimits, clusters, content_key, display_width,
                        reflow_paired_srt, render_bilingual_cue, reviewed_pages)


class SrtLayoutTest(unittest.TestCase):
    def test_wraps_german_words_and_japanese_graphemes_without_loss(self):
        source = 'Das ist ein langer deutscher Satz mit weiteren wichtigen Worten.'
        translation = 'これはとても長い日本語の参考訳で、途中にか\u3099と結合文字が含まれています。'
        cue = render_bilingual_cue(source, translation, LayoutLimits(36, 40))
        lines = cue.splitlines()
        self.assertEqual(lines[2], '\u200b')
        self.assertEqual(len(lines), 5)
        self.assertEqual(content_key(''.join(line[3:-4] for line in lines[:2])), content_key(source))
        self.assertEqual(content_key(''.join(lines[3:])), content_key(translation))
        self.assertFalse(any(line.startswith('\u3099') for line in lines))
        self.assertTrue(all(display_width(line[3:-4]) <= 36 for line in lines[:2]))
        self.assertTrue(all(display_width(line) <= 40 for line in lines[3:]))

    def test_long_cue_requires_reviewed_paired_pages(self):
        source = 'Eins zwei drei vier funf sechs sieben acht neun zehn elf zwolf'
        translation = '一二三四五六七八九十一二三四五六七八九十一二三四五六七八九十'
        limits = LayoutLimits(22, 20)
        with self.assertRaisesRegex(LayoutError, 'reviewed paired'):
            render_bilingual_cue(source, translation, limits)
        pages = [
            {'source':'Eins zwei drei vier funf sechs', 'translation':translation[:15], 'start':1.0, 'end':2.0},
            {'source':'sieben acht neun zehn elf zwolf', 'translation':translation[15:], 'start':2.0, 'end':3.0},
        ]
        cues = reviewed_pages(pages, source, translation, 1.0, 3.0, limits)
        self.assertEqual(len(cues), 2)
        with self.assertRaisesRegex(LayoutError, 'preserve'):
            reviewed_pages([{**pages[0], 'source':'wrong'}, pages[1]], source, translation, 1.0, 3.0, limits)
        with self.assertRaisesRegex(LayoutError, 'overlaps'):
            reviewed_pages([pages[0], {**pages[1], 'start':1.9}], source, translation, 1.0, 3.0, limits)
        japanese = '日本語の長い字幕です。さらに言葉を続けます。'
        jp_pages = [{'source':japanese[:10], 'start':1.0, 'end':2.0},
                    {'source':japanese[10:], 'start':2.0, 'end':3.0}]
        self.assertEqual(len(reviewed_pages(jp_pages, japanese, None, 1.0, 3.0, LayoutLimits(44, 20))), 2)

    def test_reflow_preserves_time_and_text_and_rejects_bad_time(self):
        original = '1\n00:00:00,000 --> 00:00:00,040\n\u200b\n\n2\n00:00:01,000 --> 00:00:03,000\n<i>Das &amp; alles ist gut</i>\n\u200b\n日本語の訳です\n'
        result = reflow_paired_srt(original, LayoutLimits(16, 20))
        self.assertIn('00:00:01,000 --> 00:00:03,000', result)
        self.assertIn('<i>Das &amp; alles ist</i>\n<i>gut</i>', result)
        self.assertIn('\u200b\n日本語の訳です', result)
        self.assertEqual(reflow_paired_srt(result, LayoutLimits(16, 20)), result)
        with self.assertRaisesRegex(LayoutError, 'invalid or overlapping'):
            reflow_paired_srt(original.replace('00:00:03,000', '00:00:00,500'))

    def test_blank_lines_flags_and_latin_words_in_japanese(self):
        self.assertEqual(clusters('🇩🇪'), ['🇩🇪'])
        cue = render_bilingual_cue('あ\n \nい', limits=LayoutLimits(44, 44))
        self.assertEqual(cue, 'あ\nい')
        source = '1\n00:00:01,000 --> 00:00:03,000\n<i>Berlin ist gut</i>\n\u200b\n東京 Berlin\nheute です\n'
        output = reflow_paired_srt(source, LayoutLimits(44, 44))
        self.assertIn('Berlin heute', output)
        self.assertEqual(reflow_paired_srt(output, LayoutLimits(44, 44)), output)

    def test_bom_crlf_premiere_export_roundtrip_and_unknown_style_rejected(self):
        source = '\ufeff1\r\n00:00:00,000 --> 00:00:00,033\r\n\u200b\r\n\r\n2\r\n00:00:05,872 --> 00:00:13,113\r\n<i>Hallo? Japanese TV show.</i>\r\n\u200b\r\n日本のテレビ番組です。\r\n'
        with tempfile.TemporaryDirectory() as folder:
            input_path, output_path = Path(folder)/'input.srt', Path(folder)/'output.srt'
            input_path.write_bytes(source.encode('utf-8'))
            subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1]/'python'/'srt_layout.py'),
                            '--input', str(input_path), '--output', str(output_path)], check=True)
            output = output_path.read_bytes()
            self.assertTrue(output.startswith(b'\xef\xbb\xbf'))
            self.assertEqual(output.count(b'\r\n'), source.count('\r\n'))
            self.assertEqual(output.decode('utf-8'), source)
        with self.assertRaisesRegex(LayoutError, 'unsupported markup'):
            reflow_paired_srt('1\n00:00:01,000 --> 00:00:02,000\n<span style="color:red">text</span>\n')

    def test_export_preflights_layout_before_creating_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            value = {'schema':'bk-master-transcript/v0.1','source_id':'sample','language':'de-de',
                     'speakers':[{'key':'a','name':'A'}], 'utterances':[{'speaker':'a','language':'de-de',
                     'translation':{'text':'日本語訳','status':'ready','provider':'manual'},
                     'words':[{'text':'Sehrlangesuntrennbaresdeutscheswort'*2,'start':1.0,'end':3.0}]}]}
            master = root/'master.json'; master.write_text(json.dumps(value), encoding='utf-8')
            workspace = Workspace(master, root/'work')
            with self.assertRaisesRegex(ValueError, 'SRT配置'):
                workspace.export(False)
            self.assertFalse((root/'work'/'exports').exists())
            draft = workspace.export(True)
            self.assertEqual(draft['srt_layout_status'], 'needs-review')
            self.assertTrue(draft['srt_layout_problems'])
            self.assertTrue(Path(draft['srt_path']).exists())
            workspace.close()

    def test_export_rejects_overlapping_and_rounded_zero_cues(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            value = {'schema':'bk-master-transcript/v0.1','source_id':'sample','language':'ja-jp',
                     'speakers':[{'key':'a','name':'A'}], 'utterances':[
                         {'speaker':'a','language':'ja-jp','words':[{'text':'先','start':1.0,'end':2.0}]},
                         {'speaker':'a','language':'ja-jp','words':[{'text':'後','start':1.9,'end':2.5}]}]}
            master = root/'master.json'; master.write_text(json.dumps(value), encoding='utf-8')
            workspace = Workspace(master, root/'work')
            with self.assertRaisesRegex(ValueError, '重複'):
                workspace.export(False)
            self.assertFalse((root/'work'/'exports').exists())
            workspace.close()


if __name__ == '__main__':
    unittest.main()
