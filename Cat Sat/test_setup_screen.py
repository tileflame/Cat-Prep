"""
test_setup_screen.py — configure an adaptive practice test.

Deliberately short: full-length or one section, timed or untimed, and the
routing threshold if you want to move it. Everything else follows the blueprint,
which is previewed here so you know exactly what you're about to sit.
"""

from __future__ import annotations

import customtkinter as ctk

import question_repo
import ui_kit as ui
from config import (
    BLUEPRINT,
    C,
    DEFAULT_ROUTING_THRESHOLD,
    SECTION_BREAK_MINUTES,
    SECTION_MATH,
    SECTION_RW,
)
from database import get_setting, set_setting
from test_flow import MODE_FULL, MODE_SECTION


class TestSetupScreen(ctk.CTkFrame):

    def __init__(self, parent, controller):
        super().__init__(parent, fg_color="transparent")
        self.controller = controller

        header = ui.row(self)
        header.pack(fill="x", padx=30, pady=(22, 6))
        ui.title(header, "🎯  Adaptive Practice Test", size=25).pack(side="left")
        ui.secondary_button(header, "← Back", controller.show_home_screen,
                            width=100).pack(side="right")

        ui.caption(self,
                   "Module 1 is a mixed-difficulty baseline. Your accuracy on it decides "
                   "whether Module 2 is the harder or the easier form.",
                   size=12).pack(anchor="w", padx=32, pady=(0, 12))

        # Bottom chrome is packed first and anchored to the bottom, so the Start
        # button can never be pushed off-screen by tall content. See the note in
        # drill_setup_screen.py.
        footer = ui.row(self)
        footer.pack(side="bottom", fill="x", padx=30, pady=(6, 18))
        ui.primary_button(footer, "🚀  Start test", self.start, width=260).pack(side="right")
        ui.secondary_button(footer, "Cancel", controller.show_home_screen,
                            width=120).pack(side="right", padx=10)

        self.status = ui.caption(self, "", size=12, color=C.ORANGE)
        self.status.pack(side="bottom", pady=(4, 0))

        body = ui.row(self)
        body.pack(side="top", fill="both", expand=True, padx=26, pady=(0, 4))

        self._options_card(body)
        self._preview_card(body)

        self._refresh_preview()

    # ---------------------------------------------------------------- options

    def _options_card(self, parent):
        # Scrollable: keeps every option reachable on small windows / high DPI.
        card = ctk.CTkScrollableFrame(parent, fg_color=C.SURFACE, corner_radius=14)
        card.pack(side="left", fill="both", expand=True, padx=(0, 6))

        ui.title(card, "Setup", size=17).pack(anchor="w", padx=22, pady=(18, 10))

        # --- Length
        ui.caption(card, "TEST LENGTH", size=10).pack(anchor="w", padx=22)
        saved_length = get_setting("last_test_length", "Single section")
        if saved_length not in ("Single section", "Full length"):
            saved_length = "Single section"          # never desync the segmented button
        self.length_var = ctk.StringVar(value=saved_length)
        self.length_menu = ctk.CTkSegmentedButton(
            card, values=["Single section", "Full length"], variable=self.length_var,
            font=ui.f(13, "bold"), command=lambda _: self._refresh_preview(),
            selected_color=C.GREEN, selected_hover_color=C.GREEN_DARK,
            unselected_color=C.SURFACE_2, fg_color=C.SURFACE_3, height=36,
        )
        self.length_menu.pack(fill="x", padx=22, pady=(4, 14))

        # --- Section (only relevant for a single-section sitting)
        ui.caption(card, "SECTION", size=10).pack(anchor="w", padx=22)
        available = question_repo.list_sections() or [SECTION_RW, SECTION_MATH]
        self.section_var = ctk.StringVar(
            value=get_setting("last_test_section", available[0]))
        if self.section_var.get() not in available:
            self.section_var.set(available[0])
        self.section_menu = ctk.CTkOptionMenu(
            card, variable=self.section_var, values=available, font=ui.f(13),
            fg_color=C.SURFACE_2, button_color=C.SLATE, height=36,
            command=lambda _: self._refresh_preview(),
        )
        self.section_menu.pack(fill="x", padx=22, pady=(4, 2))
        self.section_hint = ui.caption(card, "", size=10, color=C.TEXT_FAINT)
        self.section_hint.pack(anchor="w", padx=22, pady=(0, 12))

        # --- Timing
        ui.caption(card, "TIMING", size=10).pack(anchor="w", padx=22)
        self.timed_var = ctk.StringVar(value=get_setting("last_test_timing", "Official timing"))
        ctk.CTkSegmentedButton(
            card, values=["Official timing", "Untimed"], variable=self.timed_var,
            font=ui.f(13, "bold"), command=lambda _: self._refresh_preview(),
            selected_color=C.BLUE, selected_hover_color=C.BLUE_DARK,
            unselected_color=C.SURFACE_2, fg_color=C.SURFACE_3, height=36,
        ).pack(fill="x", padx=22, pady=(4, 14))

        # --- Routing threshold
        ui.caption(card, "ROUTING THRESHOLD", size=10).pack(anchor="w", padx=22)
        saved = str(get_setting("routing_threshold", int(DEFAULT_ROUTING_THRESHOLD * 100)))
        if f"{saved}%" not in ("55%", "60%", "65%", "70%", "75%"):
            saved = str(int(DEFAULT_ROUTING_THRESHOLD * 100))
        self.threshold_var = ctk.StringVar(value=f"{saved}%")
        ctk.CTkOptionMenu(
            card, variable=self.threshold_var,
            values=["55%", "60%", "65%", "70%", "75%"], font=ui.f(13),
            fg_color=C.SURFACE_2, button_color=C.SLATE, height=34,
            command=lambda _: self._refresh_preview(),
        ).pack(fill="x", padx=22, pady=(4, 4))
        ui.caption(card,
                   "College Board doesn't publish the real cut score. 65% is a reasonable "
                   "working estimate — raise it to make the upper route harder to earn.",
                   size=10).pack(anchor="w", padx=22, pady=(0, 10))

        self.weighted_var = ctk.StringVar(value=get_setting("routing_weighted", "on"))
        ctk.CTkCheckBox(
            card, text="Weight routing by question difficulty",
            variable=self.weighted_var, onvalue="on", offvalue="off",
            font=ui.f(12), text_color=C.TEXT_DIM, checkbox_width=18, checkbox_height=18,
            fg_color=C.GREEN, hover_color=C.GREEN_DARK,
        ).pack(anchor="w", padx=22, pady=(0, 18))

    # ---------------------------------------------------------------- preview

    def _preview_card(self, parent):
        self.preview = ctk.CTkScrollableFrame(parent, fg_color=C.SURFACE, corner_radius=14)
        self.preview.pack(side="left", fill="both", expand=True, padx=(6, 0))
        ui.title(self.preview, "What you'll sit", size=17).pack(
            anchor="w", padx=22, pady=(18, 8))
        self.preview_body = ui.row(self.preview)
        self.preview_body.pack(fill="both", expand=True, padx=22, pady=(0, 18))

    def _refresh_preview(self):
        # A full-length sitting always covers both sections, so the section
        # picker is meaningless there — grey it out instead of lying about it.
        full = self.length_var.get() == "Full length"
        self.section_menu.configure(state="disabled" if full else "normal")
        self.section_hint.configure(
            text="Both sections are included in a full-length test." if full else "")

        for widget in self.preview_body.winfo_children():
            widget.destroy()

        sections = self._sections()
        timed = self.timed_var.get() == "Official timing"
        total_questions = 0
        total_minutes = 0

        for section in sections:
            blueprint = BLUEPRINT.get(section)
            if not blueprint:
                continue
            per_module = blueprint["questions_per_module"]
            minutes = blueprint["minutes_per_module"]
            total_questions += per_module * 2
            total_minutes += minutes * 2

            block = ui.card(self.preview_body, fg_color=C.SURFACE_2, corner_radius=10)
            block.pack(fill="x", pady=5)
            ctk.CTkLabel(block, text=section, font=ui.f(14, "bold"),
                         text_color=C.TEXT, anchor="w").pack(anchor="w", padx=16, pady=(12, 2))
            ui.caption(block,
                       f"2 modules × {per_module} questions"
                       + (f" × {minutes} min" if timed else " · untimed"),
                       size=11).pack(anchor="w", padx=16)

            quota = ", ".join(f"{domain.split(' and ')[0]} {count}"
                              for domain, count in blueprint["domain_quota"].items())
            ui.caption(block, f"per module: {quota}", size=10).pack(
                anchor="w", padx=16, pady=(2, 10))

            # Honest availability check.
            have = question_repo.count_available(section=section)
            need = per_module * 2
            if have < need:
                ui.caption(block,
                           f"⚠ only {have} {section} questions imported ({need} needed) — "
                           "modules will be short",
                           size=10, color=C.ORANGE).pack(anchor="w", padx=16, pady=(0, 10))

        if len(sections) > 1:
            total_minutes += SECTION_BREAK_MINUTES
            ui.caption(self.preview_body,
                       f"+ {SECTION_BREAK_MINUTES} minute break between sections",
                       size=11).pack(anchor="w", pady=(4, 0))

        summary = ui.row(self.preview_body)
        summary.pack(fill="x", pady=(14, 0))
        tile, _, _ = ui.stat_tile(summary, "Questions", total_questions, C.BLUE)
        tile.pack(side="left", fill="both", expand=True, padx=(0, 5))
        tile, _, _ = ui.stat_tile(summary, "Time",
                                  f"{total_minutes} min" if timed else "untimed",
                                  C.AMBER)
        tile.pack(side="left", fill="both", expand=True, padx=(5, 0))

    def _sections(self) -> list[str]:
        if self.length_var.get() == "Full length":
            return [SECTION_RW, SECTION_MATH]
        return [self.section_var.get()]

    # ------------------------------------------------------------------ start

    def start(self):
        sections = self._sections()
        timed = self.timed_var.get() == "Official timing"
        threshold = int(self.threshold_var.get().rstrip("%")) / 100
        weighted = self.weighted_var.get() == "on"

        set_setting("last_test_length", self.length_var.get())
        set_setting("last_test_section", self.section_var.get())
        set_setting("last_test_timing", self.timed_var.get())
        set_setting("routing_threshold", int(threshold * 100))
        set_setting("routing_weighted", "on" if weighted else "off")

        mode = MODE_FULL if len(sections) > 1 else MODE_SECTION
        started = self.controller.start_adaptive_test(
            mode=mode, sections=sections, timed=timed,
            threshold=threshold, use_weighting=weighted,
        )
        if not started:
            self.status.configure(
                text="⚠ Couldn't build a module — the question bank has no questions "
                     "for that section. Import more PDFs and try again."
            )
