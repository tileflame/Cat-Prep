"""
One-time cleanup for junk left by the FIRST version of the test suite.

That version wrote its synthetic question bank into the real database/ and
images/ folders instead of a sandbox. This removes what it left behind and
touches nothing else.

Run from inside the "Cat Sat" folder:

    python dev_tests/cleanup_test_artifacts.py

It is deliberately paranoid:
  * only deletes PNGs whose filename matches the synthetic pattern
    (re00001.png / ma00042_rationale.png) AND whose dimensions are exactly
    900x320, the fixed size the generator used. Real College Board crops are
    rendered at 200 DPI and are never exactly that size.
  * only deletes questions.db if it contains ZERO questions. If you have since
    run sat_importer.py successfully, your bank is left completely alone.
  * never touches progress.db, your PDFs, or any .py file.
"""
import os
import re
import sqlite3
import sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES = os.path.join(APP, "images")
QUESTION_DB = os.path.join(APP, "database", "questions.db")

SYNTHETIC_NAME = re.compile(r"^(re|ma)\d{5}(_rationale)?\.png$")
SYNTHETIC_SIZE = (900, 320)


def synthetic_images():
    """Every file in images/ that is unmistakably a generated placeholder."""
    if not os.path.isdir(IMAGES):
        return [], 0
    try:
        from PIL import Image
    except ImportError:
        print("Pillow is not installed, so image sizes cannot be verified.")
        print("Install it (pip install pillow) and re-run.")
        sys.exit(1)

    matches, skipped = [], 0
    for name in os.listdir(IMAGES):
        path = os.path.join(IMAGES, name)
        if not os.path.isfile(path) or not name.lower().endswith(".png"):
            continue
        if not SYNTHETIC_NAME.match(name):
            skipped += 1
            continue
        try:
            with Image.open(path) as img:
                if img.size == SYNTHETIC_SIZE:
                    matches.append(path)
                else:
                    skipped += 1
        except Exception:
            skipped += 1
    return matches, skipped


def question_count():
    """Rows in the bank, or None if there is no readable database."""
    if not os.path.exists(QUESTION_DB):
        return None
    try:
        conn = sqlite3.connect(QUESTION_DB)
        try:
            return conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return None


def main():
    print(f"Project folder: {APP}\n")

    junk, kept = synthetic_images()
    print(f"images/  {len(junk)} synthetic placeholder(s) found, {kept} real file(s) left alone")

    removed_bytes = 0
    for path in junk:
        try:
            removed_bytes += os.path.getsize(path)
            os.remove(path)
        except OSError as exc:
            print(f"  could not delete {os.path.basename(path)}: {exc}")
    if junk:
        print(f"         deleted {len(junk)} file(s), freed {removed_bytes / 1e6:.1f} MB")

    # Drop the images folder only if the cleanup emptied it completely.
    if os.path.isdir(IMAGES) and not os.listdir(IMAGES):
        os.rmdir(IMAGES)
        print("         removed the now-empty images folder")

    count = question_count()
    if count is None:
        print("\ndatabase/questions.db: not present, nothing to clean")
    elif count == 0:
        os.remove(QUESTION_DB)
        print("\ndatabase/questions.db: contained 0 questions (a test leftover), deleted")
    else:
        print(f"\ndatabase/questions.db: contains {count:,} questions, LEFT UNTOUCHED")

    print("\nDone. Next:")
    print('   cd "Cat Sat"')
    print("   python sat_importer.py     # builds the real bank from pdfs/")
    print("   python main.py")


if __name__ == "__main__":
    main()
