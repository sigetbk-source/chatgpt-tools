"""Turn local screenshot OCR positions and file hashes into review evidence."""

import hashlib
import re
import unicodedata
from pathlib import Path

from decide import dates


TIME = re.compile(r'(?:[01]?\d|2[0-3]):[0-5]\d')
PARTIAL_DATE = re.compile(r'\d{1,2}月\s*\d{1,2}日(?:[（(][月火水木金土日](?:曜(?:日)?)?[）)])?')
FULL_DATE = re.compile(r'(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{4}年\s*\d{1,2}月\s*\d{1,2}日)')
FILE_NAME = re.compile(r'[^/\\\r\n]+\.(?:docx?|xlsx?|pdf|pptx?|zip|mp4|mov|wav|mp3|jpe?g|png)', re.I)


def screenshot_lines(layout):
    if not isinstance(layout, list) or not layout or len(layout) > 1000:
        raise ValueError('invalid screenshot OCR lines')
    lines = []
    for item in layout:
        if not isinstance(item, dict) or not isinstance(item.get('text'), str):
            raise ValueError('invalid screenshot OCR observation')
        x, y, width, height = (item.get(key) for key in ('x', 'y', 'width', 'height'))
        if any(type(value) not in (float, int) or not 0 <= value <= 1 for value in (x, y, width, height)):
            raise ValueError('invalid screenshot OCR bounds')
        text = unicodedata.normalize('NFC', item['text']).strip()
        if text:
            lines.append((y, x, width, height, text))
    result = []
    for order, (y, x, width, height, text) in enumerate(sorted(lines), 1):
        side = 'left' if x + width <= 0.48 else 'right' if x >= 0.52 else 'unknown'
        observed_date = next(dates(text), None) if FULL_DATE.fullmatch(text) else None
        if observed_date:
            kind, precision = 'date_marker', 'date'
        elif PARTIAL_DATE.fullmatch(text):
            kind, precision = 'date_marker', 'month_and_day'
        elif TIME.fullmatch(text):
            kind, precision = 'time_marker', 'time_only'
        elif FILE_NAME.fullmatch(text):
            kind, precision = 'attachment_name', 'unknown'
        elif 0.035 <= y <= 0.14 and x < 0.6 and x + width > 0.4:
            kind, precision = 'talk_title_candidate', 'unknown'
        else:
            kind, precision = 'text', 'unknown'
        result.append({'order': order, 'text': text, 'kind': kind, 'side': side, 'speaker_role': 'unknown',
                       'time_precision': precision, 'visible_date': observed_date,
                       'bounds': [x, y, width, height]})
    return result


def file_fingerprints(selected_paths):
    fingerprints = {}
    for identifier, path in selected_paths.items():
        digest = hashlib.sha256()
        with Path(path).open('rb') as stream:
            size = 0
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
                size += len(chunk)
        fingerprints[identifier] = {'sha256': digest.hexdigest(), 'bytes': size}
    return fingerprints


def add_image_evidence(report, layouts):
    attachments = report['attachments']
    images = []
    for image in attachments:
        if image['role'] != 'unconfirmed' or image['file_id'] not in layouts:
            continue
        lines = screenshot_lines(layouts[image['file_id']])
        images.append({'receipt_id': image['receipt_id'], 'file_id': image['file_id'],
                       'method': 'local_image_ocr', 'status': 'unconfirmed', 'lines': lines})
        for line in lines:
            if line['kind'] == 'attachment_name':
                matches = [a['receipt_id'] for a in attachments if a['role'] == 'received_file' and a['file_id']
                           and unicodedata.normalize('NFC', a['name']) == line['text']]
                line['candidate_receipt_ids'] = matches
    return images
