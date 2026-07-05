"""LLM judge: resolves gate-ambiguous jobs with one structured call each,
falling back across providers on failure. Never drops a job for an
infrastructure problem -- if every provider fails, the job is left
`unjudged` for a retry on a later run.
"""

import asyncio
import json
import os
import re

from src import config
from src.textutil import strip_html

GEMINI_MODEL = "gemini-2.0-flash"
GROQ_MODEL = "llama-3.3-70b-versatile"
TARGET_CYCLE_YEAR = 2027

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

_disabled_providers: set[str] = set()


def reset_provider_state() -> None:
    _disabled_providers.clear()


def _load_prompt_template() -> str:
    return (config.CONFIG_DIR / "prompts.md").read_text(encoding="utf-8")


def build_prompt(job: dict) -> str:
    template = _load_prompt_template()
    description = strip_html(job.get("description_html"))[:4000]
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

    calls_made = 0
    judged = 0
    unjudged = 0
    provider_usage: dict[str, int] = {}

    for job in jobs:
        if job.get("gate_dropped") or not job.get("ambiguity_reasons"):
            continue
        if job.get("judged"):
            continue

        if calls_made >= config.MAX_LLM_CALLS_PER_RUN:
            job["unjudged"] = True
            unjudged += 1
            continue

        calls_made += 1
        result, provider = await judge_job(job)

        if result is None:
            job["unjudged"] = True
            unjudged += 1
            continue

        apply_judge_result(job, result)
        job["judged"] = True
        job["unjudged"] = False
        judged += 1
        provider_usage[provider] = provider_usage.get(provider, 0) + 1

    return {
        "llm_calls_made": calls_made,
        "llm_judged": judged,
        "llm_unjudged": unjudged,
        "llm_provider_usage": provider_usage,
    }
