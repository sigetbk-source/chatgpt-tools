"""Turn local screenshot OCR positions and file hashes into review evidence."""

import datetime as dt
import hashlib
import json
import re
import subprocess
import unicodedata
from collections import Counter
from decimal import Decimal
from pathlib import Path

from decide import dates


TIME = re.compile(r'(?:[01]?\d|2[0-3]):[0-5]\d')
MERIDIEM = re.compile(r'(午前|午後)\s*(1[0-2]|[1-9]):([0-5]\d)')
RELATIVE_DAYS = {'今日': 0, '昨日': 1, '一昨日': 2, 'おととい': 2}
PARTIAL_DATE = re.compile(r'\d{1,2}月\s*\d{1,2}日(?:[（(][月火水木金土日](?:曜(?:日)?)?[）)])?')
FULL_DATE = re.compile(r'(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{4}年\s*\d{1,2}月\s*\d{1,2}日)')
FILE_NAME = re.compile(r'[^/\\\r\n]+\.(?:docx?|xlsx?|pdf|pptx?|zip|mp4|mov|wav|mp3|jpe?g|png)', re.I)
FILE_SIZE = re.compile(r'(\d{1,10}(?:\.\d{1,3})?)\s*(B|bytes?|[KMGT]i?B|バイト)', re.I)


def display_size_ranges(label):
    value, unit = label.groups()
    number = Decimal(value)
    unit = unit.upper()
    places = max(0, -number.as_tuple().exponent)
    if unit in {'B', 'BYTE', 'BYTES', 'バイト'}:
        return [(number, number)] if number == number.to_integral_value() else []
    power = 'KMGT'.index(unit[0]) + 1
    step = Decimal(1).scaleb(-places)
    bases = (1024,) if 'I' in unit else (1000, 1024)
    return [(max(0, (number - step) * base ** power), (number + step) * base ** power) for base in bases]


def attachment_size_below(line, next_line):
    if not line['conversation_region_known'] or next_line is None:
        return None
    x, y, _, height = line['bounds']
    nx, ny, _, nh = next_line['bounds']
    if abs(nx - x) > 0.018 or not -0.006 <= ny - y - height <= max(height, nh):
        return None
    return FILE_SIZE.fullmatch(unicodedata.normalize('NFKC', next_line['text']))


def normalize_clock(text):
    if TIME.fullmatch(text):
        hour, minute = text.split(':')
        return f'{int(hour):02d}:{minute}'
    part = MERIDIEM.fullmatch(text)
    if part:
        prefix, hour, minute = part.groups()
        return f'{int(hour) % 12 + (12 if prefix == "午後" else 0):02d}:{minute}'
    return None


def normalized_box(box):
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise ValueError('invalid screenshot bounds')
    if any(type(value) not in (float, int) or not 0 <= value <= 1 for value in box):
        raise ValueError('invalid screenshot bounds')
    x, y, width, height = box
    if not width or not height or x + width > 1.001 or y + height > 1.001:
        raise ValueError('invalid screenshot bounds')
    return x, y, width, height


def desktop_conversation(lines):
    search = [line for line in lines if 'トークルームとメッセージ検索' in line[4]]
    composer = [line for line in lines if line[4] == 'メッセージを入力']
    if len(search) != 1 or len(composer) != 1:
        return None
    sy, sx, sw, sh, _ = search[0]
    cy, cx, _, _, _ = composer[0]
    if not (sx + sw < cx and sy < 0.3 and cy > 0.55 and cy - sy > 0.3):
        return None
    left = max(sx + sw, cx - 0.025)
    top = sy + sh + 0.004
    return left, top, 1 - left, cy - top


def screenshot_lines(layout):
    region = None
    captured_date = None
    if isinstance(layout, dict):
        if layout.get('region') is not None:
            region = normalized_box(layout['region'])
        if layout.get('captured_at') is not None:
            value = layout['captured_at']
            if not isinstance(value, str):
                raise ValueError('capture time requires an ISO date and timezone')
            captured = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
            if captured.utcoffset() is None:
                raise ValueError('capture time requires a timezone')
            captured_date = captured.date()
        layout = layout.get('lines')
    if not isinstance(layout, list) or not layout or len(layout) > 1000:
        raise ValueError('invalid screenshot OCR lines')
    lines = []
    for item in layout:
        if not isinstance(item, dict) or not isinstance(item.get('text'), str):
            raise ValueError('invalid screenshot OCR observation')
        x, y, width, height = normalized_box([item.get(key) for key in ('x', 'y', 'width', 'height')])
        text = unicodedata.normalize('NFC', item['text']).strip()
        if text:
            lines.append((y, x, width, height, text))
    region = region or desktop_conversation(lines)
    if region:
        left, top, span, tall = region
        lines = [line for line in lines if line[1] >= left and line[1] + line[2] <= left + span + 0.001
                 and line[0] >= top and line[0] + line[3] <= top + tall + 0.001]
    result = []
    for y, x, width, height, text in sorted(lines):
        marker = unicodedata.normalize('NFKC', text)
        clock = normalize_clock(marker)
        if region:
            near_left = x - left <= 0.17 * span
            near_right = left + span - x - width <= 0.15 * span
            side = 'left' if near_left and not near_right else 'right' if near_right and not near_left else 'unknown'
        else:
            side = 'left' if x + width <= 0.48 else 'right' if x >= 0.52 else 'unknown'
        observed_date = next(dates(marker), None) if FULL_DATE.fullmatch(marker) else None
        if observed_date:
            kind, precision = 'date_marker', 'date'
        elif marker in RELATIVE_DAYS and (not region or side == 'unknown'):
            kind, precision = 'date_marker', 'relative_day'
            if captured_date:
                observed_date = (captured_date - dt.timedelta(days=RELATIVE_DAYS[marker])).isoformat()
        elif PARTIAL_DATE.fullmatch(marker):
            kind, precision = 'date_marker', 'month_and_day'
        elif clock:
            kind, precision = 'time_marker', 'time_only'
        elif region and text in {'既読', '↓'}:
            continue
        elif FILE_NAME.fullmatch(text):
            kind, precision = 'attachment_name', 'unknown'
        elif not region and 0.035 <= y <= 0.14 and x < 0.6 and x + width > 0.4:
            kind, precision = 'talk_title_candidate', 'unknown'
        else:
            kind, precision = 'text', 'unknown'
        result.append({'order': len(result) + 1, 'text': text, 'kind': kind, 'side': side, 'speaker_role': 'unknown',
                       'time_precision': precision, 'visible_date': observed_date,
                       'visible_time': clock, 'conversation_region_known': bool(region),
                       'bounds': [x, y, width, height]})
    return result


def conversation_messages(lines):
    if not lines or not lines[0]['conversation_region_known']:
        return []
    groups = []
    day = None
    for line in lines:
        if line['kind'] == 'date_marker':
            day = line
        elif line['kind'] in {'text', 'attachment_name'}:
            previous = groups[-1][1][-1] if groups else None
            x, y, _, height = line['bounds']
            if previous and groups[-1][0] is day and line['side'] == previous['side'] != 'unknown':
                px, py, _, ph = previous['bounds']
                if abs(x - px) <= 0.012 and -0.006 <= y - py - ph <= 0.25 * min(ph, height):
                    groups[-1][1].append(line)
                    continue
            groups.append((day, [line]))

    clocks = [line for line in lines if line['kind'] == 'time_marker']
    nearby = []
    for _, message in groups:
        tail = message[-1]
        x, y, width, height = tail['bounds']
        candidates = []
        if tail['side'] != 'unknown':
            for clock in clocks:
                tx, ty, tw, th = clock['bounds']
                gap = tx - x - width if tail['side'] == 'left' else x - tx - tw
                if -0.01 <= gap <= 0.12 and abs(ty + th / 2 - y - height / 2) <= max(height, th):
                    candidates.append(clock)
        nearby.append(candidates)
    claims = Counter(clock['order'] for candidates in nearby for clock in candidates)
    messages = []
    for (day, message), candidates in zip(groups, nearby):
        clock = candidates[0] if len(candidates) == 1 and claims[candidates[0]['order']] == 1 else None
        messages.append({'text': '\n'.join(line['text'] for line in message), 'side': message[0]['side'],
                         'speaker_role': 'unknown', 'status': 'unconfirmed',
                         'date_label': day['text'] if day else None, 'visible_date': day['visible_date'] if day else None,
                         'visible_time': clock['visible_time'] if clock else None,
                         'line_orders': [line['order'] for line in message],
                         'date_line_order': day['order'] if day else None,
                         'clock_line_order': clock['order'] if clock else None})
    return messages


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


def recognize_screenshot(path, executable):
    try:
        result = subprocess.run([str(executable.resolve()), '--layout', str(Path(path).resolve())],
                                capture_output=True, text=True, check=True, timeout=45)
        layout = json.loads(result.stdout)
        screenshot_lines(layout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'error': 'local_ocr_unavailable'}
    return layout


def add_image_evidence(report, layouts):
    attachments = report['attachments']
    images = []
    for image in attachments:
        if image['role'] != 'unconfirmed' or image['file_id'] not in layouts:
            continue
        layout = layouts[image['file_id']]
        if isinstance(layout, dict) and layout.get('error') == 'local_ocr_unavailable':
            images.append({'receipt_id': image['receipt_id'], 'file_id': image['file_id'],
                           'method': 'local_image_ocr', 'status': 'unavailable', 'lines': [], 'messages': []})
            continue
        lines = screenshot_lines(layout)
        for index, line in enumerate(lines):
            if line['kind'] == 'attachment_name':
                matches = [a for a in attachments if a['role'] == 'received_file' and a['file_id']
                           and unicodedata.normalize('NFC', a['name']) == line['text']]
                size_line = lines[index + 1] if index + 1 < len(lines) else None
                size = attachment_size_below(line, size_line)
                line['candidate_receipt_ids'] = [a['receipt_id'] for a in matches]
                if size:
                    ranges = display_size_ranges(size)
                    size_line['kind'] = 'attachment_size'
                    line['display_size_line_order'] = size_line['order']
                    line['size_comparisons'] = []
                    for match in matches:
                        actual = match.get('local_file_fingerprint', {}).get('bytes')
                        state = 'unknown' if actual is None or not ranges else (
                            'consistent' if any(lo <= actual <= hi for lo, hi in ranges) else 'different')
                        line['size_comparisons'].append({'receipt_id': match['receipt_id'], 'status': state})
        images.append({'receipt_id': image['receipt_id'], 'file_id': image['file_id'],
                       'method': 'local_image_ocr', 'status': 'unconfirmed', 'lines': lines,
                       'messages': conversation_messages(lines)})
    return images
