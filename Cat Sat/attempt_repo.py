"""
attempt_repo.py, persistence and analytics for everything the user does.

Writes to database/progress.db only. Section / domain / skill / difficulty are
denormalised onto each attempt row so your history stays readable even if you
delete and re-import the whole question bank.
"""

from __future__ import annotations

import json
import sqlite3
import sys

from database import progress_conn

# ---------------------------------------------------------------------------
# SESSIONS
# ---------------------------------------------------------------------------

def create_session(mode: str, section: str, label: str, config: dict | None = None) -> int:
    """Open a new sitting and return its id."""
    with progress_conn() as conn:
        cur = conn.execute(
            "INSERT INTO sessions (mode, section, label, config_json) VALUES (?, ?, ?, ?)",
            (mode, section, label, json.dumps(config or {})),
        )
        return int(cur.lastrowid)


def finish_session(
    session_id: int,
    *,
    total_questions: int,
    correct_count: int,
    duration_seconds: int,
    estimated_score: int | None = None,
    status: str = "completed",
) -> None:
    """Close a sitting out with its headline numbers."""
    with progress_conn() as conn:
        conn.execute(
            "UPDATE sessions SET finished_at = datetime('now','localtime'), status = ?, "
            "total_questions = ?, correct_count = ?, duration_seconds = ?, estimated_score = ? "
            "WHERE session_id = ?",
            (status, total_questions, correct_count, duration_seconds,
             estimated_score, session_id),
        )


def abandon_stale_sessions() -> int:
    """
    Mark any session left 'in_progress' (app closed mid-test) as abandoned.
    Called at startup so the history list never shows phantom active tests.
    """
    with progress_conn() as conn:
        cur = conn.execute(
            "UPDATE sessions SET status = 'abandoned' WHERE status = 'in_progress'"
        )
        return cur.rowcount or 0


# ---------------------------------------------------------------------------
# MODULES
# ---------------------------------------------------------------------------

def create_module(
    session_id: int, section: str, module_number: int, tier: str,
    question_count: int, time_limit_seconds: int | None,
) -> int:
    with progress_conn() as conn:
        cur = conn.execute(
            "INSERT INTO modules (session_id, section, module_number, tier, "
            "question_count, time_limit_seconds) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, section, module_number, tier, question_count, time_limit_seconds),
        )
        return int(cur.lastrowid)


def finish_module(
    module_row_id: int, *, correct_count: int, raw_accuracy: float,
    weighted_accuracy: float, routed_to: str | None, time_used_seconds: int,
) -> None:
    with progress_conn() as conn:
        conn.execute(
            "UPDATE modules SET finished_at = datetime('now','localtime'), "
            "correct_count = ?, raw_accuracy = ?, weighted_accuracy = ?, "
            "routed_to = ?, time_used_seconds = ? WHERE module_row_id = ?",
            (correct_count, raw_accuracy, weighted_accuracy, routed_to,
             time_used_seconds, module_row_id),
        )


# ---------------------------------------------------------------------------
# ATTEMPTS
# ---------------------------------------------------------------------------

ATTEMPT_INSERT = """
INSERT INTO attempts (
    session_id, module_row_id, question_id, position, section, domain, skill,
    difficulty, correct_answer, selected_answer, is_correct, was_flagged,
    eliminated, time_spent_ms, confidence, is_redo
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


def record_attempts(session_id: int, module_row_id: int | None, records) -> int:
    """
    Persist a whole module's worth of attempts in one transaction.

    Each record is stamped with its new attempt_id so the review screen can tag
    it with a root cause afterwards without re-querying.
    """
    rows = [record.as_row(session_id, module_row_id) for record in records]
    if not rows:
        return 0
    with progress_conn() as conn:
        first = conn.execute("SELECT COALESCE(MAX(attempt_id), 0) FROM attempts").fetchone()[0]
        conn.executemany(ATTEMPT_INSERT, rows)
        new_ids = [r["attempt_id"] for r in conn.execute(
            "SELECT attempt_id FROM attempts WHERE attempt_id > ? ORDER BY attempt_id",
            (first,))]
    for record, attempt_id in zip(records, new_ids):
        try:
            record.attempt_id = attempt_id
        except Exception:
            pass
    return len(rows)


# ---------------------------------------------------------------------------
# HISTORY READS
# ---------------------------------------------------------------------------

def _query(sql: str, params=()) -> list[sqlite3.Row]:
    """
    Run a read and return rows, never raising.

    Swallowing the exception keeps a damaged database from taking the whole app
    down, a dashboard with a missing panel beats a blank screen. But it also
    made a MISTAKE look exactly like an empty result: `ORDER BY id` on a table
    whose key is `queue_id` returned [], the caller decided there was nothing
    queued, and the bug was invisible until a test happened to expect a row.
    That is the worst shape a bug can have.

    So: still return [], still never raise, but say so, loudly, on stderr. A
    real data problem and a typo in the SQL are different things, and the
    difference should not be free to ignore.
    """
    try:
        with progress_conn() as conn:
            return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        # Bad column, bad table, bad syntax: a programming error, not bad data.
        print(f"[attempt_repo] QUERY FAILED, this is a bug, not empty data:\n"
              f"    {exc}\n    {' '.join(sql.split())[:160]}", file=sys.stderr)
        return []
    except sqlite3.DatabaseError as exc:
        print(f"[attempt_repo] database error: {exc}", file=sys.stderr)
        return []


def list_sessions(limit: int = 100, mode: str | None = None) -> list[dict]:
    """Most recent sittings first, for the history screen."""
    clause = "WHERE s.mode = ?" if mode and mode != "All" else ""
    params = (mode,) if clause else ()
    rows = _query(
        "SELECT s.*, "
        "(SELECT COUNT(*) FROM attempts a WHERE a.session_id = s.session_id) AS attempt_count "
        f"FROM sessions s {clause} ORDER BY s.started_at DESC, s.session_id DESC LIMIT ?",
        params + (limit,),
    )
    sessions = [dict(r) for r in rows]
    if not sessions:
        return []

    # Attach the routing path (e.g. 'baseline > hard') for each session.
    # Done as a second query rather than GROUP_CONCAT because SQLite does not
    # guarantee aggregate ordering inside a correlated subquery.
    ids = [s["session_id"] for s in sessions]
    placeholders = ",".join("?" for _ in ids)
    module_rows = _query(
        f"SELECT session_id, tier, module_number FROM modules "
        f"WHERE session_id IN ({placeholders}) ORDER BY session_id, module_row_id",
        tuple(ids),
    )
    tiers: dict[int, list[str]] = {}
    for row in module_rows:
        tiers.setdefault(row["session_id"], []).append(row["tier"])
    for session in sessions:
        session["tier_path"] = tiers.get(session["session_id"], [])
    return sessions


def get_session(session_id: int) -> dict | None:
    rows = _query("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
    return dict(rows[0]) if rows else None


def get_session_modules(session_id: int) -> list[dict]:
    rows = _query(
        "SELECT * FROM modules WHERE session_id = ? ORDER BY module_row_id", (session_id,)
    )
    return [dict(r) for r in rows]


def get_session_attempts(session_id: int) -> list[dict]:
    rows = _query(
        "SELECT a.*, m.module_number, m.tier FROM attempts a "
        "LEFT JOIN modules m ON m.module_row_id = a.module_row_id "
        "WHERE a.session_id = ? ORDER BY m.module_number, a.position, a.attempt_id",
        (session_id,),
    )
    return [dict(r) for r in rows]


def delete_session(session_id: int) -> None:
    with progress_conn() as conn:
        conn.execute("DELETE FROM attempts WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM modules WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))


# ---------------------------------------------------------------------------
# ANALYTICS
# ---------------------------------------------------------------------------

def overall_stats() -> dict:
    rows = _query(
        "SELECT COUNT(*) AS total, "
        "SUM(is_correct) AS correct, "
        "AVG(time_spent_ms) AS avg_ms, "
        "COUNT(DISTINCT question_id) AS unique_q "
        "FROM attempts"
    )
    if not rows or not rows[0]["total"]:
        return {"total": 0, "correct": 0, "accuracy": 0.0, "avg_seconds": 0.0, "unique_q": 0}
    row = rows[0]
    total = row["total"] or 0
    correct = row["correct"] or 0
    return {
        "total": total,
        "correct": correct,
        "accuracy": (correct / total * 100) if total else 0.0,
        "avg_seconds": (row["avg_ms"] or 0) / 1000.0,
        "unique_q": row["unique_q"] or 0,
    }


def breakdown(group_by: str, *, session_id: int | None = None,
              section: str | None = None, min_attempts: int = 1) -> list[dict]:
    """
    Accuracy grouped by 'domain', 'skill', 'difficulty' or 'section'.

    Pass session_id for a single test's breakdown, or leave it None for all-time.
    """
    if group_by not in ("domain", "skill", "difficulty", "section"):
        raise ValueError(f"cannot group by {group_by!r}")

    clauses, params = ["1=1"], []
    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)
    if section and section != "Any":
        clauses.append("section = ?")
        params.append(section)

    rows = _query(
        f"SELECT COALESCE(NULLIF(TRIM({group_by}), ''), 'Unclassified') AS bucket, "
        "COUNT(*) AS total, SUM(is_correct) AS correct, AVG(time_spent_ms) AS avg_ms "
        f"FROM attempts WHERE {' AND '.join(clauses)} "
        "GROUP BY bucket HAVING total >= ? ORDER BY total DESC",
        tuple(params) + (min_attempts,),
    )
    out = []
    for row in rows:
        total = row["total"] or 0
        correct = row["correct"] or 0
        out.append({
            "bucket": row["bucket"],
            "total": total,
            "correct": correct,
            "accuracy": (correct / total * 100) if total else 0.0,
            "avg_seconds": (row["avg_ms"] or 0) / 1000.0,
        })
    return out


def weakest(group_by: str = "domain", *, min_attempts: int = 4, limit: int = 5) -> list[dict]:
    """Lowest-accuracy buckets with enough data to be meaningful."""
    data = [b for b in breakdown(group_by) if b["total"] >= min_attempts]
    data.sort(key=lambda b: (b["accuracy"], -b["total"]))
    return data[:limit]


def accuracy_timeline(limit: int = 30) -> list[dict]:
    """
    One point per completed session: accuracy and estimated score.
    Session-level points are far more meaningful than the old per-question
    running average, which flattened out and stopped moving after a few dozen
    questions.
    """
    rows = _query(
        "SELECT session_id, label, mode, section, started_at, total_questions, "
        "correct_count, estimated_score FROM sessions "
        "WHERE status = 'completed' AND total_questions > 0 "
        "ORDER BY started_at ASC, session_id ASC"
    )
    points = [{
        "session_id": r["session_id"],
        "label": r["label"],
        "mode": r["mode"],
        "section": r["section"],
        "started_at": r["started_at"],
        "accuracy": (r["correct_count"] or 0) / r["total_questions"] * 100,
        "estimated_score": r["estimated_score"],
        "total": r["total_questions"],
    } for r in rows]
    return points[-limit:]


def seen_counts(section: str | None = None) -> dict[str, int]:
    """How many times each question has been served. Drives freshness."""
    clause = "WHERE section = ?" if section and section != "Any" else ""
    params = (section,) if clause else ()
    rows = _query(
        f"SELECT question_id, COUNT(*) AS n FROM attempts {clause} GROUP BY question_id",
        params,
    )
    return {r["question_id"]: r["n"] for r in rows}


def question_ids_where(*, only_incorrect=False, only_flagged=False,
                       section=None, domain=None, since_days: int | None = None,
                       limit: int = 500) -> list[str]:
    """
    Build a targeted pool: missed questions, flagged questions, or both.
    Returns most-recent-first, de-duplicated.
    """
    clauses, params = ["1=1"], []
    if only_incorrect and only_flagged:
        clauses.append("(is_correct = 0 OR was_flagged = 1)")
    elif only_incorrect:
        clauses.append("is_correct = 0")
    elif only_flagged:
        clauses.append("was_flagged = 1")
    if section and section != "Any":
        clauses.append("section = ?")
        params.append(section)
    if domain and domain != "Any":
        clauses.append("domain = ?")
        params.append(domain)
    if since_days:
        clauses.append("answered_at >= datetime('now','localtime', ?)")
        params.append(f"-{int(since_days)} days")

    rows = _query(
        f"SELECT question_id, MAX(answered_at) AS last_seen FROM attempts "
        f"WHERE {' AND '.join(clauses)} GROUP BY question_id "
        "ORDER BY last_seen DESC LIMIT ?",
        tuple(params) + (limit,),
    )
    return [r["question_id"] for r in rows]


def mastered_question_ids(streak: int = 2) -> set[str]:
    """
    Questions answered correctly on their last `streak` encounters.

    Done in SQL with a window function so it does not pull the entire attempts
    table into Python, that got slower every week you used the app. Falls back
    to the Python version on SQLite builds older than 3.25.
    """
    sql = """
        WITH ranked AS (
            SELECT question_id, is_correct,
                   ROW_NUMBER() OVER (PARTITION BY question_id
                                      ORDER BY answered_at DESC, attempt_id DESC) AS rn
            FROM attempts
        )
        SELECT question_id FROM ranked WHERE rn <= ?
        GROUP BY question_id
        HAVING COUNT(*) = ? AND SUM(is_correct) = ?
    """
    rows = _query(sql, (streak, streak, streak))
    if rows:
        return {r["question_id"] for r in rows}

    # Either genuinely empty, or the window function is unsupported. Confirm
    # cheaply before doing the expensive scan.
    probe = _query("SELECT COUNT(*) AS n FROM attempts")
    if not probe or not probe[0]["n"]:
        return set()
    fallback = _query(
        "SELECT question_id, is_correct FROM attempts "
        "ORDER BY question_id, answered_at DESC, attempt_id DESC")
    by_question: dict[str, list[int]] = {}
    for row in fallback:
        by_question.setdefault(row["question_id"], []).append(row["is_correct"])
    return {qid for qid, results in by_question.items()
            if len(results) >= streak and all(results[:streak])}


def pace_stats(section: str | None = None) -> dict:
    """Median-ish timing info used by the review screen's pacing callout."""
    clause = "WHERE time_spent_ms > 0"
    params: tuple = ()
    if section and section != "Any":
        clause += " AND section = ?"
        params = (section,)
    rows = _query(f"SELECT time_spent_ms, is_correct FROM attempts {clause}", params)
    if not rows:
        return {"count": 0, "avg_seconds": 0.0, "avg_correct": 0.0, "avg_incorrect": 0.0}

    times = [r["time_spent_ms"] / 1000 for r in rows]
    correct = [r["time_spent_ms"] / 1000 for r in rows if r["is_correct"]]
    wrong = [r["time_spent_ms"] / 1000 for r in rows if not r["is_correct"]]
    mean = lambda xs: sum(xs) / len(xs) if xs else 0.0  # noqa: E731
    return {
        "count": len(times),
        "avg_seconds": mean(times),
        "avg_correct": mean(correct),
        "avg_incorrect": mean(wrong),
    }


# ---------------------------------------------------------------------------
# NOTES
# ---------------------------------------------------------------------------

def get_note(question_id: str) -> str:
    rows = _query("SELECT body FROM question_notes WHERE question_id = ?", (question_id,))
    return rows[0]["body"] if rows else ""


def save_note(question_id: str, body: str) -> None:
    with progress_conn() as conn:
        conn.execute(
            "INSERT INTO question_notes (question_id, body, updated_at) "
            "VALUES (?, ?, datetime('now','localtime')) "
            "ON CONFLICT(question_id) DO UPDATE SET "
            "body = excluded.body, updated_at = excluded.updated_at",
            (question_id, body),
        )


def notes_count() -> int:
    rows = _query("SELECT COUNT(*) AS n FROM question_notes WHERE TRIM(body) <> ''")
    return rows[0]["n"] if rows else 0


# ---------------------------------------------------------------------------
# DESTRUCTIVE
# ---------------------------------------------------------------------------

def clear_history(*, keep_notes: bool = True) -> None:
    """Wipe study history. Notes are kept unless explicitly dropped."""
    with progress_conn() as conn:
        conn.execute("DELETE FROM attempts")
        conn.execute("DELETE FROM modules")
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM redo_queue")
        conn.execute("DELETE FROM drill_targets")
        conn.execute("DELETE FROM plan_progress")
        if not keep_notes:
            conn.execute("DELETE FROM question_notes")


# ---------------------------------------------------------------------------
# DIAGNOSTIC LOG — confidence, root cause, executable fix
# ---------------------------------------------------------------------------

def tag_attempt(attempt_id: int, *, root_cause: str = None, fix_note: str = None,
                confidence: str = None) -> None:
    """Write the WHY axis and the one-sentence fix onto a logged attempt."""
    sets, params = [], []
    if root_cause is not None:
        sets.append("root_cause = ?")
        params.append(root_cause)
    if fix_note is not None:
        sets.append("fix_note = ?")
        params.append(fix_note)
    if confidence is not None:
        sets.append("confidence = ?")
        params.append(confidence)
    if not sets:
        return
    with progress_conn() as conn:
        conn.execute(f"UPDATE attempts SET {', '.join(sets)} WHERE attempt_id = ?",
                     params + [attempt_id])


def logged_attempts(session_id: int) -> list[dict]:
    """
    Everything that belongs in the error log for a session: every miss AND every
    lucky guess. "A log that only records wrong answers is measuring the wrong
    thing."
    """
    rows = _query(
        "SELECT a.*, m.module_number, m.tier, s.label AS session_label "
        "FROM attempts a "
        "LEFT JOIN modules m ON m.module_row_id = a.module_row_id "
        "LEFT JOIN sessions s ON s.session_id = a.session_id "
        "WHERE a.session_id = ? AND (a.is_correct = 0 OR a.confidence = 'shaky') "
        "ORDER BY a.is_correct, m.module_number, a.position",
        (session_id,),
    )
    return [dict(r) for r in rows]


def recent_logged(limit: int = 120) -> list[dict]:
    """
    Every miss and lucky guess across all sessions, newest first, in ONE query.

    The error log used to call logged_attempts() in a loop over 25 sessions,
    25 round trips to build one screen.
    """
    rows = _query(
        "SELECT a.*, m.module_number, m.tier, s.label AS session_label, s.mode AS session_mode "
        "FROM attempts a "
        "LEFT JOIN modules m ON m.module_row_id = a.module_row_id "
        "LEFT JOIN sessions s ON s.session_id = a.session_id "
        "WHERE a.is_correct = 0 OR a.confidence = 'shaky' "
        "ORDER BY a.answered_at DESC, a.attempt_id DESC LIMIT ?",
        (limit,))
    return [dict(r) for r in rows]


def untagged_count(session_id: int | None = None) -> int:
    """How many logged questions still have no root cause."""
    clause = "WHERE (is_correct = 0 OR confidence = 'shaky') AND COALESCE(root_cause,'') = ''"
    params: tuple = ()
    if session_id is not None:
        clause += " AND session_id = ?"
        params = (session_id,)
    rows = _query(f"SELECT COUNT(*) AS n FROM attempts {clause}", params)
    return rows[0]["n"] if rows else 0


def cause_counts(since_days: int | None = 7, session_id: int | None = None) -> dict:
    """Misses grouped by root cause, the input to the diagnosis table."""
    clauses = ["COALESCE(root_cause,'') <> ''"]
    params: list = []
    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)
    elif since_days:
        clauses.append("answered_at >= datetime('now','localtime', ?)")
        params.append(f"-{int(since_days)} days")
    rows = _query(
        f"SELECT root_cause, COUNT(*) AS n FROM attempts WHERE {' AND '.join(clauses)} "
        "GROUP BY root_cause", tuple(params))
    return {r["root_cause"]: r["n"] for r in rows}


def lucky_counts(since_days: int | None = 7, group_by: str = "domain") -> dict:
    """Lucky guesses (correct but flagged shaky) grouped by domain or skill."""
    if group_by not in ("domain", "skill", "section"):
        raise ValueError(group_by)
    clauses = ["confidence = 'shaky'", "is_correct = 1"]
    params: list = []
    if since_days:
        clauses.append("answered_at >= datetime('now','localtime', ?)")
        params.append(f"-{int(since_days)} days")
    rows = _query(
        f"SELECT COALESCE(NULLIF(TRIM({group_by}),''),'Unclassified') AS bucket, "
        f"COUNT(*) AS n FROM attempts WHERE {' AND '.join(clauses)} GROUP BY bucket",
        tuple(params))
    return {r["bucket"]: r["n"] for r in rows}


def miss_counts_by_type(since_days: int = 7, group_by: str = "domain") -> dict:
    """Misses grouped by question type, drives drill-target suggestions."""
    if group_by not in ("domain", "skill"):
        raise ValueError(group_by)
    rows = _query(
        f"SELECT COALESCE(NULLIF(TRIM({group_by}),''),'Unclassified') AS bucket, "
        "COUNT(*) AS n FROM attempts "
        "WHERE is_correct = 0 AND answered_at >= datetime('now','localtime', ?) "
        "GROUP BY bucket ORDER BY n DESC", (f"-{int(since_days)} days",))
    return {r["bucket"]: r["n"] for r in rows}


def log_totals(since_days: int = 7) -> dict:
    """Headline counts for the leading-indicator panel."""
    rows = _query(
        "SELECT COUNT(*) AS logged, "
        "SUM(CASE WHEN is_correct = 0 THEN 1 ELSE 0 END) AS wrong, "
        "SUM(CASE WHEN is_correct = 1 AND confidence = 'shaky' THEN 1 ELSE 0 END) AS lucky "
        "FROM attempts WHERE answered_at >= datetime('now','localtime', ?) "
        "AND (is_correct = 0 OR confidence = 'shaky')",
        (f"-{int(since_days)} days",))
    if not rows:
        return {"logged": 0, "wrong": 0, "lucky": 0}
    r = rows[0]
    return {"logged": r["logged"] or 0, "wrong": r["wrong"] or 0, "lucky": r["lucky"] or 0}


# ---------------------------------------------------------------------------
# REDO QUEUE
# ---------------------------------------------------------------------------

def pending_redo(question_id: str) -> dict | None:
    """The un-completed redo already queued for this question, if any."""
    rows = _query(
        "SELECT * FROM redo_queue WHERE question_id = ? AND completed_at IS NULL "
        "ORDER BY queue_id DESC LIMIT 1", (question_id,))
    return dict(rows[0]) if rows else None


def schedule_redo(question_id: str, *, stage: int, due_on: str,
                  source_attempt: int | None = None, section: str = "",
                  domain: str = "", skill: str = "") -> None:
    """Queue a question for a cold redo. Replaces any pending entry for it."""
    with progress_conn() as conn:
        conn.execute("DELETE FROM redo_queue WHERE question_id = ? AND completed_at IS NULL",
                     (question_id,))
        conn.execute(
            "INSERT INTO redo_queue (question_id, source_attempt, section, domain, skill, "
            "stage, due_on) VALUES (?,?,?,?,?,?,?)",
            (question_id, source_attempt, section, domain, skill, stage, due_on))


def due_redos(on_day: str, limit: int = 50) -> list[dict]:
    """Everything due on or before `on_day` (YYYY-MM-DD), oldest first."""
    rows = _query(
        "SELECT * FROM redo_queue WHERE completed_at IS NULL AND due_on <= ? "
        "ORDER BY due_on ASC, stage DESC LIMIT ?", (on_day, limit))
    return [dict(r) for r in rows]


def redo_counts(on_day: str) -> dict:
    """How many redos are due today vs still pending in the future."""
    due = _query("SELECT COUNT(*) AS n FROM redo_queue WHERE completed_at IS NULL "
                 "AND due_on <= ?", (on_day,))
    later = _query("SELECT COUNT(*) AS n FROM redo_queue WHERE completed_at IS NULL "
                   "AND due_on > ?", (on_day,))
    return {"due": due[0]["n"] if due else 0, "upcoming": later[0]["n"] if later else 0}


def complete_redo(question_id: str, *, was_correct: bool, next_stage_value,
                  next_due: str | None) -> None:
    """Mark a redo done and, if it hasn't graduated, queue the next rung."""
    with progress_conn() as conn:
        conn.execute(
            "UPDATE redo_queue SET completed_at = datetime('now','localtime'), "
            "last_result = ? WHERE question_id = ? AND completed_at IS NULL",
            ("correct" if was_correct else "incorrect", question_id))
        row = conn.execute(
            "SELECT section, domain, skill FROM redo_queue WHERE question_id = ? "
            "ORDER BY queue_id DESC LIMIT 1", (question_id,)).fetchone()
        if next_stage_value is not None and next_due:
            conn.execute(
                "INSERT INTO redo_queue (question_id, section, domain, skill, stage, due_on) "
                "VALUES (?,?,?,?,?,?)",
                (question_id, row["section"] if row else "", row["domain"] if row else "",
                 row["skill"] if row else "", next_stage_value, next_due))


def clear_redo_queue() -> None:
    with progress_conn() as conn:
        conn.execute("DELETE FROM redo_queue")


# ---------------------------------------------------------------------------
# DRILL TARGETS
# ---------------------------------------------------------------------------

def active_targets() -> list[dict]:
    rows = _query("SELECT * FROM drill_targets WHERE retired_on IS NULL "
                  "ORDER BY created_on, target_id")
    return [dict(r) for r in rows]


def add_target(label: str, axis: str = "domain") -> int | None:
    existing = [t["label"] for t in active_targets()]
    if label in existing:
        return None
    with progress_conn() as conn:
        cur = conn.execute("INSERT INTO drill_targets (axis, label) VALUES (?, ?)",
                           (axis, label))
        return int(cur.lastrowid)


def retire_target(target_id: int, accuracy: float) -> None:
    with progress_conn() as conn:
        conn.execute("UPDATE drill_targets SET retired_on = date('now','localtime'), "
                     "retire_accuracy = ? WHERE target_id = ?", (accuracy, target_id))


def target_progress(label: str, axis: str = "domain", sample: int = 15) -> dict:
    """
    Accuracy on the most recent `sample` questions of this type, the only
    evidence that counts for retiring a target.
    """
    if axis not in ("domain", "skill"):
        raise ValueError(axis)
    rows = _query(
        f"SELECT is_correct FROM attempts WHERE TRIM({axis}) = ? "
        "ORDER BY answered_at DESC, attempt_id DESC LIMIT ?", (label, sample))
    if not rows:
        return {"sample": 0, "correct": 0, "accuracy": 0.0}
    correct = sum(1 for r in rows if r["is_correct"])
    return {"sample": len(rows), "correct": correct,
            "accuracy": correct / len(rows) * 100}


# ---------------------------------------------------------------------------
# PLAN CHECKLIST
# ---------------------------------------------------------------------------

def plan_done_keys(day_key: str) -> set:
    rows = _query("SELECT task_key FROM plan_progress WHERE day_key = ? AND done = 1",
                  (day_key,))
    return {r["task_key"] for r in rows}


def set_plan_task(day_key: str, task_key: str, done: bool) -> None:
    with progress_conn() as conn:
        conn.execute(
            "INSERT INTO plan_progress (day_key, task_key, done, completed_at) "
            "VALUES (?, ?, ?, datetime('now','localtime')) "
            "ON CONFLICT(day_key, task_key) DO UPDATE SET "
            "done = excluded.done, completed_at = excluded.completed_at",
            (day_key, task_key, 1 if done else 0))


def plan_days_summary(start_key: str, end_key: str) -> dict:
    """{day: completed task count} across a date range, in one query."""
    rows = _query(
        "SELECT day_key, COUNT(*) AS n FROM plan_progress "
        "WHERE done = 1 AND day_key BETWEEN ? AND ? GROUP BY day_key",
        (start_key, end_key))
    return {r["day_key"]: r["n"] for r in rows}


def attempts_per_day(start_key: str, end_key: str) -> dict:
    """{day: questions answered} across a date range, in one query."""
    rows = _query(
        "SELECT date(answered_at) AS d, COUNT(*) AS n FROM attempts "
        "WHERE date(answered_at) BETWEEN ? AND ? GROUP BY d",
        (start_key, end_key))
    return {r["d"]: r["n"] for r in rows}


def plan_streak(today_key: str, lookback: int = 30) -> int:
    """Consecutive days ending today with at least one task ticked off."""
    rows = _query("SELECT DISTINCT day_key FROM plan_progress WHERE done = 1 "
                  "ORDER BY day_key DESC LIMIT ?", (lookback,))
    from datetime import date as _date, timedelta as _td
    done_days = {r["day_key"] for r in rows}
    try:
        cursor = _date.fromisoformat(today_key)
    except ValueError:
        return 0
    streak = 0
    while cursor.isoformat() in done_days:
        streak += 1
        cursor -= _td(days=1)
    return streak
