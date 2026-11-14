from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import imageio_ffmpeg

from .audio import _audio_selection_args
from .project import Project


def transcribe(project: Project, test_seconds: float | None = None) -> dict[str, Any]:
    project.analysis_dir.mkdir(parents=True, exist_ok=True)
    references = json.loads(project.references_path.read_text(encoding="utf-8"))
    audio_path = project.audio
    output_path = project.whisper_path
    if test_seconds:
        audio_path = _make_test_audio(project, test_seconds)
        output_path = project.analysis_dir / "whisper-test.json"

    ffmpeg_dir = _prepare_ffmpeg_link(project)
    os.environ["PATH"] = f"{ffmpeg_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    os.environ["HF_HOME"] = str(project.model_cache_dir)
    # Xet downloads can stall on some restricted networks; standard HTTPS is sufficient here.
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

    import mlx_whisper

    prompt_terms = [term for term in references["terms"] if len(term) <= 24][:120]
    initial_prompt = "固有名詞・専門用語: " + "、".join(prompt_terms)
    result = mlx_whisper.transcribe(
        str(audio_path),
        path_or_hf_repo=project.whisper_model,
        language="ja",
        task="transcribe",
        word_timestamps=True,
        initial_prompt=initial_prompt,
        condition_on_previous_text=True,
        verbose=True,
    )
    result["_meta"] = {
        "model": project.whisper_model,
        "audio": str(audio_path),
        "reference_term_count": len(references["terms"]),
        "test_seconds": test_seconds,
    }
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    return result


def _prepare_ffmpeg_link(project: Project) -> Path:
    bin_dir = project.analysis_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    link = bin_dir / "ffmpeg"
    target = Path(imageio_ffmpeg.get_ffmpeg_exe())
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(target)
    return bin_dir


def _make_test_audio(project: Project, seconds: float) -> Path:
    output = project.analysis_dir / f"whisper-test-{int(seconds)}s.wav"
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
        "-i", str(project.audio), "-t", str(seconds), *_audio_selection_args(project), "-ar", "16000",
        "-c:a", "pcm_s16le", str(output),
    ]
    subprocess.run(command, check=True)
    return output


def _json_default(value):
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"JSONへ変換できません: {type(value)!r}")
