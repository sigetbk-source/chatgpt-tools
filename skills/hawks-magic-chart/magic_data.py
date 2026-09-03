"""Validation and state-transition logic for daily magic-number data."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal


MagicEvent = Literal["lit", "extinguished", "relit", "champion"]


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def derive_events(points: list[dict[str, Any]]) -> dict[int, MagicEvent]:
    events: dict[int, MagicEvent] = {}
    has_been_lit = False
    previous_magic: int | None = None
    for index, point in enumerate(points):
        magic = point["magic"]
        if magic == 0 and previous_magic != 0:
            events[index] = "champion"
            has_been_lit = True
        elif magic is not None and previous_magic is None:
            events[index] = "relit" if has_been_lit else "lit"
            has_been_lit = True
        elif magic is None and previous_magic is not None:
            events[index] = "extinguished"
        previous_magic = magic
    return events


def display_magic_values(points: list[dict[str, Any]]) -> list[int]:
    """Carry the last active value only for vertical placement during extinction."""
    values: list[int] = []
    last_active_magic: int | None = None
    for point in points:
        if point["magic"] is not None:
            last_active_magic = point["magic"]
        if last_active_magic is None:
            raise ValueError("the chart must begin with an active magic number")
        values.append(last_active_magic)
    return values


def load_input(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return validate_payload(payload)


def validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    points = payload.get("data")
    if not isinstance(points, list) or not points:
        raise ValueError("input must contain a non-empty data array")
    if not isinstance(points[0], dict) or points[0].get("magic") is None:
        raise ValueError("the chart must begin with an active magic number")

    previous_date: date | None = None
    previous_magic: int | None = None
    active_segment = False
    last_magic_before_extinction: int | None = None
    has_been_lit = False
    has_numeric_magic = False
    champion_clinched = False
    supported = {"F", "L", "M", "E", "B", None}
    for index, point in enumerate(points):
        if not isinstance(point, dict):
            raise ValueError(f"data[{index}] must be an object")
        if "date" not in point or "magic" not in point:
            raise ValueError(f"data[{index}] must contain date and magic")
        point_date = parse_date(point["date"])
        magic = point["magic"]
        opponent = point.get("opponent")
        if magic is not None and (not isinstance(magic, int) or isinstance(magic, bool) or magic < 0):
            raise ValueError(f"data[{index}].magic must be null or a non-negative integer")
        if opponent not in supported:
            raise ValueError(f"data[{index}].opponent is unsupported: {opponent!r}")
        if previous_date is not None and point_date != previous_date + timedelta(days=1):
            raise ValueError("data must include every calendar date exactly once and without gaps")
        if champion_clinched and magic != 0:
            raise ValueError("after M0, later entries must remain M0 or be omitted")

        if magic is None:
            if active_segment:
                last_magic_before_extinction = previous_magic
            active_segment = False
        else:
            has_numeric_magic = True
            if not active_segment and has_been_lit:
                if last_magic_before_extinction is None or magic >= last_magic_before_extinction:
                    raise ValueError("relit magic must be lower than the value immediately before extinction")
            if active_segment and previous_magic is not None and magic > previous_magic:
                raise ValueError("magic must not increase within one active segment")
            active_segment = True
            has_been_lit = True
            if magic == 0:
                champion_clinched = True

        previous_date = point_date
        previous_magic = magic

    if not has_numeric_magic:
        raise ValueError("data must contain at least one active magic number")
    return payload
