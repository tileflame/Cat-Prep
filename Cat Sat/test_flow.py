"""
test_flow.py, the state machine that runs a sitting from start to results.

The quiz screen stays dumb: it shows a list of questions and hands back what the
user did. This module decides what comes next, persists everything, and works
out the routing and the score.

Supported modes
---------------
full_test     Reading and Writing M1 -> M2, break, Math M1 -> M2
section_test  One section, M1 -> M2
drill         A single untimed-or-timed set of questions, no routing
review        Replay a specific pool (missed / flagged / a past session)
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import asdict, dataclass, field, replace

import adaptive_engine as engine
import attempt_repo
from config import (
    BLUEPRINT,
    DEFAULT_ROUTING_THRESHOLD,
    FULL_TEST_SECTION_ORDER,
    TIER_BASELINE,
    TIER_LABEL,
)

MODE_FULL = "full_test"
MODE_SECTION = "section_test"
MODE_DRILL = "drill"
MODE_REVIEW = "review"
# Answer, check it straight away, read why, move on. One module like a drill,
# but the correct answer is revealed per question instead of at the end.
MODE_CHECK = "check"

#: Modes that are one module and done, with no routing into a Module 2.
SINGLE_MODULE_MODES = (MODE_DRILL, MODE_REVIEW, MODE_CHECK)

# What run()/advance() can return.
STEP_MODULE = "module"      # payload: ModulePlan
STEP_BREAK = "break"        # payload: dict describing the break
STEP_DONE = "done"          # payload: TestSummary


@dataclass
class SectionOutcome:
    """Everything that happened in one section of a sitting."""

    section: str
    modules: list = field(default_factory=list)          # list[ModuleResult]
    records: list = field(default_factory=list)          # list[AttemptRecord]
    final_tier: str = TIER_BASELINE
    routing_note: str = ""

    @property
    def total(self) -> int:
        return sum(m.total for m in self.modules)

    @property
    def correct(self) -> int:
        return sum(m.correct for m in self.modules)

    @property
    def accuracy(self) -> float:
        return (self.correct / self.total * 100) if self.total else 0.0

    @property
    def estimated_score(self) -> int:
        # The section matters: Reading and Writing is 54 questions and Math is
        # 44, so one wrong answer is not worth the same on both curves.
        return engine.estimate_section_score(
            self.correct, self.total, self.final_tier, self.section)

    @property
    def score_is_reliable(self) -> bool:
        return engine.score_is_reliable(self.total)

    @property
    def time_used(self) -> int:
        return sum(m.time_used_seconds for m in self.modules)


@dataclass
class TestSummary:
    """The finished sitting, handed to the review screen."""

    session_id: int
    mode: str
    label: str
    sections: list = field(default_factory=list)         # list[SectionOutcome]
    records: list = field(default_factory=list)          # every AttemptRecord
    duration_seconds: int = 0

    @property
    def total(self) -> int:
        return len(self.records)

    @property
    def correct(self) -> int:
        return sum(1 for r in self.records if r.is_correct)

    @property
    def accuracy(self) -> float:
        return (self.correct / self.total * 100) if self.total else 0.0

    @property
    def section_scores(self) -> dict:
        return {s.section: s.estimated_score for s in self.sections if s.total}

    @property
    def estimated_total(self) -> int:
        scores = self.section_scores
        if len(scores) < 2:
            return 0                 # a single section has no 1600-scale total
        return engine.estimate_total_score(scores)

    @property
    def is_adaptive(self) -> bool:
        return self.mode in (MODE_FULL, MODE_SECTION)


class TestRunner:
    """
    Drives one sitting. Create it, call start(), then submit_module() after each
    module until you get STEP_DONE.
    """

    def __init__(
        self,
        mode: str,
        *,
        sections: list[str] | None = None,
        label: str = "",
        timed: bool = True,
        threshold: float = DEFAULT_ROUTING_THRESHOLD,
        use_weighting: bool = True,
        drill_questions: list | None = None,
        drill_time_limit: int | None = None,
        config_snapshot: dict | None = None,
        rng: random.Random | None = None,
        fixed_modules: dict | None = None,
    ):
        self.mode = mode
        # A numbered practice test hands in its modules ready-made, keyed
        # (section, module number, tier). Anything not in here is built fresh,
        # which is what every ordinary test does.
        self.fixed_modules = fixed_modules or {}
        self.label = label or self._default_label(mode, sections)
        self.timed = timed
        self.threshold = threshold
        self.use_weighting = use_weighting
        self.rng = rng or random.Random()

        self.sections = list(sections or [])
        if mode == MODE_FULL and not self.sections:
            self.sections = list(FULL_TEST_SECTION_ORDER)

        self.drill_questions = drill_questions or []
        self.drill_time_limit = drill_time_limit

        # Progress trackers.
        self.section_index = 0
        self.module_number = 0
        self.current_tier = TIER_BASELINE
        self.current_plan = None
        self.current_module_row_id = None
        self.outcomes: list[SectionOutcome] = []
        self.all_records: list = []
        self.used_ids: set[str] = set()
        self.started_at = time.time()
        self._pending_break = None
        self._finished = False
        # Pause and resume. The clock on a sitting stops while you are away
        # from it, so a module you left for a day does not report a day spent.
        self.paused_seconds = 0.0
        self._paused_at: float | None = None
        # True from the moment a module is handed out until it is handed in.
        # It is what makes a second click on Continue, or a second Submit,
        # harmless instead of ending the sitting or grading a module twice.
        self._module_open = False

        # Freshness data, fetched once per sitting.
        self.seen = attempt_repo.seen_counts()

        section_label = (
            "Mixed" if len(self.sections) != 1 else self.sections[0]
        ) if self.sections else "Mixed"
        self.session_id = attempt_repo.create_session(
            mode, section_label, self.label, config_snapshot or {}
        )

    # ------------------------------------------------------------- labelling

    @staticmethod
    def _default_label(mode: str, sections) -> str:
        if mode == MODE_FULL:
            return "Full-length adaptive test"
        if mode == MODE_SECTION and sections:
            return f"{sections[0]}, adaptive test"
        if mode == MODE_REVIEW:
            return "Review session"
        return "Practice drill"

    @property
    def current_section(self) -> str:
        if not self.sections:
            return "Mixed"
        index = min(self.section_index, len(self.sections) - 1)
        return self.sections[index]

    @property
    def total_modules(self) -> int:
        if self.mode in (MODE_FULL, MODE_SECTION):
            return len(self.sections) * 2
        return 1

    @property
    def modules_done(self) -> int:
        return sum(len(o.modules) for o in self.outcomes)

    # ------------------------------------------------------------- lifecycle

    def start(self):
        """Return the first step: a module to sit, or done if nothing built."""
        if self.mode in SINGLE_MODULE_MODES:
            return self._start_drill()
        return self._start_section(0)

    def _start_drill(self):
        if not self.drill_questions:
            return STEP_DONE, self._finish()
        plan = engine.ModulePlan(
            section=self.current_section,
            module_number=1,
            tier=TIER_BASELINE,
            questions=self.drill_questions,
            time_limit_seconds=self.drill_time_limit or 0,
            target_count=len(self.drill_questions),
            difficulty_actual=engine._count_by_difficulty(self.drill_questions),
        )
        self.outcomes.append(SectionOutcome(section=self.current_section))
        return self._serve(plan)

    def _start_section(self, index: int):
        self.section_index = index
        section = self.current_section
        self.outcomes.append(SectionOutcome(section=section))
        plan = self._module_plan(section, 1, TIER_BASELINE)
        if not plan.questions:
            return STEP_DONE, self._finish()
        return self._serve(plan)

    def _module_plan(self, section: str, module_number: int, tier: str):
        """
        The module to sit next: the stored one for a numbered practice test,
        otherwise a freshly assembled one. Routing, timing and scoring are the
        same either way, which is the point: Practice Test 3 is graded exactly
        like any other adaptive test.
        """
        fixed = self.fixed_modules.get((section, module_number, tier))
        if fixed is None:
            return engine.build_module(
                section, module_number, tier,
                exclude_ids=set(self.used_ids), seen_counts=self.seen, rng=self.rng,
            )
        blueprint = BLUEPRINT.get(section) or {}
        minutes = blueprint.get("minutes_per_module", 0)
        target = blueprint.get("questions_per_module", len(fixed))
        return engine.ModulePlan(
            section=section,
            module_number=module_number,
            tier=tier,
            questions=list(fixed),
            time_limit_seconds=int(minutes * 60 * len(fixed) / max(target, 1)),
            target_count=target,
            difficulty_actual=engine._count_by_difficulty(fixed),
        )

    def _serve(self, plan):
        """Register a module and hand it to the caller."""
        self.current_plan = plan
        self.module_number = plan.module_number
        self.current_tier = plan.tier
        self._module_open = True
        self.used_ids.update(q.question_id for q in plan.questions)
        self.current_module_row_id = attempt_repo.create_module(
            self.session_id, plan.section, plan.module_number, plan.tier,
            len(plan.questions),
            plan.time_limit_seconds if self.timed else None,
        )
        return STEP_MODULE, plan

    def submit_module(self, records, time_used_seconds: int = 0):
        """
        Hand back what the user did. Persists, routes, and returns the next step.

        Everything that can fail (the database writes, building Module 2) runs
        BEFORE the module counts as handed in. A write that failed used to leave
        the module marked handed in with nothing saved, and the rest of the
        sitting unreachable. Now the module stays open and handing it in again
        works; answers already on record for it are not written twice.
        """
        plan = self.current_plan
        if plan is None or not self._module_open:
            # Already handed in (a double click, or a retry that crossed the
            # first reply). Never record the same module twice: say where the
            # sitting already is instead.
            if self._pending_break:
                return STEP_BREAK, self._pending_break
            return STEP_DONE, self._finish()

        for index, record in enumerate(records, start=1):
            if not record.position:
                record.position = index

        if not attempt_repo.module_has_attempts(self.current_module_row_id):
            attempt_repo.record_attempts(self.session_id, self.current_module_row_id, records)

        result = engine.evaluate_module(
            plan.section, plan.module_number, plan.tier, records, time_used_seconds
        )
        result.module_row_id = self.current_module_row_id

        single = self.mode in SINGLE_MODULE_MODES
        # Module 1 of a test decides where Module 2 goes; nothing else routes.
        next_tier = None
        if not single and plan.module_number == 1:
            next_tier = engine.route(result, threshold=self.threshold,
                                     use_weighting=self.use_weighting)
        attempt_repo.finish_module(
            self.current_module_row_id, correct_count=result.correct,
            raw_accuracy=result.raw_accuracy,
            weighted_accuracy=result.weighted_accuracy,
            routed_to=next_tier, time_used_seconds=time_used_seconds,
        )
        next_plan = self._module_plan(plan.section, 2, next_tier) if next_tier else None

        # Nothing below can fail halfway. The module is handed in.
        self._module_open = False
        outcome = self.outcomes[-1]
        outcome.modules.append(result)
        outcome.records.extend(records)
        self.all_records.extend(records)

        # ---- Drills and review sessions have no second module.
        if single:
            outcome.final_tier = TIER_BASELINE
            return STEP_DONE, self._finish()

        # ---- Module 1: Module 2 is ready, and the break comes first.
        if plan.module_number == 1:
            outcome.final_tier = next_tier
            outcome.routing_note = engine.routing_explanation(result, next_tier, self.threshold)
            if not next_plan.questions:
                return STEP_DONE, self._finish()

            self._pending_break = {
                "kind": "module",
                "section": plan.section,
                "heading": f"{plan.section}, Module 1 complete",
                "detail": outcome.routing_note,
                "next_label": f"Module 2 · {TIER_LABEL[next_tier]}",
                "tier": next_tier,
                "minutes": 0,
                "result": result,
                "plan": next_plan,
            }
            return STEP_BREAK, self._pending_break

        # ---- Module 2 finished: close the section out.
        has_more = self.section_index + 1 < len(self.sections)
        if self.mode == MODE_FULL and has_more:
            next_section = self.sections[self.section_index + 1]
            self._pending_break = {
                "kind": "section",
                "section": plan.section,
                "heading": f"{plan.section} complete",
                "detail": (f"You scored {outcome.correct}/{outcome.total} "
                           f"({outcome.accuracy:.0f}%). Estimated section score: "
                           f"{outcome.estimated_score}."),
                "next_label": f"{next_section}, Module 1",
                "tier": TIER_BASELINE,
                "minutes": 10,
                "result": result,
                "plan": None,
                "next_section_index": self.section_index + 1,
            }
            return STEP_BREAK, self._pending_break

        return STEP_DONE, self._finish()

    def resume(self):
        """Continue past a break screen."""
        pending = self._pending_break
        if not pending:
            # Nothing to continue past: Continue was clicked twice and the first
            # click already handed out the next module. Hand out the same one
            # again. Finishing here used to close a full test after Module 1.
            if self._module_open and self.current_plan is not None:
                return STEP_MODULE, self.current_plan
            return STEP_DONE, self._finish()
        self._pending_break = None
        if pending.get("plan") is not None:
            return self._serve(pending["plan"])
        if "next_section_index" in pending:
            return self._start_section(pending["next_section_index"])
        return STEP_DONE, self._finish()

    def abandon(self):
        """User quit mid-test: keep the answers, mark the sitting incomplete."""
        if self._finished:
            return
        self._finished = True
        attempt_repo.finish_session(
            self.session_id,
            total_questions=len(self.all_records),
            correct_count=sum(1 for r in self.all_records if r.is_correct),
            duration_seconds=self.active_seconds(),
            estimated_score=None,
            status="abandoned",
        )

    # ------------------------------------------------------------ pause/resume

    def pause_clock(self) -> None:
        if self._paused_at is None:
            self._paused_at = time.time()

    def resume_clock(self) -> None:
        if self._paused_at is not None:
            self.paused_seconds += max(0.0, time.time() - self._paused_at)
            self._paused_at = None

    def active_seconds(self) -> int:
        """Time actually spent in the sitting: wall time minus every pause."""
        away = self.paused_seconds
        if self._paused_at is not None:
            away += max(0.0, time.time() - self._paused_at)
        return max(0, int(time.time() - self.started_at - away))

    @property
    def module_open(self) -> bool:
        return self._module_open

    @property
    def pending_break(self):
        return self._pending_break

    def snapshot(self) -> dict:
        """
        Everything needed to rebuild this sitting later, as plain JSON.

        Questions are stored by id and fetched again on the way back, and the
        modules already handed in are not stored at all: their answers are in
        the attempts table, which is where restore() reads them from.
        """
        def plan_state(plan):
            if plan is None:
                return None
            return {"section": plan.section, "module_number": plan.module_number,
                    "tier": plan.tier, "time_limit_seconds": plan.time_limit_seconds,
                    "target_count": plan.target_count,
                    "question_ids": [q.question_id for q in plan.questions],
                    "domain_gaps": plan.domain_gaps,
                    "difficulty_actual": plan.difficulty_actual,
                    "difficulty_target": plan.difficulty_target,
                    "skill_gaps": plan.skill_gaps}

        pending = None
        if self._pending_break:
            info = self._pending_break
            result = info.get("result")
            pending = {key: info[key] for key in (
                "kind", "section", "heading", "detail", "next_label", "tier", "minutes",
                "next_section_index") if key in info}
            pending["plan"] = plan_state(info.get("plan"))
            pending["result"] = asdict(result) if result is not None else None

        return {
            "v": 1,
            "session_id": self.session_id, "mode": self.mode, "label": self.label,
            "sections": list(self.sections), "timed": self.timed,
            "threshold": self.threshold, "use_weighting": self.use_weighting,
            "drill_time_limit": self.drill_time_limit,
            "section_index": self.section_index, "module_number": self.module_number,
            "current_tier": self.current_tier,
            "current_module_row_id": self.current_module_row_id,
            "module_open": self._module_open,
            "current_plan": plan_state(self.current_plan) if self._module_open else None,
            "pending_break": pending,
            "used_ids": sorted(self.used_ids),
            "fixed_modules": [[s, m, t, [q.question_id for q in qs]]
                              for (s, m, t), qs in self.fixed_modules.items()],
            "started_at": self.started_at,
            # Pauses already over, plus when the current one began. restore()
            # needs the start, not a running total: the app can be closed for
            # a day between this snapshot and the next time anyone reads it.
            "paused_seconds": self.paused_seconds,
            "paused_at": self._paused_at,
            "saved_epoch": time.time(),
        }

    @classmethod
    def restore(cls, state: dict) -> "TestRunner":
        """
        Rebuild a sitting from snapshot(), for example after the app restarted.

        The runner comes back paused; call resume_clock() when the student is
        actually back in front of it.
        """
        import question_repo
        from models import Question

        runner = cls.__new__(cls)
        runner.mode = state["mode"]
        runner.label = state.get("label") or cls._default_label(state["mode"], state.get("sections"))
        runner.timed = bool(state.get("timed", True))
        runner.threshold = float(state.get("threshold", DEFAULT_ROUTING_THRESHOLD))
        runner.use_weighting = bool(state.get("use_weighting", True))
        runner.rng = random.Random()
        runner.sections = list(state.get("sections") or [])
        runner.drill_questions = []
        runner.drill_time_limit = state.get("drill_time_limit")
        runner.session_id = int(state["session_id"])
        runner.section_index = int(state.get("section_index") or 0)
        runner.module_number = int(state.get("module_number") or 0)
        runner.current_tier = state.get("current_tier") or TIER_BASELINE
        runner.current_module_row_id = state.get("current_module_row_id")
        runner.used_ids = set(state.get("used_ids") or [])
        runner.started_at = float(state.get("started_at") or time.time())
        runner.paused_seconds = float(state.get("paused_seconds") or 0.0)
        # Paused since the pause began, or, for a sitting the app died in the
        # middle of, since the last time it was saved.
        runner._paused_at = float(state.get("paused_at") or state.get("saved_epoch") or time.time())
        runner._finished = False
        runner.seen = attempt_repo.seen_counts()

        pending_state = state.get("pending_break") or None
        wanted = []
        for _s, _m, _t, ids in state.get("fixed_modules") or []:
            wanted.extend(ids)
        for plan in (state.get("current_plan"), (pending_state or {}).get("plan")):
            if plan:
                wanted.extend(plan.get("question_ids") or [])
        bank = question_repo.fetch_by_ids(wanted)

        def questions_for(ids, module_number, tier, section):
            out = []
            for position, qid in enumerate(ids, start=1):
                question = bank.get(qid)
                if question is None:
                    # Gone from the bank since. Keep the slot, so the answers
                    # saved against positions still line up with their questions.
                    question = Question(question_id=qid, section=section, domain="General",
                                        skill="General", difficulty="Medium", question_img=None,
                                        correct_answer="", rationale=None, is_open_ended=False)
                else:
                    # fetch_by_ids hands out one object per id; a question that
                    # sits in two stored modules must not share its position.
                    question = replace(question)
                question.module_number, question.tier, question.position = module_number, tier, position
                out.append(question)
            return out

        def plan_from(data):
            if not data:
                return None
            questions = questions_for(data.get("question_ids") or [], data["module_number"],
                                      data["tier"], data["section"])
            return engine.ModulePlan(
                section=data["section"], module_number=data["module_number"], tier=data["tier"],
                questions=questions, time_limit_seconds=int(data.get("time_limit_seconds") or 0),
                target_count=int(data.get("target_count") or len(questions)),
                domain_gaps=data.get("domain_gaps") or {},
                difficulty_actual=data.get("difficulty_actual") or {},
                difficulty_target=data.get("difficulty_target") or {},
                skill_gaps=data.get("skill_gaps") or {})

        runner.fixed_modules = {(s, m, t): questions_for(ids, m, t, s)
                                for s, m, t, ids in state.get("fixed_modules") or []}
        runner.current_plan = plan_from(state.get("current_plan"))
        runner._module_open = bool(state.get("module_open")) and runner.current_plan is not None

        runner._pending_break = None
        if pending_state:
            info = dict(pending_state)
            info["plan"] = plan_from(pending_state.get("plan"))
            result = pending_state.get("result")
            info["result"] = engine.ModuleResult(**result) if result else None
            runner._pending_break = info

        # The modules already handed in, rebuilt from the answers on record.
        # The open module has a row in `modules` but no answers yet, so it is
        # left out; the section being sat gets its outcome back, empty if this
        # is its first module.
        summary = summary_from_history(runner.session_id)
        open_row = runner.current_module_row_id if runner._module_open else None
        runner.outcomes = []
        for outcome in (summary.sections if summary else []):
            outcome.modules = [m for m in outcome.modules if m.module_row_id != open_row]
            if outcome.modules:
                runner.outcomes.append(outcome)
        current = runner.current_plan.section if runner._module_open else None
        if current and (not runner.outcomes or runner.outcomes[-1].section != current):
            runner.outcomes.append(SectionOutcome(section=current))
        runner.all_records = [r for o in runner.outcomes for r in o.records]
        return runner

    def _finish(self) -> TestSummary:
        """Close the session and build the summary the review screen renders."""
        duration = self.active_seconds()
        summary = TestSummary(
            session_id=self.session_id,
            mode=self.mode,
            label=self.label,
            sections=[o for o in self.outcomes if o.total],
            records=self.all_records,
            duration_seconds=duration,
        )

        estimated = None
        if summary.is_adaptive:
            estimated = (summary.estimated_total
                         or next(iter(summary.section_scores.values()), None))

        if not self._finished:
            attempt_repo.finish_session(
                self.session_id,
                total_questions=summary.total,
                correct_count=summary.correct,
                duration_seconds=duration,
                estimated_score=estimated,
                status="completed",
            )
            self._finished = True
        return summary


# ---------------------------------------------------------------------------
# REBUILDING A PAST SITTING FOR REVIEW
# ---------------------------------------------------------------------------

def summary_from_history(session_id: int):
    """
    Reconstruct a TestSummary from the database so any past sitting can be
    reopened in the full review screen, not just the one you finished a moment
    ago. Questions that have since been removed from the bank degrade to a
    placeholder rather than breaking the screen.
    """
    import question_repo
    from models import AttemptRecord, Question

    session = attempt_repo.get_session(session_id)
    if not session:
        return None
    # The routing line has to quote the threshold this sitting was actually
    # routed with, not the default: a 75% test explained against 65% says
    # "below the line" about a score that was above it.
    try:
        threshold = float(json.loads(session.get("config_json") or "{}")
                          .get("threshold", DEFAULT_ROUTING_THRESHOLD))
    except (TypeError, ValueError, AttributeError):
        threshold = DEFAULT_ROUTING_THRESHOLD

    attempts = attempt_repo.get_session_attempts(session_id)
    modules = attempt_repo.get_session_modules(session_id)
    bank = question_repo.fetch_by_ids([a["question_id"] for a in attempts])

    records_by_module: dict = {}
    all_records = []

    for attempt in attempts:
        question = bank.get(attempt["question_id"])
        if question is None:
            # The question is gone from the bank; keep the history readable.
            question = Question(
                question_id=attempt["question_id"],
                section=attempt["section"] or "",
                domain=attempt["domain"] or "General",
                skill=attempt["skill"] or "General",
                difficulty=attempt["difficulty"] or "Medium",
                question_img=None,
                correct_answer=attempt["correct_answer"] or "",
                rationale=None,
                is_open_ended=False,
            )
        question.position = attempt["position"] or 0
        question.module_number = attempt["module_number"] or 1
        question.tier = attempt["tier"] or TIER_BASELINE

        record = AttemptRecord(
            question=question,
            selected_answer=attempt["selected_answer"] or "",
            is_correct=bool(attempt["is_correct"]),
            was_flagged=bool(attempt["was_flagged"]),
            eliminated=set((attempt["eliminated"] or "").split(",")) - {""},
            time_spent_ms=attempt["time_spent_ms"] or 0,
            position=attempt["position"] or 0,
        )
        records_by_module.setdefault(attempt["module_row_id"], []).append(record)
        all_records.append(record)

    # Rebuild section outcomes in module order.
    outcomes: dict[str, SectionOutcome] = {}
    for module in modules:
        section = module["section"]
        outcome = outcomes.setdefault(section, SectionOutcome(section=section))
        records = records_by_module.get(module["module_row_id"], [])
        result = engine.ModuleResult(
            section=section,
            module_number=module["module_number"],
            tier=module["tier"],
            total=len(records) or (module["question_count"] or 0),
            correct=module["correct_count"] or sum(1 for r in records if r.is_correct),
            weighted_correct=sum(r.question.weight for r in records if r.is_correct),
            weighted_total=sum(r.question.weight for r in records),
            time_used_seconds=module["time_used_seconds"] or 0,
            module_row_id=module["module_row_id"],
        )
        outcome.modules.append(result)
        outcome.records.extend(records)
        if module["module_number"] == 1 and module["routed_to"]:
            outcome.final_tier = module["routed_to"]
            outcome.routing_note = engine.routing_explanation(result, module["routed_to"],
                                                              threshold)

    # A session recorded before modules existed (migrated history) still works.
    if not outcomes and all_records:
        section = session["section"] or "Mixed"
        outcome = SectionOutcome(section=section)
        outcome.records = all_records
        outcome.modules.append(engine.ModuleResult(
            section=section, module_number=1, tier=TIER_BASELINE,
            total=len(all_records),
            correct=sum(1 for r in all_records if r.is_correct),
            weighted_correct=sum(r.question.weight for r in all_records if r.is_correct),
            weighted_total=sum(r.question.weight for r in all_records),
            time_used_seconds=0,
        ))
        outcomes[section] = outcome

    return TestSummary(
        session_id=session_id,
        mode=session["mode"],
        label=session["label"] or "Practice session",
        sections=list(outcomes.values()),
        records=all_records,
        duration_seconds=session["duration_seconds"] or 0,
    )
