"""
skill_tags.py, recover each question's SKILL from the College Board PDFs.

WHY THIS EXISTS
---------------
Every question in the bank carries a domain and a difficulty, and until now an
empty skill. The College Board export prints its header as a table:

    Assessment   Test                 Domain                 Skill              Difficulty
    SAT          Reading and Writing  Craft and Structure    Words in Context   Easy

and as text that comes out one row after the other:

    Assessment Test Domain Skill Difficulty SAT Reading and Writing Craft and ...

The importer looks for whatever follows "Skill" up to "Difficulty", which on
this layout is nothing at all, so all 3,713 questions in a real bank were saved
with a blank skill. Nothing complained, because nothing needed the skill: the
blueprint only counted domains. The moment tests are built by skill ("four
vocabulary questions, then one Cross-Text Connections...") a blank skill means
there is no vocabulary question in the bank as far as the engine can tell.

sat_importer.py is deliberately left alone. This reads the same PDFs a second
time, pulls the skill out properly, and fills in ONLY the blanks. A skill that
is already recorded is never overwritten.

WHY IT MATCHES WORDS INSTEAD OF READING POSITIONS
-------------------------------------------------
When a table cell wraps, its second line is printed after the whole first row:

    Craft and Structure  Text Structure and  Hard  Purpose
    Standard English  Form, Structure, and  Easy  Conventions  Sense

so "the text between the domain and the difficulty" is "Text Structure and",
and the domain is "Standard English". Parsing positions gets both wrong, and
which cells wrap depends on the font and the column widths College Board used
that year. So instead: take every word in the header, remove the labels, the
test name, the domain (already known from the database) and the difficulty,
and find the longest official skill name whose words are all in what is left.
Word order stops mattering, which is exactly the property wrapping destroys.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from config import DATA_DIR, QUESTION_DB

#: The official skill names, per domain, as the SAT Suite Question Bank prints
#: them. Used to recognise a skill however its words were wrapped.
OFFICIAL_SKILLS = {
    "Information and Ideas": [
        "Central Ideas and Details", "Command of Evidence", "Inferences"],
    "Craft and Structure": [
        "Words in Context", "Text Structure and Purpose", "Cross-Text Connections"],
    "Expression of Ideas": ["Rhetorical Synthesis", "Transitions"],
    "Standard English Conventions": ["Boundaries", "Form, Structure, and Sense"],
    "Algebra": [
        "Linear equations in one variable", "Linear functions",
        "Linear equations in two variables",
        "Systems of two linear equations in two variables",
        "Linear inequalities in one or two variables"],
    "Advanced Math": [
        "Nonlinear functions",
        "Nonlinear equations in one variable and systems of equations in two variables",
        "Equivalent expressions"],
    "Problem-Solving and Data Analysis": [
        "Ratios, rates, proportional relationships, and units", "Percentages",
        "One-variable data: Distributions and measures of center and spread",
        "Two-variable data: Models and scatterplots",
        "Probability and conditional probability",
        "Inference from sample statistics and margin of error",
        "Evaluating statistical claims: Observational studies and experiments"],
    "Geometry and Trigonometry": [
        "Area and volume", "Lines, angles, and triangles",
        "Right triangles and trigonometry", "Circles"],
}

_LABELS = ["question", "id", "assessment", "test", "domain", "skill", "difficulty", "sat"]
_DIFFICULTIES = ("easy", "medium", "hard")
_HYPHENS = re.compile("[‐‑‒–−]")
_WORD = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)*")
_HEADER = re.compile(
    r"Question\s+ID:?\s*([0-9a-fA-F]{6,12})\b(.{0,420}?)\sQuestion\s(?!ID)", re.S)


def _words(text: str) -> list[str]:
    return _WORD.findall(_HYPHENS.sub("-", text or "").lower())


def is_blank(skill) -> bool:
    return not str(skill or "").strip() or str(skill).strip() in ("General", "Unclassified")


def skill_from_header(header: str, domain: str, section: str = "") -> str | None:
    """
    The skill named in one question's header, or None if it cannot be told.

    `header` is the text between "Question ID: xxxx" and the word "Question"
    that starts the question body. `domain` and `section` come from the
    database, which already has them right.
    """
    original = header.split()
    bag = Counter(_words(header))

    def remove(words):
        for word in words:
            if bag[word] > 0:
                bag[word] -= 1

    remove(_LABELS)
    remove(_words(section))
    remove(_words(domain))
    for difficulty in _DIFFICULTIES:
        bag[difficulty] = 0          # no skill name contains these

    best = None
    for skill in OFFICIAL_SKILLS.get(domain, []):
        need = Counter(_words(skill))
        if all(bag[w] >= n for w, n in need.items()):
            if best is None or sum(need.values()) > len(_words(best)):
                best = skill
    if best:
        return best

    # Not an official name (a new skill, or a domain this table does not know):
    # keep whatever words are left, in the order they appeared, so the
    # question still gets a label rather than a blank.
    leftover = []
    for token in original:
        words = _words(token)
        if words and all(bag[w] > 0 for w in words):
            for w in words:
                bag[w] -= 1
            leftover.append(token.strip(",:;"))
    text = " ".join(leftover).strip()
    return text or None


SECTION_DOMAINS = {
    "Reading and Writing": ["Information and Ideas", "Craft and Structure",
                            "Expression of Ideas", "Standard English Conventions"],
    "Math": ["Algebra", "Advanced Math", "Problem-Solving and Data Analysis",
             "Geometry and Trigonometry"],
}


def classify(header: str, domain: str, section: str = "") -> tuple[str, str | None]:
    """
    (domain, skill) for one question's header.

    Normally the domain comes back unchanged. The exception is a question the
    importer filed under the wrong domain: it decides the domain by searching
    the WHOLE page for "Algebra" first, so a data question that mentions algebra
    anywhere in its passage lands in Algebra. When the header names another
    domain of the same section AND a complete official skill of that domain,
    the header wins, because the header is what College Board actually printed.
    """
    skill = skill_from_header(header, domain, section)
    if skill in OFFICIAL_SKILLS.get(domain, []):
        return domain, skill
    words = Counter(_words(header))
    for other in SECTION_DOMAINS.get(section, []):
        if other == domain:
            continue
        need = Counter(_words(other))
        if not all(words[w] >= n for w, n in need.items()):
            continue
        corrected = skill_from_header(header, other, section)
        if corrected in OFFICIAL_SKILLS.get(other, []):
            return other, corrected
    return domain, skill


def headers_in_text(text: str) -> list[tuple[str, str]]:
    """Every (question id, header text) on one page, or in one document's text."""
    flat = re.sub(r"\s+", " ", text or "")
    return [(m.group(1).lower(), m.group(2)) for m in _HEADER.finditer(flat)]


# ---------------------------------------------------------------------------
# READING THE PDFS AND WRITING THE BLANKS
# ---------------------------------------------------------------------------

def question_pdfs(folder: Path | None = None) -> list[Path]:
    """The question-bank exports. Score reports live in a subfolder and are skipped."""
    folder = folder or (DATA_DIR / "pdfs")
    return sorted(folder.glob("*.pdf")) if folder.is_dir() else []


def _bank_rows() -> dict[str, tuple[str, str, str]]:
    import sqlite3
    conn = sqlite3.connect(QUESTION_DB)
    try:
        return {str(qid).lower(): (qid, section or "", domain or "", skill or "")
                for qid, section, domain, skill in conn.execute(
                    "SELECT question_id, section, domain, skill FROM questions")}
    finally:
        conn.close()


def blank_count() -> tuple[int, int]:
    """(questions with no skill recorded, questions in the bank)."""
    rows = _bank_rows()
    return sum(1 for r in rows.values() if is_blank(r[3])), len(rows)


def recover(pdfs: list[Path] | None = None, progress=None) -> dict:
    """
    Read the PDFs and fill in every blank skill that can be recovered.

    Returns counts for the screen: how many were filled, how many were already
    known, and how many blanks are left (a question whose PDF is gone).
    """
    import sqlite3
    rows = _bank_rows()
    blanks = {key for key, r in rows.items() if is_blank(r[3])}
    result = {"pdfs": 0, "filled": 0, "alreadyKnown": len(rows) - len(blanks),
              "stillBlank": len(blanks), "total": len(rows)}
    if not blanks:
        return result

    try:
        import fitz                                     # PyMuPDF, bundled with the app
    except ImportError:
        result["error"] = "PyMuPDF is not available, so the PDFs cannot be read."
        return result

    found: dict[str, str] = {}
    moved: dict[str, str] = {}                          # question key -> corrected domain
    files = pdfs if pdfs is not None else question_pdfs()
    for index, path in enumerate(files):
        try:
            doc = fitz.open(str(path))
        except Exception:                               # noqa: BLE001
            continue
        result["pdfs"] += 1
        try:
            for page_no in range(doc.page_count):
                text = doc[page_no].get_text()
                if "Question ID" not in text:
                    continue
                for key, header in headers_in_text(text):
                    if key in blanks and key not in found:
                        _, section, domain, _ = rows[key]
                        new_domain, skill = classify(header, domain, section)
                        if skill:
                            found[key] = skill
                            if new_domain != domain:
                                moved[key] = new_domain
                if progress and page_no % 200 == 0:
                    progress(index, len(files), page_no, doc.page_count)
        finally:
            doc.close()

    if found:
        conn = sqlite3.connect(QUESTION_DB)
        try:
            conn.executemany(
                "UPDATE questions SET skill = ? WHERE question_id = ? "
                "AND (skill IS NULL OR TRIM(skill) = '' OR skill IN ('General', 'Unclassified'))",
                [(skill, rows[key][0]) for key, skill in found.items()])
            if moved:
                conn.executemany("UPDATE questions SET domain = ? WHERE question_id = ?",
                                 [(domain, rows[key][0]) for key, domain in moved.items()])
            conn.commit()
        finally:
            conn.close()
        try:
            import database
            database.close_all_pools()                  # readers see the new skills
        except Exception:                               # noqa: BLE001
            pass

    result["filled"] = len(found)
    result["domainsCorrected"] = len(moved)
    result["stillBlank"] = len(blanks) - len(found)
    return result
