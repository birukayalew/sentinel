"""LLM judge: resolves gate-ambiguous jobs with one structured call each,
falling back across providers on failure. Never drops a job for an
infrastructure problem -- if every provider fails, the job is left
`unjudged` for a retry on a later run.
"""

import asyncio
import json
import os
import re
import time

from src import config

GEMINI_MODEL = "gemini-2.0-flash"
GROQ_MODEL = "llama-3.3-70b-versatile"
TARGET_CYCLE_YEAR = 2027

# Conservative floor under each provider's free-tier per-minute rate limit,
# enforced proactively so we don't burn the whole run's budget hitting 429s
# and disabling a provider that would have recovered a minute later.
PROVIDER_MIN_INTERVAL_SECONDS = {"gemini": 4.5, "groq": 2.5}

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

_disabled_providers: set[str] = set()
_provider_locks = {name: asyncio.Lock() for name in PROVIDER_MIN_INTERVAL_SECONDS}
_provider_last_call = {name: 0.0 for name in PROVIDER_MIN_INTERVAL_SECONDS}


def reset_provider_state() -> None:
    _disabled_providers.clear()
    for name in _provider_last_call:
        _provider_last_call[name] = 0.0


async def _throttle(provider_name: str) -> None:
    min_interval = PROVIDER_MIN_INTERVAL_SECONDS.get(provider_name, 2.0)
    lock = _provider_locks.setdefault(provider_name, asyncio.Lock())
    async with lock:
        wait = _provider_last_call.get(provider_name, 0.0) + min_interval - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _provider_last_call[provider_name] = time.monotonic()


def _load_prompt_template() -> str:
    return (config.CONFIG_DIR / "prompts.md").read_text(encoding="utf-8")


def build_prompt(job: dict) -> str:
    template = _load_prompt_template()
    description = (job.get("description") or "")[:4000]
    return template.format(
        company=job.get("company", ""),
        title=job.get("title", ""),
        description=description or "(no description provided)",
    )


def _is_rate_limit(exc: Exception) -> bool:
    if any(getattr(exc, attr, None) == 429 for attr in ("status_code", "code")):
        return True
    return type(exc).__name__ in {"ResourceExhausted", "RateLimitError", "TooManyRequests"}


def _error_summary(exc: Exception) -> str:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return f"{type(exc).__name__}:{status}"


async def _call_gemini(prompt: str) -> str | None:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    from google import genai

    def _sync_call():
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        return response.text

    try:
        return await asyncio.wait_for(asyncio.to_thread(_sync_call), timeout=30)
    except Exception as exc:
        if _is_rate_limit(exc):
            _disabled_providers.add("gemini")
        return None


async def _call_groq(prompt: str) -> str | None:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return None
    from groq import Groq

    def _sync_call():
        client = Groq(api_key=api_key)
        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
        )
        return completion.choices[0].message.content

    try:
        return await asyncio.wait_for(asyncio.to_thread(_sync_call), timeout=30)
    except Exception as exc:
        if _is_rate_limit(exc):
            _disabled_providers.add("groq")
        return None


_PROVIDER_CALLERS = {
    "gemini": _call_gemini,
    "groq": _call_groq,
}

REQUIRED_KEYS = {"is_internship", "is_technical_field"}


def _extract_json(text: str) -> dict | None:
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if not REQUIRED_KEYS.issubset(parsed.keys()):
        return None
    if not isinstance(parsed.get("is_internship"), bool):
        return None
    if not isinstance(parsed.get("is_technical_field"), bool):
        return None
    return parsed


async def judge_job(job: dict) -> tuple[dict | None, str | None]:
    prompt = build_prompt(job)
    for provider_name in config.LLM_PROVIDER_ORDER:
        if provider_name in _disabled_providers:
            continue
        caller = _PROVIDER_CALLERS.get(provider_name)
        if caller is None:
            continue
        await _throttle(provider_name)
        text = await caller(prompt)
        if text is None:
            continue
        parsed = _extract_json(text)
        if parsed is None:
            continue
        return parsed, provider_name
    return None, None


def apply_judge_result(job: dict, result: dict) -> None:
    if result.get("is_internship") is False:
        job["gate_dropped"] = True
        job["gate_drop_reason"] = "not_internship_llm"
        return
    if result.get("is_technical_field") is False:
        job["gate_dropped"] = True
        job["gate_drop_reason"] = "not_technical_field_llm"
        return

    cycle_year = result.get("cycle_year")
    if isinstance(cycle_year, int):
        if cycle_year != TARGET_CYCLE_YEAR:
            job["gate_dropped"] = True
            job["gate_drop_reason"] = f"wrong_cycle_{cycle_year}_llm"
            return
        job["cycle_year"] = cycle_year

    job["llm_visa_sponsorship"] = result.get("visa_sponsorship")
    job["llm_level_fit"] = result.get("level_fit")
    job["llm_deadline"] = result.get("deadline")


async def judge_batch(jobs: list[dict]) -> dict:
    reset_provider_state()

    eligible = [
        job for job in jobs
        if not job.get("gate_dropped") and job.get("ambiguity_reasons") and not job.get("judged")
    ]
    to_process = eligible[: config.MAX_LLM_CALLS_PER_RUN]
    skipped_by_cap = eligible[config.MAX_LLM_CALLS_PER_RUN :]

    judged = 0
    provider_usage: dict[str, int] = {}
    semaphore = asyncio.Semaphore(config.JUDGE_CONCURRENCY)

    async def process(job: dict) -> None:
        nonlocal judged
        async with semaphore:
            result, provider = await judge_job(job)
        if result is None:
            return
        apply_judge_result(job, result)
        job["judged"] = True
        judged += 1
        provider_usage[provider] = provider_usage.get(provider, 0) + 1

    if to_process:
        tasks = [asyncio.ensure_future(process(job)) for job in to_process]
        done, pending = await asyncio.wait(tasks, timeout=config.JUDGE_TIME_BUDGET_SECONDS)
        for task in pending:
            task.cancel()

    for job in to_process:
        job["unjudged"] = not job.get("judged")
    for job in skipped_by_cap:
        job["unjudged"] = True

    unjudged_count = sum(1 for job in eligible if job.get("unjudged"))

    return {
        "llm_jobs_queued": len(to_process),
        "llm_judged": judged,
        "llm_unjudged": unjudged_count,
        "llm_provider_usage": provider_usage,
    }
