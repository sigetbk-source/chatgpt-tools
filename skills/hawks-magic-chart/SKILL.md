---
name: hawks-magic-chart
description: Create or update a fixed 16:9 PNG chart of the Fukuoka SoftBank Hawks pennant-race magic number. Use when the user asks for a Hawks magic-number trend graph, requests that today's magic be added, or needs an X-ready static chart with opponent badges.
---

# Hawks Magic Chart

1. Read `README.md` for the canonical input, rendering, validation, and output specification.
2. Verify every new date, magic number, and opponent before editing JSON. Preserve off days with `opponent: null`.
3. Reuse `assets/` without substituting font-drawn or newly generated logos. Read `assets/SOURCE.md` before redistribution.
4. Run `render_magic_chart.py` and verify that the PNG is exactly 2880 × 1620 and matches the JSON.
5. Report the covered date, final magic number, verification state, and output path separately.
