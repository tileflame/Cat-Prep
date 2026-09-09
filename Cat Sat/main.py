"""
main.py — application shell and router.

Run this file to start Cat SAT:

    python main.py

Before the first run, put the College Board PDFs (downloaded *with* answers and
rationales) in the pdfs folder and run:

    python sat_importer.py

The controller below owns exactly two things: which screen is on display, and
the TestRunner for the sitting in progress. Screens never talk to each other
directly — they call methods here, which keeps the navigation graph in one file.
"""

from __future__ import annotations

import random
import sys
import traceback
from datetime import date

import customtkinter as ctk

import adaptive_engine as engine
import attempt_repo
import background
import database
import diagnostic
import question_repo
import study_plan
import test_flow
import ui_kit as ui
from config import C, DEFAULT_ROUTING_THRESHOLD, PACING_TARGET_SECONDS
from dashboard_screen import DashboardScreen
from database import init_db
from drill_setup_screen import DrillSetupScreen
from error_log_screen import ErrorLogScreen
from plan_screen import PlanScreen
from history_screen import HistoryScreen
from home_screen import HomeScreen
from module_break_screen import ModuleBreakScreen
from quiz_screen import QuizScreen
from review_screen import ReviewScreen
from test_flow import MODE_DRILL, MODE_REVIEW, TestRunner
from test_setup_screen import TestSetupScreen

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("dark-blue")


class SATApp(ctk.CTk):

    def __init__(self):
        super().__init__()
        self.title("Cat SAT — Adaptive Practice")
        self.configure(fg_color=C.BG)

        # Size to the screen rather than assuming a big monitor. A fixed
        # 1180x860 with a 1000x720 minimum is taller than a 1366x768 laptop can
        # show once Windows display scaling is applied, which silently pushed
        # the bottom of every screen out of view.
        try:
            screen_w = self.winfo_screenwidth()
            screen_h = self.winfo_screenheight()
        except Exception:
            screen_w, screen_h = 1280, 800
        width = max(820, min(1180, screen_w - 120))
        height = max(560, min(880, screen_h - 140))
        self.geometry(f"{width}x{height}")
        self.minsize(min(820, width), min(560, height))

        # Bring both databases to a known-good state, repairing the old schema
        # if this install predates the upgrade.
        self.db_report = init_db()
        attempt_repo.abandon_stale_sessions()

        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.pack(fill="both", expand=True)

        self.current_frame = None
        self.runner: TestRunner | None = None
        self.rng = random.Random()
        self._redo_stages = {}
        self._is_redo_session = False

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Single main-thread pump that delivers background results (image
        # decodes). Installed once; it backs off to a slow poll when idle.
        background.install_pump(self)

        # Open straight onto Today's Plan when the plan is live and the bank is
        # ready — that is the question you actually have when you sit down.
        if self.db_report["bank_ok"] and study_plan.active_week_for(date.today()):
            self.show_plan()
        else:
            self.show_home_screen()

    # ------------------------------------------------------------ navigation

    def _swap(self, factory):
        """Replace the visible screen. One place to destroy the old one."""
        old = self.current_frame
        self.current_frame = None
        if old is not None:
            try:
                old.destroy()
            except Exception:
                traceback.print_exc()
        frame = factory(self.container, self)
        frame.pack(fill="both", expand=True)
        self.current_frame = frame
        # Give the new screen the keyboard so shortcuts work without clicking first.
        try:
            frame.focus_set()
        except Exception:
            pass
        return frame

    def show_home_screen(self):
        self.runner = None
        self._swap(HomeScreen)

    def show_test_setup(self):
        self._swap(TestSetupScreen)

    def show_drill_setup(self):
        self._swap(DrillSetupScreen)

    def show_history(self):
        self._swap(HistoryScreen)

    def show_dashboard(self):
        self._swap(DashboardScreen)

    def show_plan(self, day=None):
        """Today's Plan — the screen that answers 'what am I doing right now?'"""
        self.runner = None
        self._swap(lambda parent, controller: PlanScreen(parent, controller, day))

    def show_error_log(self, session_id=None):
        self._swap(lambda parent, controller: ErrorLogScreen(parent, controller, session_id))

    # ---------------------------------------------------------- adaptive test

    def start_adaptive_test(self, *, mode, sections, timed=True,
                            threshold=DEFAULT_ROUTING_THRESHOLD, use_weighting=True):
        """Kick off a full-length or single-section adaptive test."""
        runner = TestRunner(
            mode,
            sections=sections,
            timed=timed,
            threshold=threshold,
            use_weighting=use_weighting,
            config_snapshot={
                "sections": sections, "timed": timed,
                "threshold": threshold, "weighted": use_weighting,
            },
            rng=self.rng,
        )
        step, payload = runner.start()
        if step == test_flow.STEP_DONE:
            # Nothing could be built — clean up the empty session we just opened.
            attempt_repo.delete_session(runner.session_id)
            return False
        self.runner = runner
        self._handle_step(step, payload)
        return True

    # ------------------------------------------------------------------ drill

    def start_drill(self, *, section, domains, count, difficulty=None, ramp=True,
                    timer_mode="Per-question stopwatch", source="fresh",
                    history_ids=None):
        """Build and launch a targeted drill."""
        seen = attempt_repo.seen_counts()

        if source != "fresh" and history_ids:
            bank = question_repo.fetch_by_ids(history_ids)
            questions = [bank[qid] for qid in history_ids if qid in bank]
            if difficulty:
                questions = [q for q in questions if q.difficulty == difficulty]
            if domains:
                questions = [q for q in questions if q.domain in domains]
            self.rng.shuffle(questions)
            questions = questions[:count]
            if ramp:
                questions.sort(key=lambda q: ({"Easy": 0, "Medium": 1, "Hard": 2}
                                              .get(q.difficulty, 1), self.rng.random()))
            for index, question in enumerate(questions, start=1):
                question.position = index
                question.module_number = 1
        else:
            questions = engine.build_drill(
                section=section, domains=domains, count=count,
                difficulty_ramp=ramp, difficulty_filter=difficulty,
                seen_counts=seen, rng=self.rng,
            )

        if not questions:
            return False

        # Official pacing gives the drill the same seconds-per-question budget
        # the real section uses.
        time_limit = 0
        if timer_mode == "Official pacing":
            per_question = PACING_TARGET_SECONDS.get(section, 80)
            time_limit = per_question * len(questions)

        label = self._drill_label(section, domains, len(questions), source)
        runner = TestRunner(
            MODE_DRILL,
            sections=[section],
            label=label,
            timed=bool(time_limit),
            drill_questions=questions,
            drill_time_limit=time_limit,
            config_snapshot={
                "section": section, "domains": domains, "count": count,
                "difficulty": difficulty, "ramp": ramp,
                "timer": timer_mode, "source": source,
            },
            rng=self.rng,
        )
        step, payload = runner.start()
        if step == test_flow.STEP_DONE:
            attempt_repo.delete_session(runner.session_id)
            return False
        self.runner = runner
        self._handle_step(step, payload,
                          per_question_timer=(timer_mode == "Per-question stopwatch"))
        return True

    @staticmethod
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

    # ----------------------------------------------------------- review pools

    def start_mistake_review(self):
        """Redrill everything you have missed or flagged, hardest gaps first."""
        ids = attempt_repo.question_ids_where(only_incorrect=True, only_flagged=True,
                                              limit=60)
        # Drop questions you have since answered correctly twice in a row.
        mastered = attempt_repo.mastered_question_ids(streak=2)
        ids = [qid for qid in ids if qid not in mastered]
        if not ids:
            self._toast_home("Nothing to review yet — or you've mastered everything "
                             "you previously missed. Sit a test or a drill first.")
            return False
        bank = question_repo.fetch_by_ids(ids)
        questions = [bank[qid] for qid in ids if qid in bank]
        return self.start_review_session(questions, label="Review — mistakes & flags")

    def start_redo_session(self, limit: int = 10):
        """
        Pass 3 of the three-pass review: redo due questions cold.

        Getting one right advances it up the ladder (next day -> +3 -> +10);
        getting it wrong resets it to the bottom, because "if you can't reproduce
        the solution, it isn't learned".
        """
        from datetime import date as _date

        today = _date.today()
        due = attempt_repo.due_redos(today.isoformat(), limit=limit)

        if due:
            ids = [d["question_id"] for d in due]
            self._redo_stages = {d["question_id"]: d["stage"] for d in due}
            label = f"Cold redo — {len(ids)} due"
        else:
            # Nothing has come due yet (a cold redo is scheduled for the day
            # AFTER the miss). Rather than bouncing the user back to the home
            # screen, fall back to their most recent unmastered misses — the
            # warm-up block in the plan is valuable either way.
            mastered = attempt_repo.mastered_question_ids(streak=2)
            ids = [q for q in attempt_repo.question_ids_where(
                only_incorrect=True, only_flagged=True, limit=limit * 3)
                if q not in mastered][:limit]
            self._redo_stages = {}
            label = f"Warm-up redo — {len(ids)} recent miss(es)"

        if not ids:
            self._toast_home("Nothing to redo yet. Finish a drill, log your misses in the "
                             "error log, and they'll be scheduled here automatically.")
            return False

        bank = question_repo.fetch_by_ids(ids)
        questions = [bank[qid] for qid in ids if qid in bank]
        if not questions:
            self._toast_home("The questions in your redo queue are no longer in the bank.")
            return False

        return self.start_review_session(questions, label=label, is_redo=True)

    def _advance_redo_queue(self, records):
        """Move each redone question up or down the +3 / +10 ladder."""
        from datetime import date as _date

        today = _date.today()
        stages = getattr(self, "_redo_stages", {}) or {}
        for record in records:
            qid = record.question.question_id
            if qid not in stages:
                continue
            stage = stages[qid]
            following = diagnostic.next_stage(stage, record.is_correct)
            due = (diagnostic.due_date_for(following, today).isoformat()
                   if following is not None else None)
            attempt_repo.complete_redo(qid, was_correct=record.is_correct,
                                       next_stage_value=following, next_due=due)
        self._redo_stages = {}

    def start_review_session(self, questions, label="Review session", is_redo=False):
        """Run an arbitrary pool of questions as a review sitting."""
        questions = list(questions)
        if not questions:
            return False
        for index, question in enumerate(questions, start=1):
            question.position = index
            question.module_number = 1

        runner = TestRunner(
            MODE_REVIEW,
            sections=[questions[0].section],
            label=label,
            timed=False,
            drill_questions=questions,
            rng=self.rng,
        )
        step, payload = runner.start()
        if step == test_flow.STEP_DONE:
            attempt_repo.delete_session(runner.session_id)
            return False
        self.runner = runner
        self._is_redo_session = is_redo
        self._handle_step(step, payload, per_question_timer=True)
        return True

    # ------------------------------------------------------------ step engine

    def _handle_step(self, step, payload, per_question_timer=False):
        """Render whatever the TestRunner says comes next."""
        if step == test_flow.STEP_MODULE:
            plan = payload
            context = self._module_context(plan)

            def submit(records, elapsed, auto):
                self._on_module_submitted(records, elapsed, auto, per_question_timer)

            self._swap(lambda parent, controller: QuizScreen(
                parent, controller, plan,
                timed=bool(plan.time_limit_seconds) and (self.runner.timed if self.runner else True),
                on_submit=submit, context_label=context,
                per_question_timer=per_question_timer,
                is_redo_session=getattr(self, "_is_redo_session", False),
            ))

        elif step == test_flow.STEP_BREAK:
            info = payload

            def resume():
                if self.runner is None:
                    self.show_home_screen()
                    return
                next_step, next_payload = self.runner.resume()
                self._handle_step(next_step, next_payload, per_question_timer)

            self._swap(lambda parent, controller: ModuleBreakScreen(
                parent, controller, info, resume))

        else:  # STEP_DONE
            summary = payload
            self.runner = None
            self._swap(lambda parent, controller: ReviewScreen(parent, controller, summary))

    def _module_context(self, plan) -> str:
        runner = self.runner
        if runner is None or runner.mode in (MODE_DRILL, MODE_REVIEW):
            return runner.label if runner else plan.section
        total = 2
        return f"{plan.section} — Module {plan.module_number} of {total}"

    def _on_module_submitted(self, records, elapsed, auto, per_question_timer):
        if self.runner is None:
            self.show_home_screen()
            return
        if getattr(self, "_is_redo_session", False):
            self._advance_redo_queue(records)
            self._is_redo_session = False
        step, payload = self.runner.submit_module(records, elapsed)
        self._handle_step(step, payload, per_question_timer)

    def abandon_current_test(self):
        """Leave a sitting early but keep the answers already given."""
        if self.runner is not None:
            try:
                self.runner.abandon()
            except Exception:
                traceback.print_exc()
            self.runner = None
        self.show_home_screen()

    # ---------------------------------------------------------------- history

    def show_review_from_history(self, session_id: int):
        """Reopen any past sitting in the full review screen."""
        summary = test_flow.summary_from_history(session_id)
        self._swap(lambda parent, controller: ReviewScreen(
            parent, controller, summary, from_history=True))

    # ----------------------------------------------------------------- misc

    def _toast_home(self, message: str):
        """Show a message on the home screen (used when an action can't run)."""
        self.show_home_screen()
        frame = self.current_frame
        if frame is not None and hasattr(frame, "bank_ok"):
            try:
                import ui_kit as ui
                ui.caption(frame, message, size=12, color=C.ORANGE).pack(pady=(0, 10))
            except Exception:
                print(message)

    def _on_close(self):
        """Don't lose answers, and don't leave threads or connections behind."""
        if self.runner is not None:
            try:
                self.runner.abandon()
            except Exception:
                pass
        for cleanup in (background.shutdown, database.close_pool, ui.clear_image_cache):
            try:
                cleanup()
            except Exception:
                pass
        self.destroy()


def main():
    try:
        app = SATApp()
    except Exception:
        traceback.print_exc()
        print("\nCat SAT could not start. If this is the first run, make sure "
              "customtkinter and Pillow are installed:\n"
              "    pip install customtkinter pillow pymupdf\n")
        return 1
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
