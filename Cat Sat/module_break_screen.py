"""
module_break_screen.py, the moment between modules.

This is where the adaptive routing becomes visible: you finished Module 1, here
is how you did, and here is which Module 2 that earns you. Between sections it
doubles as the 10-minute break the real test gives you, with a countdown you can
skip.
"""

from __future__ import annotations

import customtkinter as ctk

import ui_kit as ui
from config import C, TIER_COLOR, TIER_LABEL, accuracy_color


class ModuleBreakScreen(ctk.CTkFrame):

    def __init__(self, parent, controller, info, on_continue):
        super().__init__(parent, fg_color="transparent")
        self.controller = controller
        self.info = info
        self.on_continue = on_continue
        self._job = None

        wrapper = ui.row(self)
        wrapper.pack(expand=True, fill="both", padx=60, pady=40)

        card = ui.card(wrapper)
        card.pack(expand=True, fill="both")

        # Bottom actions are packed first so the Continue button is always
        # reachable, however tall the stats block gets.
        self._actions = ui.row(card)
        self._actions.pack(side="bottom", fill="x", pady=(6, 26))

        result = info.get("result")
        tier = info.get("tier", "")

        # ---- Heading
        ctk.CTkLabel(card, text="✅" if info.get("kind") == "module" else "☕",
                     font=ui.f(44)).pack(pady=(38, 6))
        ui.title(card, info.get("heading", "Module complete"), size=25).pack()

        # ---- The numbers you just posted
        if result is not None and result.total:
            stats = ui.row(card)
            stats.pack(pady=(18, 6))

            tile, _, _ = ui.stat_tile(stats, "Correct",
                                      f"{result.correct}/{result.total}",
                                      accuracy_color(result.raw_accuracy * 100))
            tile.pack(side="left", padx=6)
            tile, _, _ = ui.stat_tile(stats, "Raw accuracy",
                                      f"{result.raw_accuracy * 100:.0f}%",
                                      accuracy_color(result.raw_accuracy * 100))
            tile.pack(side="left", padx=6)
            tile, _, _ = ui.stat_tile(stats, "Weighted",
                                      f"{result.weighted_accuracy * 100:.0f}%",
                                      C.TEXT_DIM, "difficulty-adjusted")
            tile.pack(side="left", padx=6)
            tile, _, _ = ui.stat_tile(stats, "Time used",
                                      ui.format_duration(result.time_used_seconds),
                                      C.TEXT_DIM)
            tile.pack(side="left", padx=6)

        # ---- The routing decision, stated plainly
        detail = info.get("detail", "")
        if detail:
            ctk.CTkLabel(card, text=detail, font=ui.f(13), text_color=C.TEXT_DIM,
                         wraplength=680, justify="center").pack(pady=(14, 4), padx=40)

        if tier and tier in TIER_LABEL and info.get("kind") == "module":
            badge = ui.row(card)
            badge.pack(pady=(10, 4))
            ctk.CTkLabel(badge, text="Next:", font=ui.f(13),
                         text_color=C.TEXT_FAINT).pack(side="left", padx=(0, 8))
            ctk.CTkLabel(badge, text=TIER_LABEL[tier], font=ui.f(16, "bold"),
                         text_color=TIER_COLOR.get(tier, C.BLUE)).pack(side="left")
        else:
            ctk.CTkLabel(card, text=info.get("next_label", ""), font=ui.f(15, "bold"),
                         text_color=C.TEXT).pack(pady=(10, 4))

        # ---- Break countdown (section breaks only)
        self.countdown_label = ui.caption(card, "", size=13, color=C.AMBER)
        self.countdown_label.pack(pady=(8, 2))

        minutes = int(info.get("minutes") or 0)
        self.remaining = minutes * 60 if minutes else 0
        if self.remaining:
            ui.caption(card,
                       f"Take up to {minutes} minutes. Stand up, look away from the screen.",
                       size=12).pack(pady=(0, 4))
            self._tick()

        # ---- Continue
        label = ("Skip break and continue  ►" if self.remaining
                 else f"Continue to {info.get('next_label', 'the next module')}  ►")
        ui.primary_button(self._actions, label, self.proceed, width=340).pack(pady=(4, 8))
        ui.ghost_button(self._actions, "Save and exit to home", self.exit_early,
                        width=200).pack()

    # ---------------------------------------------------------------- ticking

    def _tick(self):
        if not self.winfo_exists():
            return
        if self.remaining <= 0:
            self.countdown_label.configure(text="Break over. Continue when ready.",
                                           text_color=C.GREEN)
            return
        mins, secs = divmod(self.remaining, 60)
        self.countdown_label.configure(text=f"⏳  {mins:02d}:{secs:02d} remaining")
        self.remaining -= 1
        self._job = self.after(1000, self._tick)

    def _cancel(self):
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    # ----------------------------------------------------------------- actions

    def proceed(self):
        self._cancel()
        self.on_continue()

    def exit_early(self):
        self._cancel()
        self.controller.abandon_current_test()

    def destroy(self):
        self._cancel()
        super().destroy()
