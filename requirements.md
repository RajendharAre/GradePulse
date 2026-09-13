# Requirements — Student Result Aggregator

> This file is the **source of truth for what the tool must do today**.
> When a requirement changes or a new feature is added, update this file
> first, then implement it in the code.

## 1. Purpose

An internal faculty tool that pulls each student's exam results from the
college portal (`matrusri.skolo.in`) and exports them to a single Excel
workbook for grading / promotion / backlog analysis.

## 2. Authentication

- **No admin/faculty credentials are used.**
- Each student is logged in **individually** with their **student account**:
  - Username = roll number (e.g. `2451-23-750-011`)
  - Password = same roll number
- Credentials are derived from the roll number in code and are **never
  stored, logged, or printed**.

## 3. What to fetch (per student)

Per student, per selected semester, capture:

- Subject code, subject name, month/year of exam
- Final grade, credits, status
- Semester summary: SGPA, CGPA, Result (e.g. "Promoted")

Semesters supported (labels used in the UI and Excel):

| Label            |
|------------------|
| I YEAR I SEM     |
| I YEAR II SEM    |
| II YEAR III SEM  |
| II YEAR IV SEM   |
| III YEAR V SEM   |
| III YEAR VI SEM  |

## 4. Roll number scheme

Format: `COLLEGE-BATCH-BRANCH-SERIAL`, e.g. `2451-23-750-001`

- College code: `2451`
- Batch / joining year: `23`
- Branch codes (name → numeric segment; stored in `roll_utils.BRANCH_CODES`,
  verified live 2026-09-13 via each branch's roll-001 login):

| Branch            | Code | Portal group code |
|-------------------|------|-------------------|
| CSE               | 733  | CSE               |
| DS                | 750  | CSD               |
| CIV               | 732  | CIV               |
| ECE               | 735  | ECE               |
| EEE               | 734  | EEE               |
| AIML              | 748  | CSM               |
| CSIT              | 751  | CSIT              |
| IT                | 737  | IT                |
| CIC (Cyber)       | 749  | CIC               |

- Regular serials: `001` – `060`
- Lateral serials: `301` – `306`

The tool can iterate any subset of branches in one run (branch multiselect in
the UI). IOT has no code yet — add it to `BRANCH_CODES` when the college provides one.

## 5. Output (Excel workbook `student_results.xlsx`)

Two categories of sheets, in this order:

1. **`Summary`** — one row per student, one `SGPA`/`Result` column per
   selected semester. Quick promotion/backlog overview.
2. **One sheet per semester** — one row per student, one column per
   subject grade, plus SGPA/CGPA/Result (the table-style view).

> No long-format/"Data" sheet. The per-semester sheets + summary are the
> only tabs, keeping the workbook compact.
>
> *(If a machine-readable long format is ever needed for analysis, it was
> previously available and can be re-added from git history / requirements
> before this change.)*

Consistency rules:
- Every student appears even when they have no data for a semester
  (empty cells instead of missing rows) so counts always line up.
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
  - The headless-browser fallback builds lazily and reuses ONE Chrome for the
    whole run; cookies are cleared before every browser login so sessions can
    never leak between students. It engages ONLY for transient failures when
    retries > 0.
- **Speed rules (faculty requirement — time is crucial)**:
  - `max_retries = 0` means "fastest possible run": a failing student is
    skipped immediately (no extra API attempts, no browser) and the fetch
    moves straight on to the next student.
  - Hard **`Login failed` (credential rejection)** errors NEVER trigger the
    browser fallback, even with retries enabled — the browser fails for the
    same reason and would just burn ~90 s per affected student (verified with
    `2451-23-750-033`, which fails on both API and browser).
- **Results cache**: every fetch mirrors the run to
  `results_cache/<batch_year>/` (long-format CSV + raw JSON) as a
  debugging/repro mirror only — **analytics always read the live run, never
  the cache.**
- Recommended use: once per exam cycle, not repeatedly.

## 7. UI (Streamlit — `app.py`) — 5 tabs

Top nav is a segmented control; the active tab lives in session state
(keyless widget) so buttons anywhere can switch tabs.

1. **Home** — platform overview, developer info (Rajendhar Are,
   `2451-23-750-011`, rajendharare.tech, LinkedIn), a "How GradePulse
   analyses results" explainer, and quick buttons to jump to Results
   / Analysis.
2. **Results** — the aggregator:
   - Section 1 Roll Number Range: batch year, college code, branch
     multiselect (from `BRANCH_CODES`) plus a manual "branch not listed?"
     entry (name + 3-digit code) for this run only; regular + lateral serial
     ranges.
   - Section 2 Semester(s) to fetch.
   - Section 3 Fetch & Download: retries per failed student (with an
     always-visible explanation incl. the `max_retries = 0` fast mode), the
     Fetch button, then post-run status (OK/failed counts), the
     failed-student list, the Excel download and a "View Analysis" shortcut.
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

## 8. Verified scraping details (2026-09-12 / 2026-09-13)

### 8a. Browser selectors (Selenium fallback)

The site is an Angular Material SPA. The real selectors are filled in
`scraper.py` and were verified against live student accounts:

- Login username: `input[formcontrolname='usernameOrEmail']`
- Login password: `input[formcontrolname='password']`
- Login button: `//button[normalize-space(.)='LOGIN']`
- Post-login check: URL contains `dashboard` and no longer `/auth/`
- Results URL: `#/admin-examination-section/student-exam-results`
- Results loaded marker: `[role='tab']` on the results page
- Semester control: `//*[@role='tab' and normalize-space(.)='<label>']`
- Results table: `//table[.//th[normalize-space(.)='Subject Code']]` (rows = `tbody tr`, 7 columns)
- SGPA/CGPA/Result: `td.td-result` cells (text like `SGPA : 9.28`)

### 8b. Direct JSON API (fast path)

The SPA's backend answers on `https://matrusri.skolo.in:8443/cms`. Per student:

| Step | Endpoint | Purpose |
|------|----------|---------|
| 1 | `POST /api/auth/login`  body `{"usernameOrEmail": <roll>, "password": <roll>, "isMobile": false}` | returns `data` = JWT; JWT `sub` = internal `userId` |
| 2 | `GET /studentdetail?userId=<userId>` (Bearer JWT) | returns `data.studentId` |
| 3 | `GET /getAllRecords/s_get_exam_student_results?in_flag=exam_std_result_detail&in_exam_id=0&in_college_id=17&in_course_id=0&in_course_group_id=0&in_course_year_id=0&in_std_id=<studentId>&in_regulation_id=0&in_ispass=0&in_subject_id=0&in_above_fail_subjects=0&in_below_credits=0` | **`in_course_year_id=0` returns ALL semesters in one call** |

The response contains `data.result` (rows grouped — either nested per semester
or a single flat list) where each row carries `course_year_code`
(`IYEARISEM`, `IIYEARIVSEM`, ...), `subject_code/name`, `grade`,
`grade_points`, `credits`, `subject_result`, `sgpa`, `cgpa`, `result`.
The parser buckets rows by `course_year_code` itself (robust to either shape)
and maps codes back to UI labels (e.g. `IIYEARIVSEM` → `II YEAR IV SEM`).

**Known quirks:**
- The JSON backend occasionally logs a student's account as failing while the
  browser succeeds and vice-versa (e.g. roll `2451-23-750-033` failed on both
  paths in the 2026-09-13 benchmark — treat persistently-failing rolls as
  account-level, not pipeline-level). If the API mode fails a roll, the
  browser mode (`fetch_all_results`) is a useful cross-check.
- `in_college_id=17` is constant for MVSR (taken from the SPA's own calls).

If the portal ever changes endpoints or markup, re-verify §8a/§8b before
blaming the data pipeline.

### 8c. Important API quirk — SGPA vs F grades

The API **omits `sgpa`/`cgpa` for a whole semester block whenever that student
has ANY `F`-graded subject in that semester** (verified live on
`2451-23-733-005`: blocks with `F` rows come back with `sgpa: null`, and the
SGPA is repeated on every subject row of clean blocks, e.g. `733-001 → 9.74`).
Consequence for analytics/exports:

- "Promoted / has SGPA" and "Clean pass (no F)" therefore always give the SAME
  number from this data source. That is real portal behaviour, not a bug.
- A missing SGPA on a semester row = the student failed ≥1 subject that
  semester (backlog). Excel renders such semesters as FAILED.

## 9. Analytics layer (Step 2 — `analytics.py`)

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
- **4th Year (III YEAR V SEM + VI SEM) combined** — "IV YEAR VII/VIII" results
  are not announced yet and are never counted; the row only appears when
  students actually have V/VI data (never a phantom 0%).
- **Subject failure ranking** — subjects with most `F` grades (code, name,
  attempts, fails, pass-rate), bar + table.
- **Pass vs Fail pie chart** — donut per semester / overall scope
  (definition: has SGPA = Pass; missing SGPA = Fail); Altair, native to
  Streamlit.
- **Branch comparison** — same metrics per (branch, semester), only when >1
  branch was selected in that run.

Missing semesters (e.g. lateral entries that start later) are counted as NOT
passed — consistent with the Excel FAILED rule.

## 10. Out of scope / future ideas (not built yet)

- Comparing results across batches/branches in one workbook.
- Interactive Plotly-style charts inside the Streamlit UI (Step 3).
- Running thumbnails/photos alongside the results.
- A shared database for faculty Notes and Feedback (currently local JSON
  under `app_data/`, git-ignored) with server-side storage + access control.