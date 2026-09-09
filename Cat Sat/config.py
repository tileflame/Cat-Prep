"""
config.py — single source of truth for paths, theme tokens and SAT blueprint data.

Everything else in Cat SAT imports from here so there is exactly one place to
change a colour, a file location, or a test blueprint.

IMPORTANT: sat_importer.py is treated as read-only. It writes to
``database/questions.db`` and ``images/`` *relative to the current working
directory*.  We mirror those locations here but resolve them against this
file's folder, so the app runs correctly no matter where you launch it from.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# PATHS
# ---------------------------------------------------------------------------

# Folder that contains main.py / this file.
BASE_DIR = Path(__file__).resolve().parent

# Where the database and images live. Normally right next to the code, but the
# test suite points this at a throwaway sandbox by setting CATSAT_DATA_DIR
# BEFORE importing this module — so running tests can never touch, overwrite or
# delete your real imported question bank.
_OVERRIDE = os.environ.get("CATSAT_DATA_DIR", "").strip()
DATA_DIR = Path(_OVERRIDE).resolve() if _OVERRIDE else BASE_DIR
USING_SANDBOX = bool(_OVERRIDE)

DB_DIR = DATA_DIR / "database"
IMAGE_DIR = DATA_DIR / "images"
PDF_DIR = BASE_DIR / "pdfs"          # always the real one; the app only reads it

# The question bank, written by sat_importer.py. We never write to it.
QUESTION_DB = DB_DIR / "questions.db"

# All *user* data (sessions, attempts, notes, settings) lives in its own file.
# Keeping it separate means you can delete or rebuild the question bank without
# losing a single minute of study history.
PROGRESS_DB = DB_DIR / "progress.db"


def ensure_dirs() -> None:
    """Create the folders the app expects. Safe to call repeatedly."""
    for folder in (DB_DIR, IMAGE_DIR):
        folder.mkdir(parents=True, exist_ok=True)
    if not USING_SANDBOX:
        PDF_DIR.mkdir(parents=True, exist_ok=True)


def resolve_asset(stored_path: str | None) -> str | None:
    """
    Turn a path stored in the database into something openable.

    sat_importer.py stores relative paths like ``images/abc123.png``. If the app
    is launched from another directory those break, so we fall back to resolving
    against BASE_DIR. Returns None when there is nothing usable.
    """
    if not stored_path:
        return None

    text = str(stored_path).strip()
    if not text or text == NO_RATIONALE_SENTINEL:
        return None

    candidate = Path(text)
    if candidate.is_absolute():
        return str(candidate) if candidate.exists() else None

    # Try the data folder first, then the working directory (matches legacy
    # behaviour), then the app folder (correct when launched from elsewhere).
    for root in (DATA_DIR, Path.cwd(), BASE_DIR):
        resolved = root / candidate
        if resolved.exists():
            return str(resolved)
    return None


# sat_importer.py writes this literal string when it could not crop a rationale.
# It is NOT a path, so the UI must not try to open it as one.
NO_RATIONALE_SENTINEL = "No explanation image generated."


# ---------------------------------------------------------------------------
# THEME TOKENS
# ---------------------------------------------------------------------------

class C:
    """Colour palette. Named tokens instead of hex strings scattered everywhere."""

    BG = "#0F1017"          # app background
    SURFACE = "#1A1B26"     # cards
    SURFACE_2 = "#232536"   # nested cards / hover
    SURFACE_3 = "#12131C"   # sunken wells (image viewers, canvases)
    BORDER = "#2A2D42"

    TEXT = "#E8ECF1"
    TEXT_DIM = "#98A2B3"
    TEXT_FAINT = "#6B7280"

    GREEN = "#2ECC71"
    GREEN_DARK = "#27AE60"
    BLUE = "#3B82F6"
    BLUE_DARK = "#2563EB"
    PURPLE = "#8E44AD"
    PURPLE_DARK = "#732D91"
    AMBER = "#F1C40F"
    ORANGE = "#E67E22"
    RED = "#E74C3C"
    RED_DARK = "#C0392B"
    SLATE = "#34495E"
    SLATE_DARK = "#2C3E50"

    # Difficulty accents, used consistently everywhere difficulty is shown.
    EASY = "#2ECC71"
    MEDIUM = "#F1C40F"
    HARD = "#E74C3C"


DIFFICULTY_COLOR = {"Easy": C.EASY, "Medium": C.MEDIUM, "Hard": C.HARD}


def accuracy_color(pct: float) -> str:
    """Consistent red/amber/green mapping for any accuracy percentage."""
    if pct >= 75:
        return C.GREEN
    if pct >= 50:
        return C.AMBER
    return C.RED


FONT = "Inter"  # falls back to the system default if Inter isn't installed


# ---------------------------------------------------------------------------
# SAT BLUEPRINT
# ---------------------------------------------------------------------------
# Structure of the digital SAT as delivered in Bluebook:
#   Reading and Writing : 2 modules x 27 questions x 32 minutes
#   Math                : 2 modules x 22 questions x 35 minutes
# Domain weightings follow College Board's published content distribution.
# Within a Reading and Writing module, questions testing similar skills are
# grouped together and ordered easiest -> hardest.

SECTION_RW = "Reading and Writing"
SECTION_MATH = "Math"

# Alternate spellings the importer may produce, normalised on read.
SECTION_ALIASES = {
    "reading and writing": SECTION_RW,
    "reading & writing": SECTION_RW,
    "reading/writing": SECTION_RW,
    "rw": SECTION_RW,
    "english": SECTION_RW,
    "math": SECTION_MATH,
    "mathematics": SECTION_MATH,
}

BLUEPRINT = {
    SECTION_RW: {
        "questions_per_module": 27,
        "minutes_per_module": 32,
        # Questions per module, per domain (7+7+7+6 = 27).
        "domain_quota": {
            "Craft and Structure": 7,
            "Information and Ideas": 7,
            "Standard English Conventions": 7,
            "Expression of Ideas": 6,
        },
        # Presentation order inside a module.
        "domain_order": [
            "Craft and Structure",
            "Information and Ideas",
            "Standard English Conventions",
            "Expression of Ideas",
        ],
        "grid_ins_last": False,
    },
    SECTION_MATH: {
        "questions_per_module": 22,
        "minutes_per_module": 35,
        # 7+7+4+4 = 22.
        "domain_quota": {
            "Algebra": 7,
            "Advanced Math": 7,
            "Problem-Solving and Data Analysis": 4,
            "Geometry and Trigonometry": 4,
        },
        "domain_order": [
            "Algebra",
            "Advanced Math",
            "Problem-Solving and Data Analysis",
            "Geometry and Trigonometry",
        ],
        # Student-produced response (grid-in) questions come last in a real module.
        "grid_ins_last": True,
    },
}

# Order in which sections are delivered in a full-length sitting.
FULL_TEST_SECTION_ORDER = [SECTION_RW, SECTION_MATH]

# Minutes of break between the Reading and Writing section and the Math section.
SECTION_BREAK_MINUTES = 10


# ---------------------------------------------------------------------------
# ADAPTIVE ROUTING
# ---------------------------------------------------------------------------

TIER_BASELINE = "baseline"   # Module 1: mixed difficulty
TIER_HARD = "hard"           # Module 2 upper route
TIER_EASY = "easy"           # Module 2 lower route

# Target proportion of Easy / Medium / Hard for each tier.
DIFFICULTY_MIX = {
    TIER_BASELINE: {"Easy": 0.34, "Medium": 0.33, "Hard": 0.33},
    TIER_HARD:     {"Easy": 0.10, "Medium": 0.30, "Hard": 0.60},
    TIER_EASY:     {"Easy": 0.60, "Medium": 0.30, "Hard": 0.10},
}

# A correct Hard question says more about ability than a correct Easy one, so
# routing uses a difficulty-weighted accuracy by default.
DIFFICULTY_WEIGHT = {"Easy": 1.0, "Medium": 1.25, "Hard": 1.5}

# Module 1 accuracy at or above this routes you to the harder Module 2.
# College Board does not publish the real cut score; 65% is a widely used
# working estimate and is adjustable in Settings.
DEFAULT_ROUTING_THRESHOLD = 0.65

TIER_LABEL = {
    TIER_BASELINE: "Baseline (mixed)",
    TIER_HARD: "Upper route — harder Module 2",
    TIER_EASY: "Lower route — easier Module 2",
}

TIER_COLOR = {
    TIER_BASELINE: C.BLUE,
    TIER_HARD: C.GREEN,
    TIER_EASY: C.ORANGE,
}


# ---------------------------------------------------------------------------
# SCALED SCORE ESTIMATE
# ---------------------------------------------------------------------------
# College Board's real conversion tables are not public and vary per form.
# This is a routing-aware approximation, always labelled as an estimate.
# The key modelled behaviour: taking the lower-route Module 2 caps your ceiling.

SCORE_MODEL = {
    TIER_HARD:     {"base": 330, "span": 490, "floor": 200, "ceiling": 800},
    TIER_EASY:     {"base": 200, "span": 520, "floor": 200, "ceiling": 640},
    TIER_BASELINE: {"base": 250, "span": 550, "floor": 200, "ceiling": 800},
}


# ---------------------------------------------------------------------------
# DRILLS
# ---------------------------------------------------------------------------

DRILL_PRESETS = [6, 8, 10, 12, 15, 20, 30]
DRILL_DEFAULT_COUNT = 8

# Per-question pacing targets (seconds) used to colour the timing column in
# review. Derived from the real section pacing: 32min/27q and 35min/22q.
PACING_TARGET_SECONDS = {
    SECTION_RW: 71,
    SECTION_MATH: 95,
}


def normalize_section(raw: str | None) -> str:
    """Map whatever the importer wrote onto a canonical section name."""
    if not raw:
        return SECTION_RW
    key = " ".join(str(raw).split()).strip().lower()
    return SECTION_ALIASES.get(key, str(raw).strip())


def normalize_difficulty(raw: str | None) -> str:
    """Coerce a difficulty string to exactly Easy / Medium / Hard."""
    if not raw:
        return "Medium"
    key = str(raw).strip().capitalize()
    return key if key in ("Easy", "Medium", "Hard") else "Medium"


# Set CATSAT_DEBUG=1 to print blueprint fidelity reports to the console.
DEBUG = os.environ.get("CATSAT_DEBUG", "") not in ("", "0", "false", "False")
