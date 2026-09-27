import copy
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from multi_iso import (DEFAULTS, analyze, build_master, duplicate_groups, frame_levels,
                       explicit_reference_candidates, contextual_reference_candidates,
                       preserve_manual_speaker_edits, score_utterance, volume_evidence)
from multilingual_review import Workspace, display_text


def audio(levels=(0.8, 0.1, 0.03), seconds=3, sr=16000):
    t = np.arange(seconds * sr) / sr
    return {key: (amplitude * np.sin(2 * math.pi * frequency * t)).astype('float32')
            for key, amplitude, frequency in zip(('host', 'guest_en', 'guest_ja'), levels, (210, 120, 135))}


def row(source='host', text='Hello', start=0.2, end=1.6):
    return {'id': source + '-1', 'source_iso_speaker': source, 'source_iso': source + '.wav',
            'start': start, 'end': end, 'text': text, 'raw_asr_text': text,
            'words': [{'text': text, 'start': start, 'end': end, 'confidence': .9, 'eos': True}],
            'aai_speaker': 'A', 'aai_confidence': .9, 'detected_language': 'en', 'language': 'en-us'}


class MultiISOTests(unittest.TestCase):
    def test_loudest_iso_and_strong_threshold(self):
        signals = audio()
        levels = {key: frame_levels(value, 16000) for key, value in signals.items()}
        evidence = volume_evidence(levels, .2, 1.5)
        self.assertEqual(evidence['primary_iso'], 'host')
        self.assertEqual(evidence['strength'], 'strong')
        self.assertGreaterEqual(evidence['margin_db'], 8)
        self.assertIn('rms', evidence['per_iso']['host'])
        self.assertIn('peak', evidence['per_iso']['host'])

    def test_below_three_db_is_ambiguous_and_two_loud_iso_overlap(self):
        signals = audio((.8, .7, .01))
        levels = {key: frame_levels(value, 16000) for key, value in signals.items()}
        evidence = volume_evidence(levels, .2, 1.5)
        self.assertEqual(evidence['strength'], 'ambiguous')
        self.assertTrue(evidence['overlap_candidate'])

    def test_source_iso_is_retained_with_aai_volume_conflict(self):
        signals = audio((.06, .8, .03))
        levels = {key: frame_levels(value, 16000) for key, value in signals.items()}
        scored = score_utterance(row(), list(signals), levels, signals, 16000, {}, DEFAULTS)
        self.assertEqual(scored['source_iso_speaker'], 'host')
        self.assertEqual(scored['speaker'], 'guest_en')
        self.assertTrue(scored['speaker_evidence']['aai_iso_conflict'])
        self.assertEqual(scored['speaker_evidence']['aai_speaker'], 'A')
        self.assertFalse(scored['speaker_evidence']['pyannote_used'])

    def test_pyannote_hook_is_opt_in_and_ambiguous_only(self):
        signals = audio((.8, .05, .01))
        levels = {key: frame_levels(value, 16000) for key, value in signals.items()}
        calls = []
        fallback = lambda utterance, volume: calls.append(utterance['id']) or {'speaker': 'host'}
        score_utterance(row(), list(signals), levels, signals, 16000, {}, DEFAULTS, fallback)
        self.assertEqual(calls, [])
        signals = audio((.8, .7, .01))
        levels = {key: frame_levels(value, 16000) for key, value in signals.items()}
        result = score_utterance(row(), list(signals), levels, signals, 16000, {}, DEFAULTS, fallback)
        self.assertEqual(calls, ['host-1'])
        self.assertTrue(result['speaker_evidence']['pyannote_used'])

    def test_duplicate_candidate_retains_raw_and_primary(self):
        first = row('host', 'Amazon Ads', 0, 2)
        second = row('guest_ja', 'Amazon Ads', .1, 2.1)
        for x in (first, second):
            x['speaker'] = 'host'; x['speaker_scores'] = {'host': .8}; x['speaker_evidence'] = {
                'volume': {'per_iso': {'host': {'dbfs': -10}, 'guest_ja': {'dbfs': -25}},
                           'margin_db': 15, 'strength': 'strong'}}
        grouped = duplicate_groups([first, second])
        self.assertEqual(sum(x['duplicate_primary'] for x in grouped), 1)
        self.assertEqual(sum(len(x['duplicate_candidates']) for x in grouped), 1)
        self.assertEqual(len(grouped), 2)

    def test_same_words_with_simultaneous_voices_are_kept(self):
        first = row('host', 'はい', 0, 1)
        second = row('guest_ja', 'はい', 0, 1)
        for key, item in zip(('host', 'guest_ja'), (first, second)):
            item.update({'speaker': key, 'speaker_scores': {key: .8}, 'overlap': True,
                         'speaker_evidence': {'volume': {'margin_db': 1, 'strength': 'ambiguous',
                             'per_iso': {key: {'dbfs': -12}}}}})
        grouped = duplicate_groups([first, second])
        self.assertTrue(all(x['duplicate_primary'] for x in grouped))

    def test_master_keeps_iso_and_raw_text_and_timing_under_reference(self):
        item = row()
        item.update({'speaker': 'host', 'speaker_confidence': .8, 'speaker_scores': {'host': .8},
                     'speaker_evidence': {'volume': {'margin_db': 9}, 'pyannote_used': False},
                     'ambiguous': False, 'overlap': False, 'duplicate_candidates': [],
                     'duplicate_primary': True})
        manifest = {'start_tc': '13:32:51', 'duration_seconds': 120,
                    'sources': [{'speaker_id': key, 'speaker_name': name, 'source_iso': key + '.wav'}
                                for key, name in [('host', '田中'), ('guest_en', 'ステファン'), ('guest_ja', '岡田')]]}
        with tempfile.TemporaryDirectory() as tmp:
            reference = Path(tmp) / 'terms.txt'
            reference.write_text('Amazon Ad → Amazon Ads\n')
            item['raw_asr_text'] = item['text'] = 'Amazon Ad'
            item['words'][0]['text'] = 'Amazon Ad'
            master, docs = build_master([item], manifest, [reference])
        utterance = master['utterances'][0]
        self.assertEqual(utterance['source_iso_speaker'], 'host')
        self.assertEqual(utterance['raw_asr_text'], 'Amazon Ad')
        self.assertEqual(utterance['words'][0]['start'], .2)
        self.assertEqual(utterance['reference_corrections'][0]['candidate'], 'Amazon Ads')
        self.assertEqual(utterance['reference_corrections'][0]['before'], display_text(utterance))
        self.assertFalse(utterance['reference_corrections'][0]['adopted'])
        self.assertEqual(len(docs), 1)

    def test_manual_speaker_assignment_survives_reprocessing(self):
        old = {'utterances': [{'speaker': 'guest_en', 'speaker_label_source': 'manual',
                              'source_iso_speaker': 'host', 'review_envelope': {'start': 1, 'end': 3}}]}
        incoming = {'utterances': [{'speaker': 'host', 'source_iso_speaker': 'host',
                                    'review_envelope': {'start': 1.1, 'end': 2.9},
                                    'speaker_evidence': {}}]}
        preserve_manual_speaker_edits(old, incoming)
        self.assertEqual(incoming['utterances'][0]['speaker'], 'guest_en')
        self.assertEqual(incoming['utterances'][0]['speaker_label_source'], 'manual')

    def test_general_reference_inference_is_traceable_and_not_applied(self):
        docs = [{'name': 'terms.md', 'path': '/tmp/terms.md', 'text': 'marketer\n',
                 'terms': [{'from': None, 'to': 'marketer', 'line': 1, 'evidence': 'marketer'}]}]
        proposed = contextual_reference_candidates('a market er', docs, [])
        self.assertEqual(proposed, [])  # Space repair needs an explicit contextual alias.
        docs[0]['text'] = 'market er → marketer\n'
        explicit = explicit_reference_candidates('a market er', docs)
        self.assertEqual(explicit[0]['candidate'], 'a marketer')
        self.assertFalse(explicit[0]['adopted'])

    def test_existing_review_manual_edit_is_saved(self):
        master = {'schema': 'bk-master-transcript/v0.1', 'source_id': 'test', 'language': 'en-us',
                  'provenance': {'multi_iso': True, 'interval': {'start_tc': '13:32:51', 'duration_seconds': 120}},
                  'speakers': [{'key': 'host', 'name': '田中'}, {'key': 'guest_en', 'name': 'ステファン'}],
                  'utterances': [{'speaker': 'host', 'language': 'en-us', 'words': [
                      {'text': 'Hi', 'start': 0.1, 'end': .5, 'confidence': .9, 'eos': True}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'master.json'; source.write_text(json.dumps(master))
            work = Workspace(source, Path(tmp) / 'review')
            work.mutate('/api/speaker', {'revision': 0, 'segment_index': 0, 'speaker_name': 'ステファン'})
            with patch('workflow_backend._diarization_ready', side_effect=AssertionError('pyannote readiness called')):
                self.assertEqual(work.project()['items'][0]['speaker_name'], 'ステファン')
            self.assertEqual(work.state['master']['utterances'][0]['speaker_label_source'], 'manual')
            work.close()
            reopened = Workspace(source, Path(tmp) / 'review')
            self.assertEqual(reopened.project()['items'][0]['speaker_name'], 'ステファン')
            reopened.close()

    def test_reference_adoption_retains_raw_asr_and_utterance_times(self):
        master = {'schema': 'bk-master-transcript/v0.1', 'source_id': 'test', 'language': 'en-us',
                  'provenance': {'multi_iso': True, 'interval': {'start_tc': '13:32:51', 'duration_seconds': 120}},
                  'speakers': [{'key': 'host', 'name': '田中'}],
                  'utterances': [{'speaker': 'host', 'language': 'en-us', 'raw_asr_text': 'market er',
                      'review_envelope': {'start': 4.2, 'end': 5.9},
                      'words': [{'text': 'market er', 'start': 4.2, 'end': 5.9, 'confidence': .9, 'eos': True}],
                      'reference_corrections': [{'before': 'market er', 'candidate': 'marketer',
                          'source_name': 'dictionary.md', 'evidence': 'marketer', 'matched_term': 'market er',
                          'adopted': False, 'status': 'candidate'}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'master.json'; source.write_text(json.dumps(master))
            work = Workspace(source, Path(tmp) / 'review')
            work.mutate('/api/references/iso-adopt', {'revision': 0, 'segment_index': 0,
                        'correction_index': 0, 'current_text': 'market er'})
            changed = work.state['master']['utterances'][0]
            self.assertEqual(changed['raw_asr_text'], 'market er')
            self.assertEqual(changed['review_envelope'], {'start': 4.2, 'end': 5.9})
            self.assertEqual(changed['words'][0]['start'], 4.2)
            self.assertEqual(changed['text_override'], 'marketer')
            self.assertEqual(changed['alignment_status'], 'unresolved')
            self.assertTrue(changed['reference_corrections'][0]['adopted'])
            work.close()

    def test_second_reference_candidate_uses_current_corrected_text(self):
        master = {'schema': 'bk-master-transcript/v0.1', 'source_id': 'test', 'language': 'en-us',
                  'provenance': {'multi_iso': True, 'interval': {'start_tc': '13:32:51', 'duration_seconds': 120}},
                  'speakers': [{'key': 'host', 'name': '田中'}],
                  'utterances': [{'speaker': 'host', 'language': 'en-us',
                      'raw_asr_text': 'And Questions Amazon Ad',
                      'review_envelope': {'start': 4.2, 'end': 5.9},
                      'words': [{'text': 'And Questions Amazon Ad', 'start': 4.2, 'end': 5.9,
                                 'confidence': .9, 'eos': True}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'master.json'; source.write_text(json.dumps(master))
            terms = root / 'terms.md'; terms.write_text('AndQuestions → &questions\nAmazon Ad → Amazon Ads\n')
            work = Workspace(source, root / 'review')
            work.mutate('/api/references/load', {'revision': 0, 'paths': [str(terms)]})
            work.mutate('/api/references/suggest', {'revision': 1})
            first = next(x for x in work.state['references']['suggestions'] if '&questions' in x['candidate_text'])
            work.mutate('/api/references/adopt', {'revision': 2, 'segment_index': 0,
                        'suggestion_id': first['id'], 'current_text': 'And Questions Amazon Ad'})
            work.mutate('/api/references/suggest', {'revision': 3})
            second = next(x for x in work.state['references']['suggestions'] if 'Amazon Ads' in x['candidate_text'])
            self.assertEqual(second['base_text'], '&questions Amazon Ad')
            work.mutate('/api/references/adopt', {'revision': 4, 'segment_index': 0,
                        'suggestion_id': second['id'], 'current_text': second['base_text']})
            changed = work.state['master']['utterances'][0]
            self.assertEqual(changed['text_override'], '&questions Amazon Ads')
            self.assertEqual(changed['review_envelope'], {'start': 4.2, 'end': 5.9})
            self.assertEqual(changed['raw_asr_text'], 'And Questions Amazon Ad')
            self.assertEqual(sum(x['adopted'] for x in changed['reference_corrections']), 2)
            work.close()


if __name__ == '__main__':
    unittest.main()
