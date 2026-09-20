"""
migration.py, move an existing Cat Prep install into this one.

The logic lives here rather than in migrate.py so that two front ends can share
it: the Migrate screen inside the app, and the command-line script for anyone
who prefers a terminal. One copy of the rules means the screen cannot quietly
drift away from the script and lose somebody's work.

THE RULE THIS FILE EXISTS TO ENFORCE: the old folder is never written to. Every
operation on the source is a read. If anything fails halfway, the old install is
still complete and the user can go back to it.

WHY NOT JUST COPY THE FILES
---------------------------
SQLite in WAL mode does not keep recent writes in the .db file. They live in a
second file next to it called `progress.db-wal` until something checkpoints them
back. Measured on a real install:

    progress.db         122 KB      13 sessions,  75 attempts
    progress.db-wal     2.5 MB      the other 16 sessions and 280 attempts

Copy the .db on its own, which is the obvious thing and the thing a file manager
makes easy, and you get a database that opens perfectly, shows a plausible
history, and is missing two thirds of the work. Nothing errors. Nothing warns.

So a database always moves as .db + -wal + -shm together, and the COPY gets
checkpointed, never the original.
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

from config import DATA_DIR

#: The three files SQLite spreads one database across.
DB_SUFFIXES = ("", "-wal", "-shm")
DATABASES = ("questions.db", "progress.db")
BULK_DIRS = ("images", "pdfs")


# ---------------------------------------------------------------------------
# FINDING AND SURVEYING
# ---------------------------------------------------------------------------

def find_app_folder(raw: str) -> Path | None:
    """Accept the project folder, the `Cat Sat` folder, or the data folder."""
    text = (raw or "").strip().strip('"').strip("'").strip()
    if not text:
        return None
    try:
        root = Path(text).expanduser().resolve()
    except (OSError, ValueError):
        return None
    if not root.exists():
        return None
    for candidate in (root / "Cat Sat", root / "CatPrep Data", root):
        if (candidate / "database").is_dir() or (candidate / "images").is_dir():
            return candidate
    return None


def count_rows(db_dir: Path, name: str, *tables: str) -> dict:
    """
    Row counts for one database, WITHOUT letting SQLite near the original.

    The obvious version opens the file with `?mode=ro` and counts. That is what
    this did first, and it is wrong in a way that only shows up when you check:
    opening a WAL database makes SQLite create the shared-memory `-shm` file
    beside it, read-only URI or not. So the promise above was already broken by
    the survey, before a single byte had been copied.

    `immutable=1` would stop that, and would also make SQLite ignore the -wal
    entirely, reporting 75 attempts where there are 355. The cure causes the
    disease.

    So: copy the file set somewhere scratch and count there. The only thing that
    ever touches the original is shutil, which reads bytes.
    """
    out: dict = {t: None for t in tables}
    if not (db_dir / name).is_file():
        return out
    scratch = Path(tempfile.mkdtemp(prefix="catprep-survey-"))
    try:
        for suffix in DB_SUFFIXES:
            source = db_dir / (name + suffix)
            if source.is_file():
                shutil.copy2(source, scratch / (name + suffix))
        conn = sqlite3.connect(scratch / name)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            for table in tables:
                try:
                    out[table] = conn.execute(
                        f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                except sqlite3.DatabaseError:
                    out[table] = None
        finally:
            conn.close()
    except (OSError, sqlite3.DatabaseError):
        pass
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return out


def _count_files(folder: Path) -> int:
    # .gitkeep exists only to keep an empty folder in git. Counting it made a
    # clean destination report one more file than the source sent.
    if not folder.is_dir():
        return 0
    return sum(1 for f in folder.iterdir() if f.is_file() and f.name != ".gitkeep")


def survey(app: Path) -> dict:
    """What is in an install, for comparing before against after."""
    db = app / "database"
    progress = count_rows(db, "progress.db", "sessions", "attempts")
    out = {
        "questions": count_rows(db, "questions.db", "questions")["questions"],
        "sessions": progress["sessions"],
        "attempts": progress["attempts"],
        "profile": (db / "profile.json").is_file(),
    }
    for name in BULK_DIRS:
        out[name] = _count_files(app / name)
    return out


def scan(raw_path: str) -> dict:
    """Look at a folder and report what is in it. Reads only."""
    source = find_app_folder(raw_path)
    if source is None:
        return {"found": False,
                "error": "No Cat Prep install in that folder. Point it at the "
                         "folder that holds 'Cat Sat', or at 'Cat Sat' itself."}
    if source.resolve() == DATA_DIR.resolve():
        return {"found": False, "error": "That is this copy's own folder."}
    found = survey(source)
    if not found["questions"]:
        return {"found": False,
                "error": "That folder has no question bank in it. Is it the right one?"}
    return {"found": True, "path": str(source), "source": found,
            "here": survey(DATA_DIR)}


# ---------------------------------------------------------------------------
# COPYING
# ---------------------------------------------------------------------------

class Progress:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = "idle"        # idle | copying | done | error
        self.message = ""
        self.copied = 0
        self.total = 0
        self.started_at = 0.0
        self.before: dict | None = None
        self.after: dict | None = None
        self.problems: list[str] = []

    def as_json(self) -> dict:
        return {
            "state": self.state,
            "message": self.message,
            "copied": self.copied,
            "total": self.total,
            "progress": round(self.copied / self.total, 3) if self.total else 0.0,
            "elapsed": round(time.time() - self.started_at, 1) if self.started_at else 0,
            "before": self.before,
            "after": self.after,
            "problems": self.problems,
        }


_progress = Progress()
_lock = threading.Lock()
_worker: threading.Thread | None = None


def status() -> dict:
    with _lock:
        return _progress.as_json()


def _copy_database(src_dir: Path, dest_dir: Path, name: str) -> None:
    """
    Replace one database here with the one from the old folder.

    Not a three-file copy over the top of the existing one, which is what this
    did first and which silently lost every session. Two things go wrong that
    way:

    The destination already has a -shm from the database this app created at
    startup. Its salt does not match the incoming -wal, and SQLite's answer to
    a log it cannot verify is to throw the log away. On a real install that is
    the most recent month of answers, gone, with no error anywhere.

    And copying onto an existing path keeps the same inode, so every pooled
    connection in every request thread carries on reading the old file.

    So: fold the source's log into a single file in a scratch directory first,
    then close every connection, then replace. One file arrives, there is no
    stale -shm to misread, and the new inode makes the pool reconnect.
    """
    if not (src_dir / name).is_file():
        return

    scratch = Path(tempfile.mkdtemp(prefix="catprep-move-"))
    try:
        for suffix in DB_SUFFIXES:
            source = src_dir / (name + suffix)
            if source.is_file():
                shutil.copy2(source, scratch / (name + suffix))

        # journal_mode=DELETE checkpoints the log into the database and removes
        # the -wal, leaving one self-contained file. On OUR copy; the original
        # is never opened.
        conn = sqlite3.connect(scratch / name)
        try:
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.commit()
        finally:
            conn.close()

        try:
            import database
            database.close_all_pools()
        except Exception:                                 # noqa: BLE001
            pass

        for suffix in DB_SUFFIXES:
            target = dest_dir / (name + suffix)
            try:
                target.unlink()
            except OSError:
                pass                                      # not there, or held open

        shutil.copy2(scratch / name, dest_dir / name)

        conn = sqlite3.connect(dest_dir / name)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.commit()
        finally:
            conn.close()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _copy_tree(src: Path, dest: Path, on_step) -> None:
    if not src.is_dir():
        return
    dest.mkdir(parents=True, exist_ok=True)
    files = [f for f in src.iterdir() if f.is_file() and f.name != ".gitkeep"]
    for source in files:
        target = dest / source.name
        # Skip files already here at the right size, so a run that died half
        # way can be started again without recopying a gigabyte.
        if not (target.is_file() and target.stat().st_size == source.stat().st_size):
            shutil.copy2(source, target)
        on_step()


def _run(source: Path, skip_pdfs: bool) -> None:
    try:
        with _lock:
            _progress.state = "copying"
            _progress.message = "Reading the old folder"
            _progress.before = survey(source)
            _progress.total = (
                len(DATABASES)
                + _count_files(source / "images")
                + (0 if skip_pdfs else _count_files(source / "pdfs"))
            )

        for folder in ("database", *BULK_DIRS):
            (DATA_DIR / folder).mkdir(parents=True, exist_ok=True)

        def step():
            with _lock:
                _progress.copied += 1

        with _lock:
            _progress.message = "Moving your question bank and history"
        for name in DATABASES:
            _copy_database(source / "database", DATA_DIR / "database", name)
            step()

        profile = source / "database" / "profile.json"
        if profile.is_file():
            shutil.copy2(profile, DATA_DIR / "database" / "profile.json")

        with _lock:
            _progress.message = "Copying question images, this is the slow part"
        _copy_tree(source / "images", DATA_DIR / "images", step)

        if not skip_pdfs:
            with _lock:
                _progress.message = "Copying your source PDFs"
            _copy_tree(source / "pdfs", DATA_DIR / "pdfs", step)

        after = survey(DATA_DIR)
        problems = []
        for key, label in (("questions", "questions"), ("sessions", "practice sittings"),
                           ("attempts", "answered questions")):
            old, new = (_progress.before or {}).get(key) or 0, after.get(key) or 0
            if new < old:
                problems.append(f"{label}: {old} before, {new} after")
        if after["images"] < (_progress.before or {}).get("images", 0):
            problems.append("some question images did not copy")
        if (_progress.before or {}).get("profile") and not after["profile"]:
            problems.append("your profile did not copy")

        # Bring the incoming databases up to this version's schema. Somebody
        # migrating is by definition coming from an OLDER copy, so their
        # progress.db can be missing columns this build queries. init_db ran at
        # startup, against the empty database we have just replaced, so it has
        # to run again over the one that actually arrived. Without this the
        # migration reports success and then every stats query fails.
        try:
            import database
            database.init_db()
        except Exception:                                 # noqa: BLE001
            pass

        # The plan is generated from the profile that just arrived, so the
        # cached one belongs to the empty install we were a minute ago.
        try:
            import plan_builder
            plan_builder.clear_plan_cache()
        except Exception:                                 # noqa: BLE001
            pass

        with _lock:
            _progress.after = after
            _progress.problems = problems
            _progress.state = "error" if problems else "done"
            _progress.message = ("Something is missing. Your old folder is untouched."
                                 if problems else "Everything arrived.")
    except Exception as exc:                              # noqa: BLE001
        with _lock:
            _progress.state = "error"
            _progress.message = f"{type(exc).__name__}: {exc}"
            _progress.problems = ["Nothing was written to your old folder."]


def start(raw_path: str, skip_pdfs: bool = False) -> dict:
    """Begin a migration in the background. Returns immediately."""
    global _worker
    with _lock:
        if _progress.state == "copying":
            return {"error": "A migration is already running."}

    looked = scan(raw_path)
    if not looked.get("found"):
        return {"error": looked.get("error", "Could not read that folder.")}

    with _lock:
        _progress.reset()
        _progress.started_at = time.time()
        _progress.state = "copying"

    source = Path(looked["path"])
    _worker = threading.Thread(target=_run, args=(source, skip_pdfs), daemon=True)
    _worker.start()
    return {"started": True, "path": str(source)}
