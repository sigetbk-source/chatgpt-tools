#!/usr/bin/env python3
"""Conservative, lossless SRT text layout. Widths are display units, not renderer pixels."""
import argparse
import html
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path


class LayoutError(ValueError):
    pass


@dataclass(frozen=True)
class LayoutLimits:
    source_width: int = 44
    translation_width: int = 44
    source_lines: int = 2
    translation_lines: int = 2

    def __post_init__(self):
        if min(self.source_width, self.translation_width, self.source_lines, self.translation_lines) < 1:
            raise ValueError('SRT layout limits must be positive')


def clusters(value):
    """Keep combining marks, variation selectors, joined emoji and flag pairs together."""
    result = []
    for char in value:
        code = ord(char)
        attach = (unicodedata.combining(char) or unicodedata.category(char) in {'Mn', 'Mc', 'Me'}
                  or 0xfe00 <= code <= 0xfe0f or 0x1f3fb <= code <= 0x1f3ff
                  or char == '\u200d' or (result and result[-1].endswith('\u200d'))
                  or (0x1f1e6 <= code <= 0x1f1ff and result
                      and len(result[-1]) == 1 and 0x1f1e6 <= ord(result[-1]) <= 0x1f1ff))
        if attach and result:
            result[-1] += char
        else:
            result.append(char)
    return result


def display_width(value):
    total = 0
    for cluster in clusters(value):
        base = next((char for char in cluster if not unicodedata.combining(char)
                     and unicodedata.category(char) not in {'Mn', 'Mc', 'Me', 'Cf'}), '')
        if base:
            total += 2 if unicodedata.east_asian_width(base) in {'F', 'W'} else 1
    return total


def content_key(value):
    return ''.join(char for char in value if not char.isspace())


def _wrap_source(value, width, line_count):
    words = value.split()
    if not words:
        raise LayoutError('source text is empty')
    if any(display_width(word) > width for word in words):
        raise LayoutError('source has a word wider than the configured line width; review the wording or limit')
    lines = []
    for word in words:
        proposed = f'{lines[-1]} {word}' if lines else word
        if lines and display_width(proposed) <= width:
            lines[-1] = proposed
        else:
            lines.append(word)
    if len(lines) > line_count:
        raise LayoutError(f'source requires {len(lines)} lines; provide reviewed paired SRT pages')
    return lines


def _wrap_japanese(value, width, line_count):
    chars = []
    for cluster in clusters(value):
        if chars and cluster.isascii() and cluster.isalnum() and chars[-1].isascii() and chars[-1].isalnum():
            chars[-1] += cluster
        else:
            chars.append(cluster)
    if not chars or not content_key(value):
        raise LayoutError('字幕の本文が空です')
    lines, current = [], ''
    for cluster in chars:
        if cluster in {'\n', '\r'}:
            if current:
                stripped = current.rstrip()
                if stripped: lines.append(stripped)
                current = ''
            continue
        if display_width(cluster) > width:
            raise LayoutError('字幕に1行の幅を超える文字列があります')
        if current and display_width(current + cluster) > width:
            stripped = current.rstrip()
            if stripped: lines.append(stripped)
            current = cluster.lstrip()
        else:
            current += cluster
    if current:
        stripped = current.rstrip()
        if stripped: lines.append(stripped)
    if len(lines) > line_count:
        raise LayoutError(f'字幕に{len(lines)}行必要です。確認済みの原文・訳文ペア分割を指定してください')
    return lines


def render_bilingual_cue(source, translation=None, limits=LayoutLimits()):
    original_source = source
    cjk_source = translation is None and any('\u3040' <= c <= '\u9fff' for c in source)
    source_lines = (_wrap_japanese(source, limits.translation_width, limits.source_lines + limits.translation_lines) if cjk_source
                    else _wrap_source(source, limits.source_width, limits.source_lines))
    if content_key(''.join(source_lines)) != content_key(original_source):
        raise LayoutError('source text changed during layout')
    if translation is None:
        return '\n'.join(html.escape(line, quote=False) for line in source_lines)
    translation_lines = _wrap_japanese(translation, limits.translation_width, limits.translation_lines)
    if content_key(''.join(translation_lines)) != content_key(translation):
        raise LayoutError('translation text changed during layout')
    return '\n'.join(f'<i>{html.escape(line, quote=False)}</i>' for line in source_lines) + '\n\u200b\n' + '\n'.join(
        html.escape(line, quote=False) for line in translation_lines)


def checked_time(start, end):
    import math
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (start, end)) or start < 0 or end <= start:
        raise LayoutError('invalid SRT cue time range')


def reviewed_pages(pages, source, translation, start, end, limits=LayoutLimits()):
    if not isinstance(pages, list) or not pages:
        raise LayoutError('reviewed SRT pages must be a nonempty list')
    output, last = [], start
    for page in pages:
        required = ('source', 'start', 'end') if translation is None else ('source', 'translation', 'start', 'end')
        if not isinstance(page, dict) or not all(key in page for key in required):
            raise LayoutError('reviewed SRT page lacks source, translation or time')
        p_start, p_end = page['start'], page['end']
        checked_time(p_start, p_end)
        if p_start < last or p_end > end:
            raise LayoutError('reviewed SRT page time is outside or overlaps its utterance')
        if translation is None and page.get('translation') not in (None, ''):
            raise LayoutError('single-language SRT page cannot add translation')
        output.append((p_start, p_end, render_bilingual_cue(page['source'], page.get('translation') if translation is not None else None, limits)))
        last = p_end
    if content_key(''.join(p['source'] for p in pages)) != content_key(source):
        raise LayoutError('reviewed SRT pages do not preserve the full source text in order')
    if translation is not None and content_key(''.join(p['translation'] for p in pages)) != content_key(translation):
        raise LayoutError('reviewed SRT pages do not preserve the full translation text in order')
    return output


TIME = re.compile(r'^(\d{2,}:\d{2}:\d{2},\d{3}) --> (\d{2,}:\d{2}:\d{2},\d{3})$')


def _milliseconds(tc):
    hours, minutes, rest = tc.split(':'); seconds, millis = rest.split(',')
    if int(minutes) >= 60 or int(seconds) >= 60:
        raise LayoutError(f'invalid SRT time: {tc}')
    return ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(millis)


def reflow_paired_srt(value, limits=LayoutLimits()):
    """Reflow existing, already paired cues without changing indices or timecodes."""
    result, last_end = [], -1
    normalized = value.removeprefix('\ufeff').replace('\r\n', '\n').strip()
    for block in re.split(r'\n\s*\n', normalized):
        rows = block.split('\n')
        if len(rows) < 3 or not rows[0].isdigit():
            raise LayoutError('invalid SRT cue structure')
        match = TIME.fullmatch(rows[1])
        if not match:
            raise LayoutError(f'cue {rows[0]} has an invalid time range')
        start, end = (_milliseconds(part) for part in match.groups())
        if end <= start or start < last_end:
            raise LayoutError(f'cue {rows[0]} has invalid or overlapping times')
        last_end = end
        body = '\n'.join(rows[2:])
        try:
            if body == '\u200b':
                laid_out = body  # Existing invisible timeline anchor.
            elif '<i>' in body:
                lines = body.split('\n')
                if '\u200b' not in lines or lines.count('\u200b') != 1:
                    raise LayoutError('invalid bilingual markup')
                spacer = lines.index('\u200b'); originals, translations = lines[:spacer], lines[spacer + 1:]
                if not originals or not translations or any(not re.fullmatch(r'<i>.*</i>', line) for line in originals):
                    raise LayoutError('invalid bilingual markup')
                source = ' '.join(html.unescape(line[3:-4]) for line in originals)
                translation = ''
                for line in translations:
                    segment = html.unescape(line)
                    if translation and segment and translation[-1].isascii() and translation[-1].isalnum() and segment[0].isascii() and segment[0].isalnum():
                        translation += ' '
                    translation += segment
                laid_out = render_bilingual_cue(source, translation, limits)
            else:
                if '<' in body and re.search(r'<[^>]*>', body):
                    raise LayoutError('unsupported markup')
                laid_out = render_bilingual_cue(html.unescape(body), limits=limits)
        except LayoutError as error:
            raise LayoutError(f'cue {rows[0]}: {error}') from error
        result.append('\n'.join((rows[0], rows[1], laid_out)))
    return '\n\n'.join(result) + '\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Reflow and validate an existing paired SRT; times stay unchanged.')
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-width', type=int, default=44)
    parser.add_argument('--translation-width', type=int, default=44)
    args = parser.parse_args()
    try:
        original = args.input.read_bytes()
        has_bom = original.startswith(b'\xef\xbb\xbf')
        has_crlf = b'\r\n' in original
        output = reflow_paired_srt(original.decode('utf-8-sig'),
                                   LayoutLimits(args.source_width, args.translation_width))
        if args.input.resolve() == args.output.resolve():
            raise LayoutError('input and output must be separate files')
        encoded = output.replace('\n', '\r\n') if has_crlf else output
        args.output.write_bytes(encoded.encode('utf-8-sig' if has_bom else 'utf-8'))
    except (LayoutError, ValueError) as error:
        parser.exit(2, f'SRT layout error: {error}\n')
