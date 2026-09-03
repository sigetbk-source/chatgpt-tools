"""Code-native Pillow drawings for magic state icons."""

from __future__ import annotations

from PIL import Image, ImageDraw


INK = "#171717"
YELLOW = "#F4C400"
ORANGE = "#F28C00"
GRAY = "#B8B5AB"


def make_event_icon(kind: str, size: int = 256) -> Image.Image:
    if kind not in {"lit", "extinguished", "relit"}:
        raise ValueError(f"unsupported event icon: {kind}")
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    scale = size / 256
    box = lambda values: tuple(round(value * scale) for value in values)
    width = lambda value: max(1, round(value * scale))
    fill = GRAY if kind == "extinguished" else YELLOW

    draw.ellipse(box((8, 8, 248, 248)), fill="#FFFFFF", outline=INK, width=width(12))
    draw.ellipse(box((56, 48, 200, 192)), fill=fill, outline=INK, width=width(10))
    draw.rounded_rectangle(box((94, 176, 162, 216)), radius=width(12), fill=fill, outline=INK, width=width(9))

    if kind != "extinguished":
        rays = ((128, 6, 128, 36), (6, 128, 38, 128), (218, 128, 250, 128),
                (42, 42, 65, 65), (191, 191, 214, 214), (191, 65, 214, 42), (42, 214, 65, 191))
        for line in rays:
            draw.line(box(line), fill=ORANGE, width=width(13))

    polygon = lambda points: tuple((round(x * scale), round(y * scale)) for x, y in points)
    if kind == "lit":
        draw.polygon(polygon(((140, 75), (105, 132), (130, 132), (112, 171), (164, 111), (137, 111))), fill=INK)
    elif kind == "extinguished":
        draw.line(box((48, 48, 208, 208)), fill="#8A2846", width=width(24))
        draw.line(box((48, 48, 208, 208)), fill="#FFFFFF", width=width(8))
    else:
        draw.arc(box((38, 38, 218, 218)), start=205, end=515, fill="#0C79A8", width=width(18))
        draw.polygon(polygon(((57, 57), (88, 55), (69, 84))), fill="#0C79A8")
        draw.polygon(polygon(((199, 199), (168, 201), (187, 172))), fill="#0C79A8")
    return image
