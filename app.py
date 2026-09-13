"""
app.py
GradePulse — professional multi-tab Streamlit UI for faculty:

   Home       — platform overview, developer info, quick navigation
   Results    — pick batch/branch/roll range + semesters, fetch, download Excel
   Analysis   — pass/fail visuals & reports for the exact selection of the run
   Notes      — faculty notes per student (stored locally, no DB yet)
   Feedback   — seeded positive feedback + a form (stored locally, DB later)

Fetching is fully automatic: API fast path first, silent browser fallback per
student if needed. The tool logs in AS EACH STUDENT using their roll number as
both username and password (per requirements.md) — no admin credentials needed.

Run with:  streamlit run app.py
"""

import io
import re

import altair as alt
import pandas as pd
import streamlit as st
from streamlit import column_config as cc

from roll_utils import RollScheme, generate_roll_numbers, SEMESTER_LABELS, BRANCH_CODES
from scraper import fetch_all_results_auto
from excel_export import export_to_excel
from data_store import save_results_to_cache, results_to_long_dataframe
from local_store import (
    load_notes, add_note, delete_note,
    load_feedback, add_feedback, NOTE_CATEGORIES,
)
from visit_counter import record_visit
from report_export import build_pdf_report
import analytics as an

FOURTH_YEAR_LABEL = "4th Year (V + VI SEM)"
NAV_OPTIONS = ["Home", "Results", "Analysis", "Notes", "Feedback"]

st.set_page_config(page_title="GradePulse — Student Result Aggregator", layout="wide")


# --- Visit counter: counted once per browser session, badge shown everywhere ---
if "visit_value" not in st.session_state:
    st.session_state["visit_value"] = record_visit()


def _safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "branch"


def _go_to(tab: str) -> None:
    st.session_state["nav_active"] = tab
    st.rerun()


def _metric_cols() -> dict:
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


def _execute_run(
    rolls_by_branch, semesters, batch_year,
    max_retries=2, rerun=False, scope_override=None,
) -> None:
    """
    Shared fetch orchestration used by both the main Results button and the
    're-run only failed students' shortcut. Performs the fetch (per branch),
    writes the results cache + Excel, persists everything to session_state and
    jumps back to the Results tab so the UI can show the status.
    """
    total_rolls = sum(len(rolls) for _, rolls in rolls_by_branch.items())
    progress_bar = st.progress(0)
    status_text = st.empty()
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
            label = "Re-fetching failed students" if rerun else f"Fetching {len(rolls)} students"
            with st.status(f"[{br_name}] {label}..."):
                branch_results = fetch_all_results_auto(
                    roll_numbers=rolls,
                    semester_labels=semesters,
                    max_retries=max_retries,
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

        excel_bytes = export_to_excel(all_results, semesters).getvalue()

        scope = scope_override or an.scope_summary(
            students_count=len(ok_students),
            branches=[br for br, _, _ in branch_groups],
            semesters=list(semesters),
            batch_year=batch_year,
            reg_start=0, reg_end=0, include_lateral=False, lat_start=0, lat_end=0,
        )

        st.session_state["run_ok"] = len(ok_students)
        st.session_state["run_total"] = len(all_results)
        st.session_state["run_failed"] = failed_students
        st.session_state["rerun_groups"] = [
            (br, bc, [r.roll_number for r in rs if r.error])
            for br, bc, rs in branch_groups if any(r.error for r in rs)
        ]
        st.session_state["rerun_semesters"] = list(semesters)
        st.session_state["rerun_batch_year"] = batch_year
        st.session_state["analytics_df"] = analytics_df
        st.session_state["analytics_semesters"] = list(semesters)
        st.session_state["analytics_scope"] = scope
        st.session_state["excel_bytes"] = excel_bytes
        _go_to("Results")
    except Exception as e:
        st.error(f"Something went wrong: {e}")
        st.exception(e)


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------
# The nav is a KEYLESS widget: the active tab lives only in session_state
# ("nav_active"), so buttons anywhere can switch tabs via _go_to() without
# hitting Streamlit's rule that a widget's key cannot be changed after render.
_active_tab = st.session_state.get("nav_active", "Home")

st.markdown(
    """
    <style>
    /* subtle, professional motion + brand polish */
    @keyframes gpFade { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
    div[data-testid='stMetric'],
    div[data-testid='stHorizontalBlock'] > div,
    div[data-testid='stVerticalBlockBorderWrapper'] { animation: gpFade .45s ease both; }
    .stButton > button:hover { box-shadow: 0 4px 14px rgba(0,0,0,.14); transform: translateY(-1px); }
    .stButton > button { transition: box-shadow .2s ease, transform .2s ease; }
    .gp-brand { font-size: 1.55rem; font-weight: 800; letter-spacing: -.5px;
                background: linear-gradient(90deg, #20325c, #2e6f8f);
                -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    .gp-tagline { margin-top: -6px; color: #778088; font-size: .85rem; }
    .gp-badge { display:inline-block; background:#eef2f8; border:1px solid #d7deea;
                color:#20325c; font-size:.8rem; font-weight:600; padding:.28rem .7rem;
                border-radius:999px; white-space:nowrap; }
    .gp-footer { text-align:center; color:#98a2ae; font-size:.78rem; margin-top:2rem; }
    div[data-testid='stSegmentedControl']{max-width:720px;margin:.4rem auto 1rem auto;}
    div[data-testid='stSegmentedControl']>div{background-color:#f6f7f9;border-radius:14px;padding:6px;}
    </style>
    """,
    unsafe_allow_html=True,
)

_col_brand, _col_visit = st.columns([4, 1])
with _col_brand:
    st.markdown("<div class='gp-brand'>GradePulse</div><div class='gp-tagline'>"
                "Student Result Aggregator & Analytics Platform</div>",
                unsafe_allow_html=True)
with _col_visit:
    visits = st.session_state.get("visit_value")
    st.markdown(
        f"<div style='text-align:right'><span class='gp-badge'>"
        f"Visits (sessions): {visits if visits is not None else '—'}</span></div>",
        unsafe_allow_html=True,
    )

_chosen = st.segmented_control(
    "Navigation",
    options=NAV_OPTIONS,
    default=_active_tab,
    label_visibility="collapsed",
)
if _chosen is not None and _chosen != _active_tab:
    st.session_state["nav_active"] = _chosen
    st.rerun()
nav = _active_tab


# ---------------------------------------------------------------------------
# HOME
# ---------------------------------------------------------------------------
if nav == "Home":
    col_logo, col_dev = st.columns([2, 1], gap="large")

    with col_logo:
        st.markdown(
            "Pulls exam results in bulk from the college portal and turns them into "
            "clean Excel reports and pass/fail analytics for every semester — built "
            "specifically for faculty."
        )
        st.markdown(
            "- **Bulk fetch** — logs in as each student automatically (roll number as "
            "username & password); no admin credentials.\n"
            "- **Fast** — portal JSON API, ~0.35 s per student, all semesters in one "
            "call; silent browser fallback if the API hiccups.\n"
            "- **Excel reports** — per-semester sheets with subject-name columns, "
            "FAILED / F-grade highlighting.\n"
            "- **Analytics** — three pass/fail definitions, per-semester tables, "
            "4th-year summary, subject failure ranking, branch comparison."
        )

    with col_dev:
        st.markdown("### Developed by")
        st.markdown(
            "**Rajendhar Are**  \n"
            "Roll No : `2451-23-750-011`  \n"
            "Visit : [rajendharare.tech](https://rajendharare.tech)  \n"
            "Connect : [linkedin.com/in/rajendhar-are](https://linkedin.com/in/rajendhar-are)"
        )

    st.divider()

    st.markdown("### How GradePulse analyses results")
    with st.container(border=True):
        c1, c2, c3 = st.columns(3)
        c1.markdown(
            "**1. Promoted / has SGPA**  \n"
            "Percentage of students who have a reported SGPA for the semester "
            "(a missing SGPA means the student failed that semester)."
        )
        c2.markdown(
            "**2. Clean pass (no F)**  \n"
            "Percentage of students with a semester record containing zero `F` grades."
        )
        c3.markdown(
            "**3. Subject pass-rate**  \n"
            "Percentage of all subject attempts (individual student x subject) that "
            "were cleared (grade not `F`)."
        )
        st.caption(
            "Every metric is computed on exactly the branch(es), batch and roll range "
            "you select for the run — never on previously cached data. Students whose "
            "login failed are excluded automatically."
        )

    st.markdown("### Get started")
    b1, b2, _ = st.columns(3)
    if b1.button("Fetch Results", type="primary", width="stretch"):
        _go_to("Results")
    if b2.button("View Analysis", width="stretch"):
        _go_to("Analysis")

    st.info(
        "Use this only with authorization from your college's examination / IT "
        "section. The downloaded Excel files and cached results contain real "
        "student records — treat them as sensitive."
    )


# ---------------------------------------------------------------------------
# RESULTS
# ---------------------------------------------------------------------------
elif nav == "Results":
    st.title("Student Result Aggregator")

    if st.session_state.pop("trigger_rerun", False):
        groups = st.session_state.get("rerun_groups", [])
        sems = st.session_state.get("rerun_semesters", [])
        batch_year_rerun = st.session_state.get("rerun_batch_year", "23")
        if groups and sems:
            total_failed = sum(len(rolls) for _, _, rolls in groups)
            st.info(f"Re-running **{total_failed}** previously failed student(s) for "
                    f"the same semester(s): {', '.join(sems)}.")
            _execute_run(
                {name: (code, rolls) for name, code, rolls in groups},
                sems, batch_year_rerun, max_retries=2, rerun=True,
                scope_override=(
                    f"Re-run of previously failed students · branches: "
                    f"{', '.join(name for name, _, _ in groups)} · semesters: "
                    f"{', '.join(sems)} · {total_failed} student(s)"
                ),
            )

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

    st.subheader("3. Fetch & Download")
    max_retries = st.number_input(
        "Retries per failed student",
        min_value=0, max_value=5, value=2,
        help="How many times the tool retries a student whose fetch fails "
             "(e.g. the portal is briefly busy).",
    )
    st.caption(
        "How retries work: a failing student is attempted up to this many "
        "extra times over the API, then silently via a browser if the failure "
        "looks transient. **Set this to 0 for the fastest possible run** — "
        "failed students (e.g. login issues) are skipped immediately and the "
        "fetch moves on to the next student right away. Students that still "
        "fail are **not included in the analytics**, are marked on the Excel "
        "Summary sheet, and are listed below so you can **re-run just them** if "
        "you decide to."
    )

    if st.button("Fetch Results", type="primary", width="stretch"):
        if not selected_semesters:
            st.error("Select at least one semester.")
        elif not branch_pairs:
            st.error("Select at least one branch.")
        else:
            rolls_by_branch = {}
            for br_name, br_code in branch_pairs:
                scheme = RollScheme(college_code=college_code, batch_year=batch_year, branch_code=br_code)
                rolls = generate_roll_numbers(
                    scheme,
                    regular_start=int(reg_start), regular_end=int(reg_end),
                    include_lateral=include_lateral,
                    lateral_start=int(lat_start), lateral_end=int(lat_end),
                )
                rolls_by_branch[br_name] = (br_code, rolls)

            _execute_run(
                rolls_by_branch, selected_semesters, batch_year,
                max_retries=int(max_retries),
                scope_override=an.scope_summary(
                    students_count=sum(len(rs) for _, rs in rolls_by_branch.values()),
                    branches=list(rolls_by_branch),
                    semesters=list(selected_semesters),
                    batch_year=batch_year,
                    reg_start=int(reg_start), reg_end=int(reg_end),
                    include_lateral=include_lateral,
                    lat_start=int(lat_start), lat_end=int(lat_end),
                ),
            )

    # Post-run status + download from the last completed fetch
    if st.session_state.get("analytics_df") is not None:
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
        st.divider()
        excel_bytes = st.session_state.get("excel_bytes")
        c1, c2, c3, _ = st.columns([1, 1, 1, 2])
        if excel_bytes:
            c1.download_button(
                label="Download Excel",
                data=io.BytesIO(excel_bytes),
                file_name="student_results.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch",
            )
        if st.session_state.get("rerun_groups"):
            if c2.button(
                "Re-run only the failed students",
                width="stretch",
                help="Fetch just the student(s) that failed the last run (same semesters).",
            ):
                st.session_state["trigger_rerun"] = True
                st.rerun()
        if c3.button("View Analysis", width="stretch"):
            _go_to("Analysis")


# ---------------------------------------------------------------------------
# ANALYSIS
# ---------------------------------------------------------------------------
elif nav == "Analysis":
    st.title("Analysis & Reports")
    st.caption("Visuals and reports for the exact selection of your last run.")

    analytics_df = st.session_state.get("analytics_df")
    semesters_in_run = st.session_state.get("analytics_semesters", [])

    if analytics_df is None or not semesters_in_run:
        st.info("No run found yet — fetch results on the Results tab, then come back here.")
        if st.button("Go to Results", type="primary"):
            _go_to("Results")
    else:
        st.caption(st.session_state.get("analytics_scope", ""))

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
            st.markdown(f"**{FOURTH_YEAR_LABEL}**")
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
                width="stretch",
                hide_index=True,
            )

        pf = an.pass_fail_breakdown(analytics_df, semesters_in_run)
        if not pf.empty:
            st.markdown("##### Pass vs Fail")
            st.caption(
                "Pass = the semester record carried an SGPA; Fail = no SGPA "
                "(means at least one F-grade backlog that semester). 'Overall' "
                "sums all semester records."
            )
            chosen = st.selectbox(
                "Semester / scope", pf["scope"].tolist(),
                key="pie_scope", label_visibility="collapsed",
            )
            row = pf[pf["scope"] == chosen].iloc[0]
            passed, failed = int(row["passed"]), int(row["failed"])
            total = passed + failed
            c1, c2, c3, c4 = st.columns([1, 1, 1, 3])
            c1.metric("Passed", passed)
            c2.metric("Failed", failed)
            c3.metric("Pass rate", f"{100.0 * passed / total:.1f}%" if total else "—")
            with c4:
                pie = pd.DataFrame({"Status": ["Passed", "Failed"], "Count": [passed, failed]})
                chart = (
                    alt.Chart(pie)
                    .mark_arc(innerRadius=40, outerRadius=100)
                    .encode(
                        color=alt.Color(
                            "Status:N",
                            scale=alt.Scale(
                                domain=["Passed", "Failed"],
                                range=["#2e7d32", "#d32f2f"],
                            ),
                            legend=alt.Legend(title=None, orient="right"),
                        ),
                        theta=alt.Theta("Count:Q", stack=True),
                        tooltip=["Status:N", "Count:Q"],
                    )
                    .properties(width=260, height=230)
                )
                st.altair_chart(chart, width="content")

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
                width="stretch",
                hide_index=True,
            )
        else:
            st.info("No failing subjects (grade F) in this scope — clean across the board.")

        if not rankings.empty:
            bl = an.backlog_report(analytics_df, semesters_in_run)
            if not bl.empty:
                st.markdown("##### Backlog report (students with F grades)")
                st.caption("One row per failed subject attempt — the list faculty "
                           "usually needs for mentoring and committee meetings.")
                bl_filter = st.text_input(
                    "Filter by roll number", placeholder="e.g. 2451-23-733",
                    key="bl_filter",
                )
                view = bl
                if bl_filter.strip():
                    view = bl[bl["roll_number"].str.upper()
                              .str.contains(bl_filter.strip().upper(), na=False)]
                st.dataframe(
                    view,
                    column_config={
                        "roll_number": cc.TextColumn("Roll number"),
                        "semester_label": cc.TextColumn("Semester"),
                        "subject_code": cc.TextColumn("Subject code"),
                        "subject_name": cc.TextColumn("Subject name"),
                    },
                    width="stretch",
                    hide_index=True,
                )
                st.download_button(
                    "Download backlog CSV",
                    data=view.to_csv(index=False).encode("utf-8"),
                    file_name="backlog_report.csv",
                    mime="text/csv",
                )

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
                    width="stretch",
                    hide_index=True,
                )

        st.divider()
        scope_line = st.session_state.get("analytics_scope", "")
        pdf_bytes = build_pdf_report(analytics_df, semesters_in_run, scope_line)
        c1, c2 = st.columns([1, 3])
        c1.download_button(
            "Download PDF report",
            data=pdf_bytes,
            file_name="gradepulse_results_report.pdf",
            mime="application/pdf",
            type="primary",
            width="stretch",
        )
        c2.caption("PDF includes the KPIs, 4th-year row, per-semester table, top "
                   "failing subjects and backlog summary for this run.")


# ---------------------------------------------------------------------------
# NOTES
# ---------------------------------------------------------------------------
elif nav == "Notes":
    st.title("Faculty Notes")
    st.caption(
        "Add notes about individual students (e.g. detained, department details, "
        "subject codes) so the context is available alongside the reports. Notes are "
        "stored locally on this machine — a shared database comes later."
    )

    st.subheader("Add a note")
    col1, col2 = st.columns([1, 2])
    category = col1.selectbox("Category", NOTE_CATEGORIES)
    roll_number = col1.text_input("Roll number (e.g. 2451-23-750-033)")
    col1_, col2_ = st.columns(2)

    note_text = st.text_area("Note", placeholder="Describe the detail for this student...", height=120)
    if st.button("Save note", type="primary"):
        if not roll_number.strip():
            st.error("Enter a roll number.")
        elif not note_text.strip():
            st.error("Enter the note description.")
        else:
            add_note(roll_number, note_text, category)
            st.success(f"Note saved for {roll_number.strip().upper()}.")

    st.divider()

    notes = load_notes()
    st.subheader("All notes")
    if not notes:
        st.caption("No notes yet.")
    else:
        filter_roll = st.text_input("Filter by roll number", placeholder="e.g. 2451-23-750")
        if filter_roll:
            notes = [n for n in notes if filter_roll.strip().upper() in n["roll_number"]]
        st.dataframe(
            pd.DataFrame(notes),
            column_config={
                "id": cc.NumberColumn("ID", format="%d"),
                "roll_number": cc.TextColumn("Roll number"),
                "category": cc.TextColumn("Category"),
                "note": cc.TextColumn("Note", width="large"),
                "added": cc.TextColumn("Added"),
            },
            width="stretch",
            hide_index=True,
        )
        if notes:
            delete_choice = st.selectbox(
                "Delete a note",
                options=[f"{n['roll_number']} — [{n['category']}] {n['note'][:40]}… ({n['added']})" for n in notes],
                key="notes_delete_choice",
            )
            if st.button("Delete selected note"):
                matching = [n for n in load_notes()
                            if f"{n['roll_number']} — [{n['category']}] {n['note'][:40]}… ({n['added']})" == delete_choice]
                if matching:
                    delete_note(matching[0]["id"])
                    st.success("Note deleted.")
                    st.rerun()
                else:
                    st.error("Could not match the selected note.")


# ---------------------------------------------------------------------------
# FEEDBACK
# ---------------------------------------------------------------------------
elif nav == "Feedback":
    st.title("Feedback")
    st.caption(
        "Feedback from faculty and lecturers helps us improve GradePulse. Submissions "
        "are stored locally for now; a shared database is planned for the future."
    )

    st.subheader("What faculty say")
    for fb in load_feedback():
        with st.container(border=True):
            st.markdown(f"**{fb['name']}** — *{fb['role']}*  \n{fb['date']}")
            st.markdown(fb["message"])

    st.divider()

    st.subheader("Share your feedback")
    col1, col2 = st.columns(2)
    fb_name = col1.text_input("Your name")
    fb_role = col2.text_input("Your role (e.g. Assistant Professor)")
    fb_message = st.text_area("Feedback", placeholder="What worked well? What should improve?", height=120)
    if st.button("Submit feedback", type="primary"):
        if not fb_name.strip() or not fb_role.strip() or not fb_message.strip():
            st.error("Please fill in name, role and feedback.")
        else:
            add_feedback(fb_name, fb_role, fb_message)
            st.success("Thank you! Your feedback has been recorded locally and will be "
                       "reviewed for the next round of improvements.")
            st.rerun()


# ---------------------------------------------------------------------------
# Footer (all tabs)
# ---------------------------------------------------------------------------
st.markdown(
    "<div class='gp-footer'>GradePulse · Student Result Aggregator — developed by "
    "<a href='https://rajendharare.tech'>Rajendhar Are</a> · "
    "<a href='https://linkedin.com/in/rajendhar-are'>LinkedIn</a></div>",
    unsafe_allow_html=True,
)