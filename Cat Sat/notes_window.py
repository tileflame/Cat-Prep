"""
notes_window.py, scratchpad that actually remembers what you wrote.

The original version was a pretty text box whose contents vanished the moment
you closed it. Notes are now stored per question id in progress.db, so the note
you left on a tricky Craft and Structure item is waiting for you the next time
that question appears, and shows up in the review screen too.
"""

from __future__ import annotations

import customtkinter as ctk

import attempt_repo
import ui_kit as ui
from config import C

PLACEHOLDER = "Jot down your reasoning, the trap answer, the rule you forgot…"


class NotesWindow(ctk.CTkToplevel):

    def __init__(self, parent, question_id: str):
        super().__init__(parent)
        self.question_id = question_id

        self.title("Scratchpad")
        self.geometry("520x680")
        self.configure(fg_color=C.BG)
        try:
            self.attributes("-topmost", True)
        except Exception:
            pass

        header = ui.row(self)
        header.pack(fill="x", padx=20, pady=(18, 6))
        ui.title(header, "📝  Scratchpad", size=18).pack(side="left")
        self.status = ui.caption(header, "", size=11, color=C.GREEN)
        self.status.pack(side="right")

        self.qid_label = ui.caption(self, f"Question {question_id}", size=11)
        self.qid_label.pack(anchor="w", padx=22)

        # ---- Text notes, saved to the database
        self.textbox = ctk.CTkTextbox(
            self, font=ui.f(14), fg_color=C.SURFACE_3, text_color=C.TEXT,
            height=170, corner_radius=10, border_color=C.BORDER,
        )
        self.textbox.pack(fill="x", padx=20, pady=(8, 6))

        controls = ui.row(self)
        controls.pack(fill="x", padx=20, pady=(0, 8))
        ui.secondary_button(controls, "💾  Save note", self.save_note, width=130).pack(side="left")
        ui.ghost_button(controls, "Clear text", self.clear_text, width=100).pack(side="left", padx=8)

        # ---- Drawing canvas for working out math
        ui.caption(self, "Work it out:", size=12, color=C.TEXT_DIM).pack(
            anchor="w", padx=22, pady=(6, 4))

        self.canvas = ctk.CTkCanvas(self, bg=C.SURFACE_3, highlightthickness=0, cursor="pencil")
        self.canvas.pack(fill="both", expand=True, padx=20, pady=(0, 8))
        self.canvas.bind("<B1-Motion>", self.paint)
        self.canvas.bind("<ButtonRelease-1>", self.reset_stroke)
        self.canvas.bind("<Button-3>", self.erase_at)
        self._last = (None, None)
        self._pen = "#5DADE2"

        palette = ui.row(self)
        palette.pack(fill="x", padx=20, pady=(0, 6))
        for label, color in (("Blue", "#5DADE2"), ("Green", "#2ECC71"),
                             ("Amber", "#F1C40F"), ("Red", "#E74C3C")):
            ctk.CTkButton(palette, text=label, font=ui.f(11, "bold"),
                          fg_color=color, hover_color=color, text_color="#0B1F14",
                          width=64, height=28, corner_radius=8,
                          command=lambda c=color: self.set_pen(c)).pack(side="left", padx=3)
        ui.ghost_button(palette, "🗑 Clear", self.clear_canvas, width=90).pack(side="right")

        self.load_question(question_id)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----------------------------------------------------------------- notes

    def load_question(self, question_id: str):
        """Point this window at another question without reopening it."""
        self.question_id = question_id
        self.qid_label.configure(text=f"Question {question_id}")
        saved = attempt_repo.get_note(question_id)
        self.textbox.delete("1.0", "end")
        self.textbox.insert("1.0", saved if saved else "")
        self.status.configure(text="loaded" if saved else "", text_color=C.TEXT_FAINT)

    def save_note(self):
        try:
            body = self.textbox.get("1.0", "end").strip()
        except Exception:
            return
        attempt_repo.save_note(self.question_id, body)
        self.status.configure(text="saved ✓", text_color=C.GREEN)
        self.after(1800, lambda: self.status.configure(text="")
                   if self.winfo_exists() else None)

    def clear_text(self):
        self.textbox.delete("1.0", "end")

    def _on_close(self):
        # Never lose a note just because the window was closed.
        try:
            self.save_note()
        except Exception:
            pass
        self.destroy()

    # --------------------------------------------------------------- drawing

    def set_pen(self, color):
        self._pen = color

    def paint(self, event):
        x, y = self._last
        if x is not None and y is not None:
            self.canvas.create_line(x, y, event.x, event.y, width=3,
                                    fill=self._pen, capstyle="round", smooth=True)
        self._last = (event.x, event.y)

    def reset_stroke(self, event=None):
        self._last = (None, None)

    def erase_at(self, event):
        """Right-drag paints in the background colour, i.e. erases."""
        self.canvas.create_oval(event.x - 9, event.y - 9, event.x + 9, event.y + 9,
                                fill=C.SURFACE_3, outline="")

    def clear_canvas(self):
        self.canvas.delete("all")
