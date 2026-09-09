"""
error_log_screen.py — Pass 2 and Pass 3 of the three-pass review.

Section 07 of the plan calls this "the engine of the whole plan":

    "Every miss and every lucky guess gets a row. For each one, write the
     one-sentence rule you'll apply next time — not a summary of the
     explanation, an instruction to your future self."

So this screen shows exactly those rows — misses AND lucky guesses — and for
each one asks two things:

    WHY did you miss it?   (one of the 11 root-cause codes)
    What will you do?      (one sentence, executable)

Tagging a row automatically schedules the cold redo, then +3 days, then +10.
The diagnosis panel at the top reads next week's prescription off the dominant
root cause, and refuses to guess until you've tagged enough rows.
"""

from __future__ import annotations

from datetime import date

import customtkinter as ctk

import attempt_repo
import diagnostic
import question_repo
import ui_kit as ui
from config import C, DIFFICULTY_COLOR, accuracy_color

BANNED_PHRASES = ("read carefully", "read more carefully", "be careful",
                  "pay attention", "slow down", "focus")


class ErrorLogScreen(ctk.CTkFrame):

    def __init__(self, parent, controller, session_id=None):
        super().__init__(parent, fg_color="transparent")
        self.controller = controller
        self.session_id = session_id
        self.rows_by_attempt = {}
        self._image_refs = []          # keeps on-screen CTkImages alive
        self._rows = []
        self._questions = {}
        self._shown = 0

        header = ui.row(self)
        header.pack(fill="x", padx=30, pady=(22, 4))
        ui.title(header, "🔬  Error Log", size=25).pack(side="left")

        right = ui.row(header)
        right.pack(side="right")
        ui.secondary_button(right, "📋 Plan", controller.show_plan, width=110).pack(
            side="left", padx=6)
        ui.secondary_button(right, "🏠 Home", controller.show_home_screen,
                            width=110).pack(side="left")

        ui.caption(self,
                   "Every miss and every lucky guess. Tag the cause, write one executable "
                   "sentence, and the redo gets scheduled for tomorrow, +3 days and +10 days.",
                   size=12).pack(anchor="w", padx=32, pady=(0, 8))

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.pack(side="top", fill="both", expand=True, padx=26, pady=(0, 16))

        self.refresh()

    # ---------------------------------------------------------------- refresh

    PAGE_SIZE = 12          # rows rendered at once; "Show more" adds another page

    def refresh(self):
        for widget in self.scroll.winfo_children():
            widget.destroy()
        self.rows_by_attempt = {}
        self._shown = 0

        rows = (attempt_repo.logged_attempts(self.session_id) if self.session_id
                else self._recent_logged())
        self._rows = rows

        # One batched lookup for every question on screen, instead of one query
        # per row. This is what makes the log usable with a hundred entries.
        self._questions = question_repo.fetch_by_ids([r["question_id"] for r in rows])

        self._diagnosis_panel()

        if not rows:
            ui.empty_state(
                self.scroll, "✅", "Nothing to log",
                "No misses and no lucky guesses recorded yet. Finish a drill or a module "
                "and everything you weren't sure about will land here.",
                "Today's plan", self.controller.show_plan,
            )
            return

        untagged = [r for r in rows if not (r.get("root_cause") or "")]
        head = ui.row(self.scroll)
        head.pack(fill="x", pady=(4, 8))
        ui.title(head, f"{len(rows)} logged question(s)", size=17).pack(side="left")
        if untagged:
            ui.pill(head, f"{len(untagged)} still untagged", C.ORANGE).pack(side="left", padx=10)
        else:
            ui.pill(head, "all tagged ✓", C.GREEN).pack(side="left", padx=10)

        self._list_holder = ui.row(self.scroll)
        self._list_holder.pack(fill="x")
        self._more_holder = ui.row(self.scroll)
        self._more_holder.pack(fill="x", pady=(4, 12))
        self._render_page()

    def _render_page(self):
        """Render the next page of rows. Keeps the widget count bounded."""
        for widget in self._more_holder.winfo_children():
            widget.destroy()

        end = min(self._shown + self.PAGE_SIZE, len(self._rows))
        for row in self._rows[self._shown:end]:
            self._log_row(row)
        self._shown = end

        remaining = len(self._rows) - self._shown
        if remaining > 0:
            ui.secondary_button(
                self._more_holder,
                f"Show {min(remaining, self.PAGE_SIZE)} more  ({remaining} left)",
                self._render_page, width=280).pack(pady=6)

    def _recent_logged(self):
        """
        Everything logged across recent sessions.

        One query, not one per session — the old version called
        logged_attempts() in a loop over 25 sessions.
        """
        return attempt_repo.recent_logged(limit=120)

    # --------------------------------------------------------------- diagnosis

    def _diagnosis_panel(self):
        counts = attempt_repo.cause_counts(since_days=7)
        totals = attempt_repo.log_totals(since_days=7)
        lucky = attempt_repo.lucky_counts(since_days=7, group_by="domain")
        tagged_total = sum(counts.values())

        card = ui.card(self.scroll)
        card.pack(fill="x", pady=(4, 10))

        head = ui.row(card)
        head.pack(fill="x", padx=20, pady=(16, 6))
        ui.title(head, "This week's diagnosis", size=17).pack(side="left")
        ui.caption(head, "sorted by WHY, per Section 07", size=11).pack(side="left", padx=10)

        # Leading indicators.
        strip = ui.row(card)
        strip.pack(fill="x", padx=14, pady=(0, 8))
        tile, _, _ = ui.stat_tile(strip, "Logged this week", totals["logged"], C.BLUE,
                                  "misses + lucky guesses")
        tile.pack(side="left", fill="both", expand=True, padx=5)
        tile, _, _ = ui.stat_tile(strip, "Wrong", totals["wrong"], C.RED, "actual misses")
        tile.pack(side="left", fill="both", expand=True, padx=5)
        tile, _, _ = ui.stat_tile(strip, "Lucky guesses", totals["lucky"], C.PURPLE,
                                  "should fall steadily")
        tile.pack(side="left", fill="both", expand=True, padx=5)
        process = sum(counts.get(c, 0) for c in ("C", "M", "A", "D"))
        tile, _, _ = ui.stat_tile(strip, "Process causes", process, C.AMBER, "C + M + A + D")
        tile.pack(side="left", fill="both", expand=True, padx=5)

        if tagged_total < 4:
            ui.caption(card,
                       f"Tag at least 4 questions to get a diagnosis — {tagged_total} tagged "
                       "so far. Untagged rows can't tell you anything.",
                       size=12, color=C.ORANGE).pack(anchor="w", padx=20, pady=(0, 16))
            return

        # Cause distribution.
        bars = ui.row(card)
        bars.pack(fill="x", padx=20, pady=(0, 8))
        for code, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            if not count:
                continue
            label = f"{code} · {diagnostic.CAUSE_LABEL.get(code, code)}"
            ui.accuracy_bar(bars, label, count, tagged_total, show_counts=True)

        findings = diagnostic.diagnose(counts, tagged_total, lucky)
        if not findings:
            ui.caption(card,
                       "No single cause dominates yet. Keep logging — the pattern usually "
                       "shows up over a week, not a session.",
                       size=12).pack(anchor="w", padx=20, pady=(4, 16))
            return

        for finding in findings:
            block = ui.card(card, fg_color=C.SURFACE_2, corner_radius=10)
            block.pack(fill="x", padx=20, pady=5)

            line = ui.row(block)
            line.pack(fill="x", padx=16, pady=(12, 2))
            ctk.CTkLabel(line, text=finding["verdict"], font=ui.f(15, "bold"),
                         text_color=C.TEXT).pack(side="left")
            ui.pill(line, finding["trigger"], C.TEXT_DIM).pack(side="left", padx=10)

            ctk.CTkLabel(block, text="→  " + finding["do"], font=ui.f(12),
                         text_color=C.GREEN, wraplength=880, justify="left",
                         anchor="w").pack(anchor="w", padx=16, pady=(4, 2))
            ctk.CTkLabel(block, text="✗  " + finding["dont"], font=ui.f(12),
                         text_color=C.ORANGE, wraplength=880, justify="left",
                         anchor="w").pack(anchor="w", padx=16, pady=(0, 12))

        ui.caption(card, diagnostic.LAGGING_NOTE, size=11).pack(
            anchor="w", padx=20, pady=(2, 16))

    # ---------------------------------------------------------------- log rows

    def _log_row(self, row):
        attempt_id = row["attempt_id"]
        lucky = bool(row["is_correct"]) and row.get("confidence") == "shaky"
        question = self._questions.get(row["question_id"])

        card = ui.card(self._list_holder, fg_color=C.SURFACE, corner_radius=12)
        card.pack(fill="x", pady=5)

        # --- header line: verdict, where it came from, when, how long it took
        top = ui.row(card)
        top.pack(fill="x", padx=18, pady=(14, 2))
        ctk.CTkLabel(top, text="🍀 LUCKY" if lucky else "❌ MISS",
                     font=ui.f(12, "bold"),
                     text_color=C.PURPLE if lucky else C.RED).pack(side="left")
        ui.caption(top, f"  {row.get('domain') or '—'}  ·  {row.get('skill') or '—'}",
                   size=12).pack(side="left", padx=6)
        difficulty = row.get("difficulty") or "Medium"
        ui.pill(top, difficulty, DIFFICULTY_COLOR.get(difficulty, C.TEXT_DIM)).pack(side="left")

        meta = []
        if row.get("time_spent_ms"):
            meta.append(ui.format_seconds_short(row["time_spent_ms"] / 1000))
        if row.get("answered_at"):
            meta.append(ui.format_timestamp(row["answered_at"]))
        if row.get("module_number"):
            meta.append(f"Module {row['module_number']}")
        if row.get("session_label"):
            meta.append(row["session_label"])
        if meta:
            ui.caption(top, "  ·  ".join(meta), size=11).pack(side="right")

        # --- answers, stated plainly
        answers = ui.row(card)
        answers.pack(fill="x", padx=18, pady=(4, 2))
        given = row.get("selected_answer") or "— skipped —"
        ctk.CTkLabel(answers, text=f"Your answer:  {given}", font=ui.f(13, "bold"),
                     text_color=C.GREEN if row["is_correct"] else C.RED).pack(side="left")
        ctk.CTkLabel(answers, text=f"Correct answer:  {row.get('correct_answer') or '—'}",
                     font=ui.f(13, "bold"), text_color=C.GREEN).pack(side="left", padx=24)
        if row.get("eliminated"):
            ui.caption(answers, f"crossed out: {row['eliminated']}", size=11).pack(side="left")

        if lucky:
            ui.caption(card,
                       "You got this right but weren't sure. It counts as a miss — fragile "
                       "knowledge flips on a harder module.",
                       size=11, color=C.PURPLE).pack(anchor="w", padx=18, pady=(2, 4))

        # --- THE QUESTION ITSELF. The old log showed only metadata, which made
        # it impossible to review anything without opening another screen.
        self._question_block(card, row, question)

        # --- WHY axis
        ui.caption(card, "WHY DID YOU MISS IT?", size=10).pack(anchor="w", padx=18, pady=(8, 2))
        chips = ui.row(card)
        chips.pack(fill="x", padx=14, pady=(0, 6))

        current = row.get("root_cause") or ""
        buttons = {}
        for index, (code, label, detail, _family) in enumerate(diagnostic.ROOT_CAUSES):
            btn = ctk.CTkButton(
                chips, text=f"{code}  {label}", font=ui.f(11, "bold"),
                width=132, height=30, corner_radius=8,
                fg_color=C.BLUE if code == current else C.SURFACE_2,
                hover_color=C.SLATE, text_color=C.TEXT,
                command=lambda a=attempt_id, c=code: self._set_cause(a, c),
            )
            btn.grid(row=index // 6, column=index % 6, padx=3, pady=3, sticky="ew")
            buttons[code] = btn
        for column in range(6):
            chips.grid_columnconfigure(column, weight=1)

        # --- the executable sentence
        ui.caption(card, "ONE SENTENCE — AN INSTRUCTION TO YOUR FUTURE SELF",
                   size=10).pack(anchor="w", padx=18, pady=(6, 2))

        entry_row = ui.row(card)
        entry_row.pack(fill="x", padx=18, pady=(0, 4))

        var = ctk.StringVar(value=row.get("fix_note") or "")
        entry = ctk.CTkEntry(
            entry_row, textvariable=var, font=ui.f(13), height=38,
            fg_color=C.SURFACE_3, text_color=C.TEXT, corner_radius=8,
            border_color=C.BORDER,
            placeholder_text="e.g. \"Check whether both sides are complete before choosing "
                             "a semicolon.\"",
        )
        entry.pack(side="left", fill="x", expand=True)

        status = ui.caption(card, "", size=11)

        save = ctk.CTkButton(entry_row, text="Save", font=ui.f(12, "bold"), width=80,
                             height=38, corner_radius=8, fg_color=C.GREEN,
                             hover_color=C.GREEN_DARK, text_color="#0B1F14",
                             command=lambda a=attempt_id, v=var, st=status, r=row:
                                 self._save_fix(a, v, st, r))
        save.pack(side="left", padx=(8, 0))
        # Enter saves, so you never have to reach for the mouse.
        entry.bind("<Return>",
                   lambda e, a=attempt_id, v=var, st=status, r=row:
                       self._save_fix(a, v, st, r))

        status.pack(anchor="w", padx=18, pady=(0, 12))
        if row.get("fix_note"):
            status.configure(text="saved ✓", text_color=C.GREEN)

        self.rows_by_attempt[attempt_id] = {"buttons": buttons, "row": row,
                                            "var": var, "status": status}

    def _question_block(self, card, row, question):
        """
        Show the question, collapsed by default.

        Collapsed keeps the log skimmable and the widget count low; expanding
        decodes the image (cached) and shows the College Board rationale too.
        """
        holder = ui.row(card)
        holder.pack(fill="x", padx=18, pady=(6, 2))

        if question is None:
            ui.caption(holder,
                       f"Question {row['question_id']} is no longer in the bank — re-run "
                       "sat_importer.py to restore it.",
                       size=11, color=C.ORANGE).pack(anchor="w")
            return

        toggle_row = ui.row(holder)
        toggle_row.pack(fill="x")

        body = ui.row(holder)          # populated on first expand

        state = {"open": False, "built": False}

        def toggle():
            state["open"] = not state["open"]
            if state["open"]:
                if not state["built"]:
                    self._build_question_body(body, row, question)
                    state["built"] = True
                body.pack(fill="x", pady=(6, 2))
                button.configure(text="▲  Hide question")
            else:
                body.pack_forget()
                button.configure(text="▼  Show question & rationale")

        button = ui.ghost_button(toggle_row, "▼  Show question & rationale", toggle,
                                 width=230)
        button.pack(side="left")
        ui.caption(toggle_row, f"id {question.question_id}", size=10).pack(side="left", padx=10)
        if question.has_rationale:
            ui.pill(toggle_row, "rationale available", C.GREEN).pack(side="left")
        else:
            ui.pill(toggle_row, "no rationale captured", C.TEXT_FAINT).pack(side="left")

    def _build_question_body(self, body, row, question):
        """Decode and lay out the question + rationale images once."""
        ui.caption(body, "QUESTION", size=10).pack(anchor="w", pady=(4, 2))
        self._image_into(body, question.image_path,
                         "The question image is missing. Re-run sat_importer.py.")

        note = attempt_repo.get_note(question.question_id)
        if note:
            ui.caption(body, "YOUR NOTE", size=10).pack(anchor="w", pady=(8, 2))
            ctk.CTkLabel(body, text=note, font=ui.f(12), text_color=C.TEXT_DIM,
                         wraplength=820, justify="left", anchor="w").pack(anchor="w")

        ui.caption(body, "COLLEGE BOARD RATIONALE", size=10).pack(anchor="w", pady=(8, 2))
        self._image_into(body, question.rationale_path,
                         "No rationale image was captured for this question. Make sure you "
                         "downloaded the PDF with answers and explanations included, then "
                         "re-import.")

    def _image_into(self, parent, path, missing_message):
        if not path:
            ctk.CTkLabel(parent, text=missing_message, font=ui.f(11),
                         text_color=C.TEXT_FAINT, wraplength=800,
                         justify="left", anchor="w").pack(anchor="w", pady=(0, 4))
            return
        image, _ = ui.load_scaled_image(path, max_width=760)
        if image is None:
            ctk.CTkLabel(parent, text=f"Could not open {path}", font=ui.f(11),
                         text_color=C.RED, anchor="w").pack(anchor="w")
            return
        # Bounded: the decoded images live in ui_kit's LRU cache, so this list
        # only needs to stop Tk garbage-collecting the ones on screen.
        self._image_refs.append(image)
        if len(self._image_refs) > 24:
            del self._image_refs[:-24]
        ctk.CTkLabel(parent, text="", image=image).pack(anchor="w", pady=(0, 6))

    # ----------------------------------------------------------------- actions

    def _set_cause(self, attempt_id, code):
        attempt_repo.tag_attempt(attempt_id, root_cause=code)
        entry = self.rows_by_attempt.get(attempt_id)
        if entry:
            for candidate, btn in entry["buttons"].items():
                btn.configure(fg_color=C.BLUE if candidate == code else C.SURFACE_2)
            entry["row"]["root_cause"] = code
            self._schedule_redo(entry["row"])

    def _save_fix(self, attempt_id, var, status, row, event=None):
        """
        Reject the non-instruction. The plan is explicit that "read carefully"
        is the correct diagnosis and a useless instruction — Brandon wrote it 10
        times out of 17 and the same misses kept recurring.
        """
        text = var.get().strip()
        if not text:
            status.configure(text="Write one sentence — it's the part that makes it stick.",
                             text_color=C.ORANGE)
            return

        lowered = text.lower()
        if any(phrase in lowered for phrase in BANNED_PHRASES) and len(text) < 60:
            status.configure(
                text="That's a diagnosis, not an instruction. Name a specific, checkable "
                     "action — if a stranger couldn't watch you and tell whether you did "
                     "it, it isn't a rule.",
                text_color=C.RED)
            return

        attempt_repo.tag_attempt(attempt_id, fix_note=text)
        row["fix_note"] = text
        status.configure(text="saved ✓", text_color=C.GREEN)
        self._schedule_redo(row)

    def _schedule_redo(self, row):
        """Queue the cold redo once the row is properly logged."""
        if not (row.get("root_cause") and row.get("fix_note")):
            return
        today = date.today()
        attempt_repo.schedule_redo(
            row["question_id"], stage=0,
            due_on=diagnostic.due_date_for(0, today).isoformat(),
            source_attempt=row.get("attempt_id"),
            section=row.get("section") or "", domain=row.get("domain") or "",
            skill=row.get("skill") or "",
        )
