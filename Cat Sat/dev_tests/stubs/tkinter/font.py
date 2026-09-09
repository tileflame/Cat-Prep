class Font:
    def __init__(self, *a, **kw): self._kw = kw
    def configure(self, **kw): self._kw.update(kw)
    def measure(self, text): return len(str(text)) * 7
    def metrics(self, *a): return {"linespace": 16, "ascent": 12, "descent": 4}
def families(*a, **kw): return ("Inter", "Arial", "Helvetica")
def nametofont(name): return Font()
