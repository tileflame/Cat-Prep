"""
Launcher — build the question bank from the PDFs in Cat Sat/pdfs/.

Runs sat_importer.py from inside the "Cat Sat" folder, which is where it expects
to write database/questions.db and images/. Equivalent to:

    cd "Cat Sat"
    python sat_importer.py

sat_importer.py itself is not modified in any way.
"""
import os
import runpy
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "Cat Sat")
TARGET = os.path.join(APP, "sat_importer.py")
PDFS = os.path.join(APP, "pdfs")

if not os.path.isfile(TARGET):
    sys.exit(f"Could not find {TARGET}\nIs import_questions.py next to the 'Cat Sat' folder?")

found = [f for f in os.listdir(PDFS) if f.lower().endswith(".pdf")] if os.path.isdir(PDFS) else []
if not found:
    sys.exit(f"No PDFs found in {PDFS}\nPut the College Board exports (downloaded WITH "
             "answers and rationales) there first.")

print(f"Found {len(found)} PDF(s) in Cat Sat/pdfs/:")
for name in found:
    print(f"   - {name}")
print("\nThis takes a while for a few thousand questions. Leave it running.\n")

os.chdir(APP)
sys.path.insert(0, APP)
runpy.run_path(TARGET, run_name="__main__")
