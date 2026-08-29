"""High-confidence surface checks for in-text citations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

from economic_research_formatter.models.citations import CitationCandidate

from .common import (
    RuleContext,
    finding,
    note_info,
    note_linkage_issues,
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
_PAGE_NUMBER = r"\d+(?:\s*[-—–]\s*\d+)?"
_NARRATIVE_YEAR_ONLY_RE = re.compile(
    rf"^\s*(?P<year>{YEAR_RE.pattern})"
    rf"(?:\s*[,，:：]\s*(?:(?:pp?\.?)\s*|第\s*)?"
    rf"(?P<page>{_PAGE_NUMBER})\s*(?:页)?)?\s*$",
    re.IGNORECASE,
)
_MONTH_NAME = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)"
)
_MONTH_YEAR_RE = re.compile(
    rf"^\s*(?:{_MONTH_NAME}\.?(?:\s*,)?\s+"
    rf"(?:\d{{1,2}}\s*,?\s*)?|\d{{1,2}}\s+{_MONTH_NAME}\.?\s+)"
    rf"(?:19|20)\d{{2}}\s*$",
    re.IGNORECASE,
)
_ZH_SINGLE_SURNAMES = frozenset(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏窦章苏潘葛范彭郎鲁韦昌马苗方俞任袁柳鲍史唐费廉岑薛雷"
    "贺倪汤滕殷罗毕郝邬安常乐于傅卞齐康伍余顾孟平黄穆萧尹姚邵汪祁毛"
    "米贝臧戴宋熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路江童颜郭梅林钟徐"
    "邱骆高夏蔡田樊胡凌霍万柯卢莫解应宗丁宣邓郁单杭洪包左石崔吉龚程"
    "裴陆荣翁荀甄曲封储靳井段富巫乌焦侯全班秋仲伊宫宁仇栾甘祖武符刘"
    "景詹龙叶黎乔闻党翟谭贡劳姬申冉宰雍桑桂牛寿边温庄晏柴瞿阎连艾向"
    "古易慎廖耿匡文寇广聂辛简饶曾沙关查游权益尚琚"
)
_ZH_COMPOUND_SURNAMES = (
    "欧阳",
    "司马",
    "上官",
    "诸葛",
    "东方",
    "皇甫",
    "尉迟",
    "公孙",
    "慕容",
    "宇文",
    "长孙",
    "司徒",
    "司空",
    "令狐",
    "夏侯",
    "钟离",
    "南宫",
)
_ZH_NARRATIVE_NON_NAMES = frozenset({"何种", "何时", "高于", "曾在"})

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
    rf"(?:参考\s*)?(?P<authors>{EN_NAME}(?:\s+(?:and|&)\s+{EN_NAME}|\s+et\s+al\.)?|"
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
            elif re.fullmatch(rf"{EN_NAME}(?:\s+et\s+al\.)?", piece, re.IGNORECASE):
                tokens.extend(re.findall(EN_NAME, piece))
        return tuple(tokens)
    return tuple(re.findall(EN_NAME, value))


def _year_and_page(content: str) -> tuple[str | None, str | None]:
    year_match = YEAR_RE.search(content)
    if year_match is None:
        return None, None
    year = year_match.group(0)
    after_year = content[year_match.end() :]
    explicit_page_match = re.search(
        rf"(?:\bpp?\.?\s*|第\s*)({_PAGE_NUMBER})\s*(?:页)?",
        after_year,
        re.IGNORECASE,
    )
    next_year = YEAR_RE.search(after_year)
    if explicit_page_match is not None and (
        next_year is None or explicit_page_match.start() < next_year.start()
    ):
        return year, explicit_page_match.group(1)
    page_search_area = after_year[: next_year.start()] if next_year is not None else after_year
    page_match = re.search(rf"({_PAGE_NUMBER})", page_search_area)
    return year, page_match.group(1) if page_match else None


def _narrative_year_and_page(content: str) -> tuple[str | None, str | None]:
    """Return fields only when narrative parentheses contain year/page data."""

    if _NARRATIVE_YEAR_ONLY_RE.fullmatch(content) is None:
        return None, None
    return _year_and_page(content)


def _spans_overlap(start: int, end: int, other_start: int, other_end: int) -> bool:
    return start < other_end and other_start < end


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


def _is_high_confidence_zh_name(value: str) -> bool:
    """Return whether a compact CJK token has positive personal-name evidence."""

    value = value.strip()
    if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", value) is None:
        return False
    if value in _ZH_NARRATIVE_NON_NAMES:
        return False
    for surname in _ZH_COMPOUND_SURNAMES:
        if value.startswith(surname):
            return len(value) in {3, 4}
    return len(value) in {2, 3} and value[0] in _ZH_SINGLE_SURNAMES


def _narrative_author_tokens(author_text: str) -> tuple[str, ...]:
    if re.search(r"[\u4e00-\u9fff]", author_text):
        if any(author_text.endswith(suffix) for suffix in _NARRATIVE_STOP_SUFFIXES):
            return ()
        normalized = re.sub(r"\s*等\s*$", "", author_text.strip())
        name_parts = re.split(r"\s*(?:和|与|及)\s*", normalized)
        if not name_parts or not all(_is_high_confidence_zh_name(part) for part in name_parts):
            return ()
    authors = _author_tokens(author_text)
    if authors and all(author.casefold() in _COMMON_AUTHOR_WORDS for author in authors):
        return ()
    return authors


def _citation_candidates(paragraph: Mapping[str, Any]) -> list[CitationCandidate]:
    """Parse high-confidence parenthetical and narrative author-year spans."""

    text = text_of(paragraph)
    candidates: list[CitationCandidate] = []
    parenthetical_spans: list[tuple[int, int]] = []

    # Bracket contents are authoritative for candidate arbitration.  When an
    # enclosed span already contains an author and year it is parenthetical;
    # nearby prose must not be promoted to a narrative author and then hide the
    # more precise bracket target.
    for match in PAREN_RE.finditer(text):
        content = match.group(1)
        if not _looks_like_author_parenthetical(content):
            continue
        year, page = _year_and_page(content)
        if year is None:
            continue
        years = list(YEAR_RE.finditer(content))
        before_year = content[: years[0].start()].strip(" ，,;；、")
        authors = _author_tokens(before_year)
        if not authors:
            continue
        parenthetical_spans.append((match.start(), match.end()))
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

    for match in _NARRATIVE_AUTHOR_YEAR_RE.finditer(text):
        start, author_text = _narrative_match_text(text, match)
        full_open = text.find("(", match.start(0), match.end(0))
        full_open = full_open if full_open >= 0 else text.find("（", match.start(0), match.end(0))
        if full_open < 0:
            continue
        bracket_end = match.end(0)
        if any(
            _spans_overlap(full_open, bracket_end, span_start, span_end)
            for span_start, span_end in parenthetical_spans
        ):
            continue
        content = match.group("content")
        year, page = _narrative_year_and_page(content)
        if year is None:
            continue
        authors = _narrative_author_tokens(author_text)
        if not authors:
            continue
        end = match.end(0)
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

    # Preserve a conservative candidate when a compact preview ends before a
    # closing parenthesis.  There is no reliable narrative span in that case.
    for match in re.finditer(r"[（(]([^（）()]*(?:19|20)\d{2}[^（）()]*)$", text):
        content = match.group(1)
        if not _looks_like_author_parenthetical(content):
            continue
        if any(
            _spans_overlap(match.start(), match.end(), candidate.start, candidate.end)
            for candidate in candidates
        ):
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
        "endnote",
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
    if _MONTH_YEAR_RE.fullmatch(content):
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
    if chinese_tokens and not (
        re.search(r"[\u4e00-\u9fff]{2,4}(?:等)?$", before_year)
        or re.search(rf"{EN_NAME}(?:\s+et\s+al\.)?\s*等$", before_year, re.IGNORECASE)
    ):
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
    """Return actual footnote/endnote items with their original kind.

    The historical helper name is retained because ``references.py`` imports
    it, but its contract is now the shared note abstraction used by both
    citation and reference rules.
    """

    result: list[dict[str, Any]] = []
    for note in ctx.paragraph_list:
        kind = str(note.get("kind", "")).casefold()
        if kind not in {"footnote", "endnote"}:
            continue
        item = dict(note)
        target = item.get("target")
        if not isinstance(target, Mapping):
            note_id = item.get(f"{kind}_id", item.get("note_id"))
            target = {
                "kind": kind,
                "id": str(item.get("source_id") or item.get("id") or f"{kind}-{note_id}"),
                "source_id": str(item.get("source_id") or item.get("id") or f"{kind}-{note_id}"),
                "note_id": note_id,
                f"{kind}_id": note_id,
                "index": item.get("index"),
                "text_preview": text_of(item)[:80],
            }
        nested = item.get("paragraphs", [])
        paragraphs = (
            [dict(paragraph) for paragraph in nested if isinstance(paragraph, Mapping)]
            if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes))
            else []
        )
        if not paragraphs:
            paragraphs = [{"id": f"{target.get('id', kind)}-p-0000", "text": text_of(item), "text_preview": text_of(item)[:80]}]
        text = "\n".join(text_of(paragraph) for paragraph in paragraphs).strip()
        if not text:
            text = text_of(item).strip()
        result.append(
            {
                "item": item,
                "target": dict(target),
                "kind": kind,
                "note_id": item.get(f"{kind}_id", item.get("note_id")),
                "text_length": item.get("text_length"),
                "text_truncated": bool(
                    item.get("text_truncated")
                    or any(
                        isinstance(paragraph, Mapping) and paragraph.get("text_truncated")
                        for paragraph in paragraphs
                    )
                ),
                "preview_only": bool(
                    item.get("preview_only")
                    or any(
                        isinstance(paragraph, Mapping) and paragraph.get("preview_only")
                        for paragraph in paragraphs
                    )
                ),
                "paragraphs": paragraphs,
                "text": text,
            }
        )
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
    if _preview_only_truncated(note):
        return "insufficient_evidence", candidates
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


def _preview_only_truncated(note: Mapping[str, Any]) -> bool:
    """Return whether the note text evidence hides content after the preview."""

    values: list[Mapping[str, Any]] = [note]
    for key in ("item", "target"):
        value = note.get(key)
        if isinstance(value, Mapping):
            values.append(value)
    paragraphs = note.get("paragraphs", [])
    if isinstance(paragraphs, Sequence) and not isinstance(paragraphs, (str, bytes)):
        values.extend(value for value in paragraphs if isinstance(value, Mapping))
    for value in values:
        if not value.get("preview_only"):
            continue
        if value.get("text_truncated") is True:
            return True
        text_length = value.get("text_length")
        preview = value.get("text_preview")
        try:
            if text_length is not None and isinstance(preview, str) and int(text_length) > len(preview):
                return True
        except (TypeError, ValueError):
            continue
    return False


def _footnote_finding(
    rule: Mapping[str, Any],
    note: Mapping[str, Any],
    status: str,
    message: str,
    *,
    observed: Mapping[str, Any],
    confidence: float,
) -> dict[str, Any]:
    target = note.get("target")
    target = target if isinstance(target, Mapping) else {}
    item = note.get("item")
    item = item if isinstance(item, Mapping) else {}
    kind = str(note.get("kind") or target.get("kind") or item.get("kind") or "footnote").casefold()
    if kind not in {"footnote", "endnote"}:
        kind = "footnote"
    note_observed = dict(observed)
    if kind == "footnote":
        note_observed.setdefault("footnote_semantics", observed.get("footnote_semantics"))
        note_observed.setdefault("footnote_paragraph_ids", [paragraph.get("id") for paragraph in note["paragraphs"]])
    else:
        note_observed.pop("footnote_semantics", None)
        note_observed.pop("footnote_paragraph_ids", None)
        note_observed.setdefault("endnote_semantics", observed.get("endnote_semantics", observed.get("footnote_semantics")))
        note_observed.setdefault("endnote_paragraph_ids", [paragraph.get("id") for paragraph in note["paragraphs"]])
    return finding(
        rule,
        status,
        message,
        paragraph=note["target"],
        target=note["target"],
        observed=note_observed,
        confidence=confidence,
    )


def _footnote_literature(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    count = 0
    for kind in ("footnote", "endnote"):
        info = note_info(ctx.inspection, kind)
        value = info.get("actual_count", info.get("count", 0))
        try:
            count += int(value or 0)
        except (TypeError, ValueError):
            continue
    linkage_issues = note_linkage_issues(ctx.inspection)
    if count == 0 and linkage_issues:
        return [
            finding(
                rule,
                "MANUAL_REVIEW",
                "正文注释引用与脚注或尾注定义无法一一对应。",
                target=ctx.document_target,
                observed={"note_linkage_issues": linkage_issues},
                confidence=0.40,
            )
        ]
    if count == 0:
        return _not_applicable(rule, ctx, "文档没有实际脚注或尾注。")
    results: list[dict[str, Any]] = []
    if linkage_issues:
        results.append(
            finding(
                rule,
                "MANUAL_REVIEW",
                "脚注或尾注定义与正文引用无法一一对应；仅对已绑定注释继续检查。",
                target=ctx.document_target,
                observed={"note_linkage_issues": linkage_issues},
                confidence=0.40,
            )
        )
    for note in _footnote_items(ctx):
        semantics, candidates = _footnote_semantics(note)
        if semantics == "pure_literature_index":
            results.append(
                _footnote_finding(
                    rule,
                    note,
                    rule_severity(rule),
                    "文献引文应采用文内括号形式，不应仅置于脚注或尾注。",
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
                    "脚注或尾注包含疑似文献作者-年份信号，但语义不足以自动确认。",
                    observed={"footnote_semantics": semantics, "text_preview": note["text"][:80]},
                    confidence=0.55,
                )
            )
        elif semantics == "insufficient_evidence":
            results.append(
                _footnote_finding(
                    rule,
                    note,
                    "MANUAL_REVIEW",
                    "脚注或尾注仅提供截断预览，无法排除预览范围外的文献引文。",
                    observed={
                        "footnote_semantics": semantics,
                        "text_preview": note["text"][:80],
                        "preview_only": True,
                        "text_truncated": True,
                    },
                    confidence=0.40,
                )
            )
    if results:
        return results
    if not _footnote_items(ctx):
        return [finding(rule, "MANUAL_REVIEW", "文档存在实际脚注或尾注，但 Inspector 未提供可判断语义的注释段落证据。", target=ctx.document_target, observed={"actual_note_count": count}, confidence=0.40)]
    return _not_applicable(rule, ctx, "未识别到仅以脚注或尾注承载的文献引文。")


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
