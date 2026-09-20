"""
desmos_window.py, opens the Desmos graphing calculator.

Two problems this file has now solved:

1. The README noted the app crashed on a second click. The old class launched a
   browser from ``__init__`` with no guard, and the quiz screen then called
   ``winfo_exists()`` on the returned object, which is not a widget.

2. Chrome's ``--app=`` mode reuses whatever size that profile last used, which
   in practice meant Desmos opened maximised over the whole screen. It now gets
   an explicit size and position, computed from the actual screen so it works on
   any resolution, and it is placed on the right-hand side so you can still see
   the question.

The window is a real browser window, so it stays resizable and movable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import webbrowser

DESMOS_URL = "https://www.desmos.com/calculator"

# Fraction of the screen the calculator should take, and the bounds it is
# clamped to so it is neither a postage stamp nor a full-screen takeover.
WIDTH_FRACTION = 0.42
HEIGHT_FRACTION = 0.78
MIN_SIZE = (620, 520)
MAX_SIZE = (1000, 1100)
DEFAULT_SCREEN = (1440, 900)

WINDOWS_BROWSERS = [
    r"C:/Program Files/Google/Chrome/Application/chrome.exe",
    r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    r"C:/Program Files/Microsoft/Edge/Application/msedge.exe",
]

POSIX_BROWSERS = ["google-chrome", "chromium", "chromium-browser",
                  "brave-browser", "microsoft-edge"]

MAC_BROWSERS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]


def calculator_geometry(screen_width: int, screen_height: int) -> tuple[int, int, int, int]:
    """
    (width, height, x, y) for the calculator on a screen of this size.

    Sized as a fraction of the screen and clamped, then parked against the right
    edge with a margin, so it sits beside the question rather than on top of it.
    Pure arithmetic, so it is directly unit-testable.
    """
    screen_width = max(640, int(screen_width or DEFAULT_SCREEN[0]))
    screen_height = max(480, int(screen_height or DEFAULT_SCREEN[1]))

    width = int(screen_width * WIDTH_FRACTION)
    height = int(screen_height * HEIGHT_FRACTION)

    width = max(MIN_SIZE[0], min(MAX_SIZE[0], width))
    height = max(MIN_SIZE[1], min(MAX_SIZE[1], height))

    # Never wider or taller than the screen itself (small laptops, 4:3 monitors).
    width = min(width, screen_width - 40)
    height = min(height, screen_height - 80)

    margin = 24
    x = max(0, screen_width - width - margin)
    y = max(0, (screen_height - height) // 2)
    return width, height, x, y


def _screen_size(parent=None) -> tuple[int, int]:
    """Ask Tk for the real screen size; fall back to a sane default."""
    if parent is not None:
        try:
            return int(parent.winfo_screenwidth()), int(parent.winfo_screenheight())
        except Exception:
            pass
    return DEFAULT_SCREEN


class DesmosWindow:
    """Not a real window, a guarded launcher, kept as a class for compatibility."""

    _last_launch = 0.0
    COOLDOWN_SECONDS = 1.5

    def __init__(self, parent=None, url: str = DESMOS_URL):
        self.url = url
        self.launched = False
        self.geometry = calculator_geometry(*_screen_size(parent))

        now = time.monotonic()
        # Rapid double clicks used to spawn two processes (and crash the caller).
        if now - DesmosWindow._last_launch < DesmosWindow.COOLDOWN_SECONDS:
            return
        DesmosWindow._last_launch = now
        self.launched = self._launch()

    # ---------------------------------------------------------------- launching

    def _flags(self) -> list[str]:
        width, height, x, y = self.geometry
        return [
            f"--app={self.url}",
            f"--window-size={width},{height}",
            f"--window-position={x},{y}",
        ]

    def _candidates(self) -> list[str]:
        if sys.platform.startswith("win"):
            return [p for p in WINDOWS_BROWSERS if os.path.exists(p)]
        if sys.platform == "darwin":
            return [p for p in MAC_BROWSERS if os.path.exists(p)]
        return [p for p in (shutil.which(name) for name in POSIX_BROWSERS) if p]

    def _launch(self) -> bool:
        flags = self._flags()
        for binary in self._candidates():
            try:
                kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
                if sys.platform.startswith("win"):
                    # Don't flash a console window, and don't die with the app.
                    kwargs = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
                subprocess.Popen([binary] + flags, **kwargs)
                return True
            except Exception:
                continue

        # No Chromium-family browser: the default browser can't be sized, but a
        # working calculator beats a correctly-sized nothing.
        try:
            webbrowser.open(self.url)
            return True
        except Exception:
            return False

    # The quiz screen treats this like a window; keep those calls harmless.
    def winfo_exists(self) -> int:
        return 0

    def focus(self):
        return None

    def destroy(self):
        return None
