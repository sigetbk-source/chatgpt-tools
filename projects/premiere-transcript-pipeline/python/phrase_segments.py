"""Experimental phrase grouping; preserve manually edited utterances exactly."""
from copy import deepcopy
from collections import defaultdict
from premiere_export import convert


def regroup(original, edited):
    convert(original)
    convert(edited)
    flat = lambda m: [w for u in m['utterances'] for w in u['words']]
    if flat(original) != flat(edited):
        raise ValueError('word content/timing/annotations changed; cannot map manual edits')
    def indexed(master):
        offset = 0
        for u in master['utterances']:
            end = offset + len(u['words'])
            yield offset, end, u
            offset = end
    baseline = {(a,b,u['speaker']) for a,b,u in indexed(original)}
    result = deepcopy(edited)
    output, pending, protected = [], [], 0
    def flush():
        if not pending:
            return
        scores = defaultdict(float)
        for w in pending:
            evidence = w.get('diarization', {}).get('speaker_overlap_seconds', {})
            for key, score in evidence.items():
                if key in {s['key'] for s in edited['speakers']}:
                    scores[key] += score
        ranked = sorted(scores, key=lambda k: (-scores[k], k))
        speaker = ranked[0] if ranked else 'unknown'
        if len(ranked)>1 and abs(scores[ranked[0]]-scores[ranked[1]])<1e-9:
            speaker = 'unknown'
        if speaker not in {s['key'] for s in edited['speakers']}:
            raise ValueError('unknown speaker must exist for unresolved phrase')
        output.append({'speaker':speaker,'words':deepcopy(pending)})
        pending.clear()
    for a,b,u in indexed(edited):
        if (a,b,u['speaker']) not in baseline:
            flush()
            output.append(deepcopy(u))
            protected += 1
            continue
        for w in u['words']:
            index = w.get('diarization', {}).get('source_segment_index')
            if index is None:
                raise ValueError('source ASR segment metadata required')
            if pending and index != pending[-1]['diarization']['source_segment_index']:
                flush()
            pending.append(w)
    flush()
    result['utterances'] = output
    result.setdefault('provenance', {})['phrase_comparison'] = {
        'experimental':True, 'method':'source_asr_segment_overlap_sum',
        'protected_utterances':protected, 'before':len(edited['utterances']), 'after':len(output),
        'speaker_accuracy_verified':False}
    convert(result)
    return result
