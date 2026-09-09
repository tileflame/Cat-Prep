"""
profile.py — who is studying, when they test, and what they have scored.

Everything the planner needs about one student lives in a single JSON file at
`database/profile.json`. It is deliberately plain text: you can read it, edit
it by hand, and see exactly what the app believes about you. Nothing is hidden
in a database and nothing is inferred that is not written down.

    from profile import Profile
    me = Profile.load()
    me.add_report(parse_score_report("scores.pdf"))
    me.save()
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import date, datetime

from config import DATA_DIR
from score_report import (RW, MATH, RW_DOMAINS, MATH_DOMAINS, ALL_DOMAINS,
                          DOMAIN_SHARE, SECTION_OF)

PROFILE_PATH = DATA_DIR / "database" / "profile.json"

# A section counts as finished when its best score is at or above this AND
# every one of its domains sits in the top band. Superscore keeps your best
# section forever, so once that is true the section can never help you again.
BANKED_SCORE = 750
TOP_BAND_LOW = 680

# Rule-based domains can be moved in about a week. Comprehension domains take
# three or more. That distinction decides what a short window before a test
# should be spent on, and it is the single most useful thing this file knows.
DOMAIN_KIND = {
    "Standard English Conventions": "rules",
    "Expression of Ideas": "rules",
    "Craft and Structure": "comprehension",
    "Information and Ideas": "comprehension",
    "Algebra": "procedure",
    "Advanced Math": "procedure",
    "Problem-Solving and Data Analysis": "procedure",
    "Geometry and Trigonometry": "procedure",
}

SHORT_WINDOW_DAYS = 14


def _iso(value):
    return value.isoformat() if isinstance(value, date) else value


def _as_date(value):
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


@dataclass
class Profile:
    name: str = ""
    target_total: int | None = None
    test_dates: list[dict] = field(default_factory=list)   # {date, label}
    reports: list[dict] = field(default_factory=list)      # parsed score reports
    study_days_per_week: int = 5
    minutes_per_day: int = 90

    # ------------------------------------------------------------- storage

    @classmethod
    def load(cls, path=None) -> "Profile | None":
        path = path or PROFILE_PATH
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path=None) -> None:
        path = path or PROFILE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.loads(json.dumps(asdict(self), default=_iso))
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # --------------------------------------------------------------- input

    def add_report(self, report: dict) -> None:
        """Add a parsed score report, newest last, replacing a same-day one."""
        stamped = dict(report)
        stamped["date"] = _iso(report.get("date"))
        self.reports = [r for r in self.reports
                        if not (r.get("date") and r["date"] == stamped["date"]
                                and r.get("label") == stamped.get("label"))]
        self.reports.append(stamped)
        self.reports.sort(key=lambda r: r.get("date") or "")

    def add_test_date(self, when: date, label: str = "") -> None:
        iso = _iso(when)
        if any(t["date"] == iso for t in self.test_dates):
            return
        self.test_dates.append({"date": iso, "label": label or f"SAT {iso}"})
        self.test_dates.sort(key=lambda t: t["date"])

    # ------------------------------------------------------------- derived

    def upcoming_tests(self, today: date) -> list[dict]:
        out = []
        for entry in self.test_dates:
            when = _as_date(entry["date"])
            if when and when >= today:
                out.append({"date": when, "label": entry.get("label") or "SAT"})
        return out

    def official_reports(self) -> list[dict]:
        """Real sittings only — practice reports cannot be superscored."""
        return [r for r in self.reports if r.get("is_official")]

    def best_sections(self, official_only: bool = True) -> dict[str, int]:
        """Best score per section — this is what a superscore is made of."""
        pool = self.official_reports() if official_only else self.reports
        if not pool and official_only:
            pool = self.reports
        best: dict[str, int] = {}
        for report in pool:
            for name, payload in (report.get("sections") or {}).items():
                score = payload.get("score")
                if score and score > best.get(name, 0):
                    best[name] = score
        return best

    def superscore(self) -> int | None:
        best = self.best_sections()
        if RW in best and MATH in best:
            return best[RW] + best[MATH]
        return None

    def latest_domains(self) -> dict[str, dict]:
        """Most recent report that actually carried domain bands."""
        for report in reversed(self.reports):
            if report.get("domains"):
                return report["domains"]
        return {}

    def banked_sections(self) -> set[str]:
        """
        Sections that are finished and should get zero study time.

        A section is banked when its best score is high AND all of its domains
        are in the top band. Superscore means that score is kept forever, so
        every further minute spent there is a minute stolen from the section
        that can still move.
        """
        best = self.best_sections()
        domains = self.latest_domains()
        banked = set()
        for section, members in ((RW, RW_DOMAINS), (MATH, MATH_DOMAINS)):
            if best.get(section, 0) < BANKED_SCORE:
                continue
            bands = [domains.get(d) for d in members]
            if bands and all(b and b["low"] >= TOP_BAND_LOW for b in bands):
                banked.add(section)
        return banked

    def domain_priorities(self) -> list[dict]:
        """
        Rank every domain by how many points realistically live in it.

        weight = how far below the ceiling the band sits, times the share of
        the section it occupies. A weak domain that is 28% of the section is
        worth far more than an equally weak one that is 15%, and that is the
        judgement most study plans get wrong.
        """
        domains = self.latest_domains()
        banked = self.banked_sections()
        ranked = []
        for name in ALL_DOMAINS:
            section = SECTION_OF[name]
            band = domains.get(name)
            if section in banked:
                weight, verdict = 0.0, "Banked — this section is finished"
            elif not band:
                weight, verdict = 0.5, "No data yet — import a score report"
            else:
                midpoint = (band["low"] + band["high"]) / 2
                gap = max(0.0, (800 - midpoint) / 400)          # 0 at 800, 1 at 400
                weight = round(gap * DOMAIN_SHARE[name] * 10, 3)
                if band["low"] >= TOP_BAND_LOW:
                    verdict = "Top band — leave it alone"
                elif gap > 0.45:
                    verdict = "Weakest — this is where the points are"
                else:
                    verdict = "Middle — after the weakest ones"
            ranked.append({
                "domain": name, "section": section, "band": band,
                "weight": weight, "verdict": verdict,
                "kind": DOMAIN_KIND[name], "share": DOMAIN_SHARE[name],
            })
        ranked.sort(key=lambda d: -d["weight"])
        return ranked

    def focus_for_window(self, days_until_test: int | None,
                         already_worked: set[str] | None = None) -> list[str]:
        """
        Which domains to work, given how long there is before the next test.

        Under two weeks, prefer a rule-based domain even if a comprehension one
        ranks higher: rules move in days, comprehension takes weeks, and
        spending a short window on something that cannot move in it is the most
        common way a study plan wastes itself.

        `already_worked` stops that preference firing twice. A rule-based domain
        that has already had its own dedicated week has had what a short window
        can give it — a second short window should go to the domain that is
        actually costing the most points.
        """
        already_worked = already_worked or set()
        ranked = [d for d in self.domain_priorities() if d["weight"] > 0]
        if not ranked:
            return []
        short = days_until_test is not None and days_until_test <= SHORT_WINDOW_DAYS
        if short:
            rules = [d for d in ranked
                     if d["kind"] == "rules" and d["domain"] not in already_worked]
            if rules and rules[0]["weight"] >= ranked[0]["weight"] * 0.5:
                return [rules[0]["domain"]]
        fresh = [d for d in ranked if d["domain"] not in already_worked] or ranked
        return [d["domain"] for d in fresh[:2]]

    def summary(self) -> dict:
        best = self.best_sections()
        return {
            "name": self.name,
            "superscore": self.superscore(),
            "best": best,
            "banked": sorted(self.banked_sections()),
            "target": self.target_total,
            "reports": len(self.reports),
            "priorities": self.domain_priorities()[:3],
        }
