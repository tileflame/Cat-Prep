"""Headless tests for the non-UI layers. Run: python3 dev_tests/test_engine.py"""
import os, random, sqlite3, sys, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)          # dev_tests/ lives inside the app folder
sys.path.insert(0, HERE)
sys.path.insert(0, APP)
os.chdir(APP)

# Point the app at dev_tests/_sandbox BEFORE importing config, so the suite can
# never read, overwrite or delete your real question bank or study history.
import make_fake_bank
make_fake_bank.sandbox_env()

if not os.path.exists(os.path.join(make_fake_bank.SANDBOX, "database", "questions.db")):
    make_fake_bank.build(6)
for _f in ("database/progress.db", "database/progress.db-wal", "database/progress.db-shm"):
    _p = os.path.join(make_fake_bank.SANDBOX, _f)
    if os.path.exists(_p):
        os.remove(_p)

import config, database, models, question_repo, attempt_repo, adaptive_engine as ae
from models import Question, AttemptRecord, answers_match

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok  " if cond else "  FAIL ") + name + (f"  <- {detail}" if detail and not cond else ""))


# ---------------------------------------------------------------- 1. SCHEMA
print("\n[1] schema + migrations")
report = database.init_db()
check("init_db reports bank ok", report["bank_ok"], report["bank_message"])
with database.question_conn() as c:
    cols = [r[1] for r in c.execute("PRAGMA table_info(questions)")]
check("bank has rationale + is_open_ended", "rationale" in cols and "is_open_ended" in cols, str(cols))

# Simulate the legacy bug: a questions.db made by the OLD database.py.
legacy = os.path.join(make_fake_bank.SANDBOX, "database", "questions.db")
bak = legacy + ".bak"
os.replace(legacy, bak)
lc = sqlite3.connect(legacy)
lc.execute("""CREATE TABLE questions (id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id TEXT UNIQUE, section TEXT, domain TEXT, skill TEXT, difficulty TEXT,
    question_img TEXT, answer_img TEXT, correct_answer TEXT)""")
lc.execute("""CREATE TABLE user_history (history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id TEXT, section TEXT, domain TEXT, is_correct INTEGER,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)""")
lc.execute("INSERT INTO user_history (question_id,section,domain,is_correct,timestamp) VALUES ('x1','Math','Algebra',1,'2026-01-01 10:00:00')")
lc.execute("INSERT INTO user_history (question_id,section,domain,is_correct,timestamp) VALUES ('x2','Math','Algebra',0,'2026-01-01 10:05:00')")
lc.commit(); lc.close()

added = database.repair_question_bank_schema()
check("legacy schema repaired", "rationale" in added and "is_open_ended" in added, str(added))

# The importer's INSERT must now succeed against the repaired table.
try:
    ic = sqlite3.connect(legacy)
    ic.execute("""INSERT OR REPLACE INTO questions
        (question_id, section, domain, skill, difficulty, question_img, correct_answer, rationale, is_open_ended)
        VALUES (?,?,?,?,?,?,?,?,?)""", ("zz1", "Math", "Algebra", "s", "Easy", "i.png", "A", "r.png", 0))
    ic.commit(); ic.close()
    check("sat_importer INSERT works on repaired table", True)
except Exception as e:
    check("sat_importer INSERT works on repaired table", False, repr(e))

# Legacy history migration.
prog = os.path.join(make_fake_bank.SANDBOX, "database", "progress.db")
for p in (prog, prog + "-wal", prog + "-shm"):
    if os.path.exists(p):
        os.remove(p)
database.init_progress_db()
sessions = attempt_repo.list_sessions()
check("legacy user_history migrated into a session", len(sessions) == 1 and sessions[0]["attempt_count"] == 2, str(sessions))
database.init_progress_db()
check("migration is idempotent", len(attempt_repo.list_sessions()) == 1)

os.replace(bak, legacy)
for p in (prog, prog + "-wal", prog + "-shm"):
    if os.path.exists(p):
        os.remove(p)
database.init_db()

# ------------------------------------------------------- 2. ANSWER MATCHING
print("\n[2] answer equivalence")
cases = [
    (("B", "B", False), True), (("b", "B", False), True), (("b ", "B.", False), True),
    (("A", "B", False), False), (("", "B", False), False), ((None, "B", False), False),
    (("0.5", "1/2", True), True), ((".5", "0.5", True), True), (("1/2", ".5", True), True),
    (("2 1/2", "5/2", True), True), (("-6", "−6", True), True), (("$12", "12", True), True),
    (("12", "1,200", True), False), ((".667", "2/3", True), True), ((".666", "2/3", True), True),
    (("0.66", "2/3", True), False), (("3/4", ".75, 3/4", True), True), (("7", "7 or -7", True), True),
    (("-7", "7 or -7", True), True), (("8", "7 or -7", True), False), (("abc", "abc", True), True),
    (("12", "", True), False),
]
for (user, correct, oe), want in cases:
    got = answers_match(user, correct, oe)
    check(f"answers_match({user!r},{correct!r},oe={oe}) -> {want}", got == want, f"got {got}")

# ------------------------------------------------------- 3. DIFFICULTY MATH
print("\n[3] difficulty targets")
for total, tier in [(27, "baseline"), (22, "baseline"), (27, "hard"), (22, "easy"), (6, "baseline"), (8, "hard")]:
    t = ae._difficulty_targets(total, tier)
    check(f"targets sum to {total} ({tier})", sum(t.values()) == total, str(t))
b = ae._difficulty_targets(27, "baseline")
check("baseline 27 is an even 9/9/9", b == {"Easy": 9, "Medium": 9, "Hard": 9}, str(b))
h = ae._difficulty_targets(27, "hard")
check("hard tier is hard-weighted", h["Hard"] > h["Easy"] and h["Hard"] >= 16, str(h))
e = ae._difficulty_targets(27, "easy")
check("easy tier is easy-weighted", e["Easy"] > e["Hard"] and e["Easy"] >= 16, str(e))

split = ae._split_targets_across_domains(config.BLUEPRINT["Reading and Writing"]["domain_quota"], b)
check("domain split totals 27", sum(sum(v.values()) for v in split.values()) == 27, str(split))
# Both margins must hold: every domain hits its quota AND every difficulty its target.
for section in ("Reading and Writing", "Math"):
    quota = config.BLUEPRINT[section]["domain_quota"]
    size = config.BLUEPRINT[section]["questions_per_module"]
    for tier in ("baseline", "hard", "easy"):
        targets = ae._difficulty_targets(size, tier)
        grid = ae._split_targets_across_domains(quota, targets)
        rows = {d: sum(v.values()) for d, v in grid.items()}
        cols = {k: sum(grid[d][k] for d in quota) for k in ("Easy", "Medium", "Hard")}
        check(f"{section}/{tier}: every domain hits its quota", rows == quota, f"{rows} vs {quota}")
        check(f"{section}/{tier}: every difficulty hits its target", cols == targets, f"{cols} vs {targets}")
        check(f"{section}/{tier}: no negative cells",
              all(n >= 0 for v in grid.values() for n in v.values()), str(grid))

# -------------------------------------------------------- 4. MODULE BUILD
print("\n[4] module assembly")
rng = random.Random(1)
m1 = ae.build_module("Reading and Writing", 1, "baseline", rng=rng)
check("RW M1 has 27 questions", m1.size == 27, f"{m1.size}, gaps={m1.domain_gaps}")
check("RW M1 no duplicates", len({q.question_id for q in m1.questions}) == m1.size)
check("RW M1 fidelity 1.0", m1.fidelity == 1.0, str(m1.fidelity))
domains_seen = [q.domain for q in m1.questions]
order = config.BLUEPRINT["Reading and Writing"]["domain_order"]
ranks = [order.index(d) for d in domains_seen if d in order]
check("RW M1 grouped by domain in Bluebook order", ranks == sorted(ranks), str(domains_seen))
from collections import Counter
counts = Counter(q.domain for q in m1.questions)
expected = config.BLUEPRINT["Reading and Writing"]["domain_quota"]
check("RW M1 matches the domain quota exactly", dict(counts) == expected,
      f"{dict(counts)} vs {expected}")
mcounts = Counter(q.domain for q in ae.build_module("Math", 1, "baseline", rng=random.Random(11)).questions)
mexpected = config.BLUEPRINT["Math"]["domain_quota"]
check("Math M1 matches the domain quota exactly", dict(mcounts) == mexpected,
      f"{dict(mcounts)} vs {mexpected}")
# easiest -> hardest inside each domain group
rank = {"Easy": 0, "Medium": 1, "Hard": 2}
ok = True
for d in order:
    seq = [rank[q.difficulty] for q in m1.questions if q.domain == d]
    if seq != sorted(seq):
        ok = False
check("RW M1 easy->hard inside each domain", ok)

m2 = ae.build_module("Reading and Writing", 2, "hard",
                     exclude_ids={q.question_id for q in m1.questions}, rng=rng)
check("RW M2 has 27 questions", m2.size == 27, f"{m2.size}, gaps={m2.domain_gaps}")
overlap = {q.question_id for q in m1.questions} & {q.question_id for q in m2.questions}
check("M1 and M2 share no questions", not overlap, str(overlap))
check("M2 hard tier skews hard", m2.difficulty_actual["Hard"] > m2.difficulty_actual["Easy"], str(m2.difficulty_actual))

mm1 = ae.build_module("Math", 1, "baseline", rng=rng)
check("Math M1 has 22 questions", mm1.size == 22, f"{mm1.size}, gaps={mm1.domain_gaps}")
oe_positions = [i for i, q in enumerate(mm1.questions) if q.is_open_ended]
mc_positions = [i for i, q in enumerate(mm1.questions) if not q.is_open_ended]
check("Math grid-ins come last", (not oe_positions) or (not mc_positions) or min(oe_positions) > max(mc_positions),
      f"oe={oe_positions} mc={mc_positions}")
check("positions are 1..n", [q.position for q in mm1.questions] == list(range(1, mm1.size + 1)))

# Degradation: a bank too thin to satisfy the blueprint must report, not crash.
print("\n[5] graceful degradation on a thin bank")
os.replace(os.path.join(make_fake_bank.SANDBOX, "database", "questions.db"), os.path.join(make_fake_bank.SANDBOX, "database", "full.db"))
make_fake_bank.build(1, with_images=False, thin_domains=("Expression of Ideas",))
thin = ae.build_module("Reading and Writing", 1, "baseline", rng=random.Random(3))
check("thin bank still returns a module", thin.size > 0, str(thin.size))
check("thin bank reports honest fidelity", 0 < thin.fidelity <= 1.0, str(thin.fidelity))
check("thin bank has no duplicates", len({q.question_id for q in thin.questions}) == thin.size)
os.remove(os.path.join(make_fake_bank.SANDBOX, "database", "questions.db"))
os.replace(os.path.join(make_fake_bank.SANDBOX, "database", "full.db"), os.path.join(make_fake_bank.SANDBOX, "database", "questions.db"))

# Empty bank must not explode.
os.replace(os.path.join(make_fake_bank.SANDBOX, "database", "questions.db"), os.path.join(make_fake_bank.SANDBOX, "database", "full.db"))
sqlite3.connect(os.path.join(make_fake_bank.SANDBOX, "database", "questions.db")).close()
database.repair_question_bank_schema()
empty = ae.build_module("Math", 1, "baseline", rng=random.Random(4))
check("empty bank returns empty module without raising", empty.size == 0)
ok_flag, msg = database.question_bank_is_usable()
check("empty bank reports unusable with a message", not ok_flag and "empty" in msg.lower(), msg)
os.remove(os.path.join(make_fake_bank.SANDBOX, "database", "questions.db"))
os.replace(os.path.join(make_fake_bank.SANDBOX, "database", "full.db"), os.path.join(make_fake_bank.SANDBOX, "database", "questions.db"))

# ------------------------------------------------------------- 6. ROUTING
print("\n[6] routing")


def fake_records(pattern, difficulties=None):
    recs = []
    for i, ok in enumerate(pattern):
        d = (difficulties or ["Medium"] * len(pattern))[i]
        q = Question(f"q{i}", "Math", "Algebra", "s", d, None, "A", None, False)
        recs.append(AttemptRecord(question=q, selected_answer="A" if ok else "B", is_correct=bool(ok)))
    return recs


r = ae.evaluate_module("Math", 1, "baseline", fake_records([1] * 18 + [0] * 9))
check("18/27 routes to hard", ae.route(r) == "hard", f"{r.weighted_accuracy:.3f}")
r = ae.evaluate_module("Math", 1, "baseline", fake_records([1] * 17 + [0] * 10))
check("17/27 (63%) routes to easy", ae.route(r) == "easy", f"{r.weighted_accuracy:.3f}")
r = ae.evaluate_module("Math", 1, "baseline", fake_records([0] * 27))
check("0/27 routes to easy", ae.route(r) == "easy")
r = ae.evaluate_module("Math", 1, "baseline", fake_records([1] * 27))
check("27/27 routes to hard", ae.route(r) == "hard")
# Weighting: same raw score, different difficulty profile -> different route.
hard_right = ae.evaluate_module("Math", 1, "baseline",
                                fake_records([1] * 17 + [0] * 10, ["Hard"] * 17 + ["Easy"] * 10))
easy_right = ae.evaluate_module("Math", 1, "baseline",
                                fake_records([1] * 17 + [0] * 10, ["Easy"] * 17 + ["Hard"] * 10))
check("weighting rewards correct Hard items",
      ae.route(hard_right) == "hard" and ae.route(easy_right) == "easy",
      f"{hard_right.weighted_accuracy:.3f} vs {easy_right.weighted_accuracy:.3f}")
check("unweighted mode ignores difficulty",
      ae.route(hard_right, use_weighting=False) == ae.route(easy_right, use_weighting=False))
empty_r = ae.evaluate_module("Math", 1, "baseline", [])
check("empty module does not divide by zero", ae.route(empty_r) == "easy" and empty_r.raw_accuracy == 0.0)
check("routing_explanation mentions the threshold", "65%" in ae.routing_explanation(r, "hard"))

# ------------------------------------------------------------ 7. SCORING
print("\n[7] scoring")
RW = "Reading and Writing"
MATH = "Math"
check("hard route perfect = 800", ae.estimate_section_score(54, 54, "hard", RW) == 800)
check("easy route perfect is capped", ae.estimate_section_score(54, 54, "easy", RW) <= 640,
      str(ae.estimate_section_score(54, 54, "easy", RW)))
check("easy route ceiling < hard route perfect",
      ae.estimate_section_score(54, 54, "easy", RW) < ae.estimate_section_score(54, 54, "hard", RW))
check("zero correct floors at 200", ae.estimate_section_score(0, 54, "easy", RW) == 200)
# The old parametric curve returned 330 for 0/54 on the hard route, because its
# base was 330 and the floor only clamped BELOW 200. Getting nothing right was
# worth 130 points.
check("zero correct floors at 200 on the hard route too",
      ae.estimate_section_score(0, 54, "hard", RW) == 200,
      str(ae.estimate_section_score(0, 54, "hard", RW)))
check("scores are multiples of 10",
      all(ae.estimate_section_score(k, 54, "hard", RW) % 10 == 0 for k in range(0, 55)))
mono = [ae.estimate_section_score(k, 54, "hard", RW) for k in range(0, 55)]
check("score is monotonic in correct count", all(a <= b for a, b in zip(mono, mono[1:])), str(mono[:6]))
check("total clamps to 400..1600", ae.estimate_total_score({"a": 800, "b": 800}) == 1600
      and ae.estimate_total_score({"a": 200, "b": 200}) == 400)
check("zero-question section scores 0", ae.estimate_section_score(0, 0, "hard", RW) == 0)

# ---- The complaint that prompted the rewrite: "2 wrong cant be an 800".
# A smooth base+span*pct**0.9 curve cannot have a steep top AND a sane middle,
# so it gave 800 for 54, 53 and 52 correct alike, then ran 40-50 points hot all
# the way down. These are the anchors that must not drift back.
check("one wrong is not 800", ae.estimate_section_score(53, 54, "hard", RW) == 780,
      str(ae.estimate_section_score(53, 54, "hard", RW)))
check("two wrong is not 800", ae.estimate_section_score(52, 54, "hard", RW) == 770,
      str(ae.estimate_section_score(52, 54, "hard", RW)))
check("three wrong is not 800", ae.estimate_section_score(51, 54, "hard", RW) == 760,
      str(ae.estimate_section_score(51, 54, "hard", RW)))
check("only a perfect raw score earns 800",
      sum(1 for k in range(0, 55) if ae.estimate_section_score(k, 54, "hard", RW) == 800) == 1)
check("eight wrong lands near 710", ae.estimate_section_score(46, 54, "hard", RW) == 710,
      str(ae.estimate_section_score(46, 54, "hard", RW)))
check("the top is steeper than the middle",
      (ae.estimate_section_score(54, 54, "hard", RW) - ae.estimate_section_score(50, 54, "hard", RW))
      > (ae.estimate_section_score(34, 54, "hard", RW) - ae.estimate_section_score(30, 54, "hard", RW)))

# Math is a 44-question section, so one wrong answer is a bigger fraction of it
# and the two sections must not share a curve.
check("math perfect = 800", ae.estimate_section_score(44, 44, "hard", MATH) == 800)
check("math two wrong is not 800", ae.estimate_section_score(42, 44, "hard", MATH) == 770,
      str(ae.estimate_section_score(42, 44, "hard", MATH)))
check("math only a perfect raw score earns 800",
      sum(1 for k in range(0, 45) if ae.estimate_section_score(k, 44, "hard", MATH) == 800) == 1)
check("math curve is monotonic",
      all(a <= b for a, b in zip(
          [ae.estimate_section_score(k, 44, "hard", MATH) for k in range(0, 45)],
          [ae.estimate_section_score(k, 44, "hard", MATH) for k in range(1, 45)])))
check("math and rw differ somewhere in the middle",
      any(ae.estimate_section_score(k, 44, "hard", MATH)
          != ae.estimate_section_score(round(k * 54 / 44), 54, "hard", RW)
          for k in range(0, 45)))

# Route ceilings, and the compression underneath them.
check("baseline cannot reach 800", ae.estimate_section_score(54, 54, "baseline", RW) == 760,
      str(ae.estimate_section_score(54, 54, "baseline", RW)))
check("a perfect 8-question drill is not an 800",
      ae.estimate_section_score(8, 8, "baseline", RW) < 800,
      str(ae.estimate_section_score(8, 8, "baseline", RW)))
check("the easy route compresses, it does not just clip",
      ae.estimate_section_score(30, 54, "easy", RW) < ae.estimate_section_score(30, 54, "hard", RW))
check("the easy route still ranks performances apart",
      ae.estimate_section_score(50, 54, "easy", RW) > ae.estimate_section_score(30, 54, "easy", RW))

# Fallbacks: an unknown section or tier must degrade, never raise.
check("unknown section falls back to a curve", ae.estimate_section_score(52, 54, "hard", "Nonsense") == 770)
check("unknown tier falls back to baseline",
      ae.estimate_section_score(54, 54, "nonsense", MATH)
      == ae.estimate_section_score(54, 54, "baseline", MATH))
check("section argument is optional", ae.estimate_section_score(52, 54, "hard") == 770)
check("estimates below 20 questions are flagged unreliable",
      not ae.score_is_reliable(8) and ae.score_is_reliable(27))

# ------------------------------------------------------------- 8. DRILLS
print("\n[8] drills")
d = ae.build_drill(section="Reading and Writing", domains=["Craft and Structure"], count=8, rng=random.Random(5))
check("drill returns 8", len(d) == 8, str(len(d)))
check("drill stays in the chosen domain", all(q.domain == "Craft and Structure" for q in d))
seq = [rank[q.difficulty] for q in d]
check("drill ramps easy -> medium -> hard", seq == sorted(seq), str([q.difficulty for q in d]))
check("drill spans more than one difficulty", len(set(seq)) > 1, str([q.difficulty for q in d]))
multi = ae.build_drill(section="Math", domains=["Algebra", "Geometry and Trigonometry"], count=10, rng=random.Random(6))
check("multi-domain drill returns 10", len(multi) == 10, str(len(multi)))
check("multi-domain drill covers both domains", len({q.domain for q in multi}) == 2, str({q.domain for q in multi}))
check("drill has no duplicates", len({q.question_id for q in multi}) == 10)
huge = ae.build_drill(section="Math", domains=["Algebra"], count=100000, rng=random.Random(7))
check("over-large drill degrades to what exists", 0 < len(huge) < 100000, str(len(huge)))
filtered = ae.build_drill(section="Math", domains=["Algebra"], count=5,
                          difficulty_filter="Hard", rng=random.Random(8))
check("difficulty filter respected", all(q.difficulty == "Hard" for q in filtered))

# --------------------------------------------------------- 9. PERSISTENCE
print("\n[9] persistence + analytics")
attempt_repo.clear_history(keep_notes=False)
sid = attempt_repo.create_session("section_test", "Reading and Writing", "Test sitting", {"x": 1})
mid = attempt_repo.create_module(sid, "Reading and Writing", 1, "baseline", 27, 1920)
recs = []
for i, q in enumerate(m1.questions, start=1):
    correct = i % 3 != 0
    recs.append(AttemptRecord(question=q, selected_answer=q.correct_answer if correct else "Z",
                              is_correct=correct, was_flagged=(i % 7 == 0),
                              eliminated={"A"} if i % 5 == 0 else set(),
                              time_spent_ms=40000 + i * 500, position=i))
n = attempt_repo.record_attempts(sid, mid, recs)
check("attempts written", n == 27, str(n))
attempt_repo.finish_module(mid, correct_count=18, raw_accuracy=18 / 27, weighted_accuracy=0.68,
                           routed_to="hard", time_used_seconds=1500)
attempt_repo.finish_session(sid, total_questions=27, correct_count=18,
                            duration_seconds=1500, estimated_score=610)

stats = attempt_repo.overall_stats()
check("overall_stats totals", stats["total"] == 27 and stats["correct"] == 18, str(stats))
check("overall_stats avg time > 0", stats["avg_seconds"] > 0, str(stats["avg_seconds"]))
dom = attempt_repo.breakdown("domain", session_id=sid)
check("domain breakdown covers 4 domains", len(dom) == 4, str([d["bucket"] for d in dom]))
check("domain accuracies are percentages", all(0 <= d["accuracy"] <= 100 for d in dom))
sk = attempt_repo.breakdown("skill", session_id=sid)
check("skill breakdown is non-empty (skill column now fetched)", len(sk) >= 3, str(len(sk)))
check("skill buckets are real skill names, not 'Unclassified'",
      all(b["bucket"] != "Unclassified" for b in sk), str([b["bucket"] for b in sk]))
diffb = attempt_repo.breakdown("difficulty", session_id=sid)
check("difficulty breakdown has 3 buckets", len(diffb) == 3, str([d["bucket"] for d in diffb]))

atts = attempt_repo.get_session_attempts(sid)
check("attempts read back in order", [a["position"] for a in atts] == list(range(1, 28)))
check("flags round-trip", sum(a["was_flagged"] for a in atts) == 3, str(sum(a["was_flagged"] for a in atts)))
check("eliminated round-trips", any(a["eliminated"] == "A" for a in atts))
check("time_spent round-trips", all(a["time_spent_ms"] > 0 for a in atts))
sess = attempt_repo.get_session(sid)
check("session summary stored", sess["correct_count"] == 18 and sess["estimated_score"] == 610)
mods = attempt_repo.get_session_modules(sid)
check("module row stored with routing", mods[0]["routed_to"] == "hard")
listed = attempt_repo.list_sessions()
check("history list includes tier path", listed[0]["tier_path"] == ["baseline"], str(listed[0]["tier_path"]))

seen = attempt_repo.seen_counts()
check("seen_counts tracks served questions", len(seen) == 27)
fresh_module = ae.build_module("Reading and Writing", 1, "baseline",
                               seen_counts=seen, rng=random.Random(9))
reused = len({q.question_id for q in fresh_module.questions} & set(seen))
check("freshness prefers unseen questions", reused == 0, f"{reused} repeats")

missed = attempt_repo.question_ids_where(only_incorrect=True)
check("missed pool matches the 9 wrong answers", len(missed) == 9, str(len(missed)))
flagged = attempt_repo.question_ids_where(only_flagged=True)
check("flagged pool matches the 3 flags", len(flagged) == 3, str(len(flagged)))
both = attempt_repo.question_ids_where(only_incorrect=True, only_flagged=True)
check("combined pool de-duplicates", len(both) <= 12 and len(both) >= 9, str(len(both)))

tl = attempt_repo.accuracy_timeline()
check("timeline has one point per session", len(tl) == 1 and abs(tl[0]["accuracy"] - 100 * 18 / 27) < 0.01, str(tl))
weak = attempt_repo.weakest("domain", min_attempts=1)
check("weakest returns sorted ascending", weak == sorted(weak, key=lambda b: (b["accuracy"], -b["total"])))
pace = attempt_repo.pace_stats()
check("pace stats computed", pace["count"] == 27 and pace["avg_seconds"] > 0, str(pace))

attempt_repo.save_note("q-note-1", "remember: transitions signal contrast")
check("note saved and read back", attempt_repo.get_note("q-note-1").startswith("remember"))
attempt_repo.save_note("q-note-1", "updated")
check("note upserts", attempt_repo.get_note("q-note-1") == "updated")
check("notes_count", attempt_repo.notes_count() == 1)

fetched = question_repo.fetch_by_ids([q.question_id for q in m1.questions[:5]])
check("fetch_by_ids round-trips", len(fetched) == 5)
check("fetch_by_ids tolerates unknown ids", len(question_repo.fetch_by_ids(["nope"])) == 0)

attempt_repo.delete_session(sid)
check("delete_session cascades", attempt_repo.overall_stats()["total"] == 0)
check("notes survive session deletion", attempt_repo.notes_count() == 1)
attempt_repo.clear_history(keep_notes=True)
check("clear_history keeps notes by default", attempt_repo.notes_count() == 1)
attempt_repo.clear_history(keep_notes=False)
check("clear_history can drop notes", attempt_repo.notes_count() == 0)

# ------------------------------------------------------------ 10. MODELS
print("\n[10] model helpers")
q = Question("z", "Math", "Algebra", "s", "Hard", "images/nope.png", "A",
             "No explanation image generated.", False)
check("missing rationale sentinel -> None", q.rationale_path is None)
check("has_rationale False for sentinel", q.has_rationale is False)
check("missing image file -> None", q.image_path is None)
real = question_repo.fetch(section="Math", limit=1)[0]
check("real image resolves", real.image_path is not None and os.path.exists(real.image_path), str(real.question_img))
check("difficulty weight applied", Question("a", "M", "d", "s", "Hard", None, "A", None, False).weight == 1.5)
check("unknown difficulty normalises to Medium",
      Question.from_row({"question_id": "a", "difficulty": "bogus"}).difficulty == "Medium")
check("section alias normalises", config.normalize_section("reading & writing") == "Reading and Writing")
check("None correct_answer does not crash check()",
      Question("a", "M", "d", "s", "Easy", None, "", None, False).check("A") is False)

print(f"\n{'=' * 60}\nPASSED {len(PASS)}   FAILED {len(FAIL)}")
if FAIL:
    for f in FAIL:
        print("   FAILED:", f)
    sys.exit(1)
print("all green")
