"""Generate PDF quarterly reports for members using ReportLab."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from libs.common.logging import get_logger

if TYPE_CHECKING:
    from services.reporting_service.models import MemberQuarterlyReport

logger = get_logger(__name__)

# Brand colours (RGB 0-1 for ReportLab)
CYAN = (0 / 255, 188 / 255, 212 / 255)
DARK = (15 / 255, 23 / 255, 42 / 255)
LIGHT_GRAY = (0.95, 0.96, 0.97)
WHITE = (1, 1, 1)


def _fmt_pct(val: float) -> str:
    return f"{val * 100:.0f}%"


def _fmt_ngn(val: int) -> str:
    return f"NGN {val:,}"


async def generate_pdf_report(report: "MemberQuarterlyReport") -> bytes:
    """Produce a single-member quarterly PDF report.

    Returns raw PDF bytes ready for streaming.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontSize=24,
        textColor=colors.HexColor("#00BCD4"),
        spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        "ReportSubtitle",
        parent=styles["Normal"],
        fontSize=12,
        textColor=colors.HexColor("#64748B"),
        spaceAfter=20,
    )
    section_style = ParagraphStyle(
        "SectionHeader",
        parent=styles["Heading2"],
        fontSize=14,
        textColor=colors.HexColor("#0F172A"),
        spaceBefore=16,
        spaceAfter=8,
        borderWidth=0,
        borderPadding=0,
    )
    body_style = ParagraphStyle(
        "ReportBody",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#334155"),
        leading=14,
    )

    elements: list = []

    # ── Header ──
    elements.append(Paragraph("SwimBuddz", title_style))
    quarter_label = f"Q{report.quarter} {report.year} Quarterly Report"
    elements.append(Paragraph(quarter_label, subtitle_style))
    elements.append(
        Paragraph(
            f"<b>{report.member_name}</b> &nbsp;|&nbsp; "
            f"Tier: {report.member_tier or 'Community'} &nbsp;|&nbsp; "
            f"Generated: {report.computed_at.strftime('%B %d, %Y')}",
            body_style,
        )
    )
    elements.append(Spacer(1, 16))

    # ── Summary Stats Table ──
    elements.append(Paragraph("Performance Summary", section_style))

    summary_data = [
        ["Metric", "Value"],
        ["Sessions Attended", str(report.total_sessions_attended)],
        ["Sessions Available", str(report.total_sessions_available)],
        ["Attendance Rate", _fmt_pct(report.attendance_rate)],
        ["Punctuality Rate", _fmt_pct(report.punctuality_rate)],
        ["Longest Streak", f"{report.streak_longest} weeks"],
        ["Current Streak", f"{report.streak_current} weeks"],
    ]
    if report.favorite_day:
        summary_data.append(["Favorite Day", report.favorite_day])
    if report.favorite_location:
        summary_data.append(["Favorite Location", report.favorite_location])

    summary_table = Table(summary_data, colWidths=[8 * cm, 8 * cm])
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#00BCD4")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, LIGHT_GRAY]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    elements.append(summary_table)
    elements.append(Spacer(1, 16))

    # ── Academy & Achievements ──
    if any(
        [
            report.milestones_achieved,
            report.programs_enrolled,
            report.certificates_earned,
        ]
    ):
        elements.append(Paragraph("Academy & Achievements", section_style))
        academy_data = [
            ["Metric", "Value"],
            ["Milestones Achieved", str(report.milestones_achieved)],
            ["Milestones In Progress", str(report.milestones_in_progress)],
            ["Programs Enrolled", str(report.programs_enrolled)],
            ["Certificates Earned", str(report.certificates_earned)],
        ]
        academy_table = Table(academy_data, colWidths=[8 * cm, 8 * cm])
        academy_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#7C3AED")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, LIGHT_GRAY]),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ]
            )
        )
        elements.append(academy_table)
        elements.append(Spacer(1, 16))

    # ── Financial & Rewards ──
    elements.append(Paragraph("Financial & Rewards", section_style))
    fin_data = [
        ["Metric", "Value"],
        # ["Total Spent", _fmt_ngn(report.total_spent_ngn)],  # hidden for now
        ["Store Purchases", _fmt_ngn(report.store_spent_ngn)],
        ["Orders Placed", str(report.orders_placed)],
        ["Bubbles Earned", str(report.bubbles_earned)],
        ["Bubbles Spent", str(report.bubbles_spent)],
    ]
    fin_table = Table(fin_data, colWidths=[8 * cm, 8 * cm])
    fin_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#059669")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, LIGHT_GRAY]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    elements.append(fin_table)
    elements.append(Spacer(1, 16))

    # ── Community Engagement ──
    elements.append(Paragraph("Community Engagement", section_style))
    community_data = [
        ["Metric", "Value"],
        ["Volunteer Hours", f"{report.volunteer_hours:.1f}"],
        ["Events Attended", str(report.events_attended)],
        ["Rides Taken", str(report.rides_taken)],
        ["Rides Offered", str(report.rides_offered)],
    ]
    community_table = Table(community_data, colWidths=[8 * cm, 8 * cm])
    community_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2563EB")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, LIGHT_GRAY]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    elements.append(community_table)
    elements.append(Spacer(1, 24))

    # ── Footer ──
    footer_style = ParagraphStyle(
        "Footer",
        parent=styles["Normal"],
        fontSize=8,
        textColor=colors.HexColor("#94A3B8"),
        alignment=1,  # CENTER
    )
    elements.append(
        Paragraph(
            "SwimBuddz — Keep swimming, you're making waves! " "| swimbuddz.com",
            footer_style,
        )
    )

    doc.build(elements)
    return buf.getvalue()
