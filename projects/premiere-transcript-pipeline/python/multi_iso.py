"""Known-speaker ISO transcription and conservative, evidence-backed merge.

The source files are read only. Every provider submission has a separate UUID
directory, and raw provider responses are never rewritten by this module.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import math
import re
import struct
import time
import uuid
from copy import deepcopy
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.fft import dct
from scipy.signal import resample_poly

from workflow_backend import extract_reference, media_identity, reference_terms, transcribe_assemblyai

DEFAULTS = {
    'strong_db': 8.0, 'ambiguous_db': 3.0, 'window_seconds': 0.2,
    'overlap_max_margin_db': 6.0, 'active_dbfs': -35.0,
    'weights': {'iso_prior': 0.27, 'volume': 0.48, 'aai_confidence': 0.08, 'frequency': 0.17},
    'duplicate_time_fraction': 0.55, 'duplicate_text_similarity': 0.72,
    'echo_volume_margin_db': 3.0, 'echo_text_similarity': 0.8, 'echo_min_characters': 12,
}


def bwf_time_reference(path):
    """Return the BWF sample reference without loading a potentially huge WAV."""
    with Path(path).open('rb') as stream:
        if stream.read(4) != b'RIFF':
            raise ValueError('BWF source must be a RIFF WAV')
        stream.seek(12)
        while True:
            header = stream.read(8)
            if len(header) != 8:
                raise ValueError('BWF bext time reference not found')
            tag, size = struct.unpack('<4sI', header)
            if tag == b'bext':
                if size < 346:
                    raise ValueError('BWF bext chunk is too short')
                stream.seek(338, 1)
                return struct.unpack('<Q', stream.read(8))[0]
            stream.seek(size + size % 2, 1)


def tc_seconds(value):
    h, m, s = value.split(':')
    return int(h) * 3600 + int(m) * 60 + float(s)


def prepare_clips(sources, start_tc, duration, output):
    """Cut the same absolute BWF sample interval from all three sources."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    observed = []
    for item in sources:
        path = Path(item['path']).resolve(strict=True)
        info = sf.info(path)
        if info.channels != 1:
            raise ValueError(f'expected mono ISO: {path.name}')
        reference = bwf_time_reference(path)
        absolute_start = tc_seconds(start_tc)
        offset = round(absolute_start * info.samplerate - reference)
        frames = round(duration * info.samplerate)
        if offset < 0 or offset + frames > info.frames:
            raise ValueError(f'interval exceeds {path.name}')
        observed.append((info.samplerate, reference, offset, frames))
    if len(set(observed)) != 1:
        raise ValueError('ISO sample rates, BWF references, or sample offsets differ')
    sample_rate, reference, offset, frames = observed[0]
    manifest = {'start_tc': start_tc, 'duration_seconds': duration, 'source_sample_rate': sample_rate,
                'bwf_start_reference_samples': reference, 'offset_samples': offset,
                'frames': frames, 'sources': []}
    for item in sources:
        path = Path(item['path']).resolve(strict=True)
        identity = media_identity(path)
        with sf.SoundFile(path) as stream:
            stream.seek(offset)
            pcm = stream.read(frames, dtype='float32')
        if len(pcm) != frames:
            raise ValueError(f'short read from {path.name}')
        clip = output / (item['speaker_id'] + '.wav')
        # 16 kHz PCM is accepted by AAI and can be played by the browser.
        audio = resample_poly(pcm, 1, 3) if sample_rate == 48000 else pcm
        output_rate = 16000 if sample_rate == 48000 else sample_rate
        sf.write(clip, audio, output_rate, subtype='PCM_16')
        manifest['sources'].append({'speaker_id': item['speaker_id'], 'speaker_name': item['speaker_name'],
                                    'source_iso': str(path), 'source_identity': identity,
                                    'clip': str(clip), 'clip_identity': media_identity(clip)})
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return manifest


def frame_levels(audio, sr, window_seconds=0.2):
    size = max(1, round(sr * window_seconds))
    count = math.ceil(len(audio) / size)
    padded = np.pad(np.asarray(audio, dtype=np.float64), (0, count * size - len(audio)))
    frames = padded.reshape(count, size)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    peak = np.max(np.abs(frames), axis=1)
    dbfs = 20 * np.log10(np.maximum(rms, 1e-9))
    return {'rms': rms, 'peak': peak, 'dbfs': dbfs, 'window_seconds': window_seconds}


def interval_levels(levels, start, end):
    size = levels['window_seconds']
    lo = max(0, int(start / size))
    hi = min(len(levels['rms']), max(lo + 1, math.ceil(end / size)))
    if hi <= lo:
        return {'rms': 0.0, 'dbfs': -180.0, 'peak': 0.0}
    rms = float(np.sqrt(np.mean(levels['rms'][lo:hi] ** 2)))
    return {'rms': rms, 'dbfs': float(20 * math.log10(max(rms, 1e-9))),
            'peak': float(np.max(levels['peak'][lo:hi]))}


def volume_evidence(levels_by_iso, start, end, config=DEFAULTS):
    measured = {key: interval_levels(value, start, end) for key, value in levels_by_iso.items()}
    ranked = sorted(measured, key=lambda key: measured[key]['dbfs'], reverse=True)
    margin = measured[ranked[0]]['dbfs'] - measured[ranked[1]]['dbfs']
    strength = 'strong' if margin >= config['strong_db'] else 'medium' if margin >= config['ambiguous_db'] else 'ambiguous'
    active = [key for key in ranked if measured[key]['dbfs'] >= config['active_dbfs'] and
              measured[ranked[0]]['dbfs'] - measured[key]['dbfs'] <= config['overlap_max_margin_db']]
    # Similar levels alone cannot prove two people spoke. Mark only a candidate.
    return {'per_iso': measured, 'ranked': ranked, 'primary_iso': ranked[0], 'margin_db': round(margin, 3),
            'strength': strength, 'overlap_candidate': len(active) >= 2, 'active_isos': active}


def spectral_features(audio, sr):
    """Lightweight spectral summary and 13 MFCCs; never used as voice ID."""
    x = np.asarray(audio, dtype=np.float64)
    if len(x) < 256:
        return None
    n = 1024
    hop = 512
    if len(x) < n:
        x = np.pad(x, (0, n - len(x)))
    frames = np.lib.stride_tricks.sliding_window_view(x, n)[::hop]
    frames = frames[:500]
    power = np.abs(np.fft.rfft(frames * np.hanning(n), axis=1)) ** 2
    freq = np.fft.rfftfreq(n, 1 / sr)
    weights = power / np.maximum(power.sum(axis=1, keepdims=True), 1e-12)
    centroid = (weights * freq).sum(axis=1)
    bandwidth = np.sqrt((weights * (freq - centroid[:, None]) ** 2).sum(axis=1))
    rolloff = freq[np.minimum(np.argmax(np.cumsum(weights, axis=1) >= 0.85, axis=1), len(freq) - 1)]
    zcr = np.mean(np.signbit(frames[:, 1:]) != np.signbit(frames[:, :-1]), axis=1)
    mel = 2595 * np.log10(1 + freq / 700)
    edges = np.linspace(mel[0], mel[-1], 28)
    bands = []
    for left, mid, right in zip(edges[:-2], edges[1:-1], edges[2:]):
        triangle = np.maximum(0, np.minimum((mel - left) / max(mid - left, 1e-8),
                                         (right - mel) / max(right - mid, 1e-8)))
        bands.append(triangle)
    mel_energy = power @ np.asarray(bands).T
    mfcc = dct(np.log(np.maximum(mel_energy, 1e-12)), axis=1, norm='ortho')[:, :13].mean(axis=0)
    return {'spectral_centroid_hz': float(np.median(centroid)),
            'spectral_bandwidth_hz': float(np.median(bandwidth)),
            'spectral_rolloff_hz': float(np.median(rolloff)),
            'zero_crossing_rate': float(np.median(zcr)),
            'mfcc': [float(v) for v in mfcc]}


def frequency_similarity(candidate, prototype):
    if candidate is None or prototype is None:
        return None
    a = np.asarray(candidate['mfcc'][1:]); b = np.asarray(prototype['mfcc'][1:])
    distance = np.linalg.norm(a - b) / max(1, np.linalg.norm(b))
    return float(max(0.0, min(1.0, math.exp(-3 * distance))))


def text_language(text, detected):
    if re.search(r'[\u3040-\u30ff\u3400-\u9fff]', text):
        return 'ja-jp'
    if re.search(r'[A-Za-z]', text):
        return 'en-us'
    return {'ja': 'ja-jp', 'en': 'en-us'}.get(detected, '??-??')


def displayed_words(words):
    # Match the existing review screen exactly, including its handling of
    # Japanese spacing; candidate.before must equal the editable UI text.
    from multilingual_review import display_word_texts
    return ''.join(display_word_texts({'words': words}))


def explicit_reference_candidates(text, documents):
    """Only explicit aliases, with provenance and whole-token Latin matching."""
    found = []
    seen = set()
    for document in documents:
        for line_number, line in enumerate(document['text'].splitlines(), 1):
            match = re.fullmatch(r'\s*(.{1,80}?)\s*→\s*(.{1,80}?)\s*', line)
            if not match:
                continue
            source, target = match.group(1).strip(), match.group(2).strip()
            if not source or not target or source == target:
                continue
            pattern = r'\s*'.join(re.escape(char) for char in source if not char.isspace())
            if source[0].isascii() and source[0].isalnum():
                pattern = r'(?<![A-Za-z0-9])' + pattern
            if source[-1].isascii() and source[-1].isalnum():
                pattern += r'(?![A-Za-z0-9])'
            hit = re.search(pattern, text, flags=re.IGNORECASE)
            if not hit:
                continue
            candidate = text[:hit.start()] + target + text[hit.end():]
            if candidate == text or candidate in seen:
                continue
            seen.add(candidate)
            found.append({'before': text, 'candidate': candidate, 'source_name': document['name'],
                          'source_path': document['path'], 'source_line': line_number,
                          'evidence': line.strip(), 'matched_term': hit.group(),
                          'basis': 'explicit_alias', 'adopted': False, 'status': 'candidate'})
    return found


def contextual_reference_candidates(text, documents, existing):
    """Offer conservative near matches from document vocabulary, never adopt.

    General vocabulary is allowed; the source line and similarity remain
    visible so the reviewer can reject semantic near matches.
    """
    found = []
    tokens = list(re.finditer(r'[A-Za-z][A-Za-z0-9.+&_-]{2,}|[ァ-ヶー]{3,}|[一-龯々]{3,}', text))
    for document in documents:
        for term in document['terms']:
            if term.get('from') is not None:
                continue
            target = term['to']
            if len(target) < 4 or len(target) > 40 or ' ' in target or not target[0].isalnum():
                continue
            for token_match in tokens:
                token = token_match.group()
                if token_match.start() and text[token_match.start() - 1].isascii() and text[token_match.start() - 1].isalnum():
                    continue
                following = re.match(r'\s+([A-Za-z]{1,8})\b', text[token_match.end():])
                if following and (token + following.group(1)).casefold() == target.casefold():
                    continue
                similarity = SequenceMatcher(None, token.casefold(), target.casefold()).ratio()
                if not .82 <= similarity < 1 or token == target:
                    continue
                pattern = r'(?<![A-Za-z0-9])' + re.escape(token) + r'(?![A-Za-z0-9])' if token.isascii() else re.escape(token)
                candidate = re.sub(pattern, target, text, count=1)
                if candidate == text or any(x['candidate'] == candidate for x in existing + found):
                    continue
                found.append({'before': text, 'candidate': candidate, 'source_name': document['name'],
                              'source_path': document['path'], 'source_line': term['line'],
                              'evidence': term['evidence'], 'matched_term': token,
                              'similarity': round(similarity, 3), 'basis': 'contextual_near_match',
                              'adopted': False, 'status': 'candidate'})
                if len(found) >= 3:
                    return found
    return found


def aai_utterances(job, speaker_id, source_iso):
    detected = job.get('language_code') or ''
    turns = job.get('utterances') or []
    words = job.get('words') or []
    if not turns and words:
        turns = [{'start': words[0]['start'], 'end': words[-1]['end'], 'text': job.get('text') or '',
                  'confidence': None, 'speaker': None, 'words': words}]
    result = []
    for index, turn in enumerate(turns):
        start, end = float(turn['start']) / 1000, float(turn['end']) / 1000
        inside = turn.get('words') or [w for w in words if min(w['end'], turn['end']) > max(w['start'], turn['start'])]
        normalized = [{'text': str(w.get('text', '')).strip(), 'start': float(w['start']) / 1000,
                       'end': float(w['end']) / 1000, 'confidence': float(w.get('confidence') or 0),
                       'eos': False} for w in inside if str(w.get('text', '')).strip()]
        for word_index in range(1, len(normalized)):
            previous = normalized[word_index - 1]['text']
            current = normalized[word_index]['text']
            if previous and current and previous[-1].isascii() and previous[-1].isalnum() and current[0].isascii() and current[0].isalnum():
                normalized[word_index]['text'] = ' ' + current
        text = str(turn.get('text') or ' '.join(w['text'] for w in normalized)).strip()
        if not text:
            continue
        if normalized:
            normalized[-1]['eos'] = True
        confidence = turn.get('confidence')
        if confidence is None and normalized:
            confidence = sum(w['confidence'] for w in normalized) / len(normalized)
        result.append({'id': f'{speaker_id}-{index:04d}', 'source_iso_speaker': speaker_id,
                       'source_iso': source_iso, 'start': start, 'end': end, 'text': text,
                       'raw_asr_text': text, 'provider_utterance_text': text, 'words': normalized,
                       'aai_speaker': turn.get('speaker'), 'aai_confidence': float(confidence or 0),
                       'detected_language': detected, 'language': text_language(text, detected)})
    return result


def refine_utterances(rows, levels, config=DEFAULTS):
    """Split provider turns at sustained acoustic speaker changes and long gaps.

    This does not relabel provider words. It only makes evidence and duplicate
    comparison local enough for turns that AAI merged across two speakers.
    """
    refined = []
    for row in rows:
        words = row['words']
        if len(words) < 2:
            refined.append(row)
            continue
        labels = []
        for word in words:
            center = (word['start'] + word['end']) / 2
            evidence = volume_evidence(levels, max(0, center - .1), center + .1, config)
            labels.append(evidence['primary_iso'] if evidence['strength'] == 'strong' else None)
        # One or two noisy windows are not a speaker change. Keep uncertain
        # windows with the surrounding source when both sides agree.
        stable = labels[:]
        for index in range(1, len(labels) - 1):
            if labels[index - 1] == labels[index + 1] and labels[index - 1] is not None:
                stable[index] = labels[index - 1]
        boundaries = [0]
        current = stable[0] or row['source_iso_speaker']
        current_language = None
        for index in range(1, len(words)):
            gap = words[index]['start'] - words[index - 1]['end']
            if gap >= .8:
                boundaries.append(index)
                current = stable[index] or current
                current_language = None
                continue
            token = words[index]['text'].strip()
            token_language = ('ja' if re.search(r'[\u3040-\u30ff\u3400-\u9fff]', token) else
                              'en' if re.search(r'[A-Za-z]', token) else None)
            if current_language is None and index > boundaries[-1]:
                earlier = [w['text'] for w in words[boundaries[-1]:index]]
                current_language = 'ja' if any(re.search(r'[\u3040-\u30ff\u3400-\u9fff]', t) for t in earlier) else 'en'
            if token_language and current_language and token_language != current_language:
                coming = words[index:min(len(words), index + 5)]
                agreement = sum(bool(re.search(r'[\u3040-\u30ff\u3400-\u9fff]' if token_language == 'ja' else r'[A-Za-z]',
                                               w['text'])) for w in coming)
                if len(coming) >= 4 and agreement >= 4 and index - boundaries[-1] >= 2:
                    boundaries.append(index)
                    current_language = token_language
                    current = stable[index] or current
                    continue
            proposed = stable[index]
            if proposed and proposed != current and index + 2 < len(words):
                next_labels = stable[index:index + 3]
                if next_labels.count(proposed) >= 2:
                    boundaries.append(index)
                    current = proposed
        boundaries.append(len(words))
        for part, (begin, end) in enumerate(zip(boundaries, boundaries[1:])):
            segment_words = deepcopy(words[begin:end])
            if not segment_words:
                continue
            segment_words[-1]['eos'] = True
            for word in segment_words[:-1]:
                word['eos'] = False
            text = displayed_words(segment_words)
            child = deepcopy(row)
            child.update({'id': f"{row['id']}-part{part:02d}", 'start': segment_words[0]['start'],
                          'end': segment_words[-1]['end'], 'text': text, 'raw_asr_text': text,
                          'words': segment_words, 'language': text_language(text, row['detected_language']),
                          'provider_utterance_id': row['id']})
            refined.append(child)
    return refined


def score_utterance(row, speaker_ids, levels, audio, sr, prototypes, config=DEFAULTS, pyannote_fallback=None):
    volume = volume_evidence(levels, row['start'], row['end'], config)
    source = row['source_iso_speaker']
    frequencies = {}
    for key in speaker_ids:
        segment = audio[key][round(row['start'] * sr):round(row['end'] * sr)]
        frequencies[key] = spectral_features(segment, sr)
    similarities = {key: frequency_similarity(frequencies[key], prototypes.get(key)) for key in speaker_ids}
    weights = config['weights']
    scores = {}
    for key in speaker_ids:
        relative = volume['per_iso'][key]['dbfs'] - volume['per_iso'][volume['ranked'][0]]['dbfs']
        volume_score = max(0.0, 1 + relative / 15)
        scores[key] = (weights['iso_prior'] * (1.0 if key == source else 0.0) +
                       weights['volume'] * volume_score +
                       weights['aai_confidence'] * (row['aai_confidence'] if key == source else 0.0) +
                       weights['frequency'] * (similarities[key] if similarities[key] is not None else 0.5))
    ranked = sorted(scores, key=scores.get, reverse=True)
    if volume['strength'] == 'strong' and ranked[0] != volume['primary_iso']:
        # AAI on a quiet ISO can pick up leakage; retain the score conflict.
        ranked.remove(volume['primary_iso'])
        ranked.insert(0, volume['primary_iso'])
    total = sum(scores.values()) or 1
    confidence = scores[ranked[0]] / total
    conflict = ranked[0] != source
    ambiguous = volume['strength'] == 'ambiguous' or scores[ranked[0]] - scores[ranked[1]] < 0.1 or conflict
    fallback_result = None
    if pyannote_fallback is not None and (ambiguous or volume['overlap_candidate']):
        fallback_result = pyannote_fallback(row, volume)
    result = deepcopy(row)
    result.update({'speaker': ranked[0], 'speaker_confidence': round(confidence, 4),
                   'speaker_scores': {key: round(value, 4) for key, value in scores.items()},
                   'ambiguous': ambiguous, 'overlap': volume['overlap_candidate'],
                   'speaker_evidence': {'source_iso': row['source_iso'], 'source_iso_speaker': source,
                       'volume': volume, 'aai_speaker': row['aai_speaker'],
                       'aai_confidence': row['aai_confidence'], 'detected_language': row['detected_language'],
                       'frequency_features': frequencies, 'frequency_similarity': similarities,
                       'aai_iso_conflict': conflict,
                       'pyannote_used': fallback_result is not None,
                       'pyannote_result': fallback_result}})
    return result


def duplicate_groups(rows, config=DEFAULTS):
    """Flag repeats; never discard candidates or distinct simultaneous speakers."""
    rows = deepcopy(rows)
    for row in rows:
        row['duplicate_candidates'] = []
        row['duplicate_primary'] = True
    for i, first in enumerate(rows):
        for second in rows[i + 1:]:
            if first['source_iso_speaker'] == second['source_iso_speaker']:
                continue
            overlap = max(0, min(first['end'], second['end']) - max(first['start'], second['start']))
            fraction = overlap / max(0.001, min(first['end'] - first['start'], second['end'] - second['start']))
            normalized = lambda value: re.sub(r'[^\w\u3040-\u30ff\u3400-\u9fff]', '', value).casefold()
            similarity = SequenceMatcher(None, normalized(first['text']), normalized(second['text'])).ratio()
            if fraction < config['duplicate_time_fraction'] or similarity < config['duplicate_text_similarity']:
                continue
            primary, duplicate = sorted((first, second), key=lambda item: (
                item['speaker'] == item['source_iso_speaker'], item['speaker_scores'].get(item['speaker'], 0),
                item['speaker_evidence']['volume']['per_iso'][item['source_iso_speaker']]['dbfs']), reverse=True)
            evidence = {'id': duplicate['id'], 'source_iso_speaker': duplicate['source_iso_speaker'],
                        'text_similarity': round(similarity, 4), 'time_overlap_fraction': round(fraction, 4),
                        'speaker_score': duplicate['speaker_scores'],
                        'volume_margin_db': duplicate['speaker_evidence']['volume']['margin_db']}
            primary['duplicate_candidates'].append(evidence)
            # Only suppress a high-certainty echo. Near-equal levels and two
            # independently attributed voices remain as parallel utterances.
            clear_echo = (primary['speaker'] == duplicate['speaker'] and
                          primary['speaker_evidence']['volume'].get('strength') == 'strong' and
                          not primary.get('overlap') and not duplicate.get('overlap'))
            if clear_echo:
                duplicate['duplicate_primary'] = False
                duplicate['duplicate_of'] = primary['id']
            else:
                duplicate['duplicate_candidates'].append({**evidence, 'id': primary['id'],
                    'source_iso_speaker': primary['source_iso_speaker']})
    return rows


def suppress_clear_leakage(rows):
    """Prefer the owner's ISO when it covers an off-ISO recognition.

    All provider rows stay in candidates.json with the suppression reason.
    """
    for row in rows:
        if row['speaker'] == row['source_iso_speaker'] or not row['duplicate_primary']:
            continue
        owners = [other for other in rows if other is not row and other['speaker'] == row['speaker'] and
                  other['source_iso_speaker'] == row['speaker']]
        duration = max(.001, row['end'] - row['start'])
        coverage = sum(max(0, min(row['end'], other['end']) - max(row['start'], other['start'])) for other in owners)
        if coverage / duration >= .5:
            row['duplicate_primary'] = False
            row['duplicate_of'] = 'owner-iso-coverage'
            row['speaker_evidence']['suppression_reason'] = 'owner_iso_covers_at_least_half_interval'
    return rows


def suppress_louder_owner_echoes(rows, config=DEFAULTS):
    """Suppress a quiet ISO's matching copy when the louder owner's ISO retains it.

    Provider rows remain in candidates.json with the exact audio and text
    evidence. Short acknowledgements and possible simultaneous speech stay.
    """
    owners_by_iso = {key: [other for other in rows if other['source_iso_speaker'] == key and
                      other['speaker'] == key and other['duplicate_primary']]
                     for key in ('host', 'guest_en', 'guest_ja')}
    normalized = lambda value: re.sub(r'[^\w\u3040-\u30ff\u3400-\u9fff]', '', value).casefold()
    for row in rows:
        if not row['duplicate_primary'] or row['overlap']:
            continue
        volume = row['speaker_evidence']['volume']
        owner_iso = volume['primary_iso']
        text = normalized(row['text'])
        if (owner_iso == row['source_iso_speaker'] or
                volume['margin_db'] < config['echo_volume_margin_db'] or
                len(text) < config['echo_min_characters']):
            continue
        owners = [owner for owner in owners_by_iso[owner_iso]
                  if owner is not row and owner['duplicate_primary'] and
                  owner['start'] < row['end'] and owner['end'] > row['start']]
        if not owners:
            continue
        words = sorted((word for owner in owners for word in owner['words']
                        if row['start'] - 0.2 <= (word['start'] + word['end']) / 2 <= row['end'] + 0.2),
                       key=lambda word: word['start'])
        owner_text = normalized(''.join(word['text'] for word in words))
        if not owner_text:
            continue
        similarity = SequenceMatcher(None, text, owner_text).ratio()
        if similarity < config['echo_text_similarity']:
            continue
        primary = max(owners, key=lambda owner: min(owner['end'], row['end']) - max(owner['start'], row['start']))
        evidence = {'id': row['id'], 'source_iso_speaker': row['source_iso_speaker'],
                    'text_similarity': round(similarity, 4),
                    'volume_margin_db': volume['margin_db'], 'speaker_score': row['speaker_scores']}
        if not any(item['id'] == row['id'] for item in primary['duplicate_candidates']):
            primary['duplicate_candidates'].append(evidence)
        row['duplicate_primary'] = False
        row['duplicate_of'] = primary['id']
        row['speaker_evidence']['suppression_reason'] = 'louder_owner_iso_same_words'
        row['speaker_evidence']['owner_echo'] = {'owner_iso': owner_iso,
            'owner_candidate_ids': [owner['id'] for owner in owners],
            'text_similarity': round(similarity, 4), 'volume_margin_db': volume['margin_db']}
    return rows


def build_master(rows, manifest, reference_paths=()):
    """Produce a reviewable master while preserving every provider candidate."""
    names = {x['speaker_id']: x['speaker_name'] for x in manifest['sources']}
    selected = [x for x in rows if x['duplicate_primary']]
    selected.sort(key=lambda x: (x['start'], x['end'], x['id']))
    utterances = []
    for row in selected:
        words = deepcopy(row['words']) or [{'text': row['text'], 'start': row['start'], 'end': row['end'],
                                          'confidence': row['aai_confidence'], 'eos': True}]
        utterances.append({'speaker': row['speaker'], 'language': row['language'],
                           'language_source': 'provider-and-text', 'words': words,
                           'review_envelope': {'start': row['start'], 'end': row['end']},
                           'source_iso': row['source_iso'], 'source_iso_speaker': row['source_iso_speaker'],
                           'raw_asr_text': row['raw_asr_text'], 'speaker_confidence': row['speaker_confidence'],
                           'provider_utterance_id': row.get('provider_utterance_id', row['id']),
                           'provider_utterance_text': row.get('provider_utterance_text', row['raw_asr_text']),
                           'speaker_scores': row['speaker_scores'], 'speaker_evidence': row['speaker_evidence'],
                           'ambiguous': row['ambiguous'], 'overlap': row['overlap'],
                           'duplicate_candidates': row['duplicate_candidates'],
                           'aai_speaker': row['aai_speaker'], 'aai_confidence': row['aai_confidence'],
                           'detected_language': row['detected_language'],
                           'reference_corrections': []})
    master = {'schema': 'bk-master-transcript/v0.1', 'source_id': 'multi-iso:' + manifest['start_tc'],
              'language': '??-??', 'speakers': [{'key': key, 'name': name} for key, name in names.items()],
              'utterances': utterances,
              'provenance': {'provider': 'AssemblyAI multi ISO', 'speaker_identity_verified': False,
                             'diarization_supplied': False, 'multi_iso': True,
                             'interval': {key: manifest[key] for key in ('start_tc', 'duration_seconds')},
                             'sources': manifest['sources']}}
    documents = []
    for path in reference_paths:
        path = Path(path).resolve(strict=True)
        extracted = extract_reference(path)
        documents.append({'id': hashlib.sha256(str(path).encode()).hexdigest()[:16], 'name': path.name,
                          'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                          'text': extracted, 'terms': reference_terms(extracted)})
    if documents:
        for u in utterances:
            text = displayed_words(u['words'])
            explicit = explicit_reference_candidates(text, documents)
            u['reference_corrections'] = explicit + contextual_reference_candidates(text, documents, explicit)
    return master, documents


def preserve_manual_speaker_edits(previous, incoming):
    """Carry a human assignment across ASR reruns only on a clear same-ISO match."""
    old = previous.get('master', previous).get('utterances', [])
    manual = [u for u in old if u.get('speaker_label_source') == 'manual']
    for new in incoming['utterances']:
        if new.get('speaker_label_source') == 'manual':
            continue
        candidates = []
        for prior in manual:
            if prior.get('source_iso_speaker') != new.get('source_iso_speaker'):
                continue
            a, b = prior['review_envelope'], new['review_envelope']
            overlap = max(0, min(a['end'], b['end']) - max(a['start'], b['start']))
            fraction = overlap / max(0.001, min(a['end'] - a['start'], b['end'] - b['start']))
            if fraction >= 0.8:
                candidates.append((fraction, prior))
        if len(candidates) == 1:
            new['speaker'] = candidates[0][1]['speaker']
            new['speaker_label_source'] = 'manual'
            new['speaker_evidence']['manual_edit_preserved'] = True
    return incoming


def analyze(provider_jobs, manifest, config=DEFAULTS, reference_paths=(), pyannote_fallback=None):
    clips = {item['speaker_id']: Path(item['clip']) for item in manifest['sources']}
    audio = {}
    for key, path in clips.items():
        signal, sr = sf.read(path, dtype='float32')
        if audio and sr != sample_rate:
            raise ValueError('clip sample rates differ')
        sample_rate = sr
        audio[key] = signal
    levels = {key: frame_levels(signal, sample_rate, config['window_seconds']) for key, signal in audio.items()}
    rows = []
    for item in manifest['sources']:
        key = item['speaker_id']
        rows.extend(aai_utterances(provider_jobs[key], key, item['source_iso']))
    provider_utterance_count = len(rows)
    rows = refine_utterances(rows, levels, config)
    # A prototype is drawn only from long, clearly dominant intervals.
    prototypes = {}
    for key in clips:
        candidates = [r for r in rows if r['source_iso_speaker'] == key and r['end'] - r['start'] >= 1 and
                      volume_evidence(levels, r['start'], r['end'], config)['primary_iso'] == key and
                      volume_evidence(levels, r['start'], r['end'], config)['margin_db'] >= config['strong_db']]
        if candidates:
            clean = max(candidates, key=lambda r: r['end'] - r['start'])
            prototypes[key] = spectral_features(audio[key][round(clean['start'] * sample_rate):
                                                           round(clean['end'] * sample_rate)], sample_rate)
    scored = [score_utterance(row, list(clips), levels, audio, sample_rate, prototypes, config,
                              pyannote_fallback) for row in rows]
    merged = suppress_clear_leakage(suppress_louder_owner_echoes(duplicate_groups(scored, config), config))
    master, documents = build_master(merged, manifest, reference_paths)
    return {'master': master, 'candidates': merged, 'references': documents,
            'summary': {'raw_utterances': provider_utterance_count, 'refined_candidates': len(rows),
                        'master_utterances': len(master['utterances']),
                        'duplicate_candidates': sum(not r['duplicate_primary'] for r in merged),
                        'ambiguous': sum(r['ambiguous'] for r in merged),
                        'overlap_candidates': sum(r['overlap'] for r in merged),
                        'reference_candidates': sum(len(u['reference_corrections']) for u in master['utterances']),
                        'pyannote_used': any(r['speaker_evidence']['pyannote_used'] for r in merged)}}


def run(manifest_path, output, model='universal-3-5-pro', references=(), key=None, config=DEFAULTS,
        previous_master=None):
    output = Path(output)
    manifest = json.loads(Path(manifest_path).read_text())
    if len(manifest['sources']) != 3:
        raise ValueError('expected three ISO sources')
    for item in manifest['sources']:
        if media_identity(item['source_iso']) != item['source_identity'] or media_identity(item['clip']) != item['clip_identity']:
            raise ValueError('source or clip identity changed')
    if not key:
        key = getpass.getpass('AssemblyAI API key (not saved): ').strip()
    if not key:
        raise ValueError('AssemblyAI API key is required')
    if not output.exists():
        output.mkdir(parents=True)
        (output / 'input_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    elif (output / 'input_manifest.json').exists():
        if json.loads((output / 'input_manifest.json').read_text()) != manifest:
            raise ValueError('existing run uses a different source manifest')
    else:
        raise ValueError('existing run lacks its source manifest')
    jobs = {}
    timings = {}
    for item in manifest['sources']:
        speaker = item['speaker_id']
        existing = [path for path in (output / 'provider').glob('*/source_metadata.json')
                    if json.loads(path.read_text()).get('speaker_id') == speaker]
        if len(existing) > 1:
            raise ValueError('multiple provider runs for one ISO in this run directory')
        if existing:
            directory = existing[0].parent
            saved = json.loads(existing[0].read_text())
            if saved.get('source_identity') != item['source_identity'] or saved.get('clip_identity') != item['clip_identity']:
                raise ValueError('provider run source identity changed')
        else:
            directory = output / 'provider' / str(uuid.uuid4())
            directory.mkdir(parents=True)
            (directory / 'source_metadata.json').write_text(json.dumps({
                'speaker_id': speaker, 'source_iso': item['source_iso'],
                'source_identity': item['source_identity'], 'clip': item['clip'],
                'clip_identity': item['clip_identity']}, ensure_ascii=False, indent=2) + '\n')
        run_id = directory.name
        started = time.monotonic()
        language = {'host': '??-??', 'guest_en': 'en-us', 'guest_ja': 'ja-jp'}[speaker]
        raw = directory / 'assemblyai-provider.json'
        reused_raw = raw.exists()
        if reused_raw:
            job = json.loads(raw.read_text())
            if job.get('status') != 'completed':
                raise ValueError('saved provider result is incomplete')
        else:
            def save_job_id(value):
                (directory / 'provider_job_id.txt').write_text(str(value) + '\n')
            prior_id = (directory / 'provider_job_id.txt').read_text().strip() if (directory / 'provider_job_id.txt').exists() else None
            transcribe_assemblyai(Path(item['clip']), language, {'key': key, 'model': model},
                                  existing_id=prior_id, on_job_id=save_job_id, artifact_dir=directory)
            job = json.loads(raw.read_text())
        jobs[speaker] = job
        previous_metadata = json.loads((directory / 'run_metadata.json').read_text()) if (directory / 'run_metadata.json').exists() else {}
        timings[speaker] = (round(time.monotonic() - started, 3) if not reused_raw else
                            previous_metadata.get('duration_seconds'))
        metadata = {'run_id': run_id, 'provider_job_id': job['id'], 'speaker_id': speaker,
                    'source_iso': item['source_iso'], 'source_identity': item['source_identity'],
                    'clip': item['clip'], 'clip_identity': item['clip_identity'],
                    'language_requested': language, 'language_detected': job.get('language_code'),
                    'duration_seconds': timings[speaker], 'word_count': len(job.get('words') or []),
                    'utterance_count': len(job.get('utterances') or []),
                    'raw_file': 'assemblyai-provider.json'}
        (directory / 'run_metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
    result = analyze(jobs, manifest, config, references)
    if previous_master is not None:
        previous = json.loads(Path(previous_master).read_text())
        preserve_manual_speaker_edits(previous, result['master'])
    for name, value in [('master.json', result['master']), ('candidates.json', result['candidates']),
                        ('references.json', result['references']), ('summary.json', result['summary']),
                        ('timings.json', timings)]:
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--sources', type=Path, required=True)
    prep.add_argument('--start-tc', required=True)
    prep.add_argument('--duration', type=float, default=120)
    prep.add_argument('--output', type=Path, required=True)
    processing = sub.add_parser('run')
    processing.add_argument('--manifest', type=Path, required=True)
    processing.add_argument('--output', type=Path, required=True)
    processing.add_argument('--reference', type=Path, action='append', default=[])
    processing.add_argument('--model', default='universal-3-5-pro')
    processing.add_argument('--previous-master', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare_clips(json.loads(args.sources.read_text()), args.start_tc, args.duration, args.output)
    else:
        result = run(args.manifest, args.output, args.model, args.reference,
                     previous_master=args.previous_master)
        print(json.dumps(result['summary'], ensure_ascii=False))


if __name__ == '__main__':
    main()
