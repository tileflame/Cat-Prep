"""Stubbed dialogs. Tests set ANSWER to control what the user 'clicks'."""
ANSWER = True
LOG = []

def askyesno(title="", message="", **kw):
    LOG.append(("askyesno", title, message)); return ANSWER

def askokcancel(title="", message="", **kw):
    LOG.append(("askokcancel", title, message)); return ANSWER

def askretrycancel(title="", message="", **kw):
    LOG.append(("askretrycancel", title, message)); return ANSWER

def showinfo(title="", message="", **kw):
    LOG.append(("showinfo", title, message)); return "ok"

def showwarning(title="", message="", **kw):
    LOG.append(("showwarning", title, message)); return "ok"

def showerror(title="", message="", **kw):
    LOG.append(("showerror", title, message)); return "ok"
