import sys
import tempfile
import time
import unittest
import unicodedata
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from workflow_runner import Runner


class RunnerTest(unittest.TestCase):
    def make_runner(self, root):
        ipc = root / 'ipc'; ipc.mkdir()
        return Runner(root / 'workspace', ipc, 18904)

    def test_status_needs_only_exact_workspace_and_start_needs_target(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); runner = self.make_runner(root)
            request = {'version': 1, 'nonce': 'a' * 32, 'operation': 'status',
                       'deadlineMs': int(time.time() * 1000) + 30000,
                       'payload': {'workspace': str(runner.workspace)}}
            with patch.object(runner, '_probe', return_value=None):
                self.assertEqual(runner.handle(request)['state'], 'stopped')
            with self.assertRaisesRegex(ValueError, 'workspace differs'):
                runner.handle({**request, 'payload': {'workspace': str(root / 'other')}})
            with self.assertRaisesRegex(ValueError, 'target is incomplete'):
                runner.handle({**request, 'operation': 'start_or_resume',
                    'payload': {'workspace': str(runner.workspace), 'mode': 'multilingual', 'target': {}}})
            runner.lock.close()

    def test_review_child_inherits_exact_ipc_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); runner = self.make_runner(root)
            media = root / 'source.wav'; media.write_bytes(b'audio')
            project = {'workspace_root': str(runner.workspace),
                       'workflow': {'media_path': str(media.resolve())}}
            with patch.object(runner, '_probe', side_effect=[None, project, project]), \
                 patch('workflow_runner.subprocess.Popen') as spawned:
                spawned.return_value.poll.return_value = None
                result = runner._start_multilingual(media.resolve())
            self.assertEqual(result['state'], 'ready')
            command = spawned.call_args.args[0]
            self.assertIn('--media', command)
            self.assertEqual(spawned.call_args.kwargs['env']['PREMIERE_TRANSCRIPT_IPC_ROOT'], str(runner.ipc_root))
            self.assertTrue(spawned.call_args.kwargs['start_new_session'])
            runner.lock.close()

    def test_workspace_accepts_same_mac_folder_with_different_unicode_form(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / unicodedata.normalize('NFD', '案件フォルダ')
            root.mkdir()
            runner = self.make_runner(root)
            alternate = unicodedata.normalize('NFC', str(runner.workspace))
            request = {'version': 1, 'nonce': 'b' * 32, 'operation': 'status',
                       'deadlineMs': int(time.time() * 1000) + 30000,
                       'payload': {'workspace': alternate}}
            try:
                if not Path(alternate).exists():
                    self.skipTest('filesystem distinguishes Unicode normalization forms')
                with patch.object(runner, '_probe', return_value=None):
                    self.assertEqual(runner.handle(request)['workspace'], alternate)
            finally: runner.lock.close()

    def test_python_venv_symlink_is_not_resolved_to_base_interpreter(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); ipc = root / 'ipc'; ipc.mkdir()
            executable = root / 'venv' / 'bin' / 'python'
            executable.parent.mkdir(parents=True)
            executable.symlink_to(sys.executable)
            runner = Runner(root / 'workspace', ipc, 18905, python=executable)
            self.assertEqual(runner.python, str(executable.absolute()))
            media = root / 'source.wav'; media.write_bytes(b'audio')
            project = {'workspace_root': str(runner.workspace),
                       'workflow': {'media_path': str(media.resolve())}}
            with patch.object(runner, '_probe', side_effect=[None, project, project]), \
                 patch('workflow_runner.subprocess.Popen') as spawned:
                spawned.return_value.poll.return_value = None
                runner._start_multilingual(media.resolve())
            self.assertEqual(spawned.call_args.args[0][0], str(executable.absolute()))
            runner.lock.close()


if __name__ == '__main__': unittest.main()
