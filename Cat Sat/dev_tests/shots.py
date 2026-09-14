"""Capture every screen so I can actually look at them."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, APP); os.chdir(APP)

import make_fake_bank
make_fake_bank.sandbox_env()
if not os.path.exists(os.path.join(make_fake_bank.SANDBOX, "database", "questions.db")):
    make_fake_bank.build(14)
import database; database.init_db()

# ---------------------------------------------------------------------------
# Seed a SYNTHETIC profile before capturing anything.
#
# `sandbox_env()` sandboxes the question bank and nothing else. It does not
# touch the study plan, so on a machine that has a hand-written personal_plan.py
# the plan and calendar shots came out showing that person's real exam dates and
# real target score — and those two images were then committed and embedded in
# the README, directly under a line promising the screenshots contained no real
# data. Seeding a profile here is what makes that promise true.
# ---------------------------------------------------------------------------
import atexit
from datetime import date, timedelta
from user_profile import Profile, PROFILE_PATH
import score_report as sr

TOP = {"low": 680, "high": 800}
MID = {"low": 610, "high": 670}
LOW = {"low": 550, "high": 600}
_demo = Profile(name="Example Student", target_total=1400)
_demo.add_report({
    "label": "Practice test 4", "date": (date.today() - timedelta(days=21)).isoformat(),
    "total": 1180, "sections": {sr.RW: {"score": 560}, sr.MATH: {"score": 620}},
    "domains": {"Information and Ideas": MID, "Craft and Structure": LOW,
                "Expression of Ideas": MID, "Standard English Conventions": LOW,
                "Algebra": MID, "Advanced Math": LOW,
                "Problem-Solving and Data Analysis": TOP,
                "Geometry and Trigonometry": MID},
    "is_official": True, "has_domain_bands": True, "source_file": "demo"})
# Offsets chosen so the rendered dates cannot coincide with any real
# sitting the author has taken or is registered for.
for _days, _label in ((31, "SAT #1"), (73, "SAT #2")):
    _demo.add_test_date(date.today() + timedelta(days=_days), _label)
_demo.save()

@atexit.register
def _drop_demo_profile(_p=PROFILE_PATH):
    """Must not outlive the run — see the note in dev_tests/test_web.py."""
    try:
        os.remove(_p)
    except OSError:
        pass
import server as server_module
from playwright.sync_api import sync_playwright

OUT = os.path.join(os.path.dirname(APP), "Screenshots"); os.makedirs(OUT, exist_ok=True)
DEMO = {"01-plan": "demo-01-plan", "02-calendar": "demo-02-calendar",
        "08-quiz": "demo-03-quiz", "12-review": "demo-04-review",
        "07-log": "demo-05-error-log", "05-dashboard": "demo-06-dashboard",
        "04-drill-setup": "demo-07-drill"}
srv, URL = server_module.create_server()
server_module.serve_forever_in_thread(srv)

def shot(page, name, full=False):
    page.wait_for_timeout(350)
    page.screenshot(path=os.path.join(OUT, DEMO.get(name, name) + ".png"), full_page=full)
    print("  shot", DEMO.get(name, name))

try:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 900}, device_scale_factor=1)
        page.goto(URL, wait_until="networkidle")
        page.wait_for_selector("#screen h1")
        shot(page, "01-plan", full=True)
        page.click('#nav button:has-text("Calendar")'); page.wait_for_selector(".calday")
        shot(page, "02-calendar", full=True)
        page.click('#nav button:has-text("Test")'); page.wait_for_selector('button:has-text("Start test")')
        shot(page, "03-test-setup", full=True)
        page.click('#nav button:has-text("Drill")'); page.wait_for_selector('button:has-text("Start drill")')
        shot(page, "04-drill-setup", full=True)
        page.click('#nav button:has-text("Dashboard")'); page.wait_for_timeout(400)
        shot(page, "05-dashboard", full=True)
        page.click('#nav button:has-text("History")'); page.wait_for_timeout(400)
        shot(page, "06-history", full=True)
        page.click('#nav button:has-text("Error Log")'); page.wait_for_timeout(600)
        shot(page, "07-log", full=True)
        page.click('#nav button:has-text("Test")'); page.wait_for_selector('button:has-text("Start test")')
        page.click('button:has-text("Start test")'); page.wait_for_selector(".quiz .choice", timeout=15000)
        shot(page, "08-quiz")
        page.click(".choice >> nth=1"); page.wait_for_timeout(80)
        shot(page, "09-quiz-selected")
        page.click('button:has-text("Question index")'); page.wait_for_selector(".modal")
        shot(page, "10-index-modal")
        page.click('.modal .btn:has-text("Close")')
        page.on("dialog", lambda d: d.accept())
        for _ in range(30):
            if page.locator(".choice").count(): page.click(".choice >> nth=0")
            if "Review ►" in page.inner_text(".quiznav"): break
            page.click('button:has-text("Next ►")')
        page.click('.quizbar button:has-text("Submit")'); page.wait_for_selector("#screen h1", timeout=15000)
        shot(page, "11-break", full=True)
        page.click('button:has-text("Continue")'); page.wait_for_selector(".quiz .choice", timeout=15000)
        for _ in range(30):
            if page.locator(".choice").count(): page.click(".choice >> nth=0")
            if "Review ►" in page.inner_text(".quiznav"): break
            page.click('button:has-text("Next ►")')
        page.click('.quizbar button:has-text("Submit")'); page.wait_for_selector("#screen h1", timeout=20000)
        shot(page, "12-review", full=True)
        b.close()
finally:
    srv.shutdown(); srv.server_close()
print("done")
