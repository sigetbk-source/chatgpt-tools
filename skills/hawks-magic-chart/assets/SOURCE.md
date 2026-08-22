# Logo asset sources

The original ChatGPT conversation fixed the visual rule of reusing PNG logo assets instead of drawing logo-like letters with fonts. The prior attachment bytes were not exposed by the referenced-task API, so the files in this repository were reconstructed from vector insignia sources on Wikimedia Commons and normalized into the confirmed fixed-asset layout.

Sources retrieved on 2026-08-23:

- `hawks_sh.png`: [Fukuoka SoftBank Hawks insignia.svg](https://commons.wikimedia.org/wiki/File:Fukuoka_SoftBank_Hawks_insignia.svg)
- `F.png`: [Hokkaido Nippon-Ham Fighters insignia.svg](https://commons.wikimedia.org/wiki/File:Hokkaido_Nippon-Ham_Fighters_insignia.svg)
- `L.png`: [Seibu lions insignia.svg](https://commons.wikimedia.org/wiki/File:Seibu_lions_insignia.svg)
- `M.png`: [Chiba Lotte Marines insignia.svg](https://commons.wikimedia.org/wiki/File:Chiba_Lotte_Marines_insignia.svg)
- `E.png`: [Rakuten eagles insignia.svg](https://commons.wikimedia.org/wiki/File:Rakuten_eagles_insignia.svg)
- `B.png`: [Orix Buffaloes insignia.svg](https://commons.wikimedia.org/wiki/File:Orix_Buffaloes_insignia.svg)

Processing applied here:

- crop transparent outer padding;
- preserve the vector-rendered logo shapes;
- normalize opponent assets to a 256×256 transparent canvas and equal-size circular frame;
- apply the confirmed optical correction toward upper-right for M, E, and B;\n- normalize `B.png` as a solid Buffaloes navy circle with the original gold B monogram centered inside it, matching the filled-circle F/L/M/E badge system;
- keep the Hawks SH mark on a transparent 256×256 canvas for title placement.

The marks may be protected trademarks even where a source page describes a simple text logo as public domain. Keep this repository private and confirm the relevant trademark and usage rules before commercial redistribution.
