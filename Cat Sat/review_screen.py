"""
review_screen.py — what you see after a test, and whenever you reopen one.

The original review screen showed a flat list of right/wrong. This one answers
the questions that actually change how you study next:

  * What would this have scored, and which Module 2 did I route into?
  * Which domains and skills am I losing points in?
  * Where am I spending too long?
  * Show me the rationale for everything I got wrong or flagged.

It renders from a TestSummary, which can come straight from a finished test or
be rebuilt from the database — so the same screen serves both "just finished"
and "open a test from three weeks ago".
"""

from __future__ import annotations

import customtkinter as ctk

import attempt_repo
import ui_kit as ui
from adaptive_engine import score_band
from config import (
    C,
    DIFFICULTY_COLOR,
    PACING_TARGET_SECONDS,
    TIER_COLOR,
    TIER_LABEL,
    accuracy_color,
)

FILTERS = ["All", "Incorrect", "Flagged", "Skipped", "Slow"]
SLOW_MULTIPLIER = 1.6      # a question is "slow" at 1.6x the pacing target


class ReviewScreen(ctk.CTkFrame):

    def __init__(self, parent, controller, summary, *, from_history=False):
        super().__init__(parent, fg_color="transparent")
        self.controller = controller
        self.summary = summary
        self.from_history = from_history
        self.filter = "All"
        self._image_refs = []

        if summary is None or not summary.records:
            self._render_empty()
            return

        self._build_header()
        self._build_body()

    # ------------------------------------------------------------------ empty

    def _render_empty(self):
        ui.empty_state(
            self, "🗒", "Nothing to review yet",
            "Finish a practice test or a drill and the breakdown will appear here.",
            "Back to home", self.controller.show_home_screen,
        )

    # ----------------------------------------------------------------- header

    def _build_header(self):
        summary = self.summary
        header = ui.row(self)
        header.pack(fill="x", padx=26, pady=(20, 6))

        left = ui.row(header)
        left.pack(side="left")

        accuracy = summary.accuracy
        headline = ("🎉  Strong session" if accuracy >= 80 else
                    "✅  Session complete" if accuracy >= 60 else
                    "📚  Session complete")
        ui.title(left, headline, size=25).pack(anchor="w")
        ui.caption(left, f"{summary.label}  ·  {ui.format_duration(summary.duration_seconds)}"
                         f"  ·  {summary.total} questions", size=12).pack(anchor="w", pady=(2, 0))

        right = ui.row(header)
        right.pack(side="right")
        ctk.CTkLabel(right, text=f"{summary.correct}/{summary.total}",
                     font=ui.f(28, "bold"),
                     text_color=accuracy_color(accuracy)).pack(side="right", padx=(12, 0))
        ctk.CTkLabel(right, text=f"{accuracy:.0f}%", font=ui.f(16, "bold"),
                     text_color=C.TEXT_DIM).pack(side="right")

    # ------------------------------------------------------------------- body

    def _build_body(self):
        # Footer first, anchored bottom: Home / Dashboard / History must never be
        # pushed off-screen by a long review list.
        self._footer()

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.pack(side="top", fill="both", expand=True, padx=20, pady=(0, 4))

        self._section_score_cards()
        self._routing_cards()
        self._breakdowns()
        self._pacing_card()
        self._question_list()

    # ---------------------------------------------------------- score summary

    def _section_score_cards(self):
        summary = self.summary
        if not summary.is_adaptive:
            return

        band = ui.card(self.scroll)
        band.pack(fill="x", pady=(6, 10))

        head = ui.row(band)
        head.pack(fill="x", padx=20, pady=(16, 4))
        ui.title(head, "Estimated score", size=17).pack(side="left")
        ui.pill(head, "estimate — not an official conversion", C.TEXT_FAINT).pack(side="left", padx=10)

        tiles = ui.row(band)
        tiles.pack(fill="x", padx=14, pady=(6, 16))

        for section in summary.sections:
            if not section.total:
                continue
            score = section.estimated_score
            tile, _, _ = ui.stat_tile(
                tiles, section.section, score, accuracy_color(section.accuracy),
                f"{section.correct}/{section.total} · {score_band(score)}",
            )
            tile.pack(side="left", fill="both", expand=True, padx=6)

        total = summary.estimated_total
        if total:
            tile, _, _ = ui.stat_tile(tiles, "Estimated total", total, C.GREEN, "400–1600 scale")
            tile.pack(side="left", fill="both", expand=True, padx=6)

    def _routing_cards(self):
        summary = self.summary
        adaptive_sections = [s for s in summary.sections if s.routing_note]
        if not adaptive_sections:
            return

        card = ui.card(self.scroll)
        card.pack(fill="x", pady=(0, 10))
        ui.title(card, "Adaptive routing", size=17).pack(anchor="w", padx=20, pady=(16, 2))

        for section in adaptive_sections:
            block = ui.row(card)
            block.pack(fill="x", padx=20, pady=(6, 4))

            line = ui.row(block)
            line.pack(fill="x")
            ctk.CTkLabel(line, text=section.section, font=ui.f(14, "bold"),
                         text_color=C.TEXT).pack(side="left")
            ui.pill(line, TIER_LABEL.get(section.final_tier, section.final_tier),
                    TIER_COLOR.get(section.final_tier, C.BLUE)).pack(side="left", padx=10)

            ui.body(block, section.routing_note, size=12).pack(anchor="w", pady=(4, 0))

            # Per-module strip so you can see whether you improved in Module 2.
            strip = ui.row(block)
            strip.pack(fill="x", pady=(8, 6))
            for module in section.modules:
                chip = ui.card(strip, fg_color=C.SURFACE_2, corner_radius=10)
                chip.pack(side="left", padx=(0, 8))
                ui.caption(chip, f"MODULE {module.module_number} · "
                                 f"{TIER_LABEL.get(module.tier, module.tier).split('—')[0].strip()}",
                           size=10).pack(anchor="w", padx=14, pady=(8, 0))
                ctk.CTkLabel(chip, text=f"{module.correct}/{module.total}"
                                        f"   ({module.raw_accuracy * 100:.0f}%)",
                             font=ui.f(15, "bold"),
                             text_color=accuracy_color(module.raw_accuracy * 100)).pack(
                    anchor="w", padx=14, pady=(0, 4))
                ui.caption(chip, f"time used {ui.format_duration(module.time_used_seconds)}",
                           size=10).pack(anchor="w", padx=14, pady=(0, 8))

        ui.caption(card,
                   "Routing uses difficulty-weighted accuracy: a correct Hard question "
                   "counts for more than a correct Easy one.",
                   size=11).pack(anchor="w", padx=20, pady=(2, 16))

    # ------------------------------------------------------------- breakdowns

    def _breakdowns(self):
        holder = ui.row(self.scroll)
        holder.pack(fill="x", pady=(0, 10))

        domain_card = ui.card(holder)
        domain_card.pack(side="left", fill="both", expand=True, padx=(0, 5))
        ui.title(domain_card, "By domain", size=16).pack(anchor="w", padx=20, pady=(16, 8))
        inner = ui.row(domain_card)
        inner.pack(fill="x", padx=20, pady=(0, 16))
        for bucket in self._buckets("domain"):
            ui.accuracy_bar(inner, bucket["bucket"], bucket["correct"], bucket["total"])
        if not self._buckets("domain"):
            ui.body(inner, "No domain data.", size=12).pack(anchor="w")

        skill_card = ui.card(holder)
        skill_card.pack(side="left", fill="both", expand=True, padx=(5, 0))
        ui.title(skill_card, "By skill", size=16).pack(anchor="w", padx=20, pady=(16, 8))
        inner2 = ui.row(skill_card)
        inner2.pack(fill="x", padx=20, pady=(0, 16))
        skills = self._buckets("skill")[:9]
        for bucket in skills:
            ui.accuracy_bar(inner2, bucket["bucket"], bucket["correct"], bucket["total"])
        if not skills:
            ui.body(inner2, "No skill data.", size=12).pack(anchor="w")

        # Difficulty strip: are you missing easy questions you should be getting?
        diff_card = ui.card(self.scroll)
        diff_card.pack(fill="x", pady=(0, 10))
        ui.title(diff_card, "By difficulty", size=16).pack(anchor="w", padx=20, pady=(16, 8))
        strip = ui.row(diff_card)
        strip.pack(fill="x", padx=14, pady=(0, 16))
        for difficulty in ("Easy", "Medium", "Hard"):
            bucket = next((b for b in self._buckets("difficulty")
                           if b["bucket"] == difficulty), None)
            if not bucket:
                continue
            tile, _, _ = ui.stat_tile(
                strip, difficulty, f"{bucket['accuracy']:.0f}%",
                DIFFICULTY_COLOR[difficulty],
                f"{bucket['correct']}/{bucket['total']} correct",
            )
            tile.pack(side="left", fill="both", expand=True, padx=6)

    def _buckets(self, group_by):
        """Group this session's records without another database round trip."""
        cache = getattr(self, "_bucket_cache", None)
        if cache is None:
            cache = self._bucket_cache = {}
        if group_by in cache:
            return cache[group_by]

        totals: dict[str, list[int]] = {}
        for record in self.summary.records:
            key = getattr(record.question, group_by, "") or "Unclassified"
            entry = totals.setdefault(key, [0, 0])
            entry[0] += 1
            entry[1] += 1 if record.is_correct else 0

        out = [{"bucket": key, "total": total, "correct": correct,
                "accuracy": (correct / total * 100) if total else 0.0}
               for key, (total, correct) in totals.items()]
        out.sort(key=lambda b: (b["accuracy"], -b["total"]))
        cache[group_by] = out
        return out

    # ----------------------------------------------------------------- pacing

    def _pacing_card(self):
        records = [r for r in self.summary.records if r.time_spent_ms > 0]
        if not records:
            return

        card = ui.card(self.scroll)
        card.pack(fill="x", pady=(0, 10))
        ui.title(card, "Pacing", size=16).pack(anchor="w", padx=20, pady=(16, 8))

        tiles = ui.row(card)
        tiles.pack(fill="x", padx=14, pady=(0, 8))

        average = sum(r.time_spent_seconds for r in records) / len(records)
        correct = [r.time_spent_seconds for r in records if r.is_correct]
        wrong = [r.time_spent_seconds for r in records if not r.is_correct]

        section = self.summary.sections[0].section if self.summary.sections else ""
        target = PACING_TARGET_SECONDS.get(section)

        tile, _, _ = ui.stat_tile(tiles, "Average per question",
                                  ui.format_seconds_short(average), C.TEXT,
                                  f"target ≈ {target}s" if target else "")
        tile.pack(side="left", fill="both", expand=True, padx=6)

        if correct:
            tile, _, _ = ui.stat_tile(tiles, "When you were right",
                                      ui.format_seconds_short(sum(correct) / len(correct)),
                                      C.GREEN, f"{len(correct)} questions")
            tile.pack(side="left", fill="both", expand=True, padx=6)
        if wrong:
            tile, _, _ = ui.stat_tile(tiles, "When you were wrong",
                                      ui.format_seconds_short(sum(wrong) / len(wrong)),
                                      C.RED, f"{len(wrong)} questions")
            tile.pack(side="left", fill="both", expand=True, padx=6)

        slow = self._slow_records()
        if slow:
            ui.caption(card,
                       f"{len(slow)} question(s) took more than "
                       f"{SLOW_MULTIPLIER:g}× the pacing target — filter to 'Slow' below.",
                       size=11, color=C.ORANGE).pack(anchor="w", padx=20, pady=(0, 16))
        else:
            ui.caption(card, "No questions ran badly over time. Good pacing.",
                       size=11, color=C.GREEN).pack(anchor="w", padx=20, pady=(0, 16))

    def _slow_records(self):
        slow = []
        for record in self.summary.records:
            target = PACING_TARGET_SECONDS.get(record.question.section, 80)
            if record.time_spent_ms and record.time_spent_seconds > target * SLOW_MULTIPLIER:
                slow.append(record)
        return slow

    # --------------------------------------------------------- question list

    def _question_list(self):
        card = ui.card(self.scroll)
        card.pack(fill="both", expand=True, pady=(0, 10))

        head = ui.row(card)
        head.pack(fill="x", padx=20, pady=(16, 6))
        ui.title(head, "Every question", size=16).pack(side="left")

        self.filter_buttons = {}
        filters = ui.row(head)
        filters.pack(side="right")
        for name in FILTERS:
            btn = ctk.CTkButton(
                filters, text=name, font=ui.f(11, "bold"), width=74, height=28,
                corner_radius=8,
                fg_color=C.BLUE if name == self.filter else C.SURFACE_2,
                hover_color=C.SLATE, text_color=C.TEXT,
                command=lambda n=name: self.set_filter(n),
            )
            btn.pack(side="left", padx=2)
            self.filter_buttons[name] = btn

        self.list_holder = ui.row(card)
        self.list_holder.pack(fill="both", expand=True, padx=14, pady=(4, 16))
        self._render_list()

    def set_filter(self, name):
        self.filter = name
        for label, btn in self.filter_buttons.items():
            btn.configure(fg_color=C.BLUE if label == name else C.SURFACE_2)
        self._render_list()

    def _filtered(self):
        records = self.summary.records
        if self.filter == "Incorrect":
            return [r for r in records if not r.is_correct]
        if self.filter == "Flagged":
            return [r for r in records if r.was_flagged]
        if self.filter == "Skipped":
            return [r for r in records if r.skipped]
        if self.filter == "Slow":
            return self._slow_records()
        return records

    PAGE_SIZE = 20

    def _render_list(self):
        """
        Render the first page only; "Show more" adds another.

        A 98-question full test used to build ~1,700 CTk widgets in one go, and
        every filter change rebuilt all of them. Each CTk widget is a canvas
        plus labels, so that was seconds of stutter on the review screen.
        """
        for widget in self.list_holder.winfo_children():
            widget.destroy()

        self._records = self._filtered()
        self._shown = 0
        if not self._records:
            ui.body(self.list_holder,
                    f"Nothing matches “{self.filter}”. Nice.",
                    size=13).pack(anchor="w", padx=10, pady=16)
            return

        self._rows_holder = ui.row(self.list_holder)
        self._rows_holder.pack(fill="x")
        self._more_holder = ui.row(self.list_holder)
        self._more_holder.pack(fill="x")
        self._render_page()

    def _render_page(self):
        for widget in self._more_holder.winfo_children():
            widget.destroy()
        end = min(self._shown + self.PAGE_SIZE, len(self._records))
        for record in self._records[self._shown:end]:
            self._question_row(record)
        self._shown = end
        remaining = len(self._records) - self._shown
        if remaining > 0:
            ui.secondary_button(
                self._more_holder,
                f"Show {min(remaining, self.PAGE_SIZE)} more  ({remaining} left)",
                self._render_page, width=280).pack(pady=8)

    def _question_row(self, record):
        question = record.question
        row = ui.card(self._rows_holder, fg_color=C.SURFACE_2, corner_radius=10)
        row.pack(fill="x", pady=4)

        left = ui.row(row)
        left.pack(side="left", padx=16, pady=12)

        mark = "✅" if record.is_correct else ("⭕" if record.skipped else "❌")
        title_line = ui.row(left)
        title_line.pack(anchor="w")
        ctk.CTkLabel(title_line, text=f"{mark}  Q{record.position or '?'}",
                     font=ui.f(14, "bold"), text_color=C.TEXT).pack(side="left")
        if record.was_flagged:
            ui.pill(title_line, "flagged", C.ORANGE).pack(side="left", padx=6)
        if question.module_number:
            ui.pill(title_line, f"M{question.module_number}", C.TEXT_FAINT).pack(side="left", padx=2)

        meta = ui.row(left)
        meta.pack(anchor="w", pady=(3, 0))
        ui.caption(meta, f"{question.domain}  ·  {question.skill}", size=11).pack(side="left")
        ui.difficulty_pill(meta, question.difficulty).pack(side="left", padx=8)

        middle = ui.row(row)
        middle.pack(side="left", expand=True, fill="x", padx=16)
        shown = record.selected_answer or "—"
        ctk.CTkLabel(middle, text=f"You: {shown}", font=ui.f(13, "bold"),
                     text_color=C.GREEN if record.is_correct else C.RED,
                     anchor="w").pack(anchor="w")
        if not record.is_correct:
            ctk.CTkLabel(middle, text=f"Correct: {question.correct_answer}",
                         font=ui.f(13, "bold"), text_color=C.GREEN,
                         anchor="w").pack(anchor="w")

        timing = ui.row(row)
        timing.pack(side="left", padx=10)
        target = PACING_TARGET_SECONDS.get(question.section, 80)
        slow = record.time_spent_ms and record.time_spent_seconds > target * SLOW_MULTIPLIER
        ctk.CTkLabel(timing, text=ui.format_seconds_short(record.time_spent_seconds),
                     font=ui.f(13, "bold"),
                     text_color=C.ORANGE if slow else C.TEXT_DIM).pack()
        ui.caption(timing, "time", size=10).pack()

        ctk.CTkButton(row, text="Explanation", font=ui.f(12, "bold"),
                      fg_color=C.SURFACE_3, hover_color=C.SLATE, text_color=C.TEXT,
                      width=120, height=36, corner_radius=8,
                      command=lambda r=record: self.show_explanation(r)).pack(
            side="right", padx=16)

    # ------------------------------------------------------------ explanation

    def show_explanation(self, record):
        question = record.question
        window = ctk.CTkToplevel(self)
        window.title(f"Explanation — {question.question_id}")
        window.geometry("940x760")
        window.configure(fg_color=C.BG)
        try:
            window.attributes("-topmost", True)
        except Exception:
            pass

        header = ui.row(window)
        header.pack(fill="x", padx=22, pady=(18, 4))
        ui.title(header, "📖  Explanation", size=20).pack(side="left")
        ui.difficulty_pill(header, question.difficulty).pack(side="left", padx=10)
        ctk.CTkLabel(header,
                     text=("Correct" if record.is_correct else "Incorrect"),
                     font=ui.f(14, "bold"),
                     text_color=C.GREEN if record.is_correct else C.RED).pack(side="right")

        ui.caption(window, f"{question.section} · {question.domain} · {question.skill}",
                   size=11).pack(anchor="w", padx=24)

        answer_line = ui.row(window)
        answer_line.pack(fill="x", padx=22, pady=(8, 4))
        ctk.CTkLabel(answer_line,
                     text=f"Your answer: {record.selected_answer or '— skipped —'}",
                     font=ui.f(13, "bold"),
                     text_color=C.GREEN if record.is_correct else C.RED).pack(side="left")
        ctk.CTkLabel(answer_line, text=f"Correct answer: {question.correct_answer}",
                     font=ui.f(13, "bold"), text_color=C.GREEN).pack(side="left", padx=24)

        body = ctk.CTkScrollableFrame(window, fg_color=C.SURFACE, corner_radius=12)
        body.pack(fill="both", expand=True, padx=20, pady=10)

        self._embed_image(body, question.image_path, "Question",
                          "The question image is missing. Re-run sat_importer.py "
                          "to regenerate it.")
        self._embed_image(body, question.rationale_path, "College Board rationale",
                          "No rationale image was captured for this question. "
                          "Make sure you downloaded the PDF with answers and "
                          "explanations included, then re-import.")

        note = attempt_repo.get_note(question.question_id)
        if note:
            ui.title(body, "Your note", size=15).pack(anchor="w", padx=16, pady=(14, 4))
            ctk.CTkLabel(body, text=note, font=ui.f(13), text_color=C.TEXT_DIM,
                         wraplength=820, justify="left").pack(anchor="w", padx=16, pady=(0, 12))

        ui.secondary_button(window, "Close", window.destroy, width=160).pack(pady=(0, 16))

    def _embed_image(self, parent, path, heading, missing_message):
        ui.title(parent, heading, size=15).pack(anchor="w", padx=16, pady=(14, 6))
        if not path:
            ctk.CTkLabel(parent, text=missing_message, font=ui.f(12),
                         text_color=C.TEXT_FAINT, wraplength=800,
                         justify="left").pack(anchor="w", padx=16, pady=(0, 8))
            return
        image, _ = ui.load_scaled_image(path, max_width=820)
        if image is None:
            ctk.CTkLabel(parent, text=f"Could not open {path}", font=ui.f(12),
                         text_color=C.RED).pack(anchor="w", padx=16)
            return
        # Bounded: the decoded images live in ui_kit's LRU cache, so this list
        # only needs to stop Tk garbage-collecting the ones on screen.
        self._image_refs.append(image)
        if len(self._image_refs) > 24:
            del self._image_refs[:-24]
        ctk.CTkLabel(parent, text="", image=image).pack(padx=16, pady=(0, 10))

    # ----------------------------------------------------------------- footer

    def _footer(self):
        footer = ui.row(self)
        footer.pack(side="bottom", fill="x", padx=26, pady=(6, 16))

        ui.secondary_button(footer, "🏠  Home", self.controller.show_home_screen,
                            width=140).pack(side="left")
        ui.secondary_button(footer, "📊  Dashboard", self.controller.show_dashboard,
                            width=150).pack(side="left", padx=8)
        ui.secondary_button(footer, "🕘  History", self.controller.show_history,
                            width=130).pack(side="left", padx=(0, 8))
        ui.secondary_button(footer, "📋  Plan", self.controller.show_plan,
                            width=110).pack(side="left")

        # The plan's Pass 2: logging comes before redoing. Lead with it.
        to_log = [r for r in self.summary.records if r.needs_logging]
        if to_log:
            ui.primary_button(
                footer, f"🔬  Log these {len(to_log)} questions",
                lambda: self.controller.show_error_log(self.summary.session_id),
                width=240,
            ).pack(side="right")

        wrong = [r for r in self.summary.records if not r.is_correct or r.was_flagged]
        if wrong:
            ui.secondary_button(
                footer, f"🔁  Redo {len(wrong)}",
                lambda: self.controller.start_review_session(
                    [r.question for r in wrong],
                    label=f"Redo — {self.summary.label}",
                ),
                width=130,
            ).pack(side="right", padx=8)
