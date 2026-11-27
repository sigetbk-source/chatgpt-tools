import copy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from contacts import resolve_destination
from decide import decide, dry_run
from tasks import TaskQueue, screenshot_task


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.task = {'task_id': 'task-1', 'confirmed_case_id': 'case-1',
                     'cases': [{'id': 'case-1', 'title': '番組A', 'folders': {'script': 'scripts'}},
                               {'id': 'case-2', 'title': '番組A'},
                               {'id': 'subfolder', 'title': '02_素材'}],
                     'calendar': [{'id': 'event', 'case_id': 'case-1', 'version': 'v1', 'all_day': True}],
                     'evidence': [{'id': 'line-1', 'text': '日程変更をお願いします。2026-12-08'}],
                     'allowed_actions': ['update_calendar', 'move_file']}

    def test_dry_run_keeps_case_identity_and_version(self):
        decision = decide(self.task)
        preview = dry_run(self.task, decision)
        operation, = preview['operations']
        self.assertEqual(operation['target_id'], 'event')
        self.assertEqual(operation['parameters']['start_date'], '2026-12-08')
        self.assertEqual(operation['expected_version'], 'v1')
        self.assertEqual(preview, dry_run(self.task, decision))
        self.assertNotIn('subfolder', decision['project']['candidate_ids'])
        self.task['calendar'][0]['version'] = 'v2'
        with self.assertRaises(ValueError):
            dry_run(self.task, decision)

    def test_ambiguous_or_incomplete_data_is_held(self):
        self.task['evidence'][0]['text'] += ' または2026-12-10'
        self.assertEqual(decide(self.task)['proposed_actions'], [])
        self.task['evidence'][0]['text'] = '新規依頼'
        self.assertIsNone(decide(self.task)['project']['selected_id'])
        self.task['evidence'][0]['text'] = '日程変更 2026-02-30'
        self.assertIn('date_unconfirmed', decide(self.task)['uncertainties'])
        self.task['evidence'][0]['text'] = '日程変更 2026年12月8日'
        self.assertEqual(decide(self.task)['facts'][0]['value'], '2026-12-08')
        self.task['calendar'][0]['all_day'] = False
        self.assertFalse(decide(self.task)['proposed_actions'])

    def test_file_move_requires_verified_type_and_destination(self):
        self.task['evidence'][0].update(text='', attachments=[{'id': 'file', 'version': 'v1'}])
        self.assertFalse(decide(self.task)['proposed_actions'])
        self.task['evidence'][0]['attachments'][0]['reviewed_kind'] = 'script'
        proposal = decide(self.task)
        self.assertEqual(dry_run(self.task, proposal)['operations'][0]['parameters']['destination_id'], 'scripts')
        malicious = copy.deepcopy(proposal)
        malicious['proposed_actions'][0]['parameters']['destination_id'] = 'outside'
        with self.assertRaises(ValueError):
            dry_run(self.task, malicious)

    def test_rejects_invented_evidence_and_action(self):
        proposed = decide(self.task)
        proposed['source_ids'] = ['unknown']
        with self.assertRaises(ValueError):
            dry_run(self.task, proposed)
        proposed = decide(self.task)
        proposed['proposed_actions'][0]['type'] = 'send_line'
        with self.assertRaises(ValueError):
            dry_run(self.task, proposed)

    def test_contact_lookup_uses_scoped_reviewed_identity(self):
        contacts = {'a': {'people_id': 'p1', 'active': True, 'test_owner': True},
                    'b': {'people_id': 'p2', 'active': True}}
        people = {'p1': {'name': '田中', 'emails': [{'value': 'a@example.test', 'primary': True}]},
                  'p2': {'name': '田中'}}
        link = {'contact_id':'a', 'bot_id':'test-bot', 'environment':'test', 'confirmed':True, 'active':True,
                'line_id': 'U' + 'a' * 32}
        lookup = lambda channel, value, **kw: resolve_destination(contacts, people, [link], channel=channel,
                                                                    environment='test', value=value, **kw)
        self.assertEqual(lookup('line', '田中さん', bot_id='test-bot')['status'], 'review')
        self.assertEqual(lookup('line', 'a', bot_id='other-bot')['status'], 'review')
        self.assertEqual(lookup('line', 'a', bot_id='test-bot')['destination'], link['line_id'])
        self.assertEqual(lookup('email', 'a')['destination'], 'a@example.test')

    def test_task_lease_persists_after_reopen_and_rejects_stale_submission(self):
        self.task.update(environment='test')
        self.task['evidence'][0]['channel'] = 'line'
        with tempfile.TemporaryDirectory() as path:
            database = Path(path) / 'tasks.sqlite3'
            queue = TaskQueue(database)
            self.assertEqual(queue.add(self.task, 100), queue.add(self.task, 101))
            first = queue.claim('first', 100)
            queue.db.close()
            queue = TaskQueue(database)
            self.assertIsNone(queue.claim('second', 102))
            second = queue.claim('second', 401)
            decision = decide(second['task'])
            with self.assertRaises(ValueError):
                queue.submit('first', first['lease'], decision, 402)
            result = queue.submit('second', second['lease'], decision, 403)
            self.assertEqual(queue.submit('second', second['lease'], decision, 404), result)
            with self.assertRaises(ValueError):
                queue.submit('other', second['lease'], decision, 404)
            self.assertIsNone(queue.claim('next', 404))
            queue.requeue(self.task['task_id'], 405)
            again = queue.claim('next', 406)
            self.assertEqual(queue.submit('next', again['lease'], decide(again['task']), 407), result)
            self.assertIsNone(queue.claim('next', 408))
            queue.db.close()

    def test_unselected_private_source_cannot_enter_queue(self):
        self.task.update(environment='test')
        self.task['evidence'][0]['channel'] = 'gmail'
        queue = TaskQueue(':memory:')
        with self.assertRaises(ValueError):
            queue.add(self.task, 100)
        self.task['evidence'][0]['selected_by_owner'] = True
        self.assertEqual(queue.add(self.task, 101), self.task['task_id'])
        queue.db.close()

    def test_selected_screenshot_becomes_review_only_evidence(self):
        with tempfile.TemporaryDirectory() as path:
            image = Path(path) / 'selected.png'
            image.write_bytes(b'image-contents')
            tool = Path(path) / 'ocr'
            process = subprocess.CompletedProcess([], 0, '["日程変更","2026年12月8日"]', '')
            with patch('tasks.subprocess.run', return_value=process) as run:
                task = screenshot_task(image, tool)
                self.assertEqual(run.call_args.args[0], [str(tool.resolve()), str(image.resolve())])
            queue = TaskQueue(':memory:')
            queue.add(task, 100)
            claim = queue.claim('ocr-review', 101)
            decision = decide(claim['task'])
            self.assertEqual(decision['facts'][0]['value'], '2026-12-08')
            self.assertEqual(decision['proposed_actions'], [])
            queue.db.close()


if __name__ == '__main__':
    unittest.main()
