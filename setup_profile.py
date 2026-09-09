"""
setup_profile.py — first-run setup. Run this once, before anything else.

    python setup_profile.py

It asks four things: your name, your score reports, your test dates, and your
target. Everything else — which domains to work, in what order, on which days —
is worked out from your reports rather than guessed at.

You can also run it non-interactively:

    python setup_profile.py --name "Alex" --pdf scores.pdf --test 2026-10-03 --target 1500
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "Cat Sat")
sys.path.insert(0, APP)

from profile import Profile                                    # noqa: E402
from score_report import parse_score_report, describe, ScoreReportError  # noqa: E402
from plan_builder import build_plan                            # noqa: E402

RULE = "=" * 66


def _ask(prompt, default=""):
    try:
        answer = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    except EOFError:
        return default
    return answer or default


def _parse_day(text):
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def load_reports(paths, profile):
    """Parse each PDF and add it. Reports what it found and what it could not."""
    added = 0
    for path in paths:
        try:
            report = parse_score_report(path)
        except ScoreReportError as exc:
            print(f"  ✗ {os.path.basename(path)}: {exc}")
            continue
        profile.add_report(report)
        added += 1
        print(f"  ✓ {describe(report)}".replace("\n", "\n    "))
        if not report["has_domain_bands"]:
            print("      note: no domain bands in this one. A real score report from "
                  "your College Board account has them; practice reports draw them as "
                  "bars, which cannot be read from the PDF.")
    return added


def interactive(profile):
    print(RULE)
    print("  Cat Prep — setup")
    print(RULE)
    print("\nThis takes about two minutes and you only do it once.\n")

    profile.name = _ask("Your name", profile.name or "")

    print("\n--- Score reports ---")
    print("Download them from your College Board account (real sittings) or from")
    print("Bluebook (practice tests). Drop them anywhere and give the path or a")
    print("folder. Press Enter when you have none left to add.\n")
    while True:
        answer = _ask("PDF or folder (Enter to finish)")
        if not answer:
            break
        answer = os.path.expanduser(answer.strip('"').strip("'"))
        paths = (sorted(glob.glob(os.path.join(answer, "*.pdf")))
                 if os.path.isdir(answer) else [answer])
        if not paths:
            print("  nothing found there")
            continue
        load_reports(paths, profile)

    print("\n--- Test dates ---")
    print("Every SAT you are registered for or plan to sit. YYYY-MM-DD.\n")
    while True:
        answer = _ask("Test date (Enter to finish)")
        if not answer:
            break
        when = _parse_day(answer)
        if not when:
            print("  couldn't read that — try 2026-10-03")
            continue
        if when < date.today():
            print("  that is in the past; skipping")
            continue
        label = _ask("  Label", f"SAT {when.strftime('%b %d')}")
        profile.add_test_date(when, label)
        print(f"  ✓ {label} on {when.isoformat()}")

    print("\n--- Target ---")
    target = _ask("Target total out of 1600 (Enter to skip)")
    if target.isdigit():
        profile.target_total = int(target)

    return profile


def report_plan(profile):
    print("\n" + RULE)
    print("  Your plan")
    print(RULE)

    summary = profile.summary()
    best = summary["best"]
    if best:
        print("\n  Best per section (this is what a superscore is made of):")
        for name, score in sorted(best.items()):
            print(f"    {name:<24} {score}")
    if summary["superscore"]:
        print(f"\n  Current superscore: {summary['superscore']}")
        if summary["target"]:
            gap = summary["target"] - summary["superscore"]
            print(f"  Target: {summary['target']}  ({gap:+d})")

    if summary["banked"]:
        names = " and ".join(summary["banked"])
        print(f"\n  {names} is BANKED — top band across every domain, and superscore")
        print("  keeps it forever. The plan gives it zero minutes. Every minute spent")
        print("  there is a minute taken from the section that can still move.")

    print("\n  Where your remaining points actually are:")
    for entry in profile.domain_priorities():
        if entry["weight"] <= 0:
            continue
        band = f"{entry['band']['low']}-{entry['band']['high']}" if entry["band"] else "no data"
        print(f"    {entry['weight']:>6}  {entry['domain']:<32} {band:<10}"
              f" {int(entry['share'] * 100)}% of section  ({entry['kind']})")

    plan = build_plan(profile)
    if not plan["weeks"]:
        print("\n  No upcoming test dates, so no calendar was generated. Add one and")
        print("  run this again.")
        return

    print(f"\n  {len(plan['weeks'])} weeks, {len(plan['days'])} days planned:\n")
    for week in plan["weeks"]:
        focus = ", ".join(week["focus_domains"]) or "—"
        print(f"    W{week['number']}  {week['start']}  {week['kind']:<6} {focus}")

    today = plan["generated_for"]
    if today in plan["days"]:
        day = plan["days"][today]
        print(f"\n  Today ({today}) — {day['headline']}  ·  {day['hours']}")
        for task in day["tasks"]:
            print(f"    {task['minutes']:>3} min  {task['label']}")


def main():
    ap = argparse.ArgumentParser(description="Set up your Cat Prep study profile.")
    ap.add_argument("--name", default=None)
    ap.add_argument("--pdf", action="append", default=[],
                    help="a score report PDF (repeatable), or a folder of them")
    ap.add_argument("--test", action="append", default=[],
                    help="a test date, YYYY-MM-DD (repeatable)")
    ap.add_argument("--target", type=int, default=None)
    ap.add_argument("--show", action="store_true",
                    help="print the current plan without changing anything")
    args = ap.parse_args()

    profile = Profile.load() or Profile()

    if args.show:
        if not profile.test_dates and not profile.reports:
            print("No profile yet. Run: python setup_profile.py")
            return 1
        report_plan(profile)
        return 0

    non_interactive = bool(args.name or args.pdf or args.test or args.target)
    if non_interactive:
        if args.name:
            profile.name = args.name
        paths = []
        for item in args.pdf:
            item = os.path.expanduser(item)
            paths += (sorted(glob.glob(os.path.join(item, "*.pdf")))
                      if os.path.isdir(item) else [item])
        if paths:
            load_reports(paths, profile)
        for item in args.test:
            when = _parse_day(item)
            if when:
                profile.add_test_date(when)
        if args.target:
            profile.target_total = args.target
    else:
        interactive(profile)

    profile.save()
    from profile import PROFILE_PATH
    print(f"\n  Saved to {PROFILE_PATH}")
    print("  It is plain JSON — open it and edit anything that is wrong.")

    report_plan(profile)
    print("\n" + RULE)
    print("  Next:  python import_questions.py     (build your question bank)")
    print("         python run.py                  (open the app)")
    print(RULE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
