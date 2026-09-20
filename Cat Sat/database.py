"""
database.py, schema creation, repair and migration.

Two databases, on purpose:

  database/questions.db  – the question bank. Owned by sat_importer.py.
                           This module only *guarantees the schema matches* what
                           the importer expects; it never rewrites question rows.

  database/progress.db   – everything about you: sessions, per-question attempts,
                           notes, settings. Separate file so you can nuke and
                           re-import the question bank without losing history.

Why the schema guarantee matters
--------------------------------
The previous version of this file created a ``questions`` table with an
``answer_img`` column and *no* ``rationale`` / ``is_open_ended`` columns.
Because sat_importer.py uses ``CREATE TABLE IF NOT EXISTS``, running the old
database.py first meant the importer silently kept that wrong table and then
crashed on every INSERT. ``repair_question_bank_schema`` below detects that and
adds the missing columns in place, so old databases keep working.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager

from config import PROGRESS_DB, QUESTION_DB, ensure_dirs

# Columns sat_importer.py writes. Order matters only for readability.
QUESTION_COLUMNS = {
    "question_id": "TEXT PRIMARY KEY",
    "section": "TEXT",
    "domain": "TEXT",
    "skill": "TEXT",
    "difficulty": "TEXT",
    "question_img": "TEXT",
    "correct_answer": "TEXT",
    "rationale": "TEXT",
    "is_open_ended": "INTEGER DEFAULT 0",
}


# ---------------------------------------------------------------------------
# CONNECTION HELPERS
# ---------------------------------------------------------------------------

def connect(path, *, readonly: bool = False) -> sqlite3.Connection:
    """Open a fresh connection. Used for one-off work like schema repair."""
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn, readonly=readonly)
    return conn


def _apply_pragmas(conn: sqlite3.Connection, *, readonly: bool = False) -> None:
    """
    Run once per connection, not per query.

    The old code opened a new connection for every single read and set
    ``PRAGMA journal_mode = WAL`` on each one, WAL is a persistent property of
    the database file, so re-setting it thousands of times was pure overhead
    (it forces a lock and a header check every time).
    """
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        if not readonly:
            conn.execute("PRAGMA journal_mode = WAL")
            # Durable enough for a study app, and far fewer fsyncs than FULL.
            conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA temp_store = MEMORY")
        conn.execute("PRAGMA cache_size = -8000")     # ~8 MB page cache
    except sqlite3.DatabaseError:
        pass


# ---------------------------------------------------------------------------
# POOLED CONNECTIONS
# ---------------------------------------------------------------------------
# One cached connection per (database, thread). sqlite3 objects are not safe to
# share across threads, so the cache is thread-local; the background worker
# transparently gets its own.

_LOCAL = threading.local()

#: Every thread's pool dict, so a database can be REPLACED underneath a running
#: server. close_pool() below is thread-local, which is right for shutdown and
#: useless here: the request threads keep their handles open, and on Windows an
#: open handle makes the file impossible to delete.
_ALL_POOLS: list[dict] = []
_POOLS_LOCK = threading.Lock()


def _file_identity(path: str):
    """
    (inode, device) for the file behind a path, or None if it is gone.

    A pooled connection keeps working against a *deleted* file, the OS keeps
    the inode alive for the open handle, so reads silently return stale or
    empty results. That bites whenever the database is replaced underneath the
    app: the cleanup script removing an empty questions.db, sat_importer.py
    rebuilding the bank while the app is open, or a test swapping files around.
    Comparing identity on acquire makes the pool notice and reconnect.
    """
    try:
        stat = os.stat(path)
        return (stat.st_ino, stat.st_dev)
    except OSError:
        return None


def _pooled(path, *, readonly: bool = False) -> sqlite3.Connection:
    pool = getattr(_LOCAL, "pool", None)
    if pool is None:
        pool = _LOCAL.pool = {}
        with _POOLS_LOCK:
            _ALL_POOLS.append(pool)
    key = str(path)
    identity = _file_identity(key)

    cached = pool.get(key)
    if cached is not None:
        conn, cached_identity = cached
        # Reuse only if the file is still the same file we opened.
        if identity is not None and identity == cached_identity:
            return conn
        try:
            conn.close()
        except sqlite3.Error:
            pass
        pool.pop(key, None)

    conn = sqlite3.connect(key, timeout=10.0)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn, readonly=readonly)
    # Re-stat: the file exists now even if it did not a moment ago.
    pool[key] = (conn, _file_identity(key))
    return conn


def close_all_pools() -> None:
    """
    Drop EVERY thread's connections, not just this one's.

    sqlite3 connections are created with check_same_thread, so closing another
    thread's handle raises rather than working. Clearing the reference is what
    actually matters: the connection closes when it is collected, and the file
    it held is free again.
    """
    import gc
    with _POOLS_LOCK:
        for pool in _ALL_POOLS:
            for conn, _identity in list(pool.values()):
                try:
                    conn.close()
                except Exception:                         # noqa: BLE001
                    pass                                  # wrong thread, or already shut
            pool.clear()
    gc.collect()


def reset_pool() -> None:
    """Force the next access to reconnect. Used after a bulk import or wipe."""
    close_pool()


def close_pool() -> None:
    """Close this thread's cached connections. Called on app shutdown."""
    pool = getattr(_LOCAL, "pool", None) or {}
    for conn, _identity in list(pool.values()):
        try:
            conn.close()
        except sqlite3.Error:
            pass
    pool.clear()


@contextmanager
def progress_conn():
    """
    Pooled connection to progress.db that commits on success and rolls back on
    failure. The connection stays open for reuse instead of being torn down.
    """
    conn = _pooled(PROGRESS_DB)
    try:
        yield conn
        if conn.in_transaction:
            conn.commit()
    except Exception:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise


@contextmanager
def question_conn():
    """Pooled, read-only-by-convention connection to the question bank."""
    yield _pooled(QUESTION_DB)


# ---------------------------------------------------------------------------
# QUESTION BANK SCHEMA (importer-compatible)
# ---------------------------------------------------------------------------

def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    except sqlite3.DatabaseError:
        return []


def repair_question_bank_schema() -> list[str]:
    """
    Make sure ``questions`` exists and has every column sat_importer.py needs.

    Returns the list of columns that had to be added, so the caller can tell the
    user something actually happened.
    """
    ensure_dirs()
    added: list[str] = []

    conn = connect(QUESTION_DB)
    try:
        existing = _table_columns(conn, "questions")

        if not existing:
            # Fresh database: create exactly the importer's table.
            columns_sql = ",\n                ".join(
                f"{name} {decl}" for name, decl in QUESTION_COLUMNS.items()
            )
            conn.execute(f"CREATE TABLE IF NOT EXISTS questions (\n                {columns_sql}\n            )")
        else:
            # Existing database, possibly from the old database.py. Add anything
            # missing. SQLite can ALTER TABLE ADD COLUMN cheaply.
            for name, decl in QUESTION_COLUMNS.items():
                if name in existing:
                    continue
                if "PRIMARY KEY" in decl:
                    # Cannot add a primary key after the fact; the bank is
                    # unusable in that state and must be re-imported.
                    continue
                conn.execute(f"ALTER TABLE questions ADD COLUMN {name} {decl}")
                added.append(name)

        # Indexes that make blueprint assembly fast on a 3,500 question bank.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_q_section_domain_diff "
            "ON questions(section, domain, difficulty)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_q_skill ON questions(skill)")
        conn.commit()
    finally:
        conn.close()

    # The pooled connection (if any) may hold a stale schema; drop it so the
    # next read sees the repaired table.
    pool = getattr(_LOCAL, "pool", None) or {}
    stale = pool.pop(str(QUESTION_DB), None)
    if stale is not None:
        try:
            stale[0].close()
        except sqlite3.Error:
            pass

    return added


def question_bank_is_usable() -> tuple[bool, str]:
    """Cheap health check used by the home screen to show a helpful banner."""
    try:
        with question_conn() as conn:
            cols = _table_columns(conn, "questions")
            if not cols:
                return False, (f"No question bank in {QUESTION_DB.parent} yet.")
            if "rationale" not in cols or "is_open_ended" not in cols:
                return False, "Question bank schema is out of date. Restart the app to repair it."
            count = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
            if count == 0:
                # Name the FOLDER. An empty bank and "you are running a
                # different copy of the app than you think" look identical from
                # the outside, and the second is far more common once there is
                # more than one folder on the machine. The path turns a mystery
                # into something you can check in two seconds.
                return False, (f"The question bank in {QUESTION_DB.parent} is empty. "
                               f"If your questions are somewhere else, you are "
                               f"running a different copy of the app.")
            return True, f"{count:,} questions loaded"
    except sqlite3.DatabaseError as exc:
        return False, f"Could not open the question bank: {exc}"


# ---------------------------------------------------------------------------
# PROGRESS SCHEMA
# ---------------------------------------------------------------------------

PROGRESS_SCHEMA = """
-- One row per practice sitting: a full test, a single section, or a drill.
CREATE TABLE IF NOT EXISTS sessions (
    session_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    mode            TEXT NOT NULL,           -- 'full_test' | 'section_test' | 'drill' | 'review'
    section         TEXT,                    -- 'Reading and Writing' | 'Math' | 'Mixed'
    label           TEXT,                    -- human-friendly title for the history list
    started_at      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    finished_at     TEXT,
    status          TEXT NOT NULL DEFAULT 'in_progress',  -- 'in_progress' | 'completed' | 'abandoned'
    total_questions INTEGER DEFAULT 0,
    correct_count   INTEGER DEFAULT 0,
    duration_seconds INTEGER DEFAULT 0,
    estimated_score INTEGER,                 -- estimated section/total scaled score
    config_json     TEXT                     -- the setup that produced this session
);

-- One row per module inside a session. Drills have a single module.
CREATE TABLE IF NOT EXISTS modules (
    module_row_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    section         TEXT NOT NULL,
    module_number   INTEGER NOT NULL,        -- 1 or 2
    tier            TEXT NOT NULL,           -- 'baseline' | 'hard' | 'easy'
    question_count  INTEGER DEFAULT 0,
    correct_count   INTEGER DEFAULT 0,
    raw_accuracy    REAL,
    weighted_accuracy REAL,
    routed_to       TEXT,                    -- tier chosen for the NEXT module
    time_limit_seconds INTEGER,
    time_used_seconds  INTEGER DEFAULT 0,
    started_at      TEXT DEFAULT (datetime('now','localtime')),
    finished_at     TEXT
);

-- One row per question answered. Section/domain/skill/difficulty are
-- denormalised so history survives a question bank rebuild.
CREATE TABLE IF NOT EXISTS attempts (
    attempt_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER REFERENCES sessions(session_id) ON DELETE CASCADE,
    module_row_id   INTEGER REFERENCES modules(module_row_id) ON DELETE CASCADE,
    question_id     TEXT NOT NULL,
    position        INTEGER,                 -- 1-based order within the module
    section         TEXT,
    domain          TEXT,
    skill           TEXT,
    difficulty      TEXT,
    correct_answer  TEXT,
    selected_answer TEXT,                    -- '' when skipped
    is_correct      INTEGER NOT NULL DEFAULT 0,
    was_flagged     INTEGER NOT NULL DEFAULT 0,
    eliminated      TEXT,                    -- e.g. 'A,C'
    time_spent_ms   INTEGER NOT NULL DEFAULT 0,
    answered_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- Free-form notes you write while working a question, kept per question id.
CREATE TABLE IF NOT EXISTS question_notes (
    question_id     TEXT PRIMARY KEY,
    body            TEXT NOT NULL DEFAULT '',
    updated_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- Simple key/value store for preferences (target score, routing threshold...).
CREATE TABLE IF NOT EXISTS app_settings (
    key     TEXT PRIMARY KEY,
    value   TEXT
);

-- Pass 3 of the three-pass review: every miss and every lucky guess gets a
-- cold redo the next day, then +3 days, then +10 days.
CREATE TABLE IF NOT EXISTS redo_queue (
    queue_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id     TEXT NOT NULL,
    source_attempt  INTEGER,
    section         TEXT,
    domain          TEXT,
    skill           TEXT,
    stage           INTEGER NOT NULL DEFAULT 0,   -- 0 = cold, 1 = +3d, 2 = +10d
    due_on          TEXT NOT NULL,                -- YYYY-MM-DD
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    completed_at    TEXT,
    last_result     TEXT
);

-- "Any question type with 3+ misses in a week becomes a primary drill target.
--  Maximum three targets at a time."
CREATE TABLE IF NOT EXISTS drill_targets (
    target_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    axis            TEXT NOT NULL DEFAULT 'domain',   -- 'domain' | 'skill'
    label           TEXT NOT NULL,
    created_on      TEXT NOT NULL DEFAULT (date('now','localtime')),
    retired_on      TEXT,
    retire_accuracy REAL
);

-- Tick-off state for the Today's Plan checklist.
CREATE TABLE IF NOT EXISTS plan_progress (
    day_key         TEXT NOT NULL,
    task_key        TEXT NOT NULL,
    done            INTEGER NOT NULL DEFAULT 0,
    completed_at    TEXT,
    PRIMARY KEY (day_key, task_key)
);

CREATE INDEX IF NOT EXISTS idx_redo_due       ON redo_queue(due_on, completed_at);
CREATE INDEX IF NOT EXISTS idx_redo_question  ON redo_queue(question_id);
CREATE INDEX IF NOT EXISTS idx_targets_active ON drill_targets(retired_on);
CREATE INDEX IF NOT EXISTS idx_attempts_session  ON attempts(session_id);
CREATE INDEX IF NOT EXISTS idx_attempts_question ON attempts(question_id);
CREATE INDEX IF NOT EXISTS idx_attempts_domain   ON attempts(section, domain);
CREATE INDEX IF NOT EXISTS idx_attempts_time     ON attempts(answered_at);
CREATE INDEX IF NOT EXISTS idx_modules_session   ON modules(session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_started  ON sessions(started_at);
"""


# Columns added after the first release. ALTER TABLE ADD COLUMN is cheap and
# leaves existing rows intact, so upgrading never costs you your history.
PROGRESS_ADDITIONS = {
    "attempts": {
        "confidence": "TEXT DEFAULT ''",      # '' | 'sure' | 'shaky'
        "root_cause": "TEXT DEFAULT ''",      # K/M/C/T/V/P/X/D/A/G/L
        "fix_note": "TEXT DEFAULT ''",        # the one-sentence executable rule
        "is_redo": "INTEGER DEFAULT 0",       # served from the redo queue
    },
}


def _add_missing_columns(conn) -> list[str]:
    added = []
    for table, columns in PROGRESS_ADDITIONS.items():
        existing = _table_columns(conn, table)
        if not existing:
            continue
        for name, decl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                added.append(f"{table}.{name}")
    return added


def init_progress_db() -> list[str]:
    """Create progress.db, add any new columns, and migrate anything older in."""
    ensure_dirs()
    with progress_conn() as conn:
        conn.executescript(PROGRESS_SCHEMA)
        added = _add_missing_columns(conn)
    _migrate_legacy_user_history()
    return added


def _migrate_legacy_user_history() -> int:
    """
    Pull rows from the old ``user_history`` table (which lived inside
    questions.db) into the new attempts table exactly once.

    The old table only stored question_id / section / domain / is_correct /
    timestamp, so the migrated rows are attached to one synthetic
    "Imported history" session. Returns how many rows were moved.
    """
    if get_setting("legacy_history_migrated") == "1":
        return 0

    try:
        with question_conn() as qconn:
            if not _table_columns(qconn, "user_history"):
                set_setting("legacy_history_migrated", "1")
                return 0
            legacy_rows = qconn.execute(
                "SELECT question_id, section, domain, is_correct, timestamp "
                "FROM user_history ORDER BY timestamp ASC"
            ).fetchall()
    except sqlite3.DatabaseError:
        return 0

    if not legacy_rows:
        set_setting("legacy_history_migrated", "1")
        return 0

    with progress_conn() as conn:
        cur = conn.execute(
            "INSERT INTO sessions (mode, section, label, started_at, finished_at, "
            "status, total_questions, correct_count) "
            "VALUES ('drill', 'Mixed', 'Imported history (pre-upgrade)', ?, ?, 'completed', ?, ?)",
            (
                legacy_rows[0]["timestamp"],
                legacy_rows[-1]["timestamp"],
                len(legacy_rows),
                sum(1 for r in legacy_rows if r["is_correct"]),
            ),
        )
        session_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO attempts (session_id, question_id, section, domain, "
            "is_correct, answered_at) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (session_id, r["question_id"], r["section"], r["domain"],
                 1 if r["is_correct"] else 0, r["timestamp"])
                for r in legacy_rows
            ],
        )

    set_setting("legacy_history_migrated", "1")
    return len(legacy_rows)


# ---------------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------------

def get_setting(key: str, default: str | None = None) -> str | None:
    try:
        with progress_conn() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
    except sqlite3.DatabaseError:
        return default
    return row["value"] if row else default


def set_setting(key: str, value) -> None:
    try:
        with progress_conn() as conn:
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
    except sqlite3.DatabaseError:
        pass


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

def init_db() -> dict:
    """
    Bring both databases to a known-good state. Called once at startup.

    Returns a small report so the caller can surface anything interesting.
    """
    ensure_dirs()
    repaired = repair_question_bank_schema()
    upgraded = init_progress_db()
    ok, message = question_bank_is_usable()
    return {"repaired_columns": repaired, "upgraded_columns": upgraded,
            "bank_ok": ok, "bank_message": message}


if __name__ == "__main__":
    report = init_db()
    print("Question bank :", QUESTION_DB)
    print("Progress store:", PROGRESS_DB)
    if report["repaired_columns"]:
        print("Repaired missing columns:", ", ".join(report["repaired_columns"]))
    print("Status:", report["bank_message"])
