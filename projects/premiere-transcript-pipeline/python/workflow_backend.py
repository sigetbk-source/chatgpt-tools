"""Initial transcription and local reference suggestions for multilingual review."""
import hashlib
import json
import os
import signal
import sys
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
import uuid
import zipfile
from difflib import SequenceMatcher
from functools import lru_cache
from xml.etree import ElementTree


@lru_cache(maxsize=1)
def _local_whisper_installed():
    from importlib.util import find_spec
    return find_spec('mlx_whisper') is not None


def engine_availability(aai_config):
    local = {'available': _local_whisper_installed(),
             'reason': 'mlx_whisper はインストール済みです。実素材での完走は未確認です' if _local_whisper_installed()
                       else 'mlx_whisper がこの Python 環境にありません',
             'verification': 'installed_only' if _local_whisper_installed() else 'unavailable'}
    cloud = bool(aai_config.get('key') and aai_config.get('model') and shutil.which(aai_config.get('ffmpeg') or 'ffmpeg'))
    return {'local-whisper': local, 'assemblyai': {'available': cloud, 'reason': '' if cloud else 'AssemblyAI キーと ffmpeg の設定が必要です'}}


def media_identity(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    stat = Path(path).stat()
    return {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'sha256': digest.hexdigest()}


def _timed_master(words, media, language, provider):
    utterances = []; speakers = {}
    for row in words:
        text = str(row.get('text', row.get('word', '')))
        if not text.strip():
            continue
        start, end = float(row['start']), float(row['end'])
        if start < 0 or end < start:
            raise ValueError('単語時刻が不正です')
        confidence = row.get('confidence', row.get('probability', 0.0)) or 0.0
        raw_speaker = row.get('speaker')
        speaker = 'spk_' + re.sub(r'[^A-Za-z0-9_-]', '', str(raw_speaker)) if raw_speaker is not None else 'unknown'
        if speaker not in speakers:
            speakers[speaker] = '話者未確認' if speaker == 'unknown' else '自動話者' + str(len(speakers) + 1)
        utterances.append({'speaker': speaker, 'language': language,
            'language_source': 'provider' if language != '??-??' else 'unconfirmed',
            'words': [{'text': text, 'start': start, 'end': end,
                       'confidence': max(0.0, min(1.0, float(confidence))), 'eos': True}]})
    if not utterances:
        raise ValueError('単語時刻付きの文字起こしが得られませんでした')
    # Group short runs to keep the review UI manageable. No speaker identity is guessed.
    grouped = []
    for item in utterances:
        if grouped and grouped[-1]['speaker'] == item['speaker'] and len(grouped[-1]['words']) < 35 and item['words'][0]['start'] - grouped[-1]['words'][-1]['end'] < 1.2:
            grouped[-1]['words'][-1]['eos'] = False
            previous = grouped[-1]['words'][-1]['text']
            following = item['words'][0]['text']
            if previous and following and previous[-1].isascii() and previous[-1].isalnum() and following[0].isascii() and following[0].isalnum():
                item['words'][0]['text'] = ' ' + following
            grouped[-1]['words'].extend(item['words'])
        else:
            grouped.append(item)
    return {'schema': 'bk-master-transcript/v0.1', 'source_id': media.name,
        'language': language, 'speakers': [{'key': key, 'name': name} for key, name in speakers.items()],
        'utterances': grouped, 'provenance': {'provider': provider, 'speaker_identity_verified': False,
            'diarization_supplied': any(key != 'unknown' for key in speakers)}}


def transcribe_local(media, language):
    """Isolate MLX and ffmpeg from the long-running review server."""
    try:
        timeout = int(os.environ.get('LOCAL_WHISPER_TIMEOUT_SECONDS', '600'))
    except ValueError:
        raise ValueError('LOCAL_WHISPER_TIMEOUT_SECONDS は整数で指定してください') from None
    if not 30 <= timeout <= 7200:
        raise ValueError('LOCAL_WHISPER_TIMEOUT_SECONDS は30〜7200秒で指定してください')
    with tempfile.TemporaryDirectory(prefix='local-whisper-') as temporary:
        output = Path(temporary) / 'result.json'
        errors = Path(temporary) / 'errors.txt'
        command = [sys.executable, str(Path(__file__).resolve()), '--mlx-worker', str(media), language, str(output)]
        with errors.open('wb') as error_stream:
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=error_stream,
                                       start_new_session=True)
            try:
                return_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                process.wait()
                raise ValueError(f'ローカルWhisperが{timeout}秒以内に完了しませんでした。作業データは保存されています') from None
        if return_code:
            detail = errors.read_text(encoding='utf-8', errors='replace')[-1000:].strip()
            raise ValueError('ローカルWhisperの別プロセスが失敗しました: ' + (detail or f'exit {return_code}'))
        if not output.is_file():
            raise ValueError('ローカルWhisperの結果ファイルがありません')
        return json.loads(output.read_text(encoding='utf-8'))


def _transcribe_local_worker(media, language):
    import mlx_whisper
    kwargs = {'word_timestamps': True}
    if language != '??-??':
        kwargs['language'] = language.split('-')[0]
    model = os.environ.get('MLX_WHISPER_MODEL', 'mlx-community/whisper-large-v3-turbo')
    result = mlx_whisper.transcribe(str(media), path_or_hf_repo=model, **kwargs)
    words = [word for segment in result.get('segments', []) for word in segment.get('words', [])]
    detected = result.get('language') or ''
    code = language if language != '??-??' else '??-??'
    return _timed_master(words, media, code, 'mlx_whisper')


def transcribe_assemblyai(media, language, config, existing_id=None, on_job_id=None):
    key, model = config['key'], config['model']
    if existing_id:
        job = {'id': existing_id}
    else:
        ffmpeg = config.get('ffmpeg') or shutil.which('ffmpeg')
        if not ffmpeg: raise ValueError('ffmpeg が見つかりません')
        with tempfile.TemporaryDirectory(prefix='transcript-audio-') as temporary:
            audio = Path(temporary) / 'audio.mp3'
            subprocess.run([ffmpeg, '-nostdin', '-y', '-i', str(media), '-vn', '-ac', '1', '-ar', '16000',
                            '-b:a', '64k', str(audio)], check=True, capture_output=True, timeout=1800)
            req = urllib.request.Request('https://api.assemblyai.com/v2/upload', audio.read_bytes(),
                                         {'authorization': key, 'content-type': 'application/octet-stream'})
            with urllib.request.urlopen(req, timeout=300) as response:
                upload = json.load(response)
        body = {'audio_url': upload['upload_url'], 'speech_models': [model], 'speaker_labels': True,
                'prompt': 'Transcribe in the spoken original language. Do not translate.'}
        body.update({'language_detection': True} if language == '??-??' else {'language_code': language.split('-')[0]})
        req = urllib.request.Request('https://api.assemblyai.com/v2/transcript', json.dumps(body).encode(),
                                     {'authorization': key, 'content-type': 'application/json'})
        with urllib.request.urlopen(req, timeout=120) as response:
            job = json.load(response)
        if on_job_id: on_job_id(job['id'])
    deadline = time.time() + 1800
    while job.get('status') not in ('completed', 'error') and time.time() < deadline:
        req = urllib.request.Request('https://api.assemblyai.com/v2/transcript/' + job['id'],
                                     headers={'authorization': key})
        with urllib.request.urlopen(req, timeout=120) as response:
            job = json.load(response)
        if job.get('status') not in ('completed', 'error'): time.sleep(3)
    if job.get('status') != 'completed':
        raise ValueError('AssemblyAI 文字起こし失敗: ' + str(job.get('error') or job.get('id')))
    words = [dict(w, start=w['start'] / 1000, end=w['end'] / 1000) for w in job.get('words', [])]
    turns = job.get('utterances') or []
    for word in words:
        if word.get('speaker') is None:
            start_ms = word['start'] * 1000; end_ms = word['end'] * 1000
            overlaps = [(min(end_ms, turn['end']) - max(start_ms, turn['start']), turn.get('speaker'))
                        for turn in turns if turn.get('speaker') is not None]
            if overlaps:
                overlap, speaker = max(overlaps, key=lambda item: item[0])
                if overlap > 0: word['speaker'] = speaker
    detected = job.get('language_code') or ''
    code = language if language != '??-??' else '??-??'
    return _timed_master(words, media, code, 'AssemblyAI:' + str(job['id']))


def extract_reference(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError('参照資料はファイルを指定してください')
    if path.stat().st_size > 20_000_000:
        raise ValueError('参照資料は20MB以下にしてください')
    suffix = path.suffix.lower()
    if suffix == '.txt':
        return path.read_text(encoding='utf-8-sig')
    if suffix == '.docx':
        with zipfile.ZipFile(path) as archive:
            xml = ElementTree.fromstring(archive.read('word/document.xml'))
        namespace = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
        return '\n'.join(''.join(node.text or '' for node in paragraph.iter(namespace + 't'))
                         for paragraph in xml.iter(namespace + 'p'))
    if suffix == '.pdf':
        tool = shutil.which('pdftotext')
        if tool:
            return subprocess.run([tool, '-layout', str(path), '-'], capture_output=True, text=True,
                                  check=True, timeout=30).stdout
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ValueError('PDF 読み取りには pdftotext または pypdf が必要です') from None
        return '\n'.join(page.extract_text() or '' for page in PdfReader(path).pages)
    raise ValueError('参照資料は TXT、DOCX、PDF のみ対応しています')


def reference_terms(text):
    """Extract explicit aliases and named terms with their source line as evidence."""
    result = []
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        match = re.match(r'^(.{1,80}?)\s*(?:→|=>|＝|=|:|：)\s*(.{1,80})$', line)
        if match and match.group(1).strip() and match.group(2).strip():
            result.append({'from': match.group(1).strip(), 'to': match.group(2).strip(),
                           'line': number, 'evidence': line[:200]})
        else:
            named = []
            named.extend(re.findall(r'[「“"]([^」”"\n]{2,40})[」”"]', line))
            named.extend(re.findall(r'(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9.+&_-]{2,}(?:\s+[A-Za-z][A-Za-z0-9.+&_-]+){0,3}', line))
            for spelling, reading in re.findall(r'([一-龯々]{2,12})\s*[（(]\s*([ぁ-んァ-ヶー・\s]{2,30})\s*[）)]', line):
                named.append(spelling)
                result.append({'from': re.sub(r'[・\s]', '', reading), 'to': spelling,
                               'line': number, 'evidence': line[:200]})
            for term in named:
                term = term.strip()
                if len(term) >= 3 and not re.search(r'[。！？!?]', term):
                    result.append({'from': None, 'to': term, 'line': number, 'evidence': line[:200]})
    return result[:500]


def suggestions(master, documents, display_text):
    rows = []
    for index, utterance in enumerate(master['utterances']):
        current = display_text(utterance)
        for document in documents:
            for term in document['terms']:
                target = term['to']; source = term['from']
                if source is None:
                    # Only propose a bounded near match; the displayed source line remains reviewable.
                    candidates = re.findall(r'[A-Za-z][A-Za-z0-9.+&_-]{2,}|[ァ-ヶー]{3,}|[一-龯々]{3,}', current)
                    source = next((part for part in candidates if part != target and
                        0.72 <= SequenceMatcher(None, part.casefold(), target.casefold()).ratio() < 1), None)
                if source and source != target and source in current:
                    rows.append({'id': hashlib.sha256(f"{index}:{document['id']}:{term['line']}:{source}:{target}:{current}".encode()).hexdigest()[:24],
                                 'segment_index': index, 'from': source, 'to': target,
                                 'source_name': document['name'], 'evidence': term['evidence'],
                                 'source_line': term['line'], 'base_text': current, 'status': 'candidate'})
    return rows


if __name__ == '__main__':
    if len(sys.argv) != 5 or sys.argv[1] != '--mlx-worker':
        raise SystemExit('usage: workflow_backend.py --mlx-worker MEDIA LANGUAGE OUTPUT')
    _, _, media_name, language_code, output_name = sys.argv
    result = _transcribe_local_worker(Path(media_name), language_code)
    Path(output_name).write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')
