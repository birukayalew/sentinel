"""Orchestrates one full run: fetch -> store merge -> gates -> enrich ->
match -> write. Appends one summary line to run_history.jsonl regardless
of outcome, so every run leaves an audit trail.
"""

import asyncio
import datetime
import json

from src import config, enrich, gates, match, store
from src import fetch as fetchmod


def _append_run_history(summary: dict) -> None:
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with config.RUN_HISTORY_PATH.open("a", encoding="utf-8") as history_file:
        history_file.write(json.dumps(summary, sort_keys=True) + "\n")


async def run() -> dict:
    started = datetime.datetime.now(datetime.timezone.utc)

    companies = fetchmod.load_companies()
    fetched_jobs, fetched_company_keys, fetch_stats = await fetchmod.fetch_all(companies)

    existing_jobs = store.load_jobs()
    merged_jobs, merge_stats = store.merge(existing_jobs, fetched_jobs, fetched_company_keys)

    live_jobs, gate_stats = gates.filter_and_tag(merged_jobs)

    enrich_stats = enrich.enrich_batch(live_jobs)
    weight_stats = await enrich.enrich_application_weights(live_jobs)

    match_stats = match.score_batch(live_jobs)

    jobs_written = store.save_jobs(live_jobs)

    finished = datetime.datetime.now(datetime.timezone.utc)
    summary = {
        "run_started": started.isoformat(),
        "run_finished": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 1),
        "jobs_file_changed": jobs_written,
        **fetch_stats,
        **merge_stats,
        **gate_stats,
        **enrich_stats,
        **weight_stats,
        **match_stats,
    }
    _append_run_history(summary)
    return summary


if __name__ == "__main__":
    result = asyncio.run(run())
    print(json.dumps(result, indent=2))
