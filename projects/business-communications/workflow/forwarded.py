"""Group authorized test LINE forwards without changing the received records."""

import argparse
import csv
import datetime as dt
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from decide import decide, dry_run, stable_id
from forward_ocr import add_image_evidence, file_fingerprints, recognize_screenshot


FIELDS = {
    '受信ID', '経路', '環境', '受信経路詳細', 'LINE送信元種別', 'LINEグループID', '転送受付',
    '送信者ID', 'イベント種別', 'メッセージ種別', '本文', '受信日時', 'LINE発生日時',
    '原文ファイルID', '保存状態', '元ファイル名', 'DriveファイルID',
}
DECLARATION = re.compile(r'(?:転送元[：:]\s*([^\r\n「」『』]{1,60})|([^\s：:「」『』]{1,30}さん)から)[。．]?')
END = {'転送終了', '以上です'}
ATTACHMENTS = {'image', 'file', 'audio', 'video'}


def timestamp(value):
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.utcoffset() is None:
        raise ValueError('forwarding timestamps require a timezone')
    return parsed


def read_forwards(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream, strict=True)
        fields = reader.fieldnames or []
        if not FIELDS.issubset(fields) or len(fields) != len(set(fields)):
            raise ValueError('invalid forwarding ledger columns')
        return list(reader)


def message_rows(rows, now):
    by_id = {}
    for row in rows:
        if (row.get('経路'), row.get('環境'), row.get('イベント種別'), row.get('受信経路詳細'),
                row.get('LINE送信元種別'), row.get('転送受付')) != (
                'LINE', 'test', 'message', '専用グループ転送', 'group', '認可済み'):
            continue
        if not all(isinstance(row.get(field), str) for field in FIELDS):
            raise ValueError('incomplete authorized forwarding row')
        if (not row['受信ID'] or not re.fullmatch(r'C[0-9a-f]{32}', row['LINEグループID']) or
                not re.fullmatch(r'U[0-9a-f]{32}', row['送信者ID'])):
            raise ValueError('invalid authorized forwarding identity')
        received = timestamp(row['受信日時'])
        if received > now:
            continue
        event = timestamp(row['LINE発生日時']) if row['LINE発生日時'] else None
        value = ({key: row[key] for key in FIELDS}, received, event)
        prior = by_id.setdefault(row['受信ID'], value)
        if prior != value:
            raise ValueError('conflicting duplicate forwarding receipt')
    return sorted(by_id.values(), key=lambda item: (item[2] or item[1], item[1], item[0]['受信ID']))


def sender_label(row):
    if row['メッセージ種別'] == 'text':
        match = DECLARATION.fullmatch(row['本文'].strip())
        if match:
            return next(value for value in match.groups() if value is not None).strip().rstrip('。．')
    return ''


def group_messages(messages, quiet):
    batch = []
    labels = set()
    has_content = False
    for item in messages:
        row, received, event = item
        label = sender_label(row)
        if batch:
            previous = batch[-1]
            gap = (event or received) - (previous[2] or previous[1])
            if gap >= quiet or (label and labels and label not in labels and has_content):
                yield batch
                batch, labels, has_content = [], set(), False
        batch.append(item)
        if label:
            labels.add(label)
        elif row['メッセージ種別'] == 'text' and row['本文'].strip() in END and has_content:
            yield batch
            batch, labels, has_content = [], set(), False
        else:
            has_content = True
    if batch:
        yield batch


def describe(batch, now, quiet, max_open):
    source_ids = [row['受信ID'] for row, _, _ in batch]
    sender_candidates, statements, attachments, evidence, issues = [], [], [], [], set()
    saved = True
    for position, (row, received, event) in enumerate(batch, 1):
        receipt = row['受信ID']
        state = row['保存状態']
        kind = row['メッセージ種別']
        text = row['本文']
        file_id = row['DriveファイルID']
        if state != '保存済み' or not row['原文ファイルID'] or (kind in ATTACHMENTS and not file_id):
            saved = False
        if state == '保存失敗':
            issues.add('attachment_or_archive_failed')
        label = sender_label(row)
        if label:
            sender_candidates.append({'name': label, 'method': 'owner_declaration', 'receipt_id': receipt})
            continue
        if kind == 'text' and text.strip() in END:
            continue
        statement = {'receipt_id': receipt, 'order': position, 'archive_file_id': row['原文ファイルID'] or None,
                     'webhook_sender_id': row['送信者ID'], 'webhook_event_at': event.isoformat() if event else None,
                     'received_at': received.isoformat(), 'message_type': kind, 'original_sender': None,
                     'original_contact_id': None, 'original_at': None, 'time_precision': 'unknown',
                     'original_information_status': 'unknown'}
        statements.append(statement)
        review_item = {'id': receipt, 'text': text if kind == 'text' else ''}
        if kind in ATTACHMENTS:
            attachments.append({'receipt_id': receipt, 'file_id': file_id or None, 'name': row['元ファイル名'],
                                'role': 'unconfirmed' if kind == 'image' else 'received_file',
                                'matching_status': 'unconfirmed'})
            if kind != 'image' and file_id:
                review_item['attachments'] = [{'id': file_id}]
            if kind == 'image':
                issues.add('image_role_and_content_unconfirmed')
        elif kind != 'text':
            issues.add('unsupported_original_content')
        evidence.append(review_item)

    names = {candidate['name'] for candidate in sender_candidates}
    if len(statements) == 1 and len(names) == 1 and statements[0]['message_type'] == 'text':
        statements[0].update(original_sender=next(iter(names)), original_information_status='unconfirmed',
                             sender_evidence_ids=[candidate['receipt_id'] for candidate in sender_candidates])
    if not statements or len(statements) > 1 or len(names) != 1:
        issues.add('original_sender_or_boundary_unconfirmed')
    if statements:
        issues.add('original_time_unknown')
    first_received = min(received for _, received, _ in batch)
    last_received = max(received for _, received, _ in batch)
    stale = not saved and now - first_received >= max_open or last_received - first_received >= max_open
    if stale:
        issues.add('long_open_unit')
    state = 'review' if stale or 'attachment_or_archive_failed' in issues else 'collecting'
    if saved and now - last_received >= quiet:
        state = 'review'
    unit_id = stable_id('forward-', sorted(source_ids))
    decision = preview = None
    if state == 'review' and saved and evidence:
        task = {'task_id': unit_id, 'evidence': evidence, 'allowed_actions': []}
        decision = decide(task)
        if decision['classification'] == 'unknown' and len(evidence) > 1:
            issues.add('mixed_content_requires_review')
        preview = dry_run(task, decision)
    return {'unit_id': unit_id, 'state': state, 'source_ids': source_ids, 'statements': statements,
            'sender_candidates': sender_candidates, 'attachments': attachments, 'uncertainties': sorted(issues),
            'decision': decision, 'preview': preview}


def review_forwards(rows, now, quiet_seconds=120, max_open_seconds=1800, *, layouts=None, fingerprints=None):
    if now.utcoffset() is None or not 0 < quiet_seconds < max_open_seconds:
        raise ValueError('invalid forwarding time settings')
    quiet = dt.timedelta(seconds=quiet_seconds)
    max_open = dt.timedelta(seconds=max_open_seconds)
    layouts = layouts or {}
    fingerprints = fingerprints or {}
    by_pair = defaultdict(list)
    for item in message_rows(rows, now):
        row = item[0]
        by_pair[(row['LINEグループID'], row['送信者ID'])].append(item)
    reports = []
    for messages in by_pair.values():
        seen_content = {}
        seen_files = {}
        for batch in group_messages(messages, quiet):
            report = describe(batch, now, quiet, max_open)
            report['screenshot_evidence'] = add_image_evidence(report, layouts)
            for attachment in report['attachments']:
                fingerprint = fingerprints.get(attachment['file_id'])
                if fingerprint is not None:
                    attachment['local_file_fingerprint'] = fingerprint
                    preview_key = fingerprint['sha256']
                    prior_file = seen_files.setdefault(preview_key, attachment['receipt_id'])
                    if prior_file != attachment['receipt_id']:
                        attachment['possible_same_file_as'] = prior_file
            content = [row['本文'] for row, _, _ in batch if row['メッセージ種別'] == 'text'
                       and not sender_label(row) and row['本文'].strip() not in END]
            if content:
                fingerprint = stable_id('', content)
                prior = seen_content.setdefault(fingerprint, report['unit_id'])
                if prior != report['unit_id']:
                    report['possible_retransmission_of'] = prior
            reports.append(report)
    return reports


def record_reviews(path, reports):
    """Keep immutable observations and link late overlaps as correction candidates."""
    db = sqlite3.connect(str(path), timeout=10)
    try:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS forward_sources(receipt_id TEXT PRIMARY KEY, unit_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS forward_reviews(revision_id TEXT PRIMARY KEY, body TEXT NOT NULL);
        ''')
        with db:
            db.execute('BEGIN IMMEDIATE')
            for report in reports:
                if report['state'] != 'review':
                    continue
                prior_units = set()
                for receipt in report['source_ids']:
                    previous = db.execute('SELECT unit_id FROM forward_sources WHERE receipt_id=?', (receipt,)).fetchone()
                    if previous and previous[0] != report['unit_id']:
                        prior_units.add(previous[0])
                if prior_units:
                    report['correction_candidates_for'] = sorted(prior_units)
                    report['decision'] = report['preview'] = None
                else:
                    db.executemany('INSERT OR IGNORE INTO forward_sources VALUES(?,?)',
                                   [(receipt, report['unit_id']) for receipt in report['source_ids']])
                revision = stable_id('revision-', report)
                db.execute('INSERT OR IGNORE INTO forward_reviews VALUES(?,?)',
                           (revision, json.dumps(report, ensure_ascii=False, sort_keys=True)))
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ledger', type=Path)
    parser.add_argument('review_database', type=Path)
    parser.add_argument('--quiet-seconds', type=int, default=120)
    parser.add_argument('--layout-evidence', type=Path, help='JSON of saved image IDs to local OCR observations')
    parser.add_argument('--local-files', type=Path, help='JSON of saved file IDs to selected local copies')
    parser.add_argument('--ocr', type=Path, help='local OCR executable for selected saved images')
    args = parser.parse_args()
    layouts = json.loads(args.layout_evidence.read_text(encoding='utf-8')) if args.layout_evidence else {}
    paths = json.loads(args.local_files.read_text(encoding='utf-8')) if args.local_files else {}
    if not isinstance(layouts, dict) or not isinstance(paths, dict):
        parser.error('evidence files must contain JSON objects')
    if args.ocr and not args.local_files:
        parser.error('--ocr requires --local-files')
    rows = read_forwards(args.ledger)
    now = dt.datetime.now(dt.timezone.utc)
    if args.ocr:
        for row, _, _ in message_rows(rows, now):
            file_id = row['DriveファイルID']
            if row['メッセージ種別'] == 'image' and row['保存状態'] == '保存済み' and file_id in paths and file_id not in layouts:
                layouts[file_id] = recognize_screenshot(paths[file_id], args.ocr)
    reports = review_forwards(rows, now, args.quiet_seconds, layouts=layouts, fingerprints=file_fingerprints(paths))
    record_reviews(args.review_database, reports)
    print(json.dumps(reports, ensure_ascii=False))


if __name__ == '__main__':
    main()
