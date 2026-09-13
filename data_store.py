"""
data_store.py
Persists fetched StudentResult lists to a versioned results cache so the
analytics / dashboard layers can be explored without re-hitting the portal.

Cache layout (under results_cache/):
    <batch_year>/
        <Branch>_<batch_year>_long.csv      # long format: 1 row per student/semester/subject
        <Branch>_<batch_year>_results.json  # raw per-student results
        manifest.json                       # batch metadata (one entry per branch)

Rerunning a branch overwrites that branch's files (idempotent); other
branches keep their history. The long.csv is what the analytics layer
consumes next.
"""

import json
import os
import time
from typing import List

import pandas as pd

from scraper import StudentResult, SemesterResult

LONG_COLUMNS = [
    "batch_year", "branch", "branch_code", "roll_number", "semester_label",
    "sgpa", "cgpa", "result", "subject_code", "subject_name", "month_year",
    "grade", "credits", "status", "sl_no", "error",
]


def results_to_long_dataframe(
    results: List[StudentResult],
    branch: str,
    batch_year: str,
    branch_code: str,
) -> pd.DataFrame:
    """
    Flatten StudentResult objects into a long-format DataFrame: one row per
    student/semester/subject. Semester-level fields (sgpa/cgpa/result) are
    repeated on each of that semester's subject rows; students with no data
    produce a single row carrying their error text so no one disappears.
    """
    rows = []
    base = {"batch_year": batch_year, "branch": branch, "branch_code": branch_code}

    for s in results:
        student_key = {**base, "roll_number": s.roll_number}
        if not s.semesters:
            rows.append({**student_key, "error": s.error or "No data"})
            continue

        for label, sem in s.semesters.items():
            sem_key = {
                **student_key,
                "semester_label": label,
                "sgpa": sem.sgpa or "",
                "cgpa": sem.cgpa or "",
                "result": sem.result or "",
                "error": "",
            }
            if not sem.subjects:
                rows.append({**sem_key})
                continue
            for subj in sem.subjects:
                rows.append({
                    **sem_key,
                    "subject_code": subj.subject_code,
                    "subject_name": subj.subject_name,
                    "month_year": subj.month_year,
                    "grade": subj.final_grade,
                    "credits": subj.credits,
                    "status": subj.status,
                    "sl_no": subj.sl_no,
                })

    df = pd.DataFrame(rows, columns=LONG_COLUMNS)
    return df.replace({pd.NA: ""})


def _semester_to_dict(sem: SemesterResult) -> dict:
    return {
        "semester_label": sem.semester_label,
        "sgpa": sem.sgpa,
        "cgpa": sem.cgpa,
        "result": sem.result,
        "subjects": [
            {
                "sl_no": subj.sl_no,
                "subject_code": subj.subject_code,
                "subject_name": subj.subject_name,
                "month_year": subj.month_year,
                "final_grade": subj.final_grade,
                "credits": subj.credits,
                "status": subj.status,
            }
            for subj in sem.subjects
        ],
    }


def _student_to_dict(s: StudentResult) -> dict:
    return {
        "roll_number": s.roll_number,
        "error": s.error,
        "semesters": {label: _semester_to_dict(sem) for label, sem in s.semesters.items()},
    }


def save_results_to_cache(
    results: List[StudentResult],
    branch: str,
    batch_year: str,
    branch_code: str,
    cache_dir: str = "results_cache",
) -> dict:
    """
    Write one branch's results into the cache. Returns the paths written.
    """
    folder = os.path.join(cache_dir, batch_year)
    os.makedirs(folder, exist_ok=True)

    long_df = results_to_long_dataframe(results, branch, batch_year, branch_code)
    long_path = os.path.join(folder, f"{branch}_{batch_year}_long.csv")
    col_order = [c for c in LONG_COLUMNS if c in long_df.columns]
    long_df[col_order].to_csv(long_path, index=False, encoding="utf-8")

    raw_path = os.path.join(folder, f"{branch}_{batch_year}_results.json")
    with open(raw_path, "w", encoding="utf-8") as fh:
        json.dump([_student_to_dict(r) for r in results], fh, ensure_ascii=False, indent=1)

    manifest_path = os.path.join(folder, "manifest.json")
    manifest = {}
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as fh:
            try:
                manifest = json.load(fh)
            except json.JSONDecodeError:
                manifest = {}
    manifest[branch] = {
        "batch_year": batch_year,
        "branch_code": branch_code,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "students": len(results),
        "subject_rows": int(len(long_df)),
    }
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)

    return {"long_path": long_path, "raw_path": raw_path, "manifest_path": manifest_path}