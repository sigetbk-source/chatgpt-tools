from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path


def _srt_time(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def synchronize_narration_srt(manifest_path: Path, timings: list[dict]) -> dict:
    """Create a versioned SRT from the actual Premiere audio track times."""
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    audio_items = manifest.get("audioItems")
    if not isinstance(audio_items, list) or not audio_items:
        raise ValueError("ナレーション管理データに音声項目がありません")
    if len(timings) != len(audio_items):
        raise ValueError("Premiere上の音声数とナレーション管理データが一致しません")

    synchronized: list[dict] = []
    previous_end: float | None = None
    for index, (item, timing) in enumerate(zip(audio_items, timings, strict=True), 1):
        item_id = str(item.get("id", ""))
        if item_id != str(timing.get("id", "")):
            raise ValueError(f"{index}番目のナレーションIDが一致しません")
        try:
            start = float(timing["startSeconds"])
            end = float(timing["endSeconds"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"{item_id}のPremiere時刻が不正です") from error
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError(f"{item_id}のPremiere時刻が不正です")
        if previous_end is not None and start + 1e-7 < previous_end:
            raise ValueError(f"{item_id}が直前のナレーションと重なっています")
        updated = dict(item)
        updated.update({
            "startSeconds": start,
            "endSeconds": end,
            "durationSeconds": end - start,
        })
        synchronized.append(updated)
        previous_end = end

    current_srt = Path(str(manifest.get("srtPath", "")))
    if not current_srt.parent.is_dir():
        raise ValueError("同期済みSRTの保存先が見つかりません")
    revision = re.sub(r"[^0-9A-Za-z_-]+", "-", str(manifest.get("revision", "sync"))).strip("-") or "sync"
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    srt_path = current_srt.parent / f"narration_audio_{revision}_synced_{stamp}.srt"
    blocks = [
        f"{index}\n{_srt_time(float(item['startSeconds']))} --> {_srt_time(float(item['endSeconds']))}\n"
        f"N　{item.get('captionText', '')}\n"
        for index, item in enumerate(synchronized, 1)
    ]
    srt_path.write_text("\n".join(blocks) + "\n", encoding="utf-8")

    manifest["audioItems"] = synchronized
    manifest["srtPath"] = str(srt_path)
    manifest["srtSynchronizedAt"] = datetime.now().astimezone().isoformat(timespec="seconds")
    temporary = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(manifest_path)
    return {"manifest": manifest, "srtPath": str(srt_path)}
