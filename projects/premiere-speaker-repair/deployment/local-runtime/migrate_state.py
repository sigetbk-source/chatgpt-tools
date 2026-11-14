"""Snapshot existing metadata into a staged local release, without replaying IPC.

Historical case/config paths intentionally retain their original locations: this
does not move client material. Every required metadata JSON must already be
downloaded and valid before any state is written; dictionaries may not be lost.
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

DATALESS = 0x40000000


def is_dataless(path: Path) -> bool:
    return bool(getattr(path.stat(), "st_flags", 0) & DATALESS)


def migrate(source: Path, stage: Path) -> dict:
    old = source / "work" / "uxp"
    new = stage / "app" / "work" / "uxp"
    if new.exists() and any(new.iterdir()):
        raise ValueError("Refusing to overwrite an existing runtime state directory")
    copied, required, unavailable = [], [], []
    for name in ("jobs.json", "case-glossaries", "speaker-registries"):
        item = old / name
        paths = list(item.rglob("*.json")) if item.is_dir() else ([item] if item.exists() else [])
        for path in paths:
            if is_dataless(path):
                unavailable.append(str(path))
                continue
            # Validate before creating the target state: broken JSON is not an
            # empty registry and must not become an apparently successful move.
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError(f"Metadata must contain a JSON object: {path}")
            required.append(path)
    if unavailable:
        raise ValueError("Required state files are not downloaded: " + ", ".join(unavailable))
    new.mkdir(parents=True, exist_ok=True)
    for path in required:
        rel = path.relative_to(old)
        target = new / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied.append(str(rel))
    if (old / "jobs.json").exists() and not (new / "jobs.json").exists():
        raise ValueError("Historical job registry is not downloaded; cannot migrate safely")
    # The narration index already lives outside iCloud, preserving its established
    # location also keeps browser jobs and older narration sessions discoverable.
    for path in (new / "ipc" / "inbox", new / "ipc" / "outbox", new / "logs"):
        path.mkdir(parents=True, exist_ok=True)
    report = {
        "createdAt": time.time(), "source": str(old), "stage": str(new),
        "copiedMetadata": copied, "deferredDatalessFiles": [],
        "historicalJobPathsPreserved": True, "ephemeralRequestsReplayed": False,
        "modelWeightsCopied": False,
        "narrationIndex": str(Path.home() / "Library/Application Support/ナレーション制作ツール/narration-job-index.json"),
    }
    (stage / "state-migration.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--stage", type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(migrate(a.source, a.stage), ensure_ascii=False, indent=2))
