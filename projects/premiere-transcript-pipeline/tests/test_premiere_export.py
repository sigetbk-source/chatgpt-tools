import copy
import sys
from pathlib import Path
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from premiere_export import convert


def fixture():
    return {'schema': 'bk-master-transcript/v0.1', 'source_id': 'synthetic-test.wav',
            'language': 'ja-jp', 'speakers': [{'key': 'a', 'name': '話者A'}, {'key': 'b', 'name': '話者B'}],
            'utterances': [{'speaker': 'a', 'words': [{'text': '検証', 'start': 1.25, 'end': 1.75}]},
                           {'speaker': 'b', 'words': [{'text': '応答', 'start': 2.0, 'end': 2.5, 'eos': True}]}]}


class ExportTest(unittest.TestCase):
    def test_independent_speakers_absolute_timing_and_no_mutation(self):
        master = fixture()
        original = copy.deepcopy(master)
        result = convert(master)
        self.assertEqual(master, original)
        self.assertEqual(result['segments'][0]['words'][0]['start'], 1.25)
        self.assertEqual(result['segments'][0]['words'][0]['duration'], .5)
        self.assertEqual(result['segments'][1]['speaker'], result['speakers'][1]['id'])
        self.assertTrue(all(uuid.UUID(s['id']).version == 4 for s in result['speakers']))
        self.assertNotEqual(result['speakers'][0]['id'], convert(master)['speakers'][0]['id'])

    def test_rejects_bad_timing(self):
        for value in [float('nan'), float('inf'), -1, True, '1']:
            with self.subTest(value=value):
                master = fixture()
                master['utterances'][0]['words'][0]['start'] = value
                with self.assertRaises(ValueError):
                    convert(master)
        master = fixture()
        master['utterances'].reverse()
        with self.assertRaises(ValueError):
            convert(master)

    def test_rejects_dangling_or_duplicate_speakers(self):
        master = fixture()
        master['utterances'][0]['speaker'] = 'missing'
        with self.assertRaises(ValueError):
            convert(master)
        master = fixture()
        master['speakers'][1]['key'] = 'a'
        with self.assertRaises(ValueError):
            convert(master)

    def test_internal_annotations_remain_only_in_master(self):
        master = fixture()
        master['utterances'][0]['words'][0]['tags'] = ['review-needed', 'filler']
        self.assertEqual(convert(master)['segments'][0]['words'][0]['tags'], ['filler'])
        self.assertEqual(master['utterances'][0]['words'][0]['tags'], ['review-needed', 'filler'])


if __name__ == '__main__':
    unittest.main()
