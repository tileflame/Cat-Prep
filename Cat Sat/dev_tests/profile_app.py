"""
Performance profiler. Counts the things that actually cost time in a Tk app:

  * widgets created        — CTk widgets are canvas-backed and expensive
  * sqlite connections     — each one re-runs PRAGMA journal_mode
  * sqlite queries
  * PIL image opens        — 200-DPI PNGs are the single biggest cost
  * wall time

Run: python3 dev_tests/profile_app.py
"""
import os, sys, time, sqlite3, collections

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "stubs"))
sys.path.insert(0, HERE)
sys.path.insert(0, APP)
os.chdir(APP)

import make_fake_bank
make_fake_bank.sandbox_env()
if not os.path.exists(os.path.join(make_fake_bank.SANDBOX, "database", "questions.db")):
    make_fake_bank.build(14)
make_fake_bank.reset_progress()

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
from PIL import Image

COUNTS = collections.Counter()

# ---- instrument widget creation
_orig_misc_init = tk._Misc.__init__
def _counted_init(self, master=None, **kw):
    COUNTS["widgets"] += 1
    _orig_misc_init(self, master, **kw)
tk._Misc.__init__ = _counted_init

# ---- instrument sqlite
class _CountingConn(sqlite3.Connection):
    def execute(self, sql, params=()):
        COUNTS["db_queries"] += 1
        return super().execute(sql, params)
    def executemany(self, sql, seq):
        COUNTS["db_queries"] += 1
        return super().executemany(sql, seq)
    def executescript(self, script):
        COUNTS["db_queries"] += 1
        return super().executescript(script)

_orig_connect = sqlite3.connect
def _counted_connect(*a, **kw):
    COUNTS["db_connections"] += 1
    kw.setdefault("factory", _CountingConn)
    return _orig_connect(*a, **kw)
sqlite3.connect = _counted_connect

# ---- instrument PIL, separating UI-thread decodes (which cause lag) from
# worker-thread decodes (which do not).
import threading as _th
_MAIN = _th.current_thread()
_orig_open = Image.open
def _counted_open(*a, **kw):
    if _th.current_thread() is _MAIN:
        COUNTS["ui_decodes"] += 1
    else:
        COUNTS["bg_decodes"] += 1
    return _orig_open(*a, **kw)
Image.open = _counted_open


class Probe:
    def __init__(self, label):
        self.label = label
    def __enter__(self):
        COUNTS.clear()
        self.t0 = time.perf_counter()
        return self
    def __exit__(self, *exc):
        ms = (time.perf_counter() - self.t0) * 1000
        RESULTS.append((self.label, ms, dict(COUNTS)))


RESULTS = []

import attempt_repo, background, database, question_repo, study_plan
from models import AttemptRecord
from main import SATApp
from datetime import date

database.init_db()
messagebox.ANSWER = True

with Probe("app startup"):
    app = SATApp()

for label, fn in (("-> Today's Plan", lambda: app.show_plan(date(2026, 8, 17))),
                  ("-> Home", app.show_home_screen),
                  ("-> Drill setup", app.show_drill_setup),
                  ("-> Test setup", app.show_test_setup),
                  ("-> Dashboard", app.show_dashboard),
                  ("-> History", app.show_history),
                  ("-> Error log", app.show_error_log)):
    with Probe(label):
        fn()

# --- quiz screen: the hot path
app.show_test_setup()
setup = app.current_frame
setup.length_var.set("Single section")
setup.section_var.set("Reading and Writing")
with Probe("start a 27q module"):
    setup.start()
quiz = app.current_frame

with Probe("load_question (next question)"):
    quiz.load_question(1)

with Probe("select an answer"):
    quiz.select("B")

with Probe("toggle flag"):
    quiz.toggle_flag()

with Probe("toggle not-sure"):
    quiz.toggle_shaky()

with Probe("navigate through all 27 questions"):
    for i in range(27):
        quiz.load_question(i)
        tk.pump(200)                                 # fire the deferred prefetch
        background.wait_idle(); background.drain()   # a real user takes seconds per question

with Probe("open the question index"):
    quiz.open_index()

with Probe("answer all 27 + submit"):
    for i in range(27):
        quiz.load_question(i)
        q = quiz.questions[i]
        if q.is_open_ended:
            quiz.ans_entry.delete(0, "end"); quiz.ans_entry.insert(0, q.correct_answer)
            quiz.save_current()
        else:
            quiz.select(q.correct_answer if i % 3 else "Z")
        tk.pump(200); background.wait_idle(); background.drain()
    quiz.confirm_submit()

brk = app.current_frame
if type(brk).__name__ == "ModuleBreakScreen":
    with Probe("continue to module 2"):
        ctk.click(brk, "Continue")
    q2 = app.current_frame
    with Probe("answer module 2 + submit"):
        for i in range(len(q2.questions)):
            q2.load_question(i)
            qq = q2.questions[i]
            if qq.is_open_ended:
                q2.ans_entry.delete(0, "end"); q2.ans_entry.insert(0, qq.correct_answer)
                q2.save_current()
            else:
                q2.select(qq.correct_answer if i % 3 else "Z")
            tk.pump(200); background.wait_idle(); background.drain()
        q2.confirm_submit()

review = app.current_frame
if type(review).__name__ == "ReviewScreen":
    with Probe("review screen: change filter"):
        review.set_filter("Incorrect")
    with Probe("review screen: back to All (54 rows)"):
        review.set_filter("All")

sid = attempt_repo.list_sessions(limit=1)[0]["session_id"]
with Probe("reopen a 54q session from history"):
    app.show_review_from_history(sid)

with Probe("error log with real data"):
    app.show_error_log(sid)

with Probe("plan: tick a checkbox"):
    app.show_plan(date(2026, 8, 17))
    app.current_frame._toggle(0, True)

with Probe("switch screens 10x"):
    for _ in range(10):
        app.show_home_screen()
        app.show_plan(date(2026, 8, 17))

import ui_kit as _ui
print("\nimage cache:", _ui.cache_stats())
print(f"\n{'action':<42}{'ms':>9}{'widgets':>9}{'conn':>6}{'query':>7}"
      f"{'UI img':>8}{'bg img':>8}")
print("-" * 90)
total = 0
ui_decodes = 0
for label, ms, c in RESULTS:
    total += ms
    ui_decodes += c.get("ui_decodes", 0)
    print(f"{label:<42}{ms:>9.1f}{c.get('widgets', 0):>9}"
          f"{c.get('db_connections', 0):>6}{c.get('db_queries', 0):>7}"
          f"{c.get('ui_decodes', 0):>8}{c.get('bg_decodes', 0):>8}")
print("-" * 90)
print(f"{'TOTAL':<42}{total:>9.1f}{'':>9}{'':>6}{'':>7}{ui_decodes:>8}")
print("\nUI img = image decoded on the Tk thread (this is what you feel as lag).")
print("bg img = decoded on the worker thread while you were reading.")
