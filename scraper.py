"""
scraper.py
Core fetching logic for pulling students' exam results from matrusri.skolo.in.

Credential model (per requirements.md):
    - NO admin/faculty login is used.
    - Each student is logged in individually using their STUDENT account:
          username = roll number (e.g. "2451-23-750-011")
          password = same roll number

Two strategies (both keep results identical):

AUTO PATH (default, fetch_all_results_auto):
    Tries the FAST PATH for every student; if a student fails there, the tool
    silently falls back to a headless browser session *just for that student*
    (no visible choice exposed to the user). This gives the speed of the API
    with the resilience of the browser for the occasional failure.

  FAST PATH (fetch_all_results_api):
    The portal's SPA talks to a Spring JSON backend on port 8443. We log in
    via  POST /cms/api/auth/login  (returns a JWT), resolve the internal
    student id via  GET /cms/studentdetail?userId=..., then call
    GET /cms/getAllRecords/s_get_exam_student_results  once with
    in_course_year_id=0 — which returns ALL semesters for that student in a
    single response. ~0.35 s per student, no browser.

BROWSER PATH (fetch_all_results):
    One shared, headless Chrome is built lazily and only when needed. Cookies
    are cleared before each student's login so sessions can never leak.

The selectors and API details are verified live (2026-09-12/13) and tracked
in requirements.md §8.
"""

import re
import base64
import json
import random
import time
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional

import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

BASE_URL = "https://matrusri.skolo.in"
LOGIN_URL = f"{BASE_URL}/#/pages/auth/login?page=%2Fstudent-dashboard%2Fstudent-dashboard"
# TODO(selector/url): after a *student* login, the results live on a student
# dashboard page (admin URLs are NOT accessible to a student account). Replace
# this with the real student results path once you confirm it in the browser.
STUDENT_RESULTS_URL = f"{BASE_URL}/#/admin-examination-section/student-exam-results"

DEFAULT_WAIT = 15  # seconds, generous because it's a JS SPA

# ---------------------------------------------------------------------------
# Direct JSON API (discovered 2026-09-13 from the SPA's network traffic).
# The Angular SPA hits a Spring backend on port 8443. These endpoints let us
# fetch a student's results with ~3 HTTP requests, no browser at all:
#
#   1. POST /cms/api/auth/login            {usernameOrEmail, password, isMobile:false}
#        -> { data: <JWT> }                JWT "sub" = internal userId
#   2. GET  /cms/studentdetail?userId=<id> -> { data: { studentId: ... } }
#   3. GET  /cms/getAllRecords/s_get_exam_student_results
#          + in_std_id=<studentId> & in_course_year_id=0 (0 = ALL semesters)
#        -> { data: { result: [ [subject rows], ... ] } }  (one group per semester)
# ---------------------------------------------------------------------------
API_BASE = "https://matrusri.skolo.in:8443/cms"
API_LOGIN_URL = f"{API_BASE}/api/auth/login"
API_STUDENT_DETAIL_URL = f"{API_BASE}/studentdetail"
API_EXAM_RESULTS_URL = f"{API_BASE}/getAllRecords/s_get_exam_student_results"
API_COLLEGE_ID = 17  # MVSR Engineering College (taken from the SPA's requests)


@dataclass
class SubjectResult:
    sl_no: str
    subject_code: str
    subject_name: str
    month_year: str
    final_grade: str
    credits: str
    status: str


@dataclass
class SemesterResult:
    semester_label: str
    subjects: List[SubjectResult] = field(default_factory=list)
    sgpa: Optional[str] = None
    cgpa: Optional[str] = None
    result: Optional[str] = None  # e.g. "Promoted"


@dataclass
class StudentResult:
    roll_number: str
    semesters: Dict[str, SemesterResult] = field(default_factory=dict)
    error: Optional[str] = None


def build_driver(headless: bool = True) -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1400,1000")
    service = webdriver.ChromeService(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.implicitly_wait(3)
    return driver


def student_login(driver: webdriver.Chrome, roll_number: str) -> None:
    """
    Log in as a single student.
    username = roll_number, password = roll_number (per requirements.md).
    """
    driver.get(LOGIN_URL)
    wait = WebDriverWait(driver, DEFAULT_WAIT)

    # Real selector (verified 2026-09-12): Angular Material login form.
    try:
        username_field = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[formcontrolname='usernameOrEmail']"))
        )
    except TimeoutException:
        raise TimeoutException("STEP 1/4 FAILED: username field not found on login page")
    username_field.clear()
    username_field.send_keys(roll_number)

    # Real selector (verified 2026-09-12): Angular Material password field.
    try:
        password_field = driver.find_element(By.CSS_SELECTOR, "input[formcontrolname='password']")
    except NoSuchElementException:
        raise NoSuchElementException("STEP 2/4 FAILED: password field not found")
    password_field.clear()
    password_field.send_keys(roll_number)

    # Real selector (verified 2026-09-12): the LOGIN submit button.
    try:
        login_button = driver.find_element(By.XPATH, "//button[normalize-space(.)='LOGIN']")
    except NoSuchElementException:
        raise NoSuchElementException("STEP 3/4 FAILED: LOGIN button not found")
    login_button.click()

    # Post-login check (real, verified 2026-09-12): the student dashboard route.
    # NOTE: the LOGIN url itself contains "student-dashboard" (as a ?page= param),
    # so we must also require that we left the auth route.
    try:
        wait.until(lambda d: "dashboard" in d.current_url and "/auth/" not in d.current_url)
    except TimeoutException:
        current_url = driver.current_url
        raise TimeoutException(
            f"STEP 4/4 FAILED: after clicking LOGIN the dashboard never loaded "
            f"(current URL: {current_url or 'about:blank'})"
        )
    log.info("Logged in as %s", roll_number)


def open_results_page(driver: webdriver.Chrome) -> bool:
    """Navigate to the student's exam-results view. Returns False if it can't load."""
    wait = WebDriverWait(driver, DEFAULT_WAIT)
    try:
        driver.get(STUDENT_RESULTS_URL)
        # Real marker (verified 2026-09-12): the semester tab strip appears
        # once the results panel has loaded.
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "[role='tab']")))
        return True
    except TimeoutException:
        log.warning("Results page did not load")
        return False


def select_semester(driver: webdriver.Chrome, semester_label: str) -> bool:
    """Click/select the tab or dropdown option for a given semester label."""
    wait = WebDriverWait(driver, DEFAULT_WAIT)
    try:
        # Real control (verified 2026-09-12): semester switch is an Angular
        # Material tab strip — each tab is [role='tab'] with exact label text.
        # (Note: the first tab is "Semwise Final Marks", which we must never match.)
        tab = wait.until(
            EC.element_to_be_clickable((By.XPATH, f"//*[@role='tab' and normalize-space(.)='{semester_label}']"))
        )
        tab.click()
        time.sleep(0.5)
        return True
    except (TimeoutException, NoSuchElementException):
        log.warning("Semester tab '%s' not found (student may not have this semester yet)", semester_label)
        return False


def extract_current_semester_table(driver: webdriver.Chrome, semester_label: str) -> Optional[SemesterResult]:
    """
    Reads the currently-displayed results table (SI.No, Subject Code,
    Subject Name, Month Year, Final Grade, Credits, Status) plus the
    SGPA / CGPA / RESULT summary line.
    """
    wait = WebDriverWait(driver, DEFAULT_WAIT)
    try:
        # Real table (verified 2026-09-12): the subject table is the one whose
        # header contains a "Subject Code" column.
        table = wait.until(EC.presence_of_element_located(
            (By.XPATH, "//table[.//th[normalize-space(.)='Subject Code']]")
        ))
    except TimeoutException:
        log.warning("No results table found for semester '%s'", semester_label)
        return None

    rows = table.find_elements(By.CSS_SELECTOR, "tbody tr")
    subjects = []
    for row in rows:
        cells = [c.text.strip() for c in row.find_elements(By.TAG_NAME, "td")]
        if len(cells) < 7:
            continue
        if any(c.startswith(("SGPA", "CGPA", "RESULT")) for c in cells if c):
            continue
        subjects.append(SubjectResult(
            sl_no=cells[0],
            subject_code=cells[1],
            subject_name=cells[2],
            month_year=cells[3],
            final_grade=cells[4],
            credits=cells[5],
            status=cells[6],
        ))

    # Real summary (verified 2026-09-12): cells with class td-result, e.g.
    # "SGPA : 9.28", "CGPA : 9.28", "RESULT : Promoted". Join & regex parse.
    sgpa = cgpa = result = None
    try:
        summary_cells = driver.find_elements(By.CSS_SELECTOR, "td.td-result")
        summary_text = " ".join(c.text.strip() for c in summary_cells)
        if not summary_text.strip():
            # fallback: any element that visibly contains the full summary line
            el = driver.find_element(By.XPATH, "//*[contains(., 'SGPA') and contains(., 'RESULT')]")
            summary_text = el.text
        # Expected text e.g. "SGPA : 9.28 CGPA : 9.28 RESULT : Promoted"
        sgpa_m = re.search(r"SGPA\s*:\s*([\d.]+)", summary_text)
        cgpa_m = re.search(r"CGPA\s*:\s*([\d.]+)", summary_text)
        result_m = re.search(r"RESULT\s*:\s*(\w+)", summary_text)
        sgpa = sgpa_m.group(1) if sgpa_m else None
        cgpa = cgpa_m.group(1) if cgpa_m else None
        result = result_m.group(1) if result_m else None
    except NoSuchElementException:
        log.warning("No SGPA/CGPA/Result summary found for semester '%s'", semester_label)

    return SemesterResult(semester_label=semester_label, subjects=subjects, sgpa=sgpa, cgpa=cgpa, result=result)


def fetch_student_results(
    driver: webdriver.Chrome,
    roll_number: str,
    semester_labels: List[str],
) -> StudentResult:
    """
    Fetch results for ONE student across the requested semesters using the
    shared driver. Cookies are cleared first so a reused browser never leaks
    the previous student's session into this one.
    """
    student = StudentResult(roll_number=roll_number)

    try:
        driver.delete_all_cookies()
    except Exception:
        pass

    try:
        student_login(driver, roll_number)
    except (TimeoutException, NoSuchElementException) as exc:
        log.warning("Login failed for %s: %s", roll_number, exc)
        student.error = "Login failed"
        return student

    if not open_results_page(driver):
        student.error = "Results page not available"
        return student

    for sem in semester_labels:
        if not select_semester(driver, sem):
            continue
        sem_result = extract_current_semester_table(driver, sem)
        if sem_result:
            student.semesters[sem] = sem_result

    return student


def fetch_all_results(
    roll_numbers: List[str],
    semester_labels: List[str],
    headless: bool = True,
    delay_between_students: float = 0.5,
    max_retries: int = 2,
    retry_delay: float = 3.0,
    progress_callback=None,
) -> List[StudentResult]:
    """
    Main entry point: loops over all roll numbers using ONE reused browser.

    Speed & reliability trade-offs:
      - A single browser is kept alive for the whole run (no Chrome relaunch
        per student). Cookies are cleared before every student's login, so
        sessions can never leak between students.
      - Students that fail (e.g. transient login timeouts / server throttling)
        are retried up to `max_retries` times with a short pause in between,
        so burst failures like a whole rate-limit block recover on their own.

    progress_callback(index, total, roll_number) -- optional, called after
    each student is processed (handy for a Streamlit progress bar).
    """
    driver = build_driver(headless=headless)
    results: List[StudentResult] = []
    try:
        for i, roll in enumerate(roll_numbers, start=1):
            log.info("[%d/%d] Fetching %s", i, len(roll_numbers), roll)
            student = _fetch_one_with_retry(
                driver, roll, semester_labels,
                max_retries=max_retries, retry_delay=retry_delay,
            )
            results.append(student)
            if progress_callback:
                progress_callback(i, len(roll_numbers), roll)
            time.sleep(delay_between_students)  # be polite to the server
    finally:
        driver.quit()

    return results


def _fetch_one_with_retry(
    driver: webdriver.Chrome,
    roll_number: str,
    semester_labels: List[str],
    max_retries: int,
    retry_delay: float,
) -> StudentResult:
    """Attempt one student, retrying transient failures up to max_retries times."""
    last: Optional[StudentResult] = None
    for attempt in range(max_retries + 1):
        if attempt:
            log.warning("%s attempt %d/%d", roll_number, attempt, max_retries)
            time.sleep(retry_delay)
        try:
            student = fetch_student_results(driver, roll_number, semester_labels)
        except Exception as exc:
            log.exception("Unexpected error for %s", roll_number)
            student = StudentResult(roll_number=roll_number, error=f"Unexpected: {exc}")

        if not student.error:
            return student
        last = student
        # leave the browser on a clean state before the next attempt
        try:
            driver.delete_all_cookies()
        except Exception:
            pass

    return last


# ---------------------------------------------------------------------------
# FAST PATH: direct JSON API (no browser).
#
# The portal's SPA backend returns everything we need as JSON:
#   - one login + one results call gives ALL semesters for a student,
#   - about 0.3-0.4 s per student (vs ~4-5 s with Selenium).
# ---------------------------------------------------------------------------

_SEM_CODE_PATTERN = re.compile(r"^(?P<year>[IVX]+)YEAR(?P<sem>[IVX]+)SEM$")


def semester_label_from_code(code: str) -> Optional[str]:
    """
    Convert the API's "course_year_code" (e.g. "IYEARISEM", "IIYEARIVSEM")
    back to the UI label used elsewhere (e.g. "I YEAR I SEM", "II YEAR IV SEM").
    Returns None when the code isn't a semester code.
    """
    m = _SEM_CODE_PATTERN.match(code or "")
    if not m:
        return None
    return f"{m.group('year')} YEAR {m.group('sem')} SEM"


def _clean_subject_name(name: Optional[str]) -> str:
    """The API returns some subject names with embedded newlines — flatten them."""
    if not name:
        return ""
    return " ".join(str(name).split())


def fetch_student_results_api(
    session: requests.Session,
    roll_number: str,
    semester_labels: List[str],
) -> StudentResult:
    """
    Fetch ONE student via the portal's JSON API using a shared requests.Session.

    Uses the same credential model as the browser path: username AND password
    are both the roll number. Returns a StudentResult (error message set if the
    account can't be logged into / has no data).
    """
    headers = {"Content-Type": "application/json"}
    student = StudentResult(roll_number=roll_number)

    # 1) login
    try:
        resp = session.post(
            API_LOGIN_URL,
            json={"usernameOrEmail": roll_number, "password": roll_number, "isMobile": False},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        login = resp.json()
    except (requests.RequestException, ValueError) as exc:
        student.error = f"Login request failed: {exc}"
        return student

    token = login.get("data")
    if not login.get("success") or not token:
        student.error = "Login failed"
        return student
    auth = {"Authorization": f"Bearer {token}"}

    # 2) internal student id (JWT sub = userId, studentId is different and needed)
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        user_id = json.loads(base64.urlsafe_b64decode(payload))["sub"]

        detail = session.get(
            API_STUDENT_DETAIL_URL, headers=auth, params={"userId": user_id}, timeout=30
        )
        detail.raise_for_status()
        student_id = detail.json().get("data", {}).get("studentId")

        if not student_id:
            student.error = "No student record found"
            return student

        # 3) results — in_course_year_id=0 returns ALL semesters in one call
        params = {
            "in_flag": "exam_std_result_detail",
            "in_exam_id": 0,
            "in_college_id": API_COLLEGE_ID,
            "in_course_id": 0,
            "in_course_group_id": 0,
            "in_course_year_id": 0,
            "in_std_id": student_id,
            "in_regulation_id": 0,
            "in_ispass": 0,
            "in_subject_id": 0,
            "in_above_fail_subjects": 0,
            "in_below_credits": 0,
        }
        results = session.get(API_EXAM_RESULTS_URL, headers=auth, params=params, timeout=30)
        results.raise_for_status()
        payload_res = results.json()
    except (requests.RequestException, ValueError, IndexError, KeyError, json.JSONDecodeError) as exc:
        student.error = f"API failure: {exc}"
        return student

    if not payload_res.get("success"):
        err = payload_res.get("data", {}).get("errorDetails") if isinstance(payload_res.get("data"), dict) else None
        student.error = f"Results fetch failed: {err or payload_res.get('message', '')}"
        return student

    groups = payload_res.get("data", {}).get("result")
    if not isinstance(groups, list) or not groups:
        student.error = "No results data"
        return student

    # Bucket rows by semester ourselves. The portal returns the rows either
    # nested (one inner list per semester) or as a single flat list, so we
    # key on each row's own course_year_code rather than trusting the nesting.
    per_semester: Dict[str, List[dict]] = {}
    for group in groups:
        flat = group if isinstance(group, list) else [group]
        for row in flat:
            if not isinstance(row, dict):
                continue
            label = semester_label_from_code(row.get("course_year_code"))
            if label:
                per_semester.setdefault(label, []).append(row)

    for label in semester_labels:
        rows = per_semester.get(label)
        if not rows:
            continue
        summary = rows[0]
        student.semesters[label] = SemesterResult(
            semester_label=label,
            subjects=[_parse_api_subject(r) for r in rows],
            sgpa=str(summary["sgpa"]) if summary.get("sgpa") is not None else None,
            cgpa=str(summary["cgpa"]) if summary.get("cgpa") is not None else None,
            result=summary.get("result") or None,
        )

    if not student.semesters:
        student.error = "No matching semester data"
    else:
        log.info("API ok: %s -> %d semester(s)", roll_number, len(student.semesters))

    return student


def _parse_api_subject(row: dict) -> SubjectResult:
    return SubjectResult(
        sl_no=str(row.get("sort_order") or ""),
        subject_code=row.get("subject_code") or "",
        subject_name=_clean_subject_name(row.get("subject_name")),
        month_year=row.get("exam_month_yr") or "",
        final_grade=row.get("grade") or "",
        credits=str(row.get("credits") or ""),
        status=row.get("subject_result") or "",
    )


def _fetch_one_via_api_with_retry(
    session: requests.Session,
    roll_number: str,
    semester_labels: List[str],
    max_retries: int,
    retry_delay: float,
) -> StudentResult:
    """Attempt one student over the API, retrying transient failures."""
    last: Optional[StudentResult] = None
    for attempt in range(max_retries + 1):
        if attempt:
            log.warning("%s API attempt %d/%d", roll_number, attempt, max_retries)
            time.sleep(retry_delay)
        try:
            student = fetch_student_results_api(session, roll_number, semester_labels)
        except Exception as exc:
            log.exception("Unexpected error for %s", roll_number)
            student = StudentResult(roll_number=roll_number, error=f"Unexpected: {exc}")
        if not student.error:
            return student
        last = student
    return last


def fetch_all_results_api(
    roll_numbers: List[str],
    semester_labels: List[str],
    delay_between_students: float = 0.15,
    max_retries: int = 2,
    retry_delay: float = 2.0,
    progress_callback=None,
) -> List[StudentResult]:
    """
    Main fast-path entry point. One shared requests.Session, ~3 HTTP calls per
    student, so a whole batch (60 students x all semesters) finishes in well
    under a minute instead of the ~10+ min a Selenium run needs.

    A tiny jitter delay between students keeps the burst polite to the portal;
    anything that still fails is auto-retried via `max_retries`.
    """
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})

    results: List[StudentResult] = []
    for i, roll in enumerate(roll_numbers, start=1):
        log.info("[%d/%d] Fetching %s", i, len(roll_numbers), roll)
        student = _fetch_one_via_api_with_retry(
            session, roll, semester_labels, max_retries=max_retries, retry_delay=retry_delay,
        )
        results.append(student)
        if progress_callback:
            progress_callback(i, len(roll_numbers), roll)
        if i < len(roll_numbers):
            time.sleep(delay_between_students + random.uniform(0.0, 0.05))

    return results


def fetch_all_results_auto(
    roll_numbers: List[str],
    semester_labels: List[str],
    delay_between_students: float = 0.15,
    max_retries: int = 2,
    retry_delay: float = 2.0,
    headless: bool = True,
    progress_callback=None,
) -> List[StudentResult]:
    """
    Default entry point used by the UI.

    Strategy per student:
      1. Try the fast JSON API (shared requests.Session, retried up to
         `max_retries` times).
      2. If the student still fails, quietly fall back to a browser session
         for THAT student only.

    The browser driver is built lazily on the FIRST student that needs it and
    reused for every later fallback, so a fully-API-clean run never opens
    Chrome at all. If any student fails even after retries and browser
    fallback, their StudentResult keeps an `error` so the UI can exclude them
    from analytics.

    progress_callback(index, total, roll_number) -- optional, called once per
    student after it is done (API tried, browser fallback if needed).
    """
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})

    driver = None  # created lazily, only when a browser fallback is first needed
    results: List[StudentResult] = []
    try:
        for i, roll in enumerate(roll_numbers, start=1):
            log.info("[%d/%d] Fetching %s", i, len(roll_numbers), roll)
            student = _fetch_one_via_api_with_retry(
                session, roll, semester_labels, max_retries=max_retries, retry_delay=retry_delay,
            )
            if student.error:
                log.warning("%s: API failed (%s) — falling back to browser", roll, student.error)
                if driver is None:
                    log.info("Building headless browser for fallbacks...")
                    driver = build_driver(headless=headless)
                fb = _fetch_one_with_retry(
                    driver, roll, semester_labels,
                    max_retries=max_retries, retry_delay=retry_delay,
                )
                if not fb.error:
                    log.info("%s: recovered via browser", roll)
                    student = fb

            results.append(student)
            if progress_callback:
                progress_callback(i, len(roll_numbers), roll)
            if i < len(roll_numbers):
                time.sleep(delay_between_students + random.uniform(0.0, 0.05))
    finally:
        if driver is not None:
            driver.quit()

    return results