"""
Regression tests for the optimisation pass: image caching, connection pooling,
the background worker, list pagination, the Desmos window geometry, and the
rebuilt error log.

Run: python3 dev_tests/test_perf.py
"""
import os, sys, threading, time, traceback
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "stubs"))
sys.path.insert(0, HERE)
sys.path.insert(0, APP)
os.chdir(APP)

import make_fake_bank
make_fake_bank.sandbox_env()
if not os.path.exists(os.path.join(make_fake_bank.SANDBOX, "database", "questions.db")):
    make_fake_bank.build(6)
make_fake_bank.reset_progress()

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
from PIL import Image

import attempt_repo, background, database, desmos_window, question_repo, ui_kit as ui
from models import AttemptRecord

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(("  ok  " if cond else "  FAIL ") + name + (f"   <- {detail}" if detail and not cond else ""))


def guard(name, fn):
    try:
        out = fn(); check(name, True); return out
    except Exception as exc:
        check(name, False, f"{type(exc).__name__}: {exc}"); traceback.print_exc(); return None


database.init_db()
messagebox.ANSWER = True

# =====================================================================
print("\n[1] image cache")
ui.clear_image_cache()
q = question_repo.fetch(section="Reading and Writing", limit=1)[0]
path = q.image_path
check("test question has an image", bool(path), str(q.question_img))

decodes = {"n": 0}
_orig = Image.open
def counting_open(*a, **kw):
    decodes["n"] += 1
    return _orig(*a, **kw)
Image.open = counting_open

img1, size1 = ui.load_scaled_image(path, 860)
check("first load decodes once", decodes["n"] == 1, str(decodes["n"]))
img2, size2 = ui.load_scaled_image(path, 860)
check("second load is a cache hit (no decode)", decodes["n"] == 1, str(decodes["n"]))
check("cache returns the same object", img1 is img2)
check("cache stats show the hit", ui.cache_stats()["hits"] >= 1, str(ui.cache_stats()))

ui.load_scaled_image(path, 400)
check("a different target width is a separate entry", decodes["n"] == 2, str(decodes["n"]))

# The decoded image must actually be small — the old code handed CTkImage the
# full-size original and let it re-resize on every redraw.
with _orig(path) as raw:
    original_size = raw.size
decoded, small = ui.decode_scaled(path, 300)
check("decode_scaled really downscales", small[0] <= 300 and small[0] < original_size[0],
      f"{original_size} -> {small}")
check("CTkImage is given the pre-scaled image",
      img1.light_image.size == size1, f"{img1.light_image.size} vs {size1}")

# LRU must bound memory.
before = ui.cache_stats()["size"]
for i, other in enumerate(question_repo.fetch(section="Math", limit=60)):
    if other.image_path:
        ui.load_scaled_image(other.image_path, 860)
check("cache is bounded by the LRU limit", ui.cache_stats()["size"] <= ui._CACHE_LIMIT,
      str(ui.cache_stats()))

Image.open = _orig
check("missing file degrades to None", ui.load_scaled_image("nope/missing.png") == (None, None))
check("empty path degrades to None", ui.load_scaled_image("") == (None, None))

# =====================================================================
print("\n[2] background worker")
main_thread = threading.current_thread()
observed = {}


def slow_job(x):
    observed["thread"] = threading.current_thread()
    return x * 2


results = []
background.submit(slow_job, 21, on_done=lambda r, e: results.append((r, e)))
background.wait_idle()
background.drain()
check("job ran off the main thread", observed.get("thread") is not main_thread,
      str(observed.get("thread")))
check("result delivered to the caller", results == [(42, None)], str(results))

errors = []
background.submit(lambda: 1 / 0, on_done=lambda r, e: errors.append(e))
background.wait_idle(); background.drain()
check("worker survives an exception and reports it",
      errors and isinstance(errors[0], ZeroDivisionError), str(errors))
background.submit(slow_job, 1, on_done=lambda r, e: None)
background.wait_idle(); background.drain()
check("worker still alive after the exception", background.pending() == 0)

# Nothing may touch Tk from the worker thread.
import inspect
src = inspect.getsource(background)
check("background never calls after() from the worker",
      "widget.after" not in src.split("def _run")[1].split("def ")[0],
      "worker body references widget.after")

# =====================================================================
print("\n[3] connection pooling")
database.close_pool()
opened = {"n": 0}
import sqlite3
_orig_connect = sqlite3.connect
def counting_connect(*a, **kw):
    opened["n"] += 1
    return _orig_connect(*a, **kw)
sqlite3.connect = counting_connect

for _ in range(20):
    attempt_repo.overall_stats()
check("20 reads reuse one pooled connection", opened["n"] <= 1, str(opened["n"]))

opened["n"] = 0
for _ in range(10):
    question_repo.list_domains("Math")
check("10 bank reads reuse one connection", opened["n"] <= 1, str(opened["n"]))

# The pool must notice when the file underneath it is replaced.
sqlite3.connect = _orig_connect
db_path = os.path.join(make_fake_bank.SANDBOX, "database", "questions.db")
before_count = question_repo.count_available()
backup = db_path + ".bak"
os.replace(db_path, backup)
sqlite3.connect(db_path).close()          # a brand-new, empty file at the same path
database.repair_question_bank_schema()
check("pool notices the bank was replaced", question_repo.count_available() == 0,
      str(question_repo.count_available()))
os.remove(db_path)
os.replace(backup, db_path)
check("pool notices it was restored", question_repo.count_available() == before_count,
      f"{question_repo.count_available()} vs {before_count}")

database.close_pool()
check("close_pool is safe to call twice", database.close_pool() is None)

# =====================================================================
print("\n[4] Desmos window geometry")
geo = desmos_window.calculator_geometry
for screen in [(1920, 1080), (1366, 768), (2560, 1440), (3840, 2160), (1280, 720),
               (1024, 768), (800, 600)]:
    w, h, x, y = geo(*screen)
    sw, sh = screen
    check(f"{sw}x{sh}: fits on screen", w <= sw and h <= sh, f"{w}x{h}")
    check(f"{sw}x{sh}: not full-screen", w < sw * 0.95 and h < sh * 0.95, f"{w}x{h}")
    check(f"{sw}x{sh}: usable size", w >= 500 and h >= 400, f"{w}x{h}")
    check(f"{sw}x{sh}: fully on screen", x >= 0 and y >= 0 and x + w <= sw and y + h <= sh,
          f"pos {x},{y} size {w}x{h}")

w1080, h1080, _, _ = geo(1920, 1080)
w4k, h4k, _, _ = geo(3840, 2160)
check("bigger screen does not mean an unbounded window",
      w4k <= desmos_window.MAX_SIZE[0] and h4k <= desmos_window.MAX_SIZE[1],
      f"{w4k}x{h4k}")
check("a 4K window is at least as big as a 1080p one", w4k >= w1080)
check("degenerate input still returns something sane",
      all(v >= 0 for v in geo(0, 0)) and geo(0, 0)[0] >= 500, str(geo(0, 0)))

# The launcher must pass explicit size/position flags.
desmos_window.DesmosWindow._last_launch = 0.0
launched = []
import subprocess
_orig_popen = subprocess.Popen
_orig_candidates = desmos_window.DesmosWindow._candidates
subprocess.Popen = lambda cmd, **kw: launched.append(cmd) or type("P", (), {})()
# This container has no Chromium, so pretend one is installed — otherwise the
# launcher correctly falls back to webbrowser.open and there are no flags to check.
desmos_window.DesmosWindow._candidates = lambda self: ["/fake/chrome"]
try:
    desmos_window.DesmosWindow._last_launch = 0.0
    win = desmos_window.DesmosWindow(None)
    flags = " ".join(launched[0]) if launched else ""
    check("launch passes --window-size", "--window-size=" in flags, flags[:160])
    check("launch passes --window-position", "--window-position=" in flags, flags[:160])
    check("launch opens Desmos in app mode", "--app=" in flags and "desmos.com" in flags,
          flags[:160])
    # Second click within the cooldown must not spawn a second process.
    count_before = len(launched)
    desmos_window.DesmosWindow(None)
    check("rapid second click is suppressed", len(launched) == count_before,
          f"{count_before} -> {len(launched)}")
finally:
    subprocess.Popen = _orig_popen
    desmos_window.DesmosWindow._candidates = _orig_candidates

win = desmos_window.DesmosWindow.__new__(desmos_window.DesmosWindow)
check("exposes winfo_exists for the quiz screen", win.winfo_exists() == 0)
check("exposes focus/destroy", win.focus() is None and win.destroy() is None)

# =====================================================================
print("\n[5] the error log actually shows the question")
attempt_repo.clear_history(keep_notes=False)
from main import SATApp
app = SATApp()

qs = question_repo.fetch(section="Reading and Writing", limit=30)
sid = attempt_repo.create_session("drill", "Reading and Writing", "log render test", {})
records = []
for i, question in enumerate(qs[:28], start=1):
    wrong = i % 2 == 0
    records.append(AttemptRecord(
        question=question,
        selected_answer="" if i == 3 else ("Z" if wrong else question.correct_answer),
        is_correct=not wrong, confidence="shaky" if i % 5 == 0 else "sure",
        time_spent_ms=45000 + i * 900, position=i,
        eliminated={"A"} if i % 7 == 0 else set()))
attempt_repo.record_attempts(sid, None, records)
attempt_repo.finish_session(sid, total_questions=28, correct_count=14, duration_seconds=900)

guard("open the error log", lambda: app.show_error_log(sid))
log = app.current_frame
text = " ".join(ctk.texts(log))
check("log shows your answer", "Your answer:" in text, text[:300])
check("log shows the correct answer", "Correct answer:" in text, text[:300])
check("log shows time taken", "s" in text and any(ch.isdigit() for ch in text))
check("log shows a timestamp", any(m in text for m in
                                   ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")), text[:400])
check("log offers to show the question", "Show question & rationale" in text, text[:400])
check("log marks lucky guesses", "LUCKY" in text, text[:300])
check("log marks misses", "MISS" in text, text[:300])
check("log shows crossed-out choices", "crossed out" in text, text[:600])

# Pagination: 12 rows at a time, not all of them.
rendered = sum(1 for t in ctk.texts(log) if t in ("❌ MISS", "🍀 LUCKY"))
check("log paginates instead of rendering everything",
      rendered <= log.PAGE_SIZE, f"{rendered} rows rendered")
more = [b for b in ctk.buttons(log, "more") if "left)" in str(b.cget("text"))]
check("log offers a Show more button", len(more) == 1, str(len(more)))
guard("show more works", lambda: more[0].invoke())
rendered2 = sum(1 for t in ctk.texts(log) if t in ("❌ MISS", "🍀 LUCKY"))
check("show more renders more rows", rendered2 > rendered, f"{rendered} -> {rendered2}")

# Expanding a row must show the question image, not crash.
expanders = ctk.buttons(log, "Show question")
check("every row has an expander", len(expanders) >= 1, str(len(expanders)))
guard("expanding a question does not crash", lambda: expanders[0].invoke())
check("expanding kept an image reference alive", len(log._image_refs) >= 1,
      str(len(log._image_refs)))
after_expand = " ".join(ctk.texts(log))
check("expanded row labels the question", "QUESTION" in after_expand, after_expand[:200])
check("expanded row labels the rationale", "COLLEGE BOARD RATIONALE" in after_expand)
guard("collapsing again works", lambda: expanders[0].invoke())
guard("re-expanding reuses the built body", lambda: expanders[0].invoke())
check("image refs stay bounded", len(log._image_refs) <= 24, str(len(log._image_refs)))

# =====================================================================
print("\n[6] quiz screen no longer re-decodes on state changes")
app.show_test_setup()
setup = app.current_frame
setup.length_var.set("Single section")
setup.section_var.set("Reading and Writing")
setup.start()
quiz = app.current_frame

decodes["n"] = 0
Image.open = counting_open
quiz.load_question(5)
after_nav = decodes["n"]
quiz.toggle_flag()
quiz.toggle_shaky()
quiz.select("B")
quiz.eliminate("C")
check("toggling flag/not-sure/answer decodes nothing",
      decodes["n"] == after_nav, f"{after_nav} -> {decodes['n']}")
quiz.load_question(5)
check("returning to the same question decodes nothing",
      decodes["n"] == after_nav, str(decodes["n"]))
Image.open = _orig

check("flag state survived", 5 in quiz.flagged)
check("not-sure state survived", 5 in quiz.shaky)
check("answer survived", quiz.user_answers.get(5) == "B")
check("elimination survived", "C" in quiz.eliminated.get(5, set()))

# The index popup updates in place instead of rebuilding.
quiz.open_index()
built = len(quiz._index_buttons)
check("index built one button per question", built == len(quiz.questions), str(built))
ids_before = [id(b) for b in quiz._index_buttons]
quiz.load_question(6)
quiz.toggle_flag()
check("index buttons are reused, not rebuilt",
      [id(b) for b in quiz._index_buttons] == ids_before)

# =====================================================================
print("\n[7] no growth over an extended session")
import gc
app.show_home_screen()
gc.collect()
baseline = len(gc.get_objects())
for _ in range(15):
    app.show_plan(date(2026, 8, 17))
    app.show_home_screen()
    app.show_history()
    app.show_dashboard()
gc.collect()
after = len(gc.get_objects())
growth = after - baseline
check("repeated screen switching does not leak badly", growth < 20000,
      f"grew by {growth} objects over 60 screen switches")
check("image cache stayed bounded", ui.cache_stats()["size"] <= ui._CACHE_LIMIT,
      str(ui.cache_stats()))
check("no background work left hanging", background.pending() == 0,
      str(background.pending()))

pending_before = tk.pending_count()
app.show_home_screen()
tk.pump(10000)
guard("destroyed screens do not fire callbacks", lambda: tk.pump(10000))
check("timer callbacks do not accumulate", tk.pending_count() <= pending_before + 6,
      f"{pending_before} -> {tk.pending_count()}")

guard("clean shutdown", app._on_close)
check("worker stopped on shutdown", background.pending() == 0)

print(f"\n{'=' * 62}\nPASSED {len(PASS)}   FAILED {len(FAIL)}")
if FAIL:
    for name, detail in FAIL:
        print(f"   FAILED: {name}   {detail}")
    sys.exit(1)
print("all green")
