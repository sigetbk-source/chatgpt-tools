#!/usr/bin/env python3
"""Export the renderer's deterministic magic-state icons as PNG assets."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from event_icons import make_event_icon  # noqa: E402


def main() -> None:
    output = ROOT / "assets"
    output.mkdir(parents=True, exist_ok=True)
    for kind in ("lit", "extinguished", "relit"):
        make_event_icon(kind).save(output / f"magic_{kind}.png")


if __name__ == "__main__":
    main()
