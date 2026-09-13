"""
roll_utils.py
Generates student roll numbers from the institution's numbering scheme:

    COLLEGE - BATCH - BRANCH - SERIAL

Each student logs in with their own account (credentials derived from the
roll number in code), so we only need the roll-number list to iterate over.
BRANCH_CODES holds the branch-name -> numeric branch-code mapping
(verified live against the portal's student accounts).
"""

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class RollScheme:
    college_code: str = "2451"
    batch_year: str = "23"   # e.g. "23" for students who joined in 2023
    branch_code: str = "750"  # branch-code segment


def format_roll(scheme: RollScheme, serial: int) -> str:
    """Format a roll number from the college-batch-branch-serial scheme."""
    return f"{scheme.college_code}-{scheme.batch_year}-{scheme.branch_code}-{serial:03d}"


def generate_roll_numbers(
    scheme: RollScheme,
    regular_start: int = 1,
    regular_end: int = 60,
    include_lateral: bool = True,
    lateral_start: int = 301,
    lateral_end: int = 306,
) -> List[str]:
    """
    Build the full list of roll numbers for a given batch/branch.

    Regular entries: serials regular_start..regular_end (default 1-60)
    Lateral entries: serials lateral_start..lateral_end (default 301-306)
    """
    rolls = [format_roll(scheme, s) for s in range(regular_start, regular_end + 1)]
    if include_lateral:
        rolls += [format_roll(scheme, s) for s in range(lateral_start, lateral_end + 1)]
    return rolls


# Branch name -> numeric branch-code segment.
# Verified live against the portal's student accounts (roll-001 per branch).
BRANCH_CODES = {
    "CSE": "733",
    "DS": "750",
    "CIV": "732",
    "ECE": "735",
    "EEE": "734",
    "AIML": "748",
    "CSIT": "751",
    "IT": "737",
    "CIC": "749",
}

SEMESTER_LABELS = [
    "I YEAR I SEM",
    "I YEAR II SEM",
    "II YEAR III SEM",
    "II YEAR IV SEM",
    "III YEAR V SEM",
    "III YEAR VI SEM",
    "IV YEAR VII SEM",
    "IV YEAR VIII SEM",
]


if __name__ == "__main__":
    scheme = RollScheme(batch_year="23", branch_code="750")
    rolls = generate_roll_numbers(scheme)
    print(f"Generated {len(rolls)} roll numbers")
    print(rolls[:3], "...", rolls[-3:])
