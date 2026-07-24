# sentinel

A zero-cost pipeline that watches public Greenhouse, Lever, and Ashby job
boards for Summer 2027 software/software-adjacent internships, filters and
enriches what it finds, and renders the result as a static dashboard.

## How it works

1. **Sourcing** — fetches listings from every company in `data/companies.json`
   across the three supported ATS platforms.
2. **Freshness** — assigns each job a stable ID, records the date it was
   first seen, drops anything already stale, and expires anything older
   than 10 days or missing from the current feed.
3. **Gates** — deterministic keyword rules decide internship-vs-not and
   technical-field-vs-not (no LLM involved anywhere in this pipeline).
   The Summer-2027 cycle gate is the one exception that keeps an
   unresolved case visible with a ❓ badge instead of dropping it, since
   cycle information is often genuinely just unstated.
4. **Enrichment** — badges for deadline, location type, level fit, visa
   sponsorship stance, and application weight, computed once per job.
5. **Match score** — local embedding similarity between each job and your
   resume, shown for information only.

Everything runs on a GitHub Actions cron schedule and writes to
`data/jobs.json`, which a static page on GitHub Pages renders newest-first.

## Local setup

```
pip install -r requirements.txt
```

For local runs, put `RESUME_TEXT` in a `.env` file at the repo root
(already git-ignored). Run the full pipeline with:

```
python -m src.pipeline
```

## Repository layout

- `src/` — pipeline modules (fetch, store, gates, enrich, match).
- `scripts/seed_companies.py` — one-time/occasional company list builder.
- `data/` — `companies.json` (input), `jobs.json` (output), `quarantine.json`
  (per-board failure tracking).
- `logs/run_history.jsonl` — one summary line per pipeline run.
- `index.html`, `app.js`, `style.css` — the dashboard.
