from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent.parent
TAURI_ICON_DIR = ROOT / "web" / "src-tauri" / "icons"
PUBLIC_DIR = ROOT / "web" / "public"

CANVAS = 256
SCALE = 4
PURPLE = "#4d35d8"
PURPLE_DARK = "#3820b6"
LAVENDER = "#ece8ff"
WHITE = "#ffffff"


SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256" fill="none">
  <circle cx="128" cy="128" r="116" fill="#ece8ff"/>
  <path d="M84 122v32c0 12.2 9.8 22 22 22h28" stroke="#3820b6" stroke-width="14" stroke-linecap="round" stroke-linejoin="round"/>
  <rect x="46" y="46" width="76" height="76" rx="20" fill="#4d35d8"/>
  <rect x="134" y="134" width="76" height="76" rx="20" fill="#4d35d8"/>
  <g transform="translate(55 55) scale(2.4166667)" stroke="#fff" stroke-width="2.05" stroke-linecap="round" stroke-linejoin="round">
    <path d="m12.296 3.464 3.02 3.956"/>
    <path d="M20.2 6 3 11l-.9-2.4c-.3-1.1.3-2.2 1.3-2.5l13.5-4c1.1-.3 2.2.3 2.5 1.3z"/>
    <path d="M3 11h18v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
    <path d="m6.18 5.276 3.1 3.899"/>
  </g>
  <g transform="translate(143 143) scale(2.4166667)" stroke="#fff" stroke-width="2.05" stroke-linecap="round" stroke-linejoin="round">
    <path d="m15 19 2 2 4-4"/>
    <path d="M15 3v5a1 1 0 0 0 1 1h5"/>
    <path d="M21 13V9a2.4 2.4 0 0 0-.706-1.706l-3.588-3.588A2.4 2.4 0 0 0 15 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h6.5"/>
  </g>
</svg>
"""


def s(value: float) -> int:
    return round(value * SCALE)


def point(x: float, y: float) -> tuple[int, int]:
    return s(x), s(y)


def rounded_line(draw: ImageDraw.ImageDraw, points: list[tuple[float, float]], fill: str, width: float) -> None:
    draw.line([point(x, y) for x, y in points], fill=fill, width=s(width), joint="curve")
    radius = width / 2
    for x, y in points:
        draw.ellipse((s(x - radius), s(y - radius), s(x + radius), s(y + radius)), fill=fill)


def draw_clapperboard(draw: ImageDraw.ImageDraw, x: float, y: float, size: float) -> None:
    scale = size / 24
    width = max(1, round(2.05 * scale * SCALE))

    def p(px: float, py: float) -> tuple[int, int]:
        return s(x + px * scale), s(y + py * scale)

    top = [(20.2, 6), (3, 11), (2.1, 8.6), (2.05, 7.9), (2.35, 7.2), (3.4, 6.1), (16.9, 2.1), (18.1, 2.0), (19.1, 2.5), (19.4, 3.4), (20.2, 6)]
    draw.line([p(px, py) for px, py in top], fill=WHITE, width=width, joint="curve")
    draw.line([p(3, 11), p(21, 11), p(21, 19), p(19, 21), p(5, 21), p(3, 19), p(3, 11)], fill=WHITE, width=width, joint="curve")
    draw.line([p(12.296, 3.464), p(15.316, 7.42)], fill=WHITE, width=width)
    draw.line([p(6.18, 5.276), p(9.28, 9.175)], fill=WHITE, width=width)


def draw_sticky_note_check(draw: ImageDraw.ImageDraw, x: float, y: float, size: float) -> None:
    scale = size / 24
    width = max(1, round(2.05 * scale * SCALE))

    def p(px: float, py: float) -> tuple[int, int]:
        return s(x + px * scale), s(y + py * scale)

    outline = [(15, 3), (5, 3), (3, 5), (3, 19), (5, 21), (11.5, 21)]
    draw.line([p(px, py) for px, py in outline], fill=WHITE, width=width, joint="curve")
    draw.line([p(15, 3), p(15, 8), p(16, 9), p(21, 9), p(21, 13)], fill=WHITE, width=width, joint="curve")
    draw.line([p(15, 19), p(17, 21), p(21, 17)], fill=WHITE, width=width, joint="curve")


def render_png(size: int) -> Image.Image:
    image = Image.new("RGBA", (CANVAS * SCALE, CANVAS * SCALE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw.ellipse((s(12), s(12), s(244), s(244)), fill=LAVENDER)
    rounded_line(draw, [(84, 122), (84, 154), (106, 176), (134, 176)], fill=PURPLE_DARK, width=14)
    draw.rounded_rectangle((s(46), s(46), s(122), s(122)), radius=s(20), fill=PURPLE)
    draw.rounded_rectangle((s(134), s(134), s(210), s(210)), radius=s(20), fill=PURPLE)
    draw_clapperboard(draw, 55, 55, 58)
    draw_sticky_note_check(draw, 143, 143, 58)

    return image.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    TAURI_ICON_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)

    (TAURI_ICON_DIR / "icon.svg").write_text(SVG, encoding="utf-8")
    (PUBLIC_DIR / "favicon.svg").write_text(SVG, encoding="utf-8")

    png = render_png(512)
    png.save(TAURI_ICON_DIR / "icon.png")

    ico_sizes = [render_png(size) for size in (256, 128, 64, 48, 32, 16)]
    ico_sizes[0].save(TAURI_ICON_DIR / "icon.ico", sizes=[image.size for image in ico_sizes], append_images=ico_sizes[1:])


if __name__ == "__main__":
    main()
