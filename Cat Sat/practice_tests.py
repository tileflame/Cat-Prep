"""
practice_tests.py, numbered full-length tests: Practice Test 1, 2, 3...

The Test tab used to assemble a brand new test every time you pressed start.
That is useful, and it is not what Bluebook gives you. Bluebook gives you
Practice Test 4, which is the same test tomorrow as it is today, which you can
retake, compare against, and talk about with somebody else who took it.

So these are built once from your question bank and then kept. Each one is:

  * shaped like a real Bluebook test, down to the SKILL: four vocabulary
    questions, one Cross-Text Connections, three Rhetorical Synthesis and so
    on in every Reading and Writing module, with the difficulty mix exact;
  * ordered like one: vocabulary first, Rhetorical Synthesis last, easiest to
    hardest inside each group, and Math easiest to hardest with its domains
    mixed together;
  * adaptive like one: every test stores its Module 1 AND both Module 2 forms,
    the harder and the easier, and which one you sit depends on Module 1;
  * disjoint from every other numbered test. No question appears in two of
    them, so Practice Test 7 cannot quietly repeat Practice Test 2.

"As many as the bank allows" is taken literally. Tests keep being built until
the next one could no longer be Bluebook-shaped, and the build says why it
stopped. A test that had to substitute its way to 27 questions is not a
practice test, it is a drill with a timer, so the bar is checked on every one.

Adding questions later (another import) and building again appends new tests
after the existing ones. It never reshuffles a test you already have.
"""
from __future__ import annotations

import json
import random

import adaptive_engine as engine
import attempt_repo
import question_repo
from config import (BLUEPRINT, SECTION_MATH, SECTION_RW, TIER_BASELINE,
                    TIER_EASY, TIER_HARD)
from database import progress_conn

#: Sections in the order a full test is sat, which is the order Bluebook uses.
SECTIONS = (SECTION_RW, SECTION_MATH)

#: (stored key, module number, tier) for the three forms each section keeps.
MODULE_FORMS = (
    ("m1", 1, TIER_BASELINE),
    ("m2-easy", 2, TIER_EASY),
    ("m2-hard", 2, TIER_HARD),
)

#: The bar every numbered test has to clear to be called a practice test.
#: A skill gap is a question of one type standing in for another (a Words in
#: Context question in a Cross-Text Connections slot). Six across a whole test
#: is about one per module. Drift is how many questions in one module sit at
#: the wrong difficulty for its form.
MAX_SKILL_GAPS_PER_TEST = 6
MAX_DIFFICULTY_DRIFT_PER_MODULE = 3

#: A valve, not a target. A full bank makes about twenty.
MAX_TESTS = 80


def label_for(number: int) -> str:
    return f"Practice Test {number}"


# ---------------------------------------------------------------------------
# READING WHAT IS STORED
# ---------------------------------------------------------------------------

def _rows(sql: str, params=()) -> list:
    with progress_conn() as conn:
        return conn.execute(sql, params).fetchall()


def stored_numbers() -> list[int]:
    return [r[0] for r in _rows(
        "SELECT DISTINCT test_number FROM practice_tests ORDER BY test_number")]


def used_question_ids() -> set[str]:
    return {r[0] for r in _rows("SELECT DISTINCT question_id FROM practice_tests")}


def modules_for(number: int) -> dict[tuple[str, int, str], list]:
    """
    {(section, module number, tier): [Question, ...]} for one stored test,
    in the order they were built, which is the Bluebook order.

    A question that has since left the bank (a re-import without it) is
    dropped rather than served as a blank. The module comes back shorter,
    and the caller decides whether that is still worth sitting.
    """
    rows = _rows(
        "SELECT section, module_key, position, question_id FROM practice_tests "
        "WHERE test_number = ? ORDER BY section, module_key, position", (int(number),))
    if not rows:
        return {}
    bank = question_repo.fetch_by_ids([r[3] for r in rows])
    forms = {key: (num, tier) for key, num, tier in MODULE_FORMS}
    out: dict[tuple[str, int, str], list] = {}
    for section, key, _position, qid in rows:
        question = bank.get(qid)
        if question is None or key not in forms:
            continue
        module_number, tier = forms[key]
        bucket = out.setdefault((section, module_number, tier), [])
        question.module_number = module_number
        question.tier = tier
        question.position = len(bucket) + 1
        bucket.append(question)
    return out


def list_tests() -> list[dict]:
    """Everything the Test tab needs to draw the list, in one pass."""
    numbers = stored_numbers()
    if not numbers:
        return []

    sizes = {}
    for number, section, key, n in _rows(
            "SELECT test_number, section, module_key, COUNT(*) FROM practice_tests "
            "GROUP BY test_number, section, module_key"):
        sizes.setdefault(number, {}).setdefault(section, {})[key] = n

    # How much of each test you have already met elsewhere, in a drill or in
    # check mode. A practice test is worth most unseen, so this is shown
    # before you start one rather than discovered halfway through.
    seen = {number: n for number, n in _rows(
        "SELECT p.test_number, COUNT(DISTINCT p.question_id) FROM practice_tests p "
        "WHERE p.question_id IN (SELECT DISTINCT question_id FROM attempts) "
        "GROUP BY p.test_number")}
    totals = {number: n for number, n in _rows(
        "SELECT test_number, COUNT(*) FROM practice_tests GROUP BY test_number")}
    meta = {number: (gaps, drift) for number, gaps, drift in _rows(
        "SELECT test_number, skill_gaps, difficulty_drift FROM practice_test_meta")}

    sittings: dict[int, list[dict]] = {}
    for sid, label, status, started, score, config in _rows(
            "SELECT session_id, label, status, started_at, estimated_score, config_json "
            "FROM sessions WHERE label LIKE 'Practice Test %' ORDER BY started_at DESC"):
        try:
            number = int((json.loads(config or "{}") or {}).get("practiceTest") or 0)
        except (ValueError, TypeError):
            number = 0
        if not number:
            try:
                number = int(str(label).rsplit(" ", 1)[-1])
            except ValueError:
                continue
        sittings.setdefault(number, []).append({
            "sessionId": sid, "status": status, "startedAt": started,
            "score": score})

    out = []
    for number in numbers:
        gaps, drift = meta.get(number, (0, 0))
        taken = sittings.get(number, [])
        completed = [s for s in taken if s["status"] == "completed"]
        out.append({
            "number": number,
            "label": label_for(number),
            "sections": {section: sizes.get(number, {}).get(section, {})
                         for section in SECTIONS},
            "questions": totals.get(number, 0),
            "seen": seen.get(number, 0),
            "skillGaps": gaps,
            "difficultyDrift": drift,
            "sittings": taken,
            "best": max((s["score"] or 0 for s in completed), default=None) or None,
            "lastTaken": taken[0]["startedAt"] if taken else None,
        })
    return out


# ---------------------------------------------------------------------------
# BUILDING
# ---------------------------------------------------------------------------

def _drift(plan) -> int:
    """How many questions in a module sit at a difficulty its form did not ask for."""
    target = plan.difficulty_target or {}
    actual = plan.difficulty_actual or {}
    return sum(abs(actual.get(d, 0) - target.get(d, 0)) for d in target) // 2


def _judge(plans) -> tuple[bool, str, int, int]:
    """Is this set of modules a practice test? (ok, why not, skill gaps, worst drift)"""
    gaps = sum(sum(p.skill_gaps.values()) for p in plans)
    drift = max((_drift(p) for p in plans), default=0)
    for plan in plans:
        form = "Module 1" if plan.module_number == 1 else f"the {plan.tier} Module 2"
        if plan.size < plan.target_count:
            return False, (f"{plan.section} ran out of questions for {form} "
                           f"({plan.size} of {plan.target_count})"), gaps, drift
        if plan.domain_gaps:
            short = ", ".join(sorted(plan.domain_gaps))
            return False, f"{plan.section} has no more {short} questions left", gaps, drift
    if gaps > MAX_SKILL_GAPS_PER_TEST:
        return False, ("the bank has run short of some question types, so the next "
                       "test would be missing them"), gaps, drift
    if drift > MAX_DIFFICULTY_DRIFT_PER_MODULE:
        return False, ("the bank has run short of Hard (or Easy) questions, so the "
                       "next test could not have Bluebook's difficulty mix"), gaps, drift
    return True, "", gaps, drift


def build_one(number: int, exclude: set[str], seen: dict[str, int]):
    """
    Assemble the six modules of one test without saving anything.

    Returns (modules, verdict). modules maps (section, stored key) to a
    ModulePlan. The seed is the test number, so the same bank and history give
    the same Practice Test 3 every time; the freshness data still steers each
    test toward questions you have not answered yet.
    """
    rng = random.Random(f"catprep-practice-test-{number}")
    taken = set(exclude)
    modules = {}
    for section in SECTIONS:
        if section not in BLUEPRINT:
            continue
        for key, module_number, tier in MODULE_FORMS:
            plan = engine.build_module(section, module_number, tier,
                                       exclude_ids=taken, seen_counts=seen, rng=rng)
            taken.update(q.question_id for q in plan.questions)
            modules[(section, key)] = plan
    return modules, _judge(list(modules.values()))


def _save(number: int, modules: dict, gaps: int, drift: int) -> None:
    with progress_conn() as conn:
        conn.executemany(
            "INSERT INTO practice_tests (test_number, section, module_key, position, "
            "question_id) VALUES (?, ?, ?, ?, ?)",
            [(number, section, key, index, q.question_id)
             for (section, key), plan in modules.items()
             for index, q in enumerate(plan.questions, start=1)])
        conn.execute(
            "INSERT OR REPLACE INTO practice_test_meta (test_number, skill_gaps, "
            "difficulty_drift) VALUES (?, ?, ?)", (number, gaps, drift))


def build_more(max_new: int | None = None) -> dict:
    """
    Append as many new numbered tests as the unused part of the bank allows.

    Existing tests are never touched. Returns how many were added, how many
    there are now, and in plain words why it stopped.
    """
    existing = stored_numbers()
    used = used_question_ids()
    seen = attempt_repo.seen_counts()
    number = (max(existing) if existing else 0) + 1
    limit = MAX_TESTS - len(existing)
    if max_new is not None:
        limit = min(limit, max_new)

    built, reason = 0, ""
    while built < limit:
        modules, (ok, why, gaps, drift) = build_one(number, used, seen)
        if not ok:
            reason = why
            break
        _save(number, modules, gaps, drift)
        for plan in modules.values():
            used.update(q.question_id for q in plan.questions)
        built += 1
        number += 1
    else:
        reason = "that is as many as it builds in one go" if built else ""

    return {"built": built, "total": len(existing) + built, "stoppedBecause": reason}


def delete_all() -> None:
    """Forget every numbered test. Past sittings stay in History."""
    with progress_conn() as conn:
        conn.execute("DELETE FROM practice_tests")
        conn.execute("DELETE FROM practice_test_meta")
