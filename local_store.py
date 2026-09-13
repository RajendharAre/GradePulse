"""
local_store.py
File-based storage for the Notes and Feedback tabs. No database yet (per
faculty roadmap) — simple JSON files under app_data/ so faculty notes and
feedback survive page reloads. Everything here is git-ignored.

The Feedback tab starts with seeded, positive mock feedback so the tab has
real-looking content until a real feedback database is added later.
"""

import json
import os
import time
from typing import List, Dict

STORE_DIR = "app_data"
NOTES_FILE = os.path.join(STORE_DIR, "notes.json")
FEEDBACK_FILE = os.path.join(STORE_DIR, "feedback.json")
SEED_FILE_MARKER = "__seeded__"

# Mock positive feedback shown the first time the Feedback tab is opened.
# Real feedback will replace these once a database is wired up.
SEED_FEEDBACK = [
    {"name": "Neelakanta Rao", "role": "Assistant Professor", "date": "2026-09-10",
     "message": "The pass/fail analytics straight from the portal saved our department "
                "hours of manual Excel work every semester."},
    {"name": "Padma", "role": "Assistant Professor", "date": "2026-09-10",
     "message": "I could spot the weak subjects across the batch within minutes. Very "
                "useful for planning tutorials and extra classes."},
    {"name": "Dr. Rajesh Kulakarni", "role": "Associate Professor - HOD (CSE - allied)",
     "date": "2026-09-11",
     "message": "Excellent tool for semester review meetings. I would like this extended "
                "to more branches and batches."},
    {"name": "Srinivas Rao", "role": "Assistant Professor", "date": "2026-09-12",
     "message": "Simple to use, and the Excel export matches exactly what we present to "
                "the faculty — very professional."},
]

NOTE_CATEGORIES = ["General", "Detained", "Department", "Subject code", "Transfer", "Other"]


def _ensure_dir() -> None:
    os.makedirs(STORE_DIR, exist_ok=True)


def _read_json(path: str, fallback) -> list:
    _ensure_dir()
    if not os.path.exists(path):
        return fallback()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else fallback()
    except (json.JSONDecodeError, OSError):
        return fallback()


def _write_json(path: str, data: list) -> None:
    _ensure_dir()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def load_notes() -> List[Dict]:
    """All student notes, newest first. Each: {id, roll_number, note, category, added}."""
    return sorted(_read_json(NOTES_FILE, list), key=lambda n: n.get("added", ""), reverse=True)


def add_note(roll_number: str, note: str, category: str = "General") -> None:
    notes = _read_json(NOTES_FILE, list)
    notes.append({
        "id": int(time.time() * 1000),
        "roll_number": roll_number.strip().upper(),
        "note": note.strip(),
        "category": category,
        "added": time.strftime("%Y-%m-%d %H:%M"),
    })
    _write_json(NOTES_FILE, notes)


def delete_note(note_id) -> None:
    notes = [n for n in load_notes() if n.get("id") != note_id]
    _write_json(NOTES_FILE, notes)


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

def load_feedback() -> List[Dict]:
    """Feedback entries, oldest first. Seeds the mocked positive list on first use."""
    def seed():
        data = [dict(e) for e in SEED_FEEDBACK]
        _write_json(FEEDBACK_FILE, data)
        return data
    return _read_json(FEEDBACK_FILE, seed)


def add_feedback(name: str, role: str, message: str) -> None:
    fb = load_feedback()
    fb.append({
        "name": name.strip(),
        "role": role.strip(),
        "message": message.strip(),
        "date": time.strftime("%Y-%m-%d %H:%M"),
    })
    _write_json(FEEDBACK_FILE, fb)