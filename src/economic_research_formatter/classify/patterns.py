"""Patterns used by the deterministic classifier.

These patterns describe document *shapes* (for example, a chapter heading or
the literal word ``摘要``); they are not journal requirements.  Normative
values remain in ``rules/*.yaml`` and are consumed by the linter.
"""

from __future__ import annotations

import re


CHAPTER_HEADING_RE = re.compile(r"^\s*第\s*(?:[0-9一二三四五六七八九十百零〇两]+)\s*章(?:\s+|[：:、．.]|$)")
CHINESE_LEVEL_1_RE = re.compile(r"^\s*[一二三四五六七八九十百零〇]+[、.．]\s*\S+")
CHINESE_LEVEL_2_RE = re.compile(r"^\s*[（(][一二三四五六七八九十百零〇]+[）)]\s*\S+")
PAREN_LEVEL_RE = re.compile(r"^\s*[（(]\s*\d+\s*[）)]\s*\S+")
DECIMAL_HEADING_RE = re.compile(r"^\s*(\d+(?:\.\d+){1,3})(?:[、.．：:]|\s+)\S+")
ONE_DOT_HEADING_RE = re.compile(r"^\s*\d+[.．]\s+\S+")
FIGURE_CAPTION_RE = re.compile(r"^\s*图\s*\d+(?:[.．]\d+)*(?:\s|[：:、．.]|$)")
TABLE_CAPTION_RE = re.compile(r"^\s*表\s*\d+(?:[.．]\d+)*(?:\s|[：:、．.]|$)")
REFERENCE_HEADING_RE = re.compile(r"^\s*(?:参考文献|references?)\s*$", re.IGNORECASE)
KEYWORDS_RE = re.compile(r"^\s*(?:关键词|关键字|key\s*words?)\s*[：:]?", re.IGNORECASE)
ABSTRACT_RE = re.compile(r"^\s*(?:摘要|abstract)\s*$", re.IGNORECASE)
TITLE_MARKER_RE = re.compile(r"^\s*(?:题目|题名|title)\s*[：:]")
YEAR_RE = re.compile(r"(?:19|20)\d{2}[a-zA-Z]?")
PAGE_RANGE_RE = re.compile(r"\b\d+\s*([\-‐‑‒–—―])\s*\d+\b")
LATIN_OR_DIGIT_RE = re.compile(r"[A-Za-z0-9]")
ZH_NAME_RE = re.compile(r"[\u4e00-\u9fff]{2,4}")


def clean_text(value: object) -> str:
    """Normalize only whitespace that obscures structural matching."""

    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return value.replace("\u00a0", " ").replace("\r\n", "\n").strip()


def lower_style(value: object) -> str:
    return clean_text(value).casefold()


def is_probable_reference_text(text: str) -> bool:
    """Conservative reference-entry signal.

    The classifier does not try to parse bibliographic metadata.  A year plus
    punctuation, a DOI/URL, or an explicit numbering object is sufficient when
    we are already after the reference heading; outside that region this helper
    is intentionally not used to avoid classifying ordinary body prose.
    """

    if not text:
        return False
    if YEAR_RE.search(text) and any(mark in text for mark in (".", "，", ",", "：", ":")):
        return True
    return bool(re.search(r"\b(?:doi|https?://)\b", text, re.IGNORECASE))
