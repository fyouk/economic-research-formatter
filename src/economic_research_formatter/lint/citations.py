"""High-confidence surface checks for in-text citations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

from economic_research_formatter.models.citations import CitationCandidate

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
    rf"(?P<authors>{EN_NAME}(?:\s+(?:and|&)\s+{EN_NAME}|\s+et\s+al\.)?|"
    rf"{ZH_NAME}(?:\s*(?:和|与|及)\s*{ZH_NAME}|等)?)"
    rf"\s*[（(]\s*(?P<content>[^（）()]*?{YEAR_RE.pattern}[^（）()]*)[）)]"
)
_PAGE_ONLY_RE = re.compile(r"^p{1,2}\.?\s*\d+(?:\s*[-—–]\s*\d+)?$", re.IGNORECASE)
_NARRATIVE_PREFIXES = (
    "后文",
    "前文",
    "本文",
    "同时",
    "因此",
    "随后",
    "对于",
    "根据",
    "由于",
    "其中",
    "通过",
    "已有",
    "正文",
    "内容",
    "说明",
    "使用",
    "采用",
    "基于",
    "来自",
    "关于",
    "研究",
    "发现",
    "表明",
)
_NARRATIVE_STOP_SUFFIXES = _ZH_AUTHOR_STOP_SUFFIXES + ("结果", "结论", "说明", "内容", "效应", "影响")
_COMMON_AUTHOR_WORDS = {
    "this",
    "that",
    "data",
    "study",
    "research",
    "sample",
    "result",
    "results",
    "value",
    "some",
    "many",
    "other",
    "one",
    "two",
}


def _author_tokens(value: str) -> tuple[str, ...]:
    """Extract plausible author tokens from the text before a year."""

    value = value.strip(" ，,;；、")
    if not value or _PAGE_ONLY_RE.fullmatch(value):
        return ()
    if re.search(r"[\u4e00-\u9fff]", value):
        tokens: list[str] = []
        for piece in re.split(r"\s*(?:和|与|及|、|，|,)\s*", value):
            piece = re.sub(r"\s*(?:等)\s*$", "", piece.strip())
            if not piece:
                continue
            cjk_tokens = re.findall(r"[\u4e00-\u9fff]{2,4}", piece)
            if cjk_tokens and all(len(token) <= 4 for token in cjk_tokens):
                tokens.extend(cjk_tokens)
        return tuple(tokens)
    return tuple(re.findall(EN_NAME, value))


def _year_and_page(content: str) -> tuple[str | None, str | None]:
    year_match = YEAR_RE.search(content)
    if year_match is None:
        return None, None
    year = year_match.group(0)
    after_year = content[year_match.end() :]
    page_match = re.search(r"(?:\bpp?\.?\s*)?(\d+\s*[-—–]\s*\d+|\d+)", after_year, re.IGNORECASE)
    return year, page_match.group(1) if page_match else None


def _candidate_from_span(
    paragraph: Mapping[str, Any],
    *,
    kind: str,
    start: int,
    end: int,
    authors: tuple[str, ...],
    year: str | None,
    page: str | None,
    confidence: float,
) -> CitationCandidate:
    text = text_of(paragraph)
    return CitationCandidate(
        kind=kind,  # type: ignore[arg-type]
        paragraph_id=str(paragraph.get("id", "")),
        start=start,
        end=end,
        authors=authors,
        year=year,
        page=page,
        raw_preview=text[start:end][:80],
        confidence=confidence,
    )


def _narrative_match_text(text: str, match: re.Match[str]) -> tuple[int, str]:
    """Return the author-start offset and author text for a narrative match."""

    author_text = match.group("authors")
    start = match.start("authors")
    if re.search(r"[\u4e00-\u9fff]", author_text) and not re.search(r"(?:和|与|及)", author_text):
        changed = True
        while changed:
            changed = False
            for prefix in _NARRATIVE_PREFIXES:
                if author_text.startswith(prefix) and len(author_text) > len(prefix) + 1:
                    author_text = author_text[len(prefix) :]
                    start += len(prefix)
                    changed = True
                    break
    return start, author_text


def _narrative_author_tokens(author_text: str) -> tuple[str, ...]:
    if re.search(r"[\u4e00-\u9fff]", author_text):
        if any(author_text.endswith(suffix) for suffix in _NARRATIVE_STOP_SUFFIXES):
            return ()
    authors = _author_tokens(author_text)
    if authors and all(author.casefold() in _COMMON_AUTHOR_WORDS for author in authors):
        return ()
    return authors


def _citation_candidates(paragraph: Mapping[str, Any]) -> list[CitationCandidate]:
    """Parse high-confidence parenthetical and narrative author-year spans."""

    text = text_of(paragraph)
    candidates: list[CitationCandidate] = []
    narrative_spans: list[tuple[int, int]] = []
    for match in _NARRATIVE_AUTHOR_YEAR_RE.finditer(text):
        start, author_text = _narrative_match_text(text, match)
        full_open = text.find("(", match.start(0), match.end(0))
        full_open = full_open if full_open >= 0 else text.find("（", match.start(0), match.end(0))
        if full_open < 0:
            continue
        content = match.group("content")
        content_years = list(YEAR_RE.finditer(content))
        content_before_year = content[: content_years[0].start()] if content_years else content
        # In ``(Smith, 2020)`` the preceding prose may itself end in CJK
        # characters, which can superficially match the narrative pattern.
        # A narrative citation has a year (and optional page) in its
        # parentheses, not a second author token.
        if _author_tokens(content_before_year) and match.group("authors") in _NARRATIVE_PREFIXES:
            continue
        year, page = _year_and_page(content)
        if year is None:
            continue
        authors = _narrative_author_tokens(author_text)
        if not authors:
            continue
        end = match.end(0)
        narrative_spans.append((start, end))
        candidates.append(
            _candidate_from_span(
                paragraph,
                kind="narrative",
                start=start,
                end=end,
                authors=authors,
                year=year,
                page=page,
                confidence=0.96,
            )
        )

    for match in PAREN_RE.finditer(text):
        content = match.group(1)
        if not _looks_like_author_parenthetical(content):
            continue
        if any(match.start() >= start and match.end() <= end for start, end in narrative_spans):
            continue
        year, page = _year_and_page(content)
        if year is None:
            continue
        years = list(YEAR_RE.finditer(content))
        before_year = content[: years[0].start()].strip(" ，,;；、")
        authors = _author_tokens(before_year)
        if not authors:
            continue
        candidates.append(
            _candidate_from_span(
                paragraph,
                kind="parenthetical",
                start=match.start(),
                end=match.end(),
                authors=authors,
                year=year,
                page=page,
                confidence=0.98,
            )
        )

    # Preserve a conservative candidate when a compact preview ends before a
    # closing parenthesis.  There is no reliable narrative span in that case.
    for match in re.finditer(r"[（(]([^（）()]*(?:19|20)\d{2}[^（）()]*)$", text):
        content = match.group(1)
        if not _looks_like_author_parenthetical(content):
            continue
        if any(match.start() >= candidate.start and match.end() <= candidate.end for candidate in candidates):
            continue
        year, page = _year_and_page(content)
        years = list(YEAR_RE.finditer(content))
        before_year = content[: years[0].start()].strip(" ，,;；、") if years else ""
        authors = _author_tokens(before_year)
        if year is None or not authors:
            continue
        candidates.append(
            _candidate_from_span(
                paragraph,
                kind="parenthetical",
                start=match.start(),
                end=len(text),
                authors=authors,
                year=year,
                page=page,
                confidence=0.70,
            )
        )
    return sorted(candidates, key=lambda item: (item.start, item.end, item.kind))


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
        if _citation_candidates(paragraph):
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
    if _PAGE_ONLY_RE.fullmatch(before_year):
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
    authors = _author_tokens(before_year)
    if authors and all(author.casefold() in _COMMON_AUTHOR_WORDS for author in authors):
        return False
    return bool(authors)


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
    pattern = re.compile(rf"({EN_NAME})\s+(and|&)\s+({EN_NAME})", re.IGNORECASE)
    for paragraph in _candidate_paragraphs(ctx):
        for candidate in _citation_candidates(paragraph):
            for match in pattern.finditer(candidate.raw_preview):
                if len(candidate.authors) != 2:
                    continue
                observed_connector = match.group(2)
                observed = {
                    "author_connector": observed_connector,
                    "authors": [match.group(1), match.group(3)],
                    "candidate": candidate.to_dict(),
                }
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
    pattern = re.compile(rf"({ZH_NAME})\s*(和|与)\s*({ZH_NAME})")
    for paragraph in _candidate_paragraphs(ctx):
        for candidate in _citation_candidates(paragraph):
            for match in pattern.finditer(candidate.raw_preview):
                if len(candidate.authors) != 2:
                    continue
                observed_connector = match.group(2)
                observed = {
                    "author_connector": observed_connector,
                    "authors": [match.group(1), match.group(3)],
                    "candidate": candidate.to_dict(),
                }
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
    results: list[dict[str, Any]] = []
    for paragraph in candidates:
        citation_candidates = _citation_candidates(paragraph)
        if citation_candidates:
            results.append(
                finding(
                    rule,
                    "PASS",
                    "引文采用了文内括号或叙述式作者-年份结构。",
                    paragraph=paragraph,
                    observed={
                        "citation_kinds": [candidate.kind for candidate in citation_candidates],
                        "candidates": [candidate.to_dict() for candidate in citation_candidates],
                    },
                )
            )
        else:
            results.append(finding(rule, rule_severity(rule), "引文未形成明确的文内括号或叙述式作者-年份结构。", paragraph=paragraph, observed={"year_present": True}, confidence=0.70))
    footnote_results = _footnote_literature(rule, ctx)
    if results:
        results.extend(item for item in footnote_results if item.get("status") != "NOT_APPLICABLE")
        return results
    return footnote_results


def _general_fields(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    candidates = _candidate_paragraphs(ctx)
    if not candidates:
        return _not_applicable(rule, ctx, "未识别到明确的文内引文候选。")
    results: list[dict[str, Any]] = []
    for paragraph in candidates:
        citation_candidates = _citation_candidates(paragraph)
        valid = any(candidate.authors and candidate.year for candidate in citation_candidates)
        if valid:
            results.append(
                finding(
                    rule,
                    "PASS",
                    "文内引文包含作者与年份字段。",
                    paragraph=paragraph,
                    observed={
                        "author": True,
                        "year": True,
                        "candidates": [candidate.to_dict() for candidate in citation_candidates],
                    },
                )
            )
        else:
            results.append(finding(rule, rule_severity(rule), "文内引文缺少可确定的作者或年份字段。", paragraph=paragraph, observed={"year": bool(YEAR_RE.search(text_of(paragraph)))}))
    return results


def _narrative(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    """Check that narrative author mentions are followed by a year-only group."""

    results: list[dict[str, Any]] = []
    for paragraph in _candidate_paragraphs(ctx):
        for candidate in _citation_candidates(paragraph):
            if candidate.kind != "narrative":
                continue
            raw = candidate.raw_preview
            opening = min((position for position in (raw.find("("), raw.find("（")) if position >= 0), default=-1)
            closing = max(raw.rfind(")"), raw.rfind("）"))
            content = raw[opening + 1 : closing] if opening >= 0 and closing > opening else ""
            years = list(YEAR_RE.finditer(content))
            before_year = content[: years[0].start()] if years else content
            content_authors = _author_tokens(before_year)
            observed = {"candidate": candidate.to_dict(), "parenthetical_year_only": not content_authors}
            if content_authors:
                results.append(finding(rule, rule_severity(rule), "叙述式引文的括号内应仅保留年份及必要页码。", paragraph=paragraph, observed={**observed, "authors_in_parentheses": list(content_authors)}))
            else:
                results.append(finding(rule, "PASS", "叙述式引文的作者位于正文，括号内包含年份。", paragraph=paragraph, observed=observed))
    return results or _not_applicable(rule, ctx, "未识别到明确的叙述式作者-年份引文。")


def _footnote_items(ctx: RuleContext) -> list[dict[str, Any]]:
    """Return actual footnote items while retaining nested paragraph evidence."""

    notes = footnote_info(ctx.inspection)
    values = notes.get("items", [])
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or not values:
        values = notes.get("paragraphs", [])
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        values = []
    context_notes = [item for item in ctx.paragraph_list if item.get("kind") == "footnote"]
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(values):
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        note_id = item.get("id", index)
        context_note = next(
            (note for note in context_notes if str(note.get("footnote_id", note.get("id"))) == str(note_id)),
            None,
        )
        if context_note is not None:
            target_note = context_note
        else:
            target_note = {
                **item,
                "id": str(item.get("source_id") or f"footnote-{note_id}"),
                "source_id": str(item.get("source_id") or f"footnote-{note_id}"),
                "footnote_id": note_id,
                "kind": "footnote",
            }
        nested = item.get("paragraphs", [])
        paragraphs = [dict(paragraph) for paragraph in nested if isinstance(paragraph, Mapping)] if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)) else []
        if not paragraphs:
            paragraphs = [{"id": f"{target_note['id']}-p-0000", "text": text_of(item), "text_preview": text_of(item)[:80]}]
        text = "\n".join(text_of(paragraph) for paragraph in paragraphs).strip()
        if not text:
            text = text_of(item).strip()
        result.append({"item": item, "target": target_note, "paragraphs": paragraphs, "text": text})
    return result


_PURE_LITERATURE_FOOTNOTE_RE = re.compile(
    rf"^[\s\(（]*(?:{EN_NAME}(?:\s+(?:and|&)\s+{EN_NAME})?(?:\s+et\s+al\.)?|"
    rf"{ZH_NAME}(?:\s*(?:和|与|及)\s*{ZH_NAME})?(?:等)?)"
    rf"\s*[,，;；:]\s*{YEAR_RE.pattern}"
    rf"(?:\s*[,，:]\s*(?:pp?\.?\s*)?\d+(?:\s*[-—–]\s*\d+)?)?\s*[\)）\.,。；;]*$",
    re.IGNORECASE,
)
_LITERATURE_HINT_RE = re.compile(
    rf"(?:\b(?:{EN_NAME})(?:\s+(?:and|&)\s+{EN_NAME})?|{ZH_NAME}(?:\s*(?:和|与|及)\s*{ZH_NAME})?)"
    rf"\s*(?:[,，;；:]|\s+)\s*{YEAR_RE.pattern}",
    re.IGNORECASE,
)
_COMMON_NOTE_WORDS = {"this", "that", "see", "note", "sample", "data", "the", "from", "with"}


def _footnote_candidates(note: Mapping[str, Any]) -> list[CitationCandidate]:
    candidates: list[CitationCandidate] = []
    paragraphs = note.get("paragraphs", [])
    if isinstance(paragraphs, Sequence) and not isinstance(paragraphs, (str, bytes)):
        for paragraph in paragraphs:
            if isinstance(paragraph, Mapping):
                candidates.extend(_citation_candidates(paragraph))
    return candidates


def _footnote_semantics(note: Mapping[str, Any]) -> tuple[str, list[CitationCandidate]]:
    text = str(note.get("text", "")).strip()
    candidates = _footnote_candidates(note)
    if _PURE_LITERATURE_FOOTNOTE_RE.fullmatch(text):
        return "pure_literature_index", candidates
    normalized_text = text.strip(" .,。；;")
    if any(candidate.kind == "narrative" and candidate.raw_preview.strip(" .,。；;") == normalized_text for candidate in candidates):
        return "pure_literature_index", candidates
    if candidates:
        return "content_note_with_inline_citation", candidates
    hint = _LITERATURE_HINT_RE.search(text)
    if hint and hint.group(0).split()[0].casefold() not in _COMMON_NOTE_WORDS:
        return "ambiguous_literature", candidates
    return "ordinary_content_note", candidates


def _footnote_finding(
    rule: Mapping[str, Any],
    note: Mapping[str, Any],
    status: str,
    message: str,
    *,
    observed: Mapping[str, Any],
    confidence: float,
) -> dict[str, Any]:
    return finding(
        rule,
        status,
        message,
        paragraph=note["target"],
        observed={**observed, "footnote_paragraph_ids": [paragraph.get("id") for paragraph in note["paragraphs"]]},
        confidence=confidence,
    )


def _footnote_literature(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    info = footnote_info(ctx.inspection)
    count = info.get("actual_count", info.get("count", 0))
    try:
        count = int(count or 0)
    except (TypeError, ValueError):
        count = 0
    if count == 0:
        return _not_applicable(rule, ctx, "文档没有实际脚注。")
    results: list[dict[str, Any]] = []
    for note in _footnote_items(ctx):
        semantics, candidates = _footnote_semantics(note)
        if semantics == "pure_literature_index":
            results.append(
                _footnote_finding(
                    rule,
                    note,
                    rule_severity(rule),
                    "文献引文应采用文内括号形式，不应仅置于脚注。",
                    observed={"footnote_semantics": semantics, "candidates": [candidate.to_dict() for candidate in candidates]},
                    confidence=0.98,
                )
            )
        elif semantics == "ambiguous_literature":
            results.append(
                _footnote_finding(
                    rule,
                    note,
                    "MANUAL_REVIEW",
                    "脚注包含疑似文献作者-年份信号，但语义不足以自动确认。",
                    observed={"footnote_semantics": semantics, "text_preview": note["text"][:80]},
                    confidence=0.55,
                )
            )
    if results:
        return results
    if not _footnote_items(ctx):
        return [finding(rule, "MANUAL_REVIEW", "文档存在实际脚注，但 Inspector 未提供可判断语义的脚注段落证据。", target=ctx.document_target, observed={"actual_footnote_count": count}, confidence=0.40)]
    return _not_applicable(rule, ctx, "未识别到仅以脚注承载的文献引文。")


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
        "ER-CIT-NARRATIVE-001": _narrative,
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
