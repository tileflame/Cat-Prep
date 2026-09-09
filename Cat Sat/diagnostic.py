"""
diagnostic.py — the engine of the plan (Section 07).

"Practice questions don't raise scores; diagnosed practice questions do."

Two axes per logged question:
  WHAT  the question type — Cat SAT already knows this from the bank's
        domain/skill columns, so it is filled in automatically.
  WHY   the root cause — only you can supply this, so the review screen asks.

Plus the two things the plan cares about most and no app does:
  * lucky guesses are logged as misses ("your real error count is closer to 20
    than 12, and those lucky ones are next month's misses")
  * every miss is scheduled for a cold redo, then +3 days, then +10 days
"""

from __future__ import annotations

from datetime import date, timedelta

# ---------------------------------------------------------------------------
# AXIS 2 — ROOT CAUSES
# ---------------------------------------------------------------------------

ROOT_CAUSES = [
    ("K", "Knowledge gap", "Didn't know the rule or concept", "content"),
    ("M", "Misread", "Answered a different question", "process"),
    ("C", "Careless slip", "Knew it, fumbled the execution", "process"),
    ("T", "Time pressure", "Rushed or guessed because of the clock", "pacing"),
    ("V", "Vocabulary", "Didn't know the word", "method"),
    ("P", "Passage", "Didn't understand what it was saying", "method"),
    ("X", "Trap", "Chose a tempting engineered distractor", "method"),
    ("D", "Desmos", "Should have used it, or used it wrong", "process"),
    ("A", "Algebra execution", "Arithmetic or algebra slip", "process"),
    ("G", "Grammar rule", "Rule not known", "content"),
    ("L", "Logic", "The reasoning chain broke", "method"),
]

CAUSE_CODES = [c[0] for c in ROOT_CAUSES]
CAUSE_LABEL = {c[0]: c[1] for c in ROOT_CAUSES}
CAUSE_DETAIL = {c[0]: c[2] for c in ROOT_CAUSES}
CAUSE_FAMILY = {c[0]: c[3] for c in ROOT_CAUSES}

# Confidence states recorded during the quiz.
CONF_SURE = "sure"        # ≥90% confident
CONF_SHAKY = "shaky"      # below 90% — logged even when correct

# ---------------------------------------------------------------------------
# THE DIAGNOSIS TABLE (Section 07, "Turning the log into next week's plan")
# ---------------------------------------------------------------------------

def diagnose(cause_counts: dict, total_logged: int, lucky_by_type: dict | None = None) -> list[dict]:
    """
    Read the prescription off the dominant root cause.

    Returns a list of findings, most important first. Each has a verdict, the
    prescription, and — just as important — what NOT to do instead.
    """
    if not total_logged:
        return []

    def share(*codes):
        return sum(cause_counts.get(c, 0) for c in codes) / total_logged

    findings = []

    process = share("C", "A", "D", "M")
    if process > 0.40:
        findings.append({
            "trigger": f"C + A + D + M = {process * 100:.0f}% of logged questions",
            "verdict": "Process, not knowledge",
            "do": "Run the four-step answer protocol religiously. Underline the ask, "
                  "re-read the last line before submitting.",
            "dont": "Do NOT study new content — there is nothing wrong with your content.",
            "severity": 3,
        })

    method = share("X", "P", "V")
    if method > 0.35:
        findings.append({
            "trigger": f"X + P + V = {method * 100:.0f}% of logged questions",
            "verdict": "Method — you're reading the choices before forming a prediction",
            "do": "Go back to predict-first and enforce it on every R&W question for a week. "
                  "Run the final-two protocol: name the word that kills the loser.",
            "dont": "Don't drill more questions of that type until predict-first is automatic.",
            "severity": 3,
        })

    pacing = share("T")
    if pacing > 0.20:
        findings.append({
            "trigger": f"T = {pacing * 100:.0f}% of logged questions",
            "verdict": "Pacing",
            "do": "Two-pass strategy. Hard 60-second cap on pass 1. Flag without hesitation.",
            "dont": "More content study will make this WORSE, not better.",
            "severity": 2,
        })

    content = share("K", "G")
    top_content = max(cause_counts.get("K", 0), cause_counts.get("G", 0))
    if content > 0.30 and top_content >= max(cause_counts.values(), default=0):
        findings.append({
            "trigger": f"K / G on top ({content * 100:.0f}% of logged questions)",
            "verdict": "Content",
            "do": "Targeted rule study on that exact skill, then 20 fresh questions of only "
                  "that type.",
            "dont": "Don't mix practice — you learn a rule by seeing it fifteen times in a row.",
            "severity": 2,
        })

    for question_type, count in (lucky_by_type or {}).items():
        if count >= 3:
            findings.append({
                "trigger": f"{count} lucky flags in {question_type}",
                "verdict": "Fragile knowledge",
                "do": f"Treat these exactly like misses. Drill {question_type} until you're "
                      "answering it without the shaky flag.",
                "dont": "Don't count these as correct — they're the questions that will flip "
                        "on a harder module.",
                "severity": 2,
            })

    findings.sort(key=lambda f: -f["severity"])
    return findings


# ---------------------------------------------------------------------------
# DRILL TARGETS (the two standing rules)
# ---------------------------------------------------------------------------

TARGET_MISS_THRESHOLD = 3      # 3+ misses in a week => becomes a target
MAX_ACTIVE_TARGETS = 3         # maximum three targets at a time
RETIRE_ACCURACY = 90.0         # can't retire below 90%
RETIRE_SAMPLE = 15             # ...on a fresh 15-question set


def suggest_targets(miss_counts_by_type: dict, active: list[str]) -> list[str]:
    """
    "Any question type with 3 or more misses in a week becomes a primary drill
    target for the following week. Maximum three targets at a time."
    """
    candidates = [t for t, n in sorted(miss_counts_by_type.items(), key=lambda kv: -kv[1])
                  if n >= TARGET_MISS_THRESHOLD and t not in active]
    room = max(0, MAX_ACTIVE_TARGETS - len(active))
    return candidates[:room]


def can_retire(accuracy: float, sample_size: int) -> tuple[bool, str]:
    """
    "You don't get to retire a target until you hit 90% on a fresh 15-question
    set of that type. Feeling better about it doesn't count."
    """
    if sample_size < RETIRE_SAMPLE:
        return False, (f"Need {RETIRE_SAMPLE} fresh questions to judge — you have "
                       f"{sample_size}.")
    if accuracy < RETIRE_ACCURACY:
        return False, f"{accuracy:.0f}% on the last {sample_size}. Needs {RETIRE_ACCURACY:.0f}%."
    return True, f"{accuracy:.0f}% on the last {sample_size} — retire it."


# ---------------------------------------------------------------------------
# REDO SCHEDULING (Pass 3)
# ---------------------------------------------------------------------------
# "Redo every missed question from scratch... Then schedule the same questions
#  for +3 days and +10 days."

REDO_STAGES = [
    (0, 1, "cold redo (next day)"),
    (1, 3, "+3 days"),
    (2, 10, "+10 days"),
]
STAGE_OFFSET = {stage: offset for stage, offset, _ in REDO_STAGES}
STAGE_LABEL = {stage: label for stage, _, label in REDO_STAGES}
FINAL_STAGE = max(STAGE_OFFSET)


def due_date_for(stage: int, from_day: date) -> date:
    """When a redo at `stage` should surface."""
    return from_day + timedelta(days=STAGE_OFFSET.get(stage, 1))


def next_stage(stage: int, was_correct: bool) -> int | None:
    """
    Advance the ladder on a correct redo; reset to the start on a miss, because
    "if you can't reproduce the solution, it isn't learned".
    """
    if not was_correct:
        return 0
    if stage >= FINAL_STAGE:
        return None            # graduated
    return stage + 1


def should_schedule(is_correct: bool, confidence: str) -> bool:
    """
    The rule almost everyone breaks: log every question you weren't at least
    90% confident on — even the ones you got right.
    """
    return (not is_correct) or confidence == CONF_SHAKY


# ---------------------------------------------------------------------------
# LEADING INDICATORS (Section 07, "How to tell whether you're actually improving")
# ---------------------------------------------------------------------------

INDICATORS = [
    ("target_accuracy", "Accuracy on your drill targets",
     "Measured on fresh sets. Your real leading indicator.", "up"),
    ("process_misses", "Misses from process causes (C + M)",
     "If these are falling, your process is working even if the score hasn't caught up.", "down"),
    ("lucky_count", "Lucky flags",
     "Should fall steadily. Fragile knowledge becoming solid.", "down"),
    ("total_wrong", "Total questions wrong",
     "Strips out adaptive scaling noise.", "down"),
]

LAGGING_NOTE = ("Do not judge progress by comparing single practice-test scores. "
                "Section-score noise is roughly ±30 points test to test. The score is a "
                "lagging indicator, and it lags by two to three weeks.")


def indicator_direction(current: float, previous: float, want: str) -> str:
    """'better' | 'worse' | 'flat' for the dashboard arrows."""
    if previous is None or current is None:
        return "flat"
    delta = current - previous
    if abs(delta) < 1e-9:
        return "flat"
    improving = delta > 0 if want == "up" else delta < 0
    return "better" if improving else "worse"
