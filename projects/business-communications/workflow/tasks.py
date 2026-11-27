"""Store selected evidence and route review-only decisions through a durable queue."""

import argparse
import csv
import hashlib
import json
import secrets
import sqlite3
import subprocess
import time
from pathlib import Path

from decide import decide, dry_run, sources, stable_id


LINE_RECEIPT_FIELDS = {
    '受信ID', '経路', '環境', 'イベント種別', 'メッセージ種別', '本文',
    '保存状態', '原文ファイルID', 'DriveファイルID', '元ファイル名',
}
LINE_ATTACHMENT_TYPES = {'image', 'video', 'audio', 'file'}


class TaskQueue:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path), timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks(
                task_id TEXT PRIMARY KEY, input TEXT NOT NULL, state TEXT NOT NULL,
                owner TEXT, lease TEXT, expires INTEGER, created INTEGER NOT NULL,
                updated INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS decisions(
                task_id TEXT NOT NULL, decision_id TEXT NOT NULL, engine TEXT NOT NULL,
                decision TEXT NOT NULL, preview TEXT NOT NULL, created INTEGER NOT NULL,
                PRIMARY KEY(task_id, decision_id));
        """)

    def add(self, task, now):
        normalized = normalize_task(task)
        body = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        identifier = normalized['task_id']
        self.db.execute(
            "INSERT OR IGNORE INTO tasks(task_id,input,state,created,updated) VALUES(?,?,'pending',?,?)",
            (identifier, body, now, now))
        existing = self.db.execute("SELECT input FROM tasks WHERE task_id=?", (identifier,)).fetchone()
        if existing['input'] != body:
            raise ValueError('task ID already refers to different evidence')
        return identifier

    def claim(self, owner, now):
        if not isinstance(owner, str) or not owner or len(owner) > 100:
            raise ValueError('owner is required')
        token = secrets.token_hex(16)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("""
                SELECT task_id,input FROM tasks
                WHERE state='pending' OR (state='claimed' AND expires<=?)
                ORDER BY created,task_id LIMIT 1
            """, (now,)).fetchone()
            if row:
                self.db.execute("""
                    UPDATE tasks SET state='claimed',owner=?,lease=?,expires=?,updated=?
                    WHERE task_id=?
                """, (owner, token, now + 300, now, row['task_id']))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return {'lease': token, 'task': json.loads(row['input'])} if row else None

    def submit(self, owner, lease, proposal, now):
        identifier = proposal['task_id']
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT * FROM tasks WHERE task_id=?", (identifier,)).fetchone()
            encoded = json.dumps(proposal, ensure_ascii=False, sort_keys=True)
            if not row or row['owner'] != owner or row['lease'] != lease:
                raise ValueError('task lease is unavailable')
            old = self.db.execute("SELECT decision,preview FROM decisions WHERE task_id=? AND decision_id=?",
                                  (identifier, proposal['decision_id'])).fetchone()
            if old:
                if old['decision'] != encoded:
                    raise ValueError('decision ID already refers to a different proposal')
                if row['state'] == 'claimed':
                    if row['expires'] <= now:
                        raise ValueError('task lease expired')
                    self.db.execute("UPDATE tasks SET state='review',updated=? WHERE task_id=?", (now, identifier))
                self.db.commit()
                return json.loads(old['preview'])
            if row['state'] != 'claimed' or row['expires'] <= now:
                raise ValueError('task lease expired')
            preview = dry_run(json.loads(row['input']), proposal)
            self.db.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?)",
                            (identifier, proposal['decision_id'], owner, encoded,
                             json.dumps(preview, ensure_ascii=False), now))
            self.db.execute("UPDATE tasks SET state='review',updated=? WHERE task_id=?", (now, identifier))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return preview

    def requeue(self, identifier, now):
        updated = self.db.execute("""
            UPDATE tasks SET state='pending',owner=NULL,lease=NULL,expires=NULL,updated=?
            WHERE task_id=? AND state='review'
        """, (now, identifier))
        if updated.rowcount != 1:
            raise ValueError('task is not awaiting review')

    def show(self, identifier):
        row = self.db.execute("""
            SELECT tasks.state, decisions.decision, decisions.preview
            FROM tasks LEFT JOIN decisions USING(task_id) WHERE tasks.task_id=?
            ORDER BY decisions.created DESC, decisions.rowid DESC LIMIT 1
        """, (identifier,)).fetchone()
        if row is None:
            raise ValueError('task not found')
        return {'task_id': identifier, 'state': row['state'],
                'decision': json.loads(row['decision']) if row['decision'] else None,
                'preview': json.loads(row['preview']) if row['preview'] else None}


def normalize_task(task):
    if task.get('environment') != 'test':
        raise ValueError('test environment required')
    items = sources(task)
    for item in items.values():
        channel = item.get('channel')
        if channel not in {'line', 'screenshot', 'gmail', 'sms', 'manual'}:
            raise ValueError('unsupported source')
        if channel in {'screenshot', 'gmail', 'sms'} and item.get('selected_by_owner') is not True:
            raise ValueError('source has not been selected')
    identifier = task.get('task_id') or stable_id('task-', ['test', sorted(items)])
    return {**task, 'task_id': identifier, 'schema_version': '1.0'}


def screenshot_task(image, executable):
    """Create a task from an explicitly selected image; OCR stays on this computer."""
    if not image.is_file() or image.stat().st_size > 25 * 1024 * 1024:
        raise ValueError('select a local image smaller than 25 MiB')
    before = hashlib.sha256(image.read_bytes()).hexdigest()
    output = subprocess.run([str(executable.resolve()), str(image.resolve())], check=True,
                            capture_output=True, text=True, timeout=40)
    if before != hashlib.sha256(image.read_bytes()).hexdigest():
        raise ValueError('image changed during OCR')
    recognized = json.loads(output.stdout)
    if not isinstance(recognized, list) or not recognized or not all(isinstance(line, str) for line in recognized):
        raise ValueError('no usable text in selected image')
    text = '\n'.join(recognized).strip()
    if not text:
        raise ValueError('no usable text in selected image')
    return {'environment': 'test', 'evidence': [{'id': 'screenshot-' + before, 'channel': 'screenshot',
            'text': text, 'selected_by_owner': True, 'origin': 'local_ocr'}], 'allowed_actions': []}


def line_receipt_task(ledger, receipt_id):
    """Read one saved test LINE message from the receipt tab's CSV export."""
    with ledger.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream, strict=True)
        fields = reader.fieldnames or []
        if not LINE_RECEIPT_FIELDS.issubset(fields) or len(fields) != len(set(fields)):
            raise ValueError('invalid receipt ledger columns')
        receipt = None
        for row in reader:
            if not receipt_id or row['受信ID'] != receipt_id:
                continue
            if receipt is not None:
                raise ValueError('duplicate receipt ID')
            if None in row or any(row[field] is None for field in LINE_RECEIPT_FIELDS):
                raise ValueError('incomplete receipt row')
            receipt = row
    if receipt is None:
        raise ValueError('receipt not found')
    if (receipt['経路'] != 'LINE' or receipt['環境'] != 'test' or
            receipt['イベント種別'] != 'message' or receipt['保存状態'] != '保存済み' or
            not receipt['原文ファイルID']):
        raise ValueError('receipt must be a saved test LINE message with an archive reference')

    kind = receipt['メッセージ種別']
    evidence = {'id': receipt_id, 'channel': 'line', 'text': receipt['本文'],
                'archive_file_id': receipt['原文ファイルID']}
    if kind in LINE_ATTACHMENT_TYPES and receipt['DriveファイルID']:
        evidence['attachments'] = [{'id': receipt['DriveファイルID'], 'message_type': kind,
                                    'original_name': receipt['元ファイル名']}]
    elif kind != 'text' or not receipt['本文'].strip():
        raise ValueError('receipt has no usable text or saved attachment')
    return {'environment': 'test', 'evidence': [evidence], 'allowed_actions': []}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('database', type=Path)
    commands = parser.add_subparsers(dest='action', required=True)
    add = commands.add_parser('add')
    add.add_argument('task', type=Path)
    screenshot = commands.add_parser('add-screenshot')
    screenshot.add_argument('image', type=Path)
    screenshot.add_argument('--ocr', type=Path, required=True)
    line = commands.add_parser('add-line')
    line.add_argument('ledger', type=Path)
    line.add_argument('receipt_id')
    run = commands.add_parser('run')
    run.add_argument('--owner', default='local-rules')
    show = commands.add_parser('show')
    show.add_argument('task_id')
    retry = commands.add_parser('requeue')
    retry.add_argument('task_id')
    args = parser.parse_args()
    queue = TaskQueue(args.database)
    timestamp = int(time.time())
    try:
        if args.action == 'add':
            task = json.loads(args.task.read_text(encoding='utf-8'))
            result = {'task_id': queue.add(task, timestamp)}
        elif args.action == 'add-screenshot':
            result = {'task_id': queue.add(screenshot_task(args.image, args.ocr), timestamp)}
        elif args.action == 'add-line':
            result = {'task_id': queue.add(line_receipt_task(args.ledger, args.receipt_id), timestamp)}
        elif args.action == 'show':
            result = queue.show(args.task_id)
        elif args.action == 'requeue':
            queue.requeue(args.task_id, timestamp)
            result = {'status': 'pending'}
        else:
            job = queue.claim(args.owner, timestamp)
            if job:
                result = queue.submit(args.owner, job['lease'], decide(job['task']), int(time.time()))
            else:
                result = {'status': 'empty'}
    finally:
        queue.db.close()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
