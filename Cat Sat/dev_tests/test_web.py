"""
End-to-end tests for the web UI, driven by REAL Chromium via Playwright.

Unlike the CustomTkinter shim tests, this renders actual pixels and measures
actual paint times — so the performance numbers here are the ones that matter.

    python3 dev_tests/test_web.py
"""
import os, sys, threading, time, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, APP)
os.chdir(APP)

import make_fake_bank
make_fake_bank.sandbox_env()
if not os.path.exists(os.path.join(make_fake_bank.SANDBOX, "database", "questions.db")):
    make_fake_bank.build(14)
make_fake_bank.reset_progress()

import database
database.init_db()

# Give the sandbox a profile, so the suite exercises the plan a real user sees —
# one generated from their own score report — rather than depending on the
# author's personal_plan.py, which is not part of the published app.
import study_plan as _sp
if not _sp.has_profile():
    from datetime import date as _date, timedelta as _td
    from user_profile import Profile as _Profile
    import score_report as _sr
    _TOP = {"low": 680, "high": 800}
    _LOW = {"low": 550, "high": 600}
    _MID = {"low": 610, "high": 670}
    _p = _Profile(name="Test Student", target_total=1500)
    _p.add_report({
        "label": "SAT test fixture", "date": (_date.today() - _td(days=14)).isoformat(),
        "total": 1300, "sections": {_sr.RW: {"score": 620}, _sr.MATH: {"score": 680}},
        "domains": {"Information and Ideas": _MID, "Craft and Structure": _LOW,
                    "Expression of Ideas": _LOW, "Standard English Conventions": _MID,
                    "Algebra": _MID, "Advanced Math": _LOW,
                    "Problem-Solving and Data Analysis": _TOP,
                    "Geometry and Trigonometry": _MID},
        "is_official": True, "has_domain_bands": True, "source_file": "fixture"})
    for _n, _label in ((10, "SAT #1"), (38, "SAT #2"), (66, "SAT #3"), (94, "SAT #4")):
        _p.add_test_date(_date.today() + _td(days=_n), _label)
    _p.save()

import server as server_module
from playwright.sync_api import sync_playwright

PASS, FAIL, TIMINGS = [], [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(("  ok  " if cond else "  FAIL ") + name + (f"   <- {detail}" if detail and not cond else ""))


def timed(page, label, fn, budget_ms=None):
    """Run an action and record how long until the DOM settled."""
    start = time.perf_counter()
    fn()
    page.wait_for_timeout(10)
    ms = (time.perf_counter() - start) * 1000
    TIMINGS.append((label, ms))
    if budget_ms is not None:
        check(f"{label} under {budget_ms} ms", ms < budget_ms, f"{ms:.0f} ms")
    else:
        print(f"  ⏱  {label:<44}{ms:>8.0f} ms")
    return ms


srv, URL = server_module.create_server()
server_module.serve_forever_in_thread(srv)
print(f"server up at {URL}")

try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        # ============================================================ boot
        print("\n[1] boot + navigation")
        t0 = time.perf_counter()
        page.goto(URL, wait_until="networkidle")
        boot_ms = (time.perf_counter() - t0) * 1000
        TIMINGS.append(("cold page load", boot_ms))
        check("app loads", page.locator(".brand").count() == 1)
        check(f"cold load under 1500 ms", boot_ms < 1500, f"{boot_ms:.0f} ms")
        page.wait_for_selector("#screen h1", timeout=5000)
        check("lands on Today's Plan", "Today's Plan" in page.inner_text("#screen h1"),
              page.inner_text("#screen h1"))

        for label, heading in (("test", "Adaptive Practice Test"), ("drill", "Targeted Drill"),
                               ("history", "Practice History"), ("dashboard", ""),
                               ("log", "Error Log"), ("plan", "Today's Plan")):
            timed(page, f"navigate to {label}", lambda l=label: (
                page.click(f'#nav button:has-text("{l[0].upper() + l[1:]}")'),
                page.wait_for_timeout(120)), budget_ms=900)

        # ============================================================ plan
        print("\n[2] Today's Plan")
        page.click('#nav button:has-text("Plan")')
        page.wait_for_selector("#screen .tile")
        text = page.inner_text("#screen")
        check("countdown to the next SAT shown", "until sat" in text.lower(), text[:200])
        check("week focus shown", "Week" in text)
        # Task COUNT varies with where today falls in the generated window — a
# taper day or a test day legitimately has one or two. Asserting >= 4
# made this suite fail on certain calendar days for no real reason.
        check("task list rendered", page.locator("#screen .item").count() >= 1,
              str(page.locator("#screen .item").count()))
        check("Tier 4 STOP list present", "TIER 4" in text or "Tier 4" in text.lower() or "STOP" in text)

        before = page.locator("#screen .tick.on").count()
        timed(page, "tick a task checkbox",
              lambda: (page.click("#screen .tick >> nth=0"), page.wait_for_timeout(60)),
              budget_ms=250)
        check("tick toggled without a full re-render",
              page.locator("#screen .tick.on").count() != before,
              f"{before} -> {page.locator('#screen .tick.on').count()}")

        # ==================================================== date + calendar
        print("\n[2b] day navigation + calendar")
        page.click('#screen button:has-text("◄")')
        page.wait_for_timeout(200)
        head = page.inner_text("#screen h1")
        check("stepping back a day says Yesterday, not Today",
              "Yesterday's Plan" in head, head)
        check("a 'not today' pill warns you're off today",
              page.locator('#screen .pill.orange:has-text("not today")').count() >= 1)
        page.click('#screen button:has-text("◄")')
        page.wait_for_timeout(200)
        head = page.inner_text("#screen h1")
        check("two days back reads '2 days ago'", "2 days ago" in head, head)
        page.click('#screen button:has-text("Today")')
        page.wait_for_timeout(200)
        check("Today button comes back to today",
              "Today's Plan" in page.inner_text("#screen h1"),
              page.inner_text("#screen h1"))

        timed(page, "open the calendar",
              lambda: (page.click('#screen button:has-text("Calendar")'),
                       page.wait_for_selector("#screen .calgrid .calday", timeout=6000)),
              budget_ms=1200)
        check("calendar draws a full 6-week grid",
              page.locator("#screen .calgrid .calday").count() == 42,
              str(page.locator("#screen .calgrid .calday").count()))
        check("weekday header row", page.locator("#screen .calhead div").count() == 7)
        caltext = page.inner_text("#screen")
        check("month and year labelled", "2026" in caltext, caltext[:120])
        check("all four SAT dates listed",
              all(f"SAT #{n}" in caltext for n in (1, 2, 3, 4)))
        check("all seven plan weeks are listed",
              all(f"W{n} ·" in caltext for n in range(7)),
              [n for n in range(7) if f"W{n} ·" not in caltext])
        check("today is marked on the grid",
              page.locator("#screen .calday.today").count() == 1,
              str(page.locator("#screen .calday.today").count()))
        check("SAT days are marked on the grid",
              page.locator("#screen .calday.test").count() >= 1,
              str(page.locator("#screen .calday.test").count()))

        label_before = page.inner_text("#screen .head strong")
        timed(page, "page the calendar forward a month",
              lambda: (page.click('#screen .head button:has-text("►")'),
                       page.wait_for_timeout(200)), budget_ms=900)
        check("paging forward changes the month",
              page.inner_text("#screen .head strong") != label_before,
              f"{label_before} -> {page.inner_text('#screen .head strong')}")
        page.click('#screen .head button:has-text("◄")')
        page.wait_for_timeout(200)
        check("paging back returns to the same month",
              page.inner_text("#screen .head strong") == label_before)

        # A day cell should open that exact day, not today.
        cell = page.locator("#screen .calday:not(.out)").nth(3)
        cell_day = cell.locator(".calnum").inner_text().strip()
        check("calendar cells show the day of the month", cell_day.isdigit(), cell_day)
        timed(page, "click a day to open its plan",
              lambda: (cell.click(), page.wait_for_selector("#screen h1", timeout=6000),
                       page.wait_for_timeout(150)), budget_ms=1200)
        check("clicking a calendar day opens that day's plan",
              cell_day in page.inner_text("#screen .sub"),
              f"day {cell_day} not in {page.inner_text('#screen .sub')[:80]}")

        # ============================================================ quiz
        print("\n[3] the quiz — the screen that was laggy")
        page.click('#nav button:has-text("Test")')
        page.wait_for_selector('button:has-text("Start test")')
        timed(page, "start a 27-question module",
              lambda: (page.click('button:has-text("Start test")'),
                       page.wait_for_selector(".quiz .choice", timeout=15000)),
              budget_ms=4000)
        check("quiz rendered", page.locator(".quiz").count() == 1)
        check("4 choices", page.locator(".choice").count() == 4)
        check("question image loaded",
              page.evaluate("() => { const i = document.querySelector('.qwrap img'); return !!i && i.complete && i.naturalWidth > 0; }"))

        # Measured inside the page: click -> next question painted. Timing this
        # from Python would mostly measure Playwright's polling interval.
        nav_times = page.evaluate("""async () => {
            const times = [];
            const next = [...document.querySelectorAll('.quiznav button')]
                .find(b => b.textContent.includes('Next'));
            const painted = () => new Promise(r =>
                requestAnimationFrame(() => requestAnimationFrame(r)));
            for (let i = 0; i < 20; i++) {
                const t0 = performance.now();
                next.click();
                await painted();
                times.push(performance.now() - t0);
            }
            return times;
        }""")
        median_nav = sorted(nav_times)[len(nav_times) // 2]
        worst_nav = max(nav_times)
        TIMINGS.append(("next question (median of 20)", median_nav))
        TIMINGS.append(("next question (worst of 20)", worst_nav))
        check("median navigation under 40 ms", median_nav < 40, f"{median_nav:.1f} ms")
        check("worst navigation under 120 ms", worst_nav < 120, f"{worst_nav:.1f} ms")

        # Again measured in-page: a Playwright click costs ~60-90 ms of protocol
        # round-trip on its own, which would swamp the thing being measured.
        interaction = page.evaluate("""async () => {
            const painted = () => new Promise(r =>
                requestAnimationFrame(() => requestAnimationFrame(r)));
            const out = {};
            const choice = document.querySelectorAll('.choice')[1];
            let t0 = performance.now(); choice.click(); await painted();
            out.answer = performance.now() - t0;
            const flag = [...document.querySelectorAll('.quizbar button')]
                .find(b => b.textContent.includes('Flag'));
            t0 = performance.now(); flag.click(); await painted();
            out.flag = performance.now() - t0;
            const shaky = [...document.querySelectorAll('.quizbar button')]
                .find(b => b.textContent.includes('Not sure'));
            t0 = performance.now(); shaky.click(); await painted();
            out.shaky = performance.now() - t0;
            return out;
        }""")
        TIMINGS.append(("select an answer (in-page)", interaction["answer"]))
        TIMINGS.append(("toggle flag (in-page)", interaction["flag"]))
        TIMINGS.append(("toggle not-sure (in-page)", interaction["shaky"]))
        check("selecting an answer under 40 ms", interaction["answer"] < 40,
              f"{interaction['answer']:.1f} ms")
        check("toggling flag under 40 ms", interaction["flag"] < 40, f"{interaction['flag']:.1f} ms")
        check("answer highlighted", page.locator(".choice.sel").count() == 1)
        check("flag registered", "Flagged" in page.inner_text(".quizbar"))
        page.click('button:has-text("Cross out")')
        page.click(".choice >> nth=3")
        check("cross-out mode struck a choice", page.locator(".choice.out").count() >= 1)
        page.click('button:has-text("Crossing")')

        timed(page, "open the 27-question index",
              lambda: (page.click('button:has-text("Question index")'),
                       page.wait_for_selector(".modal")), budget_ms=400)
        check("index lists every question", page.locator(".modal .body .btn").count() == 27,
              str(page.locator(".modal .body .btn").count()))
        page.click('.modal .body .btn >> nth=5')
        check("jumped from the index", "Question 6" in page.inner_text(".quizbar"),
              page.inner_text(".quizbar")[:80])

        page.keyboard.press("c")
        check("keyboard answers", page.locator(".choice.sel").count() == 1)
        current = int(page.inner_text(".quizbar").split("Question ")[1].split(" ")[0])
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(60)
        check("keyboard navigates",
              f"Question {current + 1}" in page.inner_text(".quizbar"),
              page.inner_text(".quizbar")[:90])

        # answer the rest and submit
        for _ in range(27):
            if page.locator(".choice").count():
                page.click(".choice >> nth=0")
            if "Review ►" in page.inner_text(".quiznav"):
                break
            page.click('button:has-text("Next ►")')
        page.on("dialog", lambda d: d.accept())
        timed(page, "submit module 1 + route",
              lambda: (page.click('.quizbar button:has-text("Submit")'),
                       page.wait_for_selector("#screen h1", timeout=15000)), budget_ms=5000)
        check("break screen shown", "complete" in page.inner_text("#screen h1").lower(),
              page.inner_text("#screen h1"))
        check("routing explained", "Module 2" in page.inner_text("#screen"))

        timed(page, "continue to module 2",
              lambda: (page.click('button:has-text("Continue")'),
                       page.wait_for_selector(".quiz .choice", timeout=15000)), budget_ms=4000)
        for _ in range(30):
            if page.locator(".choice").count():
                page.click(".choice >> nth=0")
            if "Review ►" in page.inner_text(".quiznav"):
                break
            page.click('button:has-text("Next ►")')
        timed(page, "submit module 2 + build review",
              lambda: (page.click('.quizbar button:has-text("Submit")'),
                       page.wait_for_selector("#screen h1", timeout=20000)), budget_ms=6000)

        # ========================================================== review
        print("\n[4] review screen (54 questions)")
        text = page.inner_text("#screen")
        check("review rendered", "Session complete" in text or "Strong session" in text, text[:120])
        check("estimated score shown", "Estimated score" in text)
        check("adaptive routing shown", "Adaptive routing" in text)
        check("domain breakdown", "By domain" in text)
        check("skill breakdown", "By skill" in text)
        check("pacing", "Pacing" in text)
        for name in ("Incorrect", "Flagged", "Skipped", "Slow", "All"):
            timed(page, f"filter: {name}",
                  lambda n=name: (page.click(f'#screen button.btn.sm:has-text("{n}")'),
                                  page.wait_for_timeout(40)), budget_ms=350)
        check("question rows rendered", page.locator("#screen .item").count() > 5,
              str(page.locator("#screen .item").count()))
        timed(page, "open an explanation",
              lambda: (page.click('button:has-text("Explanation") >> nth=0'),
                       page.wait_for_selector(".modal img", timeout=5000)), budget_ms=1200)
        check("explanation shows an image", page.locator(".modal img").count() >= 1)
        page.click('.modal .btn:has-text("Close")')

        # ========================================================= log
        print("\n[5] error log")
        timed(page, "open the error log",
              lambda: (page.click('#nav button:has-text("Error Log")'),
                       page.wait_for_selector("#screen .chiplist", timeout=8000)), budget_ms=2500)
        text = page.inner_text("#screen")
        check("diagnosis panel", "This week's diagnosis" in text)
        check("asks WHY", "WHY DID YOU MISS IT" in text)
        check("asks for one sentence", "ONE SENTENCE" in text)
        check("shows your answer", "Your answer:" in text)
        check("shows the correct answer", "Correct answer:" in text)
        check("offers to show the question", "Show question & rationale" in text)
        check("all 11 cause buttons", page.locator("#screen .chiplist button").count() >= 11,
              str(page.locator("#screen .chiplist button").count()))

        timed(page, "expand a question in the log",
              lambda: (page.click("#screen details summary >> nth=0"),
                       page.wait_for_timeout(300)), budget_ms=900)
        check("expanded shows the question image",
              page.locator("#screen details img").count() >= 1,
              str(page.locator("#screen details img").count()))

        page.click("#screen .chiplist button >> nth=2")
        page.wait_for_timeout(200)
        check("tagging a cause marks it selected",
              page.locator("#screen .chiplist button.on").count() >= 1)
        page.fill('#screen input[type=text] >> nth=0', 'read carefully')
        page.click('#screen button:has-text("Save") >> nth=0')
        page.wait_for_timeout(250)
        check("'read carefully' rejected", "not an instruction" in page.inner_text("#screen"),
              page.inner_text("#screen")[:200])
        page.fill('#screen input[type=text] >> nth=0',
                  'Check both sides are independent clauses before choosing a semicolon.')
        page.click('#screen button:has-text("Save") >> nth=0')
        page.wait_for_timeout(300)
        check("a real rule saves", "saved" in page.inner_text("#screen"))

        # ===================================================== history/dash
        print("\n[6] history + dashboard")
        timed(page, "open history",
              lambda: (page.click('#nav button:has-text("History")'),
                       page.wait_for_selector("#screen .item", timeout=6000)), budget_ms=1200)
        check("session listed", page.locator("#screen .item").count() >= 1)
        check("delete-all button present",
              page.locator('button:has-text("Delete all history")').count() == 1)
        timed(page, "reopen a 54q review from history",
              lambda: (page.click('button:has-text("Open review")'),
                       page.wait_for_selector("#screen", timeout=8000),
                       page.wait_for_timeout(200)), budget_ms=2500)
        check("rebuilt review shows the score", "Estimated score" in page.inner_text("#screen"))

        timed(page, "open dashboard",
              lambda: (page.click('#nav button:has-text("Dashboard")'),
                       page.wait_for_selector("#screen .tile", timeout=6000)), budget_ms=1500)
        check("dashboard shows totals", "questions answered" in page.inner_text("#screen").lower(),
              page.inner_text("#screen")[:150])
        check("dashboard shows domains", "domains" in page.inner_text("#screen").lower())

        # ==================================================== drill + stress
        print("\n[7] drill + sustained use")
        page.click('#nav button:has-text("Drill")')
        page.wait_for_selector('button:has-text("Start drill")')
        timed(page, "start an 8-question drill",
              lambda: (page.click('button:has-text("Start drill")'),
                       page.wait_for_selector(".quiz", timeout=10000)), budget_ms=3000)
        check("drill opened", page.locator(".quiz").count() == 1)
        page.click('.quizbar button:has-text("Submit")')
        page.wait_for_selector("#screen h1", timeout=10000)
        check("drill goes straight to review (no module 2)",
              "Adaptive routing" not in page.inner_text("#screen"))

        switch = []
        for _ in range(12):
            for route in ("Plan", "History", "Dashboard"):
                start = time.perf_counter()
                page.click(f'#nav button:has-text("{route}")')
                page.wait_for_timeout(60)
                switch.append((time.perf_counter() - start) * 1000)
        median_switch = sorted(switch)[len(switch) // 2]
        TIMINGS.append(("screen switch (median of 36)", median_switch))
        check("screen switches stay fast after sustained use",
              median_switch < 400, f"{median_switch:.0f} ms")

        # Measured in-page, because a Playwright click alone costs ~60-90 ms of
        # protocol round-trip. This is the number that used to sit at a flat
        # ~67 ms because of the unconditional spinner + the delayed-ACK tax.
        paint = page.evaluate("""async () => {
            const painted = () => new Promise(r =>
                requestAnimationFrame(() => requestAnimationFrame(r)));
            const btn = (name) => [...document.querySelectorAll('#nav button')]
                .find(b => b.textContent.includes(name));
            const times = [];
            for (const name of ['Plan', 'History', 'Dashboard', 'Calendar',
                                'Plan', 'History', 'Dashboard', 'Calendar']) {
                const t0 = performance.now();
                btn(name).click();
                await painted();
                await new Promise(r => setTimeout(r, 120));   // let the fetch land
                times.push(performance.now() - t0 - 120);
            }
            return times;
        }""")
        median_paint = sorted(paint)[len(paint) // 2]
        TIMINGS.append(("nav click -> painted (median, in-page)", median_paint))
        check("no spinner flash: nav paints in under 45 ms",
              median_paint < 45, f"{median_paint:.1f} ms")
        check("no navigation stalls", max(paint) < 200, f"{max(paint):.1f} ms")

        heap = page.evaluate("() => performance.memory ? performance.memory.usedJSHeapSize/1e6 : 0")
        TIMINGS.append((f"JS heap after 36 switches (MB)", heap))
        check("memory stayed reasonable", heap == 0 or heap < 120, f"{heap:.0f} MB")

        # ================================================ [n] spinner race
        # A go() call armed a 180ms spinner timer and never cancelled it, only
        # guarded it with a `settled` flag that was local to that call. Click
        # two tabs inside 180ms — an impatient double-click — and the FIRST
        # call's timer fired against its own still-false flag and wiped the
        # screen the second one had already painted. The user was left staring
        # at a spinner that nothing would ever clear.
        print("\n[n] fast double navigation")
        # The first attempt at this test passed against the BROKEN code, which
        # makes it worthless. The reason is the same one that hid the SQLite
        # single-writer bug: locally every fetch comes back in 40ms, so the
        # 180ms timer always found its own view already settled and the race
        # had no window to happen in. The bug needs a SLOW first view — which
        # is exactly the condition the people reporting it were under.
        # So: hold one endpoint for 700ms, and race a second navigation past it.
        page.route("**/api/dashboard*", lambda route: (
            time.sleep(0.7), route.continue_()))
        try:
            stuck = page.evaluate("""async () => {
                const btn = (t) => [...document.querySelectorAll('#nav button')]
                    .find(b => b.textContent.includes(t));
                btn('Dashboard').click();                    // slow: held 700ms
                await new Promise(r => setTimeout(r, 30));   // inside the 180ms
                btn('History').click();                      // fast: paints ~90ms
                await new Promise(r => setTimeout(r, 400));  // past the 180ms timer
                const screen = document.querySelector('#screen');
                return {
                    spinner: !!screen.querySelector('.spin'),
                    empty: screen.children.length === 0,
                    text: (screen.textContent || '').replace(/\\s+/g, ' ').slice(0, 60),
                };
            }""")
            check("a slow view's spinner does not wipe the screen you navigated to",
                  not stuck["spinner"] and not stuck["empty"], str(stuck))
            check("the second navigation is what you are left looking at",
                  "History" in stuck["text"] or "history" in stuck["text"].lower(),
                  str(stuck))

            # And the slow response must not paint over you when it finally
            # lands, 700ms after you asked for something else.
            page.wait_for_timeout(900)
            late = page.evaluate("""() => {
                const s = document.querySelector('#screen');
                return { spinner: !!s.querySelector('.spin'),
                         text: (s.textContent || '').replace(/\\s+/g, ' ').slice(0, 60) };
            }""")
            check("a superseded slow view does not paint over the new screen",
                  not late["spinner"]
                  and ("History" in late["text"] or "history" in late["text"].lower()),
                  str(late))
        finally:
            page.unroute("**/api/dashboard*")

        # Same race, run hard: ten clicks in a row must still settle on a real
        # screen rather than a spinner nobody cancelled.
        hammered = page.evaluate("""async () => {
            const names = ['Plan','History','Dashboard','Calendar','Plan',
                           'History','Dashboard','Calendar','Plan','History'];
            const btn = (t) => [...document.querySelectorAll('#nav button')]
                .find(b => b.textContent.includes(t));
            for (const n of names) { btn(n).click(); await new Promise(r => setTimeout(r, 15)); }
            await new Promise(r => setTimeout(r, 900));
            const screen = document.querySelector('#screen');
            return { spinner: !!screen.querySelector('.spin'),
                     empty: screen.children.length === 0 };
        }""")
        check("ten rapid navigations still land on a painted screen",
              not hammered["spinner"] and not hammered["empty"], str(hammered))

        # ============================================== [n] bootstrap failure
        # api() answers {error, offline:true} when the server does not respond,
        # which left bankOk UNDEFINED. boot() tested `!State.boot.bankOk`, so an
        # install with thousands of imported questions was shown the first-run
        # "drag your PDFs here" wizard with the nav bar stripped off — and no
        # way back. Only an explicit false may open the wizard.
        print("\n[n] bootstrap failure does not hijack a working install")
        check("the server sends bankOk as a real boolean",
              page.evaluate("async () => typeof (await (await fetch('/api/bootstrap')).json()).bankOk")
              == "boolean")
        # Reload the app for real with /api/bootstrap unreachable. This has to
        # drive the actual boot() — asserting against a JS copy of the rule
        # would only prove the copy agrees with itself.
        #
        # THE BUG: api() answers {error, offline:true} when the server does not
        # respond, so bankOk came back UNDEFINED. boot() tested
        # `!State.boot.bankOk`, undefined is falsy, and an install with
        # thousands of imported questions was handed the first-run "drag your
        # PDFs here" wizard — nav bar deleted, no way back to its own data.
        page.route("**/api/bootstrap*", lambda route: route.abort())
        boot_errors_from = len(errors)
        try:
            page.goto(URL, wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            screen = page.inner_text("#screen")
            check("an unreachable server does not open the setup wizard",
                  "Set up Cat Prep" not in screen, screen[:160])
            check("an unreachable server does not strip the app down to setup",
                  "drag" not in screen.lower(), screen[:160])
            check("an unreachable server says so, and offers a way out",
                  "Could not reach the app" in screen and "Try again" in screen,
                  screen[:160])
        finally:
            page.unroute("**/api/bootstrap*")
        # A deliberately aborted request logs a network error; it is the point
        # of the test, not a regression.
        del errors[boot_errors_from:]
        page.goto(URL, wait_until="networkidle")
        page.wait_for_selector("#screen h1", timeout=5000)
        check("the app recovers once the server answers again",
              page.locator("#nav button").count() > 0,
              page.inner_text("#screen")[:120])

        real_errors = [e for e in errors if "favicon" not in e.lower()]
        check("no JavaScript errors anywhere", not real_errors, str(real_errors[:3]))

        page.screenshot(path=os.path.join(HERE, "web_dashboard.png"), full_page=False)
        browser.close()
finally:
    srv.shutdown()
    srv.server_close()

print("\n" + "=" * 66)
print(f"{'TIMING':<48}{'ms':>10}")
print("-" * 66)
for label, ms in TIMINGS:
    print(f"{label:<48}{ms:>10.1f}")
print("=" * 66)
print(f"PASSED {len(PASS)}   FAILED {len(FAIL)}")
if FAIL:
    for name, detail in FAIL:
        print(f"   FAILED: {name}   {detail}")
    sys.exit(1)
print("all green")
