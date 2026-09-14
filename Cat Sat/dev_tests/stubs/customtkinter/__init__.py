"""
CustomTkinter stand-in for headless testing.

Only mirrors the API surface Cat SAT actually uses, and deliberately keeps the
same constructor/method names as the real library so a typo in the app fails
here too. Widgets record what they were given so tests can assert on the tree.
"""

import tkinter as tk
from tkinter import Event, StringVar, IntVar, DoubleVar, BooleanVar, TclError  # noqa: F401

APPEARANCE = "Dark"
THEME = "dark-blue"
SCALING = 1.0


def set_appearance_mode(mode):
    global APPEARANCE
    APPEARANCE = mode


def get_appearance_mode():
    return APPEARANCE


def set_default_color_theme(theme):
    global THEME
    THEME = theme


def set_widget_scaling(v):
    global SCALING
    SCALING = v


def deactivate_automatic_dpi_awareness():
    return None


def set_window_scaling(v):
    return None


class CTkFont:
    def __init__(self, family=None, size=12, weight="normal", **kw):
        self.family, self.size, self.weight = family, size, weight

    def configure(self, **kw):
        self.__dict__.update(kw)

    def measure(self, text):
        return len(str(text)) * 7


class CTkImage:
    def __init__(self, light_image=None, dark_image=None, size=(20, 20)):
        self.light_image = light_image
        self.dark_image = dark_image
        self._size = size

    def cget(self, key):
        return {"size": self._size, "light_image": self.light_image}.get(key)

    def configure(self, **kw):
        if "size" in kw:
            self._size = kw["size"]


class _CTkBase(tk.Widget):
    """Shared base: records kwargs, supports the geometry + config API."""

    def __init__(self, master=None, **kw):
        super().__init__(master, **kw)

    def _test_text(self):
        value = self._kw.get("text", "")
        return "" if value is None else str(value)


class CTkFrame(_CTkBase):
    pass


class CTkLabel(_CTkBase):
    pass


class CTkButton(_CTkBase):
    def invoke(self):
        """Fire the command exactly as a click would."""
        command = self._kw.get("command")
        if command is None:
            return None
        if self._kw.get("state") == "disabled":
            return None
        return command()


class CTkEntry(tk.Entry, _CTkBase):
    def __init__(self, master=None, **kw):
        tk.Entry.__init__(self, master, **kw)


class CTkTextbox(tk.Text, _CTkBase):
    def __init__(self, master=None, **kw):
        tk.Text.__init__(self, master, **kw)


class CTkCanvas(tk.Canvas, _CTkBase):
    def __init__(self, master=None, **kw):
        tk.Canvas.__init__(self, master, **kw)


class CTkScrollableFrame(_CTkBase):
    def __init__(self, master=None, **kw):
        super().__init__(master, **kw)

    def _parent_canvas_yview(self, *a):
        return None


class CTkOptionMenu(_CTkBase):
    def __init__(self, master=None, **kw):
        super().__init__(master, **kw)
        self._values = list(kw.get("values") or [])
        self._variable = kw.get("variable")

    def configure(self, **kw):
        if "values" in kw:
            self._values = list(kw["values"] or [])
        return super().configure(**kw)

    def cget(self, key):
        if key == "values":
            return self._values
        return super().cget(key)

    def set(self, value):
        if self._variable is not None:
            self._variable.set(value)
        self._kw["_selected"] = value

    def get(self):
        if self._variable is not None:
            return self._variable.get()
        return self._kw.get("_selected", "")

    def invoke_command(self, value):
        """Simulate the user picking `value` from the menu."""
        self.set(value)
        command = self._kw.get("command")
        if command:
            return command(value)


class CTkComboBox(CTkOptionMenu):
    pass


class CTkSegmentedButton(CTkOptionMenu):
    pass


class CTkCheckBox(_CTkBase):
    def __init__(self, master=None, **kw):
        super().__init__(master, **kw)
        self._variable = kw.get("variable")
        self._on = kw.get("onvalue", 1)
        self._off = kw.get("offvalue", 0)

    def get(self):
        if self._variable is not None:
            return self._variable.get()
        return self._kw.get("_state", self._off)

    def select(self):
        if self._variable is not None:
            self._variable.set(self._on)
        self._kw["_state"] = self._on

    def deselect(self):
        if self._variable is not None:
            self._variable.set(self._off)
        self._kw["_state"] = self._off

    def toggle(self):
        if self.get() == self._on:
            self.deselect()
        else:
            self.select()
        command = self._kw.get("command")
        if command:
            command()


class CTkSwitch(CTkCheckBox):
    pass


class CTkRadioButton(_CTkBase):
    def __init__(self, master=None, **kw):
        super().__init__(master, **kw)
        self._variable = kw.get("variable")
        self._value = kw.get("value")

    def select(self):
        if self._variable is not None:
            self._variable.set(self._value)


class CTkProgressBar(_CTkBase):
    def __init__(self, master=None, **kw):
        super().__init__(master, **kw)
        self._value = 0.0

    def set(self, value):
        self._value = value

    def get(self):
        return self._value

    def start(self):
        return None

    def stop(self):
        return None


class CTkSlider(CTkProgressBar):
    pass


class CTkTabview(_CTkBase):
    def __init__(self, master=None, **kw):
        super().__init__(master, **kw)
        self._tabs = {}

    def add(self, name):
        frame = CTkFrame(self)
        self._tabs[name] = frame
        return frame

    def tab(self, name):
        return self._tabs[name]

    def set(self, name):
        self._kw["_current"] = name

    def get(self):
        return self._kw.get("_current", "")


class CTk(tk.Tk):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)

    def configure(self, **kw):
        self._kw.update(kw)
        return self

    config = configure


class CTkToplevel(tk.Toplevel):
    def configure(self, **kw):
        self._kw.update(kw)
        return self

    config = configure


class CTkInputDialog:
    RESULT = "42"

    def __init__(self, *a, **kw):
        self._kw = kw

    def get_input(self):
        return CTkInputDialog.RESULT


# ---- test-driver helpers -------------------------------------------------

def walk(widget):
    """Depth-first iterator over a widget tree."""
    yield widget
    for child in getattr(widget, "_children", []):
        yield from walk(child)


def texts(widget):
    """All visible text in a tree, for assertions."""
    out = []
    for node in walk(widget):
        value = getattr(node, "_kw", {}).get("text")
        if isinstance(value, str) and value.strip():
            out.append(value)
    return out


def buttons(widget, contains=None):
    """Every CTkButton in a tree, optionally filtered by label substring."""
    found = [n for n in walk(widget) if isinstance(n, CTkButton)]
    if contains:
        needle = contains.lower()
        found = [b for b in found if needle in str(b._kw.get("text", "")).lower()]
    return found


def click(widget, contains):
    """Click the first button whose label contains `contains`."""
    matches = buttons(widget, contains)
    if not matches:
        raise AssertionError(
            f"no button matching {contains!r}; available: "
            f"{[b._kw.get('text') for b in buttons(widget)]}"
        )
    return matches[0].invoke()
