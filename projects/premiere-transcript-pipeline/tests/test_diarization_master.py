import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from diarization_master import build_master
from premiere_export import convert
from master_edit import edit


class DiarizationMasterTest(unittest.TestCase):
    def make(self, turns, words=None):
        whisper = {'segments': [{'words': words or [
            {'word': '日本語', 'start': 0, 'end': 1},
            {'word': ' English', 'start': 1, 'end': 2},
            {'word': '不明', 'start': 3, 'end': 3}]}]}
        original = copy.deepcopy(whisper)
        master = build_master(whisper, {'segments': turns}, 'synthetic.wav', 'ja-jp')
        self.assertEqual(whisper, original)
        self.assertEqual([(w['text'],w['start'],w['end']) for u in master['utterances'] for w in u['words']],
                         [(w['word'],w['start'],w['end']) for w in whisper['segments'][0]['words']])
        return master

    def test_assignment_unknown_and_export_edit_preserve_words(self):
        master = self.make([{'start':0,'end':1,'speaker':'a'}, {'start':1,'end':2,'speaker':'b'}])
        self.assertEqual([u['speaker'] for u in master['utterances']], ['a','b','unknown'])
        changed = edit(master, 'assign', 0, 'b')
        self.assertEqual(changed['utterances'][0]['words'], master['utterances'][0]['words'])
        self.assertEqual(sum(len(s['words']) for s in convert(changed)['segments']), 3)

    def test_duplicates_do_not_inflate_and_ties_are_unknown(self):
        master = self.make([{'start':0,'end':1,'speaker':s} for s in ['a','a','b']])
        word = master['utterances'][0]['words'][0]
        self.assertEqual(master['utterances'][0]['speaker'], 'unknown')
        self.assertTrue(word['diarization']['overlap'])
        self.assertEqual(word['diarization']['speaker_overlap_seconds'], {'a':1,'b':1})

    def test_empty_diarization_is_explicit_unknown(self):
        self.assertEqual(self.make([])['provenance']['counts']['unknown'], 3)

    def test_invalid_and_missing_words_rejected(self):
        for value in [float('nan'), float('inf'), -1, True]:
            with self.assertRaises(ValueError):
                self.make([], [{'word':'x','start':value,'end':2}])
        with self.assertRaises(ValueError):
            build_master({'segments':[{'text':'missing'}]}, {'segments':[]}, 'x', 'ja-jp')
        with self.assertRaises(ValueError):
            self.make([{'speaker':'unknown','start':0,'end':1}])

if __name__ == '__main__':
    unittest.main()
