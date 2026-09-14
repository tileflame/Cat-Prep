"""
home_screen.py — the hub.

The old home screen was a single configuration form, which meant everything the
app could do had to be squeezed into five dropdowns. Now it's a launcher: pick
what kind of work you're doing, and the setup screen for that mode asks only the
questions that mode needs.

It also tells you the truth about your question bank up front — how many
questions are imported and whether that's enough for a real adaptive test —
instead of failing with "No questions found" after you press start.
"""

from __future__ import annotations

import customtkinter as ctk

import attempt_repo
import question_repo
import ui_kit as ui
from config import BLUEPRINT, C, SECTION_MATH, SECTION_RW
from database import get_setting, question_bank_is_usable, set_setting


class HomeScreen(ctk.CTkFrame):

    def __init__(self, parent, controller):
        super().__init__(parent, fg_color="transparent")
        self.controller = controller

        self._header()
        self._bank_banner()
        # Stats strip is anchored to the bottom and packed BEFORE the mode cards,
        # so a short window shrinks the cards instead of hiding the strip.
        self._stats_strip()
        self._mode_cards()

    # ----------------------------------------------------------------- header

    def _header(self):
        header = ui.row(self)
        header.pack(fill="x", padx=30, pady=(22, 8))

        left = ui.row(header)
        left.pack(side="left")
        ctk.CTkLabel(left, text="🐱", font=ui.f(34)).pack(side="left", padx=(0, 12))
        block = ui.row(left)
        block.pack(side="left")
        ui.title(block, "Cat SAT", size=30).pack(anchor="w")
        ui.caption(block, "Adaptive practice built on the official question bank",
                   size=12).pack(anchor="w")

        right = ui.row(header)
        right.pack(side="right")

        goal_card = ui.card(right, fg_color=C.SURFACE, corner_radius=10)
        goal_card.pack(side="right")
        ui.caption(goal_card, "TARGET SCORE", size=10).pack(anchor="w", padx=16, pady=(10, 0))
        self.goal_var = ctk.StringVar(value=get_setting("target_score", "1500"))
        entry = ctk.CTkEntry(goal_card, textvariable=self.goal_var, width=80,
                             font=ui.f(18, "bold"), fg_color=C.SURFACE_3,
                             text_color=C.GREEN, border_width=0, justify="center")
        entry.pack(padx=16, pady=(0, 10))
        self.goal_var.trace_add("write", lambda *a: self._save_goal())

    def _save_goal(self):
        value = self.goal_var.get().strip()
        if value.isdigit() and 400 <= int(value) <= 1600:
            set_setting("target_score", value)

    # ------------------------------------------------------------ bank status

    def _bank_banner(self):
        ok, message = question_bank_is_usable()
        summary = question_repo.bank_summary() if ok else {"by_section": {}, "total": 0}

        banner = ui.card(self, fg_color=C.SURFACE if ok else "#2A1A1A", corner_radius=12)
        banner.pack(fill="x", padx=30, pady=(4, 10))

        line = ui.row(banner)
        line.pack(fill="x", padx=18, pady=12)

        ctk.CTkLabel(line, text="📚" if ok else "⚠️", font=ui.f(18)).pack(side="left", padx=(0, 10))

        text_block = ui.row(line)
        text_block.pack(side="left", fill="x", expand=True)

        if ok:
            counts = summary["by_section"]
            parts = [f"{name}: {counts.get(name, 0):,}" for name in (SECTION_RW, SECTION_MATH)
                     if counts.get(name)]
            ctk.CTkLabel(text_block, text=f"Question bank ready — {summary['total']:,} questions",
                         font=ui.f(13, "bold"), text_color=C.TEXT, anchor="w").pack(anchor="w")
            ui.caption(text_block, "   ·   ".join(parts) or "—", size=11).pack(anchor="w")

            # Honest readiness check against the blueprint.
            warnings = self._coverage_warnings(summary)
            if warnings:
                ui.caption(text_block, "⚠  " + warnings[0], size=11,
                           color=C.ORANGE).pack(anchor="w", pady=(3, 0))
        else:
            ctk.CTkLabel(text_block, text=message, font=ui.f(13, "bold"),
                         text_color=C.ORANGE, anchor="w").pack(anchor="w")
            ui.caption(text_block,
                       "Put the College Board PDFs (with answers and rationales) in the "
                       "pdfs folder, then run:  python sat_importer.py",
                       size=11).pack(anchor="w")

        self.bank_ok = ok

    def _coverage_warnings(self, summary) -> list[str]:
        """
        A full adaptive section needs 2 modules of unique questions. Tell the
        user before they start if a domain is too thin to fill the blueprint.
        """
        warnings = []
        for section, blueprint in BLUEPRINT.items():
            available = summary["by_domain"].get(section, {})
            if not available:
                continue
            for domain, per_module in blueprint["domain_quota"].items():
                needed = per_module * 2
                have = available.get(domain, 0)
                if have < needed:
                    warnings.append(
                        f"{section} → {domain}: {have} imported, {needed} needed for a "
                        f"full 2-module test. Tests will still run, backfilled from other domains."
                    )
        return warnings

    # ------------------------------------------------------------- mode cards

    def _mode_cards(self):
        grid = ui.row(self)
        grid.pack(side="top", fill="both", expand=True, padx=30, pady=(0, 6))

        top = ui.row(grid)
        top.pack(fill="both", expand=True, pady=(0, 10))

        import study_plan
        from datetime import date as _date
        today = _date.today()
        week = study_plan.active_week_for(today)
        days = study_plan.active_days_until_next_test(today)
        if week is not None:
            plan_detail = (f"Week {week['number']} — {week['title']}. "
                           + (f"{days} day(s) to your next SAT. " if days is not None else "")
                           + "Today's tasks, one click each.")
        else:
            plan_detail = "Your seven-week Command Center: what to do today, in order."
        self._mode_card(
            top, "📋", "Today's Plan",
            plan_detail,
            "Open today's plan", self.controller.show_plan, C.AMBER, side="left",
        )
        self._mode_card(
            top, "🎯", "Adaptive Practice Test",
            "Module 1 sets the pace, then routes you into a harder or easier "
            "Module 2 — exactly like the real digital SAT.",
            "Start a test", self.controller.show_test_setup, C.GREEN, side="left",
        )
        bottom = ui.row(grid)
        bottom.pack(fill="both", expand=True)

        self._mode_card(
            bottom, "🎓", "Targeted Drill",
            "Pick the domains you keep losing points in. Questions ramp Easy → "
            "Medium → Hard, timed per question if you want.",
            "Build a drill", self.controller.show_drill_setup, C.BLUE, side="left",
        )

        untagged = attempt_repo.untagged_count()
        totals = attempt_repo.log_totals(since_days=7)
        self._mode_card(
            bottom, "🔬", "Error Log",
            f"{totals['wrong']} wrong · {totals['lucky']} lucky this week"
            + (f" · {untagged} untagged" if untagged else " · all tagged")
            + ". Tag the cause, write the fix, schedule the redo.",
            "Open error log", self.controller.show_error_log, C.PURPLE, side="left",
        )
        self._mode_card(
            bottom, "🕘", "History & Analytics",
            "Every past sitting stays openable — scores, routing, per-skill "
            "accuracy and every rationale you've already seen.",
            "Open history", self.controller.show_history, C.ORANGE, side="left",
        )

    def _mode_card(self, parent, icon, heading, detail, cta, command, accent, side="left"):
        card = ui.card(parent, fg_color=C.SURFACE)
        card.pack(side=side, fill="both", expand=True, padx=5)

        head = ui.row(card)
        head.pack(fill="x", padx=20, pady=(18, 4))
        ctk.CTkLabel(head, text=icon, font=ui.f(24)).pack(side="left", padx=(0, 10))
        ui.title(head, heading, size=17).pack(side="left")

        ctk.CTkLabel(card, text=detail, font=ui.f(12), text_color=C.TEXT_DIM,
                     wraplength=340, justify="left", anchor="w").pack(
            anchor="w", padx=20, pady=(2, 12))

        enabled = self.bank_ok or command == self.controller.show_history
        button = ctk.CTkButton(
            card, text=cta, font=ui.f(13, "bold"), fg_color=accent,
            hover_color=C.SLATE_DARK, text_color="#0B1F14", height=40,
            corner_radius=10, command=command,
        )
        if not enabled:
            button.configure(state="disabled", fg_color=C.SURFACE_2,
                             text_color=C.TEXT_FAINT, text="Import questions first")
        button.pack(fill="x", padx=20, pady=(0, 18))
        return card

    # ------------------------------------------------------------ stats strip

    def _stats_strip(self):
        stats = attempt_repo.overall_stats()
        sessions = attempt_repo.list_sessions(limit=200)
        completed = [s for s in sessions if s["status"] == "completed"]

        strip = ui.row(self)
        strip.pack(side="bottom", fill="x", padx=26, pady=(4, 16))

        tile, _, _ = ui.stat_tile(strip, "Questions answered", f"{stats['total']:,}",
                                  C.BLUE, f"{stats['unique_q']:,} unique")
        tile.pack(side="left", fill="both", expand=True, padx=4)

        tile, _, _ = ui.stat_tile(strip, "Overall accuracy", f"{stats['accuracy']:.0f}%",
                                  ui.accuracy_color(stats["accuracy"]) if stats["total"]
                                  else C.TEXT_FAINT,
                                  f"{stats['correct']:,} correct")
        tile.pack(side="left", fill="both", expand=True, padx=4)

        tile, _, _ = ui.stat_tile(strip, "Sessions", str(len(completed)), C.PURPLE,
                                  "completed")
        tile.pack(side="left", fill="both", expand=True, padx=4)

        weakest = attempt_repo.weakest("domain", min_attempts=4, limit=1)
        if weakest:
            worst = weakest[0]
            tile, _, _ = ui.stat_tile(strip, "Weakest domain",
                                      f"{worst['accuracy']:.0f}%", C.ORANGE,
                                      worst["bucket"])
        else:
            tile, _, _ = ui.stat_tile(strip, "Weakest domain", "—", C.TEXT_FAINT,
                                      "answer a few more")
        tile.pack(side="left", fill="both", expand=True, padx=4)
