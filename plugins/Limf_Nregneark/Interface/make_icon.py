"""Genererer et regneark-ikon (icon.png) til pluginnet.

Tegner et klassisk regneark: et hvidt dokument med en gron header-bjaelke
og et net af raekker og kolonner, samt en lille fold i ovre hojre hjorne.

Kor: python make_icon.py
"""

import os
from PIL import Image, ImageDraw

SIZE = 64
SS = 4  # supersampling-faktor for glatte kanter
W = SIZE * SS

# Farver
GREEN = (33, 160, 90, 255)        # header / regneark-gron
GREEN_DARK = (27, 133, 75, 255)   # kant
PAPER = (255, 255, 255, 255)      # dokument-baggrund
GRID = (200, 214, 205, 255)       # net-linjer
FOLD = (225, 240, 230, 255)       # hjorne-fold


def rrect(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def main():
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    margin = 8 * SS
    left, top = margin, 4 * SS
    right, bottom = W - margin, W - 4 * SS
    radius = 6 * SS
    fold = 14 * SS  # storrelse pa hjorne-folden

    # Dokumentets ydre form (med foldet hjorne oppe til hojre)
    body = [
        (left, top),
        (right - fold, top),
        (right, top + fold),
        (right, bottom),
        (left, bottom),
    ]
    d.polygon(body, fill=PAPER)
    # afrundede hjorner pa venstre/bund via rrect-overlay
    rrect(d, (left, top, right, bottom), radius, None)

    # Kant rundt om dokumentet
    d.line(body + [body[0]], fill=GREEN_DARK, width=2 * SS, joint="curve")

    # Foldet hjorne
    d.polygon([
        (right - fold, top),
        (right, top + fold),
        (right - fold, top + fold),
    ], fill=FOLD)
    d.line([
        (right - fold, top),
        (right - fold, top + fold),
        (right, top + fold),
    ], fill=GREEN_DARK, width=2 * SS)

    # Indre felt til net
    gx0 = left + 6 * SS
    gx1 = right - 6 * SS
    gy0 = top + 6 * SS
    gy1 = bottom - 6 * SS

    header_h = 9 * SS
    # Gron header-bjaelke (kolonneoverskrifter)
    d.rectangle((gx0, gy0, gx1, gy0 + header_h), fill=GREEN)

    rows = 4
    cols = 3
    grid_top = gy0 + header_h
    cell_h = (gy1 - grid_top) / rows
    cell_w = (gx1 - gx0) / cols

    # Vandrette linjer
    for r in range(rows + 1):
        y = grid_top + r * cell_h
        d.line((gx0, y, gx1, y), fill=GRID, width=1 * SS)
    # Lodrette linjer (over hele hojden, inkl. header)
    for c in range(cols + 1):
        x = gx0 + c * cell_w
        d.line((x, gy0, x, gy1), fill=GRID, width=1 * SS)

    # Nedskaler for glatte kanter
    img = img.resize((SIZE, SIZE), Image.LANCZOS)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
    img.save(out)
    print("Gemte ikon:", out)


if __name__ == "__main__":
    main()
