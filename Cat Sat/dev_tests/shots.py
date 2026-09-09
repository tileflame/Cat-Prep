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
import server as server_module
from playwright.sync_api import sync_playwright

OUT = os.path.join(HERE, "shots"); os.makedirs(OUT, exist_ok=True)
srv, URL = server_module.create_server()
server_module.serve_forever_in_thread(srv)

def shot(page, name, full=False):
    page.wait_for_timeout(350)
    page.screenshot(path=os.path.join(OUT, name + ".png"), full_page=full)
    print("  shot", name)

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
