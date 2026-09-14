"""
adaptive_engine.py — the brain of the practice test.

Three jobs:

1. build_module()   assemble a module that matches the real SAT blueprint as
                    closely as your imported bank allows, and say honestly how
                    close it got.
2. route()          decide whether Module 2 is the harder or easier form.
3. score_section()  turn raw correct counts into a routing-aware estimated
                    scaled score.

None of this touches the UI, so all of it is unit-testable without a display.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import question_repo
from config import (
    BLUEPRINT,
    DEBUG,
    DEFAULT_ROUTING_THRESHOLD,
    DIFFICULTY_MIX,
    SCORE_ANCHORS,
    SCORE_FLOOR,
    SCORE_MIN_RELIABLE_QUESTIONS,
    SCORE_ROUTE_CEILING,
    SCORE_TOP,
    SECTION_RW,
    TIER_BASELINE,
    TIER_EASY,
    TIER_HARD,
    normalize_section,
)
from models import Question

DIFFICULTY_ORDER = ["Easy", "Medium", "Hard"]


# ---------------------------------------------------------------------------
# RESULT TYPES
# ---------------------------------------------------------------------------

@dataclass
class ModulePlan:
    """A built module, ready to hand to the quiz screen."""

    section: str
    module_number: int
    tier: str
    questions: list[Question]
    time_limit_seconds: int
    # Diagnostics: how well the bank could satisfy the blueprint.
    target_count: int = 0
    domain_gaps: dict = field(default_factory=dict)
    difficulty_actual: dict = field(default_factory=dict)
    difficulty_target: dict = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.questions)

    @property
    def fidelity(self) -> float:
        """0-1: fraction of the blueprint the bank could actually fill."""
        return self.size / self.target_count if self.target_count else 0.0

    @property
    def is_short(self) -> bool:
        return self.size < self.target_count


@dataclass
class ModuleResult:
    """Outcome of a finished module."""

    section: str
    module_number: int
    tier: str
    total: int
    correct: int
    weighted_correct: float
    weighted_total: float
    time_used_seconds: int
    module_row_id: int | None = None

    @property
    def raw_accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def weighted_accuracy(self) -> float:
        return self.weighted_correct / self.weighted_total if self.weighted_total else 0.0


# ---------------------------------------------------------------------------
# 1. MODULE ASSEMBLY
# ---------------------------------------------------------------------------

def _difficulty_targets(total: int, tier: str) -> dict[str, int]:
    """
    Turn a percentage mix into whole question counts that sum exactly to total.

    Largest-remainder method, so 27 questions at 34/33/33 gives 9/9/9 rather
    than 9/8/8 plus a stray.
    """
    mix = DIFFICULTY_MIX[tier]
    exact = {d: total * mix[d] for d in DIFFICULTY_ORDER}
    counts = {d: int(math.floor(exact[d])) for d in DIFFICULTY_ORDER}
    remaining = total - sum(counts.values())
    # Hand out leftovers to whoever was closest to rounding up.
    for difficulty in sorted(DIFFICULTY_ORDER, key=lambda d: exact[d] - counts[d], reverse=True):
        if remaining <= 0:
            break
        counts[difficulty] += 1
        remaining -= 1
    return counts


def _split_targets_across_domains(
    domain_quota: dict[str, int], difficulty_targets: dict[str, int]
) -> dict[str, dict[str, int]]:
    """
    Spread the module-level difficulty targets over the domain quotas.

    The result has to satisfy *two* margins at once: every domain must total
    exactly its quota, and every difficulty must total exactly its target. A
    naive per-difficulty largest-remainder pass only satisfies the second one —
    it kept handing every leftover to the same domain, so Craft and Structure
    ended up with 9 of 27 questions instead of 7.

    So: floor everything, then match each domain's remaining deficit against
    each difficulty's remaining deficit, largest fractional remainder first.
    Both margins are respected because they sum to the same number.
    """
    total = sum(domain_quota.values()) or 1
    plan: dict[str, dict[str, int]] = {
        domain: {d: 0 for d in DIFFICULTY_ORDER} for domain in domain_quota
    }
    remainders: dict[tuple, float] = {}

    for domain, quota in domain_quota.items():
        for difficulty in DIFFICULTY_ORDER:
            exact = difficulty_targets.get(difficulty, 0) * quota / total
            floored = int(math.floor(exact))
            plan[domain][difficulty] = floored
            remainders[(domain, difficulty)] = exact - floored

    # How much each margin is still short.
    domain_deficit = {
        domain: quota - sum(plan[domain].values())
        for domain, quota in domain_quota.items()
    }
    difficulty_deficit = {
        difficulty: wanted - sum(plan[d][difficulty] for d in domain_quota)
        for difficulty, wanted in difficulty_targets.items()
    }

    # Hand out the remaining units to the cells that rounded down the most,
    # skipping any cell whose domain or difficulty is already satisfied.
    for (domain, difficulty), _ in sorted(remainders.items(),
                                          key=lambda item: item[1], reverse=True):
        while (domain_deficit.get(domain, 0) > 0
               and difficulty_deficit.get(difficulty, 0) > 0):
            plan[domain][difficulty] += 1
            domain_deficit[domain] -= 1
            difficulty_deficit[difficulty] -= 1

    # Safety net: if the two margins somehow disagree, top up the domains that
    # are still short from whichever difficulty still has room.
    for domain, short in domain_deficit.items():
        while short > 0:
            difficulty = max(difficulty_deficit, key=lambda d: difficulty_deficit[d]) \
                if difficulty_deficit else "Medium"
            plan[domain][difficulty] += 1
            difficulty_deficit[difficulty] = difficulty_deficit.get(difficulty, 0) - 1
            short -= 1

    return plan


def _take(pool: list[Question], n: int, used: set[str]) -> list[Question]:
    """Pull up to n unused questions off a pool, marking them used."""
    picked = []
    for question in pool:
        if len(picked) >= n:
            break
        if question.question_id in used:
            continue
        picked.append(question)
        used.add(question.question_id)
    return picked


def build_module(
    section: str,
    module_number: int,
    tier: str,
    *,
    exclude_ids: set[str] | None = None,
    seen_counts: dict[str, int] | None = None,
    rng: random.Random | None = None,
    size_override: int | None = None,
) -> ModulePlan:
    """
    Assemble one module.

    Strategy, in priority order:
      1. Hit the domain quota AND the difficulty mix for that domain.
      2. If a domain is short on a difficulty, borrow from adjacent difficulties
         within the same domain (Hard <-> Medium <-> Easy).
      3. If a domain is still short, backfill from any other domain in the
         section, still respecting the overall difficulty mix.
      4. Report whatever is still missing instead of silently shipping a short
         module.

    Fresh (never-seen) questions are always preferred, so repeated tests do not
    recycle the same items.
    """
    randomizer = rng or random.Random()
    blueprint = BLUEPRINT.get(section)
    if blueprint is None:
        # Unknown section (e.g. a custom import): fall back to a generic module.
        blueprint = {
            "questions_per_module": size_override or 20,
            "minutes_per_module": 25,
            "domain_quota": {},
            "domain_order": [],
            "grid_ins_last": False,
        }

    total = size_override or blueprint["questions_per_module"]
    time_limit = int(blueprint["minutes_per_module"] * 60 * (total / max(blueprint["questions_per_module"], 1)))

    used: set[str] = set(exclude_ids or set())
    freshness = seen_counts or {}

    domain_quota = dict(blueprint["domain_quota"])
    if not domain_quota:
        domain_quota = {"General": total}
    # Rescale quotas if the caller asked for a non-standard module size.
    if total != sum(domain_quota.values()):
        domain_quota = _rescale_quota(domain_quota, total)

    difficulty_targets = _difficulty_targets(total, tier)
    per_domain = _split_targets_across_domains(domain_quota, difficulty_targets)

    # One pooled fetch per domain, split by difficulty and ordered by freshness.
    pools: dict[str, dict[str, list[Question]]] = {}
    for domain in domain_quota:
        raw = question_repo.fetch_pool_by_difficulty(
            section=section, domain=domain, exclude_ids=used, rng=randomizer
        )
        pools[domain] = {
            difficulty: question_repo.prioritise_unseen(items, freshness, randomizer)
            for difficulty, items in raw.items()
        }

    selected: list[Question] = []
    gaps: dict[str, int] = {}

    # --- Pass 1 + 2: fill each domain, borrowing across difficulty if needed.
    for domain, quota in domain_quota.items():
        domain_picked: list[Question] = []
        for difficulty in DIFFICULTY_ORDER:
            want = per_domain.get(domain, {}).get(difficulty, 0)
            if want <= 0:
                continue
            got = _take(pools[domain].get(difficulty, []), want, used)
            domain_picked.extend(got)
            missing = want - len(got)
            if missing > 0:
                # Borrow from the nearest difficulties in the same domain.
                for neighbour in _neighbours(difficulty):
                    if missing <= 0:
                        break
                    borrowed = _take(pools[domain].get(neighbour, []), missing, used)
                    domain_picked.extend(borrowed)
                    missing -= len(borrowed)
        shortfall = quota - len(domain_picked)
        if shortfall > 0:
            gaps[domain] = shortfall
        selected.extend(domain_picked)

    # --- Pass 3: backfill from anywhere in the section.
    shortfall = total - len(selected)
    if shortfall > 0:
        spare = question_repo.fetch(section=section, exclude_ids=used,
                                    shuffle=True, rng=randomizer)
        spare = question_repo.prioritise_unseen(spare, freshness, randomizer)
        # Prefer difficulties we are still short on.
        actual = _count_by_difficulty(selected)
        spare.sort(key=lambda q: -(difficulty_targets.get(q.difficulty, 0)
                                   - actual.get(q.difficulty, 0)))
        selected.extend(_take(spare, shortfall, used))

    # --- Order the module the way Bluebook presents it.
    ordered = _order_module(selected, blueprint, randomizer)
    for index, question in enumerate(ordered, start=1):
        question.module_number = module_number
        question.tier = tier
        question.position = index

    plan = ModulePlan(
        section=section,
        module_number=module_number,
        tier=tier,
        questions=ordered,
        time_limit_seconds=time_limit,
        target_count=total,
        domain_gaps=gaps,
        difficulty_actual=_count_by_difficulty(ordered),
        difficulty_target=difficulty_targets,
    )

    if DEBUG:
        print(f"[blueprint] {section} M{module_number} ({tier}): "
              f"{plan.size}/{plan.target_count} "
              f"diff={plan.difficulty_actual} target={plan.difficulty_target} gaps={gaps}")
    return plan


def _rescale_quota(quota: dict[str, int], total: int) -> dict[str, int]:
    """Proportionally resize a domain quota to a different module size."""
    original = sum(quota.values()) or 1
    scaled = {d: int(math.floor(total * n / original)) for d, n in quota.items()}
    remaining = total - sum(scaled.values())
    for domain in sorted(quota, key=lambda d: quota[d], reverse=True):
        if remaining <= 0:
            break
        scaled[domain] += 1
        remaining -= 1
    return {d: n for d, n in scaled.items() if n > 0} or {next(iter(quota)): total}


def _neighbours(difficulty: str) -> list[str]:
    """Difficulties to borrow from, nearest first."""
    return {
        "Easy": ["Medium", "Hard"],
        "Medium": ["Easy", "Hard"],
        "Hard": ["Medium", "Easy"],
    }[difficulty]


def _count_by_difficulty(questions: list[Question]) -> dict[str, int]:
    counts = {d: 0 for d in DIFFICULTY_ORDER}
    for question in questions:
        counts[question.difficulty] = counts.get(question.difficulty, 0) + 1
    return counts


_DIFFICULTY_RANK = {"Easy": 0, "Medium": 1, "Hard": 2}


def _order_module(questions: list[Question], blueprint: dict,
                  rng: random.Random) -> list[Question]:
    """
    Present questions the way the real test does: grouped by domain in the
    published order, and easiest to hardest inside each group. Math grid-ins
    move to the end of the module, as they do in Bluebook.
    """
    domain_order = blueprint.get("domain_order") or []
    rank = {domain: index for index, domain in enumerate(domain_order)}

    def sort_key(question: Question):
        return (
            rank.get(question.domain, len(rank)),          # domain grouping
            _DIFFICULTY_RANK.get(question.difficulty, 1),  # easy -> hard
            rng.random(),                                   # stable-ish shuffle
        )

    ordered = sorted(questions, key=sort_key)

    if blueprint.get("grid_ins_last"):
        multiple_choice = [q for q in ordered if not q.is_open_ended]
        grid_ins = [q for q in ordered if q.is_open_ended]
        ordered = multiple_choice + grid_ins

    return ordered


# ---------------------------------------------------------------------------
# 2. ROUTING
# ---------------------------------------------------------------------------

def evaluate_module(section: str, module_number: int, tier: str,
                    records, time_used_seconds: int = 0) -> ModuleResult:
    """Turn a list of AttemptRecords into a ModuleResult."""
    total = len(records)
    correct = sum(1 for r in records if r.is_correct)
    weighted_correct = sum(r.question.weight for r in records if r.is_correct)
    weighted_total = sum(r.question.weight for r in records)
    return ModuleResult(
        section=section,
        module_number=module_number,
        tier=tier,
        total=total,
        correct=correct,
        weighted_correct=weighted_correct,
        weighted_total=weighted_total,
        time_used_seconds=time_used_seconds,
    )


def route(result: ModuleResult, *, threshold: float = DEFAULT_ROUTING_THRESHOLD,
          use_weighting: bool = True) -> str:
    """
    Decide the tier of Module 2.

    Weighted accuracy is the default because getting 18/27 by acing the Hard
    items is a different performance from getting 18/27 by acing the Easy ones.
    Set use_weighting=False for the plain percentage rule.
    """
    accuracy = result.weighted_accuracy if use_weighting else result.raw_accuracy
    return TIER_HARD if accuracy >= threshold else TIER_EASY


def routing_explanation(result: ModuleResult, tier: str,
                        threshold: float = DEFAULT_ROUTING_THRESHOLD) -> str:
    """One honest sentence for the review screen."""
    raw = result.raw_accuracy * 100
    weighted = result.weighted_accuracy * 100
    direction = "at or above" if tier == TIER_HARD else "below"
    target = "the harder Module 2" if tier == TIER_HARD else "the easier Module 2"
    return (
        f"You answered {result.correct}/{result.total} correctly ({raw:.0f}% raw, "
        f"{weighted:.0f}% difficulty-weighted). That is {direction} the "
        f"{threshold * 100:.0f}% routing line, so Module 2 was {target}."
    )


# ---------------------------------------------------------------------------
# 3. SCORING
# ---------------------------------------------------------------------------

def _anchor_curve(section: str) -> list[tuple[float, float]]:
    """
    The section's raw-to-scaled table, rewritten as (fraction correct, score)
    and sorted ascending so it can be interpolated.

    Fractions rather than raw counts, because this function is asked to score
    things that are not a full section: a single 27-question module, an
    8-question drill. A fraction is the only thing those share with a real
    sitting. Built once per section and cached — the table never changes.
    """
    key = normalize_section(section)
    cached = _CURVE_CACHE.get(key)
    if cached is not None:
        return cached
    table = SCORE_ANCHORS.get(key) or SCORE_ANCHORS[SECTION_RW]
    reference = table["reference_total"] or 1
    curve = sorted((raw / reference, float(scaled)) for raw, scaled in table["points"])
    _CURVE_CACHE[key] = curve
    return curve


_CURVE_CACHE: dict[str, list[tuple[float, float]]] = {}


def _interpolate(curve: list[tuple[float, float]], pct: float) -> float:
    """Linear interpolation between the two anchors that bracket pct."""
    pct = max(0.0, min(1.0, pct))
    low = curve[0]
    if pct <= low[0]:
        return low[1]
    for high in curve[1:]:
        if pct <= high[0]:
            span = high[0] - low[0]
            if span <= 0:                       # duplicate anchor; take the higher
                return high[1]
            t = (pct - low[0]) / span
            return low[1] + (high[1] - low[1]) * t
        low = high
    return curve[-1][1]


def estimate_section_score(correct: int, total: int, final_tier: str,
                           section: str = SECTION_RW) -> int:
    """
    Routing-aware estimated section score, rounded to the nearest 10.

    Two things decide the number. The section's conversion table turns a
    fraction correct into a scaled score — that is where the steep top of a
    real SAT curve comes from, and why two wrong answers is 770 and not 800.
    The route you finished on then decides how much of the 200-800 range you
    could reach at all, because on an adaptive test the same raw score is worth
    less if the second module was the easy one.

    College Board does not publish its tables and they differ per form, so this
    is labelled an estimate everywhere it appears.
    """
    if total <= 0:
        return 0
    ceiling = SCORE_ROUTE_CEILING.get(final_tier, SCORE_ROUTE_CEILING[TIER_BASELINE])
    reference = _interpolate(_anchor_curve(section), correct / total)
    # Scale the whole above-the-floor range, not just the top, so a capped
    # route compresses the curve instead of flattening everyone into the cap.
    reach = (ceiling - SCORE_FLOOR) / (SCORE_TOP - SCORE_FLOOR)
    scaled = SCORE_FLOOR + (reference - SCORE_FLOOR) * reach
    clamped = max(SCORE_FLOOR, min(ceiling, scaled))
    return int(round(clamped / 10.0) * 10)


def score_is_reliable(total: int) -> bool:
    """False when there were too few questions for the estimate to mean much."""
    return total >= SCORE_MIN_RELIABLE_QUESTIONS


def estimate_total_score(section_scores: dict[str, int]) -> int:
    """Sum of section estimates, clamped to the 400-1600 scale."""
    if not section_scores:
        return 0
    total = sum(section_scores.values())
    return max(400, min(1600, int(round(total / 10.0) * 10)))


def score_band(score: int) -> str:
    """Plain-language label for an estimated section score."""
    if score >= 700:
        return "Excellent"
    if score >= 600:
        return "Strong"
    if score >= 500:
        return "On track"
    if score >= 400:
        return "Developing"
    return "Building foundations"


# ---------------------------------------------------------------------------
# 4. DRILLS
# ---------------------------------------------------------------------------

def build_drill(
    *,
    section: str,
    domains: list[str],
    count: int,
    difficulty_ramp: bool = True,
    difficulty_filter: str | None = None,
    skill: str | None = None,
    exclude_ids: set[str] | None = None,
    seen_counts: dict[str, int] | None = None,
    rng: random.Random | None = None,
) -> list[Question]:
    """
    Build a targeted drill.

    With difficulty_ramp on (the default) the drill is ordered Easy -> Medium ->
    Hard, mirroring how College Board sequences questions within a skill group.
    Questions are spread evenly across the chosen domains and prefer items you
    have not seen before.
    """
    randomizer = rng or random.Random()
    used: set[str] = set(exclude_ids or set())
    freshness = seen_counts or {}
    targets = domains or ["Any"]

    # Even split across domains, remainder to the earlier ones.
    base, extra = divmod(count, len(targets))
    wanted = {domain: base + (1 if i < extra else 0) for i, domain in enumerate(targets)}

    picked: list[Question] = []
    for domain, quota in wanted.items():
        if quota <= 0:
            continue
        pool = question_repo.fetch(
            section=section if section != "Any" else None,
            domain=None if domain == "Any" else domain,
            skill=skill,
            difficulty=difficulty_filter,
            exclude_ids=used,
            shuffle=True,
            rng=randomizer,
        )
        pool = question_repo.prioritise_unseen(pool, freshness, randomizer)
        if difficulty_ramp:
            pool = _balance_difficulty(pool, quota, randomizer)
        picked.extend(_take(pool, quota, used))

    # Top up from the whole section if some domains were thin.
    if len(picked) < count:
        spare = question_repo.fetch(
            section=section if section != "Any" else None,
            skill=skill, difficulty=difficulty_filter,
            exclude_ids=used, shuffle=True, rng=randomizer,
        )
        spare = question_repo.prioritise_unseen(spare, freshness, randomizer)
        picked.extend(_take(spare, count - len(picked), used))

    if difficulty_ramp:
        picked.sort(key=lambda q: (_DIFFICULTY_RANK.get(q.difficulty, 1), randomizer.random()))
    else:
        randomizer.shuffle(picked)

    for index, question in enumerate(picked, start=1):
        question.position = index
        question.module_number = 1
        question.tier = TIER_BASELINE
    return picked


def _balance_difficulty(pool: list[Question], quota: int,
                        rng: random.Random) -> list[Question]:
    """
    Reorder a pool so the first `quota` items span all three difficulties
    instead of being whatever the shuffle happened to surface first.
    """
    buckets: dict[str, list[Question]] = {d: [] for d in DIFFICULTY_ORDER}
    for question in pool:
        buckets.setdefault(question.difficulty, []).append(question)

    # Round-robin Easy / Medium / Hard so a 6-question drill gets 2/2/2.
    interleaved: list[Question] = []
    index = 0
    while any(buckets[d] for d in DIFFICULTY_ORDER) and len(interleaved) < quota:
        difficulty = DIFFICULTY_ORDER[index % len(DIFFICULTY_ORDER)]
        if buckets[difficulty]:
            interleaved.append(buckets[difficulty].pop(0))
        index += 1
        if index > quota * 6:  # safety valve
            break

    remainder = [q for d in DIFFICULTY_ORDER for q in buckets[d]]
    rng.shuffle(remainder)
    return interleaved + remainder
