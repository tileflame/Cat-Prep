"""Headless tests for the non-UI layers. Run: python3 dev_tests/test_engine.py"""
import os, random, shutil, sqlite3, sys, traceback
from collections import Counter

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
database.reset_pool()
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
database.reset_pool()
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
# Bluebook groups R&W by SKILL, not just domain: vocabulary first, Rhetorical
# Synthesis last, and easiest to hardest inside each skill group. Inside a
# domain the difficulty therefore resets at every skill boundary, which is why
# this used to be asserted per domain and no longer can be.
rank = {"Easy": 0, "Medium": 1, "Hard": 2}
blocks = config.BLUEPRINT["Reading and Writing"]["skill_order"]
block_of = {ae.skill_key(name): i for i, block in enumerate(blocks) for name in block}
groups = [block_of.get(ae.skill_key(q.skill), 99) for q in m1.questions]
check("RW M1 skill groups in Bluebook order", groups == sorted(groups),
      str([q.skill for q in m1.questions]))
check("RW M1 opens with Words in Context",
      ae.skill_key(m1.questions[0].skill) == ae.skill_key("Words in Context"),
      m1.questions[0].skill)
check("RW M1 closes with Rhetorical Synthesis",
      ae.skill_key(m1.questions[-1].skill) == ae.skill_key("Rhetorical Synthesis"),
      m1.questions[-1].skill)
ok = True
for g in set(groups):
    seq = [rank[q.difficulty] for q, gg in zip(m1.questions, groups) if gg == g]
    if seq != sorted(seq):
        ok = False
check("RW M1 easy->hard inside each skill group", ok)
# Each named skill gets exactly its count, so vocabulary and Cross-Text
# Connections are counted separately even though both are Craft and Structure.
skill_counts = Counter(ae.skill_key(q.skill) for q in m1.questions)
wanted = {ae.skill_key(s): n
          for per in config.BLUEPRINT["Reading and Writing"]["skill_quota"].values()
          for s, n in per.items()}
check("RW M1 matches the skill quota exactly", dict(skill_counts) == wanted,
      f"{dict(skill_counts)} vs {wanted}")
check("RW M1 reports no skill gaps on a full bank", not m1.skill_gaps, str(m1.skill_gaps))

m2 = ae.build_module("Reading and Writing", 2, "hard",
                     exclude_ids={q.question_id for q in m1.questions}, rng=rng)
check("RW M2 has 27 questions", m2.size == 27, f"{m2.size}, gaps={m2.domain_gaps}")
overlap = {q.question_id for q in m1.questions} & {q.question_id for q in m2.questions}
check("M1 and M2 share no questions", not overlap, str(overlap))
check("M2 hard tier skews hard", m2.difficulty_actual["Hard"] > m2.difficulty_actual["Easy"], str(m2.difficulty_actual))

mm1 = ae.build_module("Math", 1, "baseline", rng=rng)
check("Math M1 has 22 questions", mm1.size == 22, f"{mm1.size}, gaps={mm1.domain_gaps}")
# Bluebook Math is one run from easiest to hardest with all four domains mixed.
# Grid-ins sat at the end on the PAPER test; on the digital one they are placed
# by difficulty like everything else.
mseq = [rank[q.difficulty] for q in mm1.questions]
check("Math M1 runs easy->hard across the whole module", mseq == sorted(mseq), str(mseq))
first_half = {q.domain for q in mm1.questions[:11]}
check("Math M1 mixes domains instead of grouping them", len(first_half) >= 3,
      str([q.domain for q in mm1.questions]))
check("Math M1 spreads its skills", len({q.skill for q in mm1.questions}) >= 8,
      str(Counter(q.skill for q in mm1.questions)))
check("positions are 1..n", [q.position for q in mm1.questions] == list(range(1, mm1.size + 1)))

# Degradation: a bank too thin to satisfy the blueprint must report, not crash.
print("\n[5] graceful degradation on a thin bank")
database.reset_pool()
os.replace(os.path.join(make_fake_bank.SANDBOX, "database", "questions.db"), os.path.join(make_fake_bank.SANDBOX, "database", "full.db"))
make_fake_bank.build(1, with_images=False, thin_domains=("Expression of Ideas",))
thin = ae.build_module("Reading and Writing", 1, "baseline", rng=random.Random(3))
check("thin bank still returns a module", thin.size > 0, str(thin.size))
check("thin bank reports honest fidelity", 0 < thin.fidelity <= 1.0, str(thin.fidelity))
check("thin bank has no duplicates", len({q.question_id for q in thin.questions}) == thin.size)
database.reset_pool()
os.remove(os.path.join(make_fake_bank.SANDBOX, "database", "questions.db"))
os.replace(os.path.join(make_fake_bank.SANDBOX, "database", "full.db"), os.path.join(make_fake_bank.SANDBOX, "database", "questions.db"))

# A bank with a whole SKILL missing. Real exports are lopsided, and Cross-Text
# Connections in particular is the rarest question type in the bank. The module
# must still come out full, fill the hole from the same domain, keep the domain
# quota exact, and say which skill it could not supply.
database.reset_pool()
_qdb = os.path.join(make_fake_bank.SANDBOX, "database", "questions.db")
shutil.copy2(_qdb, _qdb + ".bak")
_c = sqlite3.connect(_qdb)
_c.execute("DELETE FROM questions WHERE skill = 'Cross-Text Connections'")
_c.commit(); _c.close()
database.reset_pool()
holed = ae.build_module("Reading and Writing", 1, "baseline", rng=random.Random(5))
check("a missing skill still yields a full module", holed.size == 27, str(holed.size))
check("the missing skill is reported as a skill gap",
      any("Cross-Text" in k for k in holed.skill_gaps), str(holed.skill_gaps))
check("its place is filled from the same domain",
      Counter(q.domain for q in holed.questions)["Craft and Structure"] == 7,
      str(Counter(q.domain for q in holed.questions)))
check("no domain gap when a sibling skill can cover", not holed.domain_gaps, str(holed.domain_gaps))
database.reset_pool()
os.replace(_qdb + ".bak", _qdb)
database.reset_pool()

# Skill labels drift between exports: punctuation, case, an Oxford comma.
check("skill matching ignores punctuation and case",
      ae.skill_key("Form, Structure and Sense") == ae.skill_key("form, structure, and sense")
      and ae.skill_key("Cross-text Connections") == ae.skill_key("Cross-Text Connections"))

# Empty bank must not explode.
os.replace(os.path.join(make_fake_bank.SANDBOX, "database", "questions.db"), os.path.join(make_fake_bank.SANDBOX, "database", "full.db"))
sqlite3.connect(os.path.join(make_fake_bank.SANDBOX, "database", "questions.db")).close()
database.repair_question_bank_schema()
empty = ae.build_module("Math", 1, "baseline", rng=random.Random(4))
check("empty bank returns empty module without raising", empty.size == 0)
ok_flag, msg = database.question_bank_is_usable()
check("empty bank reports unusable with a message", not ok_flag and "empty" in msg.lower(), msg)
database.reset_pool()
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

# ------------------------------------------------- 11. NUMBERED PRACTICE TESTS
print("\n[11] numbered practice tests")
import practice_tests as pt  # noqa: E402
import web_api  # noqa: E402
attempt_repo.clear_history(keep_notes=False)
pt.delete_all()
built = pt.build_more()
check("at least one practice test builds from the sandbox bank", built["built"] >= 1, str(built))
check("it says why it stopped", bool(built["stoppedBecause"]), str(built))
tests = pt.list_tests()
check("the list matches what was built", len(tests) == built["total"], f"{len(tests)} vs {built}")
t1 = pt.modules_for(1)
check("test 1 keeps all six forms",
      len(t1) == 6 and {k[2] for k in t1} == {"baseline", "easy", "hard"}, str(sorted(t1)))
check("test 1 R&W Module 1 is a full module",
      len(t1[("Reading and Writing", 1, "baseline")]) == 27)
check("test 1 Math Module 1 is a full module", len(t1[("Math", 1, "baseline")]) == 22)
flat = [q.question_id for qs in t1.values() for q in qs]
check("no question appears twice inside a test", len(flat) == len(set(flat)))
m1 = t1[("Reading and Writing", 1, "baseline")]
check("test 1 opens with vocabulary, like Bluebook",
      ae.skill_key(m1[0].skill) == ae.skill_key("Words in Context"), m1[0].skill)
check("test 1 R&W closes with Rhetorical Synthesis",
      ae.skill_key(m1[-1].skill) == ae.skill_key("Rhetorical Synthesis"), m1[-1].skill)

# Stored means stored: building again adds nothing new and changes nothing old.
before = [q.question_id for q in m1]
again = pt.build_more()
check("building again does not rebuild what exists",
      [q.question_id for q in pt.modules_for(1)[("Reading and Writing", 1, "baseline")]] == before)
check("building again on an exhausted bank adds nothing", again["built"] == 0, str(again))
if built["total"] >= 2:
    a = {q.question_id for qs in pt.modules_for(1).values() for q in qs}
    b = {q.question_id for qs in pt.modules_for(2).values() for q in qs}
    check("two numbered tests share no questions", not (a & b), f"{len(a & b)} shared")

# Sit Practice Test 1 through the API, the way the browser does.
api = web_api.Api()


def _answers(plan, right: bool) -> dict:
    return {str(i): (q.correct_answer if right else "Z") for i, q in enumerate(plan.questions)}


step = api.start_practice_test(number=1, sections=["Reading and Writing"])
check("a numbered test starts", step.get("step") == "module", str(step)[:200])
served = [q.question_id for q in api.current_plan.questions]
check("Module 1 is the stored Module 1, in stored order", served == before)
step = api.submit({"answers": _answers(api.current_plan, True), "elapsed": 60})
check("acing Module 1 routes to a break", step.get("step") == "break", str(step)[:200])
step = api.resume()
hard_ids = [q.question_id for q in t1[("Reading and Writing", 2, "hard")]]
check("acing Module 1 serves the stored HARD Module 2",
      [q.question_id for q in api.current_plan.questions] == hard_ids)
step = api.submit({"answers": _answers(api.current_plan, True), "elapsed": 60})
check("the sitting completes", step.get("step") == "done", str(step)[:200])

api.start_practice_test(number=1, sections=["Reading and Writing"])
api.submit({"answers": _answers(api.current_plan, False), "elapsed": 60})
api.resume()
easy_ids = [q.question_id for q in t1[("Reading and Writing", 2, "easy")]]
check("bombing Module 1 serves the stored EASY Module 2",
      [q.question_id for q in api.current_plan.questions] == easy_ids)
api.abandon()
listed = next(t for t in pt.list_tests() if t["number"] == 1)
check("the list knows Practice Test 1 was taken", len(listed["sittings"]) == 2,
      str(listed["sittings"]))
check("and records a best score from the completed one", bool(listed["best"]), str(listed))
check("and counts the questions already seen", listed["seen"] > 0, str(listed["seen"]))
check("an unknown test number is refused, not crashed",
      "error" in api.start_practice_test(number=999))

# ------------------------------------------------------------ 12. CHECK MODE
print("\n[12] check mode")
step = api.start_check(section="Reading and Writing", count=5)
check("check mode starts", step.get("step") == "module" and step.get("mode") == "check",
      str(step)[:200])
plan = api.current_plan
got = api.check_answer(0, plan.questions[0].correct_answer)
check("a right answer checks as right", got.get("correct") is True, str(got))
check("the key comes back with it", got.get("correctAnswer") == plan.questions[0].correct_answer)
check("a wrong answer checks as wrong", api.check_answer(1, "Z").get("correct") is False)
check("a blank never counts as right", api.check_answer(2, "").get("correct") is False)
check("an index off the end is refused", "error" in api.check_answer(99, "A"))
done = api.submit({"answers": _answers(plan, True), "elapsed": 30})
check("check mode submits like a drill", done.get("step") == "done", str(done)[:200])
check("and is filed under its own name",
      attempt_repo.list_sessions(limit=1)[0]["label"].startswith("Check mode"),
      attempt_repo.list_sessions(limit=1)[0]["label"])
api.start_practice_test(number=1)
check("a TEST will not give its answers away one at a time",
      "error" in api.check_answer(0, "A"), str(api.check_answer(0, "A")))
api.abandon()
pt.delete_all()
attempt_repo.clear_history(keep_notes=False)

# ----------------------------------------------------- 13. SKILL RECOVERY
# These headers are copied from a real College Board export, including the two
# ways a wrapped table cell comes out of a PDF. The importer read every one of
# them as a blank skill.
print("\n[13] reading question types out of the PDF header")
import skill_tags  # noqa: E402
H = skill_tags.skill_from_header
check("a plain header",
      H(" Assessment Test Domain Skill Difficulty SAT Reading and Writing Information and Ideas Inferences Hard",
        "Information and Ideas", "Reading and Writing") == "Inferences")
check("a wrapped skill, continuation after the difficulty",
      H(" Assessment Test Domain Skill Difficulty SAT Reading and Writing Craft and Structure Text Structure and Hard Purpose",
        "Craft and Structure", "Reading and Writing") == "Text Structure and Purpose")
check("a wrapped skill, continuation before the difficulty (PyMuPDF order)",
      H(" Assessment Test Domain Skill Difficulty SAT Reading and Writing Craft and Structure Text Structure and Purpose Hard",
        "Craft and Structure", "Reading and Writing") == "Text Structure and Purpose")
check("a wrapped domain AND a wrapped skill",
      H(" Assessment Test Domain Skill Difficulty SAT Reading and Writing Standard English Form, Structure, and Easy Conventions Sense",
        "Standard English Conventions", "Reading and Writing") == "Form, Structure, and Sense")
check("the domain's own words are not mistaken for the skill's",
      H(" Assessment Test Domain Skill Difficulty SAT Reading and Writing Craft and Structure Words in Context Easy",
        "Craft and Structure", "Reading and Writing") == "Words in Context")
check("the longest matching official name wins",
      H(" Assessment Test Domain Skill Difficulty SAT Math Algebra Systems of two linear equations in two variables Medium",
        "Algebra", "Math") == "Systems of two linear equations in two variables")
check("a long Math name split across lines",
      H(" Assessment Test Domain Skill Difficulty SAT Math Problem-Solving and One-variable data: Distributions Hard Data Analysis and measures of center and spread",
        "Problem-Solving and Data Analysis", "Math")
      == "One-variable data: Distributions and measures of center and spread")
check("capitalisation drift is still recognised",
      H(" Assessment Test Domain Skill Difficulty SAT Reading and Writing Craft and Structure Cross-text Connections Medium",
        "Craft and Structure", "Reading and Writing") == "Cross-Text Connections")
check("a question the importer misfiled is put back in its real domain",
      skill_tags.classify(
          " Assessment Test Domain Skill Difficulty SAT Math Problem-Solving and Percentages Medium Data Analysis",
          "Algebra", "Math") == ("Problem-Solving and Data Analysis", "Percentages"))
check("headers are found in page text",
      skill_tags.headers_in_text("Question ID: 8a714fa1\nAssessment\nTest\nDomain\nSkill\nDifficulty\nSAT\nMath\n"
                                 "Algebra\nLinear functions\nEasy\nQuestion\nA line in the xy-plane...")
      == [("8a714fa1", " Assessment Test Domain Skill Difficulty SAT Math Algebra Linear functions Easy")])
check("blank, General and Unclassified all count as unknown",
      skill_tags.is_blank("") and skill_tags.is_blank("General") and skill_tags.is_blank(None)
      and not skill_tags.is_blank("Transitions"))

# The same thing end to end through PyMuPDF, the reader the app ships with.
# Skipped where PyMuPDF is not installed; CI installs it on all three systems.
try:
    import fitz  # noqa: F401
except ImportError:
    fitz = None
if fitz is None:
    print("  --  PyMuPDF not installed here, skipping the PDF round trip")
else:
    _db = os.path.join(make_fake_bank.SANDBOX, "database", "questions.db")
    shutil.copy2(_db, _db + ".bak")
    rows = [("a1b2c3d4", "Reading and Writing", "Craft and Structure", "Text Structure and", "Purpose", "Hard"),
            ("0f0f0f0f", "Math", "Algebra", "Linear functions", "", "Easy"),
            ("8a714fa1", "Math", "Problem-Solving and", "Percentages", "Data Analysis", "Medium")]
    _c = sqlite3.connect(_db)
    for qid, sec, dom, *_ in rows:
        stored_domain = "Algebra" if qid == "8a714fa1" else ("Craft and Structure" if sec != "Math" else "Algebra")
        _c.execute("INSERT OR REPLACE INTO questions (question_id, section, domain, skill, "
                   "difficulty, question_img, correct_answer, rationale, is_open_ended) "
                   "VALUES (?,?,?,?,?,?,?,?,?)",
                   (qid, sec, stored_domain, "", rows[[r[0] for r in rows].index(qid)][5],
                    "images/none.png", "A", "none", 0))
    _c.commit(); _c.close()
    pdf_path = os.path.join(make_fake_bank.SANDBOX, "export-test.pdf")
    doc = fitz.open()
    # One insert per printed row, top to bottom, so the text order PyMuPDF
    # reports cannot depend on how it groups separate words on a line. The
    # wrapped second line of a cell is its own row, as on the real export.
    for qid, sec, dom, skill1, cont, diff in rows:
        page = doc.new_page()
        page.insert_text((40, 60), f"Question ID: {qid}", fontsize=11)
        page.insert_text((40, 90), "Assessment Test Domain Skill Difficulty", fontsize=9)
        page.insert_text((40, 108), f"SAT {sec} {dom} {skill1} {diff}", fontsize=9)
        if cont:
            page.insert_text((40, 120), cont, fontsize=9)
        page.insert_text((40, 150), "Question", fontsize=11)
        page.insert_text((40, 175), "Passage text about text, structure and purpose.", fontsize=10)
    doc.save(pdf_path); doc.close()
    database.reset_pool()
    import pathlib
    got = skill_tags.recover(pdfs=[pathlib.Path(pdf_path)])
    _c = sqlite3.connect(_db)
    after = {r[0]: (r[1], r[2]) for r in _c.execute(
        "SELECT question_id, domain, skill FROM questions WHERE question_id IN ('a1b2c3d4','0f0f0f0f','8a714fa1')")}
    _c.close()
    check("PyMuPDF round trip fills the blanks", got.get("filled", 0) >= 3, str(got))
    check("a wrapped skill read through PyMuPDF",
          after.get("a1b2c3d4", ("", ""))[1] == "Text Structure and Purpose", str(after))
    check("a plain Math skill read through PyMuPDF",
          after.get("0f0f0f0f", ("", ""))[1] == "Linear functions", str(after))
    check("the misfiled question is moved to its real domain",
          after.get("8a714fa1") == ("Problem-Solving and Data Analysis", "Percentages"), str(after))
    again = skill_tags.recover(pdfs=[pathlib.Path(pdf_path)])
    check("a skill already recorded is never overwritten", again.get("filled", 1) == 0, str(again))
    database.reset_pool()
    os.replace(_db + ".bak", _db)
    os.remove(pdf_path)
    database.reset_pool()

# ------------------------------------------ 14. A BANK WITH NO SKILLS AT ALL
# Exactly the state of a bank imported before any of this, on a machine whose
# PDFs have been deleted. It must still get practice tests, shaped by domain and
# difficulty, not zero tests and a wall of "skill gaps".
print("\n[14] a bank with every skill blank")
_db = os.path.join(make_fake_bank.SANDBOX, "database", "questions.db")
shutil.copy2(_db, _db + ".bak")
_c = sqlite3.connect(_db); _c.execute("UPDATE questions SET skill = ''"); _c.commit(); _c.close()
database.reset_pool()
blind = ae.build_module("Reading and Writing", 1, "baseline", rng=random.Random(9))
check("a skill-blind bank still makes a full module", blind.size == 27, str(blind.size))
check("and reports no skill gaps it cannot know about", not blind.skill_gaps, str(blind.skill_gaps))
check("the domain quota still holds",
      dict(Counter(q.domain for q in blind.questions)) == config.BLUEPRINT["Reading and Writing"]["domain_quota"])
blind_order = [config.BLUEPRINT["Reading and Writing"]["domain_order"].index(q.domain) for q in blind.questions]
check("and domains stay in Bluebook order", blind_order == sorted(blind_order), str(blind_order))
pt.delete_all()
built_blind = pt.build_more()
check("practice tests still build from a skill-blind bank", built_blind["built"] >= 1, str(built_blind))
pt.delete_all()
database.reset_pool()
os.replace(_db + ".bak", _db)
database.reset_pool()

# ------------------------------------------ 15. PAST ANSWERS GET THEIR TYPE
# Answers copy the question's skill when they are saved, so everything answered
# before the types were read out of the PDFs sat under "General", and the
# Skills panel was one giant General row.
print("\n[15] past answers are filed under their question type")
_qdb = os.path.join(make_fake_bank.SANDBOX, "database", "questions.db")
_c = sqlite3.connect(_qdb)
_typed = _c.execute("SELECT question_id, domain, skill FROM questions WHERE TRIM(COALESCE(skill, '')) "
                    "NOT IN ('', 'General', 'Unclassified') ORDER BY question_id LIMIT 5").fetchall()
_blank_q = _typed[4][0]
_c.execute("UPDATE questions SET skill = '' WHERE question_id = ?", (_blank_q,))
_c.commit(); _c.close()
database.reset_pool()
_p = sqlite3.connect(str(config.PROGRESS_DB))
_ins = ("INSERT INTO attempts (question_id, section, domain, skill, difficulty, is_correct) "
        "VALUES (?, 'Reading and Writing', ?, ?, 'Medium', 1)")
_ids = {}
for tag, qid, dom, skill in (("general", _typed[0][0], "Wrong Domain", "General"),
                             ("general2", _typed[0][0], "Wrong Domain", "General"),
                             ("empty", _typed[1][0], _typed[1][1], ""),
                             ("null", _typed[2][0], _typed[2][1], None),
                             ("kept", _typed[3][0], _typed[3][1], "Already Recorded"),
                             ("unknowable", _blank_q, _typed[4][1], "General"),
                             ("gone", "deadbeef", "Algebra", "General")):
    _ids[tag] = _p.execute(_ins, (qid, dom, skill)).lastrowid
_p.execute("INSERT INTO redo_queue (question_id, section, domain, skill, stage, due_on) "
           "VALUES (?, 'Reading and Writing', 'Wrong Domain', 'General', 0, '2099-01-01')", (_typed[0][0],))
_p.commit()
try:
    _filed = skill_tags.sync_attempt_skills()
    _after = {tag: _p.execute("SELECT domain, skill FROM attempts WHERE attempt_id = ?",
                              (aid,)).fetchone() for tag, aid in _ids.items()}
    check("every blank answer whose question is known gets filed", _filed == 4, str(_filed))
    check("'General' takes the question's skill AND domain",
          _after["general"] == (_typed[0][1], _typed[0][2]) == _after["general2"], str(_after["general"]))
    check("an empty skill is filled", _after["empty"][1] == _typed[1][2], str(_after["empty"]))
    check("a NULL skill is filled", _after["null"][1] == _typed[2][2], str(_after["null"]))
    check("a skill already recorded is never overwritten",
          _after["kept"][1] == "Already Recorded", str(_after["kept"]))
    check("a question with no known type leaves its answers alone",
          _after["unknowable"][1] == "General" and _after["gone"][1] == "General",
          str((_after["unknowable"], _after["gone"])))
    _redo = _p.execute("SELECT domain, skill FROM redo_queue WHERE question_id = ? "
                       "AND due_on = '2099-01-01'", (_typed[0][0],)).fetchone()
    check("the redo queue is kept in step", _redo == (_typed[0][1], _typed[0][2]), str(_redo))
    check("a second pass has nothing left to do", skill_tags.sync_attempt_skills() == 0)
    database.reset_pool()
    _buckets = {b["bucket"] for b in attempt_repo.breakdown("skill")}
    check("the Skills panel shows the real type", _typed[0][2] in _buckets, str(sorted(_buckets)[:6]))
finally:
    _p.execute(f"DELETE FROM attempts WHERE attempt_id IN ({','.join('?' * len(_ids))})",
               tuple(_ids.values()))
    _p.execute("DELETE FROM redo_queue WHERE due_on = '2099-01-01'")
    _p.commit(); _p.close()
    _c = sqlite3.connect(_qdb)
    _c.execute("UPDATE questions SET skill = ? WHERE question_id = ?", (_typed[4][2], _blank_q))
    _c.commit(); _c.close()
    database.reset_pool()

# ------------------------------------------ 16. LEAVE A SITTING, COME BACK LATER
# Clicking away used to abandon the sitting, and "Save and exit" on the break
# screen abandoned it too, so a test could never be finished another day.
print("\n[16] pause a sitting and pick it up again, even after a restart")
import time as _time  # noqa: E402
import test_flow  # noqa: E402
attempt_repo.clear_history(keep_notes=False)
pt.delete_all()
pt.build_more()


def _restart():
    """What closing and reopening the app does to the sitting in memory."""
    database.close_all_pools()
    attempt_repo.abandon_stale_sessions()
    return web_api.Api()


api = web_api.Api()
step = api.start_practice_test(number=1, sections=["Reading and Writing"])
mid = step.get("moduleId")
check("a module says which module it is", isinstance(mid, int), str(step.get("moduleId")))
keys = [q.correct_answer for q in api.current_plan.questions]
client = {"index": 6, "answers": {str(i): keys[i] for i in range(6)}, "flagged": [2],
          "shaky": [3], "eliminated": {"1": ["C"]}, "times": {"0": 41000}, "remaining": 1234,
          "elapsed": 686, "checked": {}}
check("answers so far are autosaved", api.autosave({"moduleId": mid, "state": client}).get("ok"))
check("an autosave for another module is ignored",
      not api.autosave({"moduleId": mid + 999, "state": {"index": 0}}).get("ok"))
paused = api.pause({"moduleId": mid, "state": client})
check("leaving pauses instead of abandoning", paused.get("ok") and
      attempt_repo.get_session(api.runner.session_id)["status"] == "paused",
      str(paused)[:200])
info = api.bootstrap()["paused"]
check("the app knows what to offer back",
      info and info["phase"] == "module" and info["remaining"] == 1234
      and "Question 7 of 27" in info["progress"] and "6 answered" in info["progress"], str(info))
sid = api.runner.session_id

api = _restart()
check("a restart keeps it paused, not abandoned",
      attempt_repo.get_session(sid)["status"] == "paused")
check("and still offers it", (api.bootstrap()["paused"] or {}).get("sessionId") == sid)
back = api.resume_paused()
check("resuming hands back the same module", back.get("step") == "module"
      and back.get("moduleId") == mid
      and [q["id"] for q in back["module"]["questions"]] == before, str(back)[:200])
check("with every answer, flag and the clock where they were",
      back.get("restore") == client, str(back.get("restore"))[:200])
check("and it is in progress again", attempt_repo.get_session(sid)["status"] == "in_progress")
step = api.submit({"moduleId": mid, "answers": {str(i): k for i, k in enumerate(keys)}, "elapsed": 900})
check("the resumed module hands in and routes", step.get("step") == "break", str(step)[:200])
again = api.submit({"moduleId": mid, "answers": {}, "elapsed": 1})
check("handing the same module in twice changes nothing", again == step)
check("and records its answers once",
      len(attempt_repo.get_session_attempts(sid)) == 27, str(len(attempt_repo.get_session_attempts(sid))))

# Save and exit on the break screen, then come back another day.
api.pause({})
api = _restart()
info = api.bootstrap()["paused"]
check("a sitting left at the break is offered back at the break",
      info and info["phase"] == "break", str(info))
back = api.resume_paused()
check("resuming it lands on the break screen", back.get("step") == "break", str(back)[:200])
first = api.resume()
second = api.resume()
check("a double-clicked Continue does not end the test",
      first.get("step") == "module" and second.get("step") == "module"
      and first.get("moduleId") == second.get("moduleId"), f"{first.get('step')} {second.get('step')}")
check("the stored HARD Module 2 comes back after a restart",
      [q["id"] for q in first["module"]["questions"]] == hard_ids)
done = api.submit({"moduleId": first["moduleId"],
                   "answers": {str(i): q.correct_answer for i, q in enumerate(api.current_plan.questions)},
                   "elapsed": 60})
check("and the sitting finishes with both modules", done.get("step") == "done"
      and done["summary"]["total"] == 54, str(done)[:200])
check("the snapshot is gone once it is finished", attempt_repo.load_snapshot(sid) is None)
row = attempt_repo.get_session(sid)
check("the time away is not counted as time spent", row["duration_seconds"] < 120,
      str(row["duration_seconds"]))
check("nothing is left to resume", api.bootstrap()["paused"] is None)

# A crash: no pause call at all, just an autosave and then the app is gone.
step = api.start_drill(section="Math", domains=[], count=6)
did = step["moduleId"]
api.autosave({"moduleId": did, "state": {"index": 3, "answers": {"0": "A"}}})
dsid = api.runner.session_id
api = _restart()
check("a sitting open when the app died comes back as paused",
      attempt_repo.get_session(dsid)["status"] == "paused")
back = api.resume_paused()
check("with its autosaved answers", (back.get("restore") or {}).get("answers") == {"0": "A"},
      str(back)[:200])

# Starting something new ends the paused one; its answers so far stay put.
api.pause({"moduleId": did, "state": {"index": 3, "answers": {"0": "A"}}})
new = api.start_check(section="Reading and Writing", count=4)
check("starting a new sitting ends the paused one",
      attempt_repo.get_session(dsid)["status"] == "abandoned"
      and attempt_repo.load_snapshot(dsid) is None, str(attempt_repo.get_session(dsid)["status"]))
check("and the new one is the one on offer if you leave it",
      (api.pause({"moduleId": new["moduleId"], "state": {"index": 1}}).get("paused") or {})
      .get("sessionId") == api.runner.session_id)
check("End it closes the paused sitting", api.discard_paused().get("ok")
      and api.bootstrap()["paused"] is None)

# Check mode survives a restart and still checks.
step = api.start_check(section="Math", count=4)
api.pause({"moduleId": step["moduleId"], "state": {"index": 2, "checked": {"0": {"correct": True}}}})
api = _restart()
back = api.resume_paused()
check("check mode comes back as check mode", back.get("mode") == "check", str(back)[:120])
check("and still checks answers", "correct" in api.check_answer(1, "A"))
api.abandon()

# The runner round-trips through its own snapshot.
runner = test_flow.TestRunner(test_flow.MODE_SECTION, sections=["Math"], timed=True)
runner.start()
snap = runner.snapshot()
import json as _json  # noqa: E402
twin = test_flow.TestRunner.restore(_json.loads(_json.dumps(snap)))
check("a snapshot survives JSON and rebuilds the same module",
      [q.question_id for q in twin.current_plan.questions]
      == [q.question_id for q in runner.current_plan.questions]
      and twin.current_module_row_id == runner.current_module_row_id and twin.module_open)
runner.abandon()

# Deleting the paused sitting from History leaves nothing to resume.
api = web_api.Api()
step = api.start_drill(section="Math", domains=[], count=3)
api.pause({"moduleId": step["moduleId"], "state": {"index": 1}})
api.delete_session(api.runner.session_id)
check("deleting a paused sitting takes its Resume with it",
      api.bootstrap()["paused"] is None and "error" in api.resume_paused())

# Paused yesterday, back today: the day in between is not time spent.
runner = test_flow.TestRunner(test_flow.MODE_SECTION, sections=["Reading and Writing"], timed=True)
runner.start()
runner.pause_clock()
snap = _json.loads(_json.dumps(runner.snapshot()))
snap["started_at"] -= 86_400
snap["paused_at"] -= 86_400 - 120                     # sat for 2 minutes, then left
snap["saved_epoch"] -= 86_400 - 120
back = test_flow.TestRunner.restore(snap)
back.resume_clock()
check("a day away between pause and resume is not counted",
      100 <= back.active_seconds() <= 140, str(back.active_seconds()))
crashed = dict(snap, paused_at=None)                   # the app died instead of pausing
check("nor is the time after a crash's last save",
      100 <= test_flow.TestRunner.restore(crashed).active_seconds() <= 140)
back.abandon()
pt.delete_all()
attempt_repo.clear_history(keep_notes=False)

# ------------------------------------------ 18. SMALLER FIXES FROM THE REVIEW
print("\n[18] the smaller fixes")
from datetime import date as _date, timedelta as _td  # noqa: E402
import threading as _threading  # noqa: E402
attempt_repo.clear_history(keep_notes=False)
api = web_api.Api()

# A miss tagged long after it happened still schedules its redo.
step = api.start_drill(section="Math", domains=[], count=4)
api.submit({"moduleId": step["moduleId"], "answers": {}, "elapsed": 10})
old_miss = attempt_repo.get_session_attempts(attempt_repo.list_sessions(limit=1)[0]["session_id"])[0]
newer = 0
for _ in range(12):                                  # 300+ newer misses bury it
    if newer > 310:
        break
    s2 = api.start_drill(section="Reading and Writing", domains=[], count=80)
    newer += len(s2["module"]["questions"])
    api.submit({"moduleId": s2["moduleId"], "answers": {}, "elapsed": 10})
check("the old miss really is buried under 300 newer ones", newer > 300, str(newer))
api.tag(old_miss["attempt_id"], root_cause="C")
tagged = api.tag(old_miss["attempt_id"], fix_note="Name the verb tense before reading the choices.")
check("tagging an old miss still schedules its redo", tagged.get("scheduled") is True, str(tagged))

# "10 of my Hard mistakes in one domain" comes back with 10 when there are 10.
missed = attempt_repo.question_ids_where(only_incorrect=True, section="Reading and Writing",
                                         difficulty="Hard", limit=500)
bank_now = question_repo.fetch_by_ids(missed)
by_domain = Counter(bank_now[q].domain for q in missed if q in bank_now)
if by_domain:
    wanted_domain, have = by_domain.most_common(1)[0]
    drill = api.start_drill(section="Reading and Writing", domains=[wanted_domain], count=min(have, 10),
                            difficulty="Hard", source="missed")
    got = len(drill["module"]["questions"]) if drill.get("module") else 0
    check("a mistakes drill filtered by domain and difficulty is not cut short",
          got == min(have, 10), f"{got} of {min(have, 10)} ({wanted_domain})")
    api.abandon()

# Redos whose question is gone no longer block the queue.
due_day = _date.today().isoformat()
for qid in ("gone0001", "gone0002", "gone0003"):
    attempt_repo.schedule_redo(qid, stage=0, due_on=due_day)
redo = api.start_redo(limit=2)
check("a redo session skips questions the bank no longer has",
      redo.get("step") == "module" and len(redo["module"]["questions"]) >= 1, str(redo)[:160])
check("and closes those dead entries",
      not [r for r in attempt_repo.due_redos(due_day, limit=500) if r["question_id"].startswith("gone")])
api.abandon()

# A streak longer than 30 days reads as its real length, and future ticks do not shorten it.
attempt_repo.clear_history(keep_notes=False)
for back in range(45):
    attempt_repo.set_plan_task((_date.today() - _td(days=back)).isoformat(), "t0", True)
for ahead in range(1, 11):
    attempt_repo.set_plan_task((_date.today() + _td(days=ahead)).isoformat(), "t0", True)
check("a 45-day streak shows 45", attempt_repo.plan_streak(_date.today().isoformat()) == 45,
      str(attempt_repo.plan_streak(_date.today().isoformat())))

# A reopened review quotes the threshold the sitting was routed with.
step = api.start_test(mode="section", sections=["Math"], threshold=0.9)
keys = {str(i): q.correct_answer for i, q in enumerate(api.current_plan.questions) if i % 5}
api.submit({"moduleId": step["moduleId"], "answers": keys, "elapsed": 60})
sid = api.runner.session_id
api.abandon()
note = test_flow.summary_from_history(sid).sections[0].routing_note
check("a reopened review explains routing against its own threshold", "90%" in note, note)

# Two builds at once make distinct tests instead of crashing on the key.
pt.delete_all()
results, errors = [], []


def _build():
    try:
        results.append(pt.build_more(max_new=1))
    except Exception as exc:                          # noqa: BLE001
        errors.append(repr(exc))


threads = [_threading.Thread(target=_build) for _ in range(2)]
[t.start() for t in threads]
[t.join() for t in threads]
check("two practice-test builds at once do not crash", not errors, str(errors))
stored_now = sorted(pt.stored_numbers())
check("and number what they built 1, 2, ... with no clash",
      stored_now == list(range(1, len(stored_now) + 1)) and len(stored_now) == sum(r["built"] for r in results),
      f"{stored_now} from {results}")
pt.delete_all()
attempt_repo.clear_history(keep_notes=False)

# ------------------------------------------ 17. THE SERVER DOES NOT LEAK
# One thread per browser connection, and each opened its own database
# connections that were never closed: ~5 MB and four file handles per
# connection, for as long as the app stayed open.
print("\n[17] browser connections do not leak database connections")
import http.client  # noqa: E402
import server as _server  # noqa: E402
_srv, _url = _server.create_server()
_server.serve_forever_in_thread(_srv)
_host, _port = _url.split("//")[1].rstrip("/").split(":")


def _hit(n):
    for _ in range(n):
        conn = http.client.HTTPConnection(_host, int(_port), timeout=10)
        conn.request("GET", "/api/bootstrap")
        conn.getresponse().read()
        conn.close()


try:
    _hit(3)
    _time.sleep(0.4)
    _pools = len(database._ALL_POOLS)
    _fds = len(os.listdir("/proc/self/fd")) if os.path.isdir("/proc/self/fd") else None
    _hit(40)
    _time.sleep(0.8)
    check("40 connections leave no pools behind", len(database._ALL_POOLS) <= _pools + 1,
          f"{_pools} -> {len(database._ALL_POOLS)}")
    if _fds is not None:
        _after = len(os.listdir("/proc/self/fd"))
        check("and no open files behind", _after <= _fds + 4, f"{_fds} -> {_after}")
    def _boom(*_a, **_k):
        raise RuntimeError("boom")
    _srv.api.set_plan_task = _boom
    conn = http.client.HTTPConnection(_host, int(_port), timeout=5)
    _t0 = _time.time()
    try:
        conn.request("POST", "/api/plan/task", body="{\"day\": \"2026-01-01\"}",
                     headers={"Content-Type": "application/json"})
        _resp = conn.getresponse()
        _resp.read()
        _status = _resp.status
    except OSError as _exc:                  # a timeout is exactly the bug
        _status = repr(_exc)
    check("a POST that fails answers 500 at once instead of hanging",
          _status == 500 and _time.time() - _t0 < 3, f"{_status} after {_time.time() - _t0:.1f}s")
    conn.close()
finally:
    _srv.shutdown()
    _srv.server_close()

print(f"\n{'=' * 60}\nPASSED {len(PASS)}   FAILED {len(FAIL)}")
if FAIL:
    for f in FAIL:
        print("   FAILED:", f)
    sys.exit(1)
print("all green")
