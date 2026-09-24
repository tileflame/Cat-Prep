"""
config.py, single source of truth for paths, theme tokens and SAT blueprint data.

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
    roots = (DATA_DIR, Path.cwd(), BASE_DIR)
    for root in roots:
        resolved = root / candidate
        if resolved.exists():
            return str(resolved)

    # Last resort: the separators are from a different operating system.
    #
    # An import run on Windows stores "images\\abc123.png". Windows treats the
    # backslash as a separator, so that works there forever — but move the
    # folder to a Mac and every single question goes blank, because on a POSIX
    # system a backslash is an ordinary character in a filename and the file
    # "images\\abc123.png" genuinely does not exist. It fails silently: the
    # question is there, the image file is there, and the app shows nothing.
    #
    # Only reached after the literal path has already failed, so a filename
    # that really does contain a backslash still resolves first.
    swapped = text.replace("\\", "/") if "\\" in text else text.replace("/", "\\")
    if swapped != text:
        for root in roots:
            resolved = root / Path(swapped)
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
        # Questions per module per SKILL, inside each domain.
        #
        # A domain quota alone is not the real test. "Craft and Structure: 7"
        # was satisfied just as happily by seven Words in Context questions as
        # by the mix Bluebook actually gives you, and a module with no Cross-Text
        # Connections in it is missing a question type you will certainly meet.
        # These counts are the typical shape of an official Bluebook module; each
        # domain's numbers add up to its quota above.
        "skill_quota": {
            "Craft and Structure": {
                "Words in Context": 4,
                "Text Structure and Purpose": 2,
                "Cross-Text Connections": 1,
            },
            "Information and Ideas": {
                "Central Ideas and Details": 2,
                "Command of Evidence": 3,        # textual and quantitative together
                "Inferences": 2,
            },
            "Standard English Conventions": {
                "Boundaries": 4,
                "Form, Structure, and Sense": 3,
            },
            "Expression of Ideas": {
                "Transitions": 3,
                "Rhetorical Synthesis": 3,
            },
        },
        # The order Bluebook puts them in. Vocabulary first, Rhetorical Synthesis
        # ("a student has taken the following notes") last. Skills listed in the
        # same block are one group on the real test, mixed together and ordered
        # easiest to hardest as a unit: that is how the conventions questions
        # appear, punctuation and verb form interleaved.
        "skill_order": [
            ["Words in Context"],
            ["Text Structure and Purpose"],
            ["Cross-Text Connections"],
            ["Central Ideas and Details"],
            ["Command of Evidence"],
            ["Inferences"],
            ["Boundaries", "Form, Structure, and Sense"],
            ["Transitions"],
            ["Rhetorical Synthesis"],
        ],
        # Group by skill in the order above, easiest to hardest inside each.
        "order": "skill",
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
        # No fixed per-skill counts for Math: the real test's mix of skills
        # inside a domain varies far more from form to form than R&W's does.
        # Each domain's quota is spread evenly over whichever of its skills your
        # bank actually holds, so a module gets breadth instead of seven
        # "Linear functions" questions in a row.
        "skill_quota": None,
        # Bluebook Math is not grouped by topic. All four domains are mixed and
        # the module runs from easiest to hardest.
        "order": "difficulty",
        # Grid-ins (student-produced responses) came last on the PAPER SAT. The
        # digital test mixes them in, placed by difficulty like everything else.
        "grid_ins_last": False,
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
    TIER_HARD: "Upper route, harder Module 2",
    TIER_EASY: "Lower route, easier Module 2",
}

TIER_COLOR = {
    TIER_BASELINE: C.BLUE,
    TIER_HARD: C.GREEN,
    TIER_EASY: C.ORANGE,
}


# ---------------------------------------------------------------------------
# SCALED SCORE ESTIMATE
# ---------------------------------------------------------------------------
# College Board's real conversion tables are not public and vary per form, so
# this is an approximation and is labelled an estimate everywhere it appears.
#
# WHY THIS IS A TABLE AND NOT A FORMULA
# -------------------------------------
# It used to be `base + span * pct**0.9`, which is smooth and wrong. A smooth
# curve cannot reproduce the one thing everybody notices about a real SAT
# conversion: the top is brutally steep and the middle is not. On the real
# test one wrong answer costs about 20 points and the tenth costs about 10.
# The old formula gave 800 for 54/54, 53/54 AND 52/54, because 490 * (52/54)
# ** 0.9 still lands above the ceiling — so three different performances
# collapsed onto a perfect score and everything through the middle ran 40-50
# points hot. A raw-to-scaled table has no such problem: the anchors ARE the
# shape.
#
# The numbers below are the upper-route conversion, read off published digital
# SAT practice-test tables and rounded to 10. Points between anchors are
# linearly interpolated, so a raw score the table does not name still gets a
# sensible answer, and so do short drills that are not a full section.
#
# Each section has its own table because the sections are different lengths:
# 54 questions in Reading and Writing, 44 in Math. One wrong answer is a
# bigger fraction of Math, and the table says so.

SCORE_ANCHORS = {
    SECTION_RW: {
        "reference_total": 54,
        # (raw correct out of 54, scaled score)
        "points": [
            (54, 800), (53, 780), (52, 770), (51, 760), (50, 750), (49, 740),
            (48, 730), (47, 720), (46, 710), (45, 700), (44, 690), (43, 680),
            (42, 670), (41, 660), (40, 650), (38, 640), (36, 620), (34, 600),
            (32, 580), (30, 570), (27, 540), (24, 510), (20, 470), (16, 430),
            (12, 390), (8, 340), (4, 280), (0, 200),
        ],
    },
    SECTION_MATH: {
        "reference_total": 44,
        # (raw correct out of 44, scaled score)
        "points": [
            (44, 800), (43, 780), (42, 770), (41, 760), (40, 750), (39, 740),
            (38, 730), (37, 720), (36, 710), (35, 700), (34, 690), (33, 680),
            (32, 670), (31, 660), (30, 650), (28, 630), (26, 610), (24, 580),
            (22, 560), (20, 540), (18, 510), (15, 470), (12, 430), (9, 390),
            (6, 340), (3, 280), (0, 200),
        ],
    },
}

# The floor and top of the scaled range a single section is reported on.
SCORE_FLOOR = 200
SCORE_TOP = 800

# What route you finished on decides how much of that range you can reach.
# This is the structural fact about an adaptive test: answering the same number
# of questions correctly is worth less if the second module was the easy one,
# because the easy module's questions are worth less. Everything above the
# floor is scaled by (ceiling - floor) / (top - floor), which both caps the
# score and compresses the curve underneath it — a lower-route 40/54 should not
# land one point under a lower-route 54/54.
#
#   hard      you were routed up. Full range.
#   easy      you were routed down. Real lower-route ceilings sit near 600-650.
#   baseline  a drill or a single mixed module: no routing happened, so there
#             is no upper module to prove 800 on. Capped deliberately.
SCORE_ROUTE_CEILING = {
    TIER_HARD: 800,
    TIER_EASY: 640,
    TIER_BASELINE: 760,
}

# Below this many questions an estimate is arithmetic, not evidence. The score
# is still shown (it is the only number a drill can offer) but the UI flags it.
SCORE_MIN_RELIABLE_QUESTIONS = 20


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
