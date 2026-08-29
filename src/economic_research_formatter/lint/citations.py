"""High-confidence surface checks for in-text citations."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from .common import (
    RuleContext,
    finding,
    footnote_info,
    rule_severity,
    text_of,
)


CITATION_RULE_IDS = {
    "ER-CIT-GENERAL-001",
    "ER-CIT-GENERAL-002",
    "ER-CIT-NARRATIVE-001",
    "ER-CIT-SELF-001",
    "ER-CIT-TITLE-IN-SENTENCE-001",
    "ER-CIT-MULTI-AUTHORSOURCES-001",
    "ER-CIT-SAMEAUTHOR-MULTIYEAR-001",
    "ER-CIT-TRANSLATION-001",
    "ER-CIT-FOREIGN-ORIGINAL-001",
    "ER-CIT-MIXEDLANG-001",
    "ER-CIT-EN-TWOAUTHORS-001",
    "ER-CIT-EN-MULTIAUTHORS-001",
    "ER-CIT-ZH-TWOAUTHORS-001",
    "ER-CIT-ZH-MULTIAUTHORS-001",
    "ER-CIT-REF-FULLAUTHORS-001",
}


YEAR_RE = re.compile(r"(?:19|20)\d{2}[a-zA-Z]?")
PAREN_RE = re.compile(r"[（(]([^（）()]*(?:19|20)\d{2}[^（）()]*)[）)]")
EN_NAME = r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]+"
ZH_NAME = r"[\u4e00-\u9fff]{2,4}"

_ZH_AUTHOR_STOP_SUFFIXES = (
    "本文",
    "本研究",
    "使用",
    "采用",
    "数据",
    "样本",
    "期间",
    "截至",
    "基于",
    "来自",
    "关于",
    "其中",
    "研究",
    "发现",
    "表明",
    "文献",
    "变量",
    "模型",
    "分析",
    "方法",
    "作者",
)
_NARRATIVE_AUTHOR_YEAR_RE = re.compile(
    rf"(?:^|(?<=[，,。；;：:\s]))(?:"
    rf"{EN_NAME}(?:\s+(?:and|&)\s+{EN_NAME})?|"
    rf"{ZH_NAME}(?:\s*(?:和|与|及)\s*{ZH_NAME}|等)?"
    rf")\s*[（(]\s*{YEAR_RE.pattern}"
)


def _candidate_paragraphs(ctx: RuleContext) -> list[dict[str, Any]]:
    excluded = {
        "toc",
        "reference_heading",
        "reference_entry",
        "equation",
        "equation_where_paragraph",
        "table_caption",
        "table_note",
        "author_information",
        "ordinary_footnote",
    }
    result: list[dict[str, Any]] = []
    for paragraph in ctx.paragraph_list:
        role = ctx.role(paragraph)
        if role in excluded or paragraph.get("in_toc"):
            continue
        text = text_of(paragraph)
        if not YEAR_RE.search(text):
            continue
        # A bare year is not sufficient evidence of a literature citation.  A
        # parenthetical with an author token, or a narrative author followed by
        # a year, is required before any rule can inspect it.
        parenthetical = any(_looks_like_author_parenthetical(content) for content, _ in _parentheticals(paragraph))
        narrative = bool(_NARRATIVE_AUTHOR_YEAR_RE.search(text))
        if parenthetical or narrative:
            result.append(paragraph)
    return result


def _looks_like_author_parenthetical(content: str) -> bool:
    content = content.strip()
    if not content:
        return False
    years = list(YEAR_RE.finditer(content))
    if not years:
        return False
    before_year = content[: years[0].start()].strip(" ，,;；、")
    # ``(2020)`` and ``(pp. 10, 2020)`` are not enough.  At least one author
    # character/token is required; punctuation-only strings remain excluded.
    if not before_year or not re.search(r"[A-Za-z\u4e00-\u9fff]", before_year):
        return False
    if any(before_year.endswith(suffix) for suffix in _ZH_AUTHOR_STOP_SUFFIXES):
        return False
    english_tokens = re.findall(EN_NAME, before_year)
    if english_tokens and before_year.casefold().strip(" .,;:") in {"p", "pp", "vol", "no"}:
        return False
    # Chinese author evidence must end in a plausible name token.  This
    # prevents phrases such as ``本文使用2010`` from becoming a citation when
    # they are enclosed in punctuation by the source document.
    chinese_tokens = re.findall(r"[\u4e00-\u9fff]+", before_year)
    if chinese_tokens and not re.search(r"[\u4e00-\u9fff]{2,4}(?:等)?$", before_year):
        return False
    return True


def _parentheticals(paragraph: Mapping[str, Any]) -> list[tuple[str, re.Match[str] | None]]:
    text = text_of(paragraph)
    result = [(match.group(1), match) for match in PAREN_RE.finditer(text) if _looks_like_author_parenthetical(match.group(1))]
    # Default Inspector output intentionally truncates text previews.  A
    # citation can therefore end at the preview boundary before its closing
    # parenthesis.  Preserve that conservative candidate for surface checks;
    # the resulting finding remains explainable as preview-based evidence.
    for match in re.finditer(r"[（(]([^（）()]*(?:19|20)\d{2}[^（）()]*)$", text):
        content = match.group(1)
        if _looks_like_author_parenthetical(content) and all(content != existing for existing, _ in result):
            result.append((content, None))
    return result


def _not_applicable(rule: Mapping[str, Any], ctx: RuleContext, message: str) -> list[dict[str, Any]]:
    return [finding(rule, "NOT_APPLICABLE", message, target=ctx.document_target)]


def _multi_sources(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    requirement = rule.get("requirement", {})
    requirement = requirement if isinstance(requirement, Mapping) else {}
    expected_separator = str(requirement.get("separator", ""))
    results: list[dict[str, Any]] = []
    for paragraph in _candidate_paragraphs(ctx):
        for content, _ in _parentheticals(paragraph):
            if not YEAR_RE.findall(content):
                continue
            has_multiple_years = len(YEAR_RE.findall(content)) >= 2
            if not has_multiple_years:
                continue
            # Two or more year-author pairs are a multi-source candidate.  A
            # semicolon is considered an observed separator only when it is
            # adjacent to a second author/year segment.
            separators = re.findall(r"([;；])", content)
            if not separators:
                continue
            observed_separator = separators[0]
            observed = {"separator": observed_separator, "candidate": content[:80]}
            if observed_separator != expected_separator:
                results.append(finding(rule, rule_severity(rule), "不同作者文献之间的分隔符不符合规则。", paragraph=paragraph, observed=observed))
            else:
                results.append(finding(rule, "PASS", "不同作者文献之间的分隔符符合规则。", paragraph=paragraph, observed=observed))
    return results or _not_applicable(rule, ctx, "未识别到明确的不同作者多来源引文。")


def _en_two_authors(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    requirement = rule.get("requirement", {})
    requirement = requirement if isinstance(requirement, Mapping) else {}
    expected = str(requirement.get("author_connector", ""))
    results: list[dict[str, Any]] = []
    pattern = re.compile(rf"({EN_NAME})\s+(and|&)\s+({EN_NAME})\s*[（(]?\s*(?:19|20)\d{{2}}")
    for paragraph in _candidate_paragraphs(ctx):
        for match in pattern.finditer(text_of(paragraph)):
            observed_connector = match.group(2)
            observed = {"author_connector": observed_connector, "authors": [match.group(1), match.group(3)]}
            if observed_connector != expected:
                results.append(finding(rule, rule_severity(rule), "正文两位英文作者连接词不符合规则。", paragraph=paragraph, observed=observed))
            else:
                results.append(finding(rule, "PASS", "正文两位英文作者连接词符合规则。", paragraph=paragraph, observed=observed))
    return results or _not_applicable(rule, ctx, "未识别到明确的两位英文作者引文。")


def _en_multiple_authors(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    requirement = rule.get("requirement", {})
    requirement = requirement if isinstance(requirement, Mapping) else {}
    expected = str(requirement.get("abbreviation", ""))
    results: list[dict[str, Any]] = []
    # Three explicit English names before a year are a high-confidence
    # multi-author candidate.  We do not infer author count from arbitrary
    # prose or from a reference entry.
    pattern = re.compile(rf"({EN_NAME})\s*(?:,|，)\s*({EN_NAME})\s*(?:,|，|and)\s*({EN_NAME})\s*[（(]?\s*(?:19|20)\d{{2}}")
    for paragraph in _candidate_paragraphs(ctx):
        text = text_of(paragraph)
        for match in pattern.finditer(text):
            span_start, span_end = match.span()
            local = text[span_start:span_end]
            if expected and expected in local:
                results.append(finding(rule, "PASS", "正文多位英文作者缩写符合规则。", paragraph=paragraph, observed={"abbreviation": expected}, confidence=0.88))
            else:
                results.append(finding(rule, rule_severity(rule), "正文三位及以上英文作者未使用规则要求的缩写。", paragraph=paragraph, observed={"candidate": local[:80]}, confidence=0.88))
    # Also inspect the common ``A et al. (year)`` form; it is a positive pass
    # signal, not a reason to flag every English sentence.
    if not results:
        for paragraph in _candidate_paragraphs(ctx):
            if re.search(rf"{EN_NAME}\s+et\s+al\.\s*[（(]?\s*(?:19|20)\d{{2}}", text_of(paragraph), re.IGNORECASE):
                results.append(finding(rule, "PASS", "正文多位英文作者缩写符合规则。", paragraph=paragraph, observed={"abbreviation": expected}, confidence=0.96))
    return results or _not_applicable(rule, ctx, "未识别到明确的三位及以上英文作者引文。")


def _zh_two_authors(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    requirement = rule.get("requirement", {})
    requirement = requirement if isinstance(requirement, Mapping) else {}
    expected = str(requirement.get("author_connector", ""))
    results: list[dict[str, Any]] = []
    pattern = re.compile(rf"({ZH_NAME})\s*(和|与)\s*({ZH_NAME})\s*[（(]?\s*(?:19|20)\d{{2}}")
    for paragraph in _candidate_paragraphs(ctx):
        for match in pattern.finditer(text_of(paragraph)):
            observed_connector = match.group(2)
            observed = {"author_connector": observed_connector, "authors": [match.group(1), match.group(3)]}
            if observed_connector != expected:
                results.append(finding(rule, rule_severity(rule), "正文两位中文作者连接词不符合规则。", paragraph=paragraph, observed=observed))
            else:
                results.append(finding(rule, "PASS", "正文两位中文作者连接词符合规则。", paragraph=paragraph, observed=observed))
    return results or _not_applicable(rule, ctx, "未识别到明确的两位中文作者引文。")


def _zh_multiple_authors(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    requirement = rule.get("requirement", {})
    requirement = requirement if isinstance(requirement, Mapping) else {}
    expected = str(requirement.get("abbreviation", ""))
    results: list[dict[str, Any]] = []
    pattern = re.compile(rf"({ZH_NAME})\s*[、,，]\s*({ZH_NAME})\s*[、,，]\s*({ZH_NAME})\s*(等)?\s*[（(]?\s*(?:19|20)\d{{2}}")
    for paragraph in _candidate_paragraphs(ctx):
        for match in pattern.finditer(text_of(paragraph)):
            observed_abbreviation = match.group(4) or ""
            observed = {"abbreviation": observed_abbreviation, "candidate": match.group(0)[:80]}
            if observed_abbreviation == expected:
                results.append(finding(rule, "PASS", "正文多位中文作者缩写符合规则。", paragraph=paragraph, observed=observed))
            else:
                results.append(finding(rule, rule_severity(rule), "正文三位及以上中文作者未使用规则要求的缩写。", paragraph=paragraph, observed=observed))
    return results or _not_applicable(rule, ctx, "未识别到明确的三位及以上中文作者引文。")


def _full_authors(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    entries = ctx.classified("reference_entry")
    if not entries:
        return _not_applicable(rule, ctx, "未识别到文后参考文献条目。")
    results: list[dict[str, Any]] = []
    for paragraph in entries:
        text = text_of(paragraph)
        match = re.search(r"(?:\bet\s+al\.?\b|等(?:\b|[，,。；;]))", text, re.IGNORECASE)
        if match:
            results.append(finding(rule, rule_severity(rule), "文后参考文献条目仍使用作者缩写，应列出完整作者。", paragraph=paragraph, observed={"abbreviation": match.group(0)}))
        else:
            results.append(finding(rule, "PASS", "文后参考文献条目未检测到作者缩写。", paragraph=paragraph, observed={"full_authors": True}))
    return results


def _general_parenthetical(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    candidates = _candidate_paragraphs(ctx)
    if not candidates:
        return _not_applicable(rule, ctx, "未识别到明确的文内括号引文。")
    results: list[dict[str, Any]] = []
    for paragraph in candidates:
        parentheticals = _parentheticals(paragraph)
        if not parentheticals:
            results.append(finding(rule, rule_severity(rule), "引文未形成明确的文内括号结构。", paragraph=paragraph, observed={"year_present": True}, confidence=0.70))
        else:
            results.append(finding(rule, "PASS", "引文采用了文内括号结构。", paragraph=paragraph, observed={"parenthetical": True}))
    return results


def _general_fields(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    candidates = _candidate_paragraphs(ctx)
    if not candidates:
        return _not_applicable(rule, ctx, "未识别到明确的文内引文候选。")
    results: list[dict[str, Any]] = []
    for paragraph in candidates:
        valid = False
        for content, _ in _parentheticals(paragraph):
            valid = bool(re.search(r"[A-Za-z\u4e00-\u9fff]", content) and YEAR_RE.search(content))
            if valid:
                break
        if valid:
            results.append(finding(rule, "PASS", "文内引文包含作者与年份字段。", paragraph=paragraph, observed={"author": True, "year": True}))
        else:
            results.append(finding(rule, rule_severity(rule), "文内引文缺少可确定的作者或年份字段。", paragraph=paragraph, observed={"year": bool(YEAR_RE.search(text_of(paragraph)))}))
    return results


def _footnote_literature(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    info = footnote_info(ctx.inspection)
    count = info.get("actual_count", info.get("count", 0))
    try:
        count = int(count or 0)
    except (TypeError, ValueError):
        count = 0
    if count == 0:
        return _not_applicable(rule, ctx, "文档没有实际脚注。")
    return [finding(rule, "MANUAL_REVIEW", "脚注中是否包含文献引文需要结合脚注语义人工确认。", target=ctx.document_target, observed={"actual_footnote_count": count}, confidence=0.55)]


def _unsupported(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    candidates = _candidate_paragraphs(ctx)
    if not candidates:
        return _not_applicable(rule, ctx, "未识别到该规则的明确引文目标。")
    return [finding(rule, "NOT_CHECKED", "该引文语义规则尚未实现自动裁决。", paragraph=candidates[0], observed={"candidate_count": len(candidates)}, confidence=0.50)]


def lint_citation_rule(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    handlers = {
        "ER-CIT-GENERAL-001": _general_parenthetical,
        "ER-CIT-GENERAL-002": _general_fields,
        "ER-CIT-MULTI-AUTHORSOURCES-001": _multi_sources,
        "ER-CIT-EN-TWOAUTHORS-001": _en_two_authors,
        "ER-CIT-EN-MULTIAUTHORS-001": _en_multiple_authors,
        "ER-CIT-ZH-TWOAUTHORS-001": _zh_two_authors,
        "ER-CIT-ZH-MULTIAUTHORS-001": _zh_multiple_authors,
        "ER-CIT-REF-FULLAUTHORS-001": _full_authors,
        "ER-CIT-SELF-001": _unsupported,
        "ER-CIT-NARRATIVE-001": _unsupported,
        "ER-CIT-TITLE-IN-SENTENCE-001": _unsupported,
        "ER-CIT-SAMEAUTHOR-MULTIYEAR-001": _unsupported,
        "ER-CIT-TRANSLATION-001": _unsupported,
        "ER-CIT-FOREIGN-ORIGINAL-001": _unsupported,
        "ER-CIT-MIXEDLANG-001": _unsupported,
    }
    handler = handlers.get(str(rule.get("id", "")))
    if handler is None:
        return []
    return handler(rule, ctx)


__all__ = ["CITATION_RULE_IDS", "lint_citation_rule"]
