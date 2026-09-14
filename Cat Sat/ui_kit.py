"""
ui_kit.py — small reusable CustomTkinter building blocks.

Every screen used to re-declare its own cards, pills and stat tiles with
hard-coded hex values. These helpers keep the look consistent and each screen
file short enough to read in one sitting.
"""

from __future__ import annotations

import os
from collections import OrderedDict

import customtkinter as ctk
from PIL import Image

import background

from config import C, DIFFICULTY_COLOR, FONT, accuracy_color


# ---------------------------------------------------------------------------
# TYPOGRAPHY
# ---------------------------------------------------------------------------

def f(size: int = 14, weight: str = "normal") -> tuple:
    """Font tuple helper: f(20, 'bold')."""
    return (FONT, size, weight) if weight != "normal" else (FONT, size)


def title(parent, text, size=26, color=C.TEXT, **kw):
    return ctk.CTkLabel(parent, text=text, font=f(size, "bold"), text_color=color, **kw)


def body(parent, text, size=13, color=C.TEXT_DIM, **kw):
    return ctk.CTkLabel(parent, text=text, font=f(size), text_color=color, **kw)


def caption(parent, text, size=11, color=C.TEXT_FAINT, **kw):
    return ctk.CTkLabel(parent, text=text, font=f(size), text_color=color, **kw)


# ---------------------------------------------------------------------------
# CONTAINERS
# ---------------------------------------------------------------------------

def card(parent, **kw):
    """Standard raised surface."""
    options = {"fg_color": C.SURFACE, "corner_radius": 14}
    options.update(kw)
    return ctk.CTkFrame(parent, **options)


def row(parent, **kw):
    """Invisible horizontal container."""
    options = {"fg_color": "transparent"}
    options.update(kw)
    return ctk.CTkFrame(parent, **options)


def well(parent, **kw):
    """Sunken surface for image viewers and canvases."""
    options = {"fg_color": C.SURFACE_3, "corner_radius": 10}
    options.update(kw)
    return ctk.CTkFrame(parent, **options)


def divider(parent, pady=(10, 10)):
    line = ctk.CTkFrame(parent, fg_color=C.BORDER, height=1, corner_radius=0)
    line.pack(fill="x", pady=pady)
    return line


# ---------------------------------------------------------------------------
# BUTTONS
# ---------------------------------------------------------------------------

def primary_button(parent, text, command, **kw):
    options = {
        "text": text, "command": command, "font": f(15, "bold"),
        "fg_color": C.GREEN, "hover_color": C.GREEN_DARK,
        "text_color": "#0B1F14", "height": 46, "corner_radius": 10,
    }
    options.update(kw)
    return ctk.CTkButton(parent, **options)


def secondary_button(parent, text, command, **kw):
    options = {
        "text": text, "command": command, "font": f(14, "bold"),
        "fg_color": C.SURFACE_2, "hover_color": C.SLATE,
        "text_color": C.TEXT, "height": 42, "corner_radius": 10,
    }
    options.update(kw)
    return ctk.CTkButton(parent, **options)


def ghost_button(parent, text, command, **kw):
    options = {
        "text": text, "command": command, "font": f(12, "bold"),
        "fg_color": "transparent", "hover_color": C.SURFACE_2,
        "text_color": C.TEXT_DIM, "height": 32, "corner_radius": 8,
        "border_width": 1, "border_color": C.BORDER,
    }
    options.update(kw)
    return ctk.CTkButton(parent, **options)


def danger_button(parent, text, command, **kw):
    options = {
        "text": text, "command": command, "font": f(13, "bold"),
        "fg_color": C.RED, "hover_color": C.RED_DARK, "height": 40, "corner_radius": 10,
    }
    options.update(kw)
    return ctk.CTkButton(parent, **options)


# ---------------------------------------------------------------------------
# INDICATORS
# ---------------------------------------------------------------------------

def pill(parent, text, color=C.BLUE, **kw):
    """Small rounded status chip."""
    options = {
        "text": f" {text} ", "font": f(11, "bold"), "text_color": color,
        "fg_color": C.SURFACE_2, "corner_radius": 20, "height": 24,
    }
    options.update(kw)
    return ctk.CTkLabel(parent, **options)


def difficulty_pill(parent, difficulty, **kw):
    return pill(parent, difficulty, DIFFICULTY_COLOR.get(difficulty, C.TEXT_DIM), **kw)


def stat_tile(parent, label, value, color=C.TEXT, sub=None):
    """
    A metric tile. Returns (frame, value_label) so callers can update the number
    without rebuilding the widget.
    """
    tile = card(parent, fg_color=C.SURFACE, corner_radius=12)
    caption(tile, label.upper(), size=10, color=C.TEXT_FAINT).pack(
        anchor="w", padx=18, pady=(14, 0))
    value_label = ctk.CTkLabel(tile, text=str(value), font=f(26, "bold"), text_color=color)
    value_label.pack(anchor="w", padx=18, pady=(2, 0))
    sub_label = caption(tile, sub or "", size=11, color=C.TEXT_FAINT)
    sub_label.pack(anchor="w", padx=18, pady=(0, 14))
    return tile, value_label, sub_label


def accuracy_bar(parent, label, correct, total, width=260, show_counts=True):
    """
    A labelled horizontal accuracy bar. Used for domain and skill breakdowns.
    """
    pct = (correct / total * 100) if total else 0.0
    color = accuracy_color(pct)

    holder = row(parent)
    holder.pack(fill="x", pady=5)

    header = row(holder)
    header.pack(fill="x")
    ctk.CTkLabel(header, text=label, font=f(13, "bold"), text_color=C.TEXT,
                 anchor="w").pack(side="left")
    right_text = f"{pct:.0f}%" + (f"   ({correct}/{total})" if show_counts else "")
    ctk.CTkLabel(header, text=right_text, font=f(13, "bold"),
                 text_color=color, anchor="e").pack(side="right")

    bar = ctk.CTkProgressBar(holder, height=9, corner_radius=6,
                             fg_color=C.SURFACE_3, progress_color=color)
    bar.pack(fill="x", pady=(5, 0))
    bar.set(max(0.0, min(1.0, pct / 100)))
    return holder


def empty_state(parent, icon, headline, detail, action_text=None, action=None):
    """Friendly placeholder instead of a blank screen."""
    holder = row(parent)
    holder.pack(expand=True, pady=50)
    ctk.CTkLabel(holder, text=icon, font=f(46)).pack(pady=(0, 10))
    title(holder, headline, size=19).pack()
    body(holder, detail, size=13).pack(pady=(6, 0))
    if action_text and action:
        secondary_button(holder, action_text, action, width=220).pack(pady=(18, 0))
    return holder


# ---------------------------------------------------------------------------
# IMAGES
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# IMAGE CACHE
# ---------------------------------------------------------------------------
# The single biggest source of lag in the old build: every navigation, and even
# toggling a flag, re-opened the question PNG from disk and re-scaled it.
# College Board crops are rendered at 200 DPI and are frequently 1700x2000+, so
# that was tens to hundreds of milliseconds on the UI thread, every time.
#
# Two fixes live here:
#   1. an LRU cache keyed on (path, mtime, target width) so a question is
#      decoded once per session, not once per keystroke;
#   2. the PIL image is resized BEFORE it is handed to CTkImage. CTkImage keeps
#      the original and re-resizes it on every redraw and every appearance-mode
#      change — handing it a already-small image makes that a no-op.

_IMAGE_CACHE: "OrderedDict[tuple, tuple]" = OrderedDict()
_CACHE_LIMIT = 40            # questions, not bytes: each entry is already small
_CACHE_STATS = {"hits": 0, "misses": 0, "prefetched": 0}
# Keys currently being decoded on the worker. Without this, navigating quickly
# queues the same image several times and the main thread decodes it again on
# top — more work than having no prefetch at all.
_INFLIGHT: set = set()


def cache_stats() -> dict:
    """Exposed for the profiler and the tests."""
    return dict(_CACHE_STATS, size=len(_IMAGE_CACHE))


def clear_image_cache() -> None:
    _IMAGE_CACHE.clear()
    _INFLIGHT.clear()
    _CACHE_STATS["hits"] = _CACHE_STATS["misses"] = 0
    _CACHE_STATS["prefetched"] = 0


def _cache_key(path: str, max_width: int, max_height, zoom: float):
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0
    # Quantise zoom so tiny float drift doesn't miss the cache.
    return (path, mtime, int(max_width), int(max_height or 0), round(zoom, 2))


def decode_scaled(path: str, max_width: int = 860, max_height: int | None = None,
                  zoom: float = 1.0):
    """
    Open and downscale an image. Pure PIL — no Tk — so it is safe to call from
    the background worker. Returns (PIL.Image, (w, h)) or (None, None).
    """
    try:
        with Image.open(path) as source:
            source.load()
            image = source.convert("RGB") if source.mode not in ("RGB", "RGBA") else source.copy()
    except Exception:
        return None, None

    width, height = image.size
    if width <= 0 or height <= 0:
        return None, None

    target_width = max(120, int(max_width * zoom))
    ratio = min(target_width / width, 6.0)
    if max_height:
        ratio = min(ratio, (max_height * zoom) / height)
    new_size = (max(60, int(width * ratio)), max(40, int(height * ratio)))

    if new_size != image.size:
        try:
            # reducing_gap makes a big downscale roughly an order of magnitude
            # cheaper: PIL does a fast integer reduce first, then a good resample.
            image = image.resize(new_size, Image.LANCZOS, reducing_gap=2.0)
        except TypeError:                     # very old Pillow
            image = image.resize(new_size, Image.LANCZOS)
        except Exception:
            return None, None
    return image, new_size


def _store(key, ctk_image, size):
    _IMAGE_CACHE[key] = (ctk_image, size)
    _IMAGE_CACHE.move_to_end(key)
    while len(_IMAGE_CACHE) > _CACHE_LIMIT:
        _IMAGE_CACHE.popitem(last=False)
    return ctk_image, size


def load_scaled_image(path: str, max_width: int = 860, max_height: int | None = None,
                      zoom: float = 1.0):
    """
    Cached (CTkImage, (w, h)) for a path, or (None, None) if unusable.

    Same signature as before, so every caller keeps working — it is just no
    longer doing the work twice.
    """
    if not path:
        return None, None

    key = _cache_key(path, max_width, max_height, zoom)
    hit = _IMAGE_CACHE.get(key)
    if hit is not None:
        _CACHE_STATS["hits"] += 1
        _IMAGE_CACHE.move_to_end(key)
        return hit

    _CACHE_STATS["misses"] += 1
    _INFLIGHT.discard(key)          # we are about to satisfy it synchronously
    image, new_size = decode_scaled(path, max_width, max_height, zoom)
    if image is None:
        return None, None
    try:
        ctk_image = ctk.CTkImage(light_image=image, dark_image=image, size=new_size)
    except Exception:
        return None, None
    return _store(key, ctk_image, new_size)


def prefetch_image(widget, path: str, max_width: int = 860, zoom: float = 1.0) -> None:
    """
    Warm the cache for a question you are about to show, on the worker thread.

    Decoding happens off the UI thread; only the cheap CTkImage wrapper is built
    back on the Tk thread, because Tk objects must not be created from a worker.
    """
    if not path:
        return
    key = _cache_key(path, max_width, None, zoom)
    if key in _IMAGE_CACHE or key in _INFLIGHT:
        return
    _INFLIGHT.add(key)

    def done(result, error):
        _INFLIGHT.discard(key)
        if error or not result:
            return
        image, size = result
        if image is None or key in _IMAGE_CACHE:
            return
        try:
            _store(key, ctk.CTkImage(light_image=image, dark_image=image, size=size), size)
            _CACHE_STATS["prefetched"] += 1
        except Exception:
            pass

    background.submit(decode_scaled, path, max_width, None, zoom,
                      on_done=done, widget=widget)


class ImagePane:
    """
    A scrollable image viewer with zoom, used by the quiz and review screens.

    Holds a reference to every CTkImage it shows; without that, Tk garbage
    collects the underlying PhotoImage and the picture silently disappears.
    """

    def __init__(self, parent, max_width=860, fallback="No image available"):
        self.frame = ctk.CTkScrollableFrame(parent, fg_color=C.SURFACE_3, corner_radius=10)
        self.label = ctk.CTkLabel(self.frame, text="", font=f(14), text_color=C.TEXT_DIM)
        self.label.pack(expand=True, pady=12)
        self.max_width = max_width
        self.fallback = fallback
        self.zoom = 1.0
        self._path = None
        self._current = None          # keeps the live image alive

    def pack(self, **kw):
        self.frame.pack(**kw)
        return self

    def show(self, path: str | None, note: str | None = None):
        """Display an image, or a readable message when there isn't one."""
        if path == self._path and self._current is not None:
            return True                       # already on screen; do nothing

        self._path = path
        if not path:
            self._current = None
            self.label.configure(image=None, text=note or self.fallback,
                                 text_color=C.TEXT_FAINT)
            return False

        image, _ = load_scaled_image(path, self.max_width, zoom=self.zoom)
        if image is None:
            self._current = None
            self.label.configure(image=None, text=f"Could not open image:\n{path}",
                                 text_color=C.RED)
            return False

        self._current = image
        self.label.configure(image=image, text="")
        return True

    def prefetch(self, paths) -> None:
        """Warm the cache for the questions either side of this one."""
        for path in paths:
            if path:
                prefetch_image(self.frame, path, self.max_width, self.zoom)

    def set_zoom(self, zoom: float):
        zoom = max(0.5, min(2.5, round(zoom, 2)))
        if abs(zoom - self.zoom) < 0.01:
            return
        self.zoom = zoom
        path, self._path = self._path, None      # force a redraw at the new zoom
        self.show(path)

    def zoom_in(self):
        self.set_zoom(self.zoom + 0.15)

    def zoom_out(self):
        self.set_zoom(self.zoom - 0.15)

    def reset_zoom(self):
        self.set_zoom(1.0)


# ---------------------------------------------------------------------------
# FORMATTING
# ---------------------------------------------------------------------------

def format_duration(seconds) -> str:
    """90 -> '1:30', 3720 -> '1h 02m'."""
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return "—"
    if total < 0:
        total = 0
    if total < 3600:
        return f"{total // 60}:{total % 60:02d}"
    return f"{total // 3600}h {(total % 3600) // 60:02d}m"


def format_seconds_short(seconds) -> str:
    """Compact per-question timing, e.g. '48s' or '2m 05s'."""
    try:
        total = float(seconds)
    except (TypeError, ValueError):
        return "—"
    if total < 60:
        return f"{total:.0f}s"
    return f"{int(total // 60)}m {int(total % 60):02d}s"


def format_timestamp(raw) -> str:
    """'2026-08-17 14:05:22' -> 'Aug 17, 2:05 PM'."""
    if not raw:
        return "—"
    text = str(raw)
    try:
        from datetime import datetime
        stamp = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        return stamp.strftime("%b %d, %Y · %I:%M %p").replace(" 0", " ")
    except (ValueError, ImportError):
        return text
