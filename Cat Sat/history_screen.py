"""
history_screen.py — every sitting you've ever done, reopenable.

This is the "view them even after you completed them" piece. Each row rebuilds
into the full review screen, with the same score breakdown, routing explanation
and College Board rationales you saw the moment you finished.
"""

from __future__ import annotations

import customtkinter as ctk
from tkinter import messagebox

import attempt_repo
import ui_kit as ui
from config import C, TIER_COLOR, TIER_LABEL, accuracy_color

MODE_LABEL = {
    "full_test": ("Full test", "🎯"),
    "section_test": ("Section test", "🎯"),
    "drill": ("Drill", "🎓"),
    "review": ("Review", "🔁"),
}

FILTERS = [("All", None), ("Tests", "test"), ("Drills", "drill"), ("Reviews", "review")]


class HistoryScreen(ctk.CTkFrame):

    def __init__(self, parent, controller):
        super().__init__(parent, fg_color="transparent")
        self.controller = controller
        self.filter = "All"

        header = ui.row(self)
        header.pack(fill="x", padx=30, pady=(22, 6))
        ui.title(header, "🕘  Practice History", size=25).pack(side="left")

        right = ui.row(header)
        right.pack(side="right")
        ctk.CTkButton(right, text="🗑  Delete all history", font=ui.f(13, "bold"),
                      fg_color=C.SURFACE_2, hover_color=C.RED, text_color=C.TEXT_DIM,
                      width=165, height=42, corner_radius=10,
                      command=self.delete_all).pack(side="left", padx=6)
        ui.secondary_button(right, "📊 Dashboard", controller.show_dashboard,
                            width=140).pack(side="left", padx=6)
        ui.secondary_button(right, "🏠 Home", controller.show_home_screen,
                            width=110).pack(side="left")

        bar = ui.row(self)
        bar.pack(fill="x", padx=30, pady=(0, 8))
        self.filter_buttons = {}
        for label, _ in FILTERS:
            btn = ctk.CTkButton(
                bar, text=label, font=ui.f(12, "bold"), width=84, height=30,
                corner_radius=8,
                fg_color=C.BLUE if label == self.filter else C.SURFACE_2,
                hover_color=C.SLATE, text_color=C.TEXT,
                command=lambda n=label: self.set_filter(n),
            )
            btn.pack(side="left", padx=3)
            self.filter_buttons[label] = btn

        self.count_label = ui.caption(bar, "", size=11)
        self.count_label.pack(side="right")

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.pack(fill="both", expand=True, padx=26, pady=(0, 18))

        self.refresh()

    # ---------------------------------------------------------------- filters

    def set_filter(self, name):
        self.filter = name
        for label, btn in self.filter_buttons.items():
            btn.configure(fg_color=C.BLUE if label == name else C.SURFACE_2)
        self.refresh()

    def _matches(self, session) -> bool:
        if self.filter == "All":
            return True
        mode = session["mode"] or ""
        if self.filter == "Tests":
            return mode in ("full_test", "section_test")
        if self.filter == "Drills":
            return mode == "drill"
        if self.filter == "Reviews":
            return mode == "review"
        return True

    # ---------------------------------------------------------------- render

    def refresh(self):
        for widget in self.scroll.winfo_children():
            widget.destroy()

        sessions = [s for s in attempt_repo.list_sessions(limit=300) if self._matches(s)]
        self.count_label.configure(text=f"{len(sessions)} session(s)")

        if not sessions:
            ui.empty_state(
                self.scroll, "🗂", "No sessions yet",
                "Finish a practice test or drill and it will be saved here permanently.",
                "Start a test", self.controller.show_test_setup,
            )
            return

        self._sessions = sessions
        self._shown = 0
        self._rows_holder = ui.row(self.scroll)
        self._rows_holder.pack(fill="x")
        self._more_holder = ui.row(self.scroll)
        self._more_holder.pack(fill="x")
        self._render_page()

    PAGE_SIZE = 25

    def _render_page(self):
        """Keep the widget count bounded however long your history gets."""
        for widget in self._more_holder.winfo_children():
            widget.destroy()
        end = min(self._shown + self.PAGE_SIZE, len(self._sessions))
        for session in self._sessions[self._shown:end]:
            self._row(session)
        self._shown = end
        remaining = len(self._sessions) - self._shown
        if remaining > 0:
            ui.secondary_button(
                self._more_holder,
                f"Show {min(remaining, self.PAGE_SIZE)} more  ({remaining} left)",
                self._render_page, width=280).pack(pady=8)

    def _row(self, session):
        total = session["total_questions"] or session["attempt_count"] or 0
        correct = session["correct_count"] or 0
        accuracy = (correct / total * 100) if total else 0.0
        abandoned = session["status"] != "completed"

        card = ui.card(self._rows_holder, fg_color=C.SURFACE, corner_radius=12)
        card.pack(fill="x", pady=5)

        # ---- left: what and when
        left = ui.row(card)
        left.pack(side="left", padx=18, pady=14)

        label, icon = MODE_LABEL.get(session["mode"], ("Session", "•"))
        line = ui.row(left)
        line.pack(anchor="w")
        ctk.CTkLabel(line, text=icon, font=ui.f(15)).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(line, text=session["label"] or label, font=ui.f(14, "bold"),
                     text_color=C.TEXT).pack(side="left")
        if abandoned:
            ui.pill(line, "incomplete", C.ORANGE).pack(side="left", padx=8)

        meta = ui.row(left)
        meta.pack(anchor="w", pady=(3, 0))
        ui.caption(meta, ui.format_timestamp(session["started_at"]), size=11).pack(side="left")
        ui.caption(meta, f"  ·  {session['section'] or '—'}", size=11).pack(side="left")
        if session["duration_seconds"]:
            ui.caption(meta, f"  ·  {ui.format_duration(session['duration_seconds'])}",
                       size=11).pack(side="left")

        # ---- routing path
        tiers = session.get("tier_path") or []
        if len(tiers) > 1:
            path = ui.row(left)
            path.pack(anchor="w", pady=(5, 0))
            for index, tier in enumerate(tiers):
                if index:
                    ui.caption(path, "→", size=11).pack(side="left", padx=4)
                ui.pill(path,
                        TIER_LABEL.get(tier, tier).split("—")[0].strip(),
                        TIER_COLOR.get(tier, C.TEXT_DIM)).pack(side="left")

        # ---- middle: score
        middle = ui.row(card)
        middle.pack(side="left", expand=True, fill="x", padx=16)
        if total:
            ctk.CTkLabel(middle, text=f"{correct}/{total}", font=ui.f(18, "bold"),
                         text_color=accuracy_color(accuracy)).pack(anchor="e")
            ui.caption(middle, f"{accuracy:.0f}% accuracy", size=11).pack(anchor="e")
        else:
            ui.caption(middle, "no answers recorded", size=11).pack(anchor="e")

        if session["estimated_score"]:
            score = ui.row(card)
            score.pack(side="left", padx=14)
            ctk.CTkLabel(score, text=str(session["estimated_score"]),
                         font=ui.f(20, "bold"), text_color=C.GREEN).pack()
            ui.caption(score, "est. score", size=10).pack()

        # ---- actions
        actions = ui.row(card)
        actions.pack(side="right", padx=16)
        ctk.CTkButton(actions, text="🗑", font=ui.f(13), width=38, height=34,
                      corner_radius=8, fg_color=C.SURFACE_2, hover_color=C.RED,
                      text_color=C.TEXT_DIM,
                      command=lambda s=session: self.delete(s)).pack(side="right", padx=(6, 0))
        ctk.CTkButton(actions, text="Open review", font=ui.f(12, "bold"),
                      width=120, height=34, corner_radius=8,
                      fg_color=C.SURFACE_2, hover_color=C.SLATE, text_color=C.TEXT,
                      command=lambda s=session: self.open(s)).pack(side="right")

    # --------------------------------------------------------------- actions

    def open(self, session):
        self.controller.show_review_from_history(session["session_id"])

    def delete(self, session):
        confirmed = messagebox.askyesno(
            "Delete session",
            f"Delete “{session['label']}” from {ui.format_timestamp(session['started_at'])}?\n\n"
            "This removes its answers from your stats. It cannot be undone.",
        )
        if confirmed:
            attempt_repo.delete_session(session["session_id"])
            self.refresh()

    def delete_all(self):
        """Wipe every session. Notes and the question bank are untouched."""
        sessions = attempt_repo.list_sessions(limit=1000)
        if not sessions:
            messagebox.showinfo("Nothing to delete", "You have no saved sessions yet.")
            return

        stats = attempt_repo.overall_stats()
        confirmed = messagebox.askyesno(
            "Delete all history",
            f"Delete all {len(sessions)} session(s) and {stats['total']:,} recorded "
            "answers?\n\n"
            "Your imported question bank and your saved notes are NOT affected — "
            "only your practice history and statistics.\n\n"
            "This cannot be undone.",
        )
        if not confirmed:
            return

        attempt_repo.clear_history(keep_notes=True)
        self.refresh()
        messagebox.showinfo("History cleared",
                            "All practice history has been deleted. "
                            "Your question bank and notes are intact.")
