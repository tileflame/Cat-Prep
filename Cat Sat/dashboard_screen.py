"""
dashboard_screen.py, long-run analytics across every session.

Changes from the original:
  * the trend line plots one point per *session*, not a cumulative average per
    question (which flattens out and stops telling you anything after a while)
  * the canvas draws on <Configure>, so it no longer renders into a 1-pixel-wide
    widget the first time the screen opens
  * domain and skill accuracy tables, sorted worst first, with a one-click
    "drill my weakest domain" button
  * difficulty and pacing breakdowns
"""

from __future__ import annotations

import customtkinter as ctk
from tkinter import messagebox

import attempt_repo
import ui_kit as ui
from config import C, DIFFICULTY_COLOR, accuracy_color


class DashboardScreen(ctk.CTkFrame):

    def __init__(self, parent, controller):
        super().__init__(parent, fg_color="transparent")
        self.controller = controller
        self._chart_drawn_width = 0

        header = ui.row(self)
        header.pack(fill="x", padx=30, pady=(22, 8))
        ui.title(header, "📊  Performance Analytics", size=25).pack(side="left")

        right = ui.row(header)
        right.pack(side="right")
        ui.secondary_button(right, "🕘 History", controller.show_history,
                            width=120).pack(side="left", padx=6)
        ui.secondary_button(right, "🏠 Home", controller.show_home_screen,
                            width=110).pack(side="left", padx=6)
        ctk.CTkButton(right, text="Reset stats", font=ui.f(12, "bold"),
                      fg_color=C.SURFACE_2, hover_color=C.RED, text_color=C.TEXT_DIM,
                      width=110, height=42, corner_radius=10,
                      command=self.reset_history).pack(side="left")

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.pack(fill="both", expand=True, padx=26, pady=(0, 18))

        self.refresh_stats()

    # ---------------------------------------------------------------- refresh

    def refresh_stats(self):
        for widget in self.scroll.winfo_children():
            widget.destroy()

        stats = attempt_repo.overall_stats()
        if not stats["total"]:
            ui.empty_state(
                self.scroll, "📈", "No data yet",
                "Answer some questions and this fills up with accuracy trends, "
                "domain breakdowns and pacing analysis.",
                "Start a drill", self.controller.show_drill_setup,
            )
            return

        self._tiles(stats)
        self._trend_chart()
        self._tables()
        self._pacing()

    # ------------------------------------------------------------------ tiles

    def _tiles(self, stats):
        strip = ui.row(self.scroll)
        strip.pack(fill="x", pady=(6, 12))

        tile, _, _ = ui.stat_tile(strip, "Questions answered", f"{stats['total']:,}",
                                  C.BLUE, f"{stats['unique_q']:,} unique items")
        tile.pack(side="left", fill="both", expand=True, padx=4)

        tile, _, _ = ui.stat_tile(strip, "Overall accuracy", f"{stats['accuracy']:.0f}%",
                                  accuracy_color(stats["accuracy"]),
                                  f"{stats['correct']:,} correct")
        tile.pack(side="left", fill="both", expand=True, padx=4)

        tile, _, _ = ui.stat_tile(strip, "Avg time / question",
                                  ui.format_seconds_short(stats["avg_seconds"]),
                                  C.AMBER, "across all sessions")
        tile.pack(side="left", fill="both", expand=True, padx=4)

        timeline = attempt_repo.accuracy_timeline(limit=200)
        scored = [p for p in timeline if p["estimated_score"]]
        if scored:
            best = max(p["estimated_score"] for p in scored)
            latest = scored[-1]["estimated_score"]
            tile, _, _ = ui.stat_tile(strip, "Best estimated score", str(best),
                                      C.GREEN, f"latest {latest}")
        else:
            tile, _, _ = ui.stat_tile(strip, "Best estimated score", "-",
                                      C.TEXT_FAINT, "sit a full test")
        tile.pack(side="left", fill="both", expand=True, padx=4)

    # ------------------------------------------------------------------ chart

    def _trend_chart(self):
        card = ui.card(self.scroll)
        card.pack(fill="x", pady=(0, 12))

        head = ui.row(card)
        head.pack(fill="x", padx=22, pady=(16, 4))
        ui.title(head, "Accuracy by session", size=17).pack(side="left")
        ui.caption(head, "one point per completed session", size=11).pack(side="left", padx=10)

        self.timeline = attempt_repo.accuracy_timeline(limit=25)
        if len(self.timeline) < 2:
            ui.body(card,
                    "Complete at least two sessions to see a trend line.",
                    size=12).pack(anchor="w", padx=22, pady=(4, 20))
            return

        self.canvas = ctk.CTkCanvas(card, bg=C.SURFACE_3, highlightthickness=0, height=230)
        self.canvas.pack(fill="x", padx=22, pady=(8, 20))
        # Draw once the widget actually has a size. The old version measured the
        # canvas before layout, got 1 pixel, and drew the chart into a sliver.
        self.canvas.bind("<Configure>", self._draw_chart)
        self.after(60, lambda: self._draw_chart(None))

    def _draw_chart(self, _event):
        canvas = getattr(self, "canvas", None)
        if canvas is None or not canvas.winfo_exists():
            return

        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width <= 1:
            width = 900          # sensible default before the first <Configure>
        if height <= 1:
            height = 230
        if width == self._chart_drawn_width:
            return
        self._chart_drawn_width = width

        canvas.delete("all")
        data = self.timeline
        if len(data) < 2:
            return

        pad_left, pad_right, pad_top, pad_bottom = 46, 20, 22, 34
        plot_w = max(60, width - pad_left - pad_right)
        plot_h = max(50, height - pad_top - pad_bottom)

        # Gridlines every 25%.
        for pct in (0, 25, 50, 75, 100):
            y = pad_top + plot_h - plot_h * pct / 100
            canvas.create_line(pad_left, y, pad_left + plot_w, y,
                               fill=C.BORDER, dash=(3, 4))
            canvas.create_text(pad_left - 12, y, text=f"{pct}%", fill=C.TEXT_FAINT,
                               font=ui.f(9), anchor="e")

        step = plot_w / max(len(data) - 1, 1)
        points = []
        for index, point in enumerate(data):
            x = pad_left + index * step
            y = pad_top + plot_h - plot_h * min(100.0, max(0.0, point["accuracy"])) / 100
            points.append((x, y, point))

        # Line + markers.
        for (x1, y1, _), (x2, y2, _) in zip(points, points[1:]):
            canvas.create_line(x1, y1, x2, y2, fill=C.GREEN, width=3, smooth=True)
        for x, y, point in points:
            color = accuracy_color(point["accuracy"])
            canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill=color, outline=C.SURFACE_3)

        # Highlight and label the most recent session.
        last_x, last_y, last_point = points[-1]
        canvas.create_oval(last_x - 7, last_y - 7, last_x + 7, last_y + 7,
                           outline=C.GREEN, width=2)
        canvas.create_text(
            min(last_x, pad_left + plot_w - 60), max(last_y - 16, 12),
            text=f"{last_point['accuracy']:.0f}%", fill=C.TEXT,
            font=ui.f(11, "bold"), anchor="s",
        )

        # X axis: first and last dates only, so it never gets crowded.
        canvas.create_text(pad_left, height - 12,
                           text=ui.format_timestamp(data[0]["started_at"]).split("·")[0].strip(),
                           fill=C.TEXT_FAINT, font=ui.f(9), anchor="w")
        canvas.create_text(pad_left + plot_w, height - 12,
                           text=ui.format_timestamp(data[-1]["started_at"]).split("·")[0].strip(),
                           fill=C.TEXT_FAINT, font=ui.f(9), anchor="e")

    # ----------------------------------------------------------------- tables

    def _tables(self):
        holder = ui.row(self.scroll)
        holder.pack(fill="x", pady=(0, 12))

        # ---- Domains
        domain_card = ui.card(holder)
        domain_card.pack(side="left", fill="both", expand=True, padx=(0, 5))

        head = ui.row(domain_card)
        head.pack(fill="x", padx=20, pady=(16, 8))
        ui.title(head, "Domains", size=16).pack(side="left")
        ui.caption(head, "weakest first", size=11).pack(side="left", padx=8)

        inner = ui.row(domain_card)
        inner.pack(fill="x", padx=20, pady=(0, 10))
        domains = attempt_repo.breakdown("domain")
        for bucket in domains:
            ui.accuracy_bar(inner, bucket["bucket"], bucket["correct"], bucket["total"])
        if not domains:
            ui.body(inner, "No data.", size=12).pack(anchor="w")

        weakest = attempt_repo.weakest("domain", min_attempts=4, limit=1)
        if weakest:
            ui.secondary_button(
                domain_card, f"🎓  Drill {weakest[0]['bucket']}",
                self.controller.show_drill_setup, width=240,
            ).pack(anchor="w", padx=20, pady=(0, 16))

        # ---- Skills
        skill_card = ui.card(holder)
        skill_card.pack(side="left", fill="both", expand=True, padx=(5, 0))
        head2 = ui.row(skill_card)
        head2.pack(fill="x", padx=20, pady=(16, 8))
        ui.title(head2, "Skills", size=16).pack(side="left")
        ui.caption(head2, "3+ attempts", size=11).pack(side="left", padx=8)

        inner2 = ui.row(skill_card)
        inner2.pack(fill="x", padx=20, pady=(0, 16))
        skills = [b for b in attempt_repo.breakdown("skill", min_attempts=3)][:10]
        skills.sort(key=lambda b: b["accuracy"])
        for bucket in skills:
            ui.accuracy_bar(inner2, bucket["bucket"], bucket["correct"], bucket["total"])
        if not skills:
            ui.body(inner2, "Answer at least 3 questions in a skill to see it here.",
                    size=12).pack(anchor="w")

        # ---- Difficulty
        diff_card = ui.card(self.scroll)
        diff_card.pack(fill="x", pady=(0, 12))
        ui.title(diff_card, "Accuracy by difficulty", size=16).pack(
            anchor="w", padx=20, pady=(16, 8))
        strip = ui.row(diff_card)
        strip.pack(fill="x", padx=14, pady=(0, 16))
        by_difficulty = {b["bucket"]: b for b in attempt_repo.breakdown("difficulty")}
        for difficulty in ("Easy", "Medium", "Hard"):
            bucket = by_difficulty.get(difficulty)
            if not bucket:
                continue
            tile, _, _ = ui.stat_tile(strip, difficulty, f"{bucket['accuracy']:.0f}%",
                                      DIFFICULTY_COLOR[difficulty],
                                      f"{bucket['correct']}/{bucket['total']}  ·  "
                                      f"{ui.format_seconds_short(bucket['avg_seconds'])} avg")
            tile.pack(side="left", fill="both", expand=True, padx=6)

    # ----------------------------------------------------------------- pacing

    def _pacing(self):
        pace = attempt_repo.pace_stats()
        if not pace["count"]:
            return

        card = ui.card(self.scroll)
        card.pack(fill="x", pady=(0, 16))
        ui.title(card, "Pacing habits", size=16).pack(anchor="w", padx=20, pady=(16, 8))

        strip = ui.row(card)
        strip.pack(fill="x", padx=14, pady=(0, 8))
        tile, _, _ = ui.stat_tile(strip, "Average",
                                  ui.format_seconds_short(pace["avg_seconds"]), C.TEXT)
        tile.pack(side="left", fill="both", expand=True, padx=6)
        tile, _, _ = ui.stat_tile(strip, "On correct answers",
                                  ui.format_seconds_short(pace["avg_correct"]), C.GREEN)
        tile.pack(side="left", fill="both", expand=True, padx=6)
        tile, _, _ = ui.stat_tile(strip, "On wrong answers",
                                  ui.format_seconds_short(pace["avg_incorrect"]), C.RED)
        tile.pack(side="left", fill="both", expand=True, padx=6)

        # A genuinely useful read on someone's test-taking behaviour.
        if pace["avg_incorrect"] > pace["avg_correct"] * 1.25:
            note = ("You spend noticeably longer on the questions you get wrong. "
                    "That's usually a sign to guess and move on sooner, the time is "
                    "better spent double-checking questions you can actually finish.")
            color = C.ORANGE
        elif pace["avg_incorrect"] < pace["avg_correct"] * 0.75:
            note = ("You're answering wrong questions faster than right ones, which "
                    "usually means rushing or guessing. Slow down on the ones you're "
                    "unsure about before eliminating.")
            color = C.ORANGE
        else:
            note = "Your timing on right and wrong answers is balanced. Good discipline."
            color = C.GREEN
        ctk.CTkLabel(card, text=note, font=ui.f(12), text_color=color,
                     wraplength=900, justify="left").pack(anchor="w", padx=20, pady=(4, 18))

    # ------------------------------------------------------------------ reset

    def reset_history(self):
        confirmed = messagebox.askyesno(
            "Reset statistics",
            "Delete every session, attempt and statistic?\n\n"
            "Your saved notes and your imported question bank are NOT affected. "
            "This cannot be undone.",
        )
        if confirmed:
            attempt_repo.clear_history(keep_notes=True)
            self.refresh_stats()
