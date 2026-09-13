# Requirements — Student Result Aggregator

> This file is the **source of truth for what the tool must do today**.
> When a requirement changes or a new feature is added, update this file
> first, then implement it in the code.
>
> **Portal-specific integration details (endpoints, selectors, account
> behaviour) intentionally live ONLY in the code** and are not tracked here,
> so this document stays safe to share.

## 1. Purpose

An internal faculty tool that pulls each student's exam results from the
college examination portal and exports them to a single Excel workbook for
grading / promotion / backlog analysis.

## 2. Authentication

- **No admin/faculty credentials are used.**
- Each student is logged in **individually** with their **student account**
  (credentials are derived from the roll number in code).
- Credentials are never stored, logged, or printed.

## 3. What to fetch (per student)

Per student, per selected semester, capture:

- Subject code, subject name, month/year of exam
- Final grade, credits, status
- Semester summary: SGPA, CGPA, Result (e.g. "Promoted")

Semesters supported (labels used in the UI and Excel) — a full programme has
**8 semesters, two per year**:

| Label            |
|------------------|
| I YEAR I SEM     |
| I YEAR II SEM    |
| II YEAR III SEM  |
| II YEAR IV SEM   |
| III YEAR V SEM   |
| III YEAR VI SEM  |
| IV YEAR VII SEM  |
| IV YEAR VIII SEM |

Semesters with no announced data are simply not selected; the analytics never
show phantom rows for them.

## 4. Roll number scheme

Format: `COLLEGE-BATCH-BRANCH-SERIAL`

- The branch name → numeric segment mapping lives in
  `roll_utils.BRANCH_CODES` (verified live against the portal's student
  accounts) and drives the UI multiselect; a manual "branch not listed"
  entry is available for anything missing.
- Regular and lateral serial ranges are configurable in the UI.
- The tool can iterate any subset of branches in one run.

## 5. Output (Excel workbook)

Two categories of sheets, in this order:

1. **`Summary`** — one row per student, one `SGPA`/`Result` column per
   selected semester. Quick promotion/backlog overview.
2. **One sheet per semester** — one row per student, one column per
   subject grade, plus SGPA/CGPA/Result (the table-style view).

Consistency rules:

- Every student appears even when they have no data for a semester (empty
  cells instead of missing rows) so counts always line up.
- Students whose login/roll failed get a clear error noted on their
  `Summary` row; they never silently disappear.
- Roll numbers are strings in the scheme format (no broken leading zeros).
- Subject columns in per-semester sheets use the **subject name** (not code);
  if two subjects share a name, the code is appended for uniqueness.
- **A semester with subjects but no SGPA is treated as `FAILED`** in every
  sheet (regardless of the portal's own Result text).
- **Grade `F` = Fail**: every `F` grade cell is filled solid red in the
  per-semester sheets; `FAILED` result cells on the `Summary` sheet get a
  light-red highlight.

## 6. Reliability & etiquette

- **API first (default), browser fallback only when worth it**:
  - Every student is tried via the portal's JSON API first (one shared
    `requests` session; `max_retries` retries, default 2, with a short
    backoff for transient/server hiccups) before a student is reported as
    failed.
  - The headless-browser fallback builds lazily and reuses ONE browser for
    the whole run; cookies are cleared before every browser login so sessions
    can never leak between students. It engages ONLY for transient failures
    when retries > 0.
- **Speed rules (faculty requirement — time is crucial)**:
  - `max_retries = 0` means "fastest possible run": a failing student is
    skipped immediately (no extra API attempts, no browser) and the fetch
    moves straight on to the next student.
  - Hard **`Login failed` (credential rejection)** errors NEVER trigger the
    browser fallback, even with retries enabled — the browser fails for the
    same reason and would just burn ~90 s per affected student (verified
    during benchmarking).
- **Results cache**: every fetch mirrors the run to
  `results_cache/<batch_year>/` (long-format CSV + raw JSON) as a
  debugging/repro mirror only — **analytics always read the live run, never
  the cache.** The cache is git-ignored (real student records).
- Recommended use: once per exam cycle, not repeatedly.

## 7. UI (Streamlit — `app.py`) — 5 tabs

Top nav is a segmented control; the active tab lives in session state
(keyless widget) so buttons anywhere can switch tabs. Access is gated to
authorized institutional users.

1. **Home** — platform overview, developer info, a "How GradePulse
   analyses results" explainer, and quick buttons to jump to Results /
   Analysis.
2. **Results** — the aggregator:
   - Section 1 Roll Number Range: batch year, college code, branch
     multiselect plus a manual "branch not listed?" entry (name + 3-digit
     code) for this run only; regular + lateral serial ranges.
   - Section 2 Semester(s) to fetch (8 available).
   - Section 3 Fetch & Download: retries per failed student (with an
     always-visible explanation incl. the `max_retries = 0` fast mode), the
     Fetch button, then post-run status (OK/failed counts), the
     failed-student list with a **Re-run failed students** button, the Excel
     download and a "View Analysis" shortcut.
3. **Analysis** — the visuals/reports for EXACTLY the last run's selection,
   with an empty state that guides to Results when nothing was fetched yet.
4. **Notes** — faculty notes per roll number (detained, department details,
   subject codes, ...) with categories; stored locally under `app_data/`
   (JSON, no DB yet), searchable and deletable.
5. **Feedback** — seeded positive feedback from faculty plus a submit form;
   stored locally until a real feedback database is built.

Fetching is fully automatic — **no credential fields and no fetch-method
choice are exposed in the UI**. Login credentials are always derived from
each roll number; see §6 for the auto-API + selective-browser-fallback
strategy.

**Analytics scope:** any analytics shown reflect EXACTLY the branch(es) +
batch + serial range selected in this run — never previously-cached data.
Students with login/fetch failures are excluded from analytics and listed so
the faculty can re-run just them.

## 8. Portal integration (implementation only)

The exact endpoints, selectors, auth flow and any quirks of the college's
portal are implemented in `scraper.py` and are deliberately NOT reproduced
here. If the portal ever changes, fix things in the code; the pipeline's
public contracts (types, error strings) are the DataFrames and
`StudentResult`/`SemesterResult` dataclasses.

### 8c. Important API quirk — SGPA vs F grades

The portal **omits `sgpa`/`cgpa` for a whole semester block whenever that
student has ANY `F`-graded subject in that semester** (verified live).
Consequence for analytics/exports:

- "Promoted / has SGPA" and "Clean pass (no F)" therefore always give the SAME
  number from this data source. That is real portal behaviour, not a bug.
- A missing SGPA on a semester row = the student failed ≥1 subject that
  semester (backlog). Excel renders such semesters as FAILED.

## 9. Analytics layer (`analytics.py`)

Consumes ONLY the long-format DataFrame of the CURRENT run (selected
branch(es) + batch + serial range). Never previously-cached data, and
login-failed students are excluded from every metric.

Three pass/fail definitions (per faculty request):
1. **Promoted / has SGPA** — SGPA present for the semester.
2. **Clean pass (no F)** — has SGPA and zero `F` grades in the semester.
3. **Subject pass-rate** — % of subject attempts with grade != `F`.

Views exposed in the app after a fetch:

- Headline KPIs (the 3 definitions + avg SGPA) over the whole selected scope.
- Per-semester table (all three definitions per semester).
- **4th Year (V + VI) combined** summary for the current academic cohort row;
  later years only appear when a run actually selects those semesters (never
  a phantom 0%).
- **Subject failure ranking** — subjects with most `F` grades (code, name,
  attempts, fails, pass-rate), bar + table.
- **Pass vs Fail pie chart** — donut per semester / overall scope
  (definition: has SGPA = Pass; missing SGPA = Fail); Altair, native to
  Streamlit.
- **Branch comparison** — same metrics per (branch, semester), only when >1
  branch was selected in that run.
- **Backlog report** — every (roll, name, subject, semester) with grade `F`,
  searchable by roll number, plus a per-student **backlog CSV** (roll, name,
  backlogs, earliest-failed semester, last-updated semester).

Missing semesters (e.g. lateral entries that start later) are counted as NOT
passed — consistent with the Excel FAILED rule.

## 10. Pre-deployment extras

- **Visit counter (no DB)** — counts browser sessions via a free public
  badge API (increments once per session; a secondary provider is coded as a
  fallback for the cloud runner). If the service is unreachable the UI shows
  "—" and nothing breaks. For real unique-visitor analytics later, swap in a
  proper analytics service — `visit_counter.py` is the only file to touch.
- **Re-run failed students** — refetches ONLY the rolls that failed, using
  the same batch/branch/retries settings; re-run results update the active
  run scope.
- **UI polish + animations** — branded header (GradePulse + visits badge),
  soft fade/slide entrance for cards via custom CSS (`gp-*` classes), a
  footer with the developer's site, and all widget calls on the modern
  `width="stretch"` API.
- **Analytics PDF report** — `report_export.py` (fpdf2, zero web fonts)
  generates a printable A4 summary on demand: scope + run KPIs, the 4th-year
  combined row, per-semester pass-rate table, top failing subjects, and the
  backlog line. Backed by `st.download_button`, no server write.

## 11. Out of scope / future ideas (not built yet)

- Comparing results across batches/branches in one workbook.
- Interactive Plotly-style charts inside the Streamlit UI.
- Running thumbnails/photos alongside the results.
- A shared database for faculty Notes and Feedback (currently local JSON
  under `app_data/`, git-ignored) with server-side storage + access control.
- GPT-style natural-language explanation of pacing/backlog (idea noted, not
  selected — the faculty-focused reports above cover it better).

## 12. Deployment notes

- Streamlit Community Cloud is the default host: deploy straight from the
  GitHub repo; Streamlit auto-installs `requirements.txt` and serves
  `app.py`. Free-tier apps sleep after ~12h idle and cold-start on the next
  visit (30–60s) — acceptable for a faculty tool.
- Streamlit re-runs the script per interaction; the visit counter's
  "once per session" behaviour matters on cloud because instances are
  recreated — the session-gated increment keeps counts sane without a DB.
- `results_cache/` and `app_data/` are git-ignored so real student data and
  local notes never leave the repo; until a DB is added, Notes/Feedback are
  per-instance.
- The headless-browser fallback needs a local Chrome install; on managed
  cloud runners without a browser it is unavailable — runs should use
  `max_retries = 0` (fast mode) there.
- Keep the repository **private**: portal integration code + real student
  records are not for public exposure.