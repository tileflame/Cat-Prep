"""
plan_builder.py — turn a Profile into a week-by-week and day-by-day study plan.

The hardcoded seven-week plan in study_plan.py was written for one student with
one set of test dates and one set of weaknesses. This builds the same shape of
plan for anybody, from their own score reports and their own test dates.

Three rules do most of the work, and all three came from watching a real plan
succeed and fail:

  1. A banked section gets zero minutes. Superscore keeps your best section
     forever, so once a section is finished, studying it cannot raise your
     score — it can only take time from the section that can still move.

  2. A short window before a test goes to a RULE-based domain, not the
     weakest one. Rules move in days. Comprehension takes weeks. Spending
     eight days on a domain that needs three weeks wastes the eight days.

  3. The week before a test is a taper, and the day before is off. Sleep is
     what funds attention, and attention is what the test actually measures.

    from plan_builder import build_plan
    plan = build_plan(profile, today=date.today())
    plan["days"][date(2026, 9, 8)]["tasks"]
"""

from __future__ import annotations

from datetime import date, timedelta

from config import SECTION_MATH, SECTION_RW
from user_profile import Profile
from score_report import RW, MATH, SECTION_OF

# ---------------------------------------------------------------------------
# SESSION LIBRARY
# ---------------------------------------------------------------------------
# One entry per domain: the sub-skills it breaks into, and the method that
# actually moves it. The method text is the valuable part — a drill without a
# method is just more questions.

METHOD_PREDICT = ("Cover the choices. Answer in your own words FIRST, then look. "
                  "Wrong answers are written to sound reasonable — you beat them by "
                  "already knowing what you are looking for.")

SESSIONS = {
    "Expression of Ideas": {
        "skills": ["Transitions", "Rhetorical Synthesis"],
        "opening": (
            "Write the six logical relationships out by hand: addition, contrast, "
            "cause, effect, example, sequence-emphasis. That is the entire system "
            "behind every transition question."),
        "method": (
            "Transitions: read the sentence before and the sentence after, say the "
            "relationship out loud in plain English, THEN look at the choices. "
            "Rhetorical Synthesis: read the GOAL first, before the bullets. The right "
            "answer does exactly the goal — choices that are true but do something "
            "else are the trap."),
    },
    "Craft and Structure": {
        "skills": ["Words in Context", "Text Structure and Purpose",
                   "Cross-Text Connections"],
        "opening": (
            "Words in Context is a prediction exercise, not a vocabulary quiz. Cover "
            "the choices, read the sentence and the one before it, and write your own "
            "word in the blank before you look at anything."),
        "method": (
            "Words in Context: your own word first, in writing, every time. If you "
            "cannot produce one you have not understood the sentence — reread rather "
            "than guessing from the choices. Text Structure: ask what the sentence "
            "DOES, not what it says. Answer with a verb — introduces, qualifies, "
            "contrasts, illustrates, concedes, refutes."),
    },
    "Information and Ideas": {
        "skills": ["Central Ideas and Details", "Inferences",
                   "Command of Evidence"],
        "opening": (
            "An inference the text does not force is wrong, however reasonable it "
            "sounds. That single rule is most of this domain."),
        "method": (
            "Answer in your own words before you look. For evidence questions, read "
            "what the data actually says before you read the claim — the trap is a "
            "choice that describes the graph correctly but does not support the claim."),
    },
    "Standard English Conventions": {
        "skills": ["Boundaries", "Form, Structure and Sense"],
        "opening": (
            "Write C or F — complete or fragment — for each side of the punctuation "
            "BEFORE you look at the choices. Two completes need a full stop, a "
            "semicolon, or a comma plus a conjunction."),
        "method": (
            "These are rules, not judgement. Every miss should end with a written "
            "rule you can check next time, never 'read more carefully'."),
    },
    "Algebra": {
        "skills": ["Linear equations", "Systems", "Inequalities"],
        "opening": ("Systems: infinite solutions when all three ratios match, none "
                    "when the x and y ratios match but the constant does not."),
        "method": "Underline what is being asked. Re-read the last line before answering.",
    },
    "Advanced Math": {
        "skills": ["Quadratics", "Exponentials", "Functions"],
        "opening": ("Discriminant b squared minus 4ac: positive gives two solutions, "
                    "zero gives exactly one, negative gives none."),
        "method": "Underline what is being asked. Re-read the last line before answering.",
    },
    "Problem-Solving and Data Analysis": {
        "skills": ["Ratios and rates", "Percentages", "Data interpretation"],
        "opening": "Read the axis labels and units before you read the question.",
        "method": "Underline what is being asked. Re-read the last line before answering.",
    },
    "Geometry and Trigonometry": {
        "skills": ["Angles and triangles", "Circles", "Right-triangle trig"],
        "opening": "Draw it. Label everything you know before you look for what you need.",
        "method": "Underline what is being asked. Re-read the last line before answering.",
    },
}

SECTION_CONST = {RW: SECTION_RW, MATH: SECTION_MATH}


# ---------------------------------------------------------------- task makers

def _drill(minutes, label, detail, *, section, domains=None, difficulty=None, count=20):
    return {"minutes": minutes, "label": label, "detail": detail, "action": "drill",
            "params": {"section": section, "domains": domains or [],
                       "difficulty": difficulty, "count": count}}


def _module(minutes, label, detail, *, section):
    return {"minutes": minutes, "label": label, "detail": detail, "action": "module",
            "params": {"section": section}}


def _redo(minutes, label, detail, count=5):
    return {"minutes": minutes, "label": label, "detail": detail, "action": "redo",
            "params": {"count": count}}


def _review(minutes, label, detail):
    return {"minutes": minutes, "label": label, "detail": detail, "action": "review",
            "params": {}}


def _manual(minutes, label, detail):
    return {"minutes": minutes, "label": label, "detail": detail, "action": "manual",
            "params": {}}


# --------------------------------------------------------------------- weeks

def _week_bounds(day: date) -> tuple[date, date]:
    """The Sunday-to-Saturday week containing `day`."""
    start = day - timedelta(days=(day.weekday() + 1) % 7)
    return start, start + timedelta(days=6)


def build_weeks(profile: Profile, today: date) -> list[dict]:
    """
    Weeks from today to the last upcoming test, alternating build and taper.

    A week that contains a test is a taper week. Everything else is a build
    week, and its focus is chosen for the window remaining — which is how a
    short run-in ends up on a rule-based domain instead of the weakest one.
    """
    tests = profile.upcoming_tests(today)
    if not tests:
        return []

    banked = profile.banked_sections()
    last = tests[-1]["date"]
    weeks, cursor, number = [], _week_bounds(today)[0], 0
    worked: set[str] = set()          # domains that have already had a build week

    while cursor <= last:
        end = cursor + timedelta(days=6)
        in_week = [t for t in tests if cursor <= t["date"] <= end]
        next_test = next((t for t in tests if t["date"] >= cursor), None)
        days_out = (next_test["date"] - cursor).days if next_test else None
        focus = profile.focus_for_window(days_out, already_worked=worked)

        if in_week:
            title = f"Taper into {in_week[0]['label']}"
            kind, hours = "taper", 6
            summary = ("No new material. Consolidation only, and the day before is off. "
                       "Sleep is what funds attention, and attention is what this test "
                       "actually measures.")
        else:
            kind, hours = "build", 10
            title = focus[0] if focus else "General review"
            if focus:
                worked.add(focus[0])
            summary = (f"Focus: {', '.join(focus)}." if focus else
                       "Import a score report to get a targeted plan.")

        weeks.append({
            "number": number, "kind": kind, "title": title,
            "start": cursor, "end": end, "hours": hours,
            "math_hours": 0.0 if MATH in banked else hours * 0.3,
            "rw_hours": hours * (1.0 if MATH in banked else 0.7),
            "summary": summary, "focus_domains": focus,
            "bullets": _week_bullets(profile, focus, kind, in_week, banked),
        })
        cursor, number = end + timedelta(days=1), number + 1

    return weeks


def _week_bullets(profile, focus, kind, tests_in_week, banked) -> list[str]:
    bullets = []
    if banked:
        names = " and ".join(sorted(banked))
        bullets.append(f"{names} is banked by superscore — zero minutes on it. Every "
                       f"minute there is a minute taken from the section that can "
                       f"still move.")
    for domain in focus:
        entry = SESSIONS.get(domain)
        if entry:
            bullets.append(f"{domain}: {', '.join(entry['skills'])}.")
    if kind == "taper":
        bullets.append("Friday off entirely. No new material all week.")
        for test in tests_in_week:
            bullets.append(f"{test['label']} this week.")
    else:
        bullets.append("Every session: one skill block, a written justification for "
                       "every answer, then the error log. That order is what moves a "
                       "domain — the log is not optional.")
    return bullets


# ---------------------------------------------------------------------- days

def _study_day(week: dict, day: date, profile: Profile) -> dict:
    focus = week["focus_domains"] or ["Craft and Structure"]
    primary = focus[0]
    entry = SESSIONS.get(primary, {})
    banked = profile.banked_sections()
    section_name = SECTION_OF.get(primary, RW)
    section = SECTION_CONST[section_name]

    tasks = [_redo(10, "Cold warm-up — 3 redo questions",
                   "From your due queue, from scratch, no notes. This is how learning "
                   "becomes permanent instead of temporary.")]

    if entry.get("opening") and day.weekday() in (6, 0):     # Sun / Mon of the week
        tasks.append(_manual(15, f"{primary} — the method, written down",
                             entry["opening"]))

    tasks.append(_drill(
        45, f"★ {primary} — skill block",
        (entry.get("method") or METHOD_PREDICT) +
        " Write one line of why for every answer, including the ones you are sure about.",
        section=section, domains=[primary], count=20))

    if len(focus) > 1:
        second = focus[1]
        tasks.append(_drill(
            20, f"Second block — {second}",
            SESSIONS.get(second, {}).get("method") or METHOD_PREDICT,
            section=SECTION_CONST[SECTION_OF.get(second, RW)],
            domains=[second], count=10))

    tasks.append(_manual(7, "Break", "Stand up. Move. No phone. Seven minutes."))

    module_section = SECTION_RW if MATH in banked else section
    tasks.append(_module(32, f"Timed module — {module_section}",
                         "Real conditions. Two-pass strategy. Do not finish early.",
                         section=module_section))

    tasks.append(_review(30, "Error analysis — never skip",
                         "Log every miss and every lucky guess. Tag the cause. Write "
                         "the one-sentence fix. A 45-minute session that is 20 minutes "
                         "of practice and 25 of review beats 90 minutes of pure practice."))

    total = sum(t["minutes"] for t in tasks)
    headline = primary if week["kind"] == "build" else f"{week['title']} — {primary}"
    if week["kind"] == "build" and len(focus) > 1:
        headline = f"{primary} + {focus[1]}"
    return {"headline": headline,
            "hours": f"~{round(total / 60 * 4) / 4:g} h", "tasks": tasks}


def build_days(profile: Profile, weeks: list[dict], today: date) -> dict[date, dict]:
    """One explicit plan per day, from today to the last test."""
    if not weeks:
        return {}

    tests = {t["date"]: t for t in profile.upcoming_tests(today)}
    days: dict[date, dict] = {}

    for week in weeks:
        day = max(week["start"], today)
        while day <= week["end"]:
            tomorrow_is_test = (day + timedelta(days=1)) in tests
            yesterday_was_test = (day - timedelta(days=1)) in tests

            if day in tests:
                days[day] = {
                    "headline": f"{tests[day]['label']} — test day",
                    "hours": "test day",
                    "tasks": [_manual(0, "Read your one page, then nothing else.",
                                      "Five minutes. No new questions in the car — "
                                      "cramming raises anxiety and returns nothing.")],
                }
            elif yesterday_was_test:
                days[day] = {
                    "headline": "Brain dump, then rest",
                    "hours": "~30 min",
                    "tasks": [
                        _manual(30, "Write it all down within 12 hours",
                                "Which question types felt hard, where time ran out, "
                                "whether Module 2 was easier or harder. You will never "
                                "see those questions again — this is the only record "
                                "that will ever exist."),
                        _manual(0, "Then take the rest of the day off", ""),
                    ],
                }
            elif tomorrow_is_test:
                days[day] = {
                    "headline": "OFF — pack and sleep",
                    "hours": "0",
                    "tasks": [
                        _manual(10, "Read your rule sheet once. Then close the laptop.",
                                "Ten minutes, no more."),
                        _manual(5, "Ticket, ID, calculator, chargers, snack, water",
                                "Check the route to the test centre. Actually check it."),
                        _manual(0, "Sleep the amount you normally sleep",
                                "Do not change anything tonight."),
                    ],
                }
            elif day.weekday() == 6:                       # Sunday
                days[day] = {
                    "headline": f"Week {week['number']} — Sunday re-plan",
                    "hours": "~45 min",
                    "tasks": [
                        _review(30, "Weekly diagnosis",
                                "Sort this week's log by cause. Any question type with "
                                "3+ misses becomes a drill target. Maximum three at a time."),
                        _redo(15, "Clear the due redo queue", "", count=10),
                    ],
                }
            elif day.weekday() == 5 and week["kind"] == "build":
                days[day] = {
                    "headline": "Timed practice + full review",
                    "hours": "~1.25 h",
                    "tasks": [
                        _module(32, "Timed module", "Exact conditions.",
                                section=SECTION_RW if MATH in profile.banked_sections()
                                else SECTION_CONST[SECTION_OF.get(
                                    (week["focus_domains"] or ["Craft and Structure"])[0], RW)]),
                        _review(35, "Review it — 35 minutes on 32 minutes of test",
                                "That ratio is the point. Every miss, every lucky "
                                "guess, every one you flagged and got right."),
                    ],
                }
            else:
                days[day] = _study_day(week, day, profile)

            days[day]["week"] = week
            days[day]["source"] = "generated"
            day += timedelta(days=1)

    return days


# --------------------------------------------------------------------- entry

def build_plan(profile: Profile, today: date | None = None) -> dict:
    """The whole plan: priorities, weeks, and a plan for every remaining day."""
    today = today or date.today()
    weeks = build_weeks(profile, today)
    return {
        "profile": profile.summary(),
        "priorities": profile.domain_priorities(),
        "banked": sorted(profile.banked_sections()),
        "weeks": weeks,
        "days": build_days(profile, weeks, today),
        "tests": profile.upcoming_tests(today),
        "generated_for": today,
    }


def active_plan(today: date | None = None) -> dict | None:
    """The generated plan if a profile exists, otherwise None."""
    me = Profile.load()
    if me is None or not me.test_dates:
        return None
    return build_plan(me, today)
