from __future__ import annotations

import json
import re
from pathlib import Path

import fcntl


PLACEHOLDER_SPEAKER = re.compile(
    r"^(?:不明|unknown|speaker\s*\d*|話者(?:\s*[@＠#]?\s*\d*)?)$", re.IGNORECASE
)


def is_placeholder_speaker(name: str) -> bool:
    return bool(PLACEHOLDER_SPEAKER.fullmatch(str(name or "").strip()))


def read_speaker_registry(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return _clean_names(payload.get("names", []))


def add_speaker_names(path: Path, names) -> list[str]:
    """Append real names to one Premiere-project registry under a process lock."""
    additions = _clean_names(names)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(f"{path.suffix}.lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        existing = read_speaker_registry(path)
        merged = list(dict.fromkeys((*existing, *additions)))
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(
            json.dumps({"schema_version": 1, "names": merged}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return merged


def write_speaker_registry(path: Path, names) -> list[str]:
    """Replace the registry atomically while preserving order and valid real names."""
    cleaned = _clean_names(names)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(f"{path.suffix}.lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(
            json.dumps({"schema_version": 1, "names": cleaned}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return cleaned


def replace_speaker_name(path: Path, old_name: str, new_name: str | None = None) -> list[str]:
    """Remove one registry name and optionally merge it into a new canonical name."""
    old = str(old_name or "").strip()
    new = str(new_name or "").strip()
    names = [name for name in read_speaker_registry(path) if name != old]
    if new and not is_placeholder_speaker(new):
        names.append(new)
    return write_speaker_registry(path, names)


def _clean_names(names) -> list[str]:
    output = []
    for value in names or []:
        name = str(value or "").strip()[:100]
        if name and not is_placeholder_speaker(name):
            output.append(name)
    return list(dict.fromkeys(output))
