# Logo and state-icon asset sources

The fixed team assets in this directory implement the visual design approved from the finished 2880×1620 sample supplied by the user on 2026-08-23.

Team-logo sources and normalization:

- `hawks_sh.png`: reconstructed from [Fukuoka SoftBank Hawks insignia.svg](https://commons.wikimedia.org/wiki/File:Fukuoka_SoftBank_Hawks_insignia.svg) and kept on a transparent 256×256 canvas.
- `F.png`, `L.png`, `M.png`, and `E.png`: recovered from the user-supplied approved finished sample so the badge shapes, colors, and proportions match that sample.
- `B.png`: the gold monogram originated from [Orix Buffaloes insignia.svg](https://commons.wikimedia.org/wiki/File:Orix_Buffaloes_insignia.svg). It is normalized as a solid Buffaloes navy circle at the same visible diameter as F/L/M/E, with the reduced gold B geometrically centered inside it.

Fixed team rendering rules:

- preserve the approved circle colors and monograms;
- keep F/L/M/E/B at the same visible circle diameter;
- keep each monogram contained within its circle;
- do not redraw a badge with text or a substitute font;
- reuse these PNG files unchanged unless the user explicitly approves an asset revision;
- do not substitute the similarly named assets from the rejected cream-background renderer, especially its differently normalized `B.png`.

The following state icons are original deterministic drawings generated locally by `scripts/create_event_icons.py`:

- `magic_lit.png`
- `magic_extinguished.png`
- `magic_relit.png`
- `magic_champion.png`

The renderer loads these fixed PNGs from `assets/` and places them in the same 88px drawing box used for opponent badges. Do not hand-edit generated state PNGs; change `event_icons.py`, run the generator, and verify the rendered chart instead.

The team marks may be protected trademarks even where a source page describes a simple text logo as public domain. Keep this repository private and confirm the relevant trademark and usage rules before commercial redistribution.
