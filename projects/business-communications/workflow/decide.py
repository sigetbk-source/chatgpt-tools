"""Evidence-based decisions and local dry-runs. This module does not call external services."""

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path


DATE = re.compile(r'(?<![\d/])\d{4}-\d{2}-\d{2}(?![\d/])')
CLASSES = {'new_request', 'continuation', 'tentative', 'schedule_confirmed', 'schedule_change',
           'cancellation', 'material_received', 'revision', 'order_or_invoice', 'information',
           'irrelevant', 'unknown'}
RULES = (
    ('cancellation', r'キャンセル|取り消し|中止'),
    ('schedule_change', r'日程変更|日付変更|編集日.{0,12}変更'),
    ('tentative', r'仮押さえ|仮予約'),
    ('schedule_confirmed', r'日程確定|確定しました'),
    ('revision', r'修正指示|差し替え|再編集'),
    ('order_or_invoice', r'発注書|請求書|請求額'),
    ('new_request', r'新規依頼|新規のご依頼'),
    ('continuation', r'続報|進捗'),
)
SUBFOLDER = re.compile(r'^\s*\d{2}[_\s-]')


def stable_id(prefix, data):
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return prefix + hashlib.sha256(canonical.encode()).hexdigest()[:32]


def sources(task):
    evidence = task['evidence']
    by_id = {item['id']: item for item in evidence}
    if not evidence or len(evidence) != len(by_id) or any(not key or not isinstance(item['text'], str)
                                                        for key, item in by_id.items()):
        raise ValueError('invalid evidence')
    return by_id


def dates(text):
    for match in DATE.finditer(text):
        try:
            yield dt.date.fromisoformat(match.group()).isoformat()
        except ValueError:
            continue


def case_candidates(task):
    return {case['id']: case for case in task.get('cases', [])
            if case.get('id') and not SUBFOLDER.match(case.get('title', ''))}


def classification(text, attachments):
    matched = [label for label, pattern in RULES if re.search(pattern, text)]
    if len(matched) > 1:
        return 'unknown'
    if matched:
        return matched[0]
    return 'material_received' if attachments else 'unknown'


def decide(task):
    by_id = sources(task)
    combined = '\n'.join(item['text'] for item in by_id.values())
    category = classification(combined, [a for s in by_id.values() for a in s.get('attachments', [])])
    confirmed = task.get('confirmed_case_id')
    cases = case_candidates(task)
    selected = confirmed if confirmed in cases and category != 'new_request' else None
    facts = [{'field': 'edit_date', 'source_id': key, 'value': date}
             for key, item in by_id.items() for date in sorted(set(dates(item['text'])))]
    distinct_dates = {fact['value'] for fact in facts}
    uncertainties = []
    if not selected:
        uncertainties.append('case_unconfirmed')
    if category == 'unknown':
        uncertainties.append('classification_unconfirmed')
    if category == 'schedule_change' and len(distinct_dates) != 1:
        uncertainties.append('date_unconfirmed')
    actions = []
    if category == 'schedule_change' and selected and len(distinct_dates) == 1:
        events = [event for event in task.get('calendar', [])
                  if event.get('case_id') == selected and event.get('id') and event.get('version')
                  and event.get('all_day') is True]
        if len(events) == 1 and 'update_calendar' in task.get('allowed_actions', []):
            event = events[0]
            actions.append({'type': 'update_calendar', 'target_id': event['id'], 'expected_version': event['version'],
                            'parameters': {'start_date': next(iter(distinct_dates))}})
        else:
            uncertainties.append('calendar_unconfirmed')
    if category == 'material_received' and selected and 'move_file' in task.get('allowed_actions', []):
        for item in by_id.values():
            for file in item.get('attachments', []):
                kind = file.get('reviewed_kind')
                folder = cases[selected].get('folders', {}).get(kind)
                if kind in {'script', 'media', 'order'} and folder and file.get('id') and file.get('version'):
                    actions.append({'type': 'move_file', 'target_id': file['id'], 'expected_version': file['version'],
                                    'parameters': {'destination_id': folder}})
                else:
                    uncertainties.append('destination_unconfirmed')
    payload = {'schema_version': '1.0', 'task_id': task['task_id'],
               'engine': {'type': 'rules', 'provider': 'local', 'model': 'v1'},
               'source_ids': list(by_id), 'classification': category,
               'project': {'match': 'existing' if selected else 'unknown', 'candidate_ids': sorted(cases),
                           'selected_id': selected},
               'facts': facts, 'proposed_actions': actions, 'uncertainties': sorted(set(uncertainties)),
               'requires_review': True, 'reason': '確認済み候補と原文に明記された情報を照合'}
    return {'decision_id': stable_id('decision-', payload), **payload}


def dry_run(task, proposal):
    """Reject arguments that do not match the current evidence and permissions."""
    baseline = decide(task)
    required = set(baseline)
    if set(proposal) != required or proposal['schema_version'] != '1.0' or proposal['task_id'] != task['task_id']:
        raise ValueError('invalid proposal contract')
    if (proposal['classification'] not in CLASSES or proposal['requires_review'] is not True
            or not isinstance(proposal['engine'], dict) or set(proposal['engine']) != {'type', 'provider', 'model'}
            or not all(isinstance(value, str) and value for value in proposal['engine'].values())
            or not isinstance(proposal['reason'], str) or not isinstance(proposal['uncertainties'], list)
            or not all(isinstance(value, str) for value in proposal['uncertainties'])
            or not isinstance(proposal['source_ids'], list) or not isinstance(proposal['facts'], list)
            or not isinstance(proposal['proposed_actions'], list)):
        raise ValueError('invalid proposal values')
    if len(set(proposal['source_ids'])) != len(proposal['source_ids']) or set(proposal['source_ids']) != set(baseline['source_ids']):
        raise ValueError('invalid evidence references')
    if proposal['project'] != baseline['project'] or any(fact not in baseline['facts'] for fact in proposal['facts']):
        raise ValueError('unverified project or fact')
    if not isinstance(proposal['decision_id'], str) or not proposal['decision_id']:
        raise ValueError('invalid decision id')
    actions = proposal['proposed_actions']
    if actions and proposal['classification'] != baseline['classification']:
        raise ValueError('classification conflict')
    if any(action not in baseline['proposed_actions'] for action in actions):
        raise ValueError('unauthorized action or stale target')
    keys = [stable_id('operation-', [task['task_id'], action]) for action in actions]
    if len(set(keys)) != len(keys):
        raise ValueError('duplicated operation')
    return {'status': 'requires_review', 'mode': 'dry_run', 'task_id': task['task_id'],
            'decision_id': proposal['decision_id'], 'operations': [
                {'idempotency_key': key, **action} for key, action in zip(keys, actions)
            ]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('task_file', type=Path)
    parser.add_argument('--proposal', type=Path)
    args = parser.parse_args()
    try:
        task = json.loads(args.task_file.read_text(encoding='utf-8'))
        decision = json.loads(args.proposal.read_text(encoding='utf-8')) if args.proposal else decide(task)
        preview = dry_run(task, decision)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'Invalid task: {exc}', file=sys.stderr)
        return 1
    print(json.dumps({'decision': decision, 'preview': preview}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
