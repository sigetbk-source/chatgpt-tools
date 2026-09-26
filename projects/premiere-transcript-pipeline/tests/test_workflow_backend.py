import json
import io
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from multilingual_review import Workspace, Conflict
from workflow_backend import _timed_master, transcribe_assemblyai, transcribe_local


class WorkflowBackendTest(unittest.TestCase):
    def test_new_workspace_transcribes_async_and_resumes_without_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            media = root / 'input.wav'; media.write_bytes(b'original-media')
            work = Workspace(None, root / 'work')
            self.assertEqual(work.project()['workflow']['stage'], 'setup')
            configured = work.mutate('/api/workflow/configure', {'revision': 0, 'mode': 'multilingual',
                'engine': 'local-whisper', 'media_path': str(media), 'language': 'en-us'})
            self.assertEqual(configured['workflow']['media_path'], str(media.resolve()))
            master = _timed_master([{'text': 'Hello', 'start': 0, 'end': .5}], media, 'en-us', 'test')
            with patch('multilingual_review.engine_availability', return_value={'local-whisper': {'available': True, 'reason': ''}}), \
                 patch('multilingual_review.transcribe_local', return_value=master):
                work.mutate('/api/workflow/start', {'revision': 1})
                for _ in range(100):
                    if work.project()['workflow']['stage'] != 'transcribing': break
                    time.sleep(.01)
            self.assertEqual(work.project()['workflow']['stage'], 'review')
            self.assertEqual(work.project()['items'][0]['text'], 'Hello')
            self.assertEqual(media.read_bytes(), b'original-media')
            with self.assertRaisesRegex(ValueError, '再設定できません'):
                work.mutate('/api/workflow/configure', {'revision': 3, 'mode': 'multilingual',
                    'engine': 'local-whisper', 'media_path': str(media)})
            work.close()
            resumed = Workspace(None, root / 'work')
            self.assertEqual(resumed.project()['items'][0]['text'], 'Hello')
            resumed.close()

    def test_reference_adoption_requires_current_text_and_preserves_timing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            master = root / 'input.json'
            master.write_text(json.dumps({'schema': 'bk-master-transcript/v0.1', 'source_id': 'sample',
                'language': 'ja-jp', 'speakers': [{'key': 'one', 'name': '未確認'}],
                'utterances': [{'speaker': 'one', 'words': [{'text': 'オオサカ', 'start': 1, 'end': 2}]}]}))
            reference = root / 'names.txt'; reference.write_text('オオサカ → 大阪\n')
            work = Workspace(master, root / 'work')
            work.mutate('/api/references/load', {'revision': 0, 'paths': [str(reference)]})
            result = work.mutate('/api/references/suggest', {'revision': 1})
            candidate = result['references']['suggestions'][0]
            with self.assertRaises(Conflict):
                work.mutate('/api/references/adopt', {'revision': 2, 'suggestion_id': candidate['id'],
                    'segment_index': 0, 'current_text': '別の本文'})
            before = work.state['master']['utterances'][0]['words'][:]
            work.mutate('/api/references/adopt', {'revision': 2, 'suggestion_id': candidate['id'],
                'segment_index': 0, 'current_text': 'オオサカ'})
            row = work.project()['items'][0]
            self.assertEqual(row['text'], '大阪')
            self.assertEqual(row['alignment_status'], 'unresolved')
            self.assertEqual(work.state['master']['utterances'][0]['words'], before)
            work.close()

    def test_unknown_language_and_two_provider_speakers_remain_reviewable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); media = root / 'input.wav'; media.write_bytes(b'original')
            work = Workspace(None, root / 'work')
            work.mutate('/api/workflow/configure', {'revision': 0, 'mode': 'multilingual',
                'engine': 'local-whisper', 'media_path': str(media)})
            master = _timed_master([{'text': 'Hello', 'start': 0, 'end': .5, 'speaker': 'A'},
                {'text': 'world', 'start': .5, 'end': .9, 'speaker': 'A'},
                {'text': 'Guten', 'start': 1, 'end': 1.4, 'speaker': 'B'}], media, '??-??', 'fixture')
            self.assertEqual(len(master['speakers']), 2)
            self.assertEqual(master['utterances'][0]['words'][1]['text'], ' world')
            self.assertEqual([u['speaker'] for u in master['utterances']], ['spk_A', 'spk_B'])
            with patch('multilingual_review.engine_availability', return_value={'local-whisper': {'available': True, 'reason': ''}}), \
                 patch('multilingual_review.transcribe_local', return_value=master):
                work.mutate('/api/workflow/start', {'revision': 1})
                for _ in range(100):
                    if work.project()['workflow']['stage'] != 'transcribing': break
                    time.sleep(.01)
            self.assertEqual(work.project()['workflow']['stage'], 'review')
            self.assertEqual(work.project()['items'][0]['language'], '??-??')
            self.assertEqual(media.read_bytes(), b'original')
            work.close()

    def test_assemblyai_saved_job_maps_two_speaker_turns_without_reupload(self):
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / 'source.wav'; media.write_bytes(b'source')
            response = {'id': 'remote-123', 'status': 'completed', 'language_code': 'en',
                'utterances': [{'start': 0, 'end': 500, 'speaker': 'A'},
                               {'start': 600, 'end': 1000, 'speaker': 'B'}],
                'words': [{'text': 'Hello', 'start': 0, 'end': 400},
                          {'text': 'Hallo', 'start': 600, 'end': 900}]}
            with patch('workflow_backend.urllib.request.urlopen', return_value=io.BytesIO(json.dumps(response).encode())) as opened:
                master = transcribe_assemblyai(media, '??-??', {'key': 'runtime-only', 'model': 'test'}, 'remote-123')
            self.assertEqual(opened.call_count, 1)
            self.assertEqual([u['speaker'] for u in master['utterances']], ['spk_A', 'spk_B'])
            self.assertEqual([u['language'] for u in master['utterances']], ['??-??', '??-??'])

    def test_first_import_requires_reported_export_error_and_explicit_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); media = root / 'media.wav'; media.write_bytes(b'original')
            source = root / 'master.json'
            source.write_text(json.dumps(_timed_master([{'text': 'Hello', 'start': 0, 'end': .5}],
                media, 'en-us', 'fixture')))
            work = Workspace(source, root / 'work', media=media)
            work.premiere_ipc_root = str(root / 'ipc')
            target = {'projectPath': '/project.prproj', 'clipName': 'media.wav', 'mediaPath': str(media.resolve())}
            def respond(operation, payload, **kwargs):
                if operation == 'snapshot':
                    return {'target': target, 'transcript': None, 'transcriptAvailable': False,
                            'exportError': 'no existing transcript'}
                self.assertEqual(operation, 'apply_transcript')
                self.assertIsNone(payload['expectedBefore'])
                self.assertTrue(payload['allowFirstImport'])
                self.assertEqual(payload['expectedExportError'], 'no existing transcript')
                return {'state': 'applied_verified', 'verified': True, 'saved': True, 'backupPath': '/backup'}
            with patch('multilingual_review.BridgeClient.request', side_effect=respond) as request:
                work.mutate('/api/premiere/snapshot', {'revision': 0, 'target': target})
                with self.assertRaisesRegex(ValueError, '明示確認'):
                    work.mutate('/api/premiere/apply', {'revision': 0, 'target': target, 'confirm_apply': True})
                work.mutate('/api/premiere/apply', {'revision': 0, 'target': target,
                    'confirm_apply': True, 'allow_first_import': True})
            self.assertEqual(request.call_count, 2)
            self.assertEqual(work.project()['workflow']['output']['premiere_status'], 'applied_verified')
            work.close()

    def test_local_whisper_worker_timeout_is_bounded_and_killed(self):
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / 'source.wav'; media.write_bytes(b'source')
            with patch('workflow_backend.subprocess.Popen') as created, patch('workflow_backend.os.killpg') as killed:
                process = created.return_value
                process.pid = 12345
                process.wait.side_effect = [subprocess.TimeoutExpired('worker', 600), -9]
                with self.assertRaisesRegex(ValueError, '600秒以内に完了'):
                    transcribe_local(media, '??-??')
                created.assert_called_once()
                self.assertTrue(created.call_args.kwargs['start_new_session'])
                killed.assert_called_once()

    def test_local_whisper_worker_failure_returns_to_review(self):
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / 'source.wav'; media.write_bytes(b'source')
            with patch('workflow_backend.subprocess.Popen') as created:
                created.return_value.wait.return_value = 7
                with self.assertRaisesRegex(ValueError, 'exit 7'):
                    transcribe_local(media, '??-??')


if __name__ == '__main__': unittest.main()
