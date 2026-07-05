"""One-time (or occasional) builder for data/companies.json.

Pulls company-to-ATS-token mappings from two public sources, keeps only
Greenhouse/Lever/Ashby entries, dedupes, validates every token against the
live ATS endpoint, and writes the survivors to data/companies.json.

Run manually: python scripts/seed_companies.py
"""

import asyncio
import json
import re
import sys
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.ats import fetch_raw

ZSHAH_COMPANIES_URL = (
    "https://raw.githubusercontent.com/zshah101/"
    "Automated-List-Of-Summer-2027-and-Fall-2026-Tech-Internships/"
    "main/data/companies.json"
)
SIMPLIFY_LISTINGS_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/"
    "dev/.github/scripts/listings.json"
)

SUPPORTED_ATS = {"greenhouse", "lever", "ashby"}

URL_PATTERNS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/([a-zA-Z0-9_-]+)")),
    ("lever", re.compile(r"jobs\.lever\.co/([a-zA-Z0-9_-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)")),
]

VALIDATE_CONCURRENCY = config.PER_HOST_CONCURRENCY


async def fetch_json(session: aiohttp.ClientSession, url: str):
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
        resp.raise_for_status()
        return await resp.json(content_type=None)


def candidates_from_zshah(entries: list) -> dict:
    candidates = {}
    for entry in entries:
        ats = entry.get("ats")
        slug = entry.get("slug")
        name = entry.get("name")
        if ats in SUPPORTED_ATS and slug and name:
            candidates.setdefault((ats, slug), name)
    return candidates


def candidates_from_simplify(entries: list) -> dict:
    candidates = {}
    for entry in entries:
        url = entry.get("url") or ""
        name = entry.get("company_name")
        if not name:
            continue
        for ats, pattern in URL_PATTERNS:
            match = pattern.search(url)
            if match:
                candidates.setdefault((ats, match.group(1)), name)
                break
    return candidates


async def validate(session: aiohttp.ClientSession, semaphores: dict, ats: str, token: str) -> bool:
    async with semaphores[ats]:
        result = await fetch_raw(session, ats, token)
        return result is not None


async def main():
    async with aiohttp.ClientSession() as session:
        print("downloading source lists...")
        zshah_raw, simplify_raw = await asyncio.gather(
            fetch_json(session, ZSHAH_COMPANIES_URL),
            fetch_json(session, SIMPLIFY_LISTINGS_URL),
        )

        candidates = {}
        candidates.update(candidates_from_zshah(zshah_raw))
        for key, name in candidates_from_simplify(simplify_raw).items():
            candidates.setdefault(key, name)

        print(f"{len(candidates)} unique candidate boards to validate")

        semaphores = {ats: asyncio.Semaphore(VALIDATE_CONCURRENCY) for ats in SUPPORTED_ATS}

        async def check(key):
            ats, token = key
            ok = await validate(session, semaphores, ats, token)
            return key, ok

        results = await asyncio.gather(*(check(key) for key in candidates))

        companies = [
            {"name": candidates[key], "ats": key[0], "token": key[1]}
            for key, ok in results
            if ok
        ]
        companies.sort(key=lambda c: c["name"].lower())

        dropped = len(candidates) - len(companies)
        print(f"{len(companies)} boards validated, {dropped} dropped (dead or unparseable)")

        config.COMPANIES_PATH.write_text(
            json.dumps(companies, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(f"wrote {config.COMPANIES_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
