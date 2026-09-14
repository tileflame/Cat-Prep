"""
Launcher — start Cat SAT.

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
