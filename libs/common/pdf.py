"""
PDF generation utilities using ReportLab.
"""

import io
from datetime import datetime
from typing import List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def generate_progress_report_pdf(
    student_name: str,
    program_name: str,
    cohort_name: str,
    start_date: datetime,
    end_date: datetime,
    milestones: List[
        dict
    ],  # [{"name": str, "status": str, "achieved_at": datetime, "coach_notes": str}]
    total_milestones: int,
    completed_milestones: int,
    coach_name: Optional[str] = None,
    report_date: Optional[datetime] = None,
) -> bytes:
    """
    Generate a PDF progress report for a student.

    Returns PDF as bytes for email attachment or download.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()
    elements = []

    # Custom styles
    title_style = ParagraphStyle(
        "CustomTitle",
        parent=styles["Heading1"],
        fontSize=24,
        textColor=colors.HexColor("#0891b2"),
        spaceAfter=20,
    )
    heading_style = ParagraphStyle(
        "CustomHeading",
        parent=styles["Heading2"],
        fontSize=14,
        textColor=colors.HexColor("#1e293b"),
        spaceBefore=20,
        spaceAfter=10,
    )
    normal_style = styles["Normal"]

    # Header
    elements.append(Paragraph("🏊‍♂️ SwimBuddz Academy", title_style))
    elements.append(Paragraph("Progress Report", styles["Heading2"]))
    elements.append(Spacer(1, 20))

    # Student Info
    report_date_str = (report_date or datetime.now()).strftime("%B %d, %Y")
    info_data = [
        ["Student:", student_name],
        ["Program:", program_name],
        ["Cohort:", cohort_name],
        ["Report Date:", report_date_str],
    ]
    if coach_name:
        info_data.append(["Coach:", coach_name])

    info_table = Table(info_data, colWidths=[1.5 * inch, 4 * inch])
    info_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 11),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#64748b")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    elements.append(info_table)
    elements.append(Spacer(1, 20))

    # Progress Summary
    elements.append(Paragraph("Progress Summary", heading_style))
    completion_rate = (
        round((completed_milestones / total_milestones) * 100)
        if total_milestones > 0
        else 0
    )
    summary_data = [
        ["Total Milestones:", str(total_milestones)],
        ["Completed:", str(completed_milestones)],
        ["Completion Rate:", f"{completion_rate}%"],
    ]
    summary_table = Table(summary_data, colWidths=[2 * inch, 2 * inch])
    summary_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 11),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                ("PADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    elements.append(summary_table)
    elements.append(Spacer(1, 20))

    # Milestones Table
    elements.append(Paragraph("Milestone Details", heading_style))
    if milestones:
        # Table header
        milestone_data = [["Milestone", "Status", "Date", "Coach Notes"]]
        for m in milestones:
            status = m.get("status", "PENDING").upper()
            achieved_at = m.get("achieved_at")
            date_str = achieved_at.strftime("%b %d") if achieved_at else "-"
            notes = m.get("coach_notes", "-") or "-"
            # Truncate long notes
            if len(notes) > 40:
                notes = notes[:37] + "..."
            milestone_data.append([m.get("name", "Unknown"), status, date_str, notes])

        milestone_table = Table(
            milestone_data, colWidths=[2 * inch, 1 * inch, 1 * inch, 2.5 * inch]
        )
        milestone_table.setStyle(
            TableStyle(
                [
                    # Header
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0891b2")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 10),
                    # Body
                    ("FONTSIZE", (0, 1), (-1, -1), 9),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                    ("PADDING", (0, 0), (-1, -1), 8),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        elements.append(milestone_table)
    else:
        elements.append(Paragraph("No milestone progress recorded yet.", normal_style))

    elements.append(Spacer(1, 30))

    # Footer
    footer_style = ParagraphStyle(
        "Footer",
        parent=normal_style,
        fontSize=9,
        textColor=colors.HexColor("#94a3b8"),
        alignment=1,  # Center
    )
    elements.append(
        Paragraph(
            f"Generated by SwimBuddz Academy • {report_date_str}",
            footer_style,
        )
    )

    # Build PDF
    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()


def generate_certificate_pdf(
    student_name: str,
    program_name: str,
    completion_date: datetime,
    verification_code: str,
) -> bytes:
    """
    Generate a completion certificate PDF.

    Returns PDF as bytes for email attachment or download.
    Landscape A4 format with professional styling.
    """
    from reportlab.lib.pagesizes import A4 as A4_SIZE
    from reportlab.lib.pagesizes import landscape

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4_SIZE),
        rightMargin=1 * inch,
        leftMargin=1 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()
    elements = []

    # Title style
    title_style = ParagraphStyle(
        "CertTitle",
        parent=styles["Heading1"],
        fontSize=36,
        textColor=colors.HexColor("#0891b2"),
        alignment=1,  # Center
        spaceAfter=10,
    )

    # Subtitle style
    subtitle_style = ParagraphStyle(
        "CertSubtitle",
        parent=styles["Normal"],
        fontSize=14,
        textColor=colors.HexColor("#64748b"),
        alignment=1,
        spaceAfter=30,
    )

    # Name style
    name_style = ParagraphStyle(
        "CertName",
        parent=styles["Heading1"],
        fontSize=28,
        textColor=colors.HexColor("#1e293b"),
        alignment=1,
        spaceBefore=20,
        spaceAfter=20,
    )

    # Body style
    body_style = ParagraphStyle(
        "CertBody",
        parent=styles["Normal"],
        fontSize=14,
        textColor=colors.HexColor("#475569"),
        alignment=1,
        spaceAfter=10,
    )

    # Footer style
    footer_style = ParagraphStyle(
        "CertFooter",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#94a3b8"),
        alignment=1,
    )

    # Build certificate content
    elements.append(Spacer(1, 40))
    elements.append(Paragraph("🏊‍♂️ SwimBuddz Academy", title_style))
    elements.append(Paragraph("Certificate of Completion", subtitle_style))
    elements.append(Spacer(1, 20))

    elements.append(Paragraph("This is to certify that", body_style))
    elements.append(Paragraph(f"<b>{student_name}</b>", name_style))
    elements.append(
        Paragraph(
            "has successfully completed all requirements for",
            body_style,
        )
    )
    elements.append(Paragraph(f"<b>{program_name}</b>", name_style))

    completion_str = completion_date.strftime("%B %d, %Y")
    elements.append(Spacer(1, 30))
    elements.append(Paragraph(f"Completed on {completion_str}", body_style))

    elements.append(Spacer(1, 50))
    elements.append(
        Paragraph(
            f"Verification Code: <b>{verification_code}</b>",
            footer_style,
        )
    )
    elements.append(
        Paragraph(
            "Verify at swimbuddz.com/verify",
            footer_style,
        )
    )

    # Build PDF
    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()
