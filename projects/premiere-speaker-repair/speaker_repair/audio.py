from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import numpy as np

from .project import Project


VIDEO_SUFFIXES = {".mov", ".mp4", ".m4v", ".mxf"}


def ffmpeg_executable() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise RuntimeError(
            "メディア変換機能が未導入です。プロジェクトの依存関係をインストールしてください。"
        ) from exc


def convert_audio(project: Project, force: bool = False) -> Path:
    """Convert the registered source audio once to 16 kHz mono PCM."""
    project.analysis_dir.mkdir(parents=True, exist_ok=True)
    if project.wav_path.exists() and not force:
        return project.wav_path
    command = [
        ffmpeg_executable(),
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(project.audio),
        *_audio_selection_args(project),
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(project.wav_path),
    ]
    subprocess.run(command, check=True)
    return project.wav_path


def prepare_review_video(project: Project, force: bool = False) -> Path | None:
    """Create a browser-compatible, case-local review proxy for video sources."""
    if project.audio.suffix.lower() not in VIDEO_SUFFIXES:
        return None
    project.analysis_dir.mkdir(parents=True, exist_ok=True)
    output = project.review_video_path
    if output.exists() and output.stat().st_size > 0 and not force:
        return output
    temporary = output.with_name(f"{output.stem}.tmp{output.suffix}")
    temporary.unlink(missing_ok=True)
    audio_index = project.audio_stream_index if project.audio_stream_index is not None else 0
    common = [
        ffmpeg_executable(),
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(project.audio),
        "-map",
        "0:v:0",
        "-map",
        f"0:a:{audio_index}?",
        "-vf",
        "bwdif=mode=send_frame:parity=auto:deint=interlaced,scale=w=960:h=-2:force_original_aspect_ratio=decrease",
    ]
    hardware_command = [
        *common,
        "-c:v",
        "h264_videotoolbox",
        "-b:v",
        "4M",
        "-maxrate",
        "6M",
        "-bufsize",
        "12M",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(temporary),
    ]
    try:
        try:
            subprocess.run(hardware_command, check=True)
        except subprocess.CalledProcessError:
            temporary.unlink(missing_ok=True)
            software_command = [
                *common,
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "27",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                str(temporary),
            ]
            subprocess.run(software_command, check=True)
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return output


def _audio_selection_args(project: Project) -> list[str]:
    args: list[str] = []
    if project.audio_stream_index is not None:
        args.extend(["-map", f"0:a:{project.audio_stream_index}"])
    if project.audio_channel_index is not None:
        args.extend(["-af", f"pan=mono|c0=c{project.audio_channel_index}"])
    else:
        args.extend(["-ac", "1"])
    return args


class WaveReader:
    def __init__(self, path: Path):
        self.path = path
        with wave.open(str(path), "rb") as handle:
            if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
                raise ValueError("16-bit mono WAV が必要です")
            self.sample_rate = handle.getframerate()
            self.frame_count = handle.getnframes()

    def read(self, start_seconds: float, duration_seconds: float) -> np.ndarray:
        start_frame = max(0, round(start_seconds * self.sample_rate))
        frame_count = max(1, round(duration_seconds * self.sample_rate))
        with wave.open(str(self.path), "rb") as handle:
            handle.setpos(min(start_frame, handle.getnframes()))
            raw = handle.readframes(frame_count)
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32)
        return samples / 32768.0


def chunk_ranges(start: float, duration: float, minimum: float, maximum: float):
    """Yield analysis windows, avoiding tiny tail chunks."""
    if duration < minimum:
        return
    cursor = start
    remaining = duration
    while remaining >= minimum:
        if remaining <= maximum:
            length = remaining
        elif remaining - maximum < minimum:
            length = remaining / 2
        else:
            length = maximum
        yield cursor, length
        cursor += length
        remaining -= length
