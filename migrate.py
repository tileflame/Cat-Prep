"""
migrate.py, bring an existing Cat Prep setup into this copy from a terminal.

    python migrate.py "C:\\Users\\you\\Desktop\\Coding Projects\\CAT SAT"

Most people should use the app instead: open Cat Prep and click Move My Data.
This script exists for anyone who prefers a terminal, and so the migration can
be run on a machine where the app will not start.

Both front ends call the same code in `Cat Sat/migration.py`. One copy of the
rules means this script cannot quietly drift away from the screen and lose
somebody's work.

IT NEVER WRITES TO THE OLD FOLDER. Every operation on the source is a read. If
something goes wrong halfway, the old folder is still a complete, working
install you can go back to.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "Cat Sat"))

import migration                                          # noqa: E402


def say(message: str = "") -> None:
    print(message, flush=True)


def ask_for_folder() -> str:
    """
    Prompt for the old folder when none was given on the command line.

    Double-clicking this file is the most likely way it gets run. argparse's
    answer to a missing argument is a usage line and exit(2), and on Windows
    that closes the console window before anyone can read it. From the outside
    that is indistinguishable from the script doing nothing at all, which is
    exactly how it was reported.
    """
    say("  Which folder is your OLD Cat Prep in?")
    say()
    say("  Drag that folder onto this window and press Enter,")
    say("  or paste the path. It looks something like:")
    say(r"      C:\Users\you\Desktop\Coding Projects\CAT SAT")
    say()
    try:
        raw = input("  old folder> ")
    except EOFError:
        return ""
    # Dragging a folder onto a console pastes it already quoted, and people
    # type the quotes themselves out of habit because the path has spaces.
    return raw.strip().strip('"').strip("'").strip()


def show(title: str, data: dict) -> None:
    def n(key):
        value = data.get(key)
        return "-" if value is None else f"{value:,}"
    say(f"  {title}")
    say(f"    questions in the bank : {n('questions')}")
    say(f"    practice sittings     : {n('sessions')}")
    say(f"    answered questions    : {n('attempts')}")
    say(f"    question images       : {n('images')}")
    say(f"    source PDFs           : {n('pdfs')}")
    say(f"    profile               : {'yes' if data.get('profile') else 'no'}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy an existing Cat Prep setup into this one.")
    parser.add_argument("old_folder", nargs="?",
                        help="your previous Cat Prep folder")
    parser.add_argument("--skip-pdfs", action="store_true",
                        help="leave the source PDFs behind, they are only needed to re-import")
    args = parser.parse_args()

    say("=" * 64)
    say("  Cat Prep, migrating your data")
    say("=" * 64)
    say()

    old = args.old_folder or ask_for_folder()
    if not old:
        sys.exit("  No folder given, so there is nothing to copy.")

    looked = migration.scan(old)
    if not looked.get("found"):
        sys.exit("  " + looked.get("error", "Could not read that folder."))

    say(f"  from : {looked['path']}")
    say(f"  to   : {migration.DATA_DIR}")
    say()
    show("Found in the old folder:", looked["source"])
    say()

    here = looked.get("here") or {}
    if (here.get("questions") or 0) > 0:
        say(f"  This copy already has {here['questions']:,} questions in it.")
        try:
            if input("  Overwrite them? [y/N] ").strip().lower() not in ("y", "yes"):
                sys.exit("  Stopped. Nothing was changed.")
        except EOFError:
            sys.exit("  Stopped. Nothing was changed.")
        say()

    started = migration.start(old, skip_pdfs=args.skip_pdfs)
    if started.get("error"):
        sys.exit("  " + started["error"])

    last = ""
    while True:
        state = migration.status()
        if state["message"] != last:
            last = state["message"]
            say(f"  {last}")
        if state["total"]:
            say(f"    {state['copied']:,} / {state['total']:,}")
        if state["state"] in ("done", "error"):
            break
        time.sleep(1.0)

    final = migration.status()
    say()
    show("Now in this copy:", final.get("after") or {})
    say()
    say("=" * 64)
    if final["state"] == "error":
        say("  SOMETHING IS MISSING. Your old folder is untouched, go back to it.")
        for line in final.get("problems") or []:
            say(f"    - {line}")
        say(f"    {final.get('message', '')}")
        say("=" * 64)
        return 1

    say("  Everything arrived. Your old folder is untouched, keep it until")
    say("  you have opened this one and seen your history.")
    say()
    say("  Next:  open Cat Prep")
    say("=" * 64)
    return 0


if __name__ == "__main__":
    # A double-click gets no arguments, and that is also the run that most needs
    # the window to stay open afterwards, because there is no terminal behind it
    # holding the output. Every exit path goes through here, including the
    # failures: an error message you cannot read is not an error message.
    double_clicked = len(sys.argv) == 1
    try:
        code = main()
    except SystemExit as stop:
        code = stop.code if isinstance(stop.code, int) else 1
        if isinstance(stop.code, str):
            say()
            say(stop.code)
    except KeyboardInterrupt:
        say()
        say("  Stopped. Your old folder is untouched.")
        code = 1
    except Exception:                                     # noqa: BLE001
        import traceback
        traceback.print_exc()
        say()
        say("  Something went wrong. Nothing was written to your old folder,")
        say("  so it is still a complete, working install. Go back to it.")
        code = 1

    if double_clicked:
        say()
        try:
            input("  Press Enter to close this window.")
        except EOFError:
            pass
    raise SystemExit(code)
