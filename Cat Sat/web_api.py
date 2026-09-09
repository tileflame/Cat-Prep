"""
web_api.py — every endpoint's logic, as plain dicts.

Deliberately free of HTTP: server.py does the plumbing, this does the work. That
means the whole API is unit-testable without a socket, and the existing engine
(adaptive_engine, study_plan, diagnostic, the repos) is reused unchanged — none
of that code knew about CustomTkinter and none of it knows about the web either.

State: exactly one TestRunner at a time, held in memory. This is a single-user
local app, so a module-level controller is the right amount of machinery.
"""

from __future__ import annotations

import random
import time
from datetime import date, timedelta

import adaptive_engine as engine
import attempt_repo
import diagnostic
import question_repo
import study_plan
import test_flow
from config import (
    BLUEPRINT,
    DEFAULT_ROUTING_THRESHOLD,
    PACING_TARGET_SECONDS,
    SECTION_MATH,
    SECTION_RW,
    TIER_COLOR,
    TIER_LABEL,
)
from database import get_setting, question_bank_is_usable, set_setting
from models import AttemptRecord
from test_flow import MODE_DRILL, MODE_FULL, MODE_REVIEW, MODE_SECTION, TestRunner

DIFFICULTY_RANK = {"Easy": 0, "Medium": 1, "Hard": 2}
BANNED_PHRASES = ("read carefully", "read more carefully", "be careful",
                  "pay attention", "slow down", "focus")


# ---------------------------------------------------------------------------
# SERIALISATION
# ---------------------------------------------------------------------------

def question_json(q, *, include_answer: bool = False) -> dict:
    """
    A question, ready for the browser.

    The correct answer is withheld during a sitting — grading happens on the
    server so grid-in equivalence (1/2 == 0.5 == .5) uses the same tested code
    the desktop app used.
    """
    data = {
        "id": q.question_id,
        "section": q.section,
        "domain": q.domain,
        "skill": q.skill,
        "difficulty": q.difficulty,
        "image": f"/img/{q.question_id}" if q.image_path else None,
        "rationale": (f"/img/{q.question_id}?kind=rationale" if q.has_rationale else None),
        "openEnded": bool(q.is_open_ended),
        "position": q.position,
        "module": q.module_number,
        "tier": q.tier,
    }
    if include_answer:
        data["correctAnswer"] = q.correct_answer
    return data


def plan_json(plan) -> dict:
    """A ModulePlan, ready to sit."""
    return {
        "section": plan.section,
        "moduleNumber": plan.module_number,
        "tier": plan.tier,
        "tierLabel": TIER_LABEL.get(plan.tier, plan.tier),
        "timeLimit": plan.time_limit_seconds,
        "targetCount": plan.target_count,
        "fidelity": round(plan.fidelity, 3),
        "difficultyActual": plan.difficulty_actual,
        "difficultyTarget": plan.difficulty_target,
        "gaps": plan.domain_gaps,
        "questions": [question_json(q) for q in plan.questions],
    }


def summary_json(summary) -> dict:
    """A finished sitting, for the review screen."""
    sections = []
    for outcome in summary.sections:
        sections.append({
            "section": outcome.section,
            "total": outcome.total,
            "correct": outcome.correct,
            "accuracy": round(outcome.accuracy, 1),
            "finalTier": outcome.final_tier,
            "tierLabel": TIER_LABEL.get(outcome.final_tier, outcome.final_tier),
            "tierColor": TIER_COLOR.get(outcome.final_tier, "#3B82F6"),
            "routingNote": outcome.routing_note,
            "estimatedScore": outcome.estimated_score,
            "scoreBand": engine.score_band(outcome.estimated_score),
            "modules": [{
                "moduleNumber": m.module_number,
                "tier": m.tier,
                "tierLabel": TIER_LABEL.get(m.tier, m.tier),
                "total": m.total,
                "correct": m.correct,
                "rawAccuracy": round(m.raw_accuracy * 100, 1),
                "weightedAccuracy": round(m.weighted_accuracy * 100, 1),
                "timeUsed": m.time_used_seconds,
            } for m in outcome.modules],
        })

    records = []
    for record in summary.records:
        q = record.question
        target = PACING_TARGET_SECONDS.get(q.section, 80)
        records.append({
            **question_json(q, include_answer=True),
            "selected": record.selected_answer,
            "isCorrect": record.is_correct,
            "flagged": record.was_flagged,
            "shaky": record.confidence == "shaky",
            "lucky": record.is_lucky,
            "skipped": record.skipped,
            "eliminated": sorted(record.eliminated),
            "seconds": round(record.time_spent_seconds, 1),
            "slow": bool(record.time_spent_ms
                         and record.time_spent_seconds > target * 1.6),
            "needsLogging": record.needs_logging,
        })

    return {
        "sessionId": summary.session_id,
        "mode": summary.mode,
        "label": summary.label,
        "total": summary.total,
        "correct": summary.correct,
        "accuracy": round(summary.accuracy, 1),
        "duration": summary.duration_seconds,
        "isAdaptive": summary.is_adaptive,
        "sectionScores": summary.section_scores,
        "estimatedTotal": summary.estimated_total,
        "sections": sections,
        "records": records,
        "breakdowns": _breakdowns(summary.records),
        "pacing": _pacing(summary.records),
    }


def _breakdowns(records) -> dict:
    """domain / skill / difficulty accuracy, computed once in Python."""
    out = {}
    for axis in ("domain", "skill", "difficulty"):
        totals: dict[str, list[int]] = {}
        for record in records:
            key = getattr(record.question, axis, "") or "Unclassified"
            entry = totals.setdefault(key, [0, 0])
            entry[0] += 1
            entry[1] += 1 if record.is_correct else 0
        buckets = [{"bucket": k, "total": t, "correct": c,
                    "accuracy": round(c / t * 100, 1) if t else 0.0}
                   for k, (t, c) in totals.items()]
        buckets.sort(key=lambda b: (b["accuracy"], -b["total"]))
        out[axis] = buckets
    return out


def _pacing(records) -> dict:
    timed = [r for r in records if r.time_spent_ms > 0]
    if not timed:
        return {"count": 0}
    correct = [r.time_spent_seconds for r in timed if r.is_correct]
    wrong = [r.time_spent_seconds for r in timed if not r.is_correct]
    mean = lambda xs: round(sum(xs) / len(xs), 1) if xs else 0.0  # noqa: E731
    section = records[0].question.section if records else ""
    return {
        "count": len(timed),
        "average": mean([r.time_spent_seconds for r in timed]),
        "onCorrect": mean(correct),
        "onWrong": mean(wrong),
        "target": PACING_TARGET_SECONDS.get(section),
        "slowCount": sum(1 for r in timed
                         if r.time_spent_seconds
                         > PACING_TARGET_SECONDS.get(r.question.section, 80) * 1.6),
    }


# ---------------------------------------------------------------------------
# CONTROLLER
# ---------------------------------------------------------------------------

class Api:
    """Holds the sitting in progress. One instance per running server."""

    def __init__(self):
        self.runner: TestRunner | None = None
        self.current_plan = None
        self.rng = random.Random()
        self._redo_stages: dict[str, int] = {}
        self._is_redo = False
        self._image_paths: dict[str, tuple] = {}
        self._pending_break = None

    # ------------------------------------------------------------- bootstrap

    def bootstrap(self) -> dict:
        ok, message = question_bank_is_usable()
        today = date.today()
        week = study_plan.active_week_for(today)
        stats = attempt_repo.overall_stats()
        return {
            "bankOk": ok,
            "bankMessage": message,
            "bank": question_repo.bank_summary() if ok else {"total": 0, "by_section": {}},
            "today": today.isoformat(),
            "planLive": week is not None,
            "targetScore": get_setting("target_score", "1500"),
            "targetSuperscore": study_plan.active_target(),
            "nextTest": _test_json(study_plan.active_next_test(today)),
            "daysToTest": study_plan.active_days_until_next_test(today),
            "stats": stats,
            "redos": attempt_repo.redo_counts(today.isoformat()),
            "untagged": attempt_repo.untagged_count(),
            "sections": question_repo.list_sections(),
            "hasSession": self.runner is not None,
        }

    def save_setting(self, key: str, value) -> dict:
        allowed = {"target_score", "routing_threshold", "routing_weighted",
                   "last_test_length", "last_test_section", "last_test_timing",
                   "last_drill_section", "last_drill_count", "last_drill_timer"}
        if key in allowed:
            set_setting(key, value)
        return {"ok": key in allowed}

    # ------------------------------------------------------------------ plan

    def plan(self, day_iso: str | None = None) -> dict:
        day = _parse_date(day_iso) or date.today()
        day_key = day.isoformat()
        plan = study_plan.active_day_plan(day)
        week = plan.get("week")
        done = attempt_repo.plan_done_keys(day_key)

        tasks = []
        for index, task in enumerate(plan.get("tasks", [])):
            tasks.append({
                "key": f"t{index}",
                "index": index,
                "minutes": task["minutes"],
                "label": task["label"],
                "detail": task.get("detail", ""),
                "action": task.get("action", "manual"),
                "params": task.get("params", {}),
                "done": f"t{index}" in done,
            })

        targets = []
        for target in attempt_repo.active_targets():
            progress = attempt_repo.target_progress(target["label"], target["axis"])
            can, message = diagnostic.can_retire(progress["accuracy"], progress["sample"])
            targets.append({**target, **progress, "canRetire": can, "message": message})

        return {
            "day": day_key,
            "prevDay": (day - timedelta(days=1)).isoformat(),
            "nextDay": (day + timedelta(days=1)).isoformat(),
            "today": date.today().isoformat(),
            "headline": plan.get("headline", ""),
            "hours": plan.get("hours", ""),
            "source": plan.get("source", ""),
            "tasks": tasks,
            "week": _week_json(week),
            "nextTest": _test_json(study_plan.active_next_test(day)),
            "daysToTest": study_plan.active_days_until_next_test(day),
            "upcoming": [_test_json(d) for d in study_plan.active_upcoming(day, 5)],
            "streak": attempt_repo.plan_streak(day_key),
            "redos": attempt_repo.redo_counts(day_key),
            "targets": targets,
            "suggestions": diagnostic.suggest_targets(
                attempt_repo.miss_counts_by_type(since_days=7),
                [t["label"] for t in attempt_repo.active_targets()]),
            "tiers": study_plan.TIERS,
            "wholePlan": study_plan.THE_WHOLE_PLAN,
            "neverCut": study_plan.NEVER_CUT,
            "targetSuperscore": study_plan.active_target(),
        }

    def calendar(self, month_iso: str | None = None) -> dict:
        """
        A month grid for the plan calendar.

        Marks test days, the seven-week bands, and which days you actually did
        work on — so "have I been consistent?" is answerable at a glance.
        """
        anchor = _parse_date(month_iso + "-01" if month_iso and len(month_iso) == 7
                             else month_iso) or date.today()
        first = anchor.replace(day=1)
        # Grid starts on the Monday on or before the 1st.
        start = first - timedelta(days=first.weekday())
        days = []

        # One query for the whole grid instead of one per day.
        done_by_day = attempt_repo.plan_days_summary(start.isoformat(),
                                                     (start + timedelta(days=41)).isoformat())
        attempts_by_day = attempt_repo.attempts_per_day(start.isoformat(),
                                                        (start + timedelta(days=41)).isoformat())
        tests = {t["date"]: t for t in study_plan.active_test_dates()}
        others = {t["date"]: t for t in study_plan.active_other_dates()}

        for offset in range(42):
            day = start + timedelta(days=offset)
            key = day.isoformat()
            week = study_plan.active_week_for(day)
            plan = study_plan.active_day_plan(day) if week else None
            days.append({
                "date": key,
                "dayOfMonth": day.day,
                "inMonth": day.month == first.month,
                "isToday": day == date.today(),
                "weekNumber": week["number"] if week else None,
                "weekKind": week["kind"] if week else None,
                "weekTitle": week["title"] if week else None,
                "headline": plan["headline"] if plan else "",
                "taskCount": len(plan["tasks"]) if plan else 0,
                "tasksDone": done_by_day.get(key, 0),
                "attempts": attempts_by_day.get(key, 0),
                "test": (tests[day]["label"] if day in tests else None),
                "milestone": (others[day]["label"] if day in others else None),
            })

        prev_month = (first - timedelta(days=1)).replace(day=1)
        next_month = (first + timedelta(days=32)).replace(day=1)
        return {
            "month": first.isoformat()[:7],
            "monthLabel": first.strftime("%B %Y"),
            "prevMonth": prev_month.isoformat()[:7],
            "nextMonth": next_month.isoformat()[:7],
            "today": date.today().isoformat(),
            "days": days,
            "weeks": [{"number": w["number"], "kind": w["kind"], "title": w["title"],
                       "start": w["start"].isoformat(), "end": w["end"].isoformat()}
                      for w in study_plan.active_weeks()],
            "testDates": [_test_json(t) for t in study_plan.active_test_dates()],
        }

    def set_plan_task(self, day: str, key: str, done: bool) -> dict:
        attempt_repo.set_plan_task(day, key, bool(done))
        return {"ok": True, "streak": attempt_repo.plan_streak(day)}

    def add_target(self, label: str) -> dict:
        attempt_repo.add_target(label)
        return {"ok": True}

    def retire_target(self, target_id: int, accuracy: float) -> dict:
        attempt_repo.retire_target(int(target_id), float(accuracy))
        return {"ok": True}

    # ------------------------------------------------------------------ bank

    def bank(self, section: str | None = None) -> dict:
        section = section or question_repo.list_sections()[0]
        history = {b["bucket"]: b for b in attempt_repo.breakdown("domain", section=section)}
        counts = question_repo.domain_counts(section)
        return {
            "section": section,
            "sections": question_repo.list_sections(),
            "domains": [{
                "name": name,
                "count": counts.get(name, 0),
                "accuracy": round(history[name]["accuracy"], 1) if name in history else None,
                "attempts": history[name]["total"] if name in history else 0,
            } for name in question_repo.list_domains(section)],
            "difficultyCounts": {f"{d}|{diff}": n for (d, diff), n in
                                 question_repo.domain_difficulty_counts(section).items()},
            "blueprint": {s: {"questions": b["questions_per_module"],
                              "minutes": b["minutes_per_module"],
                              "quota": b["domain_quota"]}
                          for s, b in BLUEPRINT.items()},
            "weakest": attempt_repo.weakest("domain", min_attempts=4, limit=1),
            "settings": {
                "threshold": get_setting("routing_threshold",
                                         int(DEFAULT_ROUTING_THRESHOLD * 100)),
                "weighted": get_setting("routing_weighted", "on"),
                "length": get_setting("last_test_length", "Single section"),
                "testSection": get_setting("last_test_section", section),
                "timing": get_setting("last_test_timing", "Official timing"),
                "drillCount": get_setting("last_drill_count", "8"),
                "drillTimer": get_setting("last_drill_timer", "Per-question stopwatch"),
            },
        }

    # --------------------------------------------------------------- sittings

    def start_test(self, *, mode: str, sections: list, timed: bool = True,
                   threshold: float = DEFAULT_ROUTING_THRESHOLD,
                   weighted: bool = True) -> dict:
        runner = TestRunner(
            MODE_FULL if mode == "full" else MODE_SECTION,
            sections=sections, timed=timed, threshold=threshold,
            use_weighting=weighted,
            config_snapshot={"sections": sections, "timed": timed,
                             "threshold": threshold, "weighted": weighted},
            rng=self.rng)
        return self._begin(runner)

    def start_drill(self, *, section: str, domains: list, count: int,
                    difficulty: str | None = None, ramp: bool = True,
                    timer: str = "Per-question stopwatch", source: str = "fresh") -> dict:
        count = max(1, min(int(count or 8), 200))
        seen = attempt_repo.seen_counts()

        if source != "fresh":
            ids = attempt_repo.question_ids_where(
                only_incorrect=source in ("missed", "both"),
                only_flagged=source in ("flagged", "both"),
                section=section, limit=count * 4)
            bank = question_repo.fetch_by_ids(ids)
            questions = [bank[qid] for qid in ids if qid in bank]
            if difficulty:
                questions = [q for q in questions if q.difficulty == difficulty]
            if domains:
                questions = [q for q in questions if q.domain in domains]
            self.rng.shuffle(questions)
            questions = questions[:count]
            if ramp:
                questions.sort(key=lambda q: (DIFFICULTY_RANK.get(q.difficulty, 1),
                                              self.rng.random()))
            for index, q in enumerate(questions, start=1):
                q.position, q.module_number = index, 1
        else:
            questions = engine.build_drill(
                section=section, domains=domains or [], count=count,
                difficulty_ramp=ramp, difficulty_filter=difficulty,
                seen_counts=seen, rng=self.rng)

        if not questions:
            return {"error": "No questions matched. Try more domains, a different "
                             "difficulty, or switch back to fresh questions."}

        time_limit = 0
        if timer == "Official pacing":
            time_limit = PACING_TARGET_SECONDS.get(section, 80) * len(questions)

        runner = TestRunner(
            MODE_DRILL, sections=[section], label=_drill_label(section, domains,
                                                               len(questions), source),
            timed=bool(time_limit), drill_questions=questions,
            drill_time_limit=time_limit,
            config_snapshot={"section": section, "domains": domains, "count": count,
                             "difficulty": difficulty, "ramp": ramp, "timer": timer,
                             "source": source},
            rng=self.rng)
        result = self._begin(runner)
        result["perQuestionTimer"] = timer == "Per-question stopwatch"
        return result

    def start_redo(self, limit: int = 10) -> dict:
        today = date.today()
        due = attempt_repo.due_redos(today.isoformat(), limit=limit)
        if due:
            ids = [d["question_id"] for d in due]
            self._redo_stages = {d["question_id"]: d["stage"] for d in due}
            label = f"Cold redo — {len(ids)} due"
        else:
            mastered = attempt_repo.mastered_question_ids(streak=2)
            ids = [q for q in attempt_repo.question_ids_where(
                only_incorrect=True, only_flagged=True, limit=limit * 3)
                if q not in mastered][:limit]
            self._redo_stages = {}
            label = f"Warm-up redo — {len(ids)} recent miss(es)"

        if not ids:
            return {"error": "Nothing to redo yet. Finish a drill, log your misses, "
                             "and they'll be scheduled here automatically."}
        bank = question_repo.fetch_by_ids(ids)
        questions = [bank[qid] for qid in ids if qid in bank]
        if not questions:
            return {"error": "The questions in your redo queue are no longer in the bank."}
        for index, q in enumerate(questions, start=1):
            q.position, q.module_number = index, 1

        runner = TestRunner(MODE_REVIEW, sections=[questions[0].section], label=label,
                            timed=False, drill_questions=questions, rng=self.rng)
        self._is_redo = True
        result = self._begin(runner)
        result["perQuestionTimer"] = True
        return result

    def start_review_pool(self, question_ids: list, label="Review session") -> dict:
        bank = question_repo.fetch_by_ids(question_ids)
        questions = [bank[qid] for qid in question_ids if qid in bank]
        if not questions:
            return {"error": "Those questions are no longer in the bank."}
        for index, q in enumerate(questions, start=1):
            q.position, q.module_number = index, 1
        runner = TestRunner(MODE_REVIEW, sections=[questions[0].section], label=label,
                            timed=False, drill_questions=questions, rng=self.rng)
        result = self._begin(runner)
        result["perQuestionTimer"] = True
        return result

    def _begin(self, runner) -> dict:
        step, payload = runner.start()
        if step == test_flow.STEP_DONE:
            attempt_repo.delete_session(runner.session_id)
            self._is_redo = False
            return {"error": "Couldn't build that — the bank has no matching questions."}
        self.runner = runner
        return self._step_json(step, payload)

    def submit(self, payload: dict) -> dict:
        """Grade a module the browser just finished."""
        if self.runner is None or self.current_plan is None:
            return {"error": "No sitting in progress."}

        questions = self.current_plan.questions
        answers = payload.get("answers") or {}
        times = payload.get("times") or {}
        flagged = {int(i) for i in payload.get("flagged") or []}
        shaky = {int(i) for i in payload.get("shaky") or []}
        eliminated = payload.get("eliminated") or {}
        elapsed = int(payload.get("elapsed") or 0)

        records = []
        for index, question in enumerate(questions):
            answer = str(answers.get(str(index), answers.get(index, "")) or "").strip()
            records.append(AttemptRecord(
                question=question,
                selected_answer=answer,
                is_correct=question.check(answer),
                was_flagged=index in flagged,
                eliminated=set(eliminated.get(str(index), eliminated.get(index, [])) or []),
                time_spent_ms=int(times.get(str(index), times.get(index, 0)) or 0),
                position=index + 1,
                confidence="shaky" if index in shaky else "sure",
                is_redo=self._is_redo,
            ))

        if self._is_redo:
            self._advance_redo(records)

        step, result = self.runner.submit_module(records, elapsed)
        return self._step_json(step, result)

    def resume(self) -> dict:
        if self.runner is None:
            return {"error": "No sitting in progress."}
        step, payload = self.runner.resume()
        return self._step_json(step, payload)

    def abandon(self) -> dict:
        if self.runner is not None:
            self.runner.abandon()
        self.runner = None
        self.current_plan = None
        self._is_redo = False
        return {"ok": True}

    def _step_json(self, step, payload) -> dict:
        if step == test_flow.STEP_MODULE:
            self.current_plan = payload
            self._cache_images(payload.questions)
            runner = self.runner
            context = (runner.label if runner and runner.mode in (MODE_DRILL, MODE_REVIEW)
                       else f"{payload.section} — Module {payload.module_number} of 2")
            return {"step": "module", "module": plan_json(payload),
                    "context": context,
                    "timed": bool(payload.time_limit_seconds) and bool(runner and runner.timed),
                    "mode": runner.mode if runner else ""}

        if step == test_flow.STEP_BREAK:
            info = payload
            result = info.get("result")
            return {"step": "break", "break": {
                "kind": info.get("kind"),
                "heading": info.get("heading"),
                "detail": info.get("detail"),
                "nextLabel": info.get("next_label"),
                "tier": info.get("tier"),
                "tierLabel": TIER_LABEL.get(info.get("tier"), info.get("tier")),
                "minutes": info.get("minutes") or 0,
                "result": None if result is None else {
                    "total": result.total, "correct": result.correct,
                    "rawAccuracy": round(result.raw_accuracy * 100, 1),
                    "weightedAccuracy": round(result.weighted_accuracy * 100, 1),
                    "timeUsed": result.time_used_seconds,
                },
            }}

        self.runner = None
        self.current_plan = None
        self._is_redo = False
        return {"step": "done", "summary": summary_json(payload)}

    def _advance_redo(self, records):
        today = date.today()
        for record in records:
            qid = record.question.question_id
            if qid not in self._redo_stages:
                continue
            stage = self._redo_stages[qid]
            following = diagnostic.next_stage(stage, record.is_correct)
            due = (diagnostic.due_date_for(following, today).isoformat()
                   if following is not None else None)
            attempt_repo.complete_redo(qid, was_correct=record.is_correct,
                                       next_stage_value=following, next_due=due)
        self._redo_stages = {}
        self._is_redo = False

    # -------------------------------------------------------------- images

    def _cache_images(self, questions):
        for q in questions:
            self._image_paths[q.question_id] = (q.image_path, q.rationale_path)

    def image_path(self, question_id: str, kind: str = "question") -> str | None:
        cached = self._image_paths.get(question_id)
        if cached is None:
            found = question_repo.fetch_by_ids([question_id]).get(question_id)
            if found is None:
                return None
            cached = (found.image_path, found.rationale_path)
            self._image_paths[question_id] = cached
        return cached[1] if kind == "rationale" else cached[0]

    # --------------------------------------------------------------- review

    def review(self, session_id: int) -> dict:
        summary = test_flow.summary_from_history(int(session_id))
        if summary is None:
            return {"error": "That session no longer exists."}
        return summary_json(summary)

    # ------------------------------------------------------------ error log

    def log(self, session_id=None) -> dict:
        rows = (attempt_repo.logged_attempts(int(session_id)) if session_id
                else attempt_repo.recent_logged(limit=150))
        bank = question_repo.fetch_by_ids([r["question_id"] for r in rows])

        entries = []
        for row in rows:
            question = bank.get(row["question_id"])
            entries.append({
                "attemptId": row["attempt_id"],
                "questionId": row["question_id"],
                "lucky": bool(row["is_correct"]) and row.get("confidence") == "shaky",
                "isCorrect": bool(row["is_correct"]),
                "domain": row.get("domain") or "",
                "skill": row.get("skill") or "",
                "difficulty": row.get("difficulty") or "Medium",
                "selected": row.get("selected_answer") or "",
                "correctAnswer": row.get("correct_answer") or "",
                "eliminated": row.get("eliminated") or "",
                "seconds": round((row.get("time_spent_ms") or 0) / 1000, 1),
                "answeredAt": row.get("answered_at") or "",
                "sessionLabel": row.get("session_label") or "",
                "moduleNumber": row.get("module_number"),
                "rootCause": row.get("root_cause") or "",
                "fixNote": row.get("fix_note") or "",
                "image": f"/img/{row['question_id']}" if question and question.image_path else None,
                "rationale": (f"/img/{row['question_id']}?kind=rationale"
                              if question and question.has_rationale else None),
                "note": attempt_repo.get_note(row["question_id"]),
                "missing": question is None,
            })

        counts = attempt_repo.cause_counts(since_days=7)
        tagged = sum(counts.values())
        return {
            "entries": entries,
            "causes": [{"code": c, "label": l, "detail": d}
                       for c, l, d, _f in diagnostic.ROOT_CAUSES],
            "counts": counts,
            "tagged": tagged,
            "totals": attempt_repo.log_totals(since_days=7),
            "findings": diagnostic.diagnose(
                counts, tagged, attempt_repo.lucky_counts(since_days=7)),
            "laggingNote": diagnostic.LAGGING_NOTE,
        }

    def tag(self, attempt_id: int, root_cause=None, fix_note=None) -> dict:
        if fix_note is not None:
            text = str(fix_note).strip()
            if not text:
                return {"error": "Write one sentence — it's the part that makes it stick."}
            lowered = text.lower()
            if any(p in lowered for p in BANNED_PHRASES) and len(text) < 60:
                return {"error": "That's a diagnosis, not an instruction. Name a specific, "
                                 "checkable action — if a stranger couldn't watch you and "
                                 "tell whether you did it, it isn't a rule."}
        attempt_repo.tag_attempt(int(attempt_id), root_cause=root_cause, fix_note=fix_note)

        # Schedule the cold redo once both axes are filled in.
        row = next((r for r in attempt_repo.recent_logged(limit=300)
                    if r["attempt_id"] == int(attempt_id)), None)
        scheduled = False
        if row and row.get("root_cause") and row.get("fix_note"):
            attempt_repo.schedule_redo(
                row["question_id"], stage=0,
                due_on=diagnostic.due_date_for(0, date.today()).isoformat(),
                source_attempt=int(attempt_id), section=row.get("section") or "",
                domain=row.get("domain") or "", skill=row.get("skill") or "")
            scheduled = True
        return {"ok": True, "scheduled": scheduled}

    def save_note(self, question_id: str, body: str) -> dict:
        attempt_repo.save_note(question_id, body or "")
        return {"ok": True}

    def get_note(self, question_id: str) -> dict:
        return {"body": attempt_repo.get_note(question_id)}

    # -------------------------------------------------------------- history

    def history(self) -> dict:
        sessions = attempt_repo.list_sessions(limit=300)
        for session in sessions:
            session["tierLabels"] = [TIER_LABEL.get(t, t).split("—")[0].strip()
                                     for t in session.get("tier_path", [])]
        return {"sessions": sessions}

    def delete_session(self, session_id: int) -> dict:
        attempt_repo.delete_session(int(session_id))
        return {"ok": True}

    def delete_all_history(self) -> dict:
        attempt_repo.clear_history(keep_notes=True)
        return {"ok": True}

    # ------------------------------------------------------------ dashboard

    def dashboard(self) -> dict:
        return {
            "stats": attempt_repo.overall_stats(),
            "timeline": attempt_repo.accuracy_timeline(limit=30),
            "domains": attempt_repo.breakdown("domain"),
            "skills": sorted(attempt_repo.breakdown("skill", min_attempts=3),
                             key=lambda b: b["accuracy"])[:12],
            "difficulty": attempt_repo.breakdown("difficulty"),
            "pacing": attempt_repo.pace_stats(),
            "causes": attempt_repo.cause_counts(since_days=30),
            "indicators": [
                {"key": k, "label": l, "detail": d, "want": w}
                for k, l, d, w in diagnostic.INDICATORS
            ],
        }


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _week_json(week):
    if not week:
        return None
    return {
        "number": week["number"], "kind": week["kind"], "title": week["title"],
        "summary": week["summary"], "bullets": week["bullets"],
        "hours": week["hours"], "mathHours": week["math_hours"],
        "rwHours": week["rw_hours"],
        "focus": [{"domain": d, **study_plan.active_priorities().get(d, {})}
                  for d in week.get("focus_domains", [])],
    }


def _test_json(entry):
    if not entry:
        return None
    return {"date": entry["date"].isoformat(), "label": entry["label"],
            "note": entry.get("note", ""), "kind": entry.get("kind", "test")}


def _drill_label(section, domains, count, source):
    if source == "missed":
        scope = "my mistakes"
    elif source == "flagged":
        scope = "flagged questions"
    elif source == "both":
        scope = "mistakes + flagged"
    elif not domains:
        scope = section
    elif len(domains) == 1:
        scope = domains[0]
    else:
        scope = f"{len(domains)} domains"
    return f"Drill — {scope} ({count}q)"
