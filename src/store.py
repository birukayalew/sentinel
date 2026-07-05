"""Job identity, freshness, and expiry.

Turns a batch of freshly-fetched normalized jobs plus the existing
data/jobs.json into the next data/jobs.json: new jobs get a frozen
first-seen date, stale jobs never enter, and jobs age out after 10 days or
when they genuinely disappear from a board that was actually checked this
run (a failed/skipped/quarantined fetch never counts as "disappeared" --
that would punish a company for our own network hiccup).
"""

import datetime
import json

from src import config

RAW_FIELDS = [
    "company",
    "title",
    "location",
    "workplace_type",
    "description_html",
    "apply_url",
    "posted_at",
    "deadline",
    "questions",
]


def build_id(job: dict) -> str:
    return f"{job['source']}:{job['company_token']}:{job['external_id']}"


def company_key(job: dict) -> str:
    return f"{job['source']}:{job['company_token']}"


def _parse_date(value) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.date.fromisoformat(str(value)[:10])
        except ValueError:
            return None


def age_days(job: dict, today: datetime.date) -> int:
    reference = _parse_date(job.get("posted_at")) or _parse_date(job.get("first_seen"))
    if reference is None:
        return 0
    return (today - reference).days


def load_jobs() -> list[dict]:
    if not config.JOBS_PATH.exists():
        return []
    return json.loads(config.JOBS_PATH.read_text(encoding="utf-8"))


def save_jobs(jobs: list[dict]) -> bool:
    """Writes jobs.json with stable ordering. Returns True if the file
    content actually changed, False if the write was skipped as a no-op."""
    jobs_sorted = sorted(jobs, key=lambda j: j["id"])
    serialized = json.dumps(jobs_sorted, indent=2, sort_keys=True)

    existing = config.JOBS_PATH.read_text(encoding="utf-8") if config.JOBS_PATH.exists() else None
    if existing == serialized:
        return False

    config.JOBS_PATH.write_text(serialized, encoding="utf-8")
    return True


def merge(
    existing_jobs: list[dict],
    fetched_jobs: list[dict],
    fetched_company_keys: set[str],
    today: datetime.date | None = None,
) -> tuple[list[dict], dict]:
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    today_str = today.isoformat()

    existing_by_id = {job["id"]: job for job in existing_jobs}
    merged_by_id: dict[str, dict] = {}

    new_count = 0
    dropped_stale_at_ingest = 0

    for norm_job in fetched_jobs:
        job_id = build_id(norm_job)
        if job_id in existing_by_id:
            record = dict(existing_by_id[job_id])
            for field in RAW_FIELDS:
                record[field] = norm_job.get(field)
            merged_by_id[job_id] = record
        else:
            candidate = {
                "id": job_id,
                "source": norm_job["source"],
                "company_token": norm_job["company_token"],
                "external_id": norm_job["external_id"],
                "first_seen": today_str,
                **{field: norm_job.get(field) for field in RAW_FIELDS},
            }
            if age_days(candidate, today) > config.FRESHNESS_GATE_DAYS:
                dropped_stale_at_ingest += 1
                continue
            merged_by_id[job_id] = candidate
            new_count += 1

    disappeared_count = 0
    carried_forward_count = 0
    for job_id, job in existing_by_id.items():
        if job_id in merged_by_id:
            continue
        if company_key(job) in fetched_company_keys:
            disappeared_count += 1
            continue
        merged_by_id[job_id] = job
        carried_forward_count += 1

    expired_count = 0
    final_jobs = []
    for job in merged_by_id.values():
        if age_days(job, today) > config.EXPIRY_DAYS:
            expired_count += 1
            continue
        final_jobs.append(job)

    stats = {
        "jobs_new": new_count,
        "jobs_dropped_stale_at_ingest": dropped_stale_at_ingest,
        "jobs_disappeared": disappeared_count,
        "jobs_expired": expired_count,
        "jobs_carried_forward_unfetched": carried_forward_count,
        "jobs_live": len(final_jobs),
    }
    return final_jobs, stats
