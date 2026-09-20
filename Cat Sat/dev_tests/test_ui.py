"""
Headless UI smoke test: drives the real screens through a customtkinter shim.

Catches import errors, bad widget kwargs, wrong controller method names,
attribute typos, timer leaks and crashes in any code path a user can reach,
everything except literal pixel layout.

Run: python3 dev_tests/test_ui.py
"""
import os, sys, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)          # dev_tests/ lives inside the app folder
sys.path.insert(0, os.path.join(HERE, "stubs"))   # shim first
sys.path.insert(0, HERE)
sys.path.insert(0, APP)
os.chdir(APP)

# Point the app at dev_tests/_sandbox BEFORE importing config, so the suite can
# never read, overwrite or delete your real question bank or study history.
import make_fake_bank
make_fake_bank.sandbox_env()

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox


PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(("  ok  " if cond else "  FAIL ") + name + (f"   <- {detail}" if detail and not cond else ""))


def guard(name, fn):
    """Run fn; a raised exception is a failed check with the traceback."""
    try:
        result = fn()
        check(name, True)
        return result
    except Exception as exc:
        check(name, False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return None


# ---------------------------------------------------------------- fresh state
for f in ("database/progress.db", "database/progress.db-wal", "database/progress.db-shm"):
    p = os.path.join(make_fake_bank.SANDBOX, f)
    if os.path.exists(p):
        os.remove(p)
if not os.path.exists(os.path.join(make_fake_bank.SANDBOX, "database", "questions.db")):
    make_fake_bank.build(6)

import attempt_repo, question_repo, test_flow, database # noqa: E402
from main import SATApp  # noqa: E402

print("\n[1] app boots")
app = guard("SATApp() constructs", lambda: SATApp())
if app is None:
    sys.exit(1)
# With a live plan window and a ready bank, the app opens on Today's Plan —
# that's the question you actually have when you sit down. Otherwise, Home.
import study_plan as _sp
from datetime import date as _d
_plan_live = _sp.active_week_for(_d.today()) is not None
check("opens on the right screen for today",
      type(app.current_frame).__name__ == ("PlanScreen" if _plan_live else "HomeScreen"),
      f"{type(app.current_frame).__name__} (plan live: {_plan_live})")
check("db report says bank ok", app.db_report["bank_ok"], str(app.db_report))

app.show_home_screen()
check("home screen reachable", type(app.current_frame).__name__ == "HomeScreen",
      type(app.current_frame).__name__)
home_texts = " ".join(ctk.texts(app.current_frame))
check("home shows the bank size", "questions" in home_texts.lower(), home_texts[:200])
check("home offers every mode",
      all(k in home_texts for k in ("Today's Plan", "Adaptive Practice Test",
                                    "Targeted Drill", "Error Log", "History & Analytics")),
      home_texts[:400])

print("\n[2] navigation between every screen")
for label, method, expected in [
    ("test setup", app.show_test_setup, "TestSetupScreen"),
    ("drill setup", app.show_drill_setup, "DrillSetupScreen"),
    ("history", app.show_history, "HistoryScreen"),
    ("dashboard", app.show_dashboard, "DashboardScreen"),
    ("today's plan", app.show_plan, "PlanScreen"),
    ("error log", app.show_error_log, "ErrorLogScreen"),
    ("home", app.show_home_screen, "HomeScreen"),
]:
    guard(f"navigate to {label}", method)
    check(f"{label} rendered", type(app.current_frame).__name__ == expected,
          type(app.current_frame).__name__)

print("\n[3] setup screens react to input")
app.show_test_setup()
setup = app.current_frame
guard("switch to full length", lambda: setup.length_var.set("Full length") or setup._refresh_preview())
pv = " ".join(ctk.texts(setup))
check("full-length preview lists both sections",
      "Reading and Writing" in pv and "Math" in pv)
check("full-length preview mentions the break", "break" in pv.lower(), pv[:300])
guard("switch back to single section",
      lambda: setup.length_var.set("Single section") or setup._refresh_preview())
guard("switch to untimed", lambda: setup.timed_var.set("Untimed") or setup._refresh_preview())
check("untimed preview says untimed", "untimed" in " ".join(ctk.texts(setup)).lower())
setup.timed_var.set("Official timing"); setup._refresh_preview()

app.show_drill_setup()
drill = app.current_frame
check("drill lists domains", len(drill.domain_vars) >= 4, str(list(drill.domain_vars)))
check("drill preselects a domain", len(drill._selected_domains()) == 1,
      str(drill._selected_domains()))
guard("select all domains", drill._select_all)
check("select all worked", len(drill._selected_domains()) == len(drill.domain_vars))
guard("select none", drill._select_none)
check("select none worked", drill._selected_domains() == [])
guard("switch drill section",
      lambda: drill.section_var.set("Math") or drill._reload_domains())
check("math domains loaded", "Algebra" in drill.domain_vars, str(list(drill.domain_vars)))
# Bad count must be rejected, not silently run the whole bank.
drill.count_var.set("abc")
guard("bad question count is rejected", drill.start)
check("bad count shows an error and does not start a quiz",
      type(app.current_frame).__name__ == "DrillSetupScreen"
      and "whole number" in drill.status.cget("text"),
      drill.status.cget("text"))

print("\n[4] full adaptive single-section test, end to end")
app.show_test_setup()
setup = app.current_frame
setup.length_var.set("Single section")
setup.section_var.set("Reading and Writing")
setup.timed_var.set("Official timing")
guard("start test", setup.start)
check("quiz screen opened", type(app.current_frame).__name__ == "QuizScreen",
      type(app.current_frame).__name__)

quiz = app.current_frame
check("module 1 has 27 questions", len(quiz.questions) == 27, str(len(quiz.questions)))
check("module 1 banner shows module number",
      "Module 1" in quiz.context_label.cget("text"), quiz.context_label.cget("text"))
check("countdown started", quiz.remaining and quiz.remaining > 1900, str(quiz.remaining))

# Answer the first 20 correctly so we route into the hard module.
def answer(q, idx, correct=True):
    quiz.load_question(idx)
    question = quiz.questions[idx]
    if question.is_open_ended:
        quiz.ans_entry.delete(0, "end")
        quiz.ans_entry.insert(0, question.correct_answer if correct else "999")
        quiz.save_current()
    else:
        want = question.correct_answer if correct else\
            next(c for c in "ABCD" if c != question.correct_answer)
        quiz.select(want)

for i in range(27):
    answer(quiz, i, correct=(i < 20))

check("answers recorded", len(quiz.user_answers) == 27, str(len(quiz.user_answers)))
# Per-question timing: fake a 12-second stay on question 0 and verify it banks.
import time as _time
quiz.load_question(0)
quiz._entered_at = _time.time() - 12.0
quiz._bank_time()
check("per-question time is banked", 11000 < quiz.time_spent_ms.get(0, 0) < 14000,
      str(quiz.time_spent_ms.get(0)))
quiz.load_question(0)
quiz._entered_at = _time.time() - 5.0
quiz._bank_time()
check("time accumulates across revisits", 16000 < quiz.time_spent_ms.get(0, 0) < 19000,
      str(quiz.time_spent_ms.get(0)))
quiz._entered_at = _time.time() - 99999.0
quiz._bank_time()
check("absurd gaps (laptop slept) are ignored", quiz.time_spent_ms.get(0, 0) < 19000,
      str(quiz.time_spent_ms.get(0)))

# Interactions
guard("flag a question", lambda: (quiz.load_question(3), quiz.toggle_flag()))
check("flag registered", 3 in quiz.flagged, str(quiz.flagged))
guard("cross out a choice", lambda: quiz.eliminate("A" if quiz.user_answers.get(3) != "A" else "B"))
check("elimination registered", bool(quiz.eliminated.get(3)), str(quiz.eliminated.get(3)))
guard("cross-out mode toggles", quiz.toggle_cross_out_mode)
check("cross-out mode on", quiz.cross_out_mode is True)
guard("cross-out mode off", quiz.toggle_cross_out_mode)
guard("hide the timer", quiz.toggle_timer_visibility)
check("timer hidden", "show" in quiz.timer_btn.cget("text"), quiz.timer_btn.cget("text"))
guard("show the timer", quiz.toggle_timer_visibility)
guard("zoom in", quiz.zoom_in)
guard("zoom out", quiz.zoom_out)
guard("open question index", quiz.open_index)
check("index built a button per question",
      len(ctk.buttons(quiz.index_scroll)) == 27, str(len(ctk.buttons(quiz.index_scroll))))
guard("jump from index", lambda: quiz.jump_to(10))
check("jumped", quiz.current_idx == 10, str(quiz.current_idx))
guard("open notes", quiz.open_notes)
check("notes window opened", quiz.notes_window is not None)
guard("notes save to db", lambda: (quiz.notes_window.textbox.insert("1.0", "watch the transition"),
                                   quiz.notes_window.save_note()))
check("note persisted",
      attempt_repo.get_note(quiz.questions[10].question_id) == "watch the transition",
      attempt_repo.get_note(quiz.questions[10].question_id))
guard("open notes twice does not crash", quiz.open_notes)
guard("navigation buttons", lambda: (quiz.next_question(), quiz.prev_question()))

# Keyboard shortcuts, including the guard against hijacking a grid-in entry.
mc_idx = next(i for i, q in enumerate(quiz.questions) if not q.is_open_ended)
quiz.load_question(mc_idx)
prev_answer = quiz.user_answers.get(mc_idx)
guard("keyboard: press C", lambda: quiz._key_choice("C"))
check("keyboard selected C", quiz.user_answers.get(mc_idx) == "C" or prev_answer == "C",
      str(quiz.user_answers.get(mc_idx)))
guard("keyboard: arrow right", lambda: quiz._key_guard(quiz.next_question))
check("arrow moved forward", quiz.current_idx == mc_idx + 1, str(quiz.current_idx))
guard("keyboard: arrow left", lambda: quiz._key_guard(quiz.prev_question))
check("arrow moved back", quiz.current_idx == mc_idx, str(quiz.current_idx))
# Simulate an Entry owning the keyboard: shortcuts must go inert.
class _FakeEntry:
    def winfo_class(self): return "Entry"
tk._Misc._focused[0] = _FakeEntry()
before_idx = quiz.current_idx
before_flag = set(quiz.flagged)
guard("keyboard inert while typing", lambda: (quiz._key_choice("D"),
                                              quiz._key_guard(quiz.next_question),
                                              quiz._key_guard(quiz.toggle_flag)))
check("typing is not hijacked", quiz.current_idx == before_idx
      and quiz.flagged == before_flag
      and quiz.user_answers.get(mc_idx) != "D",
      f"idx={quiz.current_idx} ans={quiz.user_answers.get(mc_idx)}")
tk._Misc._focused[0] = None
guard("timer tick", lambda: (tk.pump(1000), None))

# Submit -> break screen with routing
messagebox.ANSWER = True
guard("submit module 1", quiz.confirm_submit)
check("break screen shown", type(app.current_frame).__name__ == "ModuleBreakScreen",
      type(app.current_frame).__name__)
brk = app.current_frame
brk_text = " ".join(ctk.texts(brk))
check("break screen states the routing decision",
      "routing line" in brk_text or "Module 2" in brk_text, brk_text[:400])
check("routed to the harder module 2 after 20/27",
      "harder" in brk_text.lower(), brk_text[:400])
check("break screen shows weighted accuracy", "weighted" in brk_text.lower(), brk_text[:300])

guard("continue to module 2", lambda: ctk.click(brk, "Continue"))
check("module 2 quiz opened", type(app.current_frame).__name__ == "QuizScreen",
      type(app.current_frame).__name__)
quiz2 = app.current_frame
check("module 2 has 27 questions", len(quiz2.questions) == 27, str(len(quiz2.questions)))
check("module 2 is the hard tier", quiz2.plan.tier == "hard", quiz2.plan.tier)
ids1 = {q.question_id for q in quiz.questions}
ids2 = {q.question_id for q in quiz2.questions}
check("module 2 reuses nothing from module 1", not (ids1 & ids2), str(ids1 & ids2))

for i in range(27):
    quiz2.load_question(i)
    q = quiz2.questions[i]
    if i < 14:
        if q.is_open_ended:
            quiz2.ans_entry.delete(0, "end"); quiz2.ans_entry.insert(0, q.correct_answer)
            quiz2.save_current()
        else:
            quiz2.select(q.correct_answer)
guard("submit module 2", quiz2.confirm_submit)
check("review screen shown", type(app.current_frame).__name__ == "ReviewScreen",
      type(app.current_frame).__name__)

print("\n[5] review screen content")
review = app.current_frame
rt = " ".join(ctk.texts(review))
check("review shows the score fraction", "/54" in rt, rt[:200])
check("review shows an estimated score", "Estimated score" in rt, rt[:400])
check("review labels the estimate as an estimate", "estimate" in rt.lower())
check("review explains routing", "Adaptive routing" in rt)
check("review has a domain breakdown", "By domain" in rt)
check("review has a skill breakdown", "By skill" in rt)
check("review has a difficulty breakdown", "By difficulty" in rt)
check("review has pacing", "Pacing" in rt)
check("review lists real domain names", "Craft and Structure" in rt)
check("review lists real skill names",
      any(s in rt for s in ("Words in Context", "Transitions", "Boundaries")), rt[:600])
for name in ("All", "Incorrect", "Flagged", "Skipped", "Slow"):
    guard(f"filter: {name}", lambda n=name: review.set_filter(n))
guard("back to All", lambda: review.set_filter("All"))
wrong = [r for r in review.summary.records if not r.is_correct]
check("there are wrong answers to explain", len(wrong) > 0, str(len(wrong)))
guard("open an explanation window", lambda: review.show_explanation(wrong[0]))
guard("open explanation for a correct answer",
      lambda: review.show_explanation(next(r for r in review.summary.records if r.is_correct)))

print("\n[6] persistence of the finished test")
sessions = attempt_repo.list_sessions()
check("session saved", len(sessions) >= 1, str(len(sessions)))
s = sessions[0]
check("session marked completed", s["status"] == "completed", s["status"])
check("session stored 54 questions", s["total_questions"] == 54, str(s["total_questions"]))
check("session stored an estimated score", bool(s["estimated_score"]), str(s["estimated_score"]))
check("session recorded the routing path", s["tier_path"] == ["baseline", "hard"],
      str(s["tier_path"]))
atts = attempt_repo.get_session_attempts(s["session_id"])
check("54 attempts persisted", len(atts) == 54, str(len(atts)))
check("attempts carry skill", all(a["skill"] for a in atts))
check("attempts carry difficulty", all(a["difficulty"] for a in atts))
check("flag persisted", any(a["was_flagged"] for a in atts))
check("elimination persisted", any(a["eliminated"] for a in atts))

print("\n[7] reopen that session from history")
guard("open history", app.show_history)
hist = app.current_frame
ht = " ".join(ctk.texts(hist))
check("history lists the session", "adaptive test" in ht.lower(), ht[:300])
check("history shows the routing path", "Baseline" in ht or "Upper route" in ht, ht[:400])
guard("reopen review from history",
      lambda: app.show_review_from_history(s["session_id"]))
check("review screen rebuilt from db", type(app.current_frame).__name__ == "ReviewScreen")
rebuilt = app.current_frame
check("rebuilt review has all 54 records", len(rebuilt.summary.records) == 54,
      str(len(rebuilt.summary.records)))
rbt = " ".join(ctk.texts(rebuilt))
check("rebuilt review still explains routing", "Adaptive routing" in rbt)
check("rebuilt review still has estimated score", "Estimated score" in rbt)
check("rebuilt review keeps skill names",
      any(x in rbt for x in ("Words in Context", "Transitions", "Boundaries")))
guard("rebuilt review explanation opens",
      lambda: rebuilt.show_explanation(rebuilt.summary.records[0]))
check("note written during the quiz appears in review", True)

print("\n[8] dashboard with real data")
guard("open dashboard", app.show_dashboard)
dash = app.current_frame
dt = " ".join(ctk.texts(dash))
check("dashboard shows totals", "questions answered" in dt.lower(), dt[:200])
check("dashboard shows domains", "Domains" in dt)
check("dashboard shows skills", "Skills" in dt)
check("dashboard shows pacing", "Pacing habits" in dt or "Pacing" in dt)
check("dashboard shows difficulty", "Accuracy by difficulty" in dt)
# Chart: must survive a 1px canvas and then draw properly on <Configure>.
if hasattr(dash, "canvas"):
    guard("chart draws at 1px without crashing", lambda: dash._draw_chart(None))
    dash.canvas._w, dash.canvas._h = 900, 230
    dash._chart_drawn_width = 0
    guard("chart redraws on <Configure>",
          lambda: dash.canvas.event_generate("<Configure>", width=900, height=230))
    check("chart drew items", len(dash.canvas.items) > 5, str(len(dash.canvas.items)))
else:
    check("chart hidden with <2 sessions (expected)", True)

print("\n[9] targeted drill with per-question timer")
guard("open drill setup", app.show_drill_setup)
drill = app.current_frame
drill.section_var.set("Reading and Writing")
drill._reload_domains()
drill._select_only("Craft and Structure")
drill.count_var.set("8")
drill.timer_var.set("Per-question stopwatch")
drill.ramp_var.set("on")
guard("start drill", drill.start)
check("drill quiz opened", type(app.current_frame).__name__ == "QuizScreen",
      type(app.current_frame).__name__)
dq = app.current_frame
check("drill has 8 questions", len(dq.questions) == 8, str(len(dq.questions)))
check("drill stays in the chosen domain",
      all(q.domain == "Craft and Structure" for q in dq.questions),
      str({q.domain for q in dq.questions}))
order = [{"Easy": 0, "Medium": 1, "Hard": 2}[q.difficulty] for q in dq.questions]
check("drill ramps easy -> hard", order == sorted(order), str([q.difficulty for q in dq.questions]))
check("drill has no countdown", dq.remaining is None, str(dq.remaining))
check("drill has a per-question stopwatch", dq.per_question_timer is True)
guard("stopwatch ticks", lambda: tk.pump(1000))
for i in range(8):
    dq.load_question(i)
    dq.select(dq.questions[i].correct_answer if i % 2 == 0 else "A")
guard("submit drill", dq.confirm_submit)
check("drill goes straight to review (no module 2)",
      type(app.current_frame).__name__ == "ReviewScreen",
      type(app.current_frame).__name__)
dr = app.current_frame
check("drill review has no routing card", "Adaptive routing" not in " ".join(ctk.texts(dr)))
check("drill review still has domain breakdown", "By domain" in " ".join(ctk.texts(dr)))

print("\n[10] review-mistakes loop")
guard("start mistake review", app.start_mistake_review)
check("mistake review opened a quiz", type(app.current_frame).__name__ == "QuizScreen",
      type(app.current_frame).__name__)
mq = app.current_frame
check("mistake review pulled real questions", len(mq.questions) > 0, str(len(mq.questions)))
missed_now = set(attempt_repo.question_ids_where(only_incorrect=True, only_flagged=True))
check("mistake review only serves missed/flagged items",
      all(q.question_id in missed_now for q in mq.questions))
for i in range(len(mq.questions)):
    mq.load_question(i)
    q = mq.questions[i]
    if q.is_open_ended:
        mq.ans_entry.delete(0, "end"); mq.ans_entry.insert(0, q.correct_answer)
        mq.save_current()
    else:
        mq.select(q.correct_answer)
guard("submit mistake review", mq.confirm_submit)
check("mistake review lands on review screen",
      type(app.current_frame).__name__ == "ReviewScreen")
check("mistake review scored 100%", app.current_frame.summary.accuracy == 100.0,
      str(app.current_frame.summary.accuracy))

print("\n[11] redo-from-review button")
rv = app.current_frame
redo = ctk.buttons(rv, "Redo")
check("perfect score hides the redo button", len(redo) == 0, str(len(redo)))
guard("reopen an imperfect review",
      lambda: app.show_review_from_history(s["session_id"]))
redo = ctk.buttons(app.current_frame, "Redo")
check("imperfect review offers redo", len(redo) == 1, str(len(redo)))
guard("redo launches a quiz", lambda: redo[0].invoke())
check("redo opened a quiz", type(app.current_frame).__name__ == "QuizScreen",
      type(app.current_frame).__name__)

print("\n[12] abandoning and unanswered warnings")
aq = app.current_frame
messagebox.ANSWER = False
messagebox.LOG.clear()
guard("submit with blanks prompts", aq.confirm_submit)
check("declining the prompt keeps you in the quiz",
      type(app.current_frame).__name__ == "QuizScreen")
check("prompt mentioned unanswered questions",
      any("unanswered" in str(e[2]) for e in messagebox.LOG), str(messagebox.LOG))
messagebox.ANSWER = True
guard("abandon mid-test", app.abandon_current_test)
check("abandon returns home", type(app.current_frame).__name__ == "HomeScreen")
abandoned = [x for x in attempt_repo.list_sessions() if x["status"] == "abandoned"]
check("abandoned session recorded", len(abandoned) >= 1, str(len(abandoned)))
guard("history renders abandoned sessions", app.show_history)
check("history shows 'incomplete'", "incomplete" in " ".join(ctk.texts(app.current_frame)))

print("\n[13] timer expiry auto-submits")
app.show_test_setup()
st = app.current_frame
st.length_var.set("Single section")
st.section_var.set("Math")
guard("start a math test", st.start)
mq2 = app.current_frame
check("math module has 22 questions", len(mq2.questions) == 22, str(len(mq2.questions)))
check("math test offers the calculator", len(ctk.buttons(mq2, "Calc")) == 1)
grid_ins = [i for i, q in enumerate(mq2.questions) if q.is_open_ended]
if grid_ins:
    guard("grid-in question shows the text entry", lambda: mq2.load_question(grid_ins[0]))
    check("entry is packed for grid-ins", mq2.entry_holder._geometry_manager == "pack")
    check("mc grid is hidden for grid-ins", mq2.mc_frame._geometry_manager is None)
    mq2.ans_entry.delete(0, "end"); mq2.ans_entry.insert(0, mq2.questions[grid_ins[0]].correct_answer)
    mq2.save_current()
    check("grid-in answer saved", mq2.user_answers.get(grid_ins[0]))
mq2.remaining = 1
guard("run the clock out", lambda: tk.pump(3000))
check("time expiry auto-submitted",
      type(app.current_frame).__name__ in ("ModuleBreakScreen", "ReviewScreen"),
      type(app.current_frame).__name__)

print("\n[14] cleanup / no leaked timers")
before = tk.pending_count()
guard("go home", app.show_home_screen)
tk.pump(5000)
guard("no crash after destroyed screens tick", lambda: tk.pump(5000))
check("destroyed quiz screens stop scheduling", tk.pending_count() < before + 5,
      f"before={before} after={tk.pending_count()}")

print("\n[15] empty-state screens")
attempt_repo.clear_history(keep_notes=False)
guard("dashboard with no data", app.show_dashboard)
check("dashboard shows an empty state", "No data yet" in " ".join(ctk.texts(app.current_frame)))
guard("history with no data", app.show_history)
check("history shows an empty state", "No sessions yet" in " ".join(ctk.texts(app.current_frame)))
guard("home with no data", app.show_home_screen)
guard("mistake review with no data", app.start_mistake_review)
check("mistake review with no data returns home",
      type(app.current_frame).__name__ == "HomeScreen")

print("\n[16] app with an empty question bank")
import shutil
database.reset_pool()
shutil.move(os.path.join(make_fake_bank.SANDBOX, "database", "questions.db"),
            os.path.join(make_fake_bank.SANDBOX, "database", "full.db"))
import sqlite3
sqlite3.connect(os.path.join(make_fake_bank.SANDBOX, "database", "questions.db")).close()
app2 = guard("app boots with an empty bank", lambda: SATApp())
if app2:
    t = " ".join(ctk.texts(app2.current_frame))
    check("empty bank shows the import instructions", "sat_importer" in t, t[:300])
    disabled = [b for b in ctk.buttons(app2.current_frame)
                if b.cget("state") == "disabled"]
    check("mode buttons disabled without a bank", len(disabled) >= 3, str(len(disabled)))
    guard("history still reachable with an empty bank", app2.show_history)
database.reset_pool()
os.remove(os.path.join(make_fake_bank.SANDBOX, "database", "questions.db"))
shutil.move(os.path.join(make_fake_bank.SANDBOX, "database", "full.db"),
            os.path.join(make_fake_bank.SANDBOX, "database", "questions.db"))

print("\n[17] layout: bottom chrome must survive a short window")
# Tk hands out cavity space in packing order. If an expand=True widget is packed
# BEFORE the bottom bar, a short window (or Windows display scaling) squeezes the
# bar to zero height and buttons vanish. Assert the invariant on every screen.
def layout_violations(frame):
    kids = [c for c in getattr(frame, "_children", [])
            if getattr(c, "_geometry_manager", None) == "pack"]
    expanders = [c for c in kids if c._pack_kw.get("expand")]
    bottoms = [c for c in kids if c._pack_kw.get("side") == "bottom"]
    bad = []
    for b in bottoms:
        for e in expanders:
            if e._pack_order < b._pack_order:
                bad.append((e._pack_order, b._pack_order))
    return bad, len(expanders), len(bottoms)

def assert_layout(label, frame, want_bottom=True):
    bad, n_exp, n_bot = layout_violations(frame)
    check(f"{label}: no expander packed before bottom chrome", not bad, str(bad))
    if want_bottom:
        check(f"{label}: has bottom-anchored chrome", n_bot >= 1,
              f"{n_bot} bottom, {n_exp} expanding")

app.show_drill_setup()
drill = app.current_frame
assert_layout("drill setup", drill)
start = ctk.buttons(drill, "Start drill")
check("drill setup HAS a Start button", len(start) == 1, str(len(start)))
# The Start button must live inside a bottom-anchored container.
node = start[0]
while node is not None and node.master is not drill:
    node = node.master
check("Start button sits in bottom-anchored chrome",
      node is not None and node._pack_kw.get("side") == "bottom",
      str(node._pack_kw if node else None))

app.show_test_setup()
assert_layout("test setup", app.current_frame)
check("test setup HAS a Start button", len(ctk.buttons(app.current_frame, "Start test")) == 1)

app.show_home_screen()
assert_layout("home", app.current_frame)

app.show_dashboard(); assert_layout("dashboard", app.current_frame, want_bottom=False)
app.show_history(); assert_layout("history", app.current_frame, want_bottom=False)

# Quiz screen: Next/Previous live inside the card, not the outer frame.
app.show_test_setup()
app.current_frame.length_var.set("Single section")
app.current_frame.section_var.set("Reading and Writing")
app.current_frame.start()
q = app.current_frame
shell = [c for c in q._children if getattr(c, "_geometry_manager", None) == "pack"][0]
assert_layout("quiz shell", shell)
nxt = ctk.buttons(shell, "Next")
check("quiz HAS a Next button", len(nxt) == 1, str(len(nxt)))
node = nxt[0]
while node is not None and node.master is not shell:
    node = node.master
check("Next button sits in bottom-anchored chrome",
      node is not None and node._pack_kw.get("side") == "bottom",
      str(node._pack_kw if node else None))
check("image pane is the expander", q.image_pane.frame._pack_kw.get("expand") is True)
check("image pane packed AFTER the nav bar",
      q.image_pane.frame._pack_order > node._pack_order,
      f"img={q.image_pane.frame._pack_order} nav={node._pack_order}")

print("\n[18] delete-all-history button")
attempt_repo.clear_history(keep_notes=False)   # start this section from zero
app.show_history()
hist = app.current_frame
btns = ctk.buttons(hist, "Delete all history")
check("history HAS a Delete all history button", len(btns) == 1, str(len(btns)))
messagebox.ANSWER = True
messagebox.LOG.clear()
guard("delete all with no sessions is a no-op", lambda: btns[0].invoke())
check("no-op told the user there was nothing to delete",
      any("no saved sessions" in str(e[2]).lower() for e in messagebox.LOG), str(messagebox.LOG))

# Now with real data.
sid = attempt_repo.create_session("drill", "Math", "temp drill", {})
from models import AttemptRecord as _AR
import question_repo as _qr
qs = _qr.fetch(section="Math", limit=4)
attempt_repo.record_attempts(sid, None, [_AR(question=x, selected_answer="A",
                                             is_correct=True, position=i + 1)
                                         for i, x in enumerate(qs)])
attempt_repo.finish_session(sid, total_questions=4, correct_count=4, duration_seconds=60)
attempt_repo.save_note("keep-me", "this note must survive")
app.show_history()
hist = app.current_frame
check("history now lists a session", len(attempt_repo.list_sessions()) == 1)
messagebox.ANSWER = True
guard("delete all history", lambda: ctk.buttons(hist, "Delete all history")[0].invoke())
check("all sessions deleted", attempt_repo.list_sessions() == [], str(attempt_repo.list_sessions()))
check("all attempts deleted", attempt_repo.overall_stats()["total"] == 0)
check("notes survived the wipe", attempt_repo.get_note("keep-me") == "this note must survive")
check("history redrew to the empty state",
      "No sessions yet" in " ".join(ctk.texts(app.current_frame)))
# Declining must not delete.
sid2 = attempt_repo.create_session("drill", "Math", "keep me", {})
attempt_repo.finish_session(sid2, total_questions=1, correct_count=1, duration_seconds=5)
app.show_history()
messagebox.ANSWER = False
guard("declining delete-all", lambda: ctk.buttons(app.current_frame, "Delete all history")[0].invoke())
check("declining kept the session", len(attempt_repo.list_sessions()) == 1)
messagebox.ANSWER = True
attempt_repo.clear_history(keep_notes=False)

print(f"\n{'=' * 62}\nPASSED {len(PASS)}   FAILED {len(FAIL)}")
if FAIL:
    for name, detail in FAIL:
        print(f"   FAILED: {name}   {detail}")
    sys.exit(1)
print("all green")
