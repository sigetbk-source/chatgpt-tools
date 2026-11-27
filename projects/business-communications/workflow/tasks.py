"""Store selected evidence and route review-only decisions through a durable queue."""

import argparse
import json
import secrets
import sqlite3
import time
from pathlib import Path

from decide import decide, dry_run, sources, stable_id


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('database', type=Path)
    commands = parser.add_subparsers(dest='action', required=True)
    add = commands.add_parser('add')
    add.add_argument('task', type=Path)
    run = commands.add_parser('run')
    run.add_argument('--owner', default='local-rules')
    retry = commands.add_parser('requeue')
    retry.add_argument('task_id')
    args = parser.parse_args()
    queue = TaskQueue(args.database)
    timestamp = int(time.time())
    try:
        if args.action == 'add':
            task = json.loads(args.task.read_text(encoding='utf-8'))
            result = {'task_id': queue.add(task, timestamp)}
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
