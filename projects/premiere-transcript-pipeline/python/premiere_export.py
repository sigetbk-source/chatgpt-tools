#!/usr/bin/env python3
"""Export an independent master transcript; never reads or writes source media."""
import argparse
import json
import math
from pathlib import Path
import uuid


LANGUAGES = set('en-us en-gb zh-hk cmn-hans cmn-hant es-es de-de fr-fr ja-jp pt-pt pt-br ko-kr it-it ru-ru hi-in nb-no sv-se nl-nl da-dk id-id th-th vi-vn ms-my tr-tr pl-pl fil-ph te-in ml-in pa-in ??-??'.split())


def number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(label + ' must be a finite nonnegative number')
    return value


def nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + ' must be a nonempty string')
    return value


def convert(master):
    if master.get('schema') != 'bk-master-transcript/v0.1':
        raise ValueError('unsupported master schema')
    nonempty(master.get('source_id'), 'source_id')
    language = master.get('language')
    if language not in LANGUAGES:
        raise ValueError('unsupported language')
    speakers = master.get('speakers')
    utterances = master.get('utterances')
    if not isinstance(speakers, list) or not speakers or not isinstance(utterances, list) or not utterances:
        raise ValueError('speakers and utterances must be nonempty arrays')
    ids = {}
    output_speakers = []
    for speaker in speakers:
        key = nonempty(speaker.get('key'), 'speaker key')
        if key in ids:
            raise ValueError('duplicate speaker key')
        ids[key] = str(uuid.uuid4())
        output_speakers.append({'id': ids[key], 'name': nonempty(speaker.get('name'), 'speaker name')})
    segments = []
    previous_start = -1
    for utterance in utterances:
        key = utterance.get('speaker')
        if key not in ids:
            raise ValueError('unknown speaker')
        words = utterance.get('words')
        if not isinstance(words, list) or not words:
            raise ValueError('words must be a nonempty array')
        exported = []
        previous_word_start = -1
        for word in words:
            start = number(word.get('start'), 'word start')
            end = number(word.get('end'), 'word end')
            if end < start or start < previous_word_start:
                raise ValueError('invalid word timing order')
            confidence = number(word.get('confidence', 0.0), 'confidence')
            if confidence > 1:
                raise ValueError('confidence exceeds 1')
            eos = word.get('eos', False)
            if not isinstance(eos, bool):
                raise ValueError('eos must be boolean')
            kind = word.get('type', 'word')
            if kind not in ('word', 'punctuation'):
                raise ValueError('unsupported word type')
            # Internal annotations stay in master; only Adobe tags are exported.
            tags = word.get('tags', [])
            if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
                raise ValueError('tags must be strings')
            exported.append({'text': nonempty(word.get('text'), 'word text'),
                             'start': start, 'duration': end - start,
                             'confidence': confidence, 'eos': eos,
                             'tags': [tag for tag in tags if tag in ('profanity', 'filler')], 'type': kind})
            previous_word_start = start
        start = words[0]['start']
        if start < previous_start:
            raise ValueError('utterances must be ordered by start')
        segments.append({'start': start, 'duration': max(w['end'] for w in words) - start,
                         'language': language, 'speaker': ids[key], 'words': exported})
        previous_start = start
    return {'language': language, 'speakers': output_speakers, 'segments': segments}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('master', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = convert(json.loads(args.master.read_text(encoding='utf-8')))
    # Exclusive creation prevents overwriting a master, media file, or prior result.
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


if __name__ == '__main__':
    main()
