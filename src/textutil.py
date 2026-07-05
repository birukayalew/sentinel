"""Small text helpers shared by gates, judge prep, and match scoring."""

import html
import re

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def strip_html(raw: str | None) -> str:
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    return _WHITESPACE_RE.sub(" ", text).strip()
