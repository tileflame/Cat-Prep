"""
The two highest-risk paths not covered by test_ui.py:

  A. a complete full-length sitting: RW M1 -> M2 -> section break -> Math M1 -> M2
  B. the LOWER routing path (deliberately bombing Module 1)

Run: python3 dev_tests/test_full_length.py
"""
import os, sys, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)          # dev_tests/ lives inside the app folder
sys.path.insert(0, os.path.join(HERE, "stubs"))
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
    try:
        out = fn()
        check(name, True)
        return out
    except Exception as exc:
        check(name, False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return None


for f in ("database/progress.db", "database/progress.db-wal", "database/progress.db-shm"):
    p = os.path.join(make_fake_bank.SANDBOX, f)
    if os.path.exists(p):
        os.remove(p)
if not os.path.exists(os.path.join(make_fake_bank.SANDBOX, "database", "questions.db")):
    make_fake_bank.build(6)

import attempt_repo  # noqa: E402
from main import SATApp  # noqa: E402

messagebox.ANSWER = True
app = SATApp()


def fill(quiz, correct_count):
    """Answer the first `correct_count` questions right, the rest wrong."""
    for i in range(len(quiz.questions)):
        quiz.load_question(i)
        q = quiz.questions[i]
        right = i < correct_count
        if q.is_open_ended:
            quiz.ans_entry.delete(0, "end")
            quiz.ans_entry.insert(0, q.correct_answer if right else "99999")
            quiz.save_current()
        else:
            quiz.select(q.correct_answer if right
                        else next(c for c in "ABCD" if c != q.correct_answer))


# =====================================================================
print("\n[A] full-length sitting: 4 modules + a section break")
app.show_test_setup()
setup = app.current_frame
setup.length_var.set("Full length")
setup.timed_var.set("Official timing")
guard("start full-length test", setup.start)
check("runner is in full_test mode", app.runner and app.runner.mode == "full_test",
      str(app.runner and app.runner.mode))
check("runner queued both sections",
      app.runner.sections == ["Reading and Writing", "Math"], str(app.runner.sections))

seen_modules = []
seen_breaks = []
guardrail = 0

while type(app.current_frame).__name__ != "ReviewScreen" and guardrail < 12:
    guardrail += 1
    name = type(app.current_frame).__name__

    if name == "QuizScreen":
        quiz = app.current_frame
        seen_modules.append((quiz.plan.section, quiz.plan.module_number,
                             quiz.plan.tier, len(quiz.questions)))
        # RW routes UP (22/27), Math routes DOWN (8/22).
        target = 22 if quiz.plan.section == "Reading and Writing" else 8
        fill(quiz, min(target, len(quiz.questions)))
        quiz.confirm_submit()

    elif name == "ModuleBreakScreen":
        brk = app.current_frame
        seen_breaks.append((brk.info.get("kind"), brk.info.get("tier"),
                            brk.info.get("minutes"), " ".join(ctk.texts(brk))))
        ctk.click(brk, "Continue" if not brk.remaining else "Skip break")
    else:
        break

check("reached the review screen", type(app.current_frame).__name__ == "ReviewScreen",
      type(app.current_frame).__name__)
check("sat exactly 4 modules", len(seen_modules) == 4, str(seen_modules))
check("module order was RW1, RW2, Math1, Math2",
      [(s, m) for s, m, _, _ in seen_modules] ==
      [("Reading and Writing", 1), ("Reading and Writing", 2), ("Math", 1), ("Math", 2)],
      str(seen_modules))
check("RW modules had 27 questions each",
      all(n == 27 for s, _, _, n in seen_modules if s == "Reading and Writing"),
      str(seen_modules))
check("Math modules had 22 questions each",
      all(n == 22 for s, _, _, n in seen_modules if s == "Math"), str(seen_modules))
check("RW routed UP after 22/27", seen_modules[1][2] == "hard", str(seen_modules[1]))
check("Math routed DOWN after 8/22", seen_modules[3][2] == "easy", str(seen_modules[3]))

check("there were 3 breaks (2 module, 1 section)", len(seen_breaks) == 3, str(len(seen_breaks)))
kinds = [k for k, _, _, _ in seen_breaks]
check("break kinds were module, section, module",
      kinds == ["module", "section", "module"], str(kinds))
section_break = seen_breaks[1]
check("the section break is 10 minutes", section_break[2] == 10, str(section_break[2]))
check("the section break reports the RW score",
      "Estimated section score" in section_break[3] or "scored" in section_break[3],
      section_break[3][:300])

# --- every question across the whole sitting must be unique
runner_ids = set()
attempts = attempt_repo.get_session_attempts(
    attempt_repo.list_sessions(limit=1)[0]["session_id"])
ids = [a["question_id"] for a in attempts]
check("full test asked 98 questions", len(ids) == 27 * 2 + 22 * 2, str(len(ids)))
check("no question repeated in the whole sitting", len(set(ids)) == len(ids),
      f"{len(ids) - len(set(ids))} repeats")

review = app.current_frame
rt = " ".join(ctk.texts(review))
check("review shows both section scores",
      "Reading and Writing" in rt and "Math" in rt)
check("review shows an estimated total", "estimated total" in rt.lower(), rt[:600])
summary = review.summary
check("estimated total is on the 400-1600 scale",
      400 <= summary.estimated_total <= 1600, str(summary.estimated_total))
check("two section scores computed", len(summary.section_scores) == 2,
      str(summary.section_scores))
check("the up-routed section scores higher than the down-routed one",
      summary.section_scores["Reading and Writing"] > summary.section_scores["Math"],
      str(summary.section_scores))
check("the down-routed section is capped below 800",
      summary.section_scores["Math"] <= 640, str(summary.section_scores["Math"]))
check("review explains routing for both sections",
      rt.count("routing line") >= 2 or rt.lower().count("module 2 was") >= 2, rt[:200])

session = attempt_repo.list_sessions(limit=1)[0]
check("session saved as full_test", session["mode"] == "full_test", str(session["mode"]))
check("session tier path has 4 entries",
      session["tier_path"] == ["baseline", "hard", "baseline", "easy"],
      str(session["tier_path"]))
check("session stored the estimated total",
      session["estimated_score"] == summary.estimated_total,
      f"{session['estimated_score']} vs {summary.estimated_total}")

guard("reopen the full test from history",
      lambda: app.show_review_from_history(session["session_id"]))
rebuilt = app.current_frame
check("rebuilt full test has 98 records", len(rebuilt.summary.records) == 98,
      str(len(rebuilt.summary.records)))
check("rebuilt full test still has 2 sections", len(rebuilt.summary.sections) == 2,
      str(len(rebuilt.summary.sections)))
check("rebuilt full test recomputes the same total",
      rebuilt.summary.estimated_total == summary.estimated_total,
      f"{rebuilt.summary.estimated_total} vs {summary.estimated_total}")

# =====================================================================
print("\n[B] lower routing path end to end")
app.show_test_setup()
setup = app.current_frame
setup.length_var.set("Single section")
setup.section_var.set("Reading and Writing")
guard("start a section test", setup.start)
quiz = app.current_frame
fill(quiz, 5)                        # 5/27 = 19%, clearly below the line
guard("submit a weak module 1", quiz.confirm_submit)
brk = app.current_frame
check("break screen shown", type(brk).__name__ == "ModuleBreakScreen", type(brk).__name__)
bt = " ".join(ctk.texts(brk))
check("break says you routed to the easier module", "easier" in bt.lower(), bt[:400])
check("break shows the low accuracy", "19%" in bt or "18%" in bt, bt[:300])
guard("continue", lambda: ctk.click(brk, "Continue"))
q2 = app.current_frame
check("module 2 is the easy tier", q2.plan.tier == "easy", q2.plan.tier)
check("easy module 2 skews easy",
      q2.plan.difficulty_actual["Easy"] > q2.plan.difficulty_actual["Hard"],
      str(q2.plan.difficulty_actual))
fill(q2, 27)                          # ace the easy module
guard("submit module 2", q2.confirm_submit)
rv = app.current_frame
check("review screen shown", type(rv).__name__ == "ReviewScreen", type(rv).__name__)
score = rv.summary.section_scores.get("Reading and Writing")
check("acing the easy module still caps the score", score is not None and score <= 640,
      str(score))
check("a single section has no 1600 total", rv.summary.estimated_total == 0,
      str(rv.summary.estimated_total))
rvt = " ".join(ctk.texts(rv))
check("module 2 improvement is visible", "MODULE 2" in rvt.upper(), rvt[:300])

# =====================================================================
print("\n[C] exiting during a break keeps your answers")
app.show_test_setup()
setup = app.current_frame
setup.length_var.set("Single section")
guard("start another test", setup.start)
quiz = app.current_frame
fill(quiz, 20)
guard("submit module 1", quiz.confirm_submit)
brk = app.current_frame
guard("save and exit from the break", lambda: ctk.click(brk, "Save and exit"))
check("back at home", type(app.current_frame).__name__ == "HomeScreen",
      type(app.current_frame).__name__)
latest = attempt_repo.list_sessions(limit=1)[0]
check("the abandoned sitting was saved", latest["status"] == "abandoned", latest["status"])
check("module 1 answers were kept", latest["attempt_count"] == 27,
      str(latest["attempt_count"]))
guard("history renders it", app.show_history)
check("history shows it as incomplete",
      "incomplete" in " ".join(ctk.texts(app.current_frame)).lower())
guard("its review still opens",
      lambda: app.show_review_from_history(latest["session_id"]))
check("partial review renders 27 records",
      len(app.current_frame.summary.records) == 27,
      str(len(app.current_frame.summary.records)))

print(f"\n{'=' * 62}\nPASSED {len(PASS)}   FAILED {len(FAIL)}")
if FAIL:
    for name, detail in FAIL:
        print(f"   FAILED: {name}   {detail}")
    sys.exit(1)
print("all green")
