"""
models.py — the Question object and answer-grading rules.

The old code passed raw sqlite tuples around and indexed them by position
(``q[5]`` for the correct answer, ``q[7]`` for the open-ended flag...). That
made it impossible to add a field — which is why skill-level analytics were
missing. Everything now flows through the Question dataclass instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction

from config import (
    DIFFICULTY_WEIGHT,
    NO_RATIONALE_SENTINEL,
    normalize_difficulty,
    normalize_section,
    resolve_asset,
)


@dataclass
class Question:
    """One item from the College Board bank, as stored by sat_importer.py."""

    question_id: str
    section: str
    domain: str
    skill: str
    difficulty: str
    question_img: str | None
    correct_answer: str
    rationale: str | None
    is_open_ended: bool

    # Filled in by the adaptive engine when a question is placed in a module.
    module_number: int = 0
    tier: str = ""
    position: int = 0

    # ---------------------------------------------------------------- factory

    @classmethod
    def from_row(cls, row) -> "Question":
        """Build from a sqlite3.Row or any mapping with the bank's columns."""
        get = row.__getitem__ if hasattr(row, "keys") else (lambda k: row[k])

        def field_or(key, default=""):
            try:
                value = get(key)
            except (KeyError, IndexError):
                return default
            return default if value is None else value

        return cls(
            question_id=str(field_or("question_id")),
            section=normalize_section(field_or("section")),
            domain=(str(field_or("domain", "General")).strip() or "General"),
            skill=(str(field_or("skill", "General")).strip() or "General"),
            difficulty=normalize_difficulty(field_or("difficulty")),
            question_img=field_or("question_img", None) or None,
            correct_answer=repair_answer_key(
                field_or("correct_answer"), bool(field_or("is_open_ended", 0))),
            rationale=field_or("rationale", None) or None,
            is_open_ended=bool(field_or("is_open_ended", 0)),
        )

    # ------------------------------------------------------------- properties

    @property
    def weight(self) -> float:
        """Difficulty weight used by the routing calculation."""
        return DIFFICULTY_WEIGHT.get(self.difficulty, 1.0)

    @property
    def image_path(self) -> str | None:
        """Openable path to the question screenshot, or None if missing."""
        return resolve_asset(self.question_img)

    @property
    def is_gradable(self) -> bool:
        """
        False when the row carries no recoverable answer key.

        Such an item must never be served: with no key, every answer the
        student gives is scored wrong, which is worse than not asking at all.
        """
        return bool(self.correct_answer)

    @property
    def rationale_path(self) -> str | None:
        """
        Openable path to the rationale screenshot.

        sat_importer.py stores the literal string "No explanation image
        generated." when it could not crop one. The old review screen tried to
        open that as a filename and showed a red "Image missing" error; we
        return None instead so the UI can say something sensible.
        """
        if not self.rationale or self.rationale.strip() == NO_RATIONALE_SENTINEL:
            return None
        return resolve_asset(self.rationale)

    @property
    def has_rationale(self) -> bool:
        return self.rationale_path is not None

    @property
    def label(self) -> str:
        """Short human label, e.g. 'Craft and Structure · Hard'."""
        return f"{self.domain} · {self.difficulty}"

    # ---------------------------------------------------------------- grading

    def check(self, user_answer: str | None) -> bool:
        """Is this answer correct? Handles both multiple choice and grid-ins."""
        return answers_match(user_answer, self.correct_answer, self.is_open_ended)


# ---------------------------------------------------------------------------
# ANSWER EQUIVALENCE
# ---------------------------------------------------------------------------

_FRACTION_RE = re.compile(r"^([+-]?\d+)\s*/\s*(\d+)$")
_MIXED_RE = re.compile(r"^([+-]?\d+)\s+(\d+)\s*/\s*(\d+)$")


def _clean(text) -> str:
    """
    Strip the noise students and PDFs both introduce.

    Whitespace is collapsed but not deleted, because a single space is
    meaningful in a mixed number like '2 1/2'.
    """
    if text is None:
        return ""
    out = str(text)
    out = out.replace("\u2212", "-").replace("\u2013", "-")   # unicode minus / en-dash
    out = out.replace("\u00a0", " ")                           # non-breaking space
    for junk in ("$", ",", "%"):
        out = out.replace(junk, "")
    out = " ".join(out.split()).strip()
    # A trailing period is sentence punctuation from the PDF, not a decimal point.
    while out.endswith("."):
        out = out[:-1].strip()
    return out


def _as_number(text: str):
    """
    Parse a student-produced response into a Fraction, or None.

    Accepts 0.5, .5, 1/2, -3/4, and mixed numbers like '2 1/2'.
    """
    if not text:
        return None

    mixed = _MIXED_RE.match(text)
    if mixed:
        whole, num, den = (int(mixed.group(1)), int(mixed.group(2)), int(mixed.group(3)))
        if den == 0:
            return None
        magnitude = Fraction(abs(whole)) + Fraction(num, den)
        return -magnitude if whole < 0 else magnitude

    frac = _FRACTION_RE.match(text)
    if frac:
        den = int(frac.group(2))
        if den == 0:
            return None
        return Fraction(int(frac.group(1)), den)

    try:
        return Fraction(text)          # handles '12', '-3', '0.5', '.5'
    except (ValueError, ZeroDivisionError):
        return None


def _split_accepted(correct_raw: str) -> list[str]:
    """
    College Board sometimes lists several acceptable forms in one field,
    e.g. '.75, 3/4' or '7 or -7'. Split them into individual candidates.
    """
    text = str(correct_raw or "").strip()
    if not text:
        return []
    parts = re.split(r"\s*(?:,|;|\bor\b|\band\b)\s*", text, flags=re.IGNORECASE)
    return [p for p in (part.strip() for part in parts) if p]


# ---------------------------------------------------------------------------
# ANSWER-KEY REPAIR
# ---------------------------------------------------------------------------
# Some grid-in rows arrive with the rationale's opening words in the answer
# field instead of the answer: "is", "is 74. The y-intercept of a line…",
# "is either 8 or 9. The first equation…". The answer is usually still in
# there, but a literal comparison against "is" marks a correct student wrong.
# One real example: a student entered 74, the key said "is 74. The y-…", and
# the app told him he was wrong. That is the worst possible bug in a study
# tool — it teaches the opposite of the truth.
#
# This repairs the key ON READ. sat_importer.py is not touched and its output
# is not rewritten; nothing here runs at import time.

_KEY_IS_CLEAN = re.compile(r"^[-+]?[\d.,/\s+-]+$")
# "is 74. The y-intercept…"  ->  74        (first number only, anchored at the
# start, so "is 3. It's given that Cameron drove 60 miles…" yields 3, not 60)
_KEY_LEADING_IS = re.compile(r"^is\s+([-+]?\d+(?:\.\d+)?(?:\s*/\s*\d+)?)\b")
# "is either 7, 8, or 13. Since…"  ->  7, 8, 13
_KEY_EITHER = re.compile(
    r"^is\s+either\s+((?:[-+]?\d+(?:\.\d+)?(?:\s*/\s*\d+)?)"
    r"(?:\s*(?:,|or)\s*[-+]?\d+(?:\.\d+)?(?:\s*/\s*\d+)?)*)", re.IGNORECASE)

#: Key that could not be recovered at all — the item cannot be graded.
UNGRADABLE = ""


def repair_answer_key(raw, is_open_ended: bool = False) -> str:
    """
    Return a usable answer key, or UNGRADABLE ("") when the row carries none.

    Multiple choice is left alone: every letter key in the bank is already a
    clean A-D. Only grid-ins are affected by the rationale bleed.
    """
    text = str(raw or "").strip()
    if not text or not is_open_ended:
        return text
    if _KEY_IS_CLEAN.match(text):
        return text

    either = _KEY_EITHER.match(text)
    if either:
        parts = re.split(r"\s*(?:,|or)\s*", either.group(1), flags=re.IGNORECASE)
        return ", ".join(p.strip() for p in parts if p.strip())

    leading = _KEY_LEADING_IS.match(text)
    if leading:
        return leading.group(1).replace(" ", "")

    return UNGRADABLE


def answers_match(user_answer, correct_answer, is_open_ended: bool = False) -> bool:
    """
    Compare a student's answer to the key.

    Multiple choice is a simple case-insensitive letter match. Grid-ins compare
    numerically so 0.5, .5 and 1/2 all count as the same answer — which the old
    string comparison got wrong.
    """
    user = _clean(user_answer)
    if not user:
        return False

    candidates = _split_accepted(correct_answer)
    if not candidates:
        return False

    if not is_open_ended:
        letter = user.upper()
        return any(letter == _clean(c).upper() for c in candidates)

    user_num = _as_number(user)
    for candidate in candidates:
        cleaned = _clean(candidate)
        if user.upper() == cleaned.upper():          # exact text match
            return True
        candidate_num = _as_number(cleaned)
        if user_num is not None and candidate_num is not None:
            if user_num == candidate_num:
                return True
            # The SAT accepts truncated/rounded decimals for repeating values
            # as long as the grid is filled, e.g. 2/3 -> .666 or .667.
            if _decimal_equivalent(user, candidate_num):
                return True
    return False


def _decimal_equivalent(user_text: str, exact: Fraction) -> bool:
    """Accept a correctly truncated or rounded 4-character decimal entry."""
    try:
        user_value = float(Fraction(user_text))
    except (ValueError, ZeroDivisionError):
        return False
    exact_value = float(exact)
    if exact_value == 0:
        return abs(user_value) < 1e-9
    # Grid-ins allow ~3-4 significant digits; 0.2% tolerance covers truncation.
    return abs(user_value - exact_value) / max(abs(exact_value), 1e-9) < 0.002


# ---------------------------------------------------------------------------
# ATTEMPT RECORD
# ---------------------------------------------------------------------------

@dataclass
class AttemptRecord:
    """What the user actually did on one question, ready to persist."""

    question: Question
    selected_answer: str = ""
    is_correct: bool = False
    was_flagged: bool = False
    eliminated: set = field(default_factory=set)
    time_spent_ms: int = 0
    position: int = 0
    # "Log every question where you weren't at least 90% confident — even the
    # ones you got right." '' | 'sure' | 'shaky'
    confidence: str = ""
    is_redo: bool = False
    # Filled in by attempt_repo.record_attempts once the row exists.
    attempt_id: int | None = None

    @property
    def skipped(self) -> bool:
        return not self.selected_answer.strip()

    @property
    def is_lucky(self) -> bool:
        """Right answer, but you weren't sure. Counts as a miss for study purposes."""
        return self.is_correct and self.confidence == "shaky"

    @property
    def needs_logging(self) -> bool:
        """Belongs in the error log: wrong, or right-but-shaky."""
        return (not self.is_correct) or self.confidence == "shaky"

    @property
    def time_spent_seconds(self) -> float:
        return self.time_spent_ms / 1000.0

    def as_row(self, session_id: int, module_row_id: int | None) -> tuple:
        """Tuple in the column order attempt_repo.record_attempts expects."""
        q = self.question
        return (
            session_id,
            module_row_id,
            q.question_id,
            self.position,
            q.section,
            q.domain,
            q.skill,
            q.difficulty,
            q.correct_answer,
            self.selected_answer,
            1 if self.is_correct else 0,
            1 if self.was_flagged else 0,
            ",".join(sorted(self.eliminated)) if self.eliminated else "",
            int(self.time_spent_ms),
            self.confidence or "",
            1 if self.is_redo else 0,
        )
