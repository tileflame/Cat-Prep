"""
quiz_screen.py, the test-taking surface.

Upgrades over the original screen:
  * knows which module and tier it is in, and shows a progress bar
  * times every question individually, accumulating across revisits
  * keyboard shortcuts (A-D, arrows, F to flag, +/- to zoom)
  * cross-out mode, so eliminating a choice is discoverable instead of a
    hidden right-click
  * hide/show the countdown, like Bluebook
  * warns about unanswered questions before you submit
  * notes are saved per question in the database instead of vanishing
  * the countdown is cancelled when the screen is destroyed, so a finished test
    can never fire a timer tick into a dead widget
"""

from __future__ import annotations

import time

import customtkinter as ctk
from tkinter import messagebox

import ui_kit as ui
from config import C, TIER_LABEL
from desmos_window import DesmosWindow
from models import AttemptRecord
from notes_window import NotesWindow

CHOICES = ["A", "B", "C", "D"]


class QuizScreen(ctk.CTkFrame):

    def __init__(self, parent, controller, plan, *, timed=True,
                 on_submit=None, context_label="", per_question_timer=False,
                 is_redo_session=False):
        super().__init__(parent, fg_color="transparent")
        self.controller = controller
        self.plan = plan
        self.questions = list(plan.questions)
        self.on_submit = on_submit
        self.per_question_timer = per_question_timer
        self.is_redo_session = is_redo_session

        # ---- answer state
        self.current_idx = 0
        self.user_answers: dict[int, str] = {}
        self.flagged: set[int] = set()
        self.eliminated: dict[int, set] = {}
        self.time_spent_ms: dict[int, int] = {}
        self._entered_at = None
        self.cross_out_mode = False
        # "Log every question where you weren't at least 90% confident — even the
        # ones you got right." Indices marked shaky here become error-log rows.
        self.shaky: set[int] = set()

        # ---- windows
        self.notes_window = None
        self.index_window = None
        self.desmos_win = None
        self._index_buttons = []

        # ---- timing
        self.total_limit = plan.time_limit_seconds if timed else 0
        self.remaining = self.total_limit or None
        self.timer_hidden = False
        self._timer_job = None
        self._question_timer_job = None
        self._prefetch_job = None
        self.started_at = time.time()
        self._submitted = False

        self._build(context_label)
        self._bind_keys()
        self.load_question(0)
        if self.remaining:
            self._start_countdown()
        if self.per_question_timer:
            self._tick_question()

    # ------------------------------------------------------------------ build

    def _build(self, context_label):
        shell = ui.card(self, fg_color=C.SURFACE)
        shell.pack(fill="both", expand=True, padx=16, pady=16)

        # ---- Top bar: where am I, how long left, what can I do
        top = ui.row(shell)
        top.pack(fill="x", padx=20, pady=(14, 6))

        left = ui.row(top)
        left.pack(side="left")

        heading = context_label or f"{self.plan.section}, Module {self.plan.module_number}"
        self.context_label = ctk.CTkLabel(left, text=heading, font=ui.f(17, "bold"),
                                          text_color=C.TEXT)
        self.context_label.pack(side="left")

        if self.plan.tier and self.plan.tier in TIER_LABEL:
            ui.pill(left, TIER_LABEL[self.plan.tier].split("-")[0].strip(),
                    C.BLUE).pack(side="left", padx=10)

        self.q_num_label = ctk.CTkLabel(left, text="", font=ui.f(13),
                                        text_color=C.TEXT_DIM)
        self.q_num_label.pack(side="left", padx=10)

        # Countdown. Clicking it hides the number, same as the real test.
        self.timer_btn = ctk.CTkButton(
            top, text="", font=ui.f(14, "bold"), text_color=C.AMBER,
            fg_color=C.SURFACE_2, hover_color=C.SLATE_DARK, width=110, height=32,
            corner_radius=8, command=self.toggle_timer_visibility,
        )
        if self.remaining or self.per_question_timer:
            self.timer_btn.pack(side="left", padx=14)

        actions = ui.row(top)
        actions.pack(side="right")

        if any(q.section == "Math" for q in self.questions):
            ui.ghost_button(actions, "🧮 Calc", self.open_desmos, width=68).pack(side="left", padx=3)

        ui.ghost_button(actions, "📝 Note", self.open_notes, width=72).pack(side="left", padx=3)

        self.cross_btn = ui.ghost_button(actions, "⊘ Cross out", self.toggle_cross_out_mode, width=96)
        self.cross_btn.pack(side="left", padx=3)

        self.shaky_btn = ui.ghost_button(actions, "🤔 Not sure", self.toggle_shaky, width=92)
        self.shaky_btn.pack(side="left", padx=3)

        self.flag_btn = ui.ghost_button(actions, "🚩 Flag", self.toggle_flag, width=76)
        self.flag_btn.pack(side="left", padx=3)

        ctk.CTkButton(actions, text="Submit", font=ui.f(13, "bold"),
                      fg_color=C.RED, hover_color=C.RED_DARK, width=84, height=32,
                      corner_radius=8, command=self.confirm_submit).pack(side="left", padx=(12, 0))

        # ---- Progress bar
        self.progress = ctk.CTkProgressBar(shell, height=5, corner_radius=3,
                                           fg_color=C.SURFACE_3, progress_color=C.BLUE)
        self.progress.pack(fill="x", padx=20, pady=(0, 8))
        self.progress.set(0)

        # ---- Answer area + navigation are built BEFORE the image pane and
        # anchored to the bottom. Tk hands out cavity space in packing order, so
        # if the expanding image pane went first it would push the answer buttons
        # and the Next button off-screen on a short window.
        self.answer_area = ui.row(shell)

        # ---- Question image (created after, packed last, absorbs the slack)
        self.image_pane = ui.ImagePane(shell, max_width=880,
                                       fallback="[ Question image missing ]")

        # Grid-in entry
        self.entry_holder = ui.row(self.answer_area)
        self.ans_entry = ctk.CTkEntry(
            self.entry_holder, placeholder_text="Type your answer (fractions like 3/4 are fine)…",
            font=ui.f(15), height=46, fg_color=C.SURFACE_3, text_color=C.TEXT,
            corner_radius=10, border_color=C.BORDER,
        )
        self.ans_entry.pack(fill="x", expand=True)

        # Multiple choice grid
        self.mc_frame = ui.row(self.answer_area)
        self.mc_buttons = {}
        for i, choice in enumerate(CHOICES):
            btn = ctk.CTkButton(
                self.mc_frame, text=f"{choice}", font=ui.f(15, "bold"),
                fg_color=C.SURFACE_2, hover_color=C.SLATE, text_color=C.TEXT,
                height=46, corner_radius=10, anchor="w",
                command=lambda c=choice: self.on_choice_click(c),
            )
            # Right-click still eliminates, for anyone used to Bluebook.
            btn.bind("<Button-3>", lambda e, c=choice: self.eliminate(c))
            btn.bind("<Button-2>", lambda e, c=choice: self.eliminate(c))
            btn.grid(row=i // 2, column=i % 2, padx=6, pady=4, sticky="ew")
            self.mc_buttons[choice] = btn
        self.mc_frame.grid_columnconfigure((0, 1), weight=1)

        self.hint_label = ui.caption(
            shell,
            "Keys: A–D answer · ← → navigate · F flag · S not-sure · X cross-out · +/− zoom",
            size=10,
        )
        # ---- Bottom navigation
        nav = ui.row(shell)

        self.prev_btn = ui.secondary_button(nav, "◄  Previous", self.prev_question, width=120)
        self.prev_btn.pack(side="left")

        ui.secondary_button(nav, "🗂  Question Index", self.open_index, width=170).pack(
            side="left", padx=10)

        zoom = ui.row(nav)
        zoom.pack(side="left", padx=6)
        ui.ghost_button(zoom, "−", self.zoom_out, width=34).pack(side="left", padx=2)
        ui.ghost_button(zoom, "＋", self.zoom_in, width=34).pack(side="left", padx=2)

        self.next_btn = ctk.CTkButton(nav, text="Next  ►", font=ui.f(14, "bold"),
                                      fg_color=C.BLUE, hover_color=C.BLUE_DARK,
                                      width=130, height=42, corner_radius=10,
                                      command=self.next_question)
        self.next_btn.pack(side="right")

        # Pack the bottom stack in reverse visual order, then let the image pane
        # take everything that is left over.
        nav.pack(side="bottom", fill="x", padx=20, pady=(0, 12))
        self.hint_label.pack(side="bottom", pady=(0, 4))
        self.answer_area.pack(side="bottom", fill="x", padx=20, pady=(0, 6))
        self.image_pane.pack(side="top", fill="both", expand=True, padx=20, pady=(0, 8))

    # ------------------------------------------------------------- keyboard

    def _bind_keys(self):
        """
        Shortcuts are bound on the root window, so they work wherever focus is.
        Every binding id is stored and removed in destroy(); otherwise a finished
        quiz keeps stealing keystrokes from the next screen.
        """
        self._key_bindings = []
        try:
            root = self.winfo_toplevel()
        except Exception:
            return

        def register(sequence, handler):
            try:
                self._key_bindings.append((sequence, root.bind(sequence, handler, add="+")))
            except Exception:
                pass

        for choice in CHOICES:
            register(f"<KeyPress-{choice.lower()}>",
                     lambda e, c=choice: self._key_choice(c))
        for index, choice in enumerate(CHOICES, start=1):
            register(f"<KeyPress-{index}>", lambda e, c=choice: self._key_choice(c))

        register("<Right>", lambda e: self._key_guard(self.next_question))
        register("<Left>", lambda e: self._key_guard(self.prev_question))
        register("<KeyPress-f>", lambda e: self._key_guard(self.toggle_flag))
        register("<KeyPress-s>", lambda e: self._key_guard(self.toggle_shaky))
        register("<KeyPress-x>", lambda e: self._key_guard(self.toggle_cross_out_mode))
        for sequence in ("<plus>", "<equal>"):
            register(sequence, lambda e: self._key_guard(self.zoom_in))
        register("<minus>", lambda e: self._key_guard(self.zoom_out))

    def _typing_in_entry(self) -> bool:
        """
        True when a text field owns the keyboard, so shortcuts stay out of the
        way. Checked two ways: the grid-in box is what's on screen, or tk says a
        real Entry/Text widget has focus (covers the notes scratchpad too).
        """
        question = self._question()
        if question is not None and question.is_open_ended:
            return True
        try:
            focused = self.focus_get()
            return focused is not None and focused.winfo_class() in ("Entry", "Text")
        except Exception:
            return False

    def _key_guard(self, action):
        """Run a shortcut unless the screen is gone or the user is typing."""
        if self.winfo_exists() and not self._typing_in_entry():
            action()

    def _key_choice(self, choice):
        if not self.winfo_exists() or self._typing_in_entry():
            return
        self.on_choice_click(choice)

    # ------------------------------------------------------------- questions

    def _question(self):
        if not self.questions:
            return None
        return self.questions[min(self.current_idx, len(self.questions) - 1)]

    def load_question(self, idx):
        """
        Move to a different question. Does the expensive work (image decode).

        State-only changes, flagging, marking not-sure, picking a choice, go
        through _refresh_state() instead, which touches no images at all. The old
        build called this method for every toggle and re-decoded the PNG each
        time, which is where most of the lag came from.
        """
        if not self.questions:
            return
        self._bank_time()
        self.current_idx = max(0, min(idx, len(self.questions) - 1))
        self._entered_at = time.time()

        question = self.questions[self.current_idx]
        self.q_num_label.configure(
            text=f"Question {self.current_idx + 1} of {len(self.questions)}  ·  "
                 f"{question.domain}"
        )
        self.progress.set((self.current_idx + 1) / len(self.questions))
        self.image_pane.show(question.image_path)
        self._prefetch_neighbours()

        if question.is_open_ended:
            if self.mc_frame.winfo_ismapped():
                self.mc_frame.pack_forget()
            if not self.entry_holder.winfo_ismapped():
                self.entry_holder.pack(fill="x", expand=True)
            self.ans_entry.delete(0, "end")
            saved = self.user_answers.get(self.current_idx, "")
            if saved:
                self.ans_entry.insert(0, saved)
        else:
            if self.entry_holder.winfo_ismapped():
                self.entry_holder.pack_forget()
            if not self.mc_frame.winfo_ismapped():
                self.mc_frame.pack(fill="x", expand=True)

        self._refresh_state()

    def _prefetch_neighbours(self):
        """
        Warm the next and previous question on the worker thread.

        Scheduled through after() rather than run inline, so the current
        question paints first and a fast walk through the module cancels stale
        prefetches instead of piling them up.
        """
        if self._prefetch_job:
            try:
                self.after_cancel(self._prefetch_job)
            except Exception:
                pass
        self._prefetch_job = self.after(120, self._do_prefetch)

    def _do_prefetch(self):
        self._prefetch_job = None
        if not self.winfo_exists():
            return
        paths = []
        for offset in (1, -1):
            index = self.current_idx + offset
            if 0 <= index < len(self.questions):
                paths.append(self.questions[index].image_path)
        if paths:
            self.image_pane.prefetch(paths)

    def _refresh_state(self):
        """
        Repaint only the things that change with answer/flag/not-sure state.
        No database reads, no image decoding, no widget creation.
        """
        question = self._question()
        if question is not None and not question.is_open_ended:
            self._refresh_choices()

        shaky = self.current_idx in self.shaky
        self.shaky_btn.configure(
            fg_color=C.PURPLE if shaky else "transparent",
            text_color="#FFFFFF" if shaky else C.TEXT_DIM,
        )

        flagged = self.current_idx in self.flagged
        self.flag_btn.configure(
            text="🚩 Flagged" if flagged else "🚩 Flag",
            fg_color=C.ORANGE if flagged else "transparent",
            text_color="#1A1206" if flagged else C.TEXT_DIM,
        )

        self.prev_btn.configure(state="normal" if self.current_idx > 0 else "disabled")
        last = self.current_idx >= len(self.questions) - 1
        self.next_btn.configure(text="Review  ►" if last else "Next  ►")

        # The index popup is a live view; update only the buttons that changed.
        if self.index_window is not None and self.index_window.winfo_exists():
            self._update_index_buttons()

    def _refresh_choices(self):
        selected = self.user_answers.get(self.current_idx, "")
        struck = self.eliminated.get(self.current_idx, set())
        for choice, btn in self.mc_buttons.items():
            if choice in struck:
                btn.configure(text=_strike(f"{choice}"), fg_color=C.SURFACE_3,
                              text_color=C.TEXT_FAINT)
            elif choice == selected:
                btn.configure(text=f"{choice}   ✓", fg_color=C.BLUE, text_color="#FFFFFF")
            else:
                btn.configure(text=f"{choice}", fg_color=C.SURFACE_2, text_color=C.TEXT)

    # --------------------------------------------------------------- answers

    def on_choice_click(self, choice):
        """Left click either selects or crosses out, depending on the mode."""
        question = self._question()
        if question is None or question.is_open_ended:
            return
        if self.cross_out_mode:
            self.eliminate(choice)
        else:
            self.select(choice)

    def select(self, choice):
        if choice in self.eliminated.get(self.current_idx, set()):
            return                       # can't pick something you crossed out
        if self.user_answers.get(self.current_idx) == choice:
            del self.user_answers[self.current_idx]      # click again to clear
        else:
            self.user_answers[self.current_idx] = choice
        self._refresh_state()

    def eliminate(self, choice):
        struck = self.eliminated.setdefault(self.current_idx, set())
        if self.user_answers.get(self.current_idx) == choice:
            return                       # don't cross out your own answer
        if choice in struck:
            struck.discard(choice)
        else:
            struck.add(choice)
        self._refresh_state()

    def toggle_cross_out_mode(self):
        self.cross_out_mode = not self.cross_out_mode
        self.cross_btn.configure(
            fg_color=C.PURPLE if self.cross_out_mode else "transparent",
            text_color="#FFFFFF" if self.cross_out_mode else C.TEXT_DIM,
            text="⊘ Crossing" if self.cross_out_mode else "⊘ Cross out",
        )

    def save_current(self):
        """Persist the grid-in box into user_answers before navigating away."""
        question = self._question()
        if question is None or not question.is_open_ended:
            return
        try:
            text = self.ans_entry.get().strip()
        except Exception:
            return
        if text:
            self.user_answers[self.current_idx] = text
        else:
            self.user_answers.pop(self.current_idx, None)

    def toggle_shaky(self):
        """
        Mark that you weren't ≥90% confident. If you get it right anyway it is
        logged as a lucky guess, the plan is emphatic that those are next
        month's misses, not wins.
        """
        if self.current_idx in self.shaky:
            self.shaky.discard(self.current_idx)
        else:
            self.shaky.add(self.current_idx)
        self._refresh_state()

    def toggle_flag(self):
        if self.current_idx in self.flagged:
            self.flagged.discard(self.current_idx)
        else:
            self.flagged.add(self.current_idx)
        self._refresh_state()

    # ------------------------------------------------------------ navigation

    def next_question(self):
        self.save_current()
        if self.current_idx < len(self.questions) - 1:
            self.load_question(self.current_idx + 1)
        else:
            self.confirm_submit()

    def prev_question(self):
        self.save_current()
        if self.current_idx > 0:
            self.load_question(self.current_idx - 1)

    def jump_to(self, idx):
        self.save_current()
        self.load_question(idx)
        if self.index_window is not None and self.index_window.winfo_exists():
            self.index_window.destroy()
            self.index_window = None

    def zoom_in(self):
        self.image_pane.zoom_in()

    def zoom_out(self):
        self.image_pane.zoom_out()

    # ----------------------------------------------------------------- timing

    def _bank_time(self):
        """Add the time spent on the question we're leaving."""
        if self._entered_at is None:
            return
        elapsed = int((time.time() - self._entered_at) * 1000)
        if 0 < elapsed < 3_600_000:            # ignore absurd values (machine slept)
            self.time_spent_ms[self.current_idx] = (
                self.time_spent_ms.get(self.current_idx, 0) + elapsed
            )
        self._entered_at = None

    def toggle_timer_visibility(self):
        self.timer_hidden = not self.timer_hidden
        self._render_timer()

    def _start_countdown(self):
        """Paint the starting time, then tick once a second."""
        self._render_timer()
        self._timer_job = self.after(1000, self._tick)

    def _tick(self):
        if not self.winfo_exists() or self._submitted or self.remaining is None:
            return
        self.remaining -= 1
        if self.remaining <= 0:
            # Submit exactly at zero rather than a second later.
            self.remaining = 0
            self.timer_btn.configure(text="⏱ Time's up", text_color=C.RED)
            self.submit(auto=True)
            return
        self._render_timer()
        self._timer_job = self.after(1000, self._tick)

    def _tick_question(self):
        """
        Per-question stopwatch for drills. Skips the repaint entirely while the
        timer is hidden, so a hidden stopwatch costs nothing.
        """
        if not self.winfo_exists() or self._submitted:
            return
        if not self.timer_hidden:
            self._render_timer()
        self._question_timer_job = self.after(1000, self._tick_question)

    def _render_timer(self):
        if not self.winfo_exists():
            return
        if self.timer_hidden:
            self.timer_btn.configure(text="⏱ show", text_color=C.TEXT_FAINT)
            return

        if self.remaining is not None:
            mins, secs = divmod(max(0, self.remaining), 60)
            color = C.RED if self.remaining <= 60 else (
                C.ORANGE if self.remaining <= 300 else C.AMBER)
            self.timer_btn.configure(text=f"⏳ {mins:02d}:{secs:02d}", text_color=color)
        elif self.per_question_timer:
            spent = self.time_spent_ms.get(self.current_idx, 0) / 1000
            if self._entered_at:
                spent += time.time() - self._entered_at
            self.timer_btn.configure(text=f"⏱ {int(spent // 60):02d}:{int(spent % 60):02d}",
                                     text_color=C.TEXT_DIM)

    # ------------------------------------------------------------- side panes

    def open_notes(self):
        question = self._question()
        if question is None:
            return
        if self.notes_window is None or not self.notes_window.winfo_exists():
            self.notes_window = NotesWindow(self, question.question_id)
        else:
            # Point the existing window at the current question instead of
            # opening a second one. The original crashed on a double click.
            try:
                self.notes_window.load_question(question.question_id)
                self.notes_window.focus()
            except Exception:
                self.notes_window = NotesWindow(self, question.question_id)

    def open_desmos(self):
        # DesmosWindow launches a browser; it is safe to call repeatedly now.
        self.desmos_win = DesmosWindow(self)

    def open_index(self):
        if self.index_window is not None and self.index_window.winfo_exists():
            self.index_window.focus()
            return
        self.index_window = ctk.CTkToplevel(self)
        self.index_window.title("Question Index")
        self.index_window.geometry("400x560")
        self.index_window.configure(fg_color=C.BG)
        try:
            self.index_window.attributes("-topmost", True)
        except Exception:
            pass

        ui.title(self.index_window, "Jump to question", size=17).pack(pady=(16, 2))
        ui.caption(self.index_window,
                   "green = answered · orange = flagged · grey = untouched").pack(pady=(0, 8))
        self.index_scroll = ctk.CTkScrollableFrame(self.index_window, fg_color="transparent")
        self.index_scroll.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self._render_index()

    def _render_index(self):
        """Build the index buttons once; _update_index_buttons repaints them."""
        for widget in self.index_scroll.winfo_children():
            widget.destroy()
        self._index_buttons = []
        for idx in range(len(self.questions)):
            btn = ctk.CTkButton(
                self.index_scroll, text="", font=ui.f(12, "bold"),
                fg_color=C.SURFACE_2, hover_color=C.SLATE, height=34,
                corner_radius=8, anchor="w",
                command=lambda i=idx: self.jump_to(i),
            )
            btn.pack(fill="x", pady=3)
            self._index_buttons.append(btn)
        self._update_index_buttons()

    def _update_index_buttons(self):
        """
        Recolour the existing index buttons in place.

        The old version destroyed and rebuilt all 27 CTkButtons every time
        anything changed. Each CTkButton is a canvas plus labels, so that was the
        second biggest source of stutter after image decoding.
        """
        buttons = getattr(self, "_index_buttons", None)
        if not buttons:
            return
        for idx, btn in enumerate(buttons):
            if idx >= len(self.questions):
                break
            question = self.questions[idx]
            answered = bool(self.user_answers.get(idx, "").strip())
            if idx == self.current_idx:
                color, prefix = C.BLUE, "➡"
            elif idx in self.flagged:
                color, prefix = C.ORANGE, "🚩"
            elif idx in self.shaky:
                color, prefix = C.PURPLE, "🤔"
            elif answered:
                color, prefix = C.GREEN_DARK, "✓"
            else:
                color, prefix = C.SURFACE_2, " "
            label = f"{prefix}  Q{idx + 1}   ·   {question.domain}  ·  {question.difficulty}"
            if btn.cget("fg_color") != color or btn.cget("text") != label:
                btn.configure(text=label, fg_color=color)

    # ------------------------------------------------------------- finishing

    def confirm_submit(self):
        self.save_current()
        unanswered = [i + 1 for i in range(len(self.questions))
                      if not self.user_answers.get(i, "").strip()]
        if unanswered:
            preview = ", ".join(str(n) for n in unanswered[:12])
            if len(unanswered) > 12:
                preview += f" … (+{len(unanswered) - 12} more)"
            proceed = messagebox.askyesno(
                "Submit anyway?",
                f"{len(unanswered)} question(s) still unanswered:\n{preview}\n\n"
                "Submit now? There is no penalty for guessing on the SAT.",
            )
            if not proceed:
                return
        self.submit()

    def submit(self, auto=False):
        if self._submitted:
            return
        self._submitted = True
        self.save_current()
        self._bank_time()
        self._cancel_timers()

        records = []
        for idx, question in enumerate(self.questions):
            answer = self.user_answers.get(idx, "").strip()
            records.append(AttemptRecord(
                question=question,
                selected_answer=answer,
                is_correct=question.check(answer),
                was_flagged=idx in self.flagged,
                eliminated=set(self.eliminated.get(idx, set())),
                time_spent_ms=self.time_spent_ms.get(idx, 0),
                position=idx + 1,
                confidence="shaky" if idx in self.shaky else "sure",
                is_redo=self.is_redo_session,
            ))

        elapsed = int(time.time() - self.started_at)
        if self.on_submit:
            self.on_submit(records, elapsed, auto)

    def _cancel_timers(self):
        for job in (self._timer_job, self._question_timer_job, self._prefetch_job):
            if job:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
        self._timer_job = self._question_timer_job = self._prefetch_job = None

    def destroy(self):
        """Tear down cleanly: stop timers, release key bindings, close popups."""
        self._cancel_timers()
        try:
            root = self.winfo_toplevel()
            for sequence, funcid in getattr(self, "_key_bindings", []):
                try:
                    root.unbind(sequence, funcid)
                except Exception:
                    pass
        except Exception:
            pass
        for window in (self.notes_window, self.index_window):
            try:
                if window is not None and window.winfo_exists():
                    window.destroy()
            except Exception:
                pass
        super().destroy()


def _strike(text: str) -> str:
    """Unicode combining strikethrough, so a crossed-out choice reads as struck."""
    return "".join(char + "̶" for char in text)
