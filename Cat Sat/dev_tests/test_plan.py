"""
test_plan.py — the hand-written personal plan (personal_plan.py).

That file is NOT part of the published app: it is one student's own plan and is
gitignored. This suite therefore skips itself cleanly when it is absent, which
is the normal case for anyone who downloads this repo. The generic planner that
everyone actually uses is covered by test_profile.py.
"""
import importlib.util as _ilu, os as _os, sys as _sys
_APP = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ilu.find_spec is None or not _os.path.exists(_os.path.join(_APP, "personal_plan.py")):
    print("test_plan: personal_plan.py not present — skipping (this is normal).")
    print("PASSED 0   FAILED 0")
    print("all green")
    raise SystemExit(0)

"""
Tests for the SAT Command Center integration: the 7-week plan, the diagnostic
engine, the redo ladder, and the two new screens.

Run: python3 dev_tests/test_plan.py
"""
import os, sys, traceback
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "stubs"))
sys.path.insert(0, HERE)
sys.path.insert(0, APP)
os.chdir(APP)

import make_fake_bank
make_fake_bank.sandbox_env()

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox

if not os.path.exists(os.path.join(make_fake_bank.SANDBOX, "database", "questions.db")):
    make_fake_bank.build(6)
make_fake_bank.reset_progress()

import attempt_repo, database, diagnostic, question_repo
# This suite exercises the hand-written example plan, not the generic planner.
import personal_plan as study_plan
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

# =====================================================================
print("\n[1] the seven-week calendar")
check("seven weeks encoded", len(study_plan.WEEKS) == 7, str(len(study_plan.WEEKS)))
check("plan starts Aug 16 2026", study_plan.WEEKS[0]["start"] == date(2026, 8, 16))
check("plan ends Oct 3 2026", study_plan.WEEKS[-1]["end"] == date(2026, 10, 3))

# No gaps, no overlaps across the whole window.
cursor = date(2026, 8, 16)
gaps = []
while cursor <= date(2026, 10, 3):
    matches = [w for w in study_plan.WEEKS if w["start"] <= cursor <= w["end"]]
    if len(matches) != 1:
        gaps.append((cursor.isoformat(), len(matches)))
    cursor += timedelta(days=1)
check("every day Aug 16 - Oct 3 maps to exactly one week", not gaps, str(gaps[:5]))
check("outside the window returns None", study_plan.week_for(date(2026, 11, 1)) is None)

for w in study_plan.WEEKS:
    span = (w["end"] - w["start"]).days + 1
    check(f"week {w['number']} spans 7 days", span == 7, str(span))

check("test dates are the real ones",
      [t["date"] for t in study_plan.TEST_DATES][:3] ==
      [date(2026, 8, 22), date(2026, 9, 12), date(2026, 10, 3)],
      str([t["date"] for t in study_plan.TEST_DATES]))
check("Nov 7 backstop present", study_plan.TEST_DATES[3]["date"] == date(2026, 11, 7))

# =====================================================================
print("\n[2] day resolution")
check("Aug 17 (today) resolves", study_plan.day_plan(date(2026, 8, 17))["source"] == "explicit")
aug17 = study_plan.day_plan(date(2026, 8, 17))
labels = " | ".join(t["label"] for t in aug17["tasks"])
check("Aug 17 has the timed Math module", "Timed Math module" in labels, labels)
check("Aug 17 has 20 Transitions", "Transitions" in labels, labels)
check("Aug 17 has the cold redo", "Cold redo" in labels, labels)
check("Aug 17 has vocabulary", "Vocabulary" in labels, labels)
check("Aug 17 has 6 tasks", len(aug17["tasks"]) == 6, str(len(aug17["tasks"])))

aug18 = study_plan.day_plan(date(2026, 8, 18))
check("Aug 18 is the trigonometry session",
      any("rigonometry" in t["label"] for t in aug18["tasks"]),
      " | ".join(t["label"] for t in aug18["tasks"]))
trig = next(t for t in aug18["tasks"] if "rigonometry" in t["label"])
check("trig task drills Geometry and Trigonometry, hard",
      trig["params"]["domains"] == ["Geometry and Trigonometry"]
      and trig["params"]["difficulty"] == "Hard", str(trig["params"]))

aug21 = study_plan.day_plan(date(2026, 8, 21))
check("Aug 21 is the rest day",
      all(t["action"] == "manual" for t in aug21["tasks"]),
      str([t["action"] for t in aug21["tasks"]]))
check("Aug 21 says no practice questions",
      any("NO practice" in t["label"] for t in aug21["tasks"]))

aug22 = study_plan.day_plan(date(2026, 8, 22))
check("Aug 22 is test day", "SAT #1" in aug22["headline"], aug22["headline"])

# Every day in the window must produce something renderable.
bad_days = []
cursor = date(2026, 8, 16)
while cursor <= date(2026, 10, 3):
    try:
        plan = study_plan.day_plan(cursor)
        assert isinstance(plan.get("tasks"), list)
        assert plan.get("headline")
        for task in plan["tasks"]:
            assert task["action"] in ("drill", "module", "redo", "review", "manual")
            assert isinstance(task["minutes"], int)
            assert task.get("label")
    except Exception as exc:
        bad_days.append((cursor.isoformat(), repr(exc)))
    cursor += timedelta(days=1)
check("all 49 days produce a valid plan", not bad_days, str(bad_days[:3]))

# --- the ideal-day template, exercised directly ------------------------------
# Every day from Sept 5 to Oct 3 is now explicitly planned, so the template is
# tested against a synthetic week rather than a calendar date.
banked = {"number": 4, "kind": "build", "title": "T", "hours": 10,
          "math_hours": 0.0, "rw_hours": 9.0, "summary": "",
          "focus_domains": ["Craft and Structure", "Expression of Ideas"], "bullets": []}
tpl = study_plan.ideal_day(banked, date(2026, 9, 16))
labels = " | ".join(t["label"] for t in tpl["tasks"])
check("the money block targets the week's focus domain",
      any("Craft and Structure" in t["label"] for t in tpl["tasks"]), labels)
check("a second R&W block replaces the old Math block",
      any("Expression of Ideas" in t["label"] for t in tpl["tasks"]), labels)
check("no Math task survives once Math is banked",
      not any("Math" in t["label"] or "Desmos" in t["label"] for t in tpl["tasks"]), labels)
check("timed modules are R&W only once Math is banked",
      all("Math" not in t["label"] for t in tpl["tasks"] if t["action"] == "module"), labels)
check("template still includes error analysis",
      any(t["action"] == "review" for t in tpl["tasks"]))
check("template still starts with the cold warm-up", tpl["tasks"][0]["action"] == "redo")

unbanked = dict(banked, math_hours=2.5, number=1)
check("Math returns if a week still budgets Math hours",
      any("Math" in t["label"] or "Desmos" in t["label"]
          for t in study_plan.ideal_day(unbanked, date(2026, 8, 26))["tasks"]))

# --- every day from the reset to the last test is explicitly planned ---------
cursor, missing = date(2026, 9, 5), []
while cursor <= date(2026, 10, 3):
    pl = study_plan.day_plan(cursor)
    if pl["source"] != "explicit":
        missing.append(cursor.isoformat())
    if not pl["tasks"]:
        missing.append(cursor.isoformat() + " (no tasks)")
    cursor += timedelta(days=1)
check("all 29 run-in days are explicitly planned with tasks", not missing, str(missing[:4]))

# Prose may mention Math ("Math 760 is banked") — what must not exist is a task
# that actually sends you to do Math.
math_work = [
    (d.isoformat(), t["label"])
    for d, plan in study_plan.DAYS_AFTER_SAT1.items()
    for t in plan["tasks"]
    if t["action"] in ("drill", "module")
    and t.get("params", {}).get("section") != study_plan.SECTION_RW
]
check("no run-in day schedules an actual Math drill or module",
      not math_work, str(math_work[:3]))

check("the Expression of Ideas sprint lands before Craft and Structure",
      "Expression of Ideas" in study_plan.WEEKS[3]["focus_domains"]
      and "Craft and Structure" in study_plan.WEEKS[4]["focus_domains"],
      f'W3={study_plan.WEEKS[3]["focus_domains"]} W4={study_plan.WEEKS[4]["focus_domains"]}')

check("no SPECIAL_DAYS entry is shadowed by an explicit day", not [
    d.isoformat() for d in study_plan.SPECIAL_DAYS
    if d in study_plan.DAYS_AFTER_SAT1 or d in study_plan.WEEK0_DAYS],
    "dead config: day_plan() checks the explicit tables first, so these never fire")

check("Math is recorded as banked", study_plan.MATH_IS_BANKED is True)
check("no week after the reset budgets Math hours",
      all(w["math_hours"] == 0 for w in study_plan.WEEKS if w["number"] >= 2),
      str([(w["number"], w["math_hours"]) for w in study_plan.WEEKS if w["number"] >= 2]))

sep18 = study_plan.day_plan(date(2026, 9, 18))
check("Sep 18 shouts about registering", "REGISTER" in sep18["headline"].upper(),
      sep18["headline"])
sep19 = study_plan.day_plan(date(2026, 9, 19))
check("Sep 19 is the ACT with zero prep", "ACT" in sep19["headline"], sep19["headline"])

rest = study_plan.day_plan(date(2026, 9, 13))
check("the day after a real test is a rest day",
      "rest" in rest["headline"].lower() or "OFF" in rest["headline"],
      rest["headline"])
aug30 = study_plan.day_plan(date(2026, 8, 30))
check("a Sunday with no explicit plan falls back to the weekly re-plan",
      "re-plan" in aug30["headline"], aug30["headline"])
sep6 = study_plan.day_plan(date(2026, 9, 6))
check("Sept 6 starts the Transitions sprint", "Transitions" in sep6["headline"],
      sep6["headline"])

# =====================================================================
print("\n[3] countdown + upcoming dates")
check("days to next test from Aug 17 is 5",
      study_plan.days_until_next_test(date(2026, 8, 17)) == 5,
      str(study_plan.days_until_next_test(date(2026, 8, 17))))
check("on test day the countdown is 0",
      study_plan.days_until_next_test(date(2026, 8, 22)) == 0)
check("after Aug 22 it points at Sep 12",
      study_plan.next_test(date(2026, 8, 23))["date"] == date(2026, 9, 12))
check("after the last date it returns None",
      study_plan.next_test(date(2026, 12, 1)) is None)
upcoming = study_plan.upcoming_dates(date(2026, 9, 1), limit=4)
check("upcoming dates are sorted ascending",
      [u["date"] for u in upcoming] == sorted(u["date"] for u in upcoming), str(upcoming))
check("Sept 4 score release is surfaced",
      any(u["date"] == date(2026, 9, 4) for u in upcoming), str([u["label"] for u in upcoming]))

# =====================================================================
print("\n[4] the diagnosis table (Section 07)")
d = diagnostic.diagnose({"C": 5, "M": 3, "K": 1, "T": 1}, 10)
check("process-dominant gives the process verdict",
      d and d[0]["verdict"].startswith("Process"), str(d))
check("process verdict says DON'T study content",
      "not study new content" in d[0]["dont"].lower(), d[0]["dont"])

d = diagnostic.diagnose({"X": 4, "P": 3, "V": 2, "C": 1}, 10)
check("method-dominant gives the method verdict",
      any(f["verdict"].startswith("Method") for f in d), str([f["verdict"] for f in d]))
check("method verdict prescribes predict-first",
      any("predict-first" in f["do"] for f in d), str(d))

d = diagnostic.diagnose({"T": 4, "C": 3, "K": 3}, 10)
check("pacing shows when T > 20%",
      any(f["verdict"] == "Pacing" for f in d), str([f["verdict"] for f in d]))
check("pacing warns content study makes it worse",
      any("WORSE" in f["dont"] for f in d), str(d))

d = diagnostic.diagnose({"K": 6, "G": 2, "C": 1}, 9)
check("content-dominant gives the content verdict",
      any(f["verdict"] == "Content" for f in d), str([f["verdict"] for f in d]))

d = diagnostic.diagnose({"C": 1}, 1, {"Craft and Structure": 4})
check("4 lucky flags in one type flags fragile knowledge",
      any(f["verdict"] == "Fragile knowledge" for f in d), str(d))
check("2 lucky flags do NOT trigger it",
      not any(f["verdict"] == "Fragile knowledge"
              for f in diagnostic.diagnose({"C": 1}, 1, {"X": 2})))
check("no data -> no findings", diagnostic.diagnose({}, 0) == [])
check("findings are severity-sorted",
      [f["severity"] for f in diagnostic.diagnose({"C": 5, "M": 3, "T": 3}, 11)] ==
      sorted([f["severity"] for f in diagnostic.diagnose({"C": 5, "M": 3, "T": 3}, 11)],
             reverse=True))

check("11 root causes", len(diagnostic.ROOT_CAUSES) == 11, str(len(diagnostic.ROOT_CAUSES)))
check("codes are unique", len(set(diagnostic.CAUSE_CODES)) == 11)

# =====================================================================
print("\n[5] drill targets — the two standing rules")
check("3 misses makes it a target",
      diagnostic.suggest_targets({"Craft and Structure": 3, "Algebra": 1}, []) ==
      ["Craft and Structure"])
check("2 misses does not",
      diagnostic.suggest_targets({"Craft and Structure": 2}, []) == [])
check("max three active targets",
      diagnostic.suggest_targets({"A": 5, "B": 4, "C": 3, "D": 3}, []) == ["A", "B", "C"])
check("existing targets take up room",
      diagnostic.suggest_targets({"A": 5, "B": 4}, ["X", "Y"]) == ["A"])
check("full board suggests nothing",
      diagnostic.suggest_targets({"A": 5}, ["X", "Y", "Z"]) == [])

ok, msg = diagnostic.can_retire(95.0, 15)
check("95% on 15 fresh retires", ok, msg)
ok, msg = diagnostic.can_retire(95.0, 9)
check("95% on only 9 does NOT retire", not ok and "Need 15" in msg, msg)
ok, msg = diagnostic.can_retire(87.0, 20)
check("87% does not retire however big the sample", not ok, msg)

# =====================================================================
print("\n[6] the redo ladder (+3 / +10)")
today = date(2026, 8, 17)
check("stage 0 is due tomorrow",
      diagnostic.due_date_for(0, today) == date(2026, 8, 18))
check("stage 1 is +3 days", diagnostic.due_date_for(1, today) == date(2026, 8, 20))
check("stage 2 is +10 days", diagnostic.due_date_for(2, today) == date(2026, 8, 27))
check("correct advances the ladder", diagnostic.next_stage(0, True) == 1)
check("correct at the top graduates", diagnostic.next_stage(2, True) is None)
check("a miss resets to the bottom", diagnostic.next_stage(2, False) == 0)
check("a miss at stage 0 stays at 0", diagnostic.next_stage(0, False) == 0)
check("wrong answers get scheduled", diagnostic.should_schedule(False, "sure"))
check("lucky guesses get scheduled", diagnostic.should_schedule(True, "shaky"))
check("confident correct answers do NOT", not diagnostic.should_schedule(True, "sure"))

# =====================================================================
print("\n[7] confidence + lucky guesses persist")
attempt_repo.clear_history(keep_notes=False)
qs = question_repo.fetch(section="Reading and Writing", limit=6)
sid = attempt_repo.create_session("drill", "Reading and Writing", "conf test", {})
records = [
    AttemptRecord(question=qs[0], selected_answer=qs[0].correct_answer, is_correct=True,
                  confidence="sure", position=1),
    AttemptRecord(question=qs[1], selected_answer=qs[1].correct_answer, is_correct=True,
                  confidence="shaky", position=2),            # lucky
    AttemptRecord(question=qs[2], selected_answer="Z", is_correct=False,
                  confidence="shaky", position=3),
    AttemptRecord(question=qs[3], selected_answer="Z", is_correct=False,
                  confidence="sure", position=4),
]
attempt_repo.record_attempts(sid, None, records)
check("attempt ids stamped onto records",
      all(r.attempt_id for r in records), str([r.attempt_id for r in records]))
check("is_lucky property works", records[1].is_lucky and not records[0].is_lucky)
check("needs_logging catches wrong + lucky",
      [r.needs_logging for r in records] == [False, True, True, True],
      str([r.needs_logging for r in records]))

logged = attempt_repo.logged_attempts(sid)
check("error log holds 3 rows (2 wrong + 1 lucky)", len(logged) == 3, str(len(logged)))
check("the confident-correct answer is NOT logged",
      qs[0].question_id not in [r["question_id"] for r in logged])
totals = attempt_repo.log_totals()
check("log_totals splits wrong vs lucky",
      totals["wrong"] == 2 and totals["lucky"] == 1, str(totals))
lucky = attempt_repo.lucky_counts(group_by="domain")
check("lucky guesses grouped by domain", sum(lucky.values()) == 1, str(lucky))

# =====================================================================
print("\n[8] tagging + auto-scheduled redos")
check("everything starts untagged", attempt_repo.untagged_count(sid) == 3,
      str(attempt_repo.untagged_count(sid)))
attempt_repo.tag_attempt(records[2].attempt_id, root_cause="X",
                         fix_note="Name the constraint word before eliminating.")
check("tag persisted",
      attempt_repo.cause_counts(session_id=sid).get("X") == 1,
      str(attempt_repo.cause_counts(session_id=sid)))
check("untagged count drops", attempt_repo.untagged_count(sid) == 2)

attempt_repo.schedule_redo(qs[2].question_id, stage=0,
                           due_on=date(2026, 8, 18).isoformat(),
                           section="Reading and Writing", domain="Craft and Structure")
due = attempt_repo.due_redos("2026-08-18")
check("redo is due on the scheduled day", len(due) == 1, str(len(due)))
check("redo is not due the day before", attempt_repo.due_redos("2026-08-17") == [])
counts = attempt_repo.redo_counts("2026-08-17")
check("redo_counts sees it as upcoming",
      counts["due"] == 0 and counts["upcoming"] == 1, str(counts))

# Getting it right advances to +3.
attempt_repo.complete_redo(qs[2].question_id, was_correct=True, next_stage_value=1,
                           next_due=date(2026, 8, 21).isoformat())
check("completed redo leaves the due list", attempt_repo.due_redos("2026-08-18") == [])
check("next rung is queued for +3 days",
      len(attempt_repo.due_redos("2026-08-21")) == 1,
      str(attempt_repo.due_redos("2026-08-21")))
# Getting it wrong resets.
attempt_repo.complete_redo(qs[2].question_id, was_correct=False, next_stage_value=0,
                           next_due=date(2026, 8, 22).isoformat())
reset = attempt_repo.due_redos("2026-08-22")
check("a miss requeues at stage 0", reset and reset[0]["stage"] == 0, str(reset))
check("scheduling twice does not duplicate",
      len(attempt_repo.due_redos("2026-12-31")) == 1,
      str(len(attempt_repo.due_redos("2026-12-31"))))

# =====================================================================
print("\n[9] targets + plan checklist persistence")
attempt_repo.add_target("Craft and Structure")
check("target added", len(attempt_repo.active_targets()) == 1)
check("adding the same target twice is a no-op",
      attempt_repo.add_target("Craft and Structure") is None
      and len(attempt_repo.active_targets()) == 1)
prog = attempt_repo.target_progress("Craft and Structure")
check("target progress reads the log", isinstance(prog["accuracy"], float), str(prog))
tid = attempt_repo.active_targets()[0]["target_id"]
attempt_repo.retire_target(tid, 93.0)
check("retired target leaves the active list", attempt_repo.active_targets() == [])

attempt_repo.set_plan_task("2026-08-17", "t0", True)
attempt_repo.set_plan_task("2026-08-17", "t1", True)
check("plan ticks persist", attempt_repo.plan_done_keys("2026-08-17") == {"t0", "t1"},
      str(attempt_repo.plan_done_keys("2026-08-17")))
attempt_repo.set_plan_task("2026-08-17", "t1", False)
check("un-ticking works", attempt_repo.plan_done_keys("2026-08-17") == {"t0"})
attempt_repo.set_plan_task("2026-08-16", "t0", True)
check("streak counts consecutive days",
      attempt_repo.plan_streak("2026-08-17") == 2,
      str(attempt_repo.plan_streak("2026-08-17")))
attempt_repo.set_plan_task("2026-08-14", "t0", True)
check("a gap breaks the streak",
      attempt_repo.plan_streak("2026-08-17") == 2,
      str(attempt_repo.plan_streak("2026-08-17")))

# =====================================================================
print("\n[10] the screens render and work")
from main import SATApp
messagebox.ANSWER = True
app = guard("app boots", lambda: SATApp())

guard("open Today's Plan", lambda: app.show_plan(date(2026, 8, 17)))
check("plan screen shown", type(app.current_frame).__name__ == "PlanScreen",
      type(app.current_frame).__name__)
pt = " ".join(ctk.texts(app.current_frame))
check("plan shows the countdown to SAT #1", "until sat #1" in pt.lower(), pt[:300])
check("plan shows 5 days", "5 days" in pt, pt[:300])
check("plan names the week", "Bank the Math" in pt, pt[:400])
check("plan lists today's tasks", "Timed Math module" in pt, pt[:600])
check("plan shows the Tier 4 STOP list", "STOP" in pt, pt[:800])
check("plan warns never to cut error review", "Never cut error review" in pt)

start = ctk.buttons(app.current_frame, "Drill")
check("plan has launchable drill buttons", len(start) >= 1, str(len(start)))
guard("launch a drill from the plan", lambda: start[0].invoke())
check("drill launched from the plan", type(app.current_frame).__name__ == "QuizScreen",
      type(app.current_frame).__name__)
q = app.current_frame
check("plan-launched drill is pre-filtered to the task's domain",
      len({x.domain for x in q.questions}) == 1, str({x.domain for x in q.questions}))

# confidence marking through the UI
guard("mark not-sure", lambda: (q.load_question(0), q.toggle_shaky()))
check("shaky registered", 0 in q.shaky, str(q.shaky))
guard("unmark", q.toggle_shaky)
check("shaky cleared", 0 not in q.shaky)
q.load_question(0); q.toggle_shaky()
for i in range(len(q.questions)):
    q.load_question(i)
    q.select(q.questions[i].correct_answer if i % 2 == 0 else "Z")
guard("submit the drill", q.confirm_submit)
check("landed on review", type(app.current_frame).__name__ == "ReviewScreen",
      type(app.current_frame).__name__)
rv = app.current_frame
check("review offers to log the questions",
      len(ctk.buttons(rv, "Log these")) == 1,
      str([b.cget("text") for b in ctk.buttons(rv)]))

guard("open the error log", lambda: ctk.buttons(rv, "Log these")[0].invoke())
check("error log screen shown", type(app.current_frame).__name__ == "ErrorLogScreen",
      type(app.current_frame).__name__)
el = app.current_frame
et = " ".join(ctk.texts(el))
check("error log shows the diagnosis panel", "This week's diagnosis" in et, et[:300])
check("error log asks WHY", "WHY DID YOU MISS IT" in et, et[:600])
check("error log asks for one sentence", "ONE SENTENCE" in et, et[:800])
check("error log offers all 11 causes",
      all(f"{c}  " in et for c in ("K", "M", "C", "T", "V", "P", "X", "D", "A", "G", "L")),
      et[:900])
check("lucky guesses appear in the log", "LUCKY" in et, et[:600])

rows = list(el.rows_by_attempt.items())
check("error log built rows", len(rows) >= 1, str(len(rows)))
aid, entry = rows[0]
guard("tag a cause", lambda: el._set_cause(aid, "X"))
check("cause saved", attempt_repo.cause_counts(since_days=30).get("X", 0) >= 1,
      str(attempt_repo.cause_counts(since_days=30)))

# The banned-phrase guard
entry["var"].set("read carefully")
guard("reject 'read carefully'", lambda: el._save_fix(aid, entry["var"], entry["status"],
                                                      entry["row"]))
check("'read carefully' is rejected as a non-instruction",
      "not an instruction" in entry["status"].cget("text"),
      entry["status"].cget("text"))
entry["var"].set("Check both sides are independent clauses before choosing a semicolon.")
guard("accept a real rule", lambda: el._save_fix(aid, entry["var"], entry["status"],
                                                 entry["row"]))
check("a real executable rule saves", "saved" in entry["status"].cget("text"),
      entry["status"].cget("text"))
check("tagging scheduled a redo",
      attempt_repo.redo_counts("2099-01-01")["due"] >= 1,
      str(attempt_repo.redo_counts("2099-01-01")))

print("\n[11] the redo session flow")
guard("open the plan again", lambda: app.show_plan(date.today()))
started = guard("start a redo session", lambda: app.start_redo_session())
check("redo falls back to recent misses when nothing is due yet",
      started is True and type(app.current_frame).__name__ == "QuizScreen",
      type(app.current_frame).__name__)
if type(app.current_frame).__name__ == "QuizScreen":
    rq = app.current_frame
    check("redo session marks its attempts as redos", rq.is_redo_session is True)
    for i in range(len(rq.questions)):
        rq.load_question(i)
        rq.select(rq.questions[i].correct_answer)
    before = attempt_repo.redo_counts("2099-01-01")["due"]
    guard("submit the redo", rq.confirm_submit)
    after = attempt_repo.redo_counts("2099-01-01")
    check("correct redo advanced up the ladder rather than staying due",
          after["due"] <= before, f"{before} -> {after}")
else:
    check("redo session opened a quiz", False, type(app.current_frame).__name__)

print("\n[12] layout invariants on the new screens")
def layout_ok(frame):
    kids = [c for c in getattr(frame, "_children", [])
            if getattr(c, "_geometry_manager", None) == "pack"]
    exp = [c for c in kids if c._pack_kw.get("expand")]
    bot = [c for c in kids if c._pack_kw.get("side") == "bottom"]
    return not [1 for b in bot for e in exp if e._pack_order < b._pack_order]

app.show_plan(date(2026, 8, 17))
check("plan screen layout invariant holds", layout_ok(app.current_frame))
app.show_error_log()
check("error log layout invariant holds", layout_ok(app.current_frame))

print("\n[13] plan screen survives every day in the window")
broken = []
cursor = date(2026, 8, 16)
while cursor <= date(2026, 10, 3):
    try:
        app.show_plan(cursor)
        assert type(app.current_frame).__name__ == "PlanScreen"
    except Exception as exc:
        broken.append((cursor.isoformat(), repr(exc)))
    cursor += timedelta(days=1)
check("all 49 days render without error", not broken, str(broken[:3]))
guard("a date outside the plan still renders", lambda: app.show_plan(date(2026, 12, 25)))
check("outside-window day shows a friendly message",
      "Outside the seven-week plan" in " ".join(ctk.texts(app.current_frame)))

print(f"\n{'=' * 62}\nPASSED {len(PASS)}   FAILED {len(FAIL)}")
if FAIL:
    for name, detail in FAIL:
        print(f"   FAILED: {name}   {detail}")
    sys.exit(1)
print("all green")
