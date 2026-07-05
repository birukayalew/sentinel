"""Rule-based enrichment badges, computed once per job and cached forever
(`enriched` flag). Regex is the primary source for every badge; the LLM
judge's salvage fields (populated only for jobs that already needed a
judge call for gate reasons) are used as a fallback where regex found
nothing, never as a reason to make an extra call by themselves -- that
would defeat the point of keeping LLM usage bounded by gate ambiguity.
"""

import asyncio

import aiohttp

import re

from src.textutil import strip_html

DEADLINE_KEYWORD_PATTERN = re.compile(
    r"(application[s]?\s+(?:close|closes|closing|deadline|due)|apply\s+by|deadline\s+to\s+apply)",
    re.IGNORECASE,
)
DATE_PATTERN = re.compile(
    r"\b((?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}|"
    r"\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})\b"
)

PHD_PATTERN = re.compile(r"\b(ph\.?d\.?|doctoral|doctorate)\b", re.IGNORECASE)
MS_PATTERN = re.compile(r"\b(master'?s degree|master'?s|m\.s\.|graduate student)\b", re.IGNORECASE)
BS_PATTERN = re.compile(r"\b(bachelor'?s degree|bachelor'?s|undergraduate|b\.s\.)\b", re.IGNORECASE)

NO_SPONSOR_PATTERN = re.compile(
    r"(does not (?:offer|provide) (?:visa )?sponsorship|"
    r"(?:will|is) not (?:able to )?sponsor|"
    r"unable to (?:offer|provide) (?:visa )?sponsorship|"
    r"no (?:visa )?sponsorship (?:is )?(?:available|offered|provided))",
    re.IGNORECASE,
)
SPONSOR_PATTERN = re.compile(
    r"(will sponsor|sponsorship (?:is )?available|"
    r"(?:offers?|provides?) (?:visa )?sponsorship|"
    r"open to (?:visa )?sponsorship)",
    re.IGNORECASE,
)
CITIZENS_ONLY_PATTERN = re.compile(
    r"(must be a (?:u\.?s\.?|united states) citizen|"
    r"u\.?s\.?\s+citizenship (?:is )?required|"
    r"security clearance (?:is )?required|"
    r"active (?:top secret|secret) clearance|"
    r"ability to obtain (?:a )?(?:security )?clearance)",
    re.IGNORECASE,
)

REMOTE_WORKPLACE_VALUES = {"remote", "fully_remote", "fully remote"}
HYBRID_WORKPLACE_VALUES = {"hybrid"}
ONSITE_WORKPLACE_VALUES = {"onsite", "on-site", "office"}

STANDARD_QUESTION_LABELS = {
    "first name", "last name", "email", "phone", "phone number",
    "resume/cv", "resume", "cv", "cover letter", "linkedin profile",
    "linkedin", "website", "portfolio", "location", "current location",
}

GREENHOUSE_JOB_DETAIL_URL = (
    "https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}?questions=true"
)


def find_deadline_in_text(text: str) -> str | None:
    for keyword_match in DEADLINE_KEYWORD_PATTERN.finditer(text):
        window = text[keyword_match.end():keyword_match.end() + 60]
        date_match = DATE_PATTERN.search(window)
        if date_match:
            return date_match.group(1)
    return None


def classify_workplace(job: dict) -> str | None:
    workplace_type = (job.get("workplace_type") or "").strip().lower()
    if workplace_type in REMOTE_WORKPLACE_VALUES:
        return "remote"
    if workplace_type in HYBRID_WORKPLACE_VALUES:
        return "hybrid"
    if workplace_type in ONSITE_WORKPLACE_VALUES:
        return "onsite"

    location = (job.get("location") or "").lower()
    if "remote" in location:
        return "remote"
    if "hybrid" in location:
        return "hybrid"
    return None


def classify_level(text: str) -> str | None:
    levels = set()
    if PHD_PATTERN.search(text):
        levels.add("PhD")
    if MS_PATTERN.search(text):
        levels.add("MS")
    if BS_PATTERN.search(text):
        levels.add("BS")
    return levels.pop() if len(levels) == 1 else None


def classify_visa(text: str) -> str | None:
    if CITIZENS_ONLY_PATTERN.search(text):
        return "citizens_only"
    if NO_SPONSOR_PATTERN.search(text):
        return "no_sponsorship"
    if SPONSOR_PATTERN.search(text):
        return "sponsors"
    return None


def enrich_job(job: dict) -> dict:
    if job.get("enriched"):
        return job

    text = strip_html(job.get("description_html"))

    job["deadline_badge"] = (
        job.get("deadline") or find_deadline_in_text(text) or job.get("llm_deadline")
    )
    job["workplace_badge"] = classify_workplace(job)
    job["level_badge"] = classify_level(text) or job.get("llm_level_fit")
    job["visa_badge"] = classify_visa(text) or job.get("llm_visa_sponsorship") or "unknown"
    job["application_weight"] = None
    job["enriched"] = True
    return job


def enrich_batch(jobs: list[dict]) -> dict:
    computed = 0
    for job in jobs:
        if job.get("gate_dropped"):
            continue
        if not job.get("enriched"):
            enrich_job(job)
            computed += 1
    return {"enrichment_computed": computed}


def _is_custom_question(question: dict) -> bool:
    label = (question.get("label") or "").strip().lower()
    return label not in STANDARD_QUESTION_LABELS


def classify_application_weight(questions: list[dict]) -> str:
    custom = [q for q in questions if _is_custom_question(q)]
    has_essay_field = any(
        field.get("type") == "textarea"
        for question in custom
        for field in (question.get("fields") or [])
    )
    if has_essay_field or len(custom) > 4:
        return "essay_heavy"
    return "quick_apply"


async def _fetch_application_weight(session: aiohttp.ClientSession, job: dict) -> str | None:
    url = GREENHOUSE_JOB_DETAIL_URL.format(token=job["company_token"], job_id=job["external_id"])
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return None
            body = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return None

    questions = body.get("questions")
    if not isinstance(questions, list):
        return None
    return classify_application_weight(questions)


async def enrich_application_weights(jobs: list[dict]) -> dict:
    """Greenhouse only -- Lever/Ashby don't expose an equivalent schema.
    Only runs for jobs that survived every other stage, so volume is small."""
    candidates = [
        job for job in jobs
        if not job.get("gate_dropped")
        and job.get("source") == "greenhouse"
        and job.get("enriched")
        and job.get("application_weight") is None
    ]
    if not candidates:
        return {"application_weight_fetched": 0}

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            *(_fetch_application_weight(session, job) for job in candidates)
        )

    for job, weight in zip(candidates, results):
        job["application_weight"] = weight or "unknown"

    return {"application_weight_fetched": len(candidates)}
