import json
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python'))
from native_picker import pick_paths, workspace_from_folder
from multilingual_review import Workspace, make_server


class NativePickerTest(unittest.TestCase):
    def test_selected_paths_spaces_unicode_and_cancel(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'日英 mixed.mp3';path.write_bytes(b'media')
            response=SimpleNamespace(returncode=0,stdout=json.dumps([str(path)]),stderr='')
            with patch('native_picker.subprocess.run',return_value=response) as run:
                self.assertEqual(pick_paths('media'),[str(path.resolve())])
                self.assertEqual(run.call_args.args[0][:3],['/usr/bin/osascript','-l','JavaScript'])
            with patch('native_picker.subprocess.run',return_value=SimpleNamespace(returncode=1,stdout='',stderr='User canceled. (-128)')):
                self.assertIsNone(pick_paths('references'))

    def test_unsupported_media_rejected_and_no_shell(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'bad.sh';path.write_text('untrusted file')
            with patch('native_picker.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps([str(path)]),stderr='')):
                with self.assertRaisesRegex(ValueError,'素材'):
                    pick_paths('media')
            with patch('native_picker.subprocess.run') as run:
                with self.assertRaises(ValueError):pick_paths('arbitrary')
                run.assert_not_called()

    def test_new_workspace_is_unique_and_existing_resumes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve()
            a,b=workspace_from_folder(root),workspace_from_folder(root)
            self.assertNotEqual(a,b)
            self.assertEqual(a.parent,root/'文字起こし作業')
            self.assertFalse(a.exists())
            (root/'state.json').write_text('{}')
            self.assertEqual(workspace_from_folder(root),root)

    def test_picker_http_requires_token_and_does_not_start_asr(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);work=Workspace(None,root/'work');server=make_server(work,0)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            url=f'http://127.0.0.1:{server.server_port}'
            def post(token,payload):
                return urllib.request.urlopen(urllib.request.Request(url+'/api/picker/media',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','X-Review-Token':token}))
            try:
                with patch('multilingual_review.pick_paths',return_value=[str(root/'source.mp3')]) as picker,patch('multilingual_review.transcribe_local') as transcribe:
                    with self.assertRaises(urllib.error.HTTPError) as failure:post('wrong',{'revision':0})
                    self.assertEqual(failure.exception.code,403);picker.assert_not_called()
                    value=json.load(post(work.token,{'revision':0}))
                    self.assertEqual(value['paths'],[str(root/'source.mp3')]);self.assertFalse(value['cancelled'])
                    self.assertEqual(work.state['revision'],0);self.assertIsNone(work.media)
                    transcribe.assert_not_called()
                with patch('multilingual_review.pick_paths') as picker:
                    with self.assertRaises(urllib.error.HTTPError) as failure:post(work.token,{'revision':999})
                    self.assertEqual(failure.exception.code,409);picker.assert_not_called()
            finally:
                server.shutdown();server.server_close();work.close();thread.join()


if __name__=='__main__':unittest.main()
