"""Shareable card image generator using Pillow.

Generates "SwimBuddz Wrapped" style cards for quarterly reports.
Two formats: square (1080x1080) and story (1080x1920).

Design: Vibrant gradient background (purple → blue → teal),
white DM Sans typography, bold hero stat, circular avatar with photo.
Inspired by Spotify Wrapped aesthetic with Canva-style layout.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

from libs.common.logging import get_logger

if TYPE_CHECKING:
    from PIL import Image as PILImage

    from services.reporting_service.models import MemberQuarterlyReport

logger = get_logger(__name__)

# ── Gradient colors ──
# Canva reference: purple bottom-left → blue top-left/center → teal/cyan top-right
GRAD_PURPLE = (90, 30, 200)  # deep purple
GRAD_BLUE = (50, 100, 220)  # mid blue
GRAD_TEAL = (0, 210, 220)  # bright cyan/teal

WHITE = (255, 255, 255)
GOLD = (255, 215, 0)

FORMATS = {
    "square": (1080, 1080),
    "story": (1080, 1920),
}

ASSETS_DIR = Path(__file__).parent.parent / "assets"


# ── Drawing helpers ──


def _gradient_bg(width: int, height: int) -> "PILImage":
    """Diagonal gradient: purple (bottom-left) → blue (top-left) → teal (top-right).

    Y is weighted heavier so the top-left area gets blue (not purple),
    making the dark logo visible against a lighter background.
    """
    from PIL import Image

    try:
        import numpy as np

        ys = np.linspace(0, 1, height).reshape(height, 1)
        xs = np.linspace(0, 1, width).reshape(1, width)

        # Weight Y at 0.65 so top-left (low x, low y) gets mostly blue
        t = np.clip(xs * 0.35 + (1 - ys) * 0.65, 0.0, 1.0)

        mask = t < 0.5
        s_low = t / 0.5
        s_high = (t - 0.5) / 0.5

        rgb = np.zeros((height, width, 3), dtype=np.uint8)
        for ch in range(3):
            low = GRAD_PURPLE[ch] * (1 - s_low) + GRAD_BLUE[ch] * s_low
            high = GRAD_BLUE[ch] * (1 - s_high) + GRAD_TEAL[ch] * s_high
            rgb[:, :, ch] = np.where(mask, low, high).astype(np.uint8)

        return Image.fromarray(rgb, "RGB")

    except ImportError:
        img = Image.new("RGB", (width, height))
        pixels = img.load()
        for yp in range(height):
            for xp in range(width):
                t = xp / width * 0.35 + (1 - yp / height) * 0.65
                t = max(0.0, min(1.0, t))
                if t < 0.5:
                    s = t / 0.5
                    c = tuple(
                        int(GRAD_PURPLE[i] * (1 - s) + GRAD_BLUE[i] * s)
                        for i in range(3)
                    )
                else:
                    s = (t - 0.5) / 0.5
                    c = tuple(
                        int(GRAD_BLUE[i] * (1 - s) + GRAD_TEAL[i] * s) for i in range(3)
                    )
                pixels[xp, yp] = c
        return img


def _draw_circle(img: "PILImage", center: tuple, radius: int, fill):
    """Draw a smooth filled circle via alpha compositing."""
    from PIL import Image, ImageDraw

    d = radius * 2
    circle = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    ImageDraw.Draw(circle).ellipse([0, 0, d - 1, d - 1], fill=fill)
    img.paste(circle, (center[0] - radius, center[1] - radius), circle)


def _paste_circular_photo(
    img: "PILImage", photo_url: str, center: tuple, radius: int
) -> bool:
    """Download and paste a member photo cropped to a circle. Returns True on success."""
    from PIL import Image, ImageDraw

    try:
        import urllib.request

        # Download photo to memory
        with urllib.request.urlopen(photo_url, timeout=5) as resp:
            photo_data = resp.read()
        photo = Image.open(io.BytesIO(photo_data)).convert("RGBA")

        # Center-crop to square
        w, h = photo.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        photo = photo.crop((left, top, left + side, top + side))

        # Resize to circle diameter
        d = radius * 2
        photo = photo.resize((d, d), Image.LANCZOS)

        # Circular mask
        mask = Image.new("L", (d, d), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, d - 1, d - 1], fill=255)
        photo.putalpha(mask)

        img.paste(photo, (center[0] - radius, center[1] - radius), photo)
        return True
    except Exception as e:
        logger.debug("Could not load member photo from %s: %s", photo_url, e)
        return False


def _draw_rounded_pill(img: "PILImage", xy, fill, radius=16):
    """Draw a rounded rectangle with alpha support."""
    from PIL import Image, ImageDraw

    x0, y0, x1, y1 = [int(v) for v in xy]
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return
    pill = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(pill).rounded_rectangle(
        [0, 0, w - 1, h - 1], radius=radius, fill=fill
    )
    img.paste(pill, (x0, y0), pill)


def _get_initials(name: str) -> str:
    """Extract up to 2 initials from a name."""
    parts = name.strip().split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    elif parts:
        return parts[0][0].upper()
    return "?"


def _load_fonts(is_story=False):
    """Load DM Sans fonts (bundled, closest to Canva Sans) with fallbacks."""
    from PIL import ImageFont

    # Preferred: DM Sans (geometric sans-serif, very close to Canva Sans)
    dm_bold = str(ASSETS_DIR / "DMSans-Bold.ttf")
    dm_regular = str(ASSETS_DIR / "DMSans-Regular.ttf")
    dm_light = str(ASSETS_DIR / "DMSans-Light.ttf")

    # Fallback: DejaVu (available in most Docker images)
    dv_bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    dv_regular = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

    def _try(paths, size):
        for p in paths:
            try:
                return ImageFont.truetype(p, size)
            except (OSError, IOError):
                continue
        return ImageFont.load_default()

    bold = [dm_bold, dv_bold]
    regular = [dm_regular, dv_regular]
    light = [dm_light, dm_regular, dv_regular]

    return {
        "hero": _try(bold, 300) if is_story else _try(bold, 250),
        "hero_label": _try(light, 48) if is_story else _try(light, 35),
        "name": _try(bold, 48) if is_story else _try(bold, 35),
        "quarter": _try(bold, 45) if is_story else _try(bold, 30),
        "initials": _try(bold, 60) if is_story else _try(bold, 46),
        "stat_label": _try(regular, 24) if is_story else _try(regular, 18),
        "stat_value": _try(bold, 50) if is_story else _try(bold, 38),
        "badge": _try(bold, 32) if is_story else _try(bold, 24),
        "fun_fact": _try(regular, 32) if is_story else _try(regular, 24),
        "footer": _try(regular, 28) if is_story else _try(regular, 22),
        "brand": _try(bold, 35) if is_story else _try(bold, 27),
    }


def _text_height(draw, text: str, font) -> int:
    """Rendered text height in pixels."""
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[3] - bbox[1]


# ── Content logic ──


def _pick_fun_fact(report: "MemberQuarterlyReport") -> str:
    """Choose a contextual fun fact based on member activity.

    Priority:
    1. High percentile (>= 50th) → competitive nudge with actual %
    2. High pool hours (>= 20) → Lagos traffic comparison
    3. Default → fish status
    """
    pool_hours = getattr(report, "pool_hours", 0.0)
    percentile = getattr(report, "attendance_percentile", 0.0)

    if percentile >= 0.5:
        pct_beat = int(percentile * 100)
        return f"Showed up more than {pct_beat}% of swimmers"
    if pool_hours >= 20:
        return "More pool time than Lagos traffic"
    return "On your way to fish status"


def _pick_third_stat(report: "MemberQuarterlyReport"):
    """Pick the 3rd stat pill using fallback chain:
    Milestones > Volunteer Hours > Bubbles > other non-zero > None (show 2 only).
    """
    if report.milestones_achieved > 0:
        return (str(report.milestones_achieved), "Milestones")
    if report.volunteer_hours > 0:
        return (f"{report.volunteer_hours:.0f}h", "Volunteer")
    if report.bubbles_earned > 0:
        return (str(report.bubbles_earned), "Bubbles")

    for val, display, label in [
        (report.events_attended, str(report.events_attended), "Events"),
        (report.rides_taken, str(report.rides_taken), "Rides"),
        (report.certificates_earned, str(report.certificates_earned), "Certs"),
        (report.orders_placed, str(report.orders_placed), "Orders"),
    ]:
        if val > 0:
            return (display, label)
    return None


async def _fetch_member_photo_url(member_auth_id: str) -> str | None:
    """Fetch member photo URL from the members service at generation time."""
    try:
        from libs.common.config import get_settings
        from libs.common.service_client import internal_request

        settings = get_settings()
        members_url = getattr(
            settings, "MEMBERS_SERVICE_URL", "http://members-service:8001"
        )
        resp = await internal_request(
            service_url=members_url,
            method="GET",
            path=f"/internal/members/by-auth/{member_auth_id}",
            calling_service="reporting_service",
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("profile_photo_url")
    except Exception as e:
        logger.debug("Could not fetch member photo: %s", e)
    return None


async def _fetch_referral_link(member_auth_id: str) -> str:
    """Fetch member's referral share link from the wallet service.

    Falls back to the swimbuddz homepage if unavailable.
    """
    try:
        import httpx

        from libs.common.config import get_settings

        settings = get_settings()
        wallet_url = getattr(
            settings, "WALLET_SERVICE_URL", "http://wallet-service:8012"
        )
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{wallet_url}/api/v1/referral/code",
                headers={"X-User-Auth-Id": member_auth_id},
            )
            if resp.status_code == 200:
                data = resp.json()
                link = data.get("share_link")
                if link:
                    return link
    except Exception as e:
        logger.debug("Could not fetch referral link: %s", e)
    return "https://swimbuddz.com"


# ── Main generator ──


async def generate_card_image(
    report: "MemberQuarterlyReport", format: str = "square"
) -> bytes:
    """Generate a vibrant SwimBuddz Wrapped card PNG."""
    from PIL import Image, ImageDraw

    width, height = FORMATS.get(format, FORMATS["square"])
    is_story = format == "story"
    fonts = _load_fonts(is_story=is_story)

    img = _gradient_bg(width, height).convert("RGBA")
    draw = ImageDraw.Draw(img)

    cx = width // 2
    pad = int(width * 0.06)

    def vh(pct: float) -> int:
        return int(height * pct / 100)

    def vw(pct: float) -> int:
        return int(width * pct / 100)

    # ═══════════════════════════════════════════
    # HEADER: Logo (left) + Q badge (right)
    # ═══════════════════════════════════════════
    y = vh(5)

    # Logo — 14% of width (~150px on 1080)
    logo_path = ASSETS_DIR / "logo.png"
    logo_size = vw(25) if is_story else vw(18)
    try:
        logo = Image.open(logo_path).convert("RGBA")
        logo = logo.resize((logo_size, logo_size), Image.LANCZOS)
        img.paste(logo, (pad, y), logo)
    except (FileNotFoundError, OSError):
        logger.warning("Logo not found at %s", logo_path)

    # "SWIMBUDDZ" text below logo icon
    draw = ImageDraw.Draw(img)
    brand_x = pad + logo_size // 2
    brand_y = logo_size + 60 if is_story else logo_size + 40
    draw.text(
        (brand_x, brand_y),
        "SWIMBUDDZ",
        fill=WHITE,
        font=fonts["brand"],
        anchor="mt",
    )

    # Quarter pill — top-right, centered vertically with logo
    q_text = f"Q{report.quarter} {report.year}"
    q_w = draw.textlength(q_text, font=fonts["quarter"]) + 36
    q_h = 44
    q_x = width - pad - q_w
    q_y = y + (logo_size - q_h) // 2
    _draw_rounded_pill(
        img, (q_x, q_y, q_x + q_w, q_y + q_h), fill=(255, 255, 255, 50), radius=22
    )
    draw = ImageDraw.Draw(img)
    draw.text(
        (q_x + q_w / 2, q_y + q_h / 2),
        q_text,
        fill=WHITE,
        font=fonts["quarter"],
        anchor="mm",
    )

    y += logo_size + (vh(1) if is_story else 0)

    # ═══════════════════════════════════════════
    # AVATAR — large circle with photo or initials
    # ═══════════════════════════════════════════
    avatar_r = vw(10) if is_story else vw(8)  # ~86px radius = 172px diameter
    avatar_cy = y + avatar_r

    # Try member photo — fetch from members service at generation time
    photo_url = await _fetch_member_photo_url(report.member_auth_id)
    photo_loaded = False
    if photo_url:
        photo_loaded = _paste_circular_photo(img, photo_url, (cx, avatar_cy), avatar_r)

    if not photo_loaded:
        _draw_circle(img, (cx, avatar_cy), avatar_r, fill=(160, 165, 175, 180))
        draw = ImageDraw.Draw(img)
        draw.text(
            (cx, avatar_cy),
            _get_initials(report.member_name),
            fill=WHITE,
            font=fonts["initials"],
            anchor="mm",
        )

    y = avatar_cy + avatar_r + vh(1.5)

    # ═══════════════════════════════════════════
    # MEMBER NAME
    # ═══════════════════════════════════════════
    draw = ImageDraw.Draw(img)
    draw.text((cx, y), report.member_name, fill=WHITE, font=fonts["name"], anchor="mt")
    y += _text_height(draw, report.member_name, fonts["name"]) + (
        vh(1) if is_story else vh(3)
    )

    # ═══════════════════════════════════════════
    # HERO STAT — giant number + label below
    # ═══════════════════════════════════════════
    pool_hours = getattr(report, "pool_hours", 0.0)
    if pool_hours > 0:
        hero_val = f"{pool_hours:.0f}" if pool_hours >= 10 else f"{pool_hours:.1f}"
        hero_unit = "pool hours"
    else:
        hero_val = str(report.total_sessions_attended)
        hero_unit = (
            "session attended"
            if report.total_sessions_attended == 1
            else "sessions attended"
        )

    if is_story:
        y += vh(4)

    draw.text((cx, y), hero_val, fill=WHITE, font=fonts["hero"], anchor="mt")
    y += _text_height(draw, hero_val, fonts["hero"]) + vh(1.5)

    draw.text(
        (cx, y),
        hero_unit,
        fill=(255, 255, 255, 200),
        font=fonts["hero_label"],
        anchor="mt",
    )
    y += _text_height(draw, hero_unit, fonts["hero_label"]) + vh(3)

    # ═══════════════════════════════════════════
    # BADGE + PERCENTILE (combined)
    # Shows "NEW SWIMMER · Top X%" if is_first_quarter
    # Shows "Top X%" alone if not first quarter but top 50%
    # ═══════════════════════════════════════════
    is_first = getattr(report, "is_first_quarter", False)
    percentile = getattr(report, "attendance_percentile", 0.0)

    badge_parts = []
    if is_first:
        badge_parts.append("NEW SWIMMER")
    if percentile >= 0.5:
        top_pct = max(1, int((1 - percentile) * 100))
        badge_parts.append(f"Top {top_pct}%")

    if badge_parts:
        full_badge = " \u00b7 ".join(badge_parts)

        bw = draw.textlength(full_badge, font=fonts["badge"]) + 52
        bh = 46
        bx = cx - bw / 2
        _draw_rounded_pill(
            img, (bx, y, bx + bw, y + bh), fill=(255, 255, 255, 45), radius=23
        )
        draw = ImageDraw.Draw(img)
        draw.text(
            (cx, y + bh / 2),
            full_badge,
            fill=GOLD if is_first else WHITE,
            font=fonts["badge"],
            anchor="mm",
        )
        y += bh + vh(3)

    # ═══════════════════════════════════════════
    # STATS ROW — label on TOP, value on BOTTOM
    # ═══════════════════════════════════════════
    stats = []
    stats.append((f"{report.attendance_rate * 100:.0f}%", "Attendance"))
    stats.append(
        (f"{report.streak_longest}w" if report.streak_longest > 0 else "0w", "Streak")
    )

    third = _pick_third_stat(report)
    if third:
        stats.append(third)

    num_stats = len(stats)
    pill_h = vh(7.5) if is_story else vh(8)
    pill_gap = vh(3.5) if is_story else vw(2.5)
    total_pill_w = width - pad * 2 - (num_stats - 1) * pill_gap
    pill_w = total_pill_w // num_stats

    for i, (val, lbl) in enumerate(stats):
        px = pad + i * (pill_w + pill_gap)
        _draw_rounded_pill(
            img, (px, y, px + pill_w, y + pill_h), fill=(255, 255, 255, 35), radius=16
        )
        draw = ImageDraw.Draw(img)
        pill_cx = px + pill_w / 2

        # LABEL on top, VALUE below — with good spacing
        lbl_h = _text_height(draw, lbl, fonts["stat_label"])
        val_h = _text_height(draw, val, fonts["stat_value"])
        inner_gap = 30 if is_story else 15
        total_inner = lbl_h + inner_gap + val_h
        lbl_top = y + (pill_h - total_inner) // 2

        draw.text(
            (pill_cx, lbl_top),
            lbl,
            fill=(255, 255, 255, 140),
            font=fonts["stat_label"],
            anchor="mt",
        )
        draw.text(
            (pill_cx, lbl_top + lbl_h + inner_gap),
            val,
            fill=WHITE,
            font=fonts["stat_value"],
            anchor="mt",
        )

    y += pill_h + vh(4)

    # ═══════════════════════════════════════════
    # FUN FACT
    # ═══════════════════════════════════════════
    fun_fact = _pick_fun_fact(report)
    draw.text(
        (cx, y),
        fun_fact,
        fill=(255, 255, 255, 180),
        font=fonts["fun_fact"],
        anchor="mt",
    )

    # ═══════════════════════════════════════════
    # QR CODE + FOOTER
    # Story: QR centered between fun fact and "...Join the wave"
    # Square: QR bottom-right, "...Join the wave" bottom-center
    # ═══════════════════════════════════════════
    referral_link = await _fetch_referral_link(report.member_auth_id)
    qr_inner = vw(20) if is_story else vw(8)  # story bigger, square stays same
    qr_pad = 10  # padding inside the background box
    qr_box = qr_inner + qr_pad * 2  # total box size
    qr_generated = None

    try:
        import qrcode

        qr = qrcode.QRCode(version=1, box_size=4, border=0)
        qr.add_data(referral_link)
        qr.make(fit=True)
        # White QR modules on dark background
        qr_raw = qr.make_image(fill_color="white", back_color="#1a1a2e")
        qr_raw = qr_raw.convert("RGBA").resize((qr_inner, qr_inner), Image.LANCZOS)

        # Put QR inside a dark rounded box for visibility
        qr_box_img = Image.new("RGBA", (qr_box, qr_box), (0, 0, 0, 0))
        from PIL import ImageDraw as _ID

        _ID.Draw(qr_box_img).rounded_rectangle(
            [0, 0, qr_box - 1, qr_box - 1],
            radius=12,
            fill=(26, 26, 46, 220),  # dark navy, slightly transparent
        )
        qr_box_img.paste(qr_raw, (qr_pad, qr_pad), qr_raw)
        qr_generated = qr_box_img
    except ImportError:
        logger.debug("qrcode not available, skipping")

    if is_story:
        # Story: "...Join the wave" anchored at bottom, QR centered above it
        footer_y = height - vh(6)
        draw.text(
            (cx, footer_y),
            "...Join the wave",
            fill=(255, 255, 255, 160),
            font=fonts["footer"],
            anchor="mt",
        )

        # QR centered between fun fact and footer
        if qr_generated:
            qr_y = footer_y - qr_box - vh(3)
            img.paste(qr_generated, (cx - qr_box // 2, qr_y), qr_generated)
            draw = ImageDraw.Draw(img)
    else:
        # Square layout: QR bottom-right, text bottom-center
        footer_y = height - vh(8)  # text stays here
        qr_y = height - vh(12)  # QR shifted up
        if qr_generated:
            img.paste(
                qr_generated,
                (width - pad - qr_box, qr_y),
                qr_generated,
            )
            draw = ImageDraw.Draw(img)

        draw.text(
            (cx, footer_y + qr_box // 2),
            "...Join the wave",
            fill=(255, 255, 255, 160),
            font=fonts["footer"],
            anchor="mm",
        )

    # ═══════════════════════════════════════════
    # EXPORT
    # ═══════════════════════════════════════════
    output = Image.new("RGB", img.size, (0, 0, 0))
    output.paste(img, mask=img.split()[3])

    buffer = io.BytesIO()
    output.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return buffer.getvalue()
