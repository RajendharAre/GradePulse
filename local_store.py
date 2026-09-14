"""
local_store.py
Storage for the Notes and Feedback tabs.

Primary store: a Google Spreadsheet (gspread + a service-account key from
.streamlit/secrets.toml) so submissions from all faculty sessions land in one
place the developer can open. If the spreadsheet is not configured or is
unreachable, every call falls back to local JSON files under app_data/
(git-ignored) so the app never breaks.
"""

import json
import os
import time
from typing import List, Optional

import streamlit as st

STORE_DIR = "app_data"
NOTES_FILE = os.path.join(STORE_DIR, "notes.json")
FEEDBACK_FILE = os.path.join(STORE_DIR, "feedback.json")

NOTE_CATEGORIES = ["General", "Detained", "Department", "Subject code", "Transfer", "Other"]

NOTES_TAB = "Notes"
FEEDBACK_TAB = "Feedback"
_TAB_HEADERS = {
    NOTES_TAB: ["id", "roll_number", "note", "category", "added"],
    FEEDBACK_TAB: ["name", "role", "message", "date"],
}

# Which backend the last store call actually used, so the UI can tell faculty
# when submissions only reach this device. One of:
#   "sheets"          — shared spreadsheet reached
#   "local-only"      — no spreadsheet configured (secrets missing)
#   "local-fallback"  — configured but unreachable, fell back to local JSON
#   "unknown"         — no store call has happened yet
_BACKEND_STATE = {"state": "unknown", "at": 0.0}


def _mark(state: str) -> None:
    _BACKEND_STATE["state"] = state
    _BACKEND_STATE["at"] = time.time()


def store_backend() -> str:
    """Current store backend; re-probes the sheet when the last result is stale."""
    state = _BACKEND_STATE["state"]
    if state != "unknown" and time.time() - _BACKEND_STATE["at"] < 10:
        return state
    if not _sheet_config():
        _mark("local-only")
        return "local-only"
    try:
        _central_load(NOTES_TAB)
        _mark("sheets")
    except Exception:
        _mark("local-fallback")
    return _BACKEND_STATE["state"]


# ---------------------------------------------------------------------------
# Central (Google Sheets) access — best effort
# ---------------------------------------------------------------------------

def _sheet_config() -> Optional[dict]:
    try:
        gcp = st.secrets.get("gcp") or {}
        doc_id = st.secrets.get("spreadsheet_id")
    except Exception:
        return None
    if not gcp or not doc_id:
        return None
    if isinstance(gcp, str):
        try:
            gcp = json.loads(gcp)
        except (TypeError, ValueError):
            return None
    return {"gcp": gcp, "doc_id": doc_id}


def _worksheet(tab: str):
    cfg = _sheet_config()
    if not cfg:
        return None
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_info(
        cfg["gcp"],
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    sh = gspread.authorize(creds).open_by_key(cfg["doc_id"])
    try:
        return sh.worksheet(tab)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab, rows=1000, cols=len(_TAB_HEADERS[tab]))
        ws.append_row(_TAB_HEADERS[tab])
        return ws


def _central_load(tab: str) -> List[dict]:
    ws = _worksheet(tab)
    if ws is None:
        return []
    rows = ws.get_all_values()
    if len(rows) < 2:
        return []
    headers = rows[0]
    out = []
    for row in rows[1:]:
        rec = {}
        for i, header in enumerate(headers):
            rec[header] = row[i] if i < len(row) else ""
        if any(str(v).strip() for v in rec.values()):
            out.append(rec)
    return out


def _central_append(tab: str, record: dict) -> None:
    ws = _worksheet(tab)
    if ws is None:
        return
    ws.append_row([record.get(h, "") for h in _TAB_HEADERS[tab]],
                  value_input_option="USER_ENTERED")


def _central_delete_note(note_id) -> bool:
    ws = _worksheet(NOTES_TAB)
    if ws is None:
        return False
    cell = ws.find(str(note_id), in_column=1)
    if cell is None:
        return False
    ws.delete_rows(cell.row)
    return True


# ---------------------------------------------------------------------------
# Local JSON fallback
# ---------------------------------------------------------------------------

def _ensure_dir() -> None:
    os.makedirs(STORE_DIR, exist_ok=True)


def _read_json(path: str) -> list:
    _ensure_dir()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _write_json(path: str, data: list) -> None:
    _ensure_dir()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def load_notes() -> List[dict]:
    """All student notes, newest first. Each: {id, roll_number, note, category, added}."""
    if _sheet_config():
        try:
            rows = _central_load(NOTES_TAB)
            _mark("sheets")
            for row in rows:
                try:
                    row["id"] = int(row["id"])
                except (TypeError, ValueError):
                    pass
            return sorted(rows, key=lambda n: str(n.get("added", "")), reverse=True)
        except Exception:
            _mark("local-fallback")
    else:
        _mark("local-only")
    return sorted(_read_json(NOTES_FILE), key=lambda n: str(n.get("added", "")), reverse=True)


def _is_duplicate_note(roll: str, text: str) -> bool:
    for e in load_notes():
        if (e.get("roll_number", "").strip().upper() == roll
                and e.get("note", "").strip().lower() == text.lower()):
            return True
    return False


def add_note(roll_number: str, note: str, category: str = "General") -> str:
    """Add a note. Returns 'added', 'duplicate' or 'error'."""
    roll = roll_number.strip().upper()
    text = note.strip()
    if not roll or not text:
        return "error"
    if _is_duplicate_note(roll, text):
        return "duplicate"
    record = {
        "id": int(time.time() * 1000),
        "roll_number": roll,
        "note": text,
        "category": category,
        "added": time.strftime("%Y-%m-%d %H:%M"),
    }
    if _sheet_config():
        try:
            _central_append(NOTES_TAB, record)
            _mark("sheets")
            return "added"
        except Exception:
            _mark("local-fallback")
    else:
        _mark("local-only")
    notes = _read_json(NOTES_FILE)
    notes.append(record)
    _write_json(NOTES_FILE, notes)
    return "added"


def delete_note(note_id) -> None:
    if _sheet_config():
        try:
            if _central_delete_note(note_id):
                _mark("sheets")
                return
        except Exception:
            _mark("local-fallback")
    else:
        _mark("local-only")
    notes = [n for n in load_notes() if n.get("id") != note_id]
    _write_json(NOTES_FILE, notes)


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

def load_feedback() -> List[dict]:
    """Feedback entries, oldest first."""
    if _sheet_config():
        try:
            rows = _central_load(FEEDBACK_TAB)
            _mark("sheets")
            return sorted(rows, key=lambda f: str(f.get("date", "")))
        except Exception:
            _mark("local-fallback")
    else:
        _mark("local-only")
    return _read_json(FEEDBACK_FILE)


def _is_duplicate_feedback(name: str, role: str, message: str) -> bool:
    for e in load_feedback():
        if (e.get("name", "").strip().lower() == name.lower()
                and e.get("role", "").strip().lower() == role.lower()
                and e.get("message", "").strip().lower() == message.lower()):
            return True
    return False


def add_feedback(name: str, role: str, message: str) -> str:
    """Submit feedback. Returns 'added', 'duplicate' or 'error'."""
    n = name.strip()
    r = role.strip()
    m = message.strip()
    if not n or not r or not m:
        return "error"
    if _is_duplicate_feedback(n, r, m):
        return "duplicate"
    record = {
        "name": n,
        "role": r,
        "message": m,
        "date": time.strftime("%Y-%m-%d %H:%M"),
    }
    if _sheet_config():
        try:
            _central_append(FEEDBACK_TAB, record)
            _mark("sheets")
            return "added"
        except Exception:
            _mark("local-fallback")
    else:
        _mark("local-only")
    fb = _read_json(FEEDBACK_FILE)
    fb.append(record)
    _write_json(FEEDBACK_FILE, fb)
    return "added"