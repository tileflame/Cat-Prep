"""
setup_api.py, first-run setup, driven from the browser instead of a terminal.

Everything here exists to delete two instructions from the README:

    cd "Cat Sat" && python sat_importer.py
    python setup_profile.py

A student who has never opened a terminal should be able to drag their
College Board PDFs onto the window, watch a progress bar, and start studying.
That was the single most common complaint from real users: "the setup took us
the whole day".

WHAT THIS DOES NOT DO
---------------------
It does not modify sat_importer.py, and it does not reimplement it. It CALLS
the importer's own `parse_and_import_pdf(pdf_path, cursor)`, the same function
the command line uses, with the same arguments. If the importer changes, this
follows it for free. The importer's output is the only
source of truth for what a question is.

THREE THINGS THAT MAKE IT FAST
------------------------------
1. Parallel. The importer walks PDFs one at a time. Rasterising ~7,400 images
   at 200 dpi is the whole cost, so each PDF gets its own worker thread (see
   _import_one for why threads and not processes).
2. Skip work already done. A re-run used to redo every image from scratch.
   A small manifest records which PDFs were imported, by size and mtime, so a
   second setup is instant instead of another ten minutes.
3. Warn about synced folders. Writing thousands of PNGs into OneDrive, iCloud
   Drive, Dropbox or Google Drive makes the sync client upload every file as it
   appears, competing for the same disk. Moving one real user's project out of
   OneDrive took their import from ~10 minutes to ~3.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from pathlib import Path

from config import DATA_DIR

PDF_DIR = DATA_DIR / "pdfs"
IMAGE_DIR = DATA_DIR / "images"
DB_DIR = DATA_DIR / "database"
DB_PATH = DB_DIR / "questions.db"
MANIFEST = DB_DIR / "imported.json"

#: Folders whose sync clients fight the importer for the disk.
SYNCED_MARKERS = ("onedrive", "icloud", "dropbox", "google drive", "gdrive",
                  "creative cloud", "box sync", "pcloud", "mega")

MAX_WORKERS = 4          # beyond this the disk, not the CPU, is the limit

#: Images produced per megabyte of College Board export, measured on a real
#: pair of exports: 269 MB of PDF produced 7,426 PNGs. Used only to give the
#: progress bar a denominator — the number shown to the user is the exact count,
#: and the bar is capped below 100% so an underestimate can never claim the
#: import is finished when it is not.
IMAGES_PER_MB = 27.6


# ---------------------------------------------------------------------------
# PROGRESS — one shared object the UI polls
# ---------------------------------------------------------------------------

@dataclass
class Progress:
    state: str = "idle"              # idle | uploading | importing | done | error
    files: list = field(default_factory=list)   # [{name, state, questions, error}]
    imported: int = 0                # questions written this run
    images: int = 0                  # PNGs on disk so far — the live signal
    expected_images: int = 0         # estimate, so the bar has a denominator
    skipped_files: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0
    error: str = ""
    message: str = ""

    def as_json(self) -> dict:
        out = asdict(self)
        out["elapsed"] = round((self.finished_at or time.time()) - self.started_at, 1)\
            if self.started_at else 0
        done = sum(1 for f in self.files if f["state"] in ("done", "skipped", "error"))
        out["progress"] = round(done / len(self.files), 3) if self.files else 0.0
        out["total_files"] = len(self.files)
        out["done_files"] = done
        return out


_progress = Progress()
_lock = threading.Lock()
_worker: threading.Thread | None = None


def status() -> dict:
    with _lock:
        return _progress.as_json()


# ---------------------------------------------------------------------------
# ENVIRONMENT CHECKS
# ---------------------------------------------------------------------------

def saved_profile() -> dict:
    """
    What is already saved, so the profile screen can start from it.

    Without this the screen always opened blank, which is why editing a test
    date was impossible: you could only ever add another one.
    """
    try:
        from user_profile import Profile
        me = Profile.load()
    except Exception:                                     # noqa: BLE001
        return {"exists": False}
    if me is None:
        return {"exists": False}
    return {
        "exists": True,
        "name": me.name or "",
        "target": me.target_total,
        "testDates": [{"date": t.get("date", ""), "label": t.get("label", "")}
                      for t in (me.test_dates or [])],
        "reportCount": len(me.reports or []),
        "superscore": me.superscore(),
    }


#: Bumped by hand on each release. Shown in the app so a bug report can say
#: which build it came from, which is always the first question.
VERSION = "1.4"
RELEASES_URL = "https://github.com/tileflame/Cat-Prep/releases"


def make_backup() -> dict:
    """
    Zip everything the user cannot get back, onto their Desktop.

    A question bank is a 160 MB export, an hour of importing and a month of
    answers. There is no version of losing it that ends well, and the people
    most likely to lose it are the least likely to have thought about backups.
    """
    import datetime
    import zipfile

    stamp = datetime.date.today().isoformat()
    desktop = Path.home() / "Desktop"
    target_dir = desktop if desktop.is_dir() else Path.home()
    target = target_dir / f"CatPrep-backup-{stamp}.zip"

    try:
        count = 0
        # Write to .part first: a backup half-written over yesterday's good one
        # is worse than no backup, because it looks like a backup.
        part = target.with_suffix(".zip.part")
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for folder in ("database", "images", "pdfs"):
                root = DATA_DIR / folder
                if not root.is_dir():
                    continue
                for item in root.rglob("*"):
                    if item.is_file() and item.name != ".gitkeep":
                        bundle.write(item, str(Path(folder) / item.relative_to(root)))
                        count += 1
        os.replace(part, target)
        size = target.stat().st_size
        return {"ok": True, "path": str(target), "files": count,
                "sizeMB": round(size / 1e6, 1)}
    except OSError as exc:
        return {"error": f"Could not write the backup: {exc}"}


def environment() -> dict:
    """What the setup screen needs to know before it starts."""
    path = str(DATA_DIR).lower()
    synced = next((m for m in SYNCED_MARKERS if m in path), None)
    free = shutil.disk_usage(DATA_DIR).free if DATA_DIR.exists() else 0
    return {
        "dataDir": str(DATA_DIR),
        "syncedFolder": synced,
        "freeGB": round(free / 1e9, 1),
        "pdfsPresent": [p.name for p in sorted(PDF_DIR.glob("*.pdf"))] if PDF_DIR.is_dir() else [],
        "bankExists": DB_PATH.is_file(),
        "bankCount": _bank_count(),
    }


def _bank_count() -> int:
    if not DB_PATH.is_file():
        return 0
    try:
        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
            return conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    except sqlite3.DatabaseError:
        return 0


# ---------------------------------------------------------------------------
# UPLOAD
# ---------------------------------------------------------------------------

def _stream_to_file(stream, target: Path, length: int) -> tuple[int, str]:
    """
    Stream `length` bytes to `target` WITHOUT destroying what is already there.

    The obvious version opens the destination and writes into it. That is wrong
    in a way that only shows up on a bad day: open(target, "wb") truncates the
    existing file before a single new byte has arrived, and a short read then
    deletes it. So re-dropping a PDF you already imported, to redo an import,
    say, and having the transfer cut off part way destroys the copy you had.
    These exports are 100-160 MB and come from a College Board account; losing
    one is a real cost, and the transfer being cut off is not exotic: closing
    the window, the laptop sleeping, or the file sitting in OneDrive with the
    sync client holding a lock on it.

    So: write to a temp file beside the target, and only replace the real file
    once every byte has arrived. os.replace is atomic on both Windows and
    POSIX, so the destination is never a half-written file either.

    Returns (bytes_written, error_message). error_message is "" on success.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".part")
    written = 0
    try:
        with open(temp, "wb") as out:
            while written < length:
                chunk = stream.read(min(1 << 20, length - written))
                if not chunk:
                    break
                out.write(chunk)
                written += len(chunk)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        return written, f"Could not write {target.name}: {exc}"

    if written < length:
        temp.unlink(missing_ok=True)          # the ORIGINAL is untouched
        return written, (f"{target.name} was cut off after {written:,} of "
                         f"{length:,} bytes, try uploading it again. "
                         f"Any copy you already had is still there.")
    try:
        os.replace(temp, target)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        return written, f"Could not save {target.name}: {exc}"
    return written, ""


def save_upload(filename: str, stream, length: int) -> dict:
    """
    Stream one uploaded PDF straight to pdfs/.

    Streamed in chunks rather than read whole: a question-bank export runs to
    160 MB and holding several in memory at once is how a small local server
    gets itself killed.
    """
    safe = Path(filename).name                      # no directory traversal
    if not safe.lower().endswith(".pdf"):
        return {"error": f"{safe} is not a PDF."}
    target = PDF_DIR / safe
    written, error = _stream_to_file(stream, target, length)
    if error:
        return {"error": error}
    return {"saved": safe, "bytes": written}


# ---------------------------------------------------------------------------
# MANIFEST — what has already been imported
# ---------------------------------------------------------------------------

def _load_manifest() -> dict:
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_manifest(data: dict) -> None:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _fingerprint(path: Path) -> str:
    """Cheap identity: size + mtime. Hashing 160 MB to save 160 MB of work is
    a poor trade, and a PDF that changes always changes one of these."""
    stat = path.stat()
    return f"{stat.st_size}:{int(stat.st_mtime)}"


# ---------------------------------------------------------------------------
# IMPORT
# ---------------------------------------------------------------------------

def _import_one(job: tuple[str, str]) -> tuple[str, int, str]:
    """
    Import one PDF into its OWN database file. Returns (name, questions, error).

    THE BUG THIS FIXES
    ------------------
    The first version had every worker write to the one shared questions.db.
    That looked fine and passed its tests, and then failed on the first real
    run: of two College Board exports, Math imported 1,875 questions and
    Reading and Writing imported zero.

    SQLite allows exactly one writer at a time. `parse_and_import_pdf` inserts
    thousands of rows inside a single transaction that stays open for minutes,
    so the first worker held the write lock for the whole import and the second
    sat blocked until its busy timeout expired and died with "database is
    locked". Raising the timeout would not help: the lock is genuinely held for
    the entire run.

    The tests missed it because the stub importer inserted 40 rows in
    milliseconds, there was never a window for the two to collide. A bug that
    only appears when the transaction lasts minutes needs a test whose
    transaction lasts minutes.

    So each worker now gets a private database and the parts are merged when
    all of them have finished. Rasterising, the part that actually costs time
  , still happens in parallel; only the merge is serial, and it is a few
    hundred milliseconds.

    Runs on a worker THREAD, not a process: `multiprocessing` with the spawn
    start method re-imports __main__ in every child, which under PyInstaller
    re-executes the whole frozen app. The expensive work is MuPDF rasterising
    and PNG encoding, both C code that releases the GIL, so threads still
    overlap the part that matters.

    NOTE: no os.chdir here. sat_importer resolves relative paths, so the
    working directory must already be the app folder (set once by the caller),
    and changing it from a thread races every other thread.
    """
    pdf_path, part_db = job
    name = os.path.basename(pdf_path)
    conn = None
    try:
        import sat_importer               # noqa: PLC0415  (deliberately lazy)

        conn = sqlite3.connect(part_db, timeout=60)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS questions (
                question_id TEXT PRIMARY KEY, section TEXT, domain TEXT, skill TEXT,
                difficulty TEXT, question_img TEXT, correct_answer TEXT,
                rationale TEXT, is_open_ended INTEGER DEFAULT 0)
        """)
        count = sat_importer.parse_and_import_pdf(pdf_path, cursor)
        conn.commit()
        return name, int(count or 0), ""
    except ModuleNotFoundError as exc:
        # PyMuPDF or Pillow is not installed. This is the ONE failure here with
        # a fix the user can act on, so name it instead of showing them
        # "No module named 'fitz'", which means nothing to anybody.
        traceback.print_exc()
        package = {"fitz": "PyMuPDF", "PIL": "Pillow"}.get(exc.name or "", exc.name or "")
        return name, 0, (
            f"{package} is not installed, and the importer needs it to read PDFs. "
            f"Close the app, run:  python -m pip install -r requirements.txt  "
            f"then start it again. Everything already imported is unaffected.")
    except Exception as exc:                          # noqa: BLE001
        traceback.print_exc()
        return name, 0, f"{type(exc).__name__}: {exc}"
    finally:
        # Closed here rather than after commit(), because the interesting case
        # is the one that raises. On Windows an open handle keeps a lock on the
        # part file, so a leaked connection turns a single failed PDF into a
        # merge that cannot read the parts and a retry that cannot delete them.
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


def _merge_parts(parts: list[str]) -> int:
    """Fold each worker's private database into the real one. Serial, and fast."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    main = sqlite3.connect(str(DB_PATH), timeout=120)
    # Wrapped so that a failure partway through still closes the handle on the
    # REAL question bank. Leaving it open on Windows locks questions.db, and
    # the next thing the user does is press the button again.
    try:
        main.execute("PRAGMA busy_timeout=120000")
        main.execute("""
            CREATE TABLE IF NOT EXISTS questions (
                question_id TEXT PRIMARY KEY, section TEXT, domain TEXT, skill TEXT,
                difficulty TEXT, question_img TEXT, correct_answer TEXT,
                rationale TEXT, is_open_ended INTEGER DEFAULT 0)
        """)
        merged = 0
        for part in parts:
            if not os.path.exists(part):
                continue
            try:
                main.execute("ATTACH DATABASE ? AS part", (part,))
                before = main.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
                main.execute(
                    "INSERT OR REPLACE INTO questions SELECT * FROM part.questions")
                main.commit()
                after = main.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
                merged += after - before
            except sqlite3.DatabaseError:
                # One corrupt part must not cost the user the other three.
                traceback.print_exc()
            finally:
                try:
                    main.execute("DETACH DATABASE part")
                except sqlite3.DatabaseError:
                    pass
        main.commit()
        return merged
    finally:
        try:
            main.close()
        except sqlite3.Error:
            pass


def _live_count(_parts: list[str]) -> int:
    """
    Images written so far, the only progress signal that is actually visible.

    The obvious approach, counting rows in each worker's database, does not
    work: `parse_and_import_pdf` inserts everything inside one transaction and
    the caller commits at the end, and uncommitted rows cannot be seen from
    another connection. Polling gave 0, 0, 0, … then the final total, which is
    exactly the "bar never moves" behaviour this is meant to cure.

    Image files have no such problem. The importer writes each PNG to disk as
    it renders it, so counting them is a true live measure of how far along the
    run is. Roughly two per question, a question and its rationale.
    """
    try:
        with os.scandir(IMAGE_DIR) as entries:
            return sum(1 for e in entries if e.name.endswith(".png"))
    except OSError:
        return 0


def _run_import(paths: list[Path], force: bool) -> None:
    """Thread target. Wrapped so NOTHING can leave the app stuck mid-import."""
    try:
        _run_import_inner(paths, force)
    except BaseException as exc:                       # noqa: BLE001
        # This is a thread target, so an escaping exception just kills the
        # thread and leaves state == "importing" forever: the UI polls a
        # spinner that never resolves and start_import refuses every retry,
        # with no way out but restarting the app. The realistic triggers are
        # mundane — a full disk while writing ~1 GB of PNGs, a PDF deleted
        # mid-run, a permissions change — not exotic failures.
        traceback.print_exc()
        with _lock:
            _progress.state = "error"
            _progress.error = f"{type(exc).__name__}: {exc}"
            _progress.finished_at = time.time()
            _progress.message = "The import stopped unexpectedly."
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise


def _run_import_inner(paths: list[Path], force: bool) -> None:
    manifest = _load_manifest()
    todo, skipped = [], []
    for path in paths:
        if not force and manifest.get(path.name) == _fingerprint(path):
            skipped.append(path)
        else:
            todo.append(path)

    with _lock:
        _progress.files = (
            [{"name": p.name, "state": "skipped", "questions": 0, "error": ""} for p in skipped]
            + [{"name": p.name, "state": "waiting", "questions": 0, "error": ""} for p in todo])
        _progress.skipped_files = len(skipped)
        _progress.state = "importing"
        _progress.started_at = time.time()
        _progress.message = (f"{len(skipped)} already imported, {len(todo)} to do"
                             if skipped else f"Importing {len(todo)} file(s)")

    if not todo:
        with _lock:
            _progress.state = "done"
            _progress.finished_at = time.time()
            _progress.message = "Everything was already imported."
        return

    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    DB_DIR.mkdir(parents=True, exist_ok=True)

    def mark(name, **kw):
        with _lock:
            for entry in _progress.files:
                if entry["name"] == name:
                    entry.update(kw)

    for path in todo:
        mark(path.name, state="running")

    # sat_importer resolves "pdfs", "images" and "database/questions.db"
    # relative to the working directory, so stand in the app folder BEFORE any
    # worker starts. Done once, here, because os.chdir is process-wide and
    # doing it inside a thread would race every other thread.
    if Path.cwd() != DATA_DIR:
        os.chdir(str(DATA_DIR))

    parts_dir = DB_DIR / "_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    jobs, parts = [], []
    for index, path in enumerate(todo):
        part = str(parts_dir / f"part{index}.db")
        for stale in (part, part + "-wal", part + "-shm"):
            try:
                os.remove(stale)
            except OSError:
                pass
        jobs.append((str(path), part))
        parts.append(part)

    # Tick the live question count so the bar actually moves. Per-FILE progress
    # read 0/2 for several minutes with two PDFs and then jumped straight to
    # done, which made a working import look identical to a hung one.
    stop_ticker = threading.Event()

    start_images = _live_count(parts)
    total_mb = sum(p.stat().st_size for p in todo) / 1e6
    with _lock:
        _progress.expected_images = int(total_mb * IMAGES_PER_MB)

    def _tick():
        while not stop_ticker.wait(1.0):
            written = max(0, _live_count(parts) - start_images)
            with _lock:
                _progress.images = written
                # ~2 images per question (the question and its rationale), so
                # this is an estimate and is labelled as one.
                _progress.message = (f"{written:,} images extracted"
                                     + (f" · about {written // 2:,} questions"
                                        if written > 40 else "") + "…")

    ticker = threading.Thread(target=_tick, daemon=True)
    ticker.start()

    workers = max(1, min(MAX_WORKERS, len(todo), (os.cpu_count() or 2)))
    try:
        if workers == 1:
            results = [_import_one(job) for job in jobs]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(_import_one, jobs))
    except Exception as exc:                          # noqa: BLE001
        traceback.print_exc()
        with _lock:
            _progress.state = "error"
            _progress.error = f"{type(exc).__name__}: {exc}"
            _progress.finished_at = time.time()
        return
    finally:
        stop_ticker.set()

    with _lock:
        _progress.message = "Merging…"
    try:
        _merge_parts(parts)
    except Exception as exc:                          # noqa: BLE001
        traceback.print_exc()
        with _lock:
            _progress.state = "error"
            _progress.error = f"Merge failed: {type(exc).__name__}: {exc}"
            _progress.finished_at = time.time()
        return
    for part in parts:
        for stale in (part, part + "-wal", part + "-shm"):
            try:
                os.remove(stale)
            except OSError:
                pass

    total = 0
    for name, count, error in results:
        total += count
        # A PDF that yielded NOTHING is a failure, even though nothing raised.
        # sat_importer returns 0 without an exception for the most common real
        # mistakes: exporting from the Question Bank without answers and
        # rationales, picking the wrong PDF, or a truncated download. Recording
        # that as success was the worst bug in this file — the file went green,
        # its fingerprint went into the manifest, and every later attempt was
        # skipped as "already imported" while the app still had no bank. There
        # was no way out of that loop from inside the UI.
        if not error and count == 0:
            error = ("No questions found in this PDF. Export it from the College "
                     "Board Question Bank WITH answers and rationales, an export "
                     "without them has nothing for the app to read.")
        mark(name, state="error" if error else "done", questions=count, error=error)
        if not error:
            match = next((p for p in todo if p.name == name), None)
            if match:
                manifest[name] = _fingerprint(match)
        else:
            manifest.pop(name, None)      # never let a failure be skipped next time
    _save_manifest(manifest)

    failed = [f for f in _progress.files if f["state"] == "error"]
    with _lock:
        _progress.imported = total
        _progress.state = "error" if failed and not total else "done"
        _progress.finished_at = time.time()
        _progress.error = failed[0]["error"] if failed else ""
        _progress.message = (f"Imported {total:,} questions."
                             if not failed else
                             f"Imported {total:,} questions; {len(failed)} file(s) failed.")


def start_import(force: bool = False) -> dict:
    """Kick the import off on a background thread and return immediately.

    It must not run on the request thread: a full import takes minutes and the
    browser's own fetch timeout would abort it long before it finished.
    """
    global _worker
    with _lock:
        if _progress.state in ("importing", "uploading"):
            # Spread FIRST. Progress has its own `error` field, so putting the
            # spread last overwrote this message with "" and the UI read a
            # refusal as a successful start.
            return {**_progress.as_json(), "error": "An import is already running."}
        paths = sorted(PDF_DIR.glob("*.pdf")) if PDF_DIR.is_dir() else []
        if not paths:
            return {"error": "No PDFs yet, drop your College Board exports on the page first."}
        _progress.__init__()                          # reset
        _progress.state = "importing"

    _worker = threading.Thread(target=_run_import, args=(paths, force), daemon=True)
    _worker.start()
    return status()


# ---------------------------------------------------------------------------
# PROFILE — the other half of setup, replacing `python setup_profile.py`
# ---------------------------------------------------------------------------
# Same idea as the question bank: the work already exists in profile.py,
# score_report.py and plan_builder.py. This only gives the browser a way to
# reach it. Nothing here re-implements score parsing or plan generation.

REPORT_DIR = DATA_DIR / "pdfs" / "score_reports"


def save_report_upload(filename: str, stream, length: int) -> dict:
    """
    Take one uploaded score-report PDF and immediately say what was read.

    Parsed on upload rather than on save so the student sees their own scores
    echoed back before committing to anything. A report that cannot be read is
    the most likely thing to go wrong here, and finding that out at the end of
    a form is the worst moment to find it out.
    """
    safe = Path(filename).name
    if not safe.lower().endswith(".pdf"):
        return {"error": f"{safe} is not a PDF."}
    target = REPORT_DIR / safe
    written, error = _stream_to_file(stream, target, length)
    if error:
        # Same protection as the question bank, and it matters more here. A
        # half-uploaded question bank fails loudly because half a PDF has no
        # parseable questions. A cut-off SCORE REPORT parses cleanly — the
        # section scores are on page one — and silently yields a superscore
        # with the domain bands missing, which then steers every week of the
        # study plan.
        return {"error": error, "file": safe}

    try:
        from score_report import parse_score_report, describe
        report = parse_score_report(str(target))
    except Exception as exc:                              # noqa: BLE001
        return {"error": f"Could not read {safe}: {exc}", "file": safe}

    return {
        "file": safe,
        "report": report,
        "summary": describe(report),
        "hasDomainBands": bool(report.get("has_domain_bands")),
        # Practice reports draw the domain bars as graphics, so there is nothing
        # to read out of the PDF. Worth saying plainly instead of silently
        # producing a weaker plan.
        "note": ("" if report.get("has_domain_bands") else
                 "This report has no readable domain bands. Reports from your "
                 "College Board account have them; Bluebook practice reports draw "
                 "them as pictures. The plan still works, it just has less to go on."),
    }


def save_profile(data: dict) -> dict:
    """Build and save the profile, then generate the plan from it."""
    from datetime import date, datetime
    from user_profile import Profile, PROFILE_PATH
    from plan_builder import build_plan

    profile = Profile.load() or Profile()
    if data.get("name"):
        profile.name = str(data["name"]).strip()[:60]
    if data.get("target"):
        try:
            profile.target_total = max(400, min(1600, int(data["target"])))
        except (TypeError, ValueError):
            pass

    # A report that cannot be attached used to be swallowed here, and the call
    # still answered saved:True. The student saw a green tick and a study plan
    # built from nothing, with no way to tell that the score report they just
    # uploaded had been dropped on the floor. Count them and say so.
    dropped_reports = []
    attached_reports = 0
    for report in data.get("reports") or []:
        # Name it BEFORE trying anything, because the thing that arrived may
        # not be a dict at all and the error path must never raise its own
        # error — that turns one unusable report into a 500 and loses the name,
        # the target score and the test dates the student just typed in.
        label = "a report"
        if isinstance(report, dict):
            label = str(report.get("source_file") or report.get("label")
                        or report.get("file") or "a report")
        try:
            if not isinstance(report, dict):
                raise TypeError("not a score report")
            # A report with no section scores in it is not a report. It used to
            # attach silently and contribute nothing, which looks identical to
            # a report that worked.
            if not (report.get("sections") or report.get("total")):
                raise ValueError("no section scores in it")
            profile.add_report(report)
            attached_reports += 1
        except Exception as exc:                          # noqa: BLE001
            dropped_reports.append(f"{label} ({exc})")

    bad_dates = []
    # REPLACE the list rather than appending to it.
    #
    # add_test_date only ever inserts, and there is no delete anywhere, so a
    # mistyped year — 2027 instead of 2026 — was permanent. Worse than an
    # annoyance: build_weeks runs to the LAST test date, so one stray date a
    # year out generated hundreds of day-plans and anchored a taper week to a
    # test that does not exist. The only fix was hand-editing profile.json.
    #
    # The setup screen sends the complete list it is editing (and now loads the
    # saved one first), so the list it sends is the truth.
    if "testDates" in data:
        profile.test_dates = []
    for entry in data.get("testDates") or []:
        raw = (entry.get("date") or "").strip()
        try:
            when = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            bad_dates.append(raw)
            continue
        if when < date.today():
            bad_dates.append(f"{raw} (already passed)")
            continue
        profile.add_test_date(when, (entry.get("label") or "").strip())

    profile.save()
    # The plan is cached by the profile file's fingerprint; clearing here means
    # the new plan is live immediately rather than on the next mtime tick.
    try:
        from plan_builder import clear_plan_cache
        clear_plan_cache()
    except Exception:                                     # noqa: BLE001
        pass
    summary = profile.summary()
    plan = build_plan(profile)
    return {
        "saved": True,
        "path": str(PROFILE_PATH),
        "name": profile.name,
        "superscore": summary.get("superscore"),
        "target": summary.get("target"),
        "banked": summary.get("banked") or [],
        "weeks": len(plan.get("weeks") or []),
        "days": len(plan.get("days") or {}),
        "priorities": [
            {"domain": p["domain"], "weight": p["weight"], "verdict": p["verdict"]}
            for p in profile.domain_priorities() if p["weight"] > 0][:4],
        "badDates": bad_dates,
        "reportsAttached": attached_reports,
        "droppedReports": dropped_reports,
    }
