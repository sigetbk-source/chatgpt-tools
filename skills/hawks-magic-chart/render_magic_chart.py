#!/usr/bin/env python3
"""Render the fixed 16:9 Hawks pennant-magic step chart from JSON data."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


WIDTH = 2880
HEIGHT = 1620
BACKGROUND = "#FFF9E9"
HAWKS_YELLOW = "#F4C400"
INK = "#171717"
GRID = "#D9D2BE"
ASSET_DIR = Path(__file__).resolve().parent / "assets"
FONT_CANDIDATES = (
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
)


def font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in FONT_CANDIDATES:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    raise FileNotFoundError("No supported Japanese-capable font was found")


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
        if not isinstance(magic, int) or magic < 0:
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


def paste_contained(canvas: Image.Image, asset: Path, box: tuple[int, int, int, int]) -> None:
    image = Image.open(asset).convert("RGBA")
    left, top, right, bottom = box
    ratio = min((right - left) / image.width, (bottom - top) / image.height)
    image = image.resize(
        (max(1, round(image.width * ratio)), max(1, round(image.height * ratio))),
        Image.Resampling.LANCZOS,
    )
    x = left + ((right - left) - image.width) // 2
    y = top + ((bottom - top) - image.height) // 2
    canvas.alpha_composite(image, (x, y))


def centered_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, face: ImageFont.FreeTypeFont, fill: str) -> None:
    box = draw.textbbox((0, 0), text, font=face)
    draw.text((xy[0] - (box[2] - box[0]) / 2, xy[1] - (box[3] - box[1]) / 2), text, font=face, fill=fill)


def render(payload: dict[str, Any], output: Path) -> None:
    points = payload["data"]
    magic_values = [point["magic"] for point in points]
    dates = [parse_date(point["date"]) for point in points]

    canvas = Image.new("RGBA", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(canvas)

    paste_contained(canvas, ASSET_DIR / "hawks_sh.png", (105, 68, 250, 213))
    title = "’26 福岡ソフトバンクホークス 優勝マジック推移"
    draw.text((280, 74), title, font=font(76), fill=INK)
    start_label = f"{dates[0].year}/{dates[0].month}/{dates[0].day} M{magic_values[0]}点灯"
    end_label = f"{dates[-1].month}/{dates[-1].day} M{magic_values[-1]}"
    draw.text((284, 177), f"{start_label} → {end_label}", font=font(43), fill="#4B4B4B")
    draw.rounded_rectangle((105, 265, 2775, 282), radius=8, fill=HAWKS_YELLOW)

    chart_left, chart_top, chart_right, chart_bottom = 185, 390, 2735, 1290
    y_min = max(0, min(magic_values) - 3)
    y_max = max(magic_values) + 3
    if y_max == y_min:
        y_max += 1

    def px(index: int) -> float:
        if len(points) == 1:
            return (chart_left + chart_right) / 2
        return chart_left + index * (chart_right - chart_left) / (len(points) - 1)

    def py(value: int) -> float:
        return chart_top + (y_max - value) * (chart_bottom - chart_top) / (y_max - y_min)

    tick_start = ((y_min + 4) // 5) * 5
    for tick in range(tick_start, y_max + 1, 5):
        y = round(py(tick))
        draw.line((chart_left, y, chart_right, y), fill=GRID, width=3)
        draw.text((82, y - 27), f"M{tick}", font=font(36), fill="#696458")

    draw.line((chart_left, chart_bottom, chart_right, chart_bottom), fill="#877F6C", width=4)

    line_points: list[tuple[float, float]] = []
    for index, magic in enumerate(magic_values):
        x, y = px(index), py(magic)
        if index == 0:
            line_points.append((x, y))
        else:
            previous_y = py(magic_values[index - 1])
            line_points.extend(((x, previous_y), (x, y)))
    if len(line_points) > 1:
        draw.line(line_points, fill=INK, width=12)

    for index, point in enumerate(points):
        x, y = px(index), py(point["magic"])
        draw.ellipse((x - 17, y - 17, x + 17, y + 17), fill=HAWKS_YELLOW, outline=INK, width=5)
        if index > 0 and point["magic"] < points[index - 1]["magic"]:
            label_x = x - 54 if index == len(points) - 1 else x + 54
            label_y = y - 32
        else:
            label_x = x
            label_y = y - 62
        centered_text(draw, (round(label_x), round(label_y)), f"M{point['magic']}", font(35), INK)

        opponent = point.get("opponent")
        if opponent:
            badge_top = min(round(y + 76), chart_bottom - 106)
            paste_contained(
                canvas,
                ASSET_DIR / f"{opponent}.png",
                (round(x - 46), badge_top, round(x + 46), badge_top + 92),
            )

        date_label = f"{dates[index].month}/{dates[index].day}"
        centered_text(draw, (round(x), 1368), date_label, font(34), "#4B4B4B")

    footer = payload.get("footer", "試合なしの日は直前のマジックを持ち越し。対戦相手ロゴは試合日のみ表示。")
    draw.text((185, 1470), footer, font=font(31), fill="#6C665A")
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output, format="PNG", optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="input JSON")
    parser.add_argument("output", type=Path, help="output PNG")
    args = parser.parse_args()
    render(load_input(args.input), args.output)


if __name__ == "__main__":
    main()
