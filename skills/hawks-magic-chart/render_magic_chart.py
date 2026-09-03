#!/usr/bin/env python3
"""Render the Hawks magic chart in the approved 2026-08-20 sample style."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from magic_data import derive_events, display_magic_values, load_input, parse_date


WIDTH, HEIGHT = 2880, 1620
WHITE = "#FFFFFF"
BLACK = "#111111"
HAWKS_YELLOW = "#F7B900"
GRID = "#D8D8D8"
MUTED = "#707070"
BADGE_SIZE = 88
ASSET_DIR = Path(__file__).resolve().parent / "assets"
EVENT_LABELS = {
    "lit": "点灯",
    "extinguished": "消滅",
    "relit": "再点灯",
    "champion": "優勝決定",
}


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


def paste_event_icon(canvas: Image.Image, kind: str, x: int, y: int) -> None:
    paste_contained(
        canvas,
        ASSET_DIR / f"magic_{kind}.png",
        (x, y, x + BADGE_SIZE, y + BADGE_SIZE),
    )


def centered(draw: ImageDraw.ImageDraw, x: float, y: float, text: str, font: ImageFont.FreeTypeFont, fill: str) -> None:
    bounds = draw.textbbox((0, 0), text, font=font)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    draw.text((x - width / 2, y - height / 2 - bounds[1]), text, font=font, fill=fill)


def summary_text(points: list[dict[str, Any]]) -> str:
    dates = [parse_date(item["date"]) for item in points]
    first_magic = next(item["magic"] for item in points if item["magic"] is not None)
    final_magic = points[-1]["magic"]
    final = "消滅中" if final_magic is None else f"M{final_magic}"
    return (
        f"{dates[0].year}/{dates[0].month}/{dates[0].day} M{first_magic}点灯"
        f"  →  {dates[-1].month}/{dates[-1].day} {final}"
    )


def render(payload: dict[str, Any], output_path: Path) -> None:
    points = payload["data"]
    dates = [parse_date(item["date"]) for item in points]
    numeric_values = [item["magic"] for item in points if item["magic"] is not None]
    display_values = display_magic_values(points)
    events = derive_events(points)

    canvas = Image.new("RGBA", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(canvas)

    # Preserve the approved header, title typography, and double rule.
    paste_contained(canvas, ASSET_DIR / "hawks_sh.png", (72, 20, 270, 210))
    short_year = str(dates[0].year)[-2:]
    title = payload.get("title", f"’{short_year} 福岡ソフトバンクホークス 優勝マジック推移")
    draw.text((320, 58), title, font=face(72), fill=BLACK)
    draw.rectangle((320, 165, 2710, 174), fill=BLACK)
    draw.rectangle((320, 183, 2710, 190), fill=HAWKS_YELLOW)

    summary = summary_text(points)
    summary_font = face(46)
    summary_bounds = draw.textbbox((0, 0), summary, font=summary_font)
    summary_width = summary_bounds[2] - summary_bounds[0]
    pill_left = (WIDTH - summary_width) // 2 - 28
    pill_right = (WIDTH + summary_width) // 2 + 28
    draw.rounded_rectangle((pill_left, 206, pill_right, 294), radius=22, fill=BLACK)
    centered(draw, WIDTH / 2, 250, summary, summary_font, WHITE)

    chart_left, chart_top, chart_right, chart_bottom = 215, 345, 2780, 1425
    first_x, last_x = 290, 2730
    y_tick_min = max(0, min(numeric_values) - 1)
    y_tick_max = max(numeric_values) + 1
    # Fifty pixels of extra headroom is the only geometry adjustment. It keeps
    # the initial state icon above the first value without touching the pill.
    plot_top = 400
    plot_bottom = 1352

    def x_at(index: int) -> float:
        if len(points) == 1:
            return (first_x + last_x) / 2
        return first_x + index * (last_x - first_x) / (len(points) - 1)

    def y_at(value: int) -> float:
        return plot_top + (y_tick_max - value) * (plot_bottom - plot_top) / (y_tick_max - y_tick_min)

    # Preserve the approved one-unit dotted grid and plain numeric y-axis.
    draw.text((96, 268), "マジック", font=face(34), fill=BLACK)
    for tick in range(y_tick_min, y_tick_max + 1):
        y = round(y_at(tick))
        for dash_left in range(chart_left, chart_right, 18):
            draw.line((dash_left, y, min(dash_left + 10, chart_right), y), fill=GRID, width=2)
        centered(draw, 184, y, str(tick), face(29, bold=False), BLACK)
    draw.line((chart_left, chart_top, chart_left, chart_bottom), fill=BLACK, width=4)
    draw.line((chart_left, chart_bottom, chart_right, chart_bottom), fill=BLACK, width=4)

    # Draw each active interval as an independent approved yellow step line.
    segment: list[tuple[float, float]] = []
    previous_magic: int | None = None
    for index, point in enumerate(points):
        magic = point["magic"]
        if magic is None:
            if len(segment) > 1:
                draw.line(segment, fill=HAWKS_YELLOW, width=8)
            segment = []
            previous_magic = None
            continue
        x, y = x_at(index), y_at(magic)
        if previous_magic is None:
            segment = [(x, y)]
        else:
            segment.extend(((x, y_at(previous_magic)), (x, y)))
        previous_magic = magic
    if len(segment) > 1:
        draw.line(segment, fill=HAWKS_YELLOW, width=8)

    number_font = face(31)
    date_font = face(29, bold=False)
    event_number_y: dict[int, float] = {}
    for index, point in enumerate(points):
        x = x_at(index)
        magic = point["magic"]
        display_magic = display_values[index]
        y = y_at(display_magic)

        if magic is not None:
            draw.ellipse((x - 10, y - 10, x + 10, y + 10), fill=BLACK)
            centered(draw, x, y - 42, str(magic), number_font, BLACK)
            event_number_y[index] = y - 42
        elif events.get(index) == "extinguished":
            centered(draw, x, y - 42, str(display_magic), number_font, MUTED)
            event_number_y[index] = y - 42

        opponent = point.get("opponent")
        if opponent:
            badge_half = BADGE_SIZE // 2
            paste_contained(
                canvas,
                ASSET_DIR / f"{opponent}.png",
                (
                    round(x - badge_half),
                    round(y + 68 - badge_half),
                    round(x + badge_half),
                    round(y + 68 + badge_half),
                ),
            )

        centered(draw, x, 1465, f"{dates[index].month}/{dates[index].day}", date_font, BLACK)

    # State icons use the same 88px drawing box as the original opponent badges.
    for index, event in events.items():
        x = round(x_at(index))
        number_y = event_number_y[index]
        icon_left = x - BADGE_SIZE // 2
        icon_top = round(number_y - BADGE_SIZE - 14)
        paste_event_icon(canvas, event, icon_left, icon_top)
        draw.text((icon_left + BADGE_SIZE + 8, icon_top + 27), EVENT_LABELS[event], font=face(25), fill=BLACK)

    centered(draw, (chart_left + chart_right) / 2, 1520, "日付", face(35, bold=False), BLACK)
    if payload.get("simulation") is True:
        centered(draw, WIDTH / 2, 1570, "※ 動作確認用ダミー（実績値ではありません）", face(27), "#982747")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, format="PNG", optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="input JSON")
    parser.add_argument("output", type=Path, help="output PNG")
    args = parser.parse_args()
    render(load_input(args.input), args.output)


if __name__ == "__main__":
    main()
