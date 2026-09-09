"""
test_profile.py — the generic layer: score-report parsing, profile maths,
and plan generation.

This is the part that lets the app plan for someone other than its author, so
it is tested against synthetic reports covering the cases that actually differ
between students: a banked section, no banked section, missing domain bands,
no reports at all, and a test date in the past.
"""
import os, sys, json, tempfile
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, APP)
os.environ.setdefault("CATSAT_DATA_DIR", tempfile.mkdtemp(prefix="catprep-test-"))

import score_report as sr
from profile import Profile
import plan_builder as pb

PASSED = FAILED = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok  {name}")
    else:
        FAILED += 1
        FAILURES.append((name, detail))
        print(f"  FAIL {name}   {detail}")


def report(label, when, rw, math, bands, official=True):
    return {"label": label, "date": when, "total": rw + math,
            "sections": {sr.RW: {"score": rw}, sr.MATH: {"score": math}},
            "domains": bands, "is_official": official,
            "has_domain_bands": bool(bands), "source_file": "synthetic"}


TOP = {"low": 680, "high": 800}
MID = {"low": 610, "high": 670}
LOW = {"low": 550, "high": 600}

ALL_TOP_MATH = {d: TOP for d in sr.MATH_DOMAINS}

print("\n[1] score report parsing")
text = """Test administration: SAT August 22, 2026
Tested on: Aug 22, 2026
TOTAL SCORE 1400
Reading and Writing 640
Math 760
Information and Ideas Algebra
Performance: 610-670 Performance: 680-800
Craft and Structure Advanced Math
Performance: 550-600 Performance: 680-800
Expression of Ideas Problem-Solving and Data Analysis
Performance: 550-600 Performance: 680-800
Standard English Conventions Geometry and Trigonometry
Performance: 680-800 Performance: 680-800
"""
domains = sr._parse_domains(text)
check("all eight domains parsed", len(domains) == 8, str(sorted(domains)))
check("two-column pairing is correct",
      domains["Craft and Structure"] == {"low": 550, "high": 600}
      and domains["Advanced Math"] == {"low": 680, "high": 800},
      str(domains.get("Craft and Structure")) + " / " + str(domains.get("Advanced Math")))
total, sections = sr._parse_scores(text)
check("total parsed", total == 1400, str(total))
check("both sections parsed", sections.get(sr.RW) == 640 and sections.get(sr.MATH) == 760,
      str(sections))
check("test date parsed", sr._parse_date(text) == date(2026, 8, 22), str(sr._parse_date(text)))
check("domain shares sum to 1 per section",
      abs(sum(sr.DOMAIN_SHARE[d] for d in sr.RW_DOMAINS) - 1.0) < 0.01
      and abs(sum(sr.DOMAIN_SHARE[d] for d in sr.MATH_DOMAINS) - 1.0) < 0.01)
check("a non-report PDF text is rejected",
      sr._parse_domains("just some words") == {})

print("\n[2] profile maths — superscore and banking")
me = Profile(name="T")
me.add_report(report("Aug", "2026-08-22", 640, 760, {**ALL_TOP_MATH,
              "Information and Ideas": MID, "Craft and Structure": LOW,
              "Expression of Ideas": LOW, "Standard English Conventions": TOP}))
check("superscore is the sum of best sections", me.superscore() == 1400, str(me.superscore()))
check("Math is detected as banked", me.banked_sections() == {sr.MATH},
      str(me.banked_sections()))
me.add_report(report("Sep", "2026-09-12", 700, 720, {}))
check("superscore keeps the best of each section, not the best total",
      me.superscore() == 700 + 760, str(me.superscore()))
check("bands come from the most recent report that has them",
      me.latest_domains()["Craft and Structure"] == LOW)

weak = Profile()
weak.add_report(report("x", "2026-08-22", 600, 600,
                       {d: MID for d in sr.ALL_DOMAINS}))
check("nothing is banked when no section is finished", weak.banked_sections() == set(),
      str(weak.banked_sections()))

practice_only = Profile()
practice_only.add_report(report("p", "2026-08-19", 720, 710, {}, official=False))
check("a practice report still gives a best-section estimate",
      practice_only.best_sections().get(sr.RW) == 720)
check("no domain bands means no crash", practice_only.domain_priorities()[0]["weight"] > 0)

print("\n[3] priorities weight by weakness AND section share")
pri = {p["domain"]: p for p in me.domain_priorities()}
check("a banked section's domains all weigh zero",
      all(pri[d]["weight"] == 0 for d in sr.MATH_DOMAINS),
      str([pri[d]["weight"] for d in sr.MATH_DOMAINS]))
check("the largest weak domain outranks an equally weak smaller one",
      pri["Craft and Structure"]["weight"] > pri["Expression of Ideas"]["weight"],
      f"C&S={pri['Craft and Structure']['weight']} EoI={pri['Expression of Ideas']['weight']}")
check("a top-band domain is marked leave-alone",
      "leave it alone" in pri["Standard English Conventions"]["verdict"].lower())

print("\n[4] short windows go to rule-based domains")
check("a short window prefers rules over the weakest domain",
      me.focus_for_window(8) == ["Expression of Ideas"], str(me.focus_for_window(8)))
check("a long window takes the weakest domain first",
      me.focus_for_window(40)[0] == "Craft and Structure", str(me.focus_for_window(40)))
check("a rules domain already worked does not claim a second short window",
      me.focus_for_window(8, already_worked={"Expression of Ideas"})[0] == "Craft and Structure",
      str(me.focus_for_window(8, already_worked={"Expression of Ideas"})))

print("\n[5] plan generation")
me.add_test_date(date(2026, 9, 12), "SAT #2")
me.add_test_date(date(2026, 10, 3), "SAT #3")
me.add_test_date(date(2020, 1, 1), "old")
plan = pb.build_plan(me, today=date(2026, 9, 4))
check("past test dates are ignored", all(t["date"].year == 2026 for t in plan["tests"]))
check("weeks were generated", len(plan["weeks"]) >= 4, str(len(plan["weeks"])))
check("every day to the last test has a plan",
      len(plan["days"]) == (date(2026, 10, 3) - date(2026, 9, 4)).days + 1,
      str(len(plan["days"])))
check("every generated day has at least one task",
      all(d["tasks"] for d in plan["days"].values()))
check("no generated day schedules the banked section",
      not [d for d, p in plan["days"].items() for t in p["tasks"]
           if t["action"] in ("drill", "module")
           and t["params"].get("section") == pb.SECTION_MATH],
      "a banked section still got scheduled")
check("test days are marked as test days",
      "test day" in plan["days"][date(2026, 9, 12)]["hours"])
check("the day before a test is off",
      "OFF" in plan["days"][date(2026, 9, 11)]["headline"],
      plan["days"][date(2026, 9, 11)]["headline"])
check("the day after a test is a brain dump",
      "Brain dump" in plan["days"][date(2026, 9, 13)]["headline"],
      plan["days"][date(2026, 9, 13)]["headline"])
check("the week containing a test is a taper",
      [w for w in plan["weeks"] if w["start"] <= date(2026, 9, 12) <= w["end"]][0]["kind"]
      == "taper")
check("a build week's first focus is a real domain",
      all(w["focus_domains"][0] in sr.ALL_DOMAINS
          for w in plan["weeks"] if w["kind"] == "build" and w["focus_domains"]))
check("every drill names a real domain",
      all(t["params"]["domains"][0] in sr.ALL_DOMAINS
          for p in plan["days"].values() for t in p["tasks"]
          if t["action"] == "drill" and t["params"]["domains"]))
check("every task carries the fields the UI reads",
      all(all(k in t for k in ("minutes", "label", "detail", "action", "params"))
          for p in plan["days"].values() for t in p["tasks"]))

print("\n[6] degenerate inputs don't crash")
empty = Profile()
check("no profile data yields no weeks", pb.build_plan(empty, date(2026, 9, 4))["weeks"] == [])
no_tests = Profile(); no_tests.add_report(report("x", "2026-08-22", 640, 760, {}))
check("reports but no test dates yields no weeks",
      pb.build_plan(no_tests, date(2026, 9, 4))["weeks"] == [])
only_tests = Profile(); only_tests.add_test_date(date(2026, 10, 3))
p2 = pb.build_plan(only_tests, date(2026, 9, 4))
check("test dates but no reports still produces a usable plan",
      len(p2["days"]) > 20 and all(d["tasks"] for d in p2["days"].values()),
      str(len(p2["days"])))

print("\n[7] round-trips through JSON")
with tempfile.TemporaryDirectory() as tmp:
    path = __import__("pathlib").Path(tmp) / "profile.json"
    me.save(path)
    back = Profile.load(path)
    check("a saved profile loads back identically",
          back.superscore() == me.superscore()
          and back.banked_sections() == me.banked_sections()
          and len(back.test_dates) == len(me.test_dates))
    check("the saved file is readable JSON a human could edit",
          isinstance(json.loads(path.read_text()), dict))
    check("an unknown key in the file is ignored, not fatal",
          (path.write_text(json.dumps({**json.loads(path.read_text()), "bogus": 1}))
           or Profile.load(path)) is not None)
check("a missing profile file returns None, not an exception",
      Profile.load(__import__("pathlib").Path("/nonexistent/profile.json")) is None)


# ---------------------------------------------------------------------------
# [8] Integration: a stranger's profile must drive the whole app, and none of
#     the author's hardcoded plan may leak into it.
# ---------------------------------------------------------------------------
print("\n[8] a new user's profile drives the running app")
_integration_ok, _detail = False, "skipped"
try:
    import shutil as _sh, tempfile as _tf
    _sandbox = _tf.mkdtemp(prefix="catprep-e2e-")
    _prev = os.environ.get("CATSAT_DATA_DIR")
    os.environ["CATSAT_DATA_DIR"] = _sandbox
    for _m in ("config", "database", "study_plan", "plan_builder", "profile", "web_api"):
        sys.modules.pop(_m, None)

    import make_fake_bank                                   # noqa: E402
    make_fake_bank.sandbox_env(); make_fake_bank.build(8)
    import database as _db; _db.init_db()                    # noqa: E402
    from profile import Profile as _P                        # noqa: E402

    _me = _P(name="Stranger", target_total=1450)
    _me.add_report(report("SAT Aug", "2026-08-22", 560, 620,
                          {"Information and Ideas": MID, "Craft and Structure": LOW,
                           "Expression of Ideas": LOW, "Standard English Conventions": MID,
                           "Algebra": MID, "Advanced Math": LOW,
                           "Problem-Solving and Data Analysis": TOP,
                           "Geometry and Trigonometry": MID}))
    _me.add_test_date(date.today() + timedelta(days=45), "SAT Nov")
    # make_fake_bank.sandbox_env() repoints CATSAT_DATA_DIR at dev_tests/_sandbox,
    # which every other suite shares. Capture where the profile ACTUALLY lands so
    # the finally block can remove it — a profile left behind here silently
    # rewrites the plan for every suite that runs afterwards.
    import profile as _profile_mod
    _written_profile = _profile_mod.PROFILE_PATH
    _me.save()

    import server as _srv_mod                                # noqa: E402
    from playwright.sync_api import sync_playwright          # noqa: E402
    _srv, _url = _srv_mod.create_server(); _srv_mod.serve_forever_in_thread(_srv)
    try:
        with sync_playwright() as _pw:
            _b = _pw.chromium.launch()
            _pg = _b.new_page(viewport={"width": 1400, "height": 900})
            _errs = []; _pg.on("pageerror", lambda e: _errs.append(str(e)))
            _pg.goto(_url, wait_until="networkidle")
            _pg.wait_for_selector("#screen h1", timeout=20000)
            # Labels are uppercased by CSS, so compare case-insensitively.
            _text = _pg.inner_text("#screen").lower()
            check("the user's own test date is what the app counts down to",
                  "sat nov" in _text, _text[:160])
            check("no hardcoded example test date leaks through",
                  "sat #2" not in _text and "2026-09-12" not in _text, _text[:160])
            check("the week focus comes from the user's own weak domains",
                  "advanced math" in _text or "craft and structure" in _text
                  or "expression of ideas" in _text, _text[:200])
            _pg.click('#nav button:has-text("Calendar")')
            _pg.wait_for_selector(".calday", timeout=10000)
            check("the calendar marks the user's own test date",
                  "sat nov" in _pg.inner_text("#screen").lower())
            check("no JavaScript errors with a generated plan", not _errs, str(_errs[:2]))
            _b.close()
    finally:
        _srv.shutdown(); _srv.server_close()
    _integration_ok = True
except ImportError as _exc:
    print(f"  --  integration skipped (playwright unavailable: {_exc})")
finally:
    try:
        try:
            _written_profile.unlink()
        except (NameError, OSError):
            pass
        _sh.rmtree(_sandbox, ignore_errors=True)
        if _prev is None:
            os.environ.pop("CATSAT_DATA_DIR", None)
        else:
            os.environ["CATSAT_DATA_DIR"] = _prev
    except Exception:
        pass

print("\n" + "=" * 62)
print(f"PASSED {PASSED}   FAILED {FAILED}")
if FAILURES:
    for name, detail in FAILURES:
        print(f"   FAILED: {name}   {detail}")
    raise SystemExit(1)
print("all green")
