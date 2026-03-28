"""Shareable card image generator using Pillow.

Generates "SwimBuddz Wrapped" style cards for quarterly reports.
Two formats: square (1080x1080) and story (1080x1920).

Design: Bold, vibrant card inspired by Spotify Wrapped.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from libs.common.logging import get_logger
from services.reporting_service.services.quarter_utils import quarter_label

if TYPE_CHECKING:
    from PIL import Image as PILImage

    from services.reporting_service.models import MemberQuarterlyReport

logger = get_logger(__name__)

# ── Brand palette ──
CYAN = (0, 188, 212)
DARK = (15, 23, 42)
WHITE = (255, 255, 255)
LIGHT_CYAN = (178, 235, 242)
GOLD = (255, 193, 7)
CORAL = (255, 111, 97)
PURPLE = (139, 92, 246)
GREEN = (16, 185, 129)

FORMATS = {
    "square": (1080, 1080),
    "story": (1080, 1920),
}


def _gradient_bg(width: int, height: int) -> "PILImage":
    """Create a vibrant gradient background using numpy-style row fills."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)

    # Three-stop gradient: teal → deep blue → near-black
    stops = [
        (0.0, (0, 172, 193)),  # bright teal
        (0.45, (13, 71, 161)),  # deep blue
        (1.0, (8, 12, 38)),  # near black
    ]

    for y in range(height):
        ratio = y / height
        # Find which two stops we're between
        for i in range(len(stops) - 1):
            if ratio <= stops[i + 1][0]:
                local = (ratio - stops[i][0]) / (stops[i + 1][0] - stops[i][0])
                c1, c2 = stops[i][1], stops[i + 1][1]
                r = int(c1[0] * (1 - local) + c2[0] * local)
                g = int(c1[1] * (1 - local) + c2[1] * local)
                b = int(c1[2] * (1 - local) + c2[2] * local)
                draw.line([(0, y), (width, y)], fill=(r, g, b))
                break

    return img


def _draw_rounded_rect(draw, xy, fill, radius=20):
    """Draw a rounded rectangle."""
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle(xy, radius=radius, fill=fill)


def _load_fonts():
    """Load fonts with fallback."""
    from PIL import ImageFont

    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    regular_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]

    def _try_load(path_list, size):
        for p in path_list:
            try:
                return ImageFont.truetype(p, size)
            except (OSError, IOError):
                continue
        return ImageFont.load_default()

    return {
        "hero": _try_load(paths, 160),
        "title": _try_load(paths, 52),
        "subtitle": _try_load(regular_paths, 32),
        "stat_value": _try_load(paths, 56),
        "stat_label": _try_load(regular_paths, 22),
        "badge": _try_load(paths, 28),
        "footer": _try_load(regular_paths, 22),
        "tag": _try_load(paths, 20),
    }


async def generate_card_image(
    report: "MemberQuarterlyReport", format: str = "square"
) -> bytes:
    """Generate a vibrant shareable card PNG."""
    from PIL import ImageDraw

    width, height = FORMATS.get(format, FORMATS["square"])
    is_story = format == "story"
    fonts = _load_fonts()

    img = _gradient_bg(width, height)
    draw = ImageDraw.Draw(img)

    cx = width // 2
    pad = 50
    y = 50 if is_story else 40

    # ── Top badge: "SWIMBUDDZ WRAPPED" ──
    badge_text = "SWIMBUDDZ WRAPPED"
    badge_w = draw.textlength(badge_text, font=fonts["tag"]) + 40
    _draw_rounded_rect(
        draw,
        (cx - badge_w / 2, y, cx + badge_w / 2, y + 38),
        fill=(0, 100, 120),
        radius=19,
    )
    draw.text((cx, y + 19), badge_text, fill=WHITE, font=fonts["tag"], anchor="mm")
    y += 52

    # ── Quarter label ──
    label = quarter_label(report.year, report.quarter)
    draw.text((cx, y), label, fill=LIGHT_CYAN, font=fonts["subtitle"], anchor="mt")
    y += 42

    # ── Member name ──
    draw.text((cx, y), report.member_name, fill=WHITE, font=fonts["title"], anchor="mt")
    y += 60

    # ── First-timer badge ──
    is_first = getattr(report, "is_first_quarter", False)
    if is_first:
        badge = "NEW SWIMMER"
        bw = draw.textlength(badge, font=fonts["tag"]) + 30
        _draw_rounded_rect(
            draw, (cx - bw / 2, y, cx + bw / 2, y + 36), fill=GOLD, radius=18
        )
        draw.text((cx, y + 18), badge, fill=DARK, font=fonts["tag"], anchor="mm")
        y += 48

    # ── Hero stat: Pool hours or sessions ──
    pool_hours = getattr(report, "pool_hours", 0.0)
    if pool_hours > 0:
        hero_val = f"{pool_hours:.0f}" if pool_hours >= 10 else f"{pool_hours:.1f}"
        hero_label = "hours in the pool"
    else:
        hero_val = str(report.total_sessions_attended)
        hero_label = "sessions attended"

    hero_y = y + (20 if is_story else 5)
    draw.text((cx, hero_y), hero_val, fill=GOLD, font=fonts["hero"], anchor="mt")
    hero_y += 140
    draw.text(
        (cx, hero_y), hero_label, fill=LIGHT_CYAN, font=fonts["subtitle"], anchor="mt"
    )
    hero_y += 45

    # ── Stat cards row ──
    stat_cards = []

    att_pct = f"{report.attendance_rate * 100:.0f}%"
    stat_cards.append(("Attendance", att_pct, CYAN))

    if report.streak_longest > 0:
        stat_cards.append(("Streak", f"{report.streak_longest}w", CORAL))

    if report.milestones_achieved > 0:
        stat_cards.append(("Milestones", str(report.milestones_achieved), PURPLE))
    elif report.bubbles_earned > 0:
        stat_cards.append(("Bubbles", str(report.bubbles_earned), PURPLE))

    if report.volunteer_hours > 0:
        stat_cards.append(("Volunteered", f"{report.volunteer_hours:.0f}h", GREEN))

    # Draw stat cards in a row
    num_cards = min(len(stat_cards), 4)
    if num_cards > 0:
        gap = 12
        card_w = (width - pad * 2 - (num_cards - 1) * gap) // num_cards
        card_h = 100
        start_x = pad

        for i, (lbl, val, color) in enumerate(stat_cards[:num_cards]):
            cx_card = start_x + i * (card_w + gap) + card_w // 2
            cy_card = hero_y

            # Card background — solid color
            _draw_rounded_rect(
                draw,
                (
                    cx_card - card_w // 2,
                    cy_card,
                    cx_card + card_w // 2,
                    cy_card + card_h,
                ),
                fill=color,
                radius=14,
            )

            # Value
            draw.text(
                (cx_card, cy_card + 32),
                val,
                fill=WHITE,
                font=fonts["stat_value"],
                anchor="mm",
            )
            # Label
            draw.text(
                (cx_card, cy_card + 74),
                lbl,
                fill=(*WHITE[:2], 200),
                font=fonts["stat_label"],
                anchor="mm",
            )

        hero_y += card_h + 24

    # ── Percentile nudge ──
    percentile = getattr(report, "attendance_percentile", 0.0)
    if percentile >= 0.5:
        top_pct = int((1 - percentile) * 100)
        if top_pct <= 0:
            top_pct = 1
        nudge = f"Top {top_pct}% of swimmers this quarter"
        nudge_w = draw.textlength(nudge, font=fonts["badge"]) + 40
        _draw_rounded_rect(
            draw,
            (cx - nudge_w / 2, hero_y, cx + nudge_w / 2, hero_y + 44),
            fill=(255, 193, 7, 60),
            radius=22,
        )
        draw.text((cx, hero_y + 22), nudge, fill=GOLD, font=fonts["badge"], anchor="mm")
        hero_y += 60

    # ── Fun comparison ──
    if pool_hours > 0:
        lagoon_crossings = pool_hours / 3.2
        if lagoon_crossings >= 1:
            comparison = f"That's {lagoon_crossings:.0f} Lagos Lagoon crossings!"
        else:
            comparison = "Keep swimming — you're making waves!"
    elif report.total_sessions_attended > 0:
        comparison = "Keep swimming — you're making waves!"
    else:
        comparison = None

    if comparison:
        draw.text(
            (cx, hero_y + 10),
            comparison,
            fill=GOLD,
            font=fonts["subtitle"],
            anchor="mt",
        )
        hero_y += 55

    # ── Footer ──
    # Position footer at bottom, but at least hero_y + 40
    footer_y = max(hero_y + 40, height - 90 if is_story else height - 70)

    # QR code
    try:
        import qrcode

        qr = qrcode.QRCode(version=1, box_size=4, border=1)
        qr.add_data("https://swimbuddz.com")
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="white", back_color=(0, 0, 0, 0))
        qr_img = qr_img.convert("RGBA").resize((100, 100))
        qr_x = width - 130
        qr_y = footer_y - 60
        img.paste(qr_img, (qr_x, qr_y), qr_img)
    except ImportError:
        logger.debug("qrcode not available, skipping")

    draw.text((pad, footer_y), "swimbuddz.com", fill=LIGHT_CYAN, font=fonts["footer"])
    draw.text(
        (pad, footer_y + 28),
        "Join the wave",
        fill=(*LIGHT_CYAN[:2], LIGHT_CYAN[2] // 2),
        font=fonts["footer"],
    )

    # ── Export ──
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return buffer.getvalue()
