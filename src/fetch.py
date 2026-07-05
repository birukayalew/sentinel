"""Fetch-all orchestration: pulls every company's listings concurrently,
isolates per-company failures so one dead board never aborts a run, tracks
consecutive failures for quarantine, and respects a wall-clock time budget
so a run can't hang against a slow or unresponsive third party.
"""

import asyncio
import json
import time

import aiohttp

from src import config
from src.ats import fetch_and_normalize


def load_companies() -> list[dict]:
    if not config.COMPANIES_PATH.exists():
        return []
    return json.loads(config.COMPANIES_PATH.read_text(encoding="utf-8"))


def load_quarantine() -> dict:
    if not config.QUARANTINE_PATH.exists():
        return {}
    return json.loads(config.QUARANTINE_PATH.read_text(encoding="utf-8"))


def save_quarantine(quarantine: dict) -> None:
    config.QUARANTINE_PATH.write_text(
        json.dumps(quarantine, indent=2, sort_keys=True), encoding="utf-8"
    )


def _board_key(company: dict) -> str:
    return f"{company['ats']}:{company['token']}"


async def _fetch_one(session, company, semaphores) -> tuple[dict, list[dict] | None]:
    semaphore = semaphores[company["ats"]]
    async with semaphore:
        try:
            jobs = await fetch_and_normalize(session, company)
        except Exception:
            jobs = None
        return company, jobs


async def fetch_all(companies: list[dict]) -> tuple[list[dict], dict]:
    """Fetch every non-quarantined company within the time budget.

    Returns (normalized_jobs, run_stats). Quarantine state is updated and
    persisted as part of this call.
    """
    quarantine = load_quarantine()

    eligible, skipped_quarantined = [], 0
    for company in companies:
        failures = quarantine.get(_board_key(company), {}).get("consecutive_failures", 0)
        if failures >= config.QUARANTINE_THRESHOLD:
            skipped_quarantined += 1
        else:
            eligible.append(company)

    semaphores = {
        "greenhouse": asyncio.Semaphore(config.PER_HOST_CONCURRENCY),
        "lever": asyncio.Semaphore(config.PER_HOST_CONCURRENCY),
        "ashby": asyncio.Semaphore(config.PER_HOST_CONCURRENCY),
    }

    all_jobs: list[dict] = []
    fetched, failed, skipped_budget = 0, 0, 0
    start = time.monotonic()

    async with aiohttp.ClientSession() as session:
        tasks = [
            asyncio.ensure_future(_fetch_one(session, company, semaphores))
            for company in eligible
        ]

        done, pending = await asyncio.wait(tasks, timeout=config.FETCH_TIME_BUDGET_SECONDS)

        for task in pending:
            task.cancel()
        skipped_budget = len(pending)

        for task in done:
            company, jobs = task.result()
            key = _board_key(company)
            if jobs is None:
                failed += 1
                entry = quarantine.setdefault(key, {"consecutive_failures": 0})
                entry["consecutive_failures"] += 1
            else:
                fetched += 1
                quarantine.pop(key, None)
                all_jobs.extend(jobs)

    save_quarantine(quarantine)

    stats = {
        "companies_total": len(companies),
        "companies_fetched": fetched,
        "companies_failed": failed,
        "companies_skipped_quarantined": skipped_quarantined,
        "companies_skipped_budget": skipped_budget,
        "jobs_fetched": len(all_jobs),
        "elapsed_seconds": round(time.monotonic() - start, 1),
    }
    return all_jobs, stats


if __name__ == "__main__":
    async def _main():
        companies = load_companies()
        jobs, stats = await fetch_all(companies)
        print(json.dumps(stats, indent=2))

    asyncio.run(_main())
