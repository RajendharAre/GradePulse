"""
excel_export.py
Turns a list of StudentResult objects into a formatted Excel workbook.

Layout rules (per requirements.md):
  - "Summary" sheet: one row per student, SGPA/Result per semester.
  - One sheet per semester: one row per student, one COLUMN PER SUBJECT NAME,
    plus SGPA/CGPA/Result.
  - Missing SGPA  -> the student is considered FAILED (per requirements).
  - Final Grade F -> that grade cell is highlighted red so faculty spot
    failures at a glance.
"""

from io import BytesIO
from typing import List, Dict, Optional
import pandas as pd
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from scraper import StudentResult, SemesterResult

# Red highlight for F grades / failed students
RED_FILL = PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
RED_FONT = Font(color="FFFFFF", bold=True)

# Standard Excel "bad" style for failed-result cells (softer than the block red)
BAD_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
BAD_FONT = Font(color="9C0006", bold=True)

FAILED_TEXT = "FAILED"


def _effective_result(sem: Optional[SemesterResult]) -> str:
    """
    Per requirements: if a semester has subjects but NO SGPA, the student
    is not promoted -> show "FAILED" regardless of what the portal says.
    """
    if sem is None:
        return ""
    if not sem.sgpa:
        return FAILED_TEXT
    return sem.result or ""


def _autofit_columns(worksheet, dataframe: pd.DataFrame) -> None:
    for i, col in enumerate(dataframe.columns, start=1):
        max_len = max(
            [len(str(col))] + [len(str(v)) for v in dataframe[col].astype(str).tolist()]
        )
        worksheet.column_dimensions[get_column_letter(i)].width = min(max_len + 2, 45)


def _style_header(worksheet, n_cols: int) -> None:
    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    for col_idx in range(1, n_cols + 1):
        cell = worksheet.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")


def _highlight_f_grade_cells(worksheet, dataframe: pd.DataFrame, grade_columns: List[str]) -> None:
    """Solid red background on every grade cell whose value is 'F'."""
    header = list(dataframe.columns)
    for col in grade_columns:
        if col not in header:
            continue
        col_idx = header.index(col) + 1
        for row_idx in range(2, dataframe.shape[0] + 2):
            cell = worksheet.cell(row=row_idx, column=col_idx)
            value = (cell.value or "").strip().upper()
            if value == "F":
                cell.fill = RED_FILL
                cell.font = RED_FONT


def _highlight_failed_summary(worksheet, dataframe: pd.DataFrame) -> None:
    """Light-red background on any Result cell whose value is 'FAILED'."""
    for col in dataframe.columns:
        if "Result" not in col:
            continue
        col_idx = list(dataframe.columns).index(col) + 1
        for row_idx in range(2, dataframe.shape[0] + 2):
            cell = worksheet.cell(row=row_idx, column=col_idx)
            value = (cell.value or "").strip()
            if value == FAILED_TEXT:
                cell.fill = BAD_FILL
                cell.font = BAD_FONT


def build_semester_dataframe(students: List[StudentResult], semester_label: str) -> pd.DataFrame:
    """
    One row per student, one column PER SUBJECT NAME (grade), plus SGPA/CGPA/Result.
    Subject columns are derived from whichever student has the most subjects
    for that semester (handles minor elective differences between students).
    """
    # Ordered subject code -> name (first seen) and their display names
    subject_order: Dict[str, str] = {}
    for s in students:
        sem = s.semesters.get(semester_label)
        if not sem:
            continue
        for subj in sem.subjects:
            subject_order.setdefault(subj.subject_code, subj.subject_name)

    name_counts: Dict[str, int] = {}
    for name in subject_order.values():
        name_counts[name] = name_counts.get(name, 0) + 1

    def display_name(code: str, name: str) -> str:
        # Duplicate subject names get their code appended to stay unique
        if name_counts[name] > 1:
            return f"{name} [{code}]"
        return name

    column_names = [display_name(c, n) for c, n in subject_order.items()]

    rows = []
    for s in students:
        sem = s.semesters.get(semester_label)
        row = {"Roll Number": s.roll_number}
        if s.error:
            row["Note"] = s.error
        if sem:
            grade_by_col = {
                display_name(subj.subject_code, subj.subject_name): subj.final_grade
                for subj in sem.subjects
            }
            for col in column_names:
                row[col] = grade_by_col.get(col, "")
            row["SGPA"] = sem.sgpa or ""
            row["CGPA"] = sem.cgpa or ""
            row["Result"] = _effective_result(sem)
        else:
            for col in column_names:
                row[col] = ""
            row["SGPA"] = row["CGPA"] = row["Result"] = ""
        rows.append(row)

    columns = ["Roll Number"] + list(column_names) + ["SGPA", "CGPA", "Result"]
    if any("Note" in r for r in rows):
        columns.insert(1, "Note")
    return pd.DataFrame(rows, columns=columns)


def build_summary_dataframe(students: List[StudentResult], semester_labels: List[str]) -> pd.DataFrame:
    rows = []
    for s in students:
        row = {"Roll Number": s.roll_number}
        if s.error:
            row["Note"] = s.error
        for sem_label in semester_labels:
            sem = s.semesters.get(sem_label)
            row[f"{sem_label} - SGPA"] = sem.sgpa if sem else ""
            row[f"{sem_label} - Result"] = _effective_result(sem)
        rows.append(row)
    return pd.DataFrame(rows)


def export_to_excel(students: List[StudentResult], semester_labels: List[str]) -> BytesIO:
    """
    Returns an in-memory .xlsx file (BytesIO) ready to hand to Streamlit's
    download_button.

    Sheets:
      1. "Summary"  — roll number x semester SGPA/Result overview
      2. "<Semester>" — one sheet per semester, roll x subject-name grades
    """
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        summary_df = build_summary_dataframe(students, semester_labels)
        summary_df.to_excel(writer, sheet_name="Summary", index=False)
        _style_header(writer.sheets["Summary"], len(summary_df.columns))
        _autofit_columns(writer.sheets["Summary"], summary_df)
        _highlight_failed_summary(writer.sheets["Summary"], summary_df)

        for sem_label in semester_labels:
            df = build_semester_dataframe(students, sem_label)
            # Excel sheet names max 31 chars
            sheet_name = sem_label[:31]
            df.to_excel(writer, sheet_name=sheet_name, index=False)
            _style_header(writer.sheets[sheet_name], len(df.columns))
            _autofit_columns(writer.sheets[sheet_name], df)

            # Grade columns = everything except Roll Number / Note / SGPA / CGPA / Result
            non_grade = {"Roll Number", "Note", "SGPA", "CGPA", "Result"}
            grade_columns = [c for c in df.columns if c not in non_grade]
            _highlight_f_grade_cells(writer.sheets[sheet_name], df, grade_columns)

    buffer.seek(0)
    return buffer