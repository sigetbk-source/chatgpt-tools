from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .project import Project


def segment_text(segment: dict[str, Any]) -> str:
    return "".join(word.get("text", "") for word in segment.get("words", []))


def build_manifest(project: Project) -> dict[str, Any]:
    transcript = json.loads(project.transcript_json.read_text(encoding="utf-8"))
    segments = transcript.get("segments", [])
    csv_rows: list[dict[str, str]] = []
    if project.transcript_csv and project.transcript_csv.exists():
        with project.transcript_csv.open(encoding="utf-8-sig", newline="") as handle:
            csv_rows = list(csv.DictReader(handle))
        if len(segments) != len(csv_rows):
            raise ValueError(
                f"JSON区間数({len(segments)})とCSV行数({len(csv_rows)})が一致しません"
            )
    else:
        csv_rows = [{} for _ in segments]

    speaker_by_id = {item["id"]: item["name"] for item in transcript.get("speakers", [])}
    manifest_segments: list[dict[str, Any]] = []
    mismatches: list[int] = []

    for index, (segment, csv_row) in enumerate(zip(segments, csv_rows, strict=True)):
        speaker_id = segment["speaker"]
        speaker_name = speaker_by_id.get(speaker_id, "不明")
        text = segment_text(segment)
        if project.transcript_csv and (
            csv_row.get("Speaker Name") != speaker_name or csv_row.get("Text") != text
        ):
            mismatches.append(index)
        manifest_segments.append(
            {
                "index": index,
                "speaker_id": speaker_id,
                "speaker_name": speaker_name,
                "full_name": project.cast.get(speaker_name),
                "locked": speaker_name in project.known_speakers,
                "start_seconds": float(segment["start"]),
                "duration_seconds": float(segment["duration"]),
                "end_seconds": float(segment["start"]) + float(segment["duration"]),
                "premiere_start": csv_row.get("Start Time") or _seconds_label(float(segment["start"])),
                "premiere_end": csv_row.get("End Time") or _seconds_label(float(segment["start"]) + float(segment["duration"])),
                "text": text,
                "mean_word_confidence": _mean_confidence(segment.get("words", [])),
            }
        )

    label_counts = Counter(item["speaker_name"] for item in manifest_segments)
    id_counts = Counter(item["speaker_id"] for item in manifest_segments)
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_name": project.project_name,
        "language": transcript.get("language"),
        "source": {
            "json": str(project.transcript_json),
            "csv": str(project.transcript_csv) if project.transcript_csv else None,
            "native_transcript": str(project.native_transcript) if project.native_transcript else None,
            "audio": str(project.audio),
        },
        "known_speakers": list(project.known_speakers),
        "cast": project.cast,
        "segment_count": len(manifest_segments),
        "speaker_label_counts": dict(label_counts),
        "speaker_id_counts": dict(id_counts),
        "csv_json_mismatch_indices": mismatches,
        "segments": manifest_segments,
    }
    return manifest


def write_manifest(project: Project) -> dict[str, Any]:
    manifest = build_manifest(project)
    project.analysis_dir.mkdir(parents=True, exist_ok=True)
    project.manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def _mean_confidence(words: list[dict[str, Any]]) -> float | None:
    values = [float(word["confidence"]) for word in words if "confidence" in word]
    return sum(values) / len(values) if values else None


def _seconds_label(value: float) -> str:
    hours, remainder = divmod(max(value, 0.0), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{seconds:06.3f}"
