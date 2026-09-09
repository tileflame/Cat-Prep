"""
question_repo.py — every read against the College Board question bank.

No SQL against questions.db lives anywhere else in the app. The adaptive engine
asks this module for pools of questions; the UI asks it for dropdown values.
"""

from __future__ import annotations

import random
import sqlite3

from config import SECTION_MATH, SECTION_RW, normalize_section
from database import question_conn
from models import Question

# Every column the app needs. The old code forgot `skill`, which is why the
# review screen could never show a skill-level breakdown.
SELECT_COLUMNS = (
    "question_id, section, domain, skill, difficulty, "
    "question_img, correct_answer, rationale, is_open_ended"
)


def _rows_to_questions(rows) -> list[Question]:
    return [Question.from_row(row) for row in rows]


def _safe_query(sql: str, params=()) -> list:
    """Run a query, returning [] instead of exploding on a missing table."""
    try:
        with question_conn() as conn:
            return conn.execute(sql, params).fetchall()
    except sqlite3.DatabaseError:
        return []


# ---------------------------------------------------------------------------
# METADATA FOR DROPDOWNS
# ---------------------------------------------------------------------------

def list_sections() -> list[str]:
    """Distinct sections present in the bank, canonical names, RW first."""
    rows = _safe_query("SELECT DISTINCT TRIM(section) AS s FROM questions WHERE s <> ''")
    found = {normalize_section(r["s"]) for r in rows if r["s"]}
    ordered = [s for s in (SECTION_RW, SECTION_MATH) if s in found]
    ordered += sorted(found - set(ordered))
    return ordered or [SECTION_RW, SECTION_MATH]


def list_domains(section: str | None = None) -> list[str]:
    """Domains available, optionally scoped to a section."""
    if section and section != "Any":
        rows = _safe_query(
            "SELECT DISTINCT TRIM(domain) AS d, COUNT(*) AS n FROM questions "
            "WHERE TRIM(section) = ? AND d <> '' GROUP BY d ORDER BY n DESC",
            (section.strip(),),
        )
    else:
        rows = _safe_query(
            "SELECT DISTINCT TRIM(domain) AS d, COUNT(*) AS n FROM questions "
            "WHERE d <> '' GROUP BY d ORDER BY n DESC"
        )
    return [r["d"] for r in rows if r["d"]]


def list_skills(section: str | None = None, domain: str | None = None) -> list[str]:
    """Skills available, optionally scoped to a section and/or domain."""
    clauses, params = ["TRIM(skill) <> ''"], []
    if section and section != "Any":
        clauses.append("TRIM(section) = ?")
        params.append(section.strip())
    if domain and domain != "Any":
        clauses.append("TRIM(domain) = ?")
        params.append(domain.strip())
    rows = _safe_query(
        "SELECT DISTINCT TRIM(skill) AS s, COUNT(*) AS n FROM questions "
        f"WHERE {' AND '.join(clauses)} GROUP BY s ORDER BY n DESC",
        tuple(params),
    )
    return [r["s"] for r in rows if r["s"]]


def count_available(section=None, domain=None, skill=None, difficulty=None) -> int:
    """How many questions match a filter. Used to warn before starting."""
    clauses, params = ["1=1"], []
    for column, value in (("section", section), ("domain", domain),
                          ("skill", skill), ("difficulty", difficulty)):
        if value and value != "Any":
            clauses.append(f"TRIM({column}) = ?")
            params.append(str(value).strip())
    rows = _safe_query(
        f"SELECT COUNT(*) AS n FROM questions WHERE {' AND '.join(clauses)}",
        tuple(params),
    )
    return rows[0]["n"] if rows else 0


def domain_counts(section: str | None = None) -> dict:
    """
    {domain: count} for a section in a single grouped query.

    Replaces N separate count_available() calls (one per domain), each of which
    opened its own connection and scanned the table.
    """
    if section and section != "Any":
        rows = _safe_query(
            "SELECT TRIM(domain) AS d, COUNT(*) AS n FROM questions "
            "WHERE TRIM(section) = ? GROUP BY d", (section.strip(),))
    else:
        rows = _safe_query(
            "SELECT TRIM(domain) AS d, COUNT(*) AS n FROM questions GROUP BY d")
    return {r["d"]: r["n"] for r in rows if r["d"]}


def domain_difficulty_counts(section: str | None = None) -> dict:
    """{(domain, difficulty): count} in one query, for the drill availability line."""
    if section and section != "Any":
        rows = _safe_query(
            "SELECT TRIM(domain) AS d, TRIM(difficulty) AS diff, COUNT(*) AS n "
            "FROM questions WHERE TRIM(section) = ? GROUP BY d, diff", (section.strip(),))
    else:
        rows = _safe_query(
            "SELECT TRIM(domain) AS d, TRIM(difficulty) AS diff, COUNT(*) AS n "
            "FROM questions GROUP BY d, diff")
    return {(r["d"], (r["diff"] or "Medium").capitalize()): r["n"] for r in rows if r["d"]}


def bank_summary() -> dict:
    """
    Coverage report: how many questions exist per section / domain / difficulty.

    The home screen uses this to tell you honestly whether your imported bank is
    deep enough to build a full adaptive test.
    """
    rows = _safe_query(
        "SELECT TRIM(section) AS section, TRIM(domain) AS domain, "
        "TRIM(difficulty) AS difficulty, COUNT(*) AS n FROM questions "
        "GROUP BY section, domain, difficulty"
    )
    summary: dict = {"total": 0, "by_section": {}, "by_domain": {}, "by_difficulty": {}}
    for row in rows:
        section = normalize_section(row["section"])
        difficulty = (row["difficulty"] or "Medium").capitalize()
        domain = row["domain"] or "General"
        n = row["n"]
        summary["total"] += n
        summary["by_section"][section] = summary["by_section"].get(section, 0) + n
        summary["by_difficulty"][difficulty] = summary["by_difficulty"].get(difficulty, 0) + n
        summary["by_domain"].setdefault(section, {})
        summary["by_domain"][section][domain] = summary["by_domain"][section].get(domain, 0) + n
    return summary


# ---------------------------------------------------------------------------
# FETCHING QUESTIONS
# ---------------------------------------------------------------------------

def fetch(
    *,
    section: str | None = None,
    domain: str | None = None,
    skill: str | None = None,
    difficulty: str | None = None,
    exclude_ids: set[str] | None = None,
    limit: int | None = None,
    shuffle: bool = True,
    rng: random.Random | None = None,
) -> list[Question]:
    """
    Generic pool fetch. `exclude_ids` prevents a question repeating inside the
    same sitting; `limit=None` means "everything that matches".
    """
    clauses, params = ["1=1"], []
    for column, value in (("section", section), ("domain", domain),
                          ("skill", skill), ("difficulty", difficulty)):
        if value and value != "Any":
            clauses.append(f"TRIM({column}) = ?")
            params.append(str(value).strip())

    rows = _safe_query(
        f"SELECT {SELECT_COLUMNS} FROM questions WHERE {' AND '.join(clauses)}",
        tuple(params),
    )
    questions = _rows_to_questions(rows)

    if exclude_ids:
        questions = [q for q in questions if q.question_id not in exclude_ids]

    if shuffle:
        (rng or random).shuffle(questions)

    return questions[:limit] if limit else questions


def fetch_by_ids(ids) -> dict[str, Question]:
    """Look up specific questions by id. Used when replaying a past session."""
    ids = [str(i) for i in ids if i]
    if not ids:
        return {}

    found: dict[str, Question] = {}
    # Chunked to stay under SQLite's variable limit on very large sessions.
    for start in range(0, len(ids), 400):
        chunk = ids[start:start + 400]
        placeholders = ",".join("?" for _ in chunk)
        rows = _safe_query(
            f"SELECT {SELECT_COLUMNS} FROM questions WHERE question_id IN ({placeholders})",
            tuple(chunk),
        )
        for question in _rows_to_questions(rows):
            found[question.question_id] = question
    return found


def fetch_pool_by_difficulty(
    *,
    section: str,
    domain: str | None = None,
    exclude_ids: set[str] | None = None,
    rng: random.Random | None = None,
) -> dict[str, list[Question]]:
    """
    One shuffled pool per difficulty, which is exactly the shape the blueprint
    builder wants. Doing it in a single query keeps module assembly to one round
    trip per domain instead of three.
    """
    pools: dict[str, list[Question]] = {"Easy": [], "Medium": [], "Hard": []}
    for question in fetch(section=section, domain=domain,
                          exclude_ids=exclude_ids, shuffle=True, rng=rng):
        pools.setdefault(question.difficulty, []).append(question)
    return pools


def prioritise_unseen(
    questions: list[Question],
    seen_counts: dict[str, int],
    rng: random.Random | None = None,
) -> list[Question]:
    """
    Reorder a pool so never-seen questions come first, then least-seen.

    This is the fix for the biggest complaint about free SAT tools: you keep
    getting the same handful of items. Ties are broken randomly so two drills
    with identical filters still look different.
    """
    randomizer = rng or random
    decorated = [(seen_counts.get(q.question_id, 0), randomizer.random(), q) for q in questions]
    decorated.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in decorated]
