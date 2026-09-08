import copy
import unittest
import uuid
from test_premiere_export import fixture
from premiere_export import convert
from master_edit import edit


class MasterEditTest(unittest.TestCase):
    def test_roundtrip_preserves_annotations_words_and_speaker_ids(self):
        m = fixture()
        m['utterances'][0]['words'] += [{'text': '続き', 'start': 1.75, 'end': 1.9, 'note': 'retain'}]
        m['utterances'][0]['note'] = 'retain'
        original = copy.deepcopy(m)
        ids = {s['key']: str(uuid.uuid4()) for s in m['speakers']}
        divided = edit(m, 'split', 0, 'b', 1)
        self.assertEqual(m, original)
        self.assertEqual(edit(divided, 'merge', 0, 'a'), original)
        a, b = convert(m, ids), convert(divided, ids)
        self.assertEqual(a['speakers'], b['speakers'])
        self.assertEqual(b['segments'][1]['speaker'], ids['b'])
        self.assertEqual([w for s in a['segments'] for w in s['words']],
                         [w for s in b['segments'] for w in s['words']])

    def test_rejects_unsafe_edits_and_mapping(self):
        m = fixture()
        for args in [('assign', -1, 'a'), ('assign', 0, 'missing'), ('split', 0, 'a', 0)]:
            with self.assertRaises(ValueError): edit(m, *args)
        m['utterances'][1]['note'] = 'different'
        with self.assertRaises(ValueError): edit(m, 'merge', 0, 'a')
        for ids in [{}, {'a': 'invalid', 'b': 'invalid'}, {'a': str(uuid.UUID(int=1)), 'b': str(uuid.UUID(int=1))}]:
            with self.assertRaises(ValueError): convert(m, ids)
