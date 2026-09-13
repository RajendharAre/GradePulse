"""
app.py
Streamlit UI for faculty:
  1. Pick batch year, college code, branch(es) + serial range.
  2. Pick which semester(s) to pull.
  3. Click "Fetch & Analytics" -> progress -> Excel download + analytics
     computed for EXACTLY the range/branch/batch chosen in that run.

Fetching is fully automatic under the hood: each student is fetched via the
fast portal API first; if that fails, the tool silently falls back to a
browser session for that student (no exposed options for faculty).

The tool logs in AS EACH STUDENT using their roll number as both the
username and the password (per requirements.md) — no admin credentials needed.

Run with:  streamlit run app.py
"""

import io
import re

import pandas as pd
import streamlit as st
from streamlit import column_config as cc

from roll_utils import RollScheme, generate_roll_numbers, SEMESTER_LABELS, BRANCH_CODES
from scraper import fetch_all_results_auto
from excel_export import export_to_excel
from data_store import save_results_to_cache, results_to_long_dataframe
import analytics as an

FOURTH_YEAR_LABEL = "4th Year (V + VI SEM)"

st.set_page_config(page_title="Student Result Aggregator", page_icon="📊", layout="centered")
st.title("📊 Student Result Aggregator")
st.caption("Internal faculty tool — pulls exam results in bulk, exports to Excel, and analyses the exact selection of this run.")


def _safe_filename(name: str) -> str:
    """Turn a branch display name into a filesystem-safe fragment."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "branch"


def _metric_cols():
    return {
        "n_students": cc.NumberColumn("Students", format="%d"),
        "n_promoted": cc.NumberColumn("Promoted", format="%d"),
        "promoted": cc.NumberColumn("Promoted / has SGPA", format="%.1f%%"),
        "n_clean": cc.NumberColumn("Clean", format="%d"),
        "clean_pass": cc.NumberColumn("Clean pass (no F)", format="%.1f%%"),
        "n_attempts": cc.NumberColumn("Subject attempts", format="%d"),
        "n_fails": cc.NumberColumn("F grades", format="%d"),
        "subject_pass_rate": cc.NumberColumn("Subject pass-rate", format="%.1f%%"),
        "avg_sgpa": cc.NumberColumn("Avg SGPA", format="%.2f"),
    }


st.subheader("1. Roll Number Range")
col1, col2 = st.columns(2)
batch_year = col1.text_input("Batch year (e.g. 23)", value="23")
college_code = col2.text_input("College code", value="2451")

selected_branches = st.multiselect(
    "Branches",
    options=list(BRANCH_CODES),
    default=["DS"],
    help="The analytics always reflect exactly the branches and ranges you "
         "select here — not any previously fetched data.",
)

with st.expander("Need a branch that isn't in the list?"):
    st.markdown(
        "If your branch code isn't listed above yet, enter it here and it will "
        "be included **in this run** (it won't be saved for future runs)."
    )
    custom_name = st.text_input("Branch name (e.g. CSE-AI)", key="custom_branch_name")
    custom_code = st.text_input("Branch code (3-digit, e.g. 749)", key="custom_branch_code")

branch_pairs = [(br, BRANCH_CODES[br]) for br in selected_branches]
custom_code = custom_code.strip() if custom_code else ""
if custom_code:
    if not (custom_code.isdigit() and len(custom_code) == 3):
        st.warning("Branch code should be exactly 3 digits (e.g. 733).")
    elif custom_code in BRANCH_CODES.values():
        st.info(f"Code {custom_code} is already in the list — no need to enter it manually.")
    else:
        display = (custom_name.strip() if custom_name and custom_name.strip() else f"Branch {custom_code}")
        branch_pairs.append((display, custom_code))
        st.success(f"Will include: **{display}** (code {custom_code}) for this run.")

col1, col2 = st.columns(2)
reg_start = col1.number_input("Regular serial start", min_value=1, value=1)
reg_end = col2.number_input("Regular serial end", min_value=1, value=60)

include_lateral = st.checkbox("Include lateral entries", value=True)
col1, col2 = st.columns(2)
lat_start = col1.number_input("Lateral serial start", min_value=1, value=301, disabled=not include_lateral)
lat_end = col2.number_input("Lateral serial end", min_value=1, value=306, disabled=not include_lateral)

st.subheader("2. Semesters")
selected_semesters = st.multiselect(
    "Select semester(s) to fetch",
    options=SEMESTER_LABELS,
    default=[SEMESTER_LABELS[0]],
)

st.subheader("3. Fetch & Analytics")
max_retries = st.number_input(
    "Retries per failed student",
    min_value=0,
    max_value=5,
    value=2,
    help="How many times the tool retries a student whose fetch fails "
         "(e.g. the portal is briefly busy).",
)
st.caption(
    "How retries work: if a student's fetch fails, the tool automatically "
    "retries it (with a short pause) up to this many times. Students that "
    "still fail are **not included in the analytics**, are marked on the Excel "
    "Summary sheet, and are listed below so you can **re-run just them** if you "
    "decide to."
)

if st.button("Fetch & Analytics", type="primary", use_container_width=True):
    if not selected_semesters:
        st.error("Select at least one semester.")
    elif not branch_pairs:
        st.error("Select at least one branch.")
    else:
        rolls_by_branch = {}
        total_rolls = 0
        for br_name, br_code in branch_pairs:
            scheme = RollScheme(college_code=college_code, batch_year=batch_year, branch_code=br_code)
            rolls = generate_roll_numbers(
                scheme,
                regular_start=int(reg_start),
                regular_end=int(reg_end),
                include_lateral=include_lateral,
                lateral_start=int(lat_start),
                lateral_end=int(lat_end),
            )
            rolls_by_branch[br_name] = (br_code, rolls)
            total_rolls += len(rolls)

        progress_bar = st.progress(0)
        status_text = st.empty()

        # shared counters so the progress bar spans all branches
        done = {"n": 0}
        total = {"n": total_rolls}

        def update_progress(i, total_rolls_, roll):
            done["n"] += 1
            progress_bar.progress(done["n"] / total["n"])
            status_text.text(f"Processing {roll} ({done['n']}/{total['n']})")

        try:
            all_results = []
            branch_groups = []
            for br_name, (br_code, rolls) in rolls_by_branch.items():
                with st.status(f"[{br_name}] Fetching {len(rolls)} students..."):
                    branch_results = fetch_all_results_auto(
                        roll_numbers=rolls,
                        semester_labels=selected_semesters,
                        max_retries=int(max_retries),
                        progress_callback=update_progress,
                    )
                    written = save_results_to_cache(
                        branch_results, br_name, batch_year, br_code,
                    )
                    n_fail = sum(1 for r in branch_results if r.error)
                    st.write(
                        f"Done: {len(branch_results) - n_fail}/{len(branch_results)} OK, "
                        f"{n_fail} failed. Cache → `{_safe_filename(written['long_path'])}`"
                    )
                branch_groups.append((br_name, br_code, branch_results))
                all_results.extend(branch_results)

            failed_students = [r.roll_number for r in all_results if r.error]
            ok_students = [r for r in all_results if not r.error]

            frames = [
                results_to_long_dataframe(rs, br, batch_year, bc)
                for br, bc, rs in branch_groups
            ]
            analytics_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

            excel_bytes = export_to_excel(all_results, selected_semesters).getvalue()

            st.session_state["run_ok"] = len(ok_students)
            st.session_state["run_total"] = len(all_results)
            st.session_state["run_failed"] = failed_students
            st.session_state["analytics_df"] = analytics_df
            st.session_state["analytics_semesters"] = list(selected_semesters)
            st.session_state["analytics_scope"] = an.scope_summary(
                students_count=len(ok_students),
                branches=[br for br, _, _ in branch_groups],
                semesters=list(selected_semesters),
                batch_year=batch_year,
                reg_start=int(reg_start), reg_end=int(reg_end),
                include_lateral=include_lateral,
                lat_start=int(lat_start), lat_end=int(lat_end),
            )
            st.session_state["excel_bytes"] = excel_bytes
            st.rerun()
        except Exception as e:
            st.error(f"Something went wrong: {e}")
            st.exception(e)


# ---------------------------------------------------------------------------
# Analytics section — always rendered for the CURRENT run (session state).
# ---------------------------------------------------------------------------
st.divider()
st.subheader("📈 Analytics")

analytics_df = st.session_state.get("analytics_df")
semesters_in_run = st.session_state.get("analytics_semesters", [])

if analytics_df is None or not semesters_in_run:
    st.caption(
        "Run a fetch above — the analytics always cover **exactly** the branch(es), "
        "batch and serial range of that run, never previously cached data. "
        "Students with login failures are excluded automatically."
    )
else:
    st.caption(st.session_state.get("analytics_scope", ""))
    ok = st.session_state.get("run_ok", 0)
    total_run = st.session_state.get("run_total", 0)
    failed = st.session_state.get("run_failed", [])
    st.success(f"Run complete — {ok} of {total_run} students fetched.")
    if failed:
        st.warning(
            f"{len(failed)} student(s) with login/fetch failures are **excluded "
            f"from analytics** (marked FAILED on the Excel Summary sheet): "
            f"{', '.join(failed[:30])}"
        )

    kpis = an.overall_kpis(analytics_df, semesters_in_run)
    if kpis:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Students analysed", kpis["n_students"])
        c2.metric("Promoted / has SGPA", None if kpis["promoted"] is None else f"{kpis['promoted']:.1f}%")
        c3.metric("Clean pass (no F)", None if kpis["clean_pass"] is None else f"{kpis['clean_pass']:.1f}%")
        c4.metric("Subject pass-rate", None if kpis["subject_pass_rate"] is None else f"{kpis['subject_pass_rate']:.1f}%")
        c5.metric("Avg SGPA", kpis["avg_sgpa"] if kpis["avg_sgpa"] is not None else "—")

    fy = an.fourth_year_metrics(analytics_df, semesters_in_run)
    if fy:
        st.markdown(f"**{FOURTH_YEAR_LABEL}** (III YEAR V + VI SEM combined)")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Promoted / has SGPA", None if fy["promoted"] is None else f"{fy['promoted']:.1f}%")
        c2.metric("Clean pass (no F)", None if fy["clean_pass"] is None else f"{fy['clean_pass']:.1f}%")
        c3.metric("Subject pass-rate", None if fy["subject_pass_rate"] is None else f"{fy['subject_pass_rate']:.1f}%")
        c4.metric("Avg SGPA", fy["avg_sgpa"] if fy["avg_sgpa"] is not None else "—")

    sem_df = an.semester_metrics(analytics_df, semesters_in_run)
    if not sem_df.empty:
        st.markdown("##### Per-semester pass/fail (all three definitions)")
        st.dataframe(
            sem_df,
            column_config={"label": cc.TextColumn("Semester"), **_metric_cols()},
            use_container_width=True,
            hide_index=True,
        )

    rankings = an.subject_ranking(analytics_df, semesters_in_run)
    if not rankings.empty:
        st.markdown("##### Subjects with most failures (grade F)")
        st.caption("Across the selected semesters, this run only.")
        chart = rankings.copy()
        chart["_label"] = chart["subject_code"] + " · " + chart["subject_name"].str[:24]
        st.bar_chart(chart.set_index("_label")["n_fails"], height=280)
        st.dataframe(
            rankings,
            column_config={
                "subject_code": cc.TextColumn("Code"),
                "subject_name": cc.TextColumn("Subject"),
                "n_attempts": cc.NumberColumn("Attempts", format="%d"),
                "n_fails": cc.NumberColumn("F grades", format="%d"),
                "pass_rate": cc.NumberColumn("Pass-rate", format="%.1f%%"),
            },
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No failing subjects (grade F) in this scope — clean across the board.")

    if analytics_df["branch"].nunique(dropna=True) > 1:
        bc = an.branch_comparison(analytics_df, semesters_in_run)
        if not bc.empty:
            st.markdown("##### Branch comparison")
            st.dataframe(
                bc,
                column_config={
                    "branch": cc.TextColumn("Branch"),
                    "label": cc.TextColumn("Semester"),
                    **_metric_cols(),
                },
                use_container_width=True,
                hide_index=True,
            )

    excel_bytes = st.session_state.get("excel_bytes")
    if excel_bytes:
        st.download_button(
            label="⬇️ Download Excel",
            data=io.BytesIO(excel_bytes),
            file_name="student_results.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )