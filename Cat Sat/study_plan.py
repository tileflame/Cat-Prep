"""
study_plan.py — what to study, and when.

Every plan this app shows comes from one of two places:

  1. A plan GENERATED from your own score reports and test dates. This is the
     normal case. Run `python setup_profile.py`, and plan_builder turns your
     eight domain performance bands into a week-by-week and day-by-day plan.

  2. An optional hand-written plan in `personal_plan.py`. That file is not part
     of the published app and is in .gitignore — a hand-written plan belongs to
     one student and is wrong for everybody else. If it is absent, which is the
     normal case, nothing here breaks.

Everything below is either generic study principle or a lookup into one of
those two sources. No student's scores are hardcoded in this file.
"""

from __future__ import annotations

from datetime import date

from config import SECTION_MATH, SECTION_RW          # noqa: F401  (re-exported)

# ---------------------------------------------------------------------------
# THE OPTIONAL HAND-WRITTEN PLAN
# ---------------------------------------------------------------------------

try:
    import personal_plan as _personal
except Exception:                                     # absent, or broken
    _personal = None


def has_personal_plan() -> bool:
    return _personal is not None


# ---------------------------------------------------------------------------
# PRIORITY TIERS — general principle, true for any student
# ---------------------------------------------------------------------------
# Deliberately free of anyone's numbers. What belongs here is advice that holds
# regardless of who is studying; anything specific to one person's score report
# is computed at runtime from their profile instead.

TIERS = {
    1: {
        "label": "Must do — this is where the points are",
        "color_key": "GREEN",
        "items": [
            "★ Work your weakest domain, weighted by how much of the section it is. "
            "A weak domain worth 28% of the section beats an equally weak one worth 15%.",
            "★ Give a banked section zero minutes. Superscore keeps your best section "
            "forever — once it is top-band everywhere, studying it can only take time "
            "from the section that can still move.",
            "★ Predict before you read the choices. Every R&W question, every time. "
            "Wrong answers are written to sound reasonable; you beat them by already "
            "knowing what you are looking for.",
            "Keep an error log after every session, including lucky guesses, and write "
            "EXECUTABLE fixes. Never 'read more carefully' — that restates the error "
            "instead of correcting it.",
            "Underline what a Math question actually asks, and re-read the last line "
            "before you answer. Most Math misses at a high score are read-wrong, not "
            "solved-wrong.",
            "Register for every sitting you intend to take, before the deadline.",
            "Sleep, and exact test-condition simulation.",
        ],
    },
    2: {
        "label": "High value — after Tier 1 is running",
        "color_key": "BLUE",
        "items": [
            "Timed modules under real conditions. Use the whole clock; never finish early.",
            "Spaced redos of everything you got wrong, at +1, +3 and +10 days.",
            "A calculator-fluency session, then short maintenance — not more.",
            "Vocabulary built from words YOU missed, not a generic list.",
            "Full practice tests, with a review at least as long as the test.",
        ],
    },
    3: {
        "label": "Nice to have — only if Tiers 1 and 2 are genuinely done",
        "color_key": "AMBER",
        "items": [
            "Method drilling on question types you already pass.",
            "Reading dense non-fiction for general comprehension.",
            "Content checklists for topics you have not missed.",
        ],
    },
    4: {
        "label": "STOP — these actively cost you points",
        "color_key": "RED",
        "items": [
            "Studying a section that is already banked.",
            "Spending a short window before a test on a comprehension domain. "
            "Rules move in days; comprehension takes weeks.",
            "Writing 'read carefully' or 'be less careless' in your error log.",
            "Learning new material in the last week before a test.",
            "Third-party question sets. Use the official bank.",
            "Cramming on test-day morning. It raises anxiety and returns nothing.",
        ],
    },
}

# When life gets busy, cut in this order. Never cut the last one.
CUT_ORDER = ["Vocabulary", "Calculator maintenance", "Timed modules"]
NEVER_CUT = ("Never cut error review. A 45-minute session that is 20 minutes of "
             "practice and 25 minutes of review beats a 90-minute session that is "
             "all practice.")

# The one-line summary shown at the foot of the plan screen. Generic on purpose:
# it is the method this whole app is built around, not one person's takeaway.
THE_WHOLE_PLAN = (
    "Work your weakest domain, weighted by how big it is. Give a banked section "
    "zero minutes. Predict before you read the choices. Log every miss with an "
    "executable fix, and redo it at +1, +3 and +10 days. Taper before a test and "
    "sleep the night before. Everything else is detail.")

NO_PROFILE_MESSAGE = ("Run  python setup_profile.py  to build your plan from your own "
                      "score reports and test dates.")


# ---------------------------------------------------------------------------
# THE ACTIVE PLAN
# ---------------------------------------------------------------------------
# A generated plan wins. A hand-written personal_plan.py is the fallback. With
# neither, every lookup returns an empty, honest "run setup" state rather than
# showing somebody else's plan.

def _generated():
    """The user's generated plan, or None if they have not run setup yet."""
    try:
        import plan_builder
        return plan_builder.active_plan()
    except Exception:                     # a broken profile must not brick the app
        return None


def has_profile() -> bool:
    return _generated() is not None


def _empty_day(day: date) -> dict:
    return {"headline": "No plan yet", "hours": "—", "week": None, "source": "none",
            "tasks": [{"minutes": 0, "label": "Set up your plan",
                       "detail": NO_PROFILE_MESSAGE, "action": "manual", "params": {}}]}


def active_weeks() -> list[dict]:
    plan = _generated()
    if plan:
        return plan["weeks"]
    return _personal.WEEKS if _personal else []


def active_test_dates() -> list[dict]:
    plan = _generated()
    if plan:
        return [{"date": t["date"], "label": t["label"], "note": ""} for t in plan["tests"]]
    return _personal.TEST_DATES if _personal else []


def active_day_plan(day: date) -> dict:
    plan = _generated()
    if plan:
        if day in plan["days"]:
            return plan["days"][day]
        return {"headline": "Outside your planned window", "hours": "—",
                "tasks": [], "week": None, "source": "none"}
    if _personal:
        return _personal.day_plan(day)
    return _empty_day(day)


def active_week_for(day: date) -> dict | None:
    plan = _generated()
    if plan:
        for week in plan["weeks"]:
            if week["start"] <= day <= week["end"]:
                return week
        return None
    return _personal.week_for(day) if _personal else None


def active_next_test(day: date) -> dict | None:
    plan = _generated()
    if plan:
        upcoming = [t for t in plan["tests"] if t["date"] >= day]
        if not upcoming:
            return None
        first = upcoming[0]
        return {"date": first["date"], "label": first["label"], "note": ""}
    return _personal.next_test(day) if _personal else None


def active_days_until_next_test(day: date) -> int | None:
    test = active_next_test(day)
    return (test["date"] - day).days if test else None


def active_upcoming(day: date, limit: int = 4) -> list[dict]:
    plan = _generated()
    if plan:
        return [{"date": t["date"], "label": t["label"], "note": ""}
                for t in plan["tests"] if t["date"] >= day][:limit]
    return _personal.upcoming_dates(day, limit) if _personal else []


def active_other_dates() -> list[dict]:
    """Milestones are a property of a hand-written plan; generated plans have none."""
    if _generated():
        return []
    return _personal.OTHER_DATES if _personal else []


def active_priorities() -> dict:
    """Domain priorities, computed from the user's own score report bands."""
    plan = _generated()
    if plan:
        return {entry["domain"]: {
            "weight": entry["weight"], "verdict": entry["verdict"],
            "note": (f"{entry['band']['low']}-{entry['band']['high']} · "
                     f"{int(entry['share'] * 100)}% of the section"
                     if entry["band"] else "no score report yet")}
            for entry in plan["priorities"]}
    return getattr(_personal, "DOMAIN_PRIORITY", {}) if _personal else {}


def active_target() -> str | None:
    plan = _generated()
    if plan:
        target = plan["profile"].get("target")
        return str(target) if target else None
    return getattr(_personal, "TARGET_SUPERSCORE", None) if _personal else None
