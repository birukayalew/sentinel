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
3. **Gates** — a rule-based internship gate and a Summer-2027 cycle gate;
   ambiguous cases are resolved by an LLM judge.
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

For local runs, put `GEMINI_API_KEY`, `GROQ_API_KEY`, and `RESUME_TEXT` in
a `.env` file at the repo root (already git-ignored). Run the full
pipeline with:

```
python -m src.pipeline
```

## GitHub Actions setup

`.github/workflows/cron.yml` runs the pipeline on a schedule and commits
`data/jobs.json`, `data/quarantine.json`, and `logs/run_history.jsonl`
when they change. It needs three repository secrets (Settings → Secrets
and variables → Actions → New repository secret):

- `GEMINI_API_KEY`
- `GROQ_API_KEY`
- `RESUME_TEXT` — your resume/profile as plain text (never commit this to
  a file in the repo; it's only ever read from this secret or a local,
  git-ignored `.env`, since the repo needs to be public for free Pages
  hosting)

All three are optional in the sense that the pipeline degrades gracefully
without them — a job is never dropped for lack of an LLM verdict, it's
kept and flagged for a retry on the next run, and match scoring is simply
skipped without a resume.

## Repository layout

- `src/` — pipeline modules (fetch, store, gates, judge, enrich, match).
- `scripts/seed_companies.py` — one-time/occasional company list builder.
- `data/` — `companies.json` (input), `jobs.json` (output), `quarantine.json`
  (per-board failure tracking).
- `config/prompts.md` — LLM judge prompt template.
- `logs/run_history.jsonl` — one summary line per pipeline run.
- `index.html`, `app.js`, `style.css` — the dashboard.
