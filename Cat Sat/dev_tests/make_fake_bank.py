"""
Generate a synthetic question bank that mimics sat_importer.py's output, so the
test suite can run without any College Board PDFs.

SAFETY: everything is written to dev_tests/_sandbox/, never to your real
database/ or images/ folders. Call sandbox_env() before importing config so the
app points at the sandbox too. build() refuses to run against any path that is
not inside a folder named _sandbox.
"""
import os
import random
import sqlite3
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
SANDBOX = os.path.join(HERE, "_sandbox")
DB_DIR = os.path.join(SANDBOX, "database")
IMG_DIR = os.path.join(SANDBOX, "images")


def sandbox_env():
    """
    Point the app at the throwaway sandbox. MUST be called before `import config`
    (or anything that imports it), because config reads this once at import time.
    """
    os.environ["CATSAT_DATA_DIR"] = SANDBOX
    return SANDBOX


def _assert_sandboxed(path):
    """Refuse to touch anything outside dev_tests/_sandbox/."""
    resolved = os.path.abspath(path)
    if "_sandbox" not in resolved.replace("\\", "/").split("/"):
        raise RuntimeError(
            f"refusing to write test data outside the sandbox: {resolved}\n"
            "This guard exists because an earlier version of this script wrote "
            "placeholder images and an empty questions.db into the real app "
            "folder, which would destroy an imported question bank."
        )


RW_SKILLS = {
    "Craft and Structure": ["Words in Context", "Text Structure and Purpose",
                            "Cross-Text Connections"],
    "Information and Ideas": ["Central Ideas and Details", "Command of Evidence",
                              "Inferences"],
    "Standard English Conventions": ["Boundaries", "Form, Structure, and Sense"],
    "Expression of Ideas": ["Rhetorical Synthesis", "Transitions"],
}
MATH_SKILLS = {
    "Algebra": ["Linear equations in one variable", "Linear functions",
                "Systems of two linear equations"],
    "Advanced Math": ["Nonlinear functions", "Equivalent expressions",
                      "Nonlinear equations"],
    "Problem-Solving and Data Analysis": ["Ratios, rates, proportional relationships",
                                          "Percentages", "One-variable data"],
    "Geometry and Trigonometry": ["Area and volume", "Lines, angles, and triangles",
                                  "Circles"],
}
DIFFS = ["Easy", "Medium", "Hard"]


def make_img(path, text, color):
    img = Image.new("RGB", (900, 320), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 899, 40], fill=color)
    draw.text((14, 14), text, fill=(0, 0, 0))
    draw.text((14, 90), "SYNTHETIC TEST PLACEHOLDER - not a real SAT question.",
              fill=(40, 40, 40))
    img.save(path)


def build(per_combo=6, with_images=True, thin_domains=()):
    _assert_sandboxed(DB_DIR)
    _assert_sandboxed(IMG_DIR)
    os.makedirs(DB_DIR, exist_ok=True)
    os.makedirs(IMG_DIR, exist_ok=True)

    db = os.path.join(DB_DIR, "questions.db")
    _assert_sandboxed(db)
    if os.path.exists(db):
        os.remove(db)

    conn = sqlite3.connect(db)
    # Exactly the schema sat_importer.py creates.
    conn.execute("""CREATE TABLE IF NOT EXISTS questions (
        question_id TEXT PRIMARY KEY, section TEXT, domain TEXT, skill TEXT,
        difficulty TEXT, question_img TEXT, correct_answer TEXT, rationale TEXT,
        is_open_ended INTEGER DEFAULT 0)""")

    rng = random.Random(7)
    n = 0
    for section, table in (("Reading and Writing", RW_SKILLS), ("Math", MATH_SKILLS)):
        for domain, skills in table.items():
            count = 2 if domain in thin_domains else per_combo
            for skill in skills:
                for diff in DIFFS:
                    for _ in range(count):
                        n += 1
                        qid = f"{section[:2].lower()}{n:05d}"
                        open_ended = 1 if (section == "Math" and rng.random() < 0.25) else 0
                        answer = (rng.choice(["12", "0.5", "3/4", "7", "2.25", "-6"])
                                  if open_ended else rng.choice(["A", "B", "C", "D"]))
                        qimg = os.path.join("images", f"{qid}.png")
                        rimg = os.path.join("images", f"{qid}_rationale.png")
                        if with_images:
                            make_img(os.path.join(SANDBOX, qimg),
                                     f"{qid} | {domain} | {diff}", (200, 220, 255))
                            if rng.random() < 0.85:
                                make_img(os.path.join(SANDBOX, rimg),
                                         f"{qid} RATIONALE", (210, 255, 210))
                            else:
                                rimg = "No explanation image generated."
                        conn.execute("INSERT INTO questions VALUES (?,?,?,?,?,?,?,?,?)",
                                     (qid, section, domain, skill, diff, qimg,
                                      answer, rimg, open_ended))
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    conn.close()
    print(f"[sandbox] built {total} synthetic questions in {SANDBOX}")
    return total


def reset_progress():
    """Drop the sandbox progress database so a suite starts clean."""
    for name in ("progress.db", "progress.db-wal", "progress.db-shm"):
        path = os.path.join(DB_DIR, name)
        _assert_sandboxed(path)
        if os.path.exists(path):
            os.remove(path)


if __name__ == "__main__":
    build(int(sys.argv[1]) if len(sys.argv) > 1 else 6,
          with_images="--noimg" not in sys.argv)
