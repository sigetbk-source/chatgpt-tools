import unittest,sys,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python'))
from phrase_segments import regroup
from diarization_master import build_master
from master_edit import edit

class PhraseTest(unittest.TestCase):
    def fixture(self):
        return build_master({'segments':[{'words':[
            {'word':'メニ','start':0,'end':1}, {'word':'ュ','start':1,'end':1},
            {'word':'ー','start':1,'end':2}]}, {'words':[{'word':'はい','start':3,'end':4}]}]},
            {'segments':[{'speaker':'a','start':0,'end':2},{'speaker':'b','start':3,'end':4}]}, 'test','ja-jp')
    def test_phrase_and_actual_asr_boundary(self):
        m=self.fixture();before=copy.deepcopy(m);r=regroup(m,m)
        self.assertEqual([u['speaker'] for u in r['utterances']],['a','b'])
        self.assertEqual(''.join(w['text'] for w in r['utterances'][0]['words']),'メニュー')
        self.assertEqual(m,before)
        self.assertEqual([w for u in m['utterances'] for w in u['words']], [w for u in r['utterances'] for w in u['words']])
    def test_manual_change_is_protected(self):
        m=self.fixture();e=edit(m,'assign',1,'b');r=regroup(m,e)
        self.assertIn(e['utterances'][1],r['utterances'])
        self.assertEqual(r['provenance']['phrase_comparison']['protected_utterances'],1)
    def test_manual_merge_preserved(self):
        m=self.fixture();e=edit(m,'merge',0,'a');r=regroup(m,e)
        self.assertEqual(r['utterances'][0],e['utterances'][0])
    def test_changed_text_rejected(self):
        m=self.fixture();e=copy.deepcopy(m);e['utterances'][0]['words'][0]['text']='別'
        with self.assertRaises(ValueError):regroup(m,e)
