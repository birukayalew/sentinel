"""Hard filtering gates: internship-vs-not, and Summer-2027-cycle-vs-not.

Both gates work in three tiers -- clear yes, clear no, ambiguous. Clear
verdicts are resolved here with no LLM call. Ambiguous verdicts are
recorded as reasons on the job and left for the judge (src/judge.py) to
resolve later; nothing is ever dropped just for being ambiguous.

Gate results are cached on the job record (`gate_evaluated`) so a job's
title/description is only ever scored once, keeping runs cheap and
verdicts stable over time.
"""

import re

from src.textutil import strip_html

INTERNSHIP_YES_PATTERN = re.compile(
    r"\b("
    r"intern|interns|internship|internships|"
    r"co-?op|"
    r"working student|"
    r"summer analyst|winter analyst|spring analyst|fall analyst|"
    r"research intern|"
    r"phd intern|"
    r"trainee|"
    r"placement|"
    r"thesis student|"
    r"apprentice|apprenticeship"
    r")\b",
    re.IGNORECASE,
)

INTERNSHIP_NO_PATTERN = re.compile(
    r"\b("
    r"senior|sr\.?|staff|principal|lead|"
    r"manager|director|head of|chief|executive|"
    r"vice president|\bvp\b"
    r")\b",
    re.IGNORECASE,
)

YEAR_PATTERN = re.compile(r"\b(20(?:2[4-9]|3[0-5]))\b")

GRAD_PHRASE_PATTERN = re.compile(
    r"(graduat\w*|class of|anticipated grad\w*|expected grad\w*)",
    re.IGNORECASE,
)

SEASON_KEYWORD_PATTERN = re.compile(
    r"(summer|internship program|intern cohort|\bcohort\b)",
    re.IGNORECASE,
)

TARGET_CYCLE_YEAR = 2027
GRAD_MASK_WINDOW = 40
SEASON_PROXIMITY_WINDOW = 30


def internship_gate(title: str) -> str:
    """Returns 'yes', 'no', or 'ambiguous'."""
    if INTERNSHIP_YES_PATTERN.search(title or ""):
        return "yes"
    if INTERNSHIP_NO_PATTERN.search(title or ""):
        return "no"
    return "ambiguous"


def _mask_graduation_phrases(text: str) -> str:
    masked = list(text)
    for match in GRAD_PHRASE_PATTERN.finditer(text):
        start = max(0, match.start() - GRAD_MASK_WINDOW)
        end = min(len(text), match.end() + GRAD_MASK_WINDOW)
        for i in range(start, end):
            masked[i] = " "
    return "".join(masked)


def _find_cycle_year_in_text(text: str) -> int | None:
    masked = _mask_graduation_phrases(text)
    season_spans = [m.span() for m in SEASON_KEYWORD_PATTERN.finditer(masked)]
    if not season_spans:
        return None

    candidates = []
    for year_match in YEAR_PATTERN.finditer(masked):
        year = int(year_match.group(1))
        y_start, y_end = year_match.span()
        near_season = any(
            (s_start - SEASON_PROXIMITY_WINDOW) <= y_end
            and (s_end + SEASON_PROXIMITY_WINDOW) >= y_start
            for s_start, s_end in season_spans
        )
        if near_season:
            candidates.append(year)

    if not candidates:
        return None
    if TARGET_CYCLE_YEAR in candidates:
        return TARGET_CYCLE_YEAR
    return candidates[0]


def _find_year_in_title(title: str) -> int | None:
    match = YEAR_PATTERN.search(title or "")
    return int(match.group(1)) if match else None


def cycle_gate(title: str, description_html: str | None) -> tuple[str, int | None]:
    """Returns (verdict, year) where verdict is 'keep', 'drop', or 'ambiguous'."""
    title_year = _find_year_in_title(title)
    if title_year is not None:
        return ("keep", title_year) if title_year == TARGET_CYCLE_YEAR else ("drop", title_year)

    description_text = strip_html(description_html)
    description_year = _find_cycle_year_in_text(description_text)
    if description_year is not None:
        return (
            ("keep", description_year)
            if description_year == TARGET_CYCLE_YEAR
            else ("drop", description_year)
        )

    return ("ambiguous", None)


def evaluate_gates(job: dict) -> dict:
    if job.get("gate_evaluated"):
        return job

    internship_verdict = internship_gate(job.get("title", ""))
    cycle_verdict, cycle_year = cycle_gate(job.get("title", ""), job.get("description_html"))

    ambiguity_reasons = set(job.get("ambiguity_reasons") or [])
    dropped = False
    drop_reason = None

    if internship_verdict == "no":
        dropped = True
        drop_reason = "not_internship"
    elif cycle_verdict == "drop":
        dropped = True
        drop_reason = f"wrong_cycle_{cycle_year}"
    else:
        if internship_verdict == "ambiguous":
            ambiguity_reasons.add("internship_ambiguous")
        if cycle_verdict == "ambiguous":
            ambiguity_reasons.add("cycle_ambiguous")

    job["gate_evaluated"] = True
    job["gate_dropped"] = dropped
    job["gate_drop_reason"] = drop_reason
    job["cycle_year"] = cycle_year if cycle_verdict == "keep" else None
    job["ambiguity_reasons"] = sorted(ambiguity_reasons)
    return job


def filter_and_tag(jobs: list[dict]) -> tuple[list[dict], dict]:
    kept = []
    dropped_not_internship = 0
    dropped_wrong_cycle = 0
    ambiguous = 0

    for job in jobs:
        evaluate_gates(job)
        if job["gate_dropped"]:
            if job["gate_drop_reason"] == "not_internship":
                dropped_not_internship += 1
            else:
                dropped_wrong_cycle += 1
            continue
        if job["ambiguity_reasons"]:
            ambiguous += 1
        kept.append(job)

    stats = {
        "gate_dropped_not_internship": dropped_not_internship,
        "gate_dropped_wrong_cycle": dropped_wrong_cycle,
        "gate_ambiguous": ambiguous,
        "gate_kept": len(kept),
    }
    return kept, stats
