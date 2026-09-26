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


def _diarization_python():
    configured = os.environ.get('PREMIERE_DIARIZATION_PYTHON')
    if configured:
        return str(Path(configured).expanduser())
    from importlib.util import find_spec
    if find_spec('pyannote') is not None:
        return sys.executable
    previous = Path.home() / 'Documents/案件フォルダ/work/tamago-independent-20260908/pyannote-env/bin/python'
    return str(previous) if previous.is_file() else None


@lru_cache(maxsize=4)
def _diarization_ready(runtime):
    if not runtime or not Path(runtime).is_file():
        return False, False
    try:
        result = subprocess.run([runtime, '-c',
            "import importlib.util,json; from huggingface_hub import get_token; print(json.dumps({'installed':importlib.util.find_spec('pyannote.audio') is not None,'credential':bool(get_token())}))"],
            capture_output=True, text=True, timeout=20, check=True)
        status = json.loads(result.stdout)
        return bool(status['installed']), bool(status['credential'])
    except (OSError, subprocess.SubprocessError, ValueError):
        return False, False


def engine_availability(aai_config):
    installed, credential = _diarization_ready(_diarization_python())
    ready = _local_whisper_installed() and installed and credential
    local = {'available': ready,
             'reason': 'ローカルWhisperと自動話者分離を使用します。初回はモデル取得が必要な場合があります' if ready
                       else 'mlx_whisper、pyannote実行環境、モデル利用承認済みのHugging Face認証が必要です',
             'verification': 'dependencies_ready' if ready else 'unavailable',
             'diarization_installed': installed, 'credential_present': credential}
    cloud = bool(aai_config.get('key') and aai_config.get('model') and _ffmpeg(aai_config.get('ffmpeg')))
    return {'local-whisper': local, 'assemblyai': {'available': cloud, 'reason': '' if cloud else 'AssemblyAI キーと ffmpeg の設定が必要です'}}


def _ffmpeg(configured=None):
    if configured:
        return shutil.which(configured)
    found = shutil.which('ffmpeg')
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return None


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


def transcribe_local(media, language, *, artifact_dir=None):
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
        artifacts = Path(artifact_dir) if artifact_dir else Path(temporary) / 'provider'
        artifacts.mkdir(parents=True, exist_ok=False)
        command = [sys.executable, str(Path(__file__).resolve()), '--mlx-worker', str(media), language, str(output), str(artifacts)]
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


def _save_raw(directory, name, value):
    path = Path(directory) / name
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False,
                  default=lambda item: item.item() if hasattr(item, 'item') else item.tolist())
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _transcribe_local_worker(media, language, artifact_dir):
    import mlx_whisper
    from diarization_master import build_master
    directory = Path(artifact_dir)
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        raise ValueError('音声変換用ffmpegが見つかりません')
    os.environ['PATH'] = str(Path(ffmpeg).parent) + os.pathsep + os.environ.get('PATH', '')
    # MLX shells out to an executable named ffmpeg; bundled executables have versioned names.
    bin_dir = directory / 'bin'; bin_dir.mkdir()
    (bin_dir / 'ffmpeg').symlink_to(ffmpeg)
    os.environ['PATH'] = str(bin_dir) + os.pathsep + os.environ['PATH']
    os.environ.setdefault('HF_HUB_DISABLE_XET', '1')
    audio = directory / 'audio-16k-mono.wav'
    subprocess.run([ffmpeg, '-nostdin', '-y', '-loglevel', 'error', '-i', str(media), '-vn',
                    '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(audio)], check=True, timeout=1800)
    kwargs = {'word_timestamps': True, 'condition_on_previous_text': False,
              'hallucination_silence_threshold': 2.0, 'task': 'transcribe', 'verbose': False}
    if language != '??-??':
        kwargs['language'] = language.split('-')[0]
    model = os.environ.get('MLX_WHISPER_MODEL', 'mlx-community/whisper-large-v3-turbo')
    result = mlx_whisper.transcribe(str(audio), path_or_hf_repo=model, **kwargs)
    whisper_hash = _save_raw(directory, 'whisper-provider.json', result)
    runtime = _diarization_python()
    if not runtime:
        raise ValueError('自動話者分離のPython環境を設定してください')
    diarization_path = directory / 'diarization-provider.json'
    subprocess.run([runtime, str(Path(__file__).resolve()), '--diarization-worker',
                    str(audio), str(diarization_path)], check=True, timeout=7200)
    diarization = json.loads(diarization_path.read_text(encoding='utf-8'))
    master = build_master(result, diarization, media.name, language, validate_export=False)
    for utterance in master['utterances']:
        utterance['language'] = language
        utterance['language_source'] = 'initial_manual' if language != '??-??' else 'unconfirmed'
        utterance['words'][-1]['eos'] = True
    master['provenance'].update(provider='mlx_whisper', model=model, built_in_premiere_asr=False,
        diarization_supplied=bool(diarization.get('segments')), provider_detected_language=result.get('language'),
        raw_artifacts={'whisper': {'file': 'whisper-provider.json', 'sha256': whisper_hash},
                       'diarization': {'file': 'diarization-provider.json',
                                      'sha256': hashlib.sha256(diarization_path.read_bytes()).hexdigest()}})
    return master


def _diarization_worker(audio, output):
    os.environ['PYANNOTE_METRICS_ENABLED'] = '0'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['MPLCONFIGDIR'] = str(output.parent / 'matplotlib-cache')
    import soundfile as sf
    import torch
    from huggingface_hub import get_token
    from pyannote.audio import Pipeline
    token = get_token()
    if not token:
        raise ValueError('自動話者分離のモデル認証を設定してください')
    model = 'pyannote/speaker-diarization-community-1'
    revision = '3533c8cf8e369892e6b79ff1bf80f7b0286a54ee'
    torch.set_num_threads(4)
    pipeline = Pipeline.from_pretrained(model, revision=revision, token=token)
    waveform, sample_rate = sf.read(audio, dtype='float32', always_2d=True)
    result = pipeline({'waveform': torch.from_numpy(waveform.T.copy()), 'sample_rate': sample_rate})
    def turns(annotation):
        return [{'start': turn.start, 'end': turn.end, 'speaker': speaker}
                for turn, _, speaker in annotation.itertracks(yield_label=True)]
    value = {'segments': turns(result.speaker_diarization),
             'exclusive_segments': turns(result.exclusive_speaker_diarization),
             'metadata': {'model': model, 'revision': revision, 'speaker_count_constraint': None,
                          'device': 'cpu', 'audio_sha256': hashlib.sha256(audio.read_bytes()).hexdigest()}}
    _save_raw(output.parent, output.name, value)


def transcribe_assemblyai(media, language, config, existing_id=None, on_job_id=None, *, artifact_dir=None):
    key, model = config['key'], config['model']
    if existing_id:
        job = {'id': existing_id}
    else:
        ffmpeg = _ffmpeg(config.get('ffmpeg'))
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
    raw_hash = None
    if artifact_dir is not None:
        Path(artifact_dir).mkdir(parents=True, exist_ok=True)
        raw_hash = _save_raw(artifact_dir, 'assemblyai-provider.json', job)
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
    master = _timed_master(words, media, code, 'AssemblyAI:' + str(job['id']))
    master['provenance'].update(built_in_premiere_asr=False, provider_detected_language=detected)
    if raw_hash:
        master['provenance']['raw_artifacts'] = {'assemblyai': {'file': 'assemblyai-provider.json', 'sha256': raw_hash}}
    return master


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
    if len(sys.argv) == 4 and sys.argv[1] == '--diarization-worker':
        _diarization_worker(Path(sys.argv[2]), Path(sys.argv[3]))
    elif len(sys.argv) == 6 and sys.argv[1] == '--mlx-worker':
        _, _, media_name, language_code, output_name, artifact_name = sys.argv
        result = _transcribe_local_worker(Path(media_name), language_code, Path(artifact_name))
        Path(output_name).write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    else:
        raise SystemExit('usage: workflow_backend.py --mlx-worker MEDIA LANGUAGE OUTPUT ARTIFACTS')
