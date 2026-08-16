"""E-Ink Document Templates and PDF Generator using ReportLab.

Generates high-contrast, typography-focused PDF documents specifically formatted
for Fujitsu Quaderno Gen 2 screen dimensions and reading ergonomics.
"""

import html
import io
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from reportlab.lib.colors import black, white, HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from quaderno_companion.config import SCREEN_PROFILES, ScreenProfile, settings


class EinkDocumentBuilder:
    """Builder for generating E-ink formatted reading documents and summaries."""

    def __init__(self, profile_name: Optional[str] = None):
        target = profile_name or settings.default_profile
        self.profile: ScreenProfile = SCREEN_PROFILES.get(target, SCREEN_PROFILES["A4"])
        
        # Dimensions in points
        self.page_width_pt = (self.profile.width / self.profile.dpi) * 72.0
        self.page_height_pt = (self.profile.height / self.profile.dpi) * 72.0
        self.styles = self._init_styles()

    def _init_styles(self) -> Dict[str, ParagraphStyle]:
        styles = getSampleStyleSheet()

        # Custom high-contrast E-ink typography
        title_style = ParagraphStyle(
            "EinkTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=28,
            textColor=black,
            alignment=0,  # Left-aligned
            spaceAfter=8,
        )

        subtitle_style = ParagraphStyle(
            "EinkSubtitle",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=11,
            leading=15,
            textColor=HexColor("#333333"),
            spaceAfter=12,
        )

        h1_style = ParagraphStyle(
            "EinkH1",
            parent=styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=20,
            textColor=black,
            spaceBefore=14,
            spaceAfter=6,
            keepWithNext=True,
        )

        h2_style = ParagraphStyle(
            "EinkH2",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=17,
            textColor=black,
            spaceBefore=10,
            spaceAfter=4,
            keepWithNext=True,
        )

        body_style = ParagraphStyle(
            "EinkBody",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=10.5,
            leading=15,
            textColor=black,
            spaceAfter=8,
        )

        bullet_style = ParagraphStyle(
            "EinkBullet",
            parent=body_style,
            leftIndent=16,
            firstLineIndent=-10,
            spaceAfter=4,
        )

        callout_style = ParagraphStyle(
            "EinkCallout",
            parent=body_style,
            fontName="Helvetica-Oblique",
            fontSize=10,
            leading=14,
            textColor=HexColor("#222222"),
        )

        footer_style = ParagraphStyle(
            "EinkFooter",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            textColor=HexColor("#555555"),
            alignment=2,  # Right-aligned
        )

        th_style = ParagraphStyle(
            "EinkTH",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=11,
            textColor=black,
        )

        td_style = ParagraphStyle(
            "EinkTD",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8.0,
            leading=10.5,
            textColor=black,
        )

        code_style = ParagraphStyle(
            "EinkCode",
            parent=styles["Normal"],
            fontName="Courier",
            fontSize=8.5,
            leading=11,
            textColor=black,
            leftIndent=12,
            spaceAfter=6,
        )

        return {
            "title": title_style,
            "subtitle": subtitle_style,
            "h1": h1_style,
            "h2": h2_style,
            "body": body_style,
            "bullet": bullet_style,
            "callout": callout_style,
            "footer": footer_style,
            "th": th_style,
            "td": td_style,
            "code": code_style,
        }

    def render_summary_pdf(
        self,
        title: str,
        source_url: Optional[str] = None,
        key_takeaways: Optional[List[str]] = None,
        sections: Optional[Dict[str, Union[str, List[str]]]] = None,
        author_or_site: Optional[str] = None,
    ) -> bytes:
        """Render a structured Executive Summary PDF."""
        title = (title or "Executive Brief").replace("\x00", "")
        if source_url:
            source_url = source_url.replace("\x00", "")
        if author_or_site:
            author_or_site = author_or_site.replace("\x00", "")
        if key_takeaways:
            key_takeaways = [str(k).replace("\x00", "") for k in key_takeaways]
        if sections:
            sanitized_sections = {}
            for k, v in sections.items():
                clean_k = str(k).replace("\x00", "")
                if isinstance(v, list):
                    sanitized_sections[clean_k] = [str(item).replace("\x00", "") for item in v]
                else:
                    sanitized_sections[clean_k] = str(v).replace("\x00", "")
            sections = sanitized_sections

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=(self.page_width_pt, self.page_height_pt),
            leftMargin=36,
            rightMargin=36,
            topMargin=36,
            bottomMargin=36,
        )

        story = []

        # Header Badge
        header_text = f"<b>QUADERNO EXECUTIVE BRIEF</b> &nbsp;|&nbsp; {datetime.now().strftime('%b %d, %Y')}"
        if author_or_site:
            header_text += f" &nbsp;|&nbsp; {author_or_site}"
        story.append(Paragraph(header_text, self.styles["footer"]))
        story.append(Spacer(1, 4))
        story.append(HRFlowable(width="100%", thickness=1.5, color=black, spaceAfter=10))

        # Title & Source
        safe_title = html.escape(str(title))
        story.append(Paragraph(safe_title, self.styles["title"]))
        if source_url:
            short_url = html.escape(str(source_url[:80]) + ("..." if len(source_url) > 80 else ""))
            story.append(Paragraph(f"Source: {short_url}", self.styles["subtitle"]))

        # Key Takeaways Box
        if key_takeaways:
            story.append(Paragraph("Key Takeaways", self.styles["h1"]))
            takeaways_flowables = []
            for item in key_takeaways:
                safe_item = html.escape(str(item))
                takeaways_flowables.append(
                    Paragraph(f"• &nbsp; {safe_item}", self.styles["bullet"])
                )
            
            # Wrap in structured callout table with border
            table_data = [[takeaways_flowables]]
            t = Table(table_data, colWidths=[self.page_width_pt - 72])
            t.setStyle(
                TableStyle([
                    ("BOX", (0, 0), (-1, -1), 1.0, black),
                    ("BACKGROUND", (0, 0), (-1, -1), HexColor("#F5F5F5")),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ])
            )
            story.append(t)
            story.append(Spacer(1, 10))

        # Sections
        if sections:
            for heading, content in sections.items():
                safe_heading = html.escape(str(heading))
                story.append(Paragraph(safe_heading, self.styles["h1"]))
                if isinstance(content, list):
                    for b in content:
                        safe_b = html.escape(str(b))
                        story.append(Paragraph(f"• &nbsp; {safe_b}", self.styles["bullet"]))
                else:
                    paragraphs = str(content).split("\n\n")
                    for p in paragraphs:
                        if p.strip():
                            safe_p = html.escape(p.strip())
                            story.append(Paragraph(safe_p, self.styles["body"]))
                story.append(Spacer(1, 6))

        doc.build(story)
        return buffer.getvalue()

    def render_article_pdf(
        self,
        title: str,
        content_html_or_text: str,
        author: Optional[str] = None,
        source_url: Optional[str] = None,
        soup_elements: Optional[Any] = None,
    ) -> bytes:
        """Render a clean reader view of a web article or markdown content with tables."""
        title = (title or "Document").replace("\x00", "")
        content_html_or_text = (content_html_or_text or "").replace("\x00", "")
        if author:
            author = author.replace("\x00", "")
        if source_url:
            source_url = source_url.replace("\x00", "")

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=(self.page_width_pt, self.page_height_pt),
            leftMargin=36,
            rightMargin=36,
            topMargin=36,
            bottomMargin=36,
        )

        story = []

        # Header
        date_str = datetime.now().strftime("%Y-%m-%d")
        header_text = f"QUADERNO READER &nbsp;•&nbsp; {date_str}"
        story.append(Paragraph(header_text, self.styles["footer"]))
        story.append(Spacer(1, 4))
        story.append(HRFlowable(width="100%", thickness=1.0, color=black, spaceAfter=12))

        # Title
        story.append(Paragraph(html.escape(str(title)), self.styles["title"]))

        # Metadata line
        meta = []
        if author:
            meta.append(f"By {html.escape(str(author))}")
        if source_url:
            short_url = html.escape(str(source_url[:60]) + ("..." if len(source_url) > 60 else ""))
            meta.append(short_url)
        if meta:
            story.append(Paragraph(" &nbsp;|&nbsp; ".join(meta), self.styles["subtitle"]))

        story.append(Spacer(1, 8))

        avail_w = self.page_width_pt - 72.0

        if soup_elements:
            # Render directly from BeautifulSoup DOM elements (preserving tables & formatting)
            for el in soup_elements:
                tag = el.name.lower()
                text = el.get_text().strip()
                if not text and tag != "table":
                    continue

                if tag == "h1":
                    story.append(Paragraph(html.escape(text), self.styles["h1"]))
                elif tag in ("h2", "h3"):
                    story.append(Paragraph(html.escape(text), self.styles["h2"]))
                elif tag in ("h4", "h5", "h6"):
                    story.append(Paragraph(html.escape(text), self.styles["callout"]))
                elif tag == "p":
                    story.append(Paragraph(html.escape(text), self.styles["body"]))
                elif tag == "li":
                    story.append(Paragraph(f"• &nbsp; {html.escape(text)}", self.styles["bullet"]))
                elif tag == "blockquote":
                    story.append(Paragraph(html.escape(text), self.styles["callout"]))
                    story.append(Spacer(1, 4))
                elif tag in ("pre", "code"):
                    story.append(Paragraph(html.escape(text), self.styles["code"]))
                elif tag == "table":
                    # Parse HTML table into ReportLab Table
                    table_rows = []
                    for tr in el.find_all("tr"):
                        row_cells = []
                        is_hdr_row = bool(tr.find_all("th"))
                        for cell in tr.find_all(["th", "td"]):
                            c_text = html.escape(cell.get_text().strip())
                            st = self.styles["th"] if (cell.name == "th" or is_hdr_row) else self.styles["td"]
                            row_cells.append(Paragraph(c_text, st))
                        if row_cells:
                            table_rows.append(row_cells)

                    if table_rows:
                        num_cols = max(len(r) for r in table_rows)
                        if num_cols > 0:
                            # Pad ragged rows
                            for r in table_rows:
                                while len(r) < num_cols:
                                    r.append(Paragraph("", self.styles["td"]))
                            col_width = avail_w / float(num_cols)
                            tbl = Table(table_rows, colWidths=[col_width] * num_cols)
                            tbl.setStyle(TableStyle([
                                ("GRID", (0, 0), (-1, -1), 0.75, black),
                                ("BACKGROUND", (0, 0), (-1, 0), HexColor("#EEEEEE")),
                                ("TOPPADDING", (0, 0), (-1, -1), 3),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ]))
                            story.append(tbl)
                            story.append(Spacer(1, 8))
        else:
            # Fallback for plain text / markdown input
            paragraphs = content_html_or_text.split("\n\n")
            for p in paragraphs:
                text = p.strip()
                if not text:
                    continue
                if text.startswith("# "):
                    story.append(Paragraph(html.escape(text[2:]), self.styles["h1"]))
                elif text.startswith("## "):
                    story.append(Paragraph(html.escape(text[3:]), self.styles["h2"]))
                elif text.startswith("- ") or text.startswith("* "):
                    story.append(Paragraph(f"• &nbsp; {html.escape(text[2:])}", self.styles["bullet"]))
                else:
                    story.append(Paragraph(html.escape(text), self.styles["body"]))

        doc.build(story)
        return buffer.getvalue()


