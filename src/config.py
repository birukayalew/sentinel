"""Constants shared across the pipeline. Kept together so tuning one
number doesn't require hunting through every module."""

from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")
DATA_DIR = ROOT_DIR / "data"
LOGS_DIR = ROOT_DIR / "logs"
CONFIG_DIR = ROOT_DIR / "config"
RESUME_PATH = ROOT_DIR / "resume" / "profile.txt"

COMPANIES_PATH = DATA_DIR / "companies.json"
JOBS_PATH = DATA_DIR / "jobs.json"
QUARANTINE_PATH = DATA_DIR / "quarantine.json"
RUN_HISTORY_PATH = LOGS_DIR / "run_history.jsonl"

# Freshness / lifecycle
FRESHNESS_GATE_DAYS = 7   # jobs older than this at first sight are never ingested
EXPIRY_DAYS = 10          # jobs are dropped once older than this, or absent from feed

# Fetch phase
PER_HOST_CONCURRENCY = 20
FETCH_TIME_BUDGET_SECONDS = 240
QUARANTINE_THRESHOLD = 3  # consecutive failures before a board is skipped
MAX_DESCRIPTION_CHARS = 6000  # stored description is stripped plain text, capped here

# LLM judge
LLM_PROVIDER_ORDER = ["gemini", "groq"]
MAX_LLM_CALLS_PER_RUN = 200
JUDGE_CONCURRENCY = 5
JUDGE_TIME_BUDGET_SECONDS = 120

# Application-weight enrichment (per-job Greenhouse detail fetch)
ENRICH_CONCURRENCY = 20
ENRICH_TIME_BUDGET_SECONDS = 120
