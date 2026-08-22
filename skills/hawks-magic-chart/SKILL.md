---
name: hawks-magic-chart
description: Create or update a fixed 16:9 PNG chart of the Fukuoka SoftBank Hawks pennant-race magic number. Use when the user asks for a Hawks magic-number trend graph, requests that today's magic be added, or needs an X-ready static chart with opponent badges.
---

# Hawks Magic Chart

Generate the chart deterministically from verified daily data. Do not edit a previous PNG and do not use image generation to alter numbers.

## Workflow

1. Verify the magic number for every date from the lighting date through the requested end date. Prefer Hawks official and NPB sources; use a secondary source only to cross-check gaps.
2. Include every calendar date. On a no-game day, carry the previous magic number forward and set `opponent` to `null`.
3. Record game-day opponents with the fixed codes `F`, `L`, `M`, `E`, or `B`.
4. Preserve the invariant that the magic number never increases. Stop and re-check the source if input data violates it.
5. Update a JSON file shaped like `example_2026-08-20.json`.
6. Install `requirements.txt`, then render:

```bash
python3 render_magic_chart.py example_2026-08-20.json hawks_magic_2026-08-20.png
```

7. Verify that the PNG is exactly 2880 x 1620, the first point has no incoming line, plateaus are horizontal, decreases are vertical, no-game dates have no badge, and all labels match the JSON.

## Fixed visual contract

- Title: `’26 福岡ソフトバンクホークス 優勝マジック推移`
- Static 16:9 PNG for posting to X
- Step line with square direction changes
- One `M<number>` label per date
- Opponent badge directly below the corresponding game-day line
- Fixed PNG assets from `assets/`; never substitute font-drawn team marks
- Same-size opponent circles; M, E, and B include optical centering corrections
- `hawks_sh.png` sits to the left of the title

Read `assets/SOURCE.md` before redistributing the logo assets outside this private skill repository.
