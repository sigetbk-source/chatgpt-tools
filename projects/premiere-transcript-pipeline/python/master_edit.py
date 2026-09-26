"""Speaker edits in the independent master; word timing and annotations stay intact."""
from copy import deepcopy
from premiere_export import convert


def validate_for_edit(master):
    """Unknown per-utterance language is valid review state, but not export state."""
    candidate = deepcopy(master)
    for utterance in candidate.get('utterances', []):
        if utterance.get('language') == '??-??':
            utterance['language'] = candidate.get('language')
    convert(candidate)


def edit(master, operation, index, speaker, word_index=None):
    validate_for_edit(master)
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(master['utterances']):
        raise ValueError('invalid utterance index')
    if speaker not in {s['key'] for s in master['speakers']}:
        raise ValueError('unknown speaker')
    result = deepcopy(master)
    utterances = result['utterances']
    current = utterances[index]
    if operation == 'assign':
        current['speaker'] = speaker
    elif operation == 'split':
        words = current['words']
        if isinstance(word_index, bool) or not isinstance(word_index, int) or not 0 < word_index < len(words):
            raise ValueError('split must be between words')
        if max(w['end'] for w in words[:word_index]) > words[word_index]['start']:
            raise ValueError('cannot split overlapping words')
        second = deepcopy(current)
        current['words'], second['words'] = words[:word_index], words[word_index:]
        second['speaker'] = speaker
        utterances.insert(index + 1, second)
    elif operation == 'merge':
        if index + 1 >= len(utterances):
            raise ValueError('missing adjacent utterance')
        second = utterances[index + 1]
        # Do not silently discard annotations belonging to the second utterance.
        metadata = lambda u: {k: v for k, v in u.items() if k not in ('words', 'speaker')}
        if metadata(current) != metadata(second):
            raise ValueError('utterance metadata differs; reconcile before merging')
        if max(w['end'] for w in current['words']) > second['words'][0]['start']:
            raise ValueError('cannot merge overlapping utterances')
        current['words'].extend(second['words'])
        current['speaker'] = speaker
        del utterances[index + 1]
    else:
        raise ValueError('unsupported operation')
    validate_for_edit(result)
    return result
