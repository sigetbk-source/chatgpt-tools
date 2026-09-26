"""Merge local Whisper words and diarization ranges into the canonical master.

assign_word is reused from premiere-speaker-repair/independent_input.py.
No model downloads, media writes or Premiere access occur here.
"""
import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from premiere_export import convert, number, nonempty


def interval(item):
    start, end = number(item['start'], 'start'), number(item['end'], 'end')
    if end < start:
        raise ValueError('end before start')
    return start, end


def assign_word(word, turns):
    start, end = interval(word)
    matches = [(t['speaker'], max(start, t['start']), min(end, t['end'])) for t in turns
               if min(end, t['end']) > max(start, t['start'])]
    spans = defaultdict(list)
    for speaker, left, right in matches:
        spans[speaker].append((left, right))
    scores = {}
    # Union duplicate turns per speaker so duplicates cannot inflate the score.
    for speaker, intervals in spans.items():
        total, edge = 0.0, -1.0
        for left, right in sorted(intervals):
            total += max(0.0, right - max(left, edge))
            edge = max(edge, right)
        scores[speaker] = total
    ranking = sorted(scores, key=lambda key: (-scores[key], key))
    overlap = any(a != b and min(ar, br) > max(al, bl)
                  for i, (a, al, ar) in enumerate(matches) for b, bl, br in matches[i + 1:])
    return {'speaker': ranking[0] if ranking else 'unknown', 'overlap': overlap,
            'ambiguous': len(ranking) > 1 and math.isclose(scores[ranking[0]], scores[ranking[1]]),
            'speaker_overlap_seconds': scores}


def build_master(whisper, diarization, source_id, language, *, validate_export=True):
    turns = []
    for item in diarization['segments']:
        start, end = interval(item)
        label = nonempty(item.get('speaker'), 'speaker')
        if label == 'unknown' or start == end:
            raise ValueError('reserved speaker or empty diarization range')
        turns.append({'start': start, 'end': end, 'speaker': label})
    speakers, utterances = {}, []
    previous_start = -1
    counts = {'unknown': 0, 'overlap': 0, 'ambiguous': 0, 'words': 0}
    for source_index, source in enumerate(whisper['segments']):
        words = source.get('words', [])
        if source.get('text', '').strip() and not words:
            raise ValueError('segment text without word timestamps')
        current = None
        for raw in words:
            start, end = interval(raw)
            if start < previous_start:
                raise ValueError('words must be ordered by start')
            previous_start = start
            text = nonempty(raw.get('word', raw.get('text')), 'word text')
            assignment = assign_word(raw, turns)
            # Equal evidence is not a verified identity. Keep candidates for review.
            label = 'unknown' if assignment['ambiguous'] else assignment['speaker']
            if label not in speakers:
                speakers[label] = '不明' if label == 'unknown' else '自動話者' + str(1 + sum(k != 'unknown' for k in speakers))
            if current is None or current['speaker'] != label:
                current = {'speaker': label, 'words': []}
                utterances.append(current)
            confidence = number(raw.get('probability', 0.0), 'probability')
            if confidence > 1:
                raise ValueError('probability exceeds 1')
            current['words'].append({'text': text, 'start': start, 'end': end,
                'confidence': confidence, 'eos': False,
                'diarization': {**assignment, 'assigned_speaker': label,
                    'source_segment_index': source_index, 'identity_verified': False}})
            counts['words'] += 1
            counts['unknown'] += label == 'unknown'
            counts['overlap'] += assignment['overlap']
            counts['ambiguous'] += assignment['ambiguous']
        if current:
            current['words'][-1]['eos'] = True
    master = {'schema': 'bk-master-transcript/v0.1', 'source_id': source_id,
        'language': language, 'speakers': [{'key': k, 'name': v} for k, v in speakers.items()],
        'utterances': utterances, 'provenance': {
            'method': 'maximum_word_time_overlap', 'speaker_identity_verified': False,
            'diarization_metadata': diarization.get('metadata', {}), 'counts': counts}}
    if validate_export:
        convert(master)
    return master


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--whisper', type=Path, required=True)
    parser.add_argument('--diarization', type=Path, required=True)
    parser.add_argument('--source-id', required=True)
    parser.add_argument('--language', required=True, help='Premiere language code, e.g. ja-jp')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    inputs = [p.read_bytes() for p in (args.whisper, args.diarization)]
    master = build_master(*(json.loads(b) for b in inputs), args.source_id, args.language)
    master['provenance']['input_sha256'] = dict(zip(('whisper', 'diarization'),
        (hashlib.sha256(b).hexdigest() for b in inputs)))
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(master, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps(master['provenance']['counts']))


if __name__ == '__main__':
    main()
