"""
analytics.py
Step 2 — the analytics layer.

Consumes the long-format DataFrame produced by data_store.results_to_long_dataframe
for the CURRENT RUN ONLY. Scope rule (per faculty): analytics reflect exactly the
branch(es) + batch + serial range selected in that run — never previously cached
data. Students whose login/fetch failed are excluded from every metric.

One row in the long df = one student x semester x subject, with semester-level
fields repeated. A student with no data at all appears as a single row carrying
an `error` text; those rows are dropped here.

Three pass/fail definitions (all requested by faculty):
  1) Promoted / has SGPA   — has a parsed SGPA for the semester
  2) Clean pass            — has SGPA and zero 'F' graded subjects in the semester
  3) Subject pass-rate     — % of subject attempts (grade != 'F') in the semester
"""

import re
from typing import List, Optional

import pandas as pd

# The combined "4th year" = the latest two recorded semesters (III YEAR V + VI).
# IV YEAR VII/VIII are not announced yet, so they are not part of any metric.
FOURTH_YEAR_SEMS = ["III YEAR V SEM", "III YEAR VI SEM"]

_FLOAT_RE = re.compile(r"^\d+(\.\d+)?$")


def _to_float(value) -> Optional[float]:
    """Parse '9.74' / '' / None / '9' / NaN to float (or None)."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or not _FLOAT_RE.match(s):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def first_nonempty(values: pd.Series) -> str:
    for v in values:
        if v is not None and not pd.isna(v) and str(v).strip():
            return v
    return ""


def _sem_summary(df: pd.DataFrame, semester_labels: List[str]) -> pd.DataFrame:
    """
    One row per (student, requested semester) for eligible students only.
    Missing semesters (e.g. lateral entries that start later) are materialised
    as NOT-passed rows so percentages always divide by every eligible student.
    """
    # A student is eligible when none of their rows carry an error.
    # CSV round-trips turn empty error cells into NaN, so treat NaN as no-error.
    errors = df["error"].fillna("").astype(str)
    bad = set(df.loc[errors.ne(""), "roll_number"])
    good = df.loc[~df["roll_number"].isin(bad)]

    if good.empty:
        empty_cols = ["roll_number", "semester_label", "promoted", "clean", "n_subjects", "n_fail"]
        return pd.DataFrame({c: pd.Series(dtype=object) for c in empty_cols})

    rows = []
    for roll, g in good.groupby("roll_number", sort=False):
        for sem in semester_labels:
            sub = g[g["semester_label"] == sem]
            if sub.empty:
                rows.append({"roll_number": roll, "semester_label": sem,
                             "present": False, "promoted": False, "clean": False,
                             "n_subjects": 0, "n_fail": 0, "sgpa": None})
                continue
            sgpa = _to_float(first_nonempty(sub["sgpa"]))
            n_subjects = int(sub["subject_code"].fillna("").astype(str).ne("").sum())
            n_fail = int(sub["grade"].fillna("").astype(str).eq("F").sum())
            rows.append({
                "roll_number": roll, "semester_label": sem,
                "present": True,
                "promoted": sgpa is not None,
                "clean": sgpa is not None and n_fail == 0,
                "n_subjects": n_subjects, "n_fail": n_fail, "sgpa": sgpa,
            })

    return pd.DataFrame(rows)


def _aggregate(summary: pd.DataFrame, label: str) -> dict:
    """Collapse a student-semester summary into the three pass/fail metrics."""
    n = len(summary)
    if n == 0:
        return {"label": label, "n_students": 0, "n_promoted": 0, "promoted": None,
                "n_clean": 0, "clean_pass": None, "n_attempts": 0, "n_fails": 0,
                "subject_pass_rate": None, "avg_sgpa": None}
    attempts = int(summary["n_subjects"].sum())
    raises_ = int(summary["n_fail"].sum())
    sg = summary["sgpa"].dropna()
    return {
        "label": label,
        "n_students": int(summary["roll_number"].nunique()),
        "n_promoted": int(summary["promoted"].sum()),
        "promoted": 100.0 * summary["promoted"].mean(),
        "n_clean": int(summary["clean"].sum()),
        "clean_pass": 100.0 * summary["clean"].mean(),
        "n_attempts": attempts,
        "n_fails": raises_,
        "subject_pass_rate": (100.0 * (attempts - raises_) / attempts) if attempts else None,
        "avg_sgpa": round(float(sg.mean()), 2) if not sg.empty else None,
    }


def semester_metrics(df: pd.DataFrame, semester_labels: List[str]) -> pd.DataFrame:
    """One row per semester + a combined 'All selected semesters' row."""
    summary = _sem_summary(df, semester_labels)
    if summary.empty:
        return pd.DataFrame()
    rows = [_aggregate(summary[summary["semester_label"] == sem], sem) for sem in semester_labels]
    rows.append(_aggregate(summary, "All selected semesters"))
    return pd.DataFrame(rows)


def fourth_year_metrics(df: pd.DataFrame, semester_labels: List[str]) -> Optional[dict]:
    """
    Combined pass/fail for the '4th year' (III YEAR V SEM + VI SEM).

    Only counts students who ACTUALLY have data in those semesters — a semester
    nobody has reached yet must not appear as a 0% failure rate. Returns None
    when none of the selected students have V/VI records.
    """
    sel = [s for s in FOURTH_YEAR_SEMS if s in semester_labels]
    if not sel:
        return None
    summary = _sem_summary(df, semester_labels)
    if summary.empty:
        return None
    subset = summary[summary["semester_label"].isin(sel) & summary["present"]]
    if subset.empty:
        return None
    return _aggregate(subset, "4th Year (V + VI SEM)")


def overall_kpis(df: pd.DataFrame, semester_labels: List[str]) -> dict:
    """The three headline percentages across the whole selected scope."""
    meta = semester_metrics(df, semester_labels)
    if meta.empty:
        return {}
    row = meta[meta["label"] == "All selected semesters"]
    if row.empty:
        return {}
    r = row.iloc[0]
    return {
        "n_students": int(r["n_students"]),
        "promoted": r["promoted"],
        "clean_pass": r["clean_pass"],
        "subject_pass_rate": r["subject_pass_rate"],
        "avg_sgpa": r["avg_sgpa"],
    }


def subject_ranking(df: pd.DataFrame, semester_labels: List[str], top_n: int = 15) -> pd.DataFrame:
    """Subjects with the most 'F' grades across the selected semesters."""
    mask = (df["semester_label"].isin(semester_labels)
            & df["subject_code"].fillna("").astype(str).ne(""))
    sub = df[mask].copy()
    if sub.empty:
        return pd.DataFrame()
    sub["_failed"] = sub["grade"].fillna("").astype(str).eq("F")
    ag = sub.groupby("subject_code").agg(
        subject_name=("subject_name", lambda s: first_nonempty(s)),
        n_attempts=("subject_code", "size"),
        n_fails=("_failed", "sum"),
    ).reset_index()
    ag["pass_rate"] = 100.0 * (ag["n_attempts"] - ag["n_fails"]) / ag["n_attempts"]
    ag = ag.sort_values(["n_fails", "n_attempts"], ascending=[False, True]).head(top_n)
    return ag.reset_index(drop=True)


def branch_comparison(df: pd.DataFrame, semester_labels: List[str]) -> pd.DataFrame:
    """One row per (branch, semester) so faculty can compare selected branches."""
    if df["branch"].nunique(dropna=True) < 1:
        return pd.DataFrame()
    frames = []
    for branch, g in df.groupby("branch", sort=False):
        summary = _sem_summary(g, semester_labels)
        if summary.empty:
            continue
        rows = [_aggregate(summary[summary["semester_label"] == sem], sem)
                for sem in semester_labels]
        frame = pd.DataFrame(rows)
        frame.insert(0, "branch", branch)
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def scope_summary(students_count: int, branches: List[str], semesters: List[str],
                  batch_year: str, reg_start: int, reg_end: int,
                  include_lateral: bool, lat_start: int, lat_end: int) -> str:
    """Human-readable sentence describing exactly what the analytics cover."""
    lat = f" + lateral {lat_start}-{lat_end}" if include_lateral else ""
    return (
        f"Batch {batch_year} · {', '.join(branches)} · serials {reg_start}-{reg_end}"
        f"{lat} · semesters: {', '.join(semesters)} · "
        f"{students_count} eligible student(s) (login failures excluded)"
    )