"""Shareable card image generator using Pillow.

Generates "SwimBuddz Wrapped" style cards for quarterly reports.
Two formats: square (1080x1080) and story (1080x1920).

Design: Clean, premium Apple-style — light background, dark typography,
subtle accent colors, intentional white space.
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

# ── Apple-style palette ──
BG_TOP = (240, 248, 255)  # very faint blue-white
BG_BOTTOM = (225, 238, 248)  # slightly deeper cool white
TEXT_PRIMARY = (15, 23, 42)  # slate-900
TEXT_SECONDARY = (100, 116, 139)  # slate-500
TEXT_TERTIARY = (148, 163, 184)  # slate-400
ACCENT = (0, 172, 193)  # SwimBuddz teal
ACCENT_LIGHT = (224, 247, 250)  # very light teal
GOLD = (234, 170, 0)  # warm gold for highlights
WHITE = (255, 255, 255)
DIVIDER = (226, 232, 240)  # slate-200

FORMATS = {
    "square": (1080, 1080),
    "story": (1080, 1920),
}


def _clean_bg(width: int, height: int) -> "PILImage":
    """Create a subtle cool-white gradient background."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)

    for y_pos in range(height):
        ratio = y_pos / height
        r = int(BG_TOP[0] * (1 - ratio) + BG_BOTTOM[0] * ratio)
        g = int(BG_TOP[1] * (1 - ratio) + BG_BOTTOM[1] * ratio)
        b = int(BG_TOP[2] * (1 - ratio) + BG_BOTTOM[2] * ratio)
        draw.line([(0, y_pos), (width, y_pos)], fill=(r, g, b))

    return img


def _rounded_rect(draw, xy, fill, radius=20):
    """Draw a rounded rectangle."""
    draw.rounded_rectangle(xy, radius=radius, fill=fill)


def _load_fonts():
    """Load fonts with fallback to system defaults."""
    from PIL import ImageFont

    bold_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    regular_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    light_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-ExtraLight.ttf",
        *regular_paths,
    ]

    def _try_load(path_list, size):
        for p in path_list:
            try:
                return ImageFont.truetype(p, size)
            except (OSError, IOError):
                continue
        return ImageFont.load_default()

    return {
        "hero": _try_load(bold_paths, 144),
        "hero_label": _try_load(light_paths, 28),
        "name": _try_load(bold_paths, 40),
        "quarter": _try_load(regular_paths, 24),
        "stat_value": _try_load(bold_paths, 44),
        "stat_label": _try_load(regular_paths, 18),
        "badge": _try_load(bold_paths, 20),
        "nudge": _try_load(bold_paths, 24),
        "comparison": _try_load(regular_paths, 22),
        "footer": _try_load(regular_paths, 18),
        "footer_small": _try_load(regular_paths, 14),
    }


async def generate_card_image(
    report: "MemberQuarterlyReport", format: str = "square"
) -> bytes:
    """Generate an Apple-style clean shareable card PNG."""
    from PIL import ImageDraw

    width, height = FORMATS.get(format, FORMATS["square"])
    is_story = format == "story"
    fonts = _load_fonts()

    img = _clean_bg(width, height)
    draw = ImageDraw.Draw(img)

    cx = width // 2
    pad = 70
    y = 80 if is_story else 60

    # ── Brand mark ──
    brand = "swimbuddz"
    draw.text((cx, y), brand, fill=ACCENT, font=fonts["quarter"], anchor="mt")
    y += 36

    # ── Thin divider ──
    div_w = 60
    draw.line([(cx - div_w, y), (cx + div_w, y)], fill=DIVIDER, width=2)
    y += 24

    # ── Quarter label ──
    label = quarter_label(report.year, report.quarter)
    draw.text(
        (cx, y),
        f"Your {label} Swim Report",
        fill=TEXT_SECONDARY,
        font=fonts["quarter"],
        anchor="mt",
    )
    y += 40

    # ── Member name ──
    draw.text(
        (cx, y), report.member_name, fill=TEXT_PRIMARY, font=fonts["name"], anchor="mt"
    )
    y += 56

    # ── First-timer badge ──
    is_first = getattr(report, "is_first_quarter", False)
    if is_first:
        badge = "NEW SWIMMER"
        bw = draw.textlength(badge, font=fonts["badge"]) + 32
        _rounded_rect(draw, (cx - bw / 2, y, cx + bw / 2, y + 32), fill=GOLD, radius=16)
        draw.text((cx, y + 16), badge, fill=WHITE, font=fonts["badge"], anchor="mm")
        y += 46

    # ── Hero stat ──
    pool_hours = getattr(report, "pool_hours", 0.0)
    if pool_hours > 0:
        hero_val = f"{pool_hours:.0f}" if pool_hours >= 10 else f"{pool_hours:.1f}"
        hero_unit = "hours in the pool"
    else:
        hero_val = str(report.total_sessions_attended)
        hero_unit = (
            "session attended"
            if report.total_sessions_attended == 1
            else "sessions attended"
        )

    hero_y = y + (30 if is_story else 15)
    draw.text(
        (cx, hero_y), hero_val, fill=TEXT_PRIMARY, font=fonts["hero"], anchor="mt"
    )
    hero_y += 130
    draw.text(
        (cx, hero_y),
        hero_unit,
        fill=TEXT_SECONDARY,
        font=fonts["hero_label"],
        anchor="mt",
    )
    hero_y += 50

    # ── Percentile nudge (above stats) ──
    percentile = getattr(report, "attendance_percentile", 0.0)
    if percentile >= 0.5:
        top_pct = max(1, int((1 - percentile) * 100))
        nudge = f"Top {top_pct}% of swimmers"
        nw = draw.textlength(nudge, font=fonts["nudge"]) + 40
        _rounded_rect(
            draw,
            (cx - nw / 2, hero_y, cx + nw / 2, hero_y + 38),
            fill=ACCENT_LIGHT,
            radius=19,
        )
        draw.text(
            (cx, hero_y + 19), nudge, fill=ACCENT, font=fonts["nudge"], anchor="mm"
        )
        hero_y += 52

    # ── Stat pills ──
    stats = []
    att_pct = f"{report.attendance_rate * 100:.0f}%"
    stats.append((att_pct, "Attendance"))

    if report.streak_longest > 0:
        stats.append((f"{report.streak_longest}w", "Streak"))

    if report.milestones_achieved > 0:
        stats.append((str(report.milestones_achieved), "Milestones"))

    if report.bubbles_earned > 0:
        stats.append((str(report.bubbles_earned), "Bubbles"))

    if report.volunteer_hours > 0:
        stats.append((f"{report.volunteer_hours:.0f}h", "Volunteered"))

    num_stats = min(len(stats), 4)
    if num_stats > 0:
        # Thin divider above stats
        draw.line(
            [(pad + 20, hero_y), (width - pad - 20, hero_y)], fill=DIVIDER, width=1
        )
        hero_y += 24

        col_w = (width - pad * 2) // num_stats

        for i, (val, lbl) in enumerate(stats[:num_stats]):
            stat_cx = pad + i * col_w + col_w // 2

            draw.text(
                (stat_cx, hero_y),
                val,
                fill=TEXT_PRIMARY,
                font=fonts["stat_value"],
                anchor="mt",
            )
            draw.text(
                (stat_cx, hero_y + 48),
                lbl.upper(),
                fill=TEXT_TERTIARY,
                font=fonts["stat_label"],
                anchor="mt",
            )

            # Vertical divider between stats
            if i < num_stats - 1:
                div_x = pad + (i + 1) * col_w
                draw.line(
                    [(div_x, hero_y + 4), (div_x, hero_y + 60)],
                    fill=DIVIDER,
                    width=1,
                )

        hero_y += 80

    # ── Fun comparison ──
    if pool_hours > 0:
        lagoon_crossings = pool_hours / 3.2
        if lagoon_crossings >= 1:
            comparison = (
                f"That's like crossing the Lagos Lagoon {lagoon_crossings:.0f}x"
            )
        else:
            comparison = "Keep swimming — you're making waves!"
    elif report.total_sessions_attended > 0:
        comparison = "Keep swimming — you're making waves!"
    else:
        comparison = None

    if comparison:
        draw.text(
            (cx, hero_y + 4),
            comparison,
            fill=TEXT_SECONDARY,
            font=fonts["comparison"],
            anchor="mt",
        )
        hero_y += 40

    # ── Footer area ──
    footer_y = max(hero_y + 50, height - 100 if is_story else height - 80)

    # Thin top line
    draw.line([(pad, footer_y), (width - pad, footer_y)], fill=DIVIDER, width=1)
    footer_y += 20

    # QR code on the right
    try:
        import qrcode

        qr = qrcode.QRCode(version=1, box_size=3, border=1)
        qr.add_data("https://swimbuddz.com")
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color=TEXT_SECONDARY, back_color=(0, 0, 0, 0))
        qr_img = qr_img.convert("RGBA").resize((70, 70))
        img.paste(qr_img, (width - pad - 70, footer_y - 6), qr_img)
    except ImportError:
        logger.debug("qrcode not available, skipping")

    draw.text(
        (pad, footer_y),
        "swimbuddz.com",
        fill=TEXT_SECONDARY,
        font=fonts["footer"],
    )
    draw.text(
        (pad, footer_y + 24),
        "Join the wave",
        fill=TEXT_TERTIARY,
        font=fonts["footer_small"],
    )

    # ── Export ──
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return buffer.getvalue()
