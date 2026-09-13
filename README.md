# GradePulse

> **Bulk-fetch student exam results from the college portal, export Excel reports, and get pass/fail analytics & subject-level insights for any year/semester.**

GradePulse logs in as each student automatically (username = password = roll
number), pulls all semesters from the portal's JSON API (~0.35 s/student,
with an automatic browser fallback if the API hiccups), writes a CSV/JSON
cache, and serves a Streamlit UI for faculty.

**What you get:**
- **Excel reports** — per-semester sheets with subject-name columns,
  FAILED / F-grade highlighting
- **Pass/fail analytics** — three definitions: Promoted (has SGPA), Clean
  pass (no F), Subject pass-rate — across any selected year/semester
- **Subject failure ranking** — which subjects fail the most, with pass-rates
- **Branch comparison** — side-by-side metrics when multiple branches are selected
- **Scoped by design** — analytics always reflect exactly the branch(es),
  batch and roll range you pick in that run; login-failed students are excluded

> **Requirements are tracked in `requirements.md`.** Read it first — it is
> the source of truth for behavior. Update it whenever requirements change.

## Before anything else

- Confirm with your college's examination section / IT that this
  automation is authorized. You're pulling academic records programmatically —
  get the sign-off in writing, even if it's just an email thread.
- **No admin credentials are used.** The tool logs in as each student using
  their roll number as username and password. Credentials are derived from the
  roll number and are never stored or logged.
- `results_cache/` contains real student records and is **git-ignored** —
  never push it to a public repository.

## Setup

```bash
pip install -r requirements.txt
```

Requires Chrome. The driver is managed automatically by `webdriver-manager`
(see `build_driver()` in `scraper.py`); it downloads a matching `chromedriver`
on first run and caches it.

## Running it

```bash
streamlit run app.py
```

This opens a local web page where faculty enter:
- Batch year, college code, and roll-number range (regular + lateral)
- **Branch(es)** to include (multiselect, e.g. CSE, DS, AIML, ...), with a
  manual entry for any branch code missing from the list
- Which semester(s) to pull
- Retries per failed student (with an in-app explanation)

...then click **Fetch & Analytics** and download the generated Excel file.
No login fields are needed — credentials come from the roll numbers. Fetching
is automatic: API fast path first, silent browser fallback per student if needed.

Each run also writes a **results cache** (`results_cache/<batch>/`) as
long-format CSV + raw JSON per branch. The **analytics section** below the
button is computed from that exact run: the three pass/fail definitions, a
per-semester table, a combined **4th Year (III YEAR V + VI SEM)** row, a
subject failure ranking, and a branch comparison when >1 branch is selected.

## Selectors

The DOM selectors for the Angular Material portal are already filled in
and were verified live (2026-09-12). They are tracked in
`requirements.md` §8. If the portal markup changes, re-verify those
selectors before debugging the data pipeline.

## Excel output

- **`Summary`** sheet — one row per student, SGPA/Result for every
  semester selected, for a quick promotion/backlog overview.
- **One sheet per semester** — one row per student, one column **per
  subject name** (grade), plus SGPA/CGPA/Result — the table-style view.

Highlighting & rules (consistent across all sheets):
- Grade `F` = **Fail** → that cell gets a **solid red background**.
- Missing SGPA for a semester → the student is treated as **FAILED**
  (shown in red on the Summary sheet).
- Every student gets a row even when a semester has no data.

## Notes on scale & etiquette

- Fetching is fully automatic under the hood: API fast path for every
  student, and if that fails, a headless browser session for just that
  student (no exposed options). A full 66-student × 6-semester run through
  the API takes **~30 seconds**.
- A tiny jitter delay sits between students, and any transient failure is
  auto-retried (`max_retries`, default 2) before a student is reported as
  failed.
- Browser fallback reuses one session and clears cookies before every
  student's login (sessions can never leak between students).
- In the 2026-09-13 benchmark, roll `2451-23-750-033` failed on both the API
  and browser paths — treat persistently-failing rolls as account-level
  issues, not bugs in the pipeline.
- Consider who else can see the downloaded Excel file — it contains every
  student's grades, so treat it like any other sensitive academic record
  (don't leave it in a shared/public drive).