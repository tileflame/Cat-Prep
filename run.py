"""
Launcher, start Cat SAT.

Runs the local server and opens the app. The UI is a web page now, but it is
still an entirely local, offline program: the server binds to 127.0.0.1 and
nothing leaves your machine.

    python run.py              # start and open it
    python run.py --no-browser # start only, print the URL
"""
import os
import subprocess
import sys
import time
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "Cat Sat")

if not os.path.isdir(APP):
    sys.exit(f"Could not find {APP}\nIs run.py sitting next to the 'Cat Sat' folder?")

os.chdir(APP)
sys.path.insert(0, APP)


# ---------------------------------------------------------------------------
# DEPENDENCIES
# ---------------------------------------------------------------------------
# Cat Prep needs two packages that are not in the standard library, and both are
# only used for reading PDFs: PyMuPDF to rasterise question images, Pillow to
# write them. Everything else — the server, the database, the adaptive engine —
# is standard library.
#
# Asking a student to run `pip install PyMuPDF Pillow` before anything works was
# the single most common complaint after the import time. It is also completely
# unnecessary: this script is already running in the Python that would do the
# installing, so it can just do it.
#
# Two rules:
#   - Only install what is actually missing, and say so before doing it.
#   - If the install fails, DO NOT die. The packages are needed to IMPORT a
#     question bank, not to use one. Somebody whose bank is already imported
#     should still get their app.

REQUIRED = [("fitz", "PyMuPDF", "reads the College Board PDFs"),
            ("PIL", "Pillow", "writes the question images")]


def _missing():
    import importlib.util
    return [(mod, pkg, why) for mod, pkg, why in REQUIRED
            if importlib.util.find_spec(mod) is None]


def ensure_dependencies() -> None:
    missing = _missing()
    if not missing:
        return

    print("=" * 58)
    print("  First run, Cat Prep needs two packages:")
    for _mod, pkg, why in missing:
        print(f"    {pkg:<10} {why}")
    print("  Installing them now. This happens once.")
    print("=" * 58)

    req = os.path.join(ROOT, "requirements.txt")
    command = [sys.executable, "-m", "pip", "install", "--quiet"]
    command += ["-r", req] if os.path.isfile(req) else [p for _m, p, _w in missing]

    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    except Exception as exc:                              # noqa: BLE001
        print(f"  Could not run pip: {exc}")
        result = None

    still = _missing()
    if not still:
        print("  Done.\n")
        return

    # Installing failed. Say exactly what to type, then carry on — an already
    # imported question bank does not need either package to study from.
    print("  Could not install automatically.")
    if result is not None and (result.stderr or "").strip():
        print("  pip said:", (result.stderr or "").strip().splitlines()[-1][:160])
    print("\n  Run this yourself, then start the app again:")
    print(f"    {os.path.basename(sys.executable)} -m pip install "
          + " ".join(pkg for _m, pkg, _w in still))
    print("\n  Starting anyway, you can still study from a question bank that")
    print("  is already imported. You just cannot import a new one yet.\n")


ensure_dependencies()

import server  # noqa: E402

# Chrome's --app mode gives a clean window with no address bar, sized to the
# screen. Same approach as the calculator, and it falls back to a normal tab.
WINDOWS_BROWSERS = [
    r"C:/Program Files/Google/Chrome/Application/chrome.exe",
    r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    r"C:/Program Files/Microsoft/Edge/Application/msedge.exe",
]
MAC_BROWSERS = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
POSIX_BROWSERS = ["google-chrome", "chromium", "chromium-browser", "microsoft-edge"]


def app_window(url):
    """Open `url` as a chromeless app window, or fall back to the default browser."""
    import shutil
    candidates = []
    if sys.platform.startswith("win"):
        candidates = [p for p in WINDOWS_BROWSERS if os.path.exists(p)]
    elif sys.platform == "darwin":
        candidates = [p for p in MAC_BROWSERS if os.path.exists(p)]
    else:
        candidates = [p for p in (shutil.which(n) for n in POSIX_BROWSERS) if p]

    for binary in candidates:
        try:
            kwargs = {}
            if sys.platform.startswith("win"):
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            else:
                kwargs.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.Popen([binary, f"--app={url}", "--window-size=1400,900"], **kwargs)
            return True
        except Exception:
            continue
    try:
        webbrowser.open(url)
        return True
    except Exception:
        return False


def main():
    srv, url = server.create_server()
    print("=" * 58)
    print("  Cat SAT is running")
    print(f"  {url}")
    print("  Close this window (or Ctrl+C) to stop.")
    print("=" * 58)

    if "--no-browser" not in sys.argv:
        server.serve_forever_in_thread(srv)
        time.sleep(0.3)
        if not app_window(url):
            print(f"Could not open a browser. Go to {url} yourself.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    else:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass

    print("\nStopping…")
    srv.shutdown()
    srv.server_close()


if __name__ == "__main__":
    main()
