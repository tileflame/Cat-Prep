"""
plan_screen.py — Today's Plan.

Answers one question the moment you open the app: what am I doing right now?

Everything on this screen comes from study_plan.py, which is the SAT Command
Center document turned into data. The buttons launch the exact drill the plan
asks for, pre-filtered — so "20 Transitions questions" is one click, not five
dropdowns.
"""

from __future__ import annotations

from datetime import date, timedelta

import customtkinter as ctk

import attempt_repo
import diagnostic
import study_plan
import ui_kit as ui
from config import C, accuracy_color

ACTION_STYLE = {
    "drill": ("▶  Drill", C.GREEN),
    "module": ("▶  Module", C.BLUE),
    "redo": ("▶  Redo", C.PURPLE),
    "review": ("▶  Error log", C.ORANGE),
    "manual": ("Mark done", C.SURFACE_2),
}


class PlanScreen(ctk.CTkFrame):

    def __init__(self, parent, controller, day=None):
        super().__init__(parent, fg_color="transparent")
        self.controller = controller
        self.day = day or date.today()
        self.day_key = self.day.isoformat()
        self._task_widgets = {}
        self._progress_bar = None
        self._progress_label = None

        self._header()

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.pack(side="top", fill="both", expand=True, padx=26, pady=(0, 16))

        self.refresh()

    # ----------------------------------------------------------------- header

    def _header(self):
        header = ui.row(self)
        header.pack(fill="x", padx=30, pady=(20, 4))

        left = ui.row(header)
        left.pack(side="left")
        ui.title(left, "📋  Today's Plan", size=25).pack(anchor="w")
        ui.caption(left, self.day.strftime("%A, %B %d, %Y").replace(" 0", " "),
                   size=12).pack(anchor="w")

        right = ui.row(header)
        right.pack(side="right")
        ui.secondary_button(right, "🔬 Error log", self.controller.show_error_log,
                            width=130).pack(side="left", padx=5)
        ui.secondary_button(right, "🏠 Home", self.controller.show_home_screen,
                            width=110).pack(side="left", padx=5)

        nav = ui.row(header)
        nav.pack(side="right", padx=14)
        ui.ghost_button(nav, "◄", lambda: self._shift(-1), width=34).pack(side="left", padx=2)
        ui.ghost_button(nav, "Today", self._today, width=62).pack(side="left", padx=2)
        ui.ghost_button(nav, "►", lambda: self._shift(1), width=34).pack(side="left", padx=2)

    def _shift(self, days):
        self.controller.show_plan(self.day + timedelta(days=days))

    def _today(self):
        self.controller.show_plan(date.today())

    # ---------------------------------------------------------------- refresh

    def refresh(self):
        for widget in self.scroll.winfo_children():
            widget.destroy()
        self._task_widgets = {}
        self._progress_bar = None
        self._progress_label = None

        self._countdown()
        self._week_card()
        self._redo_card()
        self._task_list()
        self._targets_card()
        self._tiers_card()

    # -------------------------------------------------------------- countdown

    def _countdown(self):
        strip = ui.row(self.scroll)
        strip.pack(fill="x", pady=(6, 10))

        test = study_plan.active_next_test(self.day)
        days = study_plan.active_days_until_next_test(self.day)
        if test:
            urgency = C.RED if days <= 3 else (C.ORANGE if days <= 7 else C.GREEN)
            value = "TODAY" if days == 0 else f"{days} day{'s' if days != 1 else ''}"
            tile, _, _ = ui.stat_tile(strip, f"Until {test['label']}", value, urgency,
                                      f"{test['date'].strftime('%b %d')} · {test['note']}")
            tile.pack(side="left", fill="both", expand=True, padx=4)

        week = study_plan.active_week_for(self.day)
        if week:
            tile, _, _ = ui.stat_tile(strip, "Week", f"{week['number']} · {week['kind']}",
                                      C.BLUE, week["title"])
            tile.pack(side="left", fill="both", expand=True, padx=4)

        streak = attempt_repo.plan_streak(self.day_key)
        tile, _, _ = ui.stat_tile(strip, "Streak", f"{streak}d",
                                  C.GREEN if streak else C.TEXT_FAINT,
                                  "consecutive days with work logged")
        tile.pack(side="left", fill="both", expand=True, padx=4)

        counts = attempt_repo.redo_counts(self.day_key)
        tile, _, _ = ui.stat_tile(strip, "Redos due", counts["due"],
                                  C.ORANGE if counts["due"] else C.TEXT_FAINT,
                                  f"{counts['upcoming']} scheduled later")
        tile.pack(side="left", fill="both", expand=True, padx=4)

        tile, _, _ = ui.stat_tile(strip, "Target", study_plan.active_target() or "—", C.PURPLE,
                                  "superscore")
        tile.pack(side="left", fill="both", expand=True, padx=4)

    # ------------------------------------------------------------- week focus

    def _week_card(self):
        week = study_plan.active_week_for(self.day)
        if not week:
            card = ui.card(self.scroll)
            card.pack(fill="x", pady=(0, 10))
            ui.title(card, "Outside your plan", size=16).pack(
                anchor="w", padx=20, pady=(16, 4))
            # Never hardcode a date window here: the plan is built from THIS
            # user's own test dates, so any fixed date range here is both wrong
            # for everyone else and a leak of whoever's dates got baked in.
            weeks = study_plan.active_weeks()
            span = (f"Your plan covers {weeks[0]['start']:%b %-d} to {weeks[-1]['end']:%b %-d, %Y}. "
                    if weeks else "You have no plan window yet — run setup_profile.py. ")
            ui.body(card, span + "Use the arrows above to look at a day inside it.",
                    size=12).pack(anchor="w", padx=20, pady=(0, 16))
            return

        card = ui.card(self.scroll)
        card.pack(fill="x", pady=(0, 10))

        head = ui.row(card)
        head.pack(fill="x", padx=20, pady=(16, 4))
        ui.title(head, f"Week {week['number']} — {week['title']}", size=17).pack(side="left")
        ui.pill(head, week["kind"].upper(),
                C.ORANGE if week["kind"] == "taper" else C.GREEN).pack(side="left", padx=10)
        ui.caption(head, f"{week['hours']}h total  ·  Math {week['math_hours']}h  ·  "
                         f"R&W {week['rw_hours']}h", size=11).pack(side="right")

        ctk.CTkLabel(card, text=week["summary"], font=ui.f(13), text_color=C.TEXT,
                     wraplength=900, justify="left", anchor="w").pack(
            anchor="w", padx=20, pady=(2, 8))

        for bullet in week["bullets"]:
            ctk.CTkLabel(card, text="•  " + bullet, font=ui.f(12), text_color=C.TEXT_DIM,
                         wraplength=880, justify="left", anchor="w").pack(
                anchor="w", padx=26, pady=1)

        focus = ui.row(card)
        focus.pack(fill="x", padx=20, pady=(10, 16))
        ui.caption(focus, "FOCUS:", size=10).pack(side="left", padx=(0, 8))
        for domain in week.get("focus_domains", []):
            priority = study_plan.active_priorities().get(domain, {})
            ui.pill(focus, f"{domain} — {priority.get('verdict', '')}", C.AMBER).pack(
                side="left", padx=3)

    # -------------------------------------------------------------- redo card

    def _redo_card(self):
        counts = attempt_repo.redo_counts(self.day_key)
        if not counts["due"]:
            return

        card = ui.card(self.scroll, fg_color="#241C2E")
        card.pack(fill="x", pady=(0, 10))

        line = ui.row(card)
        line.pack(fill="x", padx=20, pady=16)

        block = ui.row(line)
        block.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(block, text=f"🔁  {counts['due']} cold redo(s) due",
                     font=ui.f(15, "bold"), text_color=C.PURPLE, anchor="w").pack(anchor="w")
        ui.caption(block,
                   "From scratch, no notes. If you can't reproduce the solution it isn't "
                   "learned — recognition is not recall.",
                   size=11).pack(anchor="w", pady=(2, 0))

        ctk.CTkButton(line, text="Start redo session", font=ui.f(13, "bold"),
                      fg_color=C.PURPLE, hover_color=C.PURPLE_DARK, text_color="#FFFFFF",
                      width=170, height=40, corner_radius=10,
                      command=lambda: self.controller.start_redo_session()).pack(side="right")

    # -------------------------------------------------------------- task list

    def _task_list(self):
        plan = study_plan.active_day_plan(self.day)
        done = attempt_repo.plan_done_keys(self.day_key)

        card = ui.card(self.scroll)
        card.pack(fill="x", pady=(0, 10))

        head = ui.row(card)
        head.pack(fill="x", padx=20, pady=(16, 2))
        ui.title(head, plan["headline"], size=17).pack(side="left")
        ui.caption(head, plan.get("hours", ""), size=12).pack(side="right")

        tasks = plan.get("tasks", [])
        if not tasks:
            ui.body(card, "Nothing scheduled for this date.", size=12).pack(
                anchor="w", padx=20, pady=(4, 16))
            return

        completed = sum(1 for i, _ in enumerate(tasks) if self._key(i) in done)
        self._progress_bar = ctk.CTkProgressBar(
            card, height=7, corner_radius=4, fg_color=C.SURFACE_3,
            progress_color=accuracy_color(completed / len(tasks) * 100))
        self._progress_bar.pack(fill="x", padx=20, pady=(6, 2))
        self._progress_bar.set(completed / len(tasks))
        self._progress_label = ui.caption(card, f"{completed} of {len(tasks)} done", size=11)
        self._progress_label.pack(anchor="w", padx=20, pady=(0, 8))

        for index, task in enumerate(tasks):
            self._task_row(card, index, task, self._key(index) in done)

        ui.caption(card, study_plan.NEVER_CUT, size=11, color=C.ORANGE).pack(
            anchor="w", padx=20, pady=(8, 16))

    def _key(self, index):
        return f"t{index}"

    def _task_row(self, parent, index, task, is_done):
        row = ui.card(parent, fg_color=C.SURFACE_2 if not is_done else C.SURFACE_3,
                      corner_radius=10)
        row.pack(fill="x", padx=16, pady=4)
        handles = {"row": row, "done": is_done, "detail": None}
        self._task_widgets[index] = handles

        left = ui.row(row)
        left.pack(side="left", fill="x", expand=True, padx=14, pady=12)

        title_line = ui.row(left)
        title_line.pack(anchor="w", fill="x")

        check = ctk.CTkButton(
            title_line, text="✓" if is_done else "○", font=ui.f(14, "bold"),
            width=32, height=28, corner_radius=8,
            fg_color=C.GREEN if is_done else C.SURFACE_3,
            hover_color=C.GREEN_DARK,
            text_color="#0B1F14" if is_done else C.TEXT_FAINT,
            command=lambda i=index: self._toggle(i, not self._task_widgets[i]["done"]),
        )
        check.pack(side="left", padx=(0, 10))
        handles["check"] = check

        if task["minutes"]:
            ui.pill(title_line, f"{task['minutes']} min", C.TEXT_FAINT).pack(side="left",
                                                                            padx=(0, 8))
        title_label = ctk.CTkLabel(title_line, text=task["label"], font=ui.f(14, "bold"),
                                   text_color=C.TEXT_FAINT if is_done else C.TEXT,
                                   anchor="w")
        title_label.pack(side="left")
        handles["title"] = title_label

        if task.get("detail"):
            detail = ctk.CTkLabel(left, text=task["detail"], font=ui.f(12),
                                  text_color=C.TEXT_FAINT if is_done else C.TEXT_DIM,
                                  wraplength=760, justify="left", anchor="w")
            detail.pack(anchor="w", padx=(42, 0), pady=(3, 0))
            handles["detail"] = detail

        action = task.get("action", "manual")
        if action != "manual":
            label, color = ACTION_STYLE[action]
            ctk.CTkButton(row, text=label, font=ui.f(12, "bold"),
                          fg_color=color, hover_color=C.SLATE_DARK,
                          text_color="#0B1F14" if color != C.SURFACE_2 else C.TEXT,
                          width=110, height=36, corner_radius=8,
                          command=lambda t=task, i=index: self._launch(t, i)).pack(
                side="right", padx=14)

    def _toggle(self, index, done):
        """
        Update just this row and the progress bar.

        The old version called refresh(), which tore down and rebuilt the whole
        screen — roughly 300 widgets and 17 database connections — to tick one
        checkbox.
        """
        attempt_repo.set_plan_task(self.day_key, self._key(index), done)
        widgets = self._task_widgets.get(index)
        if not widgets:
            self.refresh()
            return

        widgets["check"].configure(
            text="✓" if done else "○",
            fg_color=C.GREEN if done else C.SURFACE_3,
            text_color="#0B1F14" if done else C.TEXT_FAINT,
        )
        widgets["row"].configure(fg_color=C.SURFACE_3 if done else C.SURFACE_2)
        widgets["title"].configure(text_color=C.TEXT_FAINT if done else C.TEXT)
        if widgets.get("detail") is not None:
            widgets["detail"].configure(
                text_color=C.TEXT_FAINT if done else C.TEXT_DIM)
        widgets["done"] = done
        self._update_task_progress()

    def _update_task_progress(self):
        total = len(self._task_widgets)
        if not total or self._progress_bar is None:
            return
        completed = sum(1 for w in self._task_widgets.values() if w["done"])
        fraction = completed / total
        self._progress_bar.set(fraction)
        self._progress_bar.configure(progress_color=accuracy_color(fraction * 100))
        if self._progress_label is not None:
            self._progress_label.configure(text=f"{completed} of {total} done")

    def _launch(self, task, index):
        """Run the plan's task through the app, pre-filtered."""
        action = task["action"]
        params = task.get("params", {})

        # Tick it off optimistically — you're doing it now.
        attempt_repo.set_plan_task(self.day_key, self._key(index), True)
        handles = self._task_widgets.get(index)
        if handles:
            handles["done"] = True

        if action == "drill":
            self.controller.start_drill(
                section=params.get("section"),
                domains=params.get("domains") or [],
                count=params.get("count", 20),
                difficulty=params.get("difficulty"),
                ramp=True,
                timer_mode="Per-question stopwatch",
                source="fresh",
            )
        elif action == "module":
            self.controller.start_adaptive_test(
                mode="section_test", sections=[params.get("section")], timed=True)
        elif action == "redo":
            self.controller.start_redo_session(limit=params.get("count", 10))
        elif action == "review":
            self.controller.show_error_log()

    # ------------------------------------------------------------ drill targets

    def _targets_card(self):
        active = attempt_repo.active_targets()
        suggestions = diagnostic.suggest_targets(
            attempt_repo.miss_counts_by_type(since_days=7),
            [t["label"] for t in active])

        if not active and not suggestions:
            return

        card = ui.card(self.scroll)
        card.pack(fill="x", pady=(0, 10))

        head = ui.row(card)
        head.pack(fill="x", padx=20, pady=(16, 4))
        ui.title(head, "Drill targets", size=17).pack(side="left")
        ui.caption(head, f"max {diagnostic.MAX_ACTIVE_TARGETS} at a time · retire at "
                         f"{diagnostic.RETIRE_ACCURACY:.0f}% on a fresh "
                         f"{diagnostic.RETIRE_SAMPLE}", size=11).pack(side="left", padx=10)

        for target in active:
            progress = attempt_repo.target_progress(target["label"], target["axis"])
            ok, message = diagnostic.can_retire(progress["accuracy"], progress["sample"])

            block = ui.card(card, fg_color=C.SURFACE_2, corner_radius=10)
            block.pack(fill="x", padx=16, pady=4)

            line = ui.row(block)
            line.pack(fill="x", padx=14, pady=12)

            text_block = ui.row(line)
            text_block.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(text_block, text=target["label"], font=ui.f(14, "bold"),
                         text_color=C.TEXT, anchor="w").pack(anchor="w")
            ui.caption(text_block, message, size=11,
                       color=C.GREEN if ok else C.TEXT_DIM).pack(anchor="w", pady=(2, 0))

            ctk.CTkLabel(line, text=f"{progress['accuracy']:.0f}%", font=ui.f(18, "bold"),
                         text_color=accuracy_color(progress["accuracy"])).pack(
                side="right", padx=12)

            if ok:
                ctk.CTkButton(line, text="Retire", font=ui.f(12, "bold"),
                              fg_color=C.GREEN, hover_color=C.GREEN_DARK,
                              text_color="#0B1F14", width=90, height=34, corner_radius=8,
                              command=lambda t=target, p=progress:
                                  self._retire(t, p)).pack(side="right")
            else:
                ctk.CTkButton(line, text="Drill it", font=ui.f(12, "bold"),
                              fg_color=C.BLUE, hover_color=C.BLUE_DARK,
                              text_color="#FFFFFF", width=90, height=34, corner_radius=8,
                              command=lambda t=target: self._drill_target(t)).pack(side="right")

        for label in suggestions:
            block = ui.row(card)
            block.pack(fill="x", padx=20, pady=4)
            ctk.CTkLabel(block,
                         text=f"⚠  {label} has 3+ misses this week — the plan says make it "
                              "a target.",
                         font=ui.f(12), text_color=C.ORANGE, anchor="w").pack(side="left")
            ctk.CTkButton(block, text="Add target", font=ui.f(11, "bold"),
                          fg_color=C.SURFACE_2, hover_color=C.SLATE, text_color=C.TEXT,
                          width=100, height=30, corner_radius=8,
                          command=lambda l=label: self._add_target(l)).pack(side="right")

        ui.caption(card, "", size=4).pack(pady=(0, 12))

    def _add_target(self, label):
        attempt_repo.add_target(label)
        self.refresh()

    def _retire(self, target, progress):
        attempt_repo.retire_target(target["target_id"], progress["accuracy"])
        self.refresh()

    def _drill_target(self, target):
        section = "Reading and Writing"
        if target["label"] in ("Algebra", "Advanced Math",
                               "Problem-Solving and Data Analysis",
                               "Geometry and Trigonometry"):
            section = "Math"
        self.controller.start_drill(section=section, domains=[target["label"]],
                                    count=15, ramp=True,
                                    timer_mode="Per-question stopwatch", source="fresh")

    # -------------------------------------------------------------- priorities

    def _tiers_card(self):
        card = ui.card(self.scroll)
        card.pack(fill="x", pady=(0, 16))

        ui.title(card, "Ruthless prioritisation", size=17).pack(
            anchor="w", padx=20, pady=(16, 2))
        ctk.CTkLabel(card, text=study_plan.THE_WHOLE_PLAN, font=ui.f(12),
                     text_color=C.TEXT, wraplength=900, justify="left",
                     anchor="w").pack(anchor="w", padx=20, pady=(2, 10))

        colors = {"GREEN": C.GREEN, "BLUE": C.BLUE, "AMBER": C.AMBER, "RED": C.RED}
        for number, tier in study_plan.TIERS.items():
            color = colors[tier["color_key"]]
            line = ui.row(card)
            line.pack(fill="x", padx=20, pady=(8, 2))
            ui.pill(line, f"TIER {number}", color).pack(side="left")
            ctk.CTkLabel(line, text=tier["label"], font=ui.f(13, "bold"),
                         text_color=color).pack(side="left", padx=8)

            # Tier 4 is the one people ignore, so it stays fully expanded.
            items = tier["items"] if number in (1, 4) else tier["items"][:3]
            for item in items:
                ctk.CTkLabel(card, text=("✗  " if number == 4 else "•  ") + item,
                             font=ui.f(11),
                             text_color=C.TEXT_DIM if number != 4 else C.TEXT_FAINT,
                             wraplength=880, justify="left", anchor="w").pack(
                    anchor="w", padx=32, pady=1)
            if len(items) < len(tier["items"]):
                ui.caption(card, f"   … and {len(tier['items']) - len(items)} more",
                           size=10).pack(anchor="w", padx=32)

        ui.caption(card, "", size=4).pack(pady=(0, 12))
