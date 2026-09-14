"""
score_report.py — read an official College Board SAT score report PDF.

This is what lets the app plan for someone other than its author. You hand it
the PDF College Board gives you after a real sitting (or a Bluebook practice
test) and it returns your total, your two section scores, and — the part that
actually matters — the performance band for each of the eight content domains.

Those eight bands are the whole point. A total score tells you nothing you can
act on. "Craft and Structure: 550-600, and it is 28% of the section" tells you
exactly where the next fifty points are.

Nothing here writes anything. It reads one PDF and returns a dict.

    from score_report import parse_score_report
    report = parse_score_report("my_scores.pdf")
    print(report["sections"]["Reading and Writing"]["score"])   # 640
    print(report["domains"]["Craft and Structure"])             # {'low': 550, 'high': 600}
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import date

RW = "Reading and Writing"
MATH = "Math"

RW_DOMAINS = [
    "Information and Ideas",
    "Craft and Structure",
    "Expression of Ideas",
    "Standard English Conventions",
]
MATH_DOMAINS = [
    "Algebra",
    "Advanced Math",
    "Problem-Solving and Data Analysis",
    "Geometry and Trigonometry",
]
ALL_DOMAINS = RW_DOMAINS + MATH_DOMAINS

# Share of each section, straight off the score report. Used to weight a weak
# band by how many questions it actually costs you.
DOMAIN_SHARE = {
    "Information and Ideas": 0.26,
    "Craft and Structure": 0.28,
    "Expression of Ideas": 0.20,
    "Standard English Conventions": 0.26,
    "Algebra": 0.35,
    "Advanced Math": 0.35,
    "Problem-Solving and Data Analysis": 0.15,
    "Geometry and Trigonometry": 0.15,
}

SECTION_OF = {d: RW for d in RW_DOMAINS} | {d: MATH for d in MATH_DOMAINS}

_MONTHS = ("January February March April May June July August September "
           "October November December").split()


class ScoreReportError(Exception):
    """The PDF could not be read as a College Board score report."""


# ---------------------------------------------------------------- extraction

def extract_text(pdf_path: str) -> str:
    """
    Pull the text out of a PDF, trying whichever backend is present.

    PyMuPDF first because the importer already depends on it, so on a machine
    that can run this app at all it is almost certainly installed. pdftotext is
    the fallback so the parser still works on a machine that only has poppler.
    """
    if not os.path.isfile(pdf_path):
        raise ScoreReportError(f"No such file: {pdf_path}")

    try:
        import fitz                                        # PyMuPDF
        with fitz.open(pdf_path) as doc:
            return "\n".join(page.get_text() for page in doc)
    except ImportError:
        pass
    except Exception as exc:                               # corrupt / encrypted
        raise ScoreReportError(f"Could not open {pdf_path}: {exc}") from exc

    if shutil.which("pdftotext"):
        try:
            out = subprocess.run(["pdftotext", pdf_path, "-"],
                                 capture_output=True, check=True, timeout=60)
            return out.stdout.decode("utf-8", "replace")
        except (subprocess.SubprocessError, OSError) as exc:
            raise ScoreReportError(f"pdftotext failed on {pdf_path}: {exc}") from exc

    raise ScoreReportError(
        "No PDF reader available. Install PyMuPDF (pip install PyMuPDF) — the "
        "question importer needs it anyway.")


# ------------------------------------------------------------------- parsing

def _parse_domains(text: str) -> dict[str, dict[str, int]]:
    """
    Pair up domain names with their performance bands.

    College Board lays the report out in two columns, so the extracted text
    runs: R&W domain, Math domain, their two percentages, then the two
    'Performance:' lines in the same order. A FIFO queue therefore pairs them
    correctly without depending on the exact line layout, which differs
    between the practice report and the real one.
    """
    token = re.compile(
        r"(" + "|".join(re.escape(d) for d in ALL_DOMAINS) + r")"
        r"|Performance:\s*(\d{3})\s*[-–—]\s*(\d{3})")

    pending: list[str] = []
    found: dict[str, dict[str, int]] = {}

    for match in token.finditer(text):
        name, low, high = match.group(1), match.group(2), match.group(3)
        if name:
            if name not in found and name not in pending:
                pending.append(name)
        elif pending:
            found[pending.pop(0)] = {"low": int(low), "high": int(high)}

    return found


def _parse_scores(text: str) -> tuple[int | None, dict[str, int]]:
    """Total out of 1600, and each section out of 800."""
    total = None
    for value in (int(m) for m in re.findall(r"\b(\d{4})\b", text)):
        if 400 <= value <= 1600 and value % 10 == 0:
            total = value
            break

    sections: dict[str, int] = {}
    # A section score is a bare 3-digit multiple of 10 in [200, 800] that is not
    # part of a range like "610-670" or a label like "200-800".
    for match in re.finditer(r"(?<![-–—\d])\b([2-8]\d0)\b(?![-–—\d])", text):
        value = int(match.group(1))
        before = text[max(0, match.start() - 400):match.start()]
        if RW in before and before.rindex(RW) > (before.rindex(MATH) if MATH in before else -1):
            sections.setdefault(RW, value)
        elif MATH in before:
            sections.setdefault(MATH, value)
        if len(sections) == 2:
            break

    if total and len(sections) == 1:                       # infer the missing one
        known = next(iter(sections))
        other = MATH if known == RW else RW
        sections[other] = total - sections[known]

    return total, sections


def _parse_date(text: str) -> date | None:
    m = re.search(r"Tested on:\s*([A-Z][a-z]{2,8})\.?\s+(\d{1,2}),?\s+(\d{4})", text)
    if not m:
        m = re.search(r"([A-Z][a-z]{2,8})\s+(\d{1,2}),\s+(\d{4})", text)
    if not m:
        return None
    month_name, day, year = m.group(1), int(m.group(2)), int(m.group(3))
    for index, full in enumerate(_MONTHS, start=1):
        if full.lower().startswith(month_name.lower()[:3]):
            try:
                return date(year, index, day)
            except ValueError:
                return None
    return None


def _parse_label(text: str) -> str:
    m = re.search(r"Test administration:\s*(.+)", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"\bSAT Practice\s+(\d+)", text)
    if m:
        return f"SAT Practice {m.group(1)}"
    return "SAT score report"


# --------------------------------------------------------------- public API

def parse_score_report(pdf_path: str) -> dict:
    """
    Read one score report PDF.

    Returns a dict with `label`, `date`, `total`, `sections` and `domains`.
    Anything the PDF does not contain comes back as None or an empty dict
    rather than a guess — a practice score report, for instance, draws its
    domain performance as bars rather than text, so `domains` will be empty
    and that is reported honestly instead of invented.
    """
    text = extract_text(pdf_path)

    if not any(d in text for d in ALL_DOMAINS) and "SAT" not in text:
        raise ScoreReportError(
            f"{os.path.basename(pdf_path)} does not look like an SAT score report. "
            "Download yours from your College Board account or from Bluebook.")

    total, sections = _parse_scores(text)
    domains = _parse_domains(text)

    return {
        "label": _parse_label(text),
        "date": _parse_date(text),
        "total": total,
        "sections": {name: {"score": score} for name, score in sections.items()},
        "domains": domains,
        "is_official": "Test administration:" in text,
        "has_domain_bands": bool(domains),
        "source_file": os.path.basename(pdf_path),
    }


def describe(report: dict) -> str:
    """A short human-readable summary, for the setup wizard and the CLI."""
    lines = [f"{report['label']}"
             + (f" — {report['date'].isoformat()}" if report.get("date") else "")]
    if report.get("total"):
        lines.append(f"  Total {report['total']}")
    for name in (RW, MATH):
        if name in report["sections"]:
            lines.append(f"  {name}: {report['sections'][name]['score']}")
    if report["domains"]:
        lines.append("  Domain bands:")
        for name in ALL_DOMAINS:
            band = report["domains"].get(name)
            if band:
                lines.append(f"    {name:<34} {band['low']}-{band['high']}")
    else:
        lines.append("  (no domain bands in this PDF — practice reports draw them "
                     "as bars, so only the scores could be read)")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python score_report.py <score-report.pdf> [more.pdf ...]")
        raise SystemExit(2)
    for path in sys.argv[1:]:
        try:
            print(describe(parse_score_report(path)))
        except ScoreReportError as exc:
            print(f"{os.path.basename(path)}: {exc}")
        print()
