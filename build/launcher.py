"""
launcher.py, the entry point of the bundled app.

run.py is the entry point when you have Python and a folder of source. This is
the entry point when you have a single downloaded file and nothing else, and
the difference between the two is entirely about paths.

WHY THIS FILE EXISTS
--------------------
A frozen app runs with the working directory set to wherever the user happened
to launch it from, the Desktop, Downloads, anywhere. But sat_importer.py
resolves "pdfs", "images" and "database/questions.db" relative to the current
directory, and config.py builds DATA_DIR the same way. Left alone, a bundled
app would scatter a question bank across whatever folder the icon was
double-clicked in, and find nothing the next time it started somewhere else.

So the first thing that happens here, before importing anything from the app,
is choosing one stable folder and moving into it. Everything downstream then
behaves exactly as it does from source.

WHERE THE DATA GOES
-------------------
Next to the executable, if that location is writable, this keeps the app
self-contained, which is what someone expects from a single downloaded file,
and makes "delete the folder" a complete uninstall.

If it is not writable (macOS .app bundles are read-only, and Program Files is
protected on Windows), it falls back to the user's home folder. Silently
failing to write is not an option: it would look like the import worked and
then lose everything.
"""

import os
import sys
from pathlib import Path

APP_NAME = "CatPrep"


def _frozen_root() -> Path:
    """The folder the user actually sees, beside the .exe, or beside the .app."""
    exe = Path(sys.executable).resolve()
    if sys.platform == "darwin" and ".app/Contents/" in str(exe):
        # …/CatPrep.app/Contents/MacOS/CatPrep -> the folder holding the .app
        return exe.parents[3]
    return exe.parent


def _writable(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def choose_data_dir() -> Path:
    """Beside the app if possible, otherwise the home folder."""
    if getattr(sys, "frozen", False):
        beside = _frozen_root() / f"{APP_NAME} Data"
        if _writable(beside):
            return beside
        return Path.home() / APP_NAME
    # Running from source: behave exactly like run.py.
    return Path(__file__).resolve().parent.parent / "Cat Sat"


def main() -> int:
    data_dir = choose_data_dir()
    for sub in ("pdfs", "images", "database"):
        (data_dir / sub).mkdir(parents=True, exist_ok=True)

    # config.py reads this on import, so it must be set BEFORE any app import.
    os.environ.setdefault("CATSAT_DATA_DIR", str(data_dir))
    os.chdir(str(data_dir))                 # sat_importer uses relative paths

    if getattr(sys, "frozen", False):
        # web/ and the app modules live inside the bundle, not beside it.
        bundle = Path(getattr(sys, "_MEIPASS", _frozen_root()))
        sys.path.insert(0, str(bundle))
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Cat Sat"))

    print(f"{APP_NAME}, data folder: {data_dir}")

    import server
    srv, url = server.create_server()

    # Print the URL BEFORE opening a window, so that anything watching this
    # process can see where it landed. The port is chosen at random by
    # free_port(), so this line is the only way to know it — the CI step that
    # launches the built app and checks it actually serves reads it from here.
    # flush=True matters. When stdout is a pipe rather than a console, Python
    # block-buffers it, and the next thing this function does is serve_forever()
    # — which never returns, so the buffer would never be flushed and a watcher
    # would wait forever for a line that had already been "printed".
    print("=" * 58, flush=True)
    print(f"  {APP_NAME} is running", flush=True)
    print(f"  Open:  {url}", flush=True)

    # Open the window. server.main() only starts the server and prints a URL —
    # opening a browser is run.py's job, and a bundled app never goes through
    # run.py. Without this the app "runs but nothing opens", which from the
    # user's side is indistinguishable from it being broken.
    #
    # CATPREP_NO_BROWSER exists for the build check: a CI runner has no desktop,
    # and a browser launch there is at best noise and at worst a hang.
    if not os.environ.get("CATPREP_NO_BROWSER"):
        _open_window(url)

    print("  Stop:  close this window, or Ctrl+C")
    print("=" * 58)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        srv.shutdown()
        srv.server_close()
    return 0


# Chrome's --app mode gives a clean window with no address bar. Same approach
# as run.py; falls back to the default browser, then to doing nothing but
# saying so, because a silent failure here looks like a crash.
_WINDOWS_BROWSERS = [
    r"C:/Program Files/Google/Chrome/Application/chrome.exe",
    r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    r"C:/Program Files/Microsoft/Edge/Application/msedge.exe",
]
_MAC_BROWSERS = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
_POSIX_BROWSERS = ["google-chrome", "chromium", "chromium-browser", "microsoft-edge"]


def _open_window(url: str) -> bool:
    import shutil
    import subprocess
    import webbrowser

    if sys.platform.startswith("win"):
        candidates = [p for p in _WINDOWS_BROWSERS if os.path.exists(p)]
    elif sys.platform == "darwin":
        candidates = [p for p in _MAC_BROWSERS if os.path.exists(p)]
    else:
        candidates = [p for p in (shutil.which(n) for n in _POSIX_BROWSERS) if p]

    for binary in candidates:
        try:
            kwargs = {}
            if sys.platform.startswith("win"):
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            else:
                kwargs.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.Popen([binary, f"--app={url}", "--window-size=1400,900"], **kwargs)
            return True
        except Exception:                                   # noqa: BLE001, S112
            continue
    try:
        webbrowser.open(url)
        return True
    except Exception:                                       # noqa: BLE001
        print(f"\n  Could not open a browser automatically. Go to {url} yourself.")
        return False


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:                                      # noqa: BLE001
        # A bundled app that dies silently gives the user nothing to report.
        import traceback
        traceback.print_exc()
        print("\nCat Prep hit an error starting up. Copy the text above into a "
              "GitHub issue: https://github.com/tileflame/Cat-Prep/issues")
        try:
            input("\nPress Enter to close…")
        except EOFError:
            pass
        raise SystemExit(1)
