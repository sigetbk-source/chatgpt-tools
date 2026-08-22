#!/usr/bin/env python3
"""Render a validated Hawks championship-magic step chart."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "hawks-magic-chart-mpl"))

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.image as mpimg
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.offsetbox import AnnotationBbox, OffsetImage


WIDTH = 2880
HEIGHT = 1620
DPI = 200
SUPPORTED_OPPONENTS = {"F", "L", "M", "E", "B"}


class InputError(ValueError):
    """Raised when chart input is unsafe to render."""


def load_and_validate(path: Path, assets_dir: Path) -> tuple[str, list[dict[str, Any]]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"入力 JSON を読めません: {exc}") from exc

    if not isinstance(payload, dict):
        raise InputError("入力の最上位はオブジェクトにしてください")
    title = payload.get("title")
    entries = payload.get("entries")
    if not isinstance(title, str) or not title.strip():
        raise InputError("title は空でない文字列にしてください")
    if not isinstance(entries, list) or not entries:
        raise InputError("entries は空でない配列にしてください")

    parsed: list[dict[str, Any]] = []
    previous_date: date | None = None
    previous_magic: int | None = None

    for index, raw in enumerate(entries):
        if not isinstance(raw, dict):
            raise InputError(f"entries[{index}] はオブジェクトにしてください")
        try:
            entry_date = date.fromisoformat(raw["date"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InputError(f"entries[{index}].date は ISO 日付にしてください") from exc
        magic = raw.get("magic")
        opponent = raw.get("opponent")
        if isinstance(magic, bool) or not isinstance(magic, int) or magic < 0:
            raise InputError(f"entries[{index}].magic は 0 以上の整数にしてください")
        if previous_date is not None and entry_date != previous_date + timedelta(days=1):
            raise InputError("entries の日付は重複なしで1日ずつ連続させてください")
        if previous_magic is not None and magic > previous_magic:
            raise InputError("マジックは前日から増やせません")
        if opponent is not None:
            if opponent not in SUPPORTED_OPPONENTS:
                raise InputError(f"未対応の対戦相手コードです: {opponent!r}")
            if not (assets_dir / f"{opponent}.png").is_file():
                raise InputError(f"固定ロゴがありません: assets/{opponent}.png")
        parsed.append({"date": entry_date, "magic": magic, "opponent": opponent})
        previous_date = entry_date
        previous_magic = magic

    if not (assets_dir / "hawks_sh.png").is_file():
        raise InputError("固定ロゴがありません: assets/hawks_sh.png")
    return title.strip(), parsed


def add_logo(ax: Any, path: Path, x: Any, y: float, zoom: float, coordinates: str = "data") -> None:
    image = OffsetImage(mpimg.imread(path), zoom=zoom)
    box = AnnotationBbox(image, (x, y), xycoords=coordinates, frameon=False, pad=0)
    ax.add_artist(box)


def render(title: str, entries: list[dict[str, Any]], assets_dir: Path, output: Path) -> None:
    dates = [entry["date"] for entry in entries]
    values = [entry["magic"] for entry in entries]
    minimum = min(values)
    maximum = max(values)

    available_fonts = {font.name for font in font_manager.fontManager.ttflist}
    font_family = next(
        (name for name in ("Hiragino Sans", "Yu Gothic", "Noto Sans CJK JP") if name in available_fonts),
        "sans-serif",
    )
    plt.rcParams.update({
        "font.family": font_family,
        "axes.unicode_minus": False,
    })
    fig, ax = plt.subplots(figsize=(WIDTH / DPI, HEIGHT / DPI), dpi=DPI)
    fig.patch.set_facecolor("#fffdf6")
    ax.set_facecolor("#fffdf6")

    ax.step(dates, values, where="post", color="#111111", linewidth=4.5, zorder=3)
    ax.scatter(dates, values, s=95, color="#f8cf21", edgecolor="#111111", linewidth=2, zorder=4)

    for entry in entries:
        ax.annotate(
            f"M{entry['magic']}",
            (entry["date"], entry["magic"]),
            xytext=(0, 14),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=13,
            fontweight="bold",
            color="#111111",
            zorder=5,
        )
        if entry["opponent"] is not None:
            add_logo(
                ax,
                assets_dir / f"{entry['opponent']}.png",
                entry["date"],
                entry["magic"] - 0.95,
                0.048,
            )

    ax.set_xlim(dates[0] - timedelta(days=1.0), dates[-1] + timedelta(days=1.0))
    ax.set_ylim(max(0, minimum - 3.2), maximum + 3.4)
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%-m/%-d"))
    ax.tick_params(axis="x", labelsize=12, length=0, pad=12)
    ax.tick_params(axis="y", labelsize=11, length=0)
    ax.grid(axis="y", color="#d9d4c5", linewidth=1, alpha=0.75)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_ylabel("優勝マジック", fontsize=13, fontweight="bold")

    subtitle = f"{dates[0].year}/{dates[0].month}/{dates[0].day} M{values[0]}点灯 → {dates[-1].month}/{dates[-1].day} M{values[-1]}"
    fig.text(0.145, 0.935, title, fontsize=27, fontweight="bold", color="#111111", va="center")
    fig.text(0.145, 0.893, subtitle, fontsize=15, color="#4b4b4b", va="center")
    add_logo(ax, assets_dir / "hawks_sh.png", 0.035, 1.105, 0.075, coordinates="axes fraction")

    fig.subplots_adjust(left=0.075, right=0.975, bottom=0.13, top=0.80)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=DPI, facecolor=fig.get_facecolor(), metadata={"Software": "hawks-magic-chart"})
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="input JSON")
    parser.add_argument("output", type=Path, help="output PNG")
    args = parser.parse_args()
    assets_dir = Path(__file__).resolve().parent / "assets"
    try:
        title, entries = load_and_validate(args.input, assets_dir)
        render(title, entries, assets_dir, args.output)
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
