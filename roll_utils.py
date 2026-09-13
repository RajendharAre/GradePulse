"""
roll_utils.py
Generates student roll numbers from the college's numbering scheme:

    2451 - 23 - 750 - 001
    |      |    |     |
    |      |    |     +--- Student serial number (e.g. 001-060 regular, 301-306 lateral)
    |      |    +--------- Branch code (e.g. 750 = Data Science)
    |      +-------------- Batch / joining year (23 = joined 2023)
    +--------------------- College code

Each student logs in with their own account (username = password = roll
number), so we only need the roll-number list to iterate over. BRANCH_CODES
holds the branch-name -> numeric branch-code mapping (verified live against
the portal's student accounts 2026-09-13; group_code confirmed for each).
"""

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class RollScheme:
    college_code: str = "2451"
    batch_year: str = "23"   # e.g. "23" for students who joined in 2023
    branch_code: str = "750"  # e.g. "750" = Data Science


def format_roll(scheme: RollScheme, serial: int) -> str:
    """Format a single roll number, e.g. serial=1 -> '2451-23-750-001'."""
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
# Verified live 2026-09-13 by logging into each branch's roll-001 account:
#   name    code   group_code on portal
#   CSE     733    CSE
#   DS      750    CSD
#   CIV     732    CIV
#   ECE     735    ECE
#   EEE     734    EEE
#   AIML    748    CSM          (portal records AIML as group "CSM")
#   CSIT    751    CSIT
#   IT      737    IT
#   CIC     749    CIC          (portal records Cyber Security as group "CIC")
# IOT: no branch code available yet — add here when the college provides it.
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
]


if __name__ == "__main__":
    scheme = RollScheme(batch_year="23", branch_code="750")
    rolls = generate_roll_numbers(scheme)
    print(f"Generated {len(rolls)} roll numbers")
    print(rolls[:3], "...", rolls[-3:])
