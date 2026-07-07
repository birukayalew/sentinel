"""Raw request helpers for the three supported ATS platforms.

Each function does one GET against the platform's public job-board API and
returns the parsed job list on success, or None on any failure (bad status,
timeout, unparseable body). None is a normal, expected outcome here -- it
just means "this token isn't a live board" -- callers decide what to do
with it (drop during seeding, count as a failure during a real fetch run).
"""

import asyncio
import datetime

import aiohttp

from src import config
from src.textutil import strip_html

FETCH_EXCEPTIONS = (aiohttp.ClientError, asyncio.TimeoutError, ValueError)

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true&questions=true"
LEVER_URL = "https://api.lever.co/v0/postings/{token}?mode=json"
ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{token}"

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)


async def fetch_greenhouse_raw(session: aiohttp.ClientSession, token: str) -> list | None:
    try:
        async with session.get(GREENHOUSE_URL.format(token=token), timeout=REQUEST_TIMEOUT) as resp:
            if resp.status != 200:
                return None
            body = await resp.json(content_type=None)
            jobs = body.get("jobs")
            return jobs if isinstance(jobs, list) else None
    except FETCH_EXCEPTIONS:
        return None


async def fetch_lever_raw(session: aiohttp.ClientSession, token: str) -> list | None:
    try:
        async with session.get(LEVER_URL.format(token=token), timeout=REQUEST_TIMEOUT) as resp:
            if resp.status != 200:
                return None
            body = await resp.json(content_type=None)
            return body if isinstance(body, list) else None
    except FETCH_EXCEPTIONS:
        return None


async def fetch_ashby_raw(session: aiohttp.ClientSession, token: str) -> list | None:
    try:
        async with session.get(ASHBY_URL.format(token=token), timeout=REQUEST_TIMEOUT) as resp:
            if resp.status != 200:
                return None
            body = await resp.json(content_type=None)
            jobs = body.get("jobs")
            return jobs if isinstance(jobs, list) else None
    except FETCH_EXCEPTIONS:
        return None


FETCHERS = {
    "greenhouse": fetch_greenhouse_raw,
    "lever": fetch_lever_raw,
    "ashby": fetch_ashby_raw,
}


async def fetch_raw(session: aiohttp.ClientSession, ats: str, token: str) -> list | None:
    fetcher = FETCHERS.get(ats)
    if fetcher is None:
        return None
    return await fetcher(session, token)


# --- Normalization -----------------------------------------------------
# Each ATS shapes its job payload differently. These functions map the raw
# payload onto one common record shape so everything downstream (gates,
# judge, enrichment) can stay ATS-agnostic. Fields that an ATS doesn't
# provide are left None rather than guessed.
#
# Descriptions are stripped to plain text and truncated here, once, at the
# source -- every downstream consumer (gates, judge, enrich, match) only
# ever needs plain text, and every one of them only needs it once (each
# caches its own "already processed" flag). Storing the raw HTML forever
# was pure waste: a real run showed ~5KB/job of mostly inline-style markup
# that nothing ever reads again after a job's one-time processing is done.

def _clean_description(raw_html: str | None) -> str | None:
    text = strip_html(raw_html)
    if not text:
        return None
    return text[: config.MAX_DESCRIPTION_CHARS]


def normalize_greenhouse(company: dict, raw_job: dict) -> dict:
    return {
        "source": "greenhouse",
        "company": company["name"],
        "company_token": company["token"],
        "external_id": str(raw_job.get("id")),
        "title": raw_job.get("title") or "",
        "location": (raw_job.get("location") or {}).get("name"),
        "workplace_type": None,
        "description": _clean_description(raw_job.get("content")),
        "apply_url": raw_job.get("absolute_url"),
        "posted_at": raw_job.get("first_published") or raw_job.get("updated_at"),
        "deadline": raw_job.get("application_deadline"),
        "questions": raw_job.get("questions"),
    }


def normalize_lever(company: dict, raw_job: dict) -> dict:
    categories = raw_job.get("categories") or {}
    return {
        "source": "lever",
        "company": company["name"],
        "company_token": company["token"],
        "external_id": str(raw_job.get("id")),
        "title": raw_job.get("text") or "",
        "location": categories.get("location"),
        "workplace_type": raw_job.get("workplaceType"),
        "description": _clean_description(
            raw_job.get("description") or raw_job.get("descriptionPlain")
        ),
        "apply_url": raw_job.get("applyUrl") or raw_job.get("hostedUrl"),
        "posted_at": _epoch_ms_to_iso(raw_job.get("createdAt")),
        "deadline": None,
        "questions": None,
    }


def normalize_ashby(company: dict, raw_job: dict) -> dict:
    return {
        "source": "ashby",
        "company": company["name"],
        "company_token": company["token"],
        "external_id": str(raw_job.get("id")),
        "title": raw_job.get("title") or "",
        "location": raw_job.get("location"),
        "workplace_type": raw_job.get("workplaceType"),
        "description": _clean_description(
            raw_job.get("descriptionHtml") or raw_job.get("descriptionPlain")
        ),
        "apply_url": raw_job.get("applyUrl") or raw_job.get("jobUrl"),
        "posted_at": raw_job.get("publishedAt"),
        "deadline": None,
        "questions": None,
    }


NORMALIZERS = {
    "greenhouse": normalize_greenhouse,
    "lever": normalize_lever,
    "ashby": normalize_ashby,
}


def _epoch_ms_to_iso(epoch_ms) -> str | None:
    if not isinstance(epoch_ms, (int, float)):
        return None
    return datetime.datetime.fromtimestamp(epoch_ms / 1000, tz=datetime.timezone.utc).isoformat()


async def fetch_and_normalize(session: aiohttp.ClientSession, company: dict) -> list[dict] | None:
    raw_jobs = await fetch_raw(session, company["ats"], company["token"])
    if raw_jobs is None:
        return None
    normalizer = NORMALIZERS[company["ats"]]
    return [normalizer(company, job) for job in raw_jobs]
