"""Process the test receiver's Drive for desktop snapshot on the local Mac."""

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import tempfile
import time
import unicodedata
from pathlib import Path

from forward_ocr import file_fingerprints, recognize_screenshot
from forwarded import message_rows, record_reviews, review_forwards


SNAPSHOT = 'ocr-inbox-test.json'
SCHEMA = 'line-ocr-test-v1'
TEST_ROOT = '業務連絡管理（テスト専用）'


def load_inbox(root, now):
    if unicodedata.normalize('NFC', root.name) != TEST_ROOT:
        raise ValueError('select the test receiver Drive folder')
    with (root / SNAPSHOT).open('rb') as stream:
        payload = stream.read(8 * 1024 * 1024 + 1)
    if len(payload) >= 8 * 1024 * 1024:
        raise ValueError('test inbox snapshot is too large')
    document = json.loads(payload)
    if not isinstance(document, dict) or document.get('schema') != SCHEMA or not isinstance(document.get('entries'), list):
        raise ValueError('invalid test inbox snapshot')
    records, entries = [], {}
    for item in document['entries']:
        row = item['record']
        identifier = row['受信ID']
        if identifier in entries:
            raise ValueError('duplicate test inbox receipt')
        records.append(row)
        entries[identifier] = item.get('attachment_path')
    authorized = message_rows(records, now)
    return [row for row, _, _ in authorized], entries


def resolve_attachment(root, identifier, components):
    if (not isinstance(components, list) or len(components) != 3 or components[0] != '受信ファイル' or
            any(not isinstance(part, str) or part in {'', '.', '..'} or '/' in part or '\\' in part for part in components) or
            not re.fullmatch(r'\d{4}-\d{2}', components[1]) or not components[2].startswith(identifier + '_')):
        raise ValueError('invalid attachment path in the test inbox')
    path = root.joinpath(*components).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('attachment is outside the test inbox')
    return path


def save_report(directory, name, document):
    destination = directory / name
    body = json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + '\n'
    if destination.is_file() and destination.read_text(encoding='utf-8') == body:
        return
    with tempfile.NamedTemporaryFile(dir=directory, mode='w', encoding='utf-8', delete=False) as stream:
        stream.write(body)
        temporary = Path(stream.name)
    try:
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def collect_image_evidence(rows, paths, root, executable, state, now):
    evidence, fingerprints, waiting = {}, {}, []
    db = sqlite3.connect(str(state), timeout=45)
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS ocr_cache (
            digest TEXT, engine TEXT, tried INTEGER, body TEXT, PRIMARY KEY(digest, engine))''')
        engine = file_fingerprints({'ocr': executable})['ocr']['sha256']
        for row in rows:
            file_id = row['DriveファイルID']
            receipt = row['受信ID']
            components = paths.get(receipt)
            if not file_id or not components or row['保存状態'] != '保存済み':
                continue
            try:
                path = resolve_attachment(root, receipt, components)
                fingerprint = file_fingerprints({file_id: path})[file_id]
            except OSError:
                waiting.append(receipt)
                continue
            fingerprints[file_id] = fingerprint
            if row['メッセージ種別'] != 'image':
                continue
            digest = fingerprint['sha256']
            with db:
                db.execute('BEGIN IMMEDIATE')
                cached = db.execute('SELECT tried, body FROM ocr_cache WHERE digest=? AND engine=?',
                                    (digest, engine)).fetchone()
                if cached:
                    layout = json.loads(cached[1])
                if not cached or (isinstance(layout, dict) and layout.get('error') and int(now.timestamp()) - cached[0] >= 300):
                    layout = recognize_screenshot(path, executable)
                    if file_fingerprints({file_id: path})[file_id] != fingerprint:
                        waiting.append(receipt)
                        continue
                    db.execute('INSERT OR REPLACE INTO ocr_cache VALUES(?,?,?,?)',
                               (digest, engine, int(now.timestamp()), json.dumps(layout, ensure_ascii=False)))
            evidence[file_id] = layout
    finally:
        db.close()
    return evidence, fingerprints, waiting


def process(root, destination, executable, now):
    root, destination = root.resolve(), destination.resolve()
    if destination.is_relative_to(root):
        raise ValueError('put OCR reports outside the synchronized Drive folder')
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination.stat().st_mode & 0o077:
        raise ValueError('OCR report directory must be private')
    records, paths = load_inbox(root, now)
    state = destination / 'review.sqlite3'
    layouts, fingerprints, waiting = collect_image_evidence(records, paths, root, executable, state, now)
    reports = review_forwards(records, now, layouts=layouts, fingerprints=fingerprints)
    waiting_ids = set(waiting)
    ready = [report for report in reports if report['state'] == 'review' and not waiting_ids.intersection(report['source_ids'])]
    record_reviews(state, ready)
    for report in ready:
        save_report(destination, report['unit_id'] + '.json', report)
    return {'review_reports': len(ready), 'waiting_for_drive': len(waiting),
            'ocr_unavailable': sum(isinstance(layout, dict) and layout.get('error') == 'local_ocr_unavailable'
                                   for layout in layouts.values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('drive_folder', type=Path)
    parser.add_argument('report_folder', type=Path)
    parser.add_argument('--ocr', type=Path, required=True)
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    last_status = None
    while True:
        try:
            status = process(args.drive_folder, args.report_folder, args.ocr, dt.datetime.now(dt.timezone.utc))
        except (ValueError, OSError, KeyError, TypeError, sqlite3.Error):
            status = {'error': 'test_ocr_inbox_unavailable'}
        if status != last_status:
            print(json.dumps(status), flush=True)
            last_status = status
        if not args.watch:
            return int('error' in status or bool(status.get('ocr_unavailable')))
        time.sleep(30)


if __name__ == '__main__':
    raise SystemExit(main())
