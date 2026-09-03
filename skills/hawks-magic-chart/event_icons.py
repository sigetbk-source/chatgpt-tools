"""Code-native Pillow drawings for magic state icons."""

from __future__ import annotations

from PIL import Image, ImageDraw


INK = "#171717"
YELLOW = "#F4C400"
ORANGE = "#F28C00"
GRAY = "#B8B5AB"
RED = "#D62828"
BLUE = "#2384C6"


def make_event_icon(kind: str, size: int = 256) -> Image.Image:
    if kind not in {"lit", "extinguished", "relit", "champion"}:
        raise ValueError(f"unsupported event icon: {kind}")
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    scale = size / 256
    box = lambda values: tuple(round(value * scale) for value in values)
    width = lambda value: max(1, round(value * scale))
    polygon = lambda points: tuple((round(x * scale), round(y * scale)) for x, y in points)

    draw.ellipse(box((8, 8, 248, 248)), fill="#FFFFFF", outline=INK, width=width(12))

    if kind == "champion":
        rays = (
            (54, 71, 72, 84, ORANGE),
            (202, 71, 184, 84, ORANGE),
            (43, 116, 67, 117, BLUE),
            (213, 116, 189, 117, BLUE),
            (77, 45, 85, 66, YELLOW),
            (179, 45, 171, 66, YELLOW),
        )
        for x1, y1, x2, y2, color in rays:
            draw.line(box((x1, y1, x2, y2)), fill=color, width=width(8))

        draw.arc(box((48, 82, 104, 148)), 82, 274, fill=INK, width=width(11))
        draw.arc(box((152, 82, 208, 148)), 266, 98, fill=INK, width=width(11))
        draw.arc(box((55, 88, 100, 141)), 83, 269, fill=YELLOW, width=width(8))
        draw.arc(box((156, 88, 201, 141)), 271, 97, fill=YELLOW, width=width(8))
        cup = polygon(((77, 76), (179, 76), (166, 133), (145, 153), (111, 153), (90, 133)))
        draw.polygon(cup, fill=YELLOW)
        draw.line(cup + (cup[0],), fill=INK, width=width(10), joint="curve")
        draw.rounded_rectangle(
            box((118, 147, 138, 188)), radius=width(5), fill=YELLOW, outline=INK, width=width(8)
        )
        draw.rounded_rectangle(
            box((91, 181, 165, 205)), radius=width(8), fill=ORANGE, outline=INK, width=width(9)
        )
        star = polygon(
            ((128, 91), (134, 107), (151, 108), (138, 119), (142, 136),
             (128, 126), (114, 136), (118, 119), (105, 108), (122, 107))
        )
        draw.polygon(star, fill=ORANGE, outline=INK)
        draw.line(star + (star[0],), fill=INK, width=width(4), joint="curve")
        return image

    fill = GRAY if kind == "extinguished" else YELLOW

    draw.ellipse(box((56, 48, 200, 192)), fill=fill, outline=INK, width=width(10))
    draw.rounded_rectangle(box((94, 176, 162, 216)), radius=width(12), fill=fill, outline=INK, width=width(9))

    if kind != "extinguished":
        rays = ((128, 6, 128, 36), (6, 128, 38, 128), (218, 128, 250, 128),
                (42, 42, 65, 65), (191, 191, 214, 214), (191, 65, 214, 42), (42, 214, 65, 191))
        for line in rays:
            draw.line(box(line), fill=ORANGE, width=width(13))

    if kind == "lit":
        draw.polygon(
            polygon(((140, 75), (105, 132), (130, 132), (112, 171), (164, 111), (137, 111))),
            fill=RED,
        )
    elif kind == "extinguished":
        draw.line(box((48, 48, 208, 208)), fill="#8A2846", width=width(24))
        draw.line(box((48, 48, 208, 208)), fill="#FFFFFF", width=width(8))
    else:
        draw.arc(box((85, 78, 171, 164)), start=202, end=347, fill=RED, width=width(13))
        draw.polygon(polygon(((164, 87), (178, 106), (153, 109))), fill=RED)
        draw.arc(box((85, 78, 171, 164)), start=22, end=167, fill=RED, width=width(13))
        draw.polygon(polygon(((92, 155), (78, 136), (103, 133))), fill=RED)
    return image
