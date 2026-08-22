#!/usr/bin/env python3
"""Render the Hawks magic chart in the approved 2026-08-20 sample style."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


WIDTH, HEIGHT = 2880, 1620
WHITE = "#FFFFFF"
BLACK = "#111111"
HAWKS_YELLOW = "#F7B900"
GRID = "#D8D8D8"
ASSET_DIR = Path(__file__).resolve().parent / "assets"


def _font_path() -> Path:
    candidates = list(Path("/System/Library/Fonts").glob("*W8.ttc")) + [
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("Japanese-capable font not found")


FONT_PATH = _font_path()
REGULAR_FONT_PATH = Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf")


def face(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    path = FONT_PATH if bold or not REGULAR_FONT_PATH.exists() else REGULAR_FONT_PATH
    return ImageFont.truetype(str(path), size=size)


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def load_input(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    points = payload.get("data")
    if not isinstance(points, list) or not points:
        raise ValueError("input must contain a non-empty data array")

    previous_date: date | None = None
    previous_magic: int | None = None
    supported = {"F", "L", "M", "E", "B", None}
    for index, point in enumerate(points):
        if not isinstance(point, dict):
            raise ValueError(f"data[{index}] must be an object")
        point_date = parse_date(point["date"])
        magic = point["magic"]
        opponent = point.get("opponent")
        if not isinstance(magic, int) or isinstance(magic, bool) or magic < 0:
            raise ValueError(f"data[{index}].magic must be a non-negative integer")
        if opponent not in supported:
            raise ValueError(f"data[{index}].opponent is unsupported: {opponent!r}")
        if previous_date is not None and point_date <= previous_date:
            raise ValueError("dates must be strictly increasing")
        if previous_date is not None and point_date != previous_date + timedelta(days=1):
            raise ValueError("data must include every calendar date without gaps")
        if previous_magic is not None and magic > previous_magic:
            raise ValueError("pennant magic must never increase")
        if opponent is None and previous_magic is not None and magic != previous_magic:
            raise ValueError("a no-game date must carry the previous magic number forward")
        previous_date = point_date
        previous_magic = magic
    return payload


def paste_contained(canvas: Image.Image, path: Path, box: tuple[int, int, int, int]) -> None:
    asset = Image.open(path).convert("RGBA")
    left, top, right, bottom = box
    scale = min((right - left) / asset.width, (bottom - top) / asset.height)
    asset = asset.resize(
        (round(asset.width * scale), round(asset.height * scale)),
        Image.Resampling.LANCZOS,
    )
    x = left + ((right - left) - asset.width) // 2
    y = top + ((bottom - top) - asset.height) // 2
    canvas.alpha_composite(asset, (x, y))


def centered(draw: ImageDraw.ImageDraw, x: float, y: float, text: str, font: ImageFont.FreeTypeFont, fill: str) -> None:
    bounds = draw.textbbox((0, 0), text, font=font)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    draw.text((x - width / 2, y - height / 2 - bounds[1]), text, font=font, fill=fill)


def render(payload: dict[str, Any], output_path: Path) -> None:
    points = payload["data"]
    dates = [parse_date(item["date"]) for item in points]
    values = [item["magic"] for item in points]

    canvas = Image.new("RGBA", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(canvas)

    # Header: approved SH placement, bold title, and black/yellow double rule.
    paste_contained(canvas, ASSET_DIR / "hawks_sh.png", (72, 20, 270, 210))
    draw.text((320, 58), "’26 福岡ソフトバンクホークス 優勝マジック推移", font=face(72), fill=BLACK)
    draw.rectangle((320, 165, 2710, 174), fill=BLACK)
    draw.rectangle((320, 183, 2710, 190), fill=HAWKS_YELLOW)

    summary = (
        f"{dates[0].year}/{dates[0].month}/{dates[0].day} M{values[0]}点灯"
        f"  →  {dates[-1].month}/{dates[-1].day} M{values[-1]}"
    )
    summary_font = face(46)
    summary_bounds = draw.textbbox((0, 0), summary, font=summary_font)
    summary_width = summary_bounds[2] - summary_bounds[0]
    pill_left = (WIDTH - summary_width) // 2 - 28
    pill_right = (WIDTH + summary_width) // 2 + 28
    draw.rounded_rectangle((pill_left, 206, pill_right, 294), radius=22, fill=BLACK)
    centered(draw, WIDTH / 2, 250, summary, summary_font, WHITE)

    chart_left, chart_top, chart_right, chart_bottom = 215, 345, 2780, 1425
    first_x, last_x = 290, 2730
    y_tick_min = max(0, min(values) - 1)
    y_tick_max = max(values) + 1
    plot_top = 350
    plot_bottom = 1352

    def x_at(index: int) -> float:
        if len(points) == 1:
            return (first_x + last_x) / 2
        return first_x + index * (last_x - first_x) / (len(points) - 1)

    def y_at(value: int) -> float:
        return plot_top + (y_tick_max - value) * (plot_bottom - plot_top) / (y_tick_max - y_tick_min)

    # Axis labels and dotted one-unit grid match the approved sample.
    draw.text((96, 268), "マジック", font=face(34), fill=BLACK)
    for tick in range(y_tick_min, y_tick_max + 1):
        y = round(y_at(tick))
        for dash_left in range(chart_left, chart_right, 18):
            draw.line((dash_left, y, min(dash_left + 10, chart_right), y), fill=GRID, width=2)
        centered(draw, 184, y, str(tick), face(29, bold=False), BLACK)
    draw.line((chart_left, chart_top, chart_left, chart_bottom), fill=BLACK, width=4)
    draw.line((chart_left, chart_bottom, chart_right, chart_bottom), fill=BLACK, width=4)

    # Yellow step line begins at the lighting date; no segment is drawn to its left.
    step: list[tuple[float, float]] = [(x_at(0), y_at(values[0]))]
    for index in range(1, len(points)):
        x = x_at(index)
        step.extend(((x, y_at(values[index - 1])), (x, y_at(values[index]))))
    draw.line(step, fill=HAWKS_YELLOW, width=8)

    number_font = face(31)
    date_font = face(29, bold=False)
    for index, point in enumerate(points):
        x, y = x_at(index), y_at(point["magic"])
        draw.ellipse((x - 10, y - 10, x + 10, y + 10), fill=BLACK)
        centered(draw, x, y - 42, str(point["magic"]), number_font, BLACK)

        opponent = point.get("opponent")
        if opponent:
            badge_path = ASSET_DIR / f"{opponent}.png"
            badge_half = 44
            paste_contained(
                canvas,
                badge_path,
                (
                    round(x - badge_half),
                    round(y + 68 - badge_half),
                    round(x + badge_half),
                    round(y + 68 + badge_half),
                ),
            )

        centered(draw, x, 1465, f"{dates[index].month}/{dates[index].day}", date_font, BLACK)

    centered(draw, (chart_left + chart_right) / 2, 1520, "日付", face(35, bold=False), BLACK)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, format="PNG", optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    render(load_input(args.input), args.output)


if __name__ == "__main__":
    main()

