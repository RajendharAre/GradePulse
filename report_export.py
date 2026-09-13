"""
report_export.py
Renders the last analytics run as a compact, shareable PDF report (fpdf2).

Sections (when data exists):
   1. Title + scope sentence
   2. Headline KPIs (5)
   3. 4th Year (V + VI SEM) KPIs if present
   4. Per-semester pass/fail table
   5. Top failing subjects table
   6. Backlog line + count of students with grade F
   7. Footer with generation date + developer line

The font is the built-in Helvetica, so all text is coerced to Latin-1 before
writing (exotic characters in subject names are simplified).
"""

from datetime import datetime
from typing import List, Optional

import pandas as pd
from fpdf import FPDF

from analytics import (
    overall_kpis,
    fourth_year_metrics,
    semester_metrics,
    subject_ranking,
    backlog_report,
)

_TAG = "Student Result Aggregator & Analytics Platform"


def _safe(value) -> str:
    """Coerce to a Latin-1-safe string so fpdf's core fonts never choke."""
    text = "" if value is None else str(value)
    return text.encode("latin-1", "replace").decode("latin-1")


def _pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.1f}%"


def _sgpa(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.2f}"


class _Report(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 15)
        self.set_text_color(30, 30, 30)
        self.cell(0, 7, "GradePulse", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(110, 110, 110)
        self.ln(0)
        self.cell(0, 5, _TAG, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(38, 60, 120)
        self.set_line_width(0.6)
        self.line(10, self.get_y() + 1, 200, self.get_y() + 1)
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(140, 140, 140)
        self.cell(0, 5, f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
                         "GradePulse by Rajendhar Are (rajendharare.tech)", align="C")


def _section_title(pdf: FPDF, text: str) -> None:
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(38, 60, 120)
    pdf.cell(0, 6, text, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(50, 50, 50)


def _kpi_lines(pdf: FPDF, pairs: List[tuple]) -> None:
    for label, value in pairs:
        pdf.cell(0, 5, f"{label}: {value}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)


def _table(pdf: FPDF, headers: List[str], rows: List[list], col_widths: List[float]) -> None:
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(38, 60, 120)
    pdf.set_text_color(255, 255, 255)
    for w, h in zip(col_widths, headers):
        pdf.cell(w, 6, _safe(h)[:40], border=1, fill=True, align="C")
    pdf.ln()
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(30, 30, 30)
    for i, row in enumerate(rows):
        fill = i % 2 == 0
        if fill:
            pdf.set_fill_color(244, 246, 248)
        for w, v in zip(col_widths, row):
            pdf.cell(w, 5.5, _safe(v)[:42], border=1, fill=fill)
        pdf.ln()
    pdf.ln(3)


def build_pdf_report(
    analytics_df: pd.DataFrame,
    semesters: List[str],
    scope: str,
) -> bytes:
    """Build the report and return raw PDF bytes."""
    pdf = _Report()
    pdf.set_auto_page_break(auto=True, margin=14)
    pdf.add_page()

    # 1) Title + scope
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(0, 8, "Results Report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(80, 80, 80)
    pdf.multi_cell(0, 5, _safe(scope))
    pdf.ln(2)

    # 2) Headline KPIs
    kpis = overall_kpis(analytics_df, semesters)
    if kpis:
        _section_title(pdf, "Overall pass/fail KPIs")
        _kpi_lines(pdf, [
            ("Students analysed", str(kpis.get("n_students", "—"))),
            ("Promoted / has SGPA", _pct(kpis.get("promoted"))),
            ("Clean pass (no F)", _pct(kpis.get("clean_pass"))),
            ("Subject pass-rate", _pct(kpis.get("subject_pass_rate"))),
            ("Avg SGPA", _sgpa(kpis.get("avg_sgpa"))),
        ])

    # 3) 4th year
    fy = fourth_year_metrics(analytics_df, semesters)
    if fy:
        _section_title(pdf, "4th Year (III YEAR V SEM + VI SEM)")
        _kpi_lines(pdf, [
            ("Promoted / has SGPA", _pct(fy["promoted"])),
            ("Clean pass (no F)", _pct(fy["clean_pass"])),
            ("Subject pass-rate", _pct(fy["subject_pass_rate"])),
            ("Avg SGPA", _sgpa(fy["avg_sgpa"])),
        ])

    # 4) Per-semester table
    sem_df = semester_metrics(analytics_df, semesters)
    if not sem_df.empty:
        _section_title(pdf, "Per-semester pass/fail")
        rows = [[
            r["label"], r["n_students"], _pct(r["promoted"]), _pct(r["clean_pass"]),
            _pct(r["subject_pass_rate"]), _sgpa(r["avg_sgpa"]),
        ] for _, r in sem_df.iterrows()]
        _table(pdf,
               ["Semester", "Students", "Promoted", "Clean", "Subject pass-rate", "Avg SGPA"],
               rows, [52, 20, 32, 32, 38, 22])

    # 5) Top failing subjects
    top = subject_ranking(analytics_df, semesters)
    if not top.empty:
        _section_title(pdf, "Subjects with most failures (grade F)")
        rows = [[
            r["subject_code"], r["subject_name"], r["n_attempts"], r["n_fails"],
            f"{r['pass_rate']:.1f}%",
        ] for _, r in top.iterrows()]
        _table(pdf, ["Code", "Subject", "Attempts", "F grades", "Pass-rate"],
               rows, [24, 74, 22, 22, 22])

    # 6) Backlog summary
    backlog = backlog_report(analytics_df, semesters)
    n_with_f = backlog["roll_number"].nunique() if not backlog.empty else 0
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(120, 30, 30)
    pdf.cell(0, 6, f"Backlog: {len(backlog)} failed subject attempt(s) across "
                   f"{n_with_f} student(s).", new_x="LMARGIN", new_y="NEXT")

    out = pdf.output()
    if isinstance(out, bytes):
        return out
    if isinstance(out, bytearray):
        return bytes(out)
    return out.encode("latin-1")