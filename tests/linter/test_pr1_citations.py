from __future__ import annotations

from pathlib import Path

import pytest

from economic_research_formatter.docx.inspector import inspect_docx
from economic_research_formatter.lint.engine import lint_inspection

from linter.pr1_citation_docx import make_footnote_docx


def _inspection(*texts: str) -> dict:
    return {
        "schema_version": "1.0",
        "input": {"filename": "pr1-citations.docx"},
        "paragraphs": [
            {"id": f"p-{index:06d}", "index": index, "text": text}
            for index, text in enumerate(texts)
        ],
        "tables": [],
        "images": [],
        "equations": {"omath_count": 0, "paragraph_ids": []},
        "notes": {"footnotes": {"actual_count": 0, "items": []}},
        "fields": {},
    }


def _statuses(audit: dict, rule_id: str) -> set[str]:
    return {item["status"] for item in audit["findings"] if item["rule_id"] == rule_id}


def _finding(audit: dict, rule_id: str, *, target_kind: str | None = None) -> dict:
    matches = [
        item
        for item in audit["findings"]
        if item["rule_id"] == rule_id
        and (target_kind is None or item["target"].get("kind") == target_kind)
    ]
    assert matches, f"missing {rule_id} finding"
    return matches[0]


def test_narrative_author_year_is_valid_for_general_rules_and_narrative_rule() -> None:
    audit = lint_inspection(
        _inspection(
            "张三（2020）指出……",
            "Smith (2020) argues……",
        )
    )

    assert _statuses(audit, "ER-CIT-GENERAL-001") == {"PASS"}
    assert _statuses(audit, "ER-CIT-GENERAL-002") == {"PASS"}
    assert _statuses(audit, "ER-CIT-NARRATIVE-001") == {"PASS"}


def test_two_author_connector_rules_still_flag_and_and_yu_independently() -> None:
    audit = lint_inspection(
        _inspection(
            "Smith and Jones (2020) argue……",
            "何威风与刘启亮（2010）指出……",
        )
    )

    assert _statuses(audit, "ER-CIT-EN-TWOAUTHORS-001") == {"ERROR"}
    assert _statuses(audit, "ER-CIT-ZH-TWOAUTHORS-001") == {"ERROR"}


def test_supported_citation_forms_are_candidates_but_ordinary_year_ranges_are_not() -> None:
    from economic_research_formatter.lint.citations import _citation_candidates

    forms = (
        ("张三（2020）指出……", "narrative"),
        ("Smith (2020) argues……", "narrative"),
        ("Smith & Jones (2020) argue……", "narrative"),
        ("Smith and Jones (2020) argue……", "narrative"),
        ("（张三，2020）", "parenthetical"),
        ("(Smith, 2020)", "parenthetical"),
    )
    for index, (text, expected_kind) in enumerate(forms):
        candidates = _citation_candidates({"id": f"p-{index:06d}", "text": text})
        assert len(candidates) == 1
        assert candidates[0].kind == expected_kind
        assert candidates[0].year == "2020"

    assert _citation_candidates({"id": "p-range", "text": "本文使用2010年至2020年的数据。"}) == []
    assert _citation_candidates({"id": "p-year", "text": "本期数据截至2020年。"}) == []


def test_parenthetical_content_wins_before_narrative_author_arbitration() -> None:
    from economic_research_formatter.lint.citations import _citation_candidates

    cases = (
        ("研究工作（Foxman, 2016）。", "parenthetical"),
        ("理论框架（Akerlof，1978）。", "parenthetical"),
        ("相关信息（Brown et al.，2025）。", "parenthetical"),
        ("减值测试等（秦荣生，2025）。", "parenthetical"),
        ("诉讼风险（姜涛和尚鼎，2020）。", "parenthetical"),
        ("Abis and Veldkamp (2022) 指出……", "narrative"),
        ("Cao et al.（2023）发现……", "narrative"),
        ("张三（2020）指出……", "narrative"),
    )

    for index, (text, expected_kind) in enumerate(cases):
        candidates = _citation_candidates({"id": f"p-arbitration-{index}", "text": text})
        assert len(candidates) == 1
        assert candidates[0].kind == expected_kind

    for text in (
        "会议时间 (September 2002)。",
        "会议时间 (9 September 2002)。",
        "会议时间 (September 9, 2002)。",
    ):
        assert _citation_candidates({"id": "p-month", "text": text}) == []

    multisource = _citation_candidates(
        {
            "id": "p-multisource",
            "text": "错报迹象(Dechow et al., 2011；Kothari et al., 2005)。",
        }
    )
    assert len(multisource) == 1
    assert multisource[0].kind == "parenthetical"
    assert multisource[0].page is None

    later_source_page = _citation_candidates(
        {
            "id": "p-multisource-page",
            "text": "(Dechow et al., 2011；Kothari et al., 2005, p. 9)",
        }
    )
    assert len(later_source_page) == 1
    assert later_source_page[0].kind == "parenthetical"
    assert later_source_page[0].year == "2011"
    assert later_source_page[0].page is None


@pytest.mark.parametrize(
    ("text", "expected_year", "expected_page"),
    [
        ("张三（2020）", "2020", None),
        ("张三（2020a）", "2020a", None),
        ("Smith (2020, p. 15)", "2020", "15"),
        ("张三（2020：15）", "2020", "15"),
    ],
)
def test_narrative_parentheses_require_year_only_semantics(
    text: str,
    expected_year: str,
    expected_page: str | None,
) -> None:
    from economic_research_formatter.lint.citations import _citation_candidates

    candidates = _citation_candidates({"id": "p-year-only", "text": text})

    assert len(candidates) == 1
    assert candidates[0].kind == "narrative"
    assert candidates[0].year == expected_year
    assert candidates[0].page == expected_page


def test_parenthetical_examples_never_become_narrative_rule_targets() -> None:
    texts = (
        "研究工作（Foxman, 2016）。",
        "理论框架（Akerlof，1978）。",
        "相关信息（Brown et al.，2025）。",
        "减值测试等（秦荣生，2025）。",
        "诉讼风险（姜涛和尚鼎，2020）。",
        "Abis and Veldkamp (2022) 指出……",
        "Cao et al.（2023）发现……",
        "张三（2020）指出……",
    )
    audit = lint_inspection(_inspection(*texts))
    narrative = [
        item for item in audit["findings"]
        if item["rule_id"] == "ER-CIT-NARRATIVE-001"
    ]

    assert {item["status"] for item in narrative} == {"PASS"}
    assert {item["target"]["id"] for item in narrative} == {
        "p-000005",
        "p-000006",
        "p-000007",
    }
    assert _statuses(audit, "ER-CIT-GENERAL-001") == {"PASS"}
    assert _statuses(audit, "ER-CIT-GENERAL-002") == {"PASS"}
    assert _statuses(audit, "ER-CIT-EN-TWOAUTHORS-001") == {"ERROR"}


def test_narrative_parser_does_not_promote_prose_labels_to_authors() -> None:
    from economic_research_formatter.lint.citations import _citation_candidates

    for text in (
        "研究结果（2020）显示……",
        "公司成立（2020）后……",
        "收入增长（2020）较快……",
        "市场规模（2020）扩大……",
        "财政年度（2020）结束……",
        "治理水平（2020）提高……",
        "研究工作（2020）继续……",
        "理论框架（2020）如下……",
        "何种（2020）情况……",
        "何时（2020）发生……",
        "高于（2020）水平……",
        "曾在（2020）出现……",
    ):
        assert _citation_candidates({"id": "p-prose-label", "text": text}) == []
    assert _citation_candidates({"id": "p-body", "text": "本文使用张三（2020）的数据。"})[0].authors == ("张三",)
    assert _citation_candidates({"id": "p-two", "text": "何威风与刘启亮（2010）指出……"})[0].authors == (
        "何威风",
        "刘启亮",
    )
    assert _citation_candidates({"id": "p-english", "text": "This (2020) is a result."}) == []


def test_narrative_only_footnote_is_still_a_pure_literature_index(tmp_path: Path) -> None:
    inspection = inspect_docx(make_footnote_docx(tmp_path, "Smith (2020)", "narrative-footnote.docx"), include_text=True)

    finding = _finding(lint_inspection(inspection), "ER-CIT-GENERAL-001", target_kind="footnote")

    assert finding["status"] == "ERROR"
    assert finding["observed"]["footnote_semantics"] == "pure_literature_index"


def test_parenthetical_and_narrative_candidates_keep_structured_spans_and_years() -> None:
    from economic_research_formatter.lint import citations

    paragraph = {"id": "p-000000", "text": "前文 (Smith, 2020)，后文张三（2021）以及 2019-2020。"}
    candidate_parser = getattr(citations, "_citation_candidates", None)
    assert candidate_parser is not None, "citation candidate parser is not implemented"
    candidates = candidate_parser(paragraph)

    assert [candidate.kind for candidate in candidates] == ["parenthetical", "narrative"]
    assert all(candidate.paragraph_id == "p-000000" for candidate in candidates)
    assert [(candidate.start, candidate.end) for candidate in candidates] == [(3, 16), (19, 27)]
    assert [candidate.year for candidate in candidates] == ["2020", "2021"]
    assert all(len(candidate.raw_preview) <= 80 for candidate in candidates)


def test_real_footnote_literature_is_error_but_content_and_ordinary_notes_are_not_false_positive(
    tmp_path: Path,
) -> None:
    ordinary = inspect_docx(make_footnote_docx(tmp_path, "This explains the sample.", "ordinary.docx"), include_text=True)
    literature = inspect_docx(make_footnote_docx(tmp_path, "Smith, 2020", "literature.docx"), include_text=True)
    content = inspect_docx(
        make_footnote_docx(tmp_path, "This note explains the sample (Smith, 2020).", "content.docx"),
        include_text=True,
    )
    no_note = inspect_docx(make_footnote_docx(tmp_path, None, "none.docx"), include_text=True)

    assert literature["notes"]["footnotes"]["items"][0]["paragraphs"][0]["text"] == "Smith, 2020"
    assert _finding(lint_inspection(literature), "ER-CIT-GENERAL-001", target_kind="footnote")["status"] == "ERROR"
    assert _statuses(lint_inspection(ordinary), "ER-CIT-GENERAL-001") == {"NOT_APPLICABLE"}
    assert _statuses(lint_inspection(ordinary), "ER-REF-CONTENT-FOOTNOTE-001") == {"NOT_APPLICABLE"}
    assert _statuses(lint_inspection(content), "ER-CIT-GENERAL-001") == {"NOT_APPLICABLE"}
    assert _statuses(lint_inspection(content), "ER-REF-CONTENT-FOOTNOTE-001") == {"PASS"}
    assert _statuses(lint_inspection(no_note), "ER-CIT-GENERAL-001") == {"NOT_APPLICABLE"}


def test_foreign_title_semicolon_without_parsed_boundaries_requires_manual_review() -> None:
    inspection = _inspection(
        "参考文献",
        "Smith, J., 2020, “Risk; Evidence and Markets”, Journal of Finance.",
    )
    inspection["paragraphs"][1]["in_reference"] = True
    inspection["paragraphs"][1]["language"] = "foreign"

    finding = _finding(lint_inspection(inspection), "ER-REF-FOREIGN-LINE-001")

    assert finding["status"] == "MANUAL_REVIEW"
    assert finding["observed"]["separator_evidence"] == "text_scan"
