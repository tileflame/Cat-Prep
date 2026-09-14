"""Minimal tkinter stand-in so Cat SAT screens can be exercised without a display."""


class TclError(Exception):
    pass


_EVENT_LOG = []


class Event:
    def __init__(self, **kw):
        self.x = kw.get("x", 0)
        self.y = kw.get("y", 0)
        self.width = kw.get("width", 1000)
        self.height = kw.get("height", 700)
        self.keysym = kw.get("keysym", "")
        self.char = kw.get("char", "")
        self.widget = kw.get("widget", None)


class Variable:
    def __init__(self, master=None, value=None, name=None):
        self._value = value if value is not None else self._default
        self._traces = []

    def get(self):
        return self._value

    def set(self, value):
        self._value = value
        for cb in list(self._traces):
            cb()

    def trace_add(self, mode, callback):
        self._traces.append(lambda: callback(None, None, mode))
        return f"trace{len(self._traces)}"

    trace = trace_add


class StringVar(Variable):
    _default = ""


class IntVar(Variable):
    _default = 0


class DoubleVar(Variable):
    _default = 0.0


class BooleanVar(Variable):
    _default = False


class _Misc:
    """Everything a tk widget can do that our screens actually call."""

    _clock = [0]
    _pending = []
    _pack_seq = [0]

    def __init__(self, master=None, **kw):
        self.master = master
        self._kw = dict(kw)
        self._children = []
        self._alive = True
        self._bindings = {}
        self._geometry_manager = None
        self._w = 1          # deliberately 1: pre-layout, like real tk
        self._h = 1
        if master is not None and hasattr(master, "_children"):
            master._children.append(self)

    # ---- geometry ----
    def pack(self, **kw):
        self._geometry_manager = "pack"
        self._pack_kw = kw
        _Misc._pack_seq[0] += 1
        self._pack_order = _Misc._pack_seq[0]
        return self

    def grid(self, **kw):
        self._geometry_manager = "grid"
        self._grid_kw = kw
        return self

    def place(self, **kw):
        self._geometry_manager = "place"
        return self

    def pack_forget(self):
        self._geometry_manager = None

    def grid_forget(self):
        self._geometry_manager = None

    def place_forget(self):
        self._geometry_manager = None

    def pack_propagate(self, flag=None):
        return None

    def grid_propagate(self, flag=None):
        return None

    def grid_columnconfigure(self, *a, **kw):
        return None

    def grid_rowconfigure(self, *a, **kw):
        return None

    def columnconfigure(self, *a, **kw):
        return None

    def rowconfigure(self, *a, **kw):
        return None

    # ---- config ----
    def configure(self, **kw):
        self._kw.update(kw)
        return self

    config = configure

    def cget(self, key):
        return self._kw.get(key)

    def keys(self):
        return list(self._kw)

    def __getitem__(self, key):
        return self._kw.get(key)

    # ---- tree ----
    def winfo_children(self):
        return [c for c in self._children if getattr(c, "_alive", False)]

    def winfo_exists(self):
        return 1 if self._alive else 0

    def winfo_ismapped(self):
        return 1 if self._geometry_manager else 0

    def winfo_width(self):
        return self._w

    def winfo_height(self):
        return self._h

    def winfo_reqwidth(self):
        return self._w

    def winfo_reqheight(self):
        return self._h

    def winfo_screenwidth(self):
        return 1920

    def winfo_screenheight(self):
        return 1080

    def winfo_rootx(self):
        return 0

    def winfo_rooty(self):
        return 0

    def winfo_toplevel(self):
        node = self
        while getattr(node, "master", None) is not None:
            node = node.master
        return node

    def destroy(self):
        for child in list(self._children):
            try:
                child.destroy()
            except Exception:
                pass
        self._children = []
        self._alive = False
        # Drop scheduled callbacks belonging to a dead widget.
        _Misc._pending[:] = [p for p in _Misc._pending if p[2] is not self]

    # ---- events ----
    def bind(self, sequence=None, func=None, add=None):
        self._bindings.setdefault(sequence, []).append(func)
        return f"bind{len(self._bindings)}"

    def bind_all(self, sequence=None, func=None, add=None):
        return self.bind(sequence, func, add)

    def unbind(self, sequence, funcid=None):
        self._bindings.pop(sequence, None)

    def unbind_all(self, sequence):
        self._bindings.pop(sequence, None)

    def event_generate(self, sequence, **kw):
        for func in self._bindings.get(sequence, []):
            func(Event(**kw))

    def focus(self):
        return None

    focus_set = focus
    focus_force = focus

    def lift(self, *a):
        return None

    def lower(self, *a):
        return None

    def update(self):
        return None

    def update_idletasks(self):
        return None

    # ---- scheduling ----
    def after(self, ms, func=None, *args):
        if func is None:
            return None
        token = f"after#{len(_Misc._pending)}#{id(func)}"
        _Misc._pending.append((_Misc._clock[0] + ms, token, self, func, args))
        return token

    def after_idle(self, func=None, *args):
        return self.after(0, func, *args)

    def after_cancel(self, token):
        _Misc._pending[:] = [p for p in _Misc._pending if p[1] != token]


class Widget(_Misc):
    pass


class Frame(Widget):
    pass


class Label(Widget):
    pass


class Button(Widget):
    def invoke(self):
        cmd = self._kw.get("command")
        if cmd:
            return cmd()


class Entry(Widget):
    def __init__(self, master=None, **kw):
        super().__init__(master, **kw)
        self._text = ""
        var = kw.get("textvariable")
        if var is not None:
            self._text = str(var.get())

    def get(self):
        var = self._kw.get("textvariable")
        return str(var.get()) if var is not None else self._text

    def insert(self, index, text):
        self._text = f"{self._text}{text}" if index != 0 else f"{text}{self._text}"
        var = self._kw.get("textvariable")
        if var is not None:
            var.set(self._text)

    def delete(self, first, last=None):
        self._text = ""
        var = self._kw.get("textvariable")
        if var is not None:
            var.set("")

    def select_range(self, *a):
        return None

    def icursor(self, *a):
        return None


class Text(Widget):
    def __init__(self, master=None, **kw):
        super().__init__(master, **kw)
        self._text = ""

    def get(self, start="1.0", end="end"):
        return self._text

    def insert(self, index, text):
        self._text += text

    def delete(self, start, end=None):
        self._text = ""

    def see(self, index):
        return None


class Canvas(Widget):
    def __init__(self, master=None, **kw):
        super().__init__(master, **kw)
        self.items = []

    def create_line(self, *a, **kw):
        self.items.append(("line", a, kw))
        return len(self.items)

    def create_oval(self, *a, **kw):
        self.items.append(("oval", a, kw))
        return len(self.items)

    def create_rectangle(self, *a, **kw):
        self.items.append(("rect", a, kw))
        return len(self.items)

    def create_text(self, *a, **kw):
        self.items.append(("text", a, kw))
        return len(self.items)

    def create_polygon(self, *a, **kw):
        self.items.append(("poly", a, kw))
        return len(self.items)

    def create_arc(self, *a, **kw):
        self.items.append(("arc", a, kw))
        return len(self.items)

    def delete(self, *a):
        self.items = []

    def coords(self, *a):
        return []

    def itemconfig(self, *a, **kw):
        return None

    def tag_bind(self, *a, **kw):
        return None

    def yview_moveto(self, *a):
        return None

    def xview_moveto(self, *a):
        return None


class Scrollbar(Widget):
    def set(self, *a):
        return None


class Tk(_Misc):
    def __init__(self, *a, **kw):
        super().__init__(None, **kw)
        self._title = ""
        self._w, self._h = 1200, 820

    def title(self, text=None):
        if text is not None:
            self._title = text
        return self._title

    def geometry(self, spec=None):
        return "1200x820+0+0"

    def minsize(self, *a):
        return None

    def maxsize(self, *a):
        return None

    def resizable(self, *a):
        return None

    def iconbitmap(self, *a):
        return None

    def wm_iconbitmap(self, *a):
        return None

    def attributes(self, *a):
        return None

    def protocol(self, name=None, func=None):
        self._bindings.setdefault(f"protocol:{name}", []).append(func)

    def state(self, *a):
        return "normal"

    def deiconify(self):
        return None

    def withdraw(self):
        return None

    def grab_set(self):
        return None

    def grab_release(self):
        return None

    def transient(self, *a):
        return None

    def mainloop(self, *a):
        return None

    def quit(self):
        return None

    def option_add(self, *a):
        return None

    def tk_setPalette(self, *a):
        return None

    def call(self, *a):
        return ""

    def eval(self, *a):
        return ""


class Toplevel(Tk):
    def __init__(self, master=None, **kw):
        _Misc.__init__(self, master, **kw)
        self._title = ""
        self._w, self._h = 600, 500


# ---- test-driver helpers -------------------------------------------------

def pump(ms=1000, max_events=400):
    """Advance the fake clock and run any callbacks that came due."""
    _Misc._clock[0] += ms
    ran = 0
    while ran < max_events:
        due = [p for p in _Misc._pending if p[0] <= _Misc._clock[0]]
        if not due:
            break
        due.sort(key=lambda p: p[0])
        entry = due[0]
        _Misc._pending.remove(entry)
        _, _, widget, func, args = entry
        if getattr(widget, "_alive", False):
            func(*args)
        ran += 1
    return ran


def pending_count():
    return len(_Misc._pending)


def reset_clock():
    _Misc._clock[0] = 0
    _Misc._pending.clear()


def _focus_get(self):
    return getattr(_Misc, "_focused", [None])[0]


_Misc._focused = [None]
_Misc.focus_get = _focus_get
