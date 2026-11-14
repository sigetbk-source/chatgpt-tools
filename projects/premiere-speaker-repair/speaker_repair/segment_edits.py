from __future__ import annotations

import copy
import difflib
import json
from datetime import datetime, timezone
from typing import Any

from .project import Project
from .speaker_registry import read_speaker_registry, write_speaker_registry


HISTORY_LIMIT = 50


def _seconds_label(value: float) -> str:
    hours, remainder = divmod(max(value, 0.0), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{seconds:06.3f}"


def _deepcopy(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def _initial_items(project: Project, candidates: dict[str, Any]) -> list[dict[str, Any]]:
    transcript = json.loads(project.transcript_json.read_text(encoding="utf-8-sig"))
    source_segments = transcript.get("segments", [])
    items = []
    for candidate in candidates.get("items", []):
        index = int(candidate["segment_index"])
        if index >= len(source_segments):
            raise ValueError(f"元JSONに区間{index}がありません")
        item = _deepcopy(candidate)
        item["segment_uid"] = f"source-{index}"
        item["source_segment_indices"] = [index]
        item["_source_words"] = _deepcopy(source_segments[index].get("words", []))
        template = _deepcopy(source_segments[index])
        template.pop("words", None)
        item["_segment_template"] = template
        items.append(item)
    return items


def load_edit_state(project: Project, candidates: dict[str, Any]) -> dict[str, Any]:
    if project.segment_edit_state_path.exists():
        return json.loads(project.segment_edit_state_path.read_text(encoding="utf-8"))
    items = _initial_items(project, candidates)
    state = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "items": items,
        "next_segment_index": max((int(item["segment_index"]) for item in items), default=-1) + 1,
        "undo": [],
        "redo": [],
    }
    save_edit_state(project, state)
    return state


def save_edit_state(project: Project, state: dict[str, Any]) -> None:
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    project.analysis_dir.mkdir(parents=True, exist_ok=True)
    project.segment_edit_state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def history_status(state: dict[str, Any]) -> dict[str, bool]:
    return {"can_undo": bool(state.get("undo")), "can_redo": bool(state.get("redo"))}


def _snapshot(
    project: Project,
    state: dict[str, Any],
    decisions: dict[str, Any],
    *,
    include_speaker_registry: bool = False,
) -> dict[str, Any]:
    snapshot = {
        "items": _deepcopy(state["items"]),
        "decisions": _deepcopy(decisions),
        "next_segment_index": int(state["next_segment_index"]),
    }
    if include_speaker_registry:
        snapshot["speaker_registry_names"] = read_speaker_registry(
            project.speaker_registry_path
        )
    return snapshot


def record_history(
    project: Project,
    state: dict[str, Any],
    decisions: dict[str, Any],
    *,
    include_speaker_registry: bool = False,
) -> None:
    state.setdefault("undo", []).append(
        _snapshot(
            project,
            state,
            decisions,
            include_speaker_registry=include_speaker_registry,
        )
    )
    state["undo"] = state["undo"][-HISTORY_LIMIT:]
    state["redo"] = []
    save_edit_state(project, state)


def _restore_snapshot(
    project: Project, state: dict[str, Any], snapshot: dict[str, Any]
) -> dict[str, Any]:
    state["items"] = _deepcopy(snapshot["items"])
    state["next_segment_index"] = int(snapshot["next_segment_index"])
    if "speaker_registry_names" in snapshot:
        write_speaker_registry(
            project.speaker_registry_path, snapshot["speaker_registry_names"]
        )
    return _deepcopy(snapshot["decisions"])


def undo(project: Project, candidates: dict[str, Any], decisions: dict[str, Any]) -> tuple[dict, dict]:
    state = load_edit_state(project, candidates)
    if not state.get("undo"):
        raise ValueError("元に戻せる区間操作がありません")
    include_registry = "speaker_registry_names" in state["undo"][-1]
    state.setdefault("redo", []).append(
        _snapshot(
            project,
            state,
            decisions,
            include_speaker_registry=include_registry,
        )
    )
    snapshot = state["undo"].pop()
    decisions = _restore_snapshot(project, state, snapshot)
    save_edit_state(project, state)
    return state, decisions


def redo(project: Project, candidates: dict[str, Any], decisions: dict[str, Any]) -> tuple[dict, dict]:
    state = load_edit_state(project, candidates)
    if not state.get("redo"):
        raise ValueError("やり直せる区間操作がありません")
    include_registry = "speaker_registry_names" in state["redo"][-1]
    state.setdefault("undo", []).append(
        _snapshot(
            project,
            state,
            decisions,
            include_speaker_registry=include_registry,
        )
    )
    snapshot = state["redo"].pop()
    decisions = _restore_snapshot(project, state, snapshot)
    save_edit_state(project, state)
    return state, decisions


def _map_boundary(source: str, destination: str, position: int) -> int:
    position = max(0, min(position, len(source)))
    opcodes = difflib.SequenceMatcher(None, source, destination, autojunk=False).get_opcodes()
    previous = 0
    for tag, source_start, source_end, dest_start, dest_end in opcodes:
        if source_start <= position <= source_end and source_end > source_start:
            if tag == "equal":
                return dest_start + (position - source_start)
            fraction = (position - source_start) / (source_end - source_start)
            return round(dest_start + fraction * (dest_end - dest_start))
        if position < source_start:
            return previous
        previous = dest_end
    return previous


def _split_ranges(ranges: list[dict[str, Any]], boundary: int) -> tuple[list[dict], list[dict]]:
    left, right = [], []
    for item in ranges or []:
        start, end = int(item.get("start", 0)), int(item.get("end", 0))
        if start < min(end, boundary):
            left.append({**item, "start": start, "end": min(end, boundary)})
        if max(start, boundary) < end:
            right.append({**item, "start": max(start, boundary) - boundary, "end": end - boundary})
    return left, right


def _split_metadata(item: dict[str, Any], left_text: str, right_text: str) -> tuple[dict, dict]:
    left, right = _deepcopy(item), _deepcopy(item)
    for key in ("exact_reference_terms",):
        values = item.get(key, [])
        left[key] = [value for value in values if value in left_text]
        right[key] = [value for value in values if value in right_text]
    for key in ("possible_reference_terms",):
        values = item.get(key, [])
        left[key] = [value for value in values if value.get("term", "") in left_text]
        right[key] = [value for value in values if value.get("term", "") in right_text]
    for key in ("applied_reference_terms",):
        values = item.get(key, [])
        left[key] = [value for value in values if value.get("to", "") in left_text or value.get("from", "") in left_text]
        right[key] = [value for value in values if value.get("to", "") in right_text or value.get("from", "") in right_text]
    return left, right


def _new_index(state: dict[str, Any]) -> int:
    value = int(state["next_segment_index"])
    state["next_segment_index"] = value + 1
    return value


def _effective_text(item: dict[str, Any], decision: dict[str, Any]) -> str:
    return str(decision.get("text") or item.get("suggested_text") or item.get("original_text") or "")


def _effective_speaker(item: dict[str, Any], decision: dict[str, Any]) -> str:
    return str(decision.get("speaker_name") or item.get("speaker_name") or "不明")


def split_segment(
    project: Project,
    candidates: dict[str, Any],
    decisions: dict[str, Any],
    segment_index: int,
    caret: int,
    current_text: str,
) -> tuple[dict, dict, list[int]]:
    state = load_edit_state(project, candidates)
    ordered = sorted(state["items"], key=lambda item: (float(item["start_seconds"]), int(item["segment_index"])))
    item = next((value for value in ordered if int(value["segment_index"]) == int(segment_index)), None)
    if item is None:
        raise ValueError("分割対象の区間が見つかりません")
    if not 0 < int(caret) < len(current_text):
        raise ValueError("文頭・文末では分割できません")
    words = item.get("_source_words", [])
    if len(words) < 2:
        raise ValueError("単語時刻が不足しているため分割できません")
    original_text = str(item.get("original_text", ""))
    source_text = "".join(str(word.get("text", "")) for word in words)
    if source_text != original_text:
        raise ValueError("区間本文と単語時刻情報が一致しません")
    mapped = _map_boundary(current_text, original_text, int(caret))
    boundaries = []
    position = 0
    for word_index, word in enumerate(words[:-1], start=1):
        position += len(str(word.get("text", "")))
        if position > 0:
            boundaries.append((abs(position - mapped), position, word_index))
    if not boundaries:
        raise ValueError("有効な単語境界がありません")
    _, original_boundary, word_index = min(boundaries, key=lambda value: (value[0], value[1]))
    left_words, right_words = words[:word_index], words[word_index:]
    split_time = float(right_words[0].get("start", item["start_seconds"]))
    start = float(item["start_seconds"])
    end = start + float(item["duration_seconds"])
    if not start < split_time < end:
        raise ValueError("分割時刻が区間内にありません")

    whisper_boundary = _map_boundary(original_text, str(item.get("whisper_text", "")), original_boundary)
    left, right = _split_metadata(item, current_text[:caret], current_text[caret:])
    left_index, right_index = _new_index(state), _new_index(state)
    common_sources = list(item.get("source_segment_indices", []))
    for child, index, uid_suffix, child_words, child_original, child_whisper, child_suggested, child_start, child_end in (
        (left, left_index, "a", left_words, original_text[:original_boundary], str(item.get("whisper_text", ""))[:whisper_boundary], current_text[:caret], start, split_time),
        (right, right_index, "b", right_words, original_text[original_boundary:], str(item.get("whisper_text", ""))[whisper_boundary:], current_text[caret:], split_time, end),
    ):
        child.update({
            "segment_index": index,
            "segment_uid": f"{item.get('segment_uid', segment_index)}:{uid_suffix}:{index}",
            "source_segment_indices": common_sources,
            "_source_words": _deepcopy(child_words),
            "original_text": child_original,
            "whisper_text": child_whisper,
            "suggested_text": child_suggested,
            "start_seconds": child_start,
            "duration_seconds": child_end - child_start,
            "premiere_start": _seconds_label(child_start),
            "premiere_end": _seconds_label(child_end),
        })
        ratio = difflib.SequenceMatcher(None, child_original, child_suggested, autojunk=False).ratio()
        child["change_score"] = 1.0 - ratio
        child["needs_review"] = child_original != child_suggested

    record_history(project, state, decisions)
    old_decision = decisions.pop(str(segment_index), {})
    left_ranges, right_ranges = _split_ranges(old_decision.get("proposal_highlights", []), int(caret))
    for child, child_text, ranges in ((left, current_text[:caret], left_ranges), (right, current_text[caret:], right_ranges)):
        if old_decision:
            decision = _deepcopy(old_decision)
            decision["segment_index"] = child["segment_index"]
            if "text" in decision or decision.get("status"):
                decision["text"] = child_text
            if ranges:
                decision["proposal_highlights"] = ranges
            else:
                decision.pop("proposal_highlights", None)
            decisions[str(child["segment_index"])] = decision
    state["items"] = [value for value in state["items"] if int(value["segment_index"]) != int(segment_index)] + [left, right]
    state["items"].sort(key=lambda value: (float(value["start_seconds"]), int(value["segment_index"])))
    save_edit_state(project, state)
    return state, decisions, [left_index, right_index]


def _merge_status(left: str, right: str) -> str:
    if left == right == "approved":
        return "approved"
    if "revising" in {left, right}:
        return "revising"
    if "hold" in {left, right}:
        return "hold"
    return ""


def _combine_unique(left: list, right: list) -> list:
    output = []
    seen = set()
    for item in [*left, *right]:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, dict) else str(item)
        if key not in seen:
            seen.add(key)
            output.append(_deepcopy(item))
    return output


def merge_with_next(
    project: Project,
    candidates: dict[str, Any],
    decisions: dict[str, Any],
    segment_index: int,
    current_text: str,
    next_text: str,
) -> tuple[dict, dict, int]:
    state = load_edit_state(project, candidates)
    ordered = sorted(state["items"], key=lambda item: (float(item["start_seconds"]), int(item["segment_index"])))
    position = next((index for index, item in enumerate(ordered) if int(item["segment_index"]) == int(segment_index)), -1)
    if position < 0 or position + 1 >= len(ordered):
        raise ValueError("次の区間がありません")
    left, right = ordered[position], ordered[position + 1]
    left_decision = decisions.get(str(left["segment_index"]), {})
    right_decision = decisions.get(str(right["segment_index"]), {})
    record_history(project, state, decisions)
    merged_index = _new_index(state)
    merged = _deepcopy(left)
    start = float(left["start_seconds"])
    end = float(right["start_seconds"]) + float(right["duration_seconds"])
    merged.update({
        "segment_index": merged_index,
        "segment_uid": f"merge:{left.get('segment_uid')}+{right.get('segment_uid')}:{merged_index}",
        "source_segment_indices": _combine_unique(left.get("source_segment_indices", []), right.get("source_segment_indices", [])),
        "_source_words": _deepcopy([*left.get("_source_words", []), *right.get("_source_words", [])]),
        "original_text": str(left.get("original_text", "")) + str(right.get("original_text", "")),
        "whisper_text": str(left.get("whisper_text", "")) + str(right.get("whisper_text", "")),
        "suggested_text": current_text + next_text,
        "start_seconds": start,
        "duration_seconds": end - start,
        "premiere_start": _seconds_label(start),
        "premiere_end": _seconds_label(end),
    })
    for key in ("applied_reference_terms", "exact_reference_terms", "possible_reference_terms", "identity_candidates", "verification_notes"):
        merged[key] = _combine_unique(left.get(key, []), right.get(key, []))
    ratio = difflib.SequenceMatcher(None, merged["original_text"], merged["suggested_text"], autojunk=False).ratio()
    merged["change_score"] = 1.0 - ratio
    merged["needs_review"] = merged["original_text"] != merged["suggested_text"]

    status = _merge_status(str(left_decision.get("status", "")), str(right_decision.get("status", "")))
    merged_decision = {
        "segment_index": merged_index,
        "speaker_name": _effective_speaker(left, left_decision),
        "speaker_original": left.get("speaker_name"),
        "speaker_scope": "segment",
        "text": current_text + next_text,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if status:
        merged_decision["status"] = status
    left_ranges = left_decision.get("proposal_highlights", [])
    right_ranges = [
        {**item, "start": int(item["start"]) + len(current_text), "end": int(item["end"]) + len(current_text)}
        for item in right_decision.get("proposal_highlights", [])
    ]
    if left_ranges or right_ranges:
        merged_decision["proposal_highlights"] = [*_deepcopy(left_ranges), *right_ranges]
    decisions.pop(str(left["segment_index"]), None)
    decisions.pop(str(right["segment_index"]), None)
    decisions[str(merged_index)] = merged_decision
    state["items"] = [
        item for item in state["items"]
        if int(item["segment_index"]) not in {int(left["segment_index"]), int(right["segment_index"])}
    ] + [merged]
    state["items"].sort(key=lambda value: (float(value["start_seconds"]), int(value["segment_index"])))
    save_edit_state(project, state)
    return state, decisions, merged_index


def validate_structural_items(transcript: dict[str, Any], items: list[dict[str, Any]]) -> None:
    source_words = [
        word
        for segment in transcript.get("segments", [])
        for word in segment.get("words", [])
    ]
    edited_words = [word for item in items for word in item.get("_source_words", [])]
    if edited_words != source_words:
        raise ValueError("分割・結合後の単語時刻情報に欠落または重複があります")
    previous_start = float("-inf")
    for item in sorted(items, key=lambda value: float(value["start_seconds"])):
        start = float(item["start_seconds"])
        duration = float(item["duration_seconds"])
        if duration <= 0 or start < previous_start:
            raise ValueError("分割・結合後の区間時刻が不正です")
        previous_start = start
