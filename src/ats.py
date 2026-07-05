"""Raw request helpers for the three supported ATS platforms.

Each function does one GET against the platform's public job-board API and
returns the parsed job list on success, or None on any failure (bad status,
timeout, unparseable body). None is a normal, expected outcome here -- it
just means "this token isn't a live board" -- callers decide what to do
with it (drop during seeding, count as a failure during a real fetch run).
"""

import asyncio

import aiohttp

FETCH_EXCEPTIONS = (aiohttp.ClientError, asyncio.TimeoutError, ValueError)

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?questions=true"
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
