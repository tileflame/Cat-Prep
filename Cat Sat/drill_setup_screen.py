"""
drill_setup_screen.py, build a targeted drill.

This is the screen the original app was missing: pick one or more domains, say
how many questions, and get them ordered Easy → Medium → Hard with an optional
per-question stopwatch.

You can also point a drill at questions you've already missed or flagged, which
turns the whole thing into a spaced-repetition loop.
"""

from __future__ import annotations

import customtkinter as ctk

import attempt_repo
import question_repo
import ui_kit as ui
from config import C, DRILL_DEFAULT_COUNT, DRILL_PRESETS, SECTION_MATH, SECTION_RW
from database import get_setting, set_setting

SOURCES = [
    ("Fresh questions", "fresh", "Prefer questions you have never seen"),
    ("My mistakes", "missed", "Only questions you have answered incorrectly"),
    ("Flagged", "flagged", "Only questions you flagged during a test"),
    ("Mistakes + flagged", "both", "Everything worth a second look"),
]


class DrillSetupScreen(ctk.CTkFrame):

    def __init__(self, parent, controller):
        super().__init__(parent, fg_color="transparent")
        self.controller = controller
        self.domain_vars: dict[str, ctk.StringVar] = {}
        self._domain_counts = None
        self._domain_difficulty = None

        header = ui.row(self)
        header.pack(fill="x", padx=30, pady=(22, 6))
        ui.title(header, "🎓  Targeted Drill", size=25).pack(side="left")
        ui.secondary_button(header, "← Back", controller.show_home_screen,
                            width=100).pack(side="right")

        ui.caption(self,
                   "Questions are ordered easiest to hardest inside each domain, "
                   "the same way College Board sequences them.",
                   size=12).pack(anchor="w", padx=32, pady=(0, 12))

        # IMPORTANT: the footer is packed BEFORE the expanding body, anchored to
        # the bottom. Tk's pack gives space to widgets in packing order, so if the
        # body went first it would eat the whole cavity on a short window and push
        # the Start button off-screen. Bottom chrome first, always.
        footer = ui.row(self)
        footer.pack(side="bottom", fill="x", padx=30, pady=(6, 18))
        ui.primary_button(footer, "🚀  Start drill", self.start, width=240).pack(side="right")
        ui.secondary_button(footer, "Cancel", controller.show_home_screen,
                            width=120).pack(side="right", padx=10)

        self.status = ui.caption(self, "", size=12, color=C.ORANGE)
        self.status.pack(side="bottom", pady=(4, 0))

        body = ui.row(self)
        body.pack(side="top", fill="both", expand=True, padx=26, pady=(0, 4))

        self._left_column(body)
        self._domain_column(body)

        self._reload_domains()

    # ------------------------------------------------------------ left column

    def _left_column(self, parent):
        # Scrollable so the options stay reachable on a small window or at 150%
        # Windows display scaling, instead of being clipped.
        card = ctk.CTkScrollableFrame(parent, fg_color=C.SURFACE, corner_radius=14)
        card.pack(side="left", fill="both", expand=True, padx=(0, 6))

        ui.title(card, "Setup", size=17).pack(anchor="w", padx=22, pady=(18, 10))

        # --- Section
        ui.caption(card, "SECTION", size=10).pack(anchor="w", padx=22)
        sections = question_repo.list_sections() or [SECTION_RW, SECTION_MATH]
        self.section_var = ctk.StringVar(value=get_setting("last_drill_section", sections[0]))
        if self.section_var.get() not in sections:
            self.section_var.set(sections[0])
        ctk.CTkOptionMenu(card, variable=self.section_var, values=sections,
                          font=ui.f(13), fg_color=C.SURFACE_2, button_color=C.SLATE,
                          height=36, command=lambda _: self._reload_domains()).pack(
            fill="x", padx=22, pady=(4, 14))

        # --- Count
        ui.caption(card, "HOW MANY QUESTIONS", size=10).pack(anchor="w", padx=22)
        self.count_var = ctk.StringVar(value=get_setting("last_drill_count",
                                                         str(DRILL_DEFAULT_COUNT)))
        ctk.CTkComboBox(card, variable=self.count_var,
                        values=[str(n) for n in DRILL_PRESETS], font=ui.f(13),
                        fg_color=C.SURFACE_2, button_color=C.SLATE, height=36).pack(
            fill="x", padx=22, pady=(4, 14))

        # --- Source pool
        ui.caption(card, "DRAW FROM", size=10).pack(anchor="w", padx=22)
        self.source_var = ctk.StringVar(value="fresh")
        for label, value, detail in SOURCES:
            holder = ui.row(card)
            holder.pack(fill="x", padx=22, pady=1)
            ctk.CTkRadioButton(
                holder, text=label, variable=self.source_var, value=value,
                font=ui.f(12, "bold"), text_color=C.TEXT,
                radiobutton_width=17, radiobutton_height=17,
                fg_color=C.BLUE, hover_color=C.BLUE_DARK,
                command=self._update_availability,
            ).pack(side="left")
            ui.caption(holder, detail, size=10).pack(side="left", padx=10)

        # --- Difficulty handling
        ui.caption(card, "DIFFICULTY", size=10).pack(anchor="w", padx=22, pady=(14, 0))
        self.ramp_var = ctk.StringVar(value="on")
        ctk.CTkCheckBox(card, text="Ramp Easy → Medium → Hard",
                        variable=self.ramp_var, onvalue="on", offvalue="off",
                        font=ui.f(12), text_color=C.TEXT_DIM,
                        checkbox_width=18, checkbox_height=18,
                        fg_color=C.GREEN, hover_color=C.GREEN_DARK).pack(
            anchor="w", padx=22, pady=(4, 2))

        self.difficulty_var = ctk.StringVar(value="All difficulties")
        ctk.CTkOptionMenu(card, variable=self.difficulty_var,
                          values=["All difficulties", "Easy", "Medium", "Hard"],
                          font=ui.f(13), fg_color=C.SURFACE_2, button_color=C.SLATE,
                          height=34, command=lambda _: self._update_availability()).pack(
            fill="x", padx=22, pady=(4, 14))

        # --- Timer
        ui.caption(card, "TIMER", size=10).pack(anchor="w", padx=22)
        self.timer_var = ctk.StringVar(value=get_setting("last_drill_timer",
                                                          "Per-question stopwatch"))
        ctk.CTkOptionMenu(
            card, variable=self.timer_var,
            values=["Per-question stopwatch", "Official pacing", "No timer"],
            font=ui.f(13), fg_color=C.SURFACE_2, button_color=C.SLATE, height=36,
        ).pack(fill="x", padx=22, pady=(4, 4))
        ui.caption(card,
                   "Stopwatch counts up per question and records the time either way. "
                   "Official pacing gives you the real seconds-per-question budget.",
                   size=10).pack(anchor="w", padx=22, pady=(0, 18))

    # ---------------------------------------------------------- domain column

    def _domain_column(self, parent):
        card = ui.card(parent)
        card.pack(side="left", fill="both", expand=True, padx=(6, 0))

        head = ui.row(card)
        head.pack(fill="x", padx=22, pady=(18, 6))
        ui.title(head, "Domains", size=17).pack(side="left")
        ui.ghost_button(head, "All", self._select_all, width=52).pack(side="right", padx=2)
        ui.ghost_button(head, "None", self._select_none, width=58).pack(side="right", padx=2)

        weakest = attempt_repo.weakest("domain", min_attempts=4, limit=1)
        if weakest:
            ui.ghost_button(head, "⚡ Weakest",
                            lambda: self._select_only(weakest[0]["bucket"]),
                            width=88).pack(side="right", padx=2)

        self.domain_holder = ctk.CTkScrollableFrame(card, fg_color="transparent")
        self.domain_holder.pack(fill="both", expand=True, padx=18, pady=(0, 8))

        self.availability = ui.caption(card, "", size=11)
        self.availability.pack(anchor="w", padx=22, pady=(0, 16))

    def _reload_domains(self):
        for widget in self.domain_holder.winfo_children():
            widget.destroy()
        self.domain_vars = {}

        section = self.section_var.get()
        domains = question_repo.list_domains(section)
        if not domains:
            ui.body(self.domain_holder, "No domains found for this section.",
                    size=12).pack(anchor="w", pady=10)
            self._update_availability()
            return

        # Two grouped queries instead of one per domain. The old version called
        # count_available() inside the loop — eight full-table scans on eight
        # separate connections, every time you switched section.
        history = {b["bucket"]: b for b in attempt_repo.breakdown("domain", section=section)}
        self._domain_counts = question_repo.domain_counts(section)
        self._domain_difficulty = question_repo.domain_difficulty_counts(section)

        for domain in domains:
            var = ctk.StringVar(value="off")
            self.domain_vars[domain] = var

            line = ui.row(self.domain_holder)
            line.pack(fill="x", pady=3)

            ctk.CTkCheckBox(
                line, text=domain, variable=var, onvalue="on", offvalue="off",
                font=ui.f(13), text_color=C.TEXT,
                checkbox_width=19, checkbox_height=19,
                fg_color=C.BLUE, hover_color=C.BLUE_DARK,
                command=self._update_availability,
            ).pack(side="left")

            stat = history.get(domain)
            if stat and stat["total"]:
                ui.pill(line, f"{stat['accuracy']:.0f}%",
                        ui.accuracy_color(stat["accuracy"])).pack(side="right")
            available = self._domain_counts.get(domain, 0)
            ui.caption(line, f"{available} in bank", size=10).pack(side="right", padx=8)

        # Default to the weakest domain if there's history, otherwise the first.
        weakest = attempt_repo.weakest("domain", min_attempts=4, limit=1)
        preferred = weakest[0]["bucket"] if weakest and weakest[0]["bucket"] in self.domain_vars\
            else domains[0]
        self.domain_vars[preferred].set("on")
        self._update_availability()

    def _select_all(self):
        for var in self.domain_vars.values():
            var.set("on")
        self._update_availability()

    def _select_none(self):
        for var in self.domain_vars.values():
            var.set("off")
        self._update_availability()

    def _select_only(self, domain):
        for name, var in self.domain_vars.items():
            var.set("on" if name == domain else "off")
        self._update_availability()

    def _selected_domains(self) -> list[str]:
        return [name for name, var in self.domain_vars.items() if var.get() == "on"]

    def _update_availability(self):
        """Tell the user how big a drill they can actually build right now."""
        section = self.section_var.get()
        domains = self._selected_domains()
        difficulty = self.difficulty_var.get()
        difficulty = None if difficulty == "All difficulties" else difficulty

        if self.source_var.get() != "fresh":
            pool = self._history_pool()
            self.availability.configure(
                text=f"{len(pool)} question(s) available from your history",
                text_color=C.TEXT_DIM if pool else C.ORANGE,
            )
            return

        counts = getattr(self, "_domain_counts", None)
        by_difficulty = getattr(self, "_domain_difficulty", None)
        if counts is None:
            counts = self._domain_counts = question_repo.domain_counts(section)
        if by_difficulty is None:
            by_difficulty = self._domain_difficulty =\
                question_repo.domain_difficulty_counts(section)

        target_domains = domains or list(counts)
        if difficulty:
            total = sum(by_difficulty.get((d, difficulty), 0) for d in target_domains)
        else:
            total = sum(counts.get(d, 0) for d in target_domains)
        self.availability.configure(
            text=f"{total} question(s) match this filter",
            text_color=C.TEXT_DIM if total else C.ORANGE,
        )

    def _history_pool(self) -> list[str]:
        source = self.source_var.get()
        domains = self._selected_domains()
        section = self.section_var.get()
        ids: list[str] = []
        targets = domains or [None]
        for domain in targets:
            ids.extend(attempt_repo.question_ids_where(
                only_incorrect=source in ("missed", "both"),
                only_flagged=source in ("flagged", "both"),
                section=section, domain=domain,
            ))
        seen, unique = set(), []
        for qid in ids:
            if qid not in seen:
                seen.add(qid)
                unique.append(qid)
        return unique

    # ------------------------------------------------------------------ start

    def start(self):
        section = self.section_var.get()
        domains = self._selected_domains()
        raw_count = self.count_var.get().strip()

        # The old app silently ran the entire bank if you typed something odd here.
        if not raw_count.isdigit() or int(raw_count) <= 0:
            self.status.configure(text="⚠ Enter a whole number of questions (e.g. 8).")
            return
        count = min(int(raw_count), 200)

        difficulty = self.difficulty_var.get()
        difficulty = None if difficulty == "All difficulties" else difficulty
        ramp = self.ramp_var.get() == "on"
        timer_mode = self.timer_var.get()

        set_setting("last_drill_section", section)
        set_setting("last_drill_count", str(count))
        set_setting("last_drill_timer", timer_mode)

        started = self.controller.start_drill(
            section=section, domains=domains, count=count,
            difficulty=difficulty, ramp=ramp, timer_mode=timer_mode,
            source=self.source_var.get(),
            history_ids=self._history_pool() if self.source_var.get() != "fresh" else None,
        )
        if not started:
            self.status.configure(
                text="⚠ No questions matched. Try more domains, a different difficulty, "
                     "or switch back to fresh questions."
            )
