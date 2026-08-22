---
name: hawks-magic-chart
description: Render or update a Fukuoka SoftBank Hawks championship-magic progression chart as a deterministic 16:9 PNG. Use when the user asks for a ホークスマジックグラフ, 優勝マジック推移, today-through update, X-post chart, or a refreshed chart with opponent logos.
---

# Hawks Magic Chart

1. Read `README.md` for the canonical input, rendering, validation, and output specification.
2. Verify every new date, magic number, and opponent before editing the input JSON. Preserve off days with `opponent: null`.
3. Reuse the fixed files in `assets/`; do not synthesize or replace logos unless the user explicitly requests an asset change.
4. Run `render_magic_chart.py` from the skill directory and verify that the PNG is 2880 × 1620.
5. Report the covered date, final magic number, data verification state, and output path separately.
