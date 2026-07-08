"""Hard filtering gates: internship-vs-not, technical-field-vs-not, and
Summer-2027-cycle-vs-not.

The internship and technical-field gates are both deterministic keyword
searches over title + description -- no LLM, no ambiguous tier, no
maybe. If the keywords aren't there, the job is dropped. This is a
deliberate departure from a softer "ambiguous -> ask an LLM" design: in
practice, waiting on a rate-limited LLM to filter obvious full-time and
non-technical postings produced a dashboard full of unverified noise
sitting behind an `unjudged` badge, which defeats the point of a filter.
Keyword coverage is intentionally broad (matching the spec's own example
categories) so the tradeoff skews toward false positives (an occasional
non-technical role slipping through) rather than false negatives (a real
internship silently dropped) -- but unlike the old design, that tradeoff
is now made once, transparently, in a list you can read, not implicitly
by whichever jobs a flaky API happened to reach this run.

Only the cycle gate keeps a genuine ambiguous tier with an LLM fallback,
because "what year is this posting for" often really is unstated or only
inferable from context in a way a keyword list can't resolve, and the
spec explicitly wants an unresolved cycle to keep the job (❓ badge)
rather than drop it -- false negatives there are costlier than for the
other two gates, since cycle information genuinely is sometimes just
missing rather than determinable.

Gate results are cached on the job record, keyed on both the title text
used to compute them (`gate_evaluated_title`) and a `GATE_LOGIC_VERSION`
stamp (`gate_evaluated_version`). Title changes handle a company editing
a live posting; the version stamp handles us editing the *rules* --
without it, changing this file would silently do nothing to the
thousands of jobs already sitting in the store with an old verdict from
before the change, since their titles never changed. Bump
GATE_LOGIC_VERSION whenever the gate logic itself changes meaningfully,
and the entire existing dataset self-heals on the next run.
"""

import re

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

# Real internship titles overwhelmingly say "intern"/"internship"/"co-op"
# right in the title -- that alone is reliable, so INTERNSHIP_YES_PATTERN
# is trusted there. Descriptions are a different story: a bare-word scan
# there catches full-time postings that mention internships only as a
# desired-background phrase ("experience gained through internships,
# projects, or coursework"), which is a real, common phrasing for entry
# level full-time roles and produced real false positives in testing.
# This pattern requires the description to describe *this posting* as an
# internship, not just mention the concept.
INTERNSHIP_DESCRIPTION_ANCHOR_PATTERN = re.compile(
    r"("
    r"this (?:is |role is )?(?:an? )?(?:summer |winter |fall |spring )?internship|"
    r"internship (?:position|opportunity|role|track)|"
    r"our (?:summer |winter |fall |spring )?internship(?: program)?|"
    r"paid internship|"
    r"\d+[\s-]week internship|"
    r"co-?op (?:position|opportunity|role)|"
    r"our co-?op(?: program)?|"
    r"working student (?:program|position)"
    r")",
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

# Broad on purpose: "any job that involves writing code or working with
# data/software systems in any capacity" is the bar, not a narrow SWE-only
# list. Bare "engineer"/"analyst"/"data" are deliberately excluded --
# too many non-technical roles use those words alone (electrical engineer,
# financial analyst, data entry) -- but compounds that specifically imply
# software/coding work are included.
TECHNICAL_PATTERN = re.compile(
    r"\b("
    r"software|firmware|"
    r"backend|back-end|frontend|front-end|full[- ]stack|"
    r"devops|site reliability|"
    r"data engineer\w*|data scien\w*|data analy\w*|"
    r"machine learning|artificial intelligence|"
    r"cloud engineer\w*|cloud infrastructure|"
    r"cyber ?security|security engineer\w*|"
    r"computer vision|natural language processing|"
    r"robotics|embedded systems?|embedded software|"
    r"quality assurance engineer\w*|test engineer\w*|"
    r"research engineer\w*|"
    r"web develop\w*|mobile develop\w*|app develop\w*|application develop\w*|"
    r"computer science|"
    r"programmer|computer programming|software programming|\bcoding\b|write code|writing code|"
    r"database engineer\w*|network engineer\w*|systems engineer\w*|infrastructure engineer\w*|"
    r"\bpython\b|\bjava\b|javascript|typescript|\bsql\b|\bc\+\+|\bgolang\b|\brust\b|"
    r"kubernetes|\bdocker\b|"
    r"\bapi\b|\bapis\b"
    r")\b",
    re.IGNORECASE,
)

# Short, collision-prone acronyms (bare "AI"/"ML" especially -- postings
# increasingly include boilerplate like "we may use AI-powered tools to
# review applications", which has nothing to do with the role itself).
# Only trusted when they appear in the title: short, purposeful text,
# not paragraphs of generic legal/EEO language.
TECHNICAL_TITLE_ONLY_PATTERN = re.compile(
    r"\b(swe|ml|ai|sre|qa|nlp|sdet)\b",
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

# Bump whenever gate logic changes meaningfully -- invalidates every
# cached verdict in the store, not just ones whose title happens to
# change, forcing a full re-evaluation of the existing dataset.
# 2: switched internship/technical gates from ambiguous+LLM to
#    deterministic title+description keyword search.
# 3: internship description scan now requires an anchored phrase
#    ("internship program", "this is an internship", ...) instead of a
#    bare "internship" mention, which was catching full-time postings
#    that only reference internships as desired prior experience.
# 4: dropped bare "programming" from the technical pattern (collided
#    with "internship programming" in the HR/event-planning sense) and
#    tightened "co-op program"/"internship program" anchors to require
#    "our"/"this" -- both were matching candidate-requirement phrasing
#    like "must be enrolled in a co-op program" rather than a
#    description of the posting itself.
GATE_LOGIC_VERSION = 4


def internship_gate(title: str, description: str | None) -> str:
    """Returns 'yes' or 'no'. No ambiguous tier -- if internship language
    isn't found in the title, or the description doesn't specifically
    describe this posting as an internship, the job is dropped."""
    if INTERNSHIP_NO_PATTERN.search(title or ""):
        return "no"
    if INTERNSHIP_YES_PATTERN.search(title or ""):
        return "yes"
    if INTERNSHIP_DESCRIPTION_ANCHOR_PATTERN.search(description or ""):
        return "yes"
    return "no"


def technical_field_gate(title: str, description: str | None) -> str:
    """Returns 'yes' or 'no'. No ambiguous tier -- if nothing suggests
    software/coding/data work, the job is dropped."""
    if TECHNICAL_TITLE_ONLY_PATTERN.search(title or ""):
        return "yes"
    combined = f"{title or ''} {description or ''}"
    return "yes" if TECHNICAL_PATTERN.search(combined) else "no"


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


def cycle_gate(title: str, description: str | None) -> tuple[str, int | None]:
    """Returns (verdict, year) where verdict is 'keep', 'drop', or 'ambiguous'."""
    title_year = _find_year_in_title(title)
    if title_year is not None:
        return ("keep", title_year) if title_year == TARGET_CYCLE_YEAR else ("drop", title_year)

    description_year = _find_cycle_year_in_text(description or "")
    if description_year is not None:
        return (
            ("keep", description_year)
            if description_year == TARGET_CYCLE_YEAR
            else ("drop", description_year)
        )

    return ("ambiguous", None)


def evaluate_gates(job: dict) -> dict:
    # Cached on title + logic version, not just a bare flag -- a company
    # can (and does) edit a posting's title after we first see it, and we
    # can (and just did) change the gate logic itself. Either one
    # invalidates the cache; without both checks a stale verdict could
    # silently persist forever even though re-running the current code
    # would obviously resolve it differently.
    if (
        job.get("gate_evaluated")
        and job.get("gate_evaluated_title") == job.get("title")
        and job.get("gate_evaluated_version") == GATE_LOGIC_VERSION
    ):
        return job

    title = job.get("title", "")
    description = job.get("description")

    internship_verdict = internship_gate(title, description)
    technical_verdict = "yes" if internship_verdict == "no" else technical_field_gate(title, description)
    cycle_verdict, cycle_year = (
        cycle_gate(title, description) if technical_verdict == "yes" else ("ambiguous", None)
    )

    ambiguity_reasons = set()
    dropped = False
    drop_reason = None

    if internship_verdict == "no":
        dropped = True
        drop_reason = "not_internship"
    elif technical_verdict == "no":
        dropped = True
        drop_reason = "not_technical_field"
    elif cycle_verdict == "drop":
        dropped = True
        drop_reason = f"wrong_cycle_{cycle_year}"
    elif cycle_verdict == "ambiguous":
        ambiguity_reasons.add("cycle_ambiguous")

    job["gate_evaluated"] = True
    job["gate_evaluated_title"] = title
    job["gate_evaluated_version"] = GATE_LOGIC_VERSION
    job["gate_dropped"] = dropped
    job["gate_drop_reason"] = drop_reason
    job["cycle_year"] = cycle_year if cycle_verdict == "keep" else None
    job["ambiguity_reasons"] = sorted(ambiguity_reasons)
    return job


def filter_and_tag(jobs: list[dict]) -> tuple[list[dict], dict]:
    kept = []
    dropped_not_internship = 0
    dropped_not_technical = 0
    dropped_wrong_cycle = 0
    ambiguous = 0

    for job in jobs:
        evaluate_gates(job)
        if job["gate_dropped"]:
            if job["gate_drop_reason"] == "not_internship":
                dropped_not_internship += 1
            elif job["gate_drop_reason"] == "not_technical_field":
                dropped_not_technical += 1
            else:
                dropped_wrong_cycle += 1
            continue
        if job["ambiguity_reasons"]:
            ambiguous += 1
        kept.append(job)

    stats = {
        "gate_dropped_not_internship": dropped_not_internship,
        "gate_dropped_not_technical": dropped_not_technical,
        "gate_dropped_wrong_cycle": dropped_wrong_cycle,
        "gate_ambiguous": ambiguous,
        "gate_kept": len(kept),
    }
    return kept, stats
