"""High-value branch coverage for the PR1 citation/reference/report paths.

The cases in this module deliberately use the same small Inspector dictionaries
accepted by :func:`lint_inspection` as the consumer-side regressions.  A few
parser tests call a private helper because the structured candidate (span,
page, and confidence) is itself the behavior under test; all rule outcomes go
through the public linter entry point.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from economic_research_formatter.classify.classifier import classify_inspection
from economic_research_formatter.lint.citations import (
    _citation_candidates,
    _footnote_candidates,
    _footnote_items,
    _looks_like_author_parenthetical,
    _year_and_page,
    lint_citation_rule,
)
from economic_research_formatter.lint.common import RuleContext
from economic_research_formatter.lint.engine import lint_inspection
from economic_research_formatter.lint.references import (
    _text_scan_proves_field_semicolons,
    _unquoted_semicolon_positions,
    lint_reference_rule,
)
from economic_research_formatter.lint.registry import rules_for
from economic_research_formatter.report.json_report import (
    serialize_audit,
    write_audit_json,
    write_json_report,
)
from economic_research_formatter.report.markdown_report import (
    render_markdown,
    write_markdown_report,
)


def _inspection(*paragraphs: dict[str, Any], notes: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": "1.0",
        "input": {"filename": "pr1-coverage.docx"},
        "paragraphs": [
            {"id": f"p-{index:06d}", "index": index, **paragraph}
            for index, paragraph in enumerate(paragraphs)
        ],
        "tables": [],
        "images": [],
        "equations": {"omath_count": 0, "paragraph_ids": []},
        "notes": {"footnotes": notes or {"actual_count": 0, "items": []}},
        "fields": {},
    }
    result.update(extra)
    return result


def _rule_findings(audit: dict[str, Any], rule_id: str) -> list[dict[str, Any]]:
    return [item for item in audit["findings"] if item["rule_id"] == rule_id]


def _one_finding(audit: dict[str, Any], rule_id: str) -> dict[str, Any]:
    matches = _rule_findings(audit, rule_id)
    assert matches, f"missing finding for {rule_id}"
    return matches[0]


def _footnotes(*items: dict[str, Any], count: Any | None = None) -> dict[str, Any]:
    return {
        "actual_count": len(items) if count is None else count,
        "items": list(items),
    }


def test_citation_candidates_capture_pages_year_suffixes_and_truncated_previews() -> None:
    paragraph = {
        "id": "p-candidates",
        "text": "前文 (Smith, 2020a, pp. 10-12)；张三（2021，第7页）；Li & Zhou (2022)。",
    }

    candidates = _citation_candidates(paragraph)

    assert [(candidate.kind, candidate.year, candidate.page) for candidate in candidates] == [
        ("parenthetical", "2020a", "10-12"),
        ("narrative", "2021", "7"),
        ("narrative", "2022", None),
    ]
    assert candidates[0].authors == ("Smith",)
    assert candidates[1].authors == ("张三",)
    assert candidates[2].authors == ("Li", "Zhou")
    assert all(candidate.paragraph_id == "p-candidates" for candidate in candidates)

    truncated = _citation_candidates({"id": "p-truncated", "text": "结论见（Smith, 2020"})

    assert len(truncated) == 1
    assert truncated[0].kind == "parenthetical"
    assert truncated[0].confidence == 0.70
    assert truncated[0].end == len("结论见（Smith, 2020")


def test_citation_candidate_parser_rejects_non_author_shapes_and_keeps_real_empty_segments() -> None:
    assert _citation_candidates({"id": "p-empty", "text": "（张三,,李四, 2020）"})[0].authors == ("张三", "李四")

    for text in (
        "（2020）",
        "（pp. 10, 2020）",
        "（PP, 2020）",
        "（本文使用, 2020）",
        "（Data, 2020）",
        "（李A, 2020）",
    ):
        assert _citation_candidates({"id": "p-reject", "text": text}) == []

    assert _year_and_page("没有年份") == (None, None)
    assert _looks_like_author_parenthetical("张三，2020") is True
    assert _looks_like_author_parenthetical("pp. 10，2020") is False


def test_general_citation_rules_ignore_bare_year_prose() -> None:
    audit = lint_inspection(_inspection({"text": "本研究使用2010年至2020年的数据。"}))

    assert _one_finding(audit, "ER-CIT-GENERAL-001")["status"] == "NOT_APPLICABLE"
    assert _one_finding(audit, "ER-CIT-GENERAL-002")["status"] == "NOT_APPLICABLE"


def test_nested_parenthetical_is_not_double_counted_and_multisource_no_separator_is_inapplicable() -> None:
    nested = _citation_candidates({"id": "p-nested", "text": "Smith (Jones, 2020)"})

    assert [(candidate.kind, candidate.authors, candidate.year) for candidate in nested] == [
        ("narrative", ("Smith",), "2020"),
    ]

    audit = lint_inspection(_inspection({"text": "（Smith 2020, Jones 2021）"}))

    assert _one_finding(audit, "ER-CIT-MULTI-AUTHORSOURCES-001")["status"] == "NOT_APPLICABLE"


def test_multisource_and_author_connector_rules_cover_pass_and_error_outcomes() -> None:
    audit = lint_inspection(
        _inspection(
            {"text": "（Smith 2020；Jones 2021）"},
            {"text": "Smith & Jones (2022)"},
            {"text": "Smith and Jones (2023)"},
            {"text": "张三和李四（2024）"},
            {"text": "张三与李四（2025）"},
        )
    )

    assert _one_finding(audit, "ER-CIT-MULTI-AUTHORSOURCES-001")["status"] == "PASS"
    assert {item["status"] for item in _rule_findings(audit, "ER-CIT-EN-TWOAUTHORS-001")} == {"PASS", "ERROR"}
    assert {item["status"] for item in _rule_findings(audit, "ER-CIT-ZH-TWOAUTHORS-001")} == {"PASS", "ERROR"}


def test_multi_author_rules_cover_explicit_and_et_al_forms_and_skip_wrong_author_counts() -> None:
    audit = lint_inspection(
        _inspection(
            {"text": "Wang, Li, Chen (2020)"},
            {"text": "张三、李四、王五 等（2022），另见 Smith (2026)"},
            {"text": "张三、李四、王五（2023）"},
        )
    )

    english = _rule_findings(audit, "ER-CIT-EN-MULTIAUTHORS-001")
    chinese = _rule_findings(audit, "ER-CIT-ZH-MULTIAUTHORS-001")
    assert {item["status"] for item in english} == {"ERROR"}
    assert {item["status"] for item in chinese} == {"PASS", "ERROR"}

    et_al = lint_inspection(_inspection({"text": "Wang et al. (2021)"}))
    et_al_finding = _one_finding(et_al, "ER-CIT-EN-MULTIAUTHORS-001")
    assert et_al_finding["status"] == "PASS"
    assert et_al_finding["observed"]["abbreviation"] == "et al."

    wrong_count = lint_inspection(
        _inspection(
            {"text": "（Smith and Jones and Brown, 2024）"},
            {"text": "（张三和李四和王五，2025）"},
        )
    )
    assert _one_finding(wrong_count, "ER-CIT-EN-TWOAUTHORS-001")["status"] == "NOT_APPLICABLE"
    assert _one_finding(wrong_count, "ER-CIT-ZH-TWOAUTHORS-001")["status"] == "NOT_APPLICABLE"


def test_narrative_rule_rejects_author_inside_parentheses() -> None:
    audit = lint_inspection(_inspection({"text": "张三（Smith, 2020）提出……"}, {"text": "张三（2021）指出……"}))

    findings = _rule_findings(audit, "ER-CIT-NARRATIVE-001")
    assert {item["status"] for item in findings} == {"PASS", "ERROR"}
    error = next(item for item in findings if item["status"] == "ERROR")
    assert error["observed"]["authors_in_parentheses"] == ["Smith"]


def test_footnote_rules_distinguish_content_ambiguous_ordinary_and_missing_evidence() -> None:
    content = lint_inspection(
        _inspection(
            notes=_footnotes(
                {
                    "id": 1,
                    "text": "This note cites Smith (2020).",
                    "paragraphs": [{"id": "fn-1-p", "text": "This note cites Smith (2020)."}],
                }
            )
        )
    )
    assert _one_finding(content, "ER-CIT-GENERAL-001")["status"] == "NOT_APPLICABLE"
    content_finding = _one_finding(content, "ER-REF-CONTENT-FOOTNOTE-001")
    assert content_finding["status"] == "PASS"
    assert content_finding["observed"]["footnote_semantics"] == "content_note_with_inline_citation"

    ambiguous = lint_inspection(
        _inspection(notes=_footnotes({"id": 2, "text": "See Smith, 2020 for details."}))
    )
    assert _one_finding(ambiguous, "ER-CIT-GENERAL-001")["status"] == "MANUAL_REVIEW"
    assert _one_finding(ambiguous, "ER-REF-CONTENT-FOOTNOTE-001")["status"] == "MANUAL_REVIEW"

    ordinary = lint_inspection(
        _inspection(notes=_footnotes({"id": 3, "text": "This explains the sample."}))
    )
    assert _one_finding(ordinary, "ER-CIT-GENERAL-001")["status"] == "NOT_APPLICABLE"
    assert _one_finding(ordinary, "ER-REF-CONTENT-FOOTNOTE-001")["status"] == "NOT_APPLICABLE"

    missing = lint_inspection(_inspection(notes={"actual_count": 1, "items": []}))
    assert _one_finding(missing, "ER-CIT-GENERAL-001")["status"] == "MANUAL_REVIEW"
    assert _one_finding(missing, "ER-REF-CONTENT-FOOTNOTE-001")["status"] == "MANUAL_REVIEW"


def test_malformed_footnote_items_are_conservative_and_nested_candidates_are_filtered() -> None:
    malformed = lint_inspection(_inspection(notes={"actual_count": 1, "items": "not-a-list"}))
    assert _one_finding(malformed, "ER-CIT-GENERAL-001")["status"] == "MANUAL_REVIEW"
    assert _one_finding(malformed, "ER-REF-CONTENT-FOOTNOTE-001")["status"] == "MANUAL_REVIEW"

    note = {"id": 4, "text": "", "paragraphs": [None]}
    context = RuleContext(
        _inspection(notes=_footnotes(note)),
        classify_inspection(_inspection(notes=_footnotes(note))),
        {str(rule["id"]): rule for rule in rules_for()},
    )
    items = _footnote_items(context)
    assert len(items) == 1
    assert items[0]["paragraphs"][0]["text"] == ""
    assert _footnote_candidates({"paragraphs": [None]}) == []


def test_invalid_footnote_count_is_not_treated_as_real_literature_note() -> None:
    audit = lint_inspection(
        _inspection(
            notes={
                "actual_count": "unknown",
                "items": [{"id": 1, "text": "Smith, 2020"}],
            }
        )
    )

    assert _one_finding(audit, "ER-CIT-GENERAL-001")["status"] == "NOT_APPLICABLE"
    assert _one_finding(audit, "ER-REF-CONTENT-FOOTNOTE-001")["status"] == "NOT_APPLICABLE"


def _foreign_entry(text: str, **extra: Any) -> dict[str, Any]:
    return {"text": text, "role_hint": "reference_entry", "language": "foreign", **extra}


def test_foreign_separator_evidence_covers_parsed_pass_error_and_unparsed_title() -> None:
    parsed_pass = lint_inspection(
        _inspection(
            _foreign_entry(
                "Smith, J., 2020, Risk Evidence, Journal.",
                observed_field_separator=[",", "，"],
                fields={"separators": [{"separator": ","}, {"delimiter": ","}]},
            )
        )
    )
    pass_finding = _one_finding(parsed_pass, "ER-REF-FOREIGN-LINE-001")
    assert pass_finding["status"] == "PASS"
    assert pass_finding["observed"]["separator_evidence"] == "inspector_boundaries"

    parsed_error = lint_inspection(
        _inspection(_foreign_entry("Smith; J.; 2020; Risk.", field_boundaries=[{"separator": ";"}]))
    )
    error_finding = _one_finding(parsed_error, "ER-REF-FOREIGN-LINE-001")
    assert error_finding["status"] == "ERROR"
    assert error_finding["observed"]["actual_separator"] == ";"

    text_error = lint_inspection(_inspection(_foreign_entry("Smith; 2020; Risk; Journal.")))
    text_error_finding = _one_finding(text_error, "ER-REF-FOREIGN-LINE-001")
    assert text_error_finding["status"] == "ERROR"
    assert text_error_finding["observed"]["separator_evidence"] == "text_scan"

    quoted_title = lint_inspection(
        _inspection(_foreign_entry("Smith, J., 2020, “Risk; Evidence and Markets”, Journal of Finance."))
    )
    quoted_finding = _one_finding(quoted_title, "ER-REF-FOREIGN-LINE-001")
    assert quoted_finding["status"] == "MANUAL_REVIEW"
    assert quoted_finding["observed"]["actual_separator"] == ","

    comma_prose = lint_inspection(_inspection(_foreign_entry("Smith, J., 2020, Risk Evidence, Journal.")))
    assert _one_finding(comma_prose, "ER-REF-FOREIGN-LINE-001")["status"] == "MANUAL_REVIEW"


def test_foreign_separator_scalar_and_line_split_evidence_are_preserved() -> None:
    scalar = lint_inspection(
        _inspection(_foreign_entry("Smith, J., 2020, Risk.", separator=","))
    )
    scalar_finding = _one_finding(scalar, "ER-REF-FOREIGN-LINE-001")
    assert scalar_finding["status"] == "PASS"
    assert scalar_finding["observed"]["actual_separator"] == ","

    nested_string = lint_inspection(
        _inspection(_foreign_entry("Smith, J., 2020, Risk.", fields={"separators": [",", ","]}))
    )
    assert _one_finding(nested_string, "ER-REF-FOREIGN-LINE-001")["status"] == "PASS"

    split = lint_inspection(_inspection(_foreign_entry("Smith, J., 2020, Risk.", line_count="2")))
    split_finding = _one_finding(split, "ER-REF-FOREIGN-LINE-001")
    assert split_finding["status"] == "ERROR"
    assert split_finding["observed"]["line_count"] == "2"

    newline = lint_inspection(_inspection(_foreign_entry("Smith, J., 2020,\nRisk.")))
    assert _one_finding(newline, "ER-REF-FOREIGN-LINE-001")["status"] == "ERROR"


def test_semicolon_scanner_respects_title_quotes_and_requires_year_bracketing() -> None:
    text = "Smith; 2020; “Risk; Evidence”; Journal"
    positions = _unquoted_semicolon_positions(text)

    assert positions == [5, 11, 29]
    assert _text_scan_proves_field_semicolons(text) is True
    assert _text_scan_proves_field_semicolons("Smith; Journal") is False
    assert _text_scan_proves_field_semicolons("Smith; Journal; no year") is False


def test_language_grouping_reports_violation_pass_and_manual_sort_evidence() -> None:
    violation = lint_inspection(
        _inspection(
            _foreign_entry("Smith, J., 2020, Title."),
            {"text": "张三，2021，中文标题。", "role_hint": "reference_entry", "language": "中文"},
        )
    )
    violation_finding = _one_finding(violation, "ER-REF-LANGUAGE-GROUP-001")
    assert violation_finding["status"] == "ERROR"
    assert violation_finding["observed"]["groups"] == ["foreign", "chinese_including_translations"]

    manual_sort = lint_inspection(
        _inspection(
            {"text": "张三，2021，中文标题。", "role_hint": "reference_entry", "language": "zh"},
            _foreign_entry("Smith, J., 2020, Title."),
        )
    )
    manual_finding = _one_finding(manual_sort, "ER-REF-LANGUAGE-GROUP-001")
    assert manual_finding["status"] == "MANUAL_REVIEW"

    verified = lint_inspection(
        _inspection(
            {"text": "张三，2021，中文标题。", "role_hint": "reference_entry", "language": "中文", "within_group_sorted": True},
            _foreign_entry("Smith, J., 2020, Title.", within_group_sorted=True),
            within_group_sort_verified=[True],
            reference_sort={"sort_verified": True},
            group_sorting={"chinese": True, "foreign": True},
        )
    )
    verified_finding = _one_finding(verified, "ER-REF-LANGUAGE-GROUP-001")
    assert verified_finding["status"] == "PASS"
    assert verified_finding["observed"]["within_group_sort_verified"] is True

    failed_sort = lint_inspection(
        _inspection(
            {"text": "张三，2021，中文标题。", "role_hint": "reference_entry", "language": "中文"},
            _foreign_entry("Smith, J., 2020, Title."),
            reference_sort={"sort_verified": False},
        )
    )
    assert _one_finding(failed_sort, "ER-REF-LANGUAGE-GROUP-001")["status"] == "ERROR"


def test_unsupported_reference_rules_keep_an_entry_as_not_checked() -> None:
    audit = lint_inspection(
        _inspection(
            {"text": "张三，2020，中文标题。", "role_hint": "reference_entry", "language": "中文"}
        )
    )

    for rule_id in (
        "ER-REF-ORDER-FIELDS-001",
        "ER-REF-SORT-001",
        "ER-REF-SAMEYEAR-001",
        "ER-REF-TRANSLATION-001",
    ):
        finding = _one_finding(audit, rule_id)
        assert finding["status"] == "NOT_CHECKED"
        assert finding["observed"]["entry_count"] == 1


def test_unknown_rule_handlers_remain_empty() -> None:
    inspection = _inspection({"text": "张三，2020，中文标题。", "role_hint": "reference_entry"})
    classification = classify_inspection(inspection)
    rules = {str(rule["id"]): rule for rule in rules_for()}
    context = RuleContext(inspection, classification, rules)

    assert lint_reference_rule({"id": "ER-REF-UNKNOWN"}, context) == []
    assert lint_citation_rule({"id": "ER-CIT-UNKNOWN"}, context) == []


def _report_audit() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "input": {"filename": "audit\\draft|v1\n.md"},
        "findings": [
            {
                "rule_id": "ER-REPORT-001",
                "status": "ERROR",
                "message": "bad | value <tag> `code` *emphasis* _under_ [link] (x) #hash ! bang ~tilde",
                "target": {"kind": "paragraph", "id": f"p-{index}", "index": index},
                "observed": {},
                "expected": {},
                "source": {},
                "confidence": 1.0,
            }
            for index in range(4)
        ],
        "capabilities": {
            "implemented": "ER-REPORT-001",
            "manual_review": None,
            "not_checked": 0,
            "not_applicable": [],
        },
    }


def test_json_report_writer_and_alias_round_trip_utf8(tmp_path: Path) -> None:
    audit = _report_audit()
    output = tmp_path / "nested" / "audit.json"

    returned = write_json_report(audit, output)

    assert returned == output
    assert output.exists()
    assert json.loads(output.read_text(encoding="utf-8")) == audit
    alias_output = tmp_path / "alias" / "audit.json"
    assert write_audit_json(audit, alias_output) == alias_output
    assert serialize_audit(audit).endswith("\n")


def test_json_and_markdown_renderers_reject_non_mapping_inputs() -> None:
    with pytest.raises(TypeError, match="audit must be a mapping"):
        serialize_audit([])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="audit must be a mapping"):
        render_markdown([])  # type: ignore[arg-type]


def test_markdown_falls_back_to_summary_aggregates_and_escapes_user_values(tmp_path: Path) -> None:
    audit = _report_audit()

    markdown = render_markdown(audit)

    assert "共记录 4 条规则结果，涉及 4 个受影响目标。" in markdown
    assert "ER-REPORT-001" in markdown
    assert "p-0, p-1, p-2" in markdown
    assert "audit\\\\draft\\|v1 .md" in markdown
    assert "bad \\| value &lt;tag&gt; \\`code\\` \\*emphasis\\*" in markdown
    assert markdown.count("bad \\| value") == 1
    assert "ER-REPORT-001" in markdown
    assert "已实现检查：ER-REPORT-001" in markdown
    assert "人工复核：无" in markdown

    output = tmp_path / "reports" / "audit.md"
    assert write_markdown_report(audit, output) == output
    assert output.read_text(encoding="utf-8") == markdown


def test_markdown_skips_invalid_aggregates_and_supports_explicit_examples_and_empty_shapes() -> None:
    audit = _report_audit()
    audit["summary"] = {
        "total_findings": 1,
        "by_status": {"ERROR": 1},
        "aggregates": [
            None,
            {
                "rule_id": "ER-EXPLICIT-001",
                "status": "PASS",
                "count": 1,
                "message": "explicit",
                "examples": [None, {}, {"id": "kept"}],
            },
        ],
    }
    audit["findings"] = []
    audit["capabilities"] = {"implemented": [], "manual_review": [], "not_checked": [], "not_applicable": "N/A"}

    markdown = render_markdown(audit)

    assert "ER-EXPLICIT-001" in markdown
    assert "kept" in markdown
    assert "| - | - | 0 | 没有规则结果 | - |" not in markdown
    assert "不适用：N/A" in markdown

    audit["summary"]["aggregates"] = "invalid"
    fallback_markdown = render_markdown(audit)
    assert "| - | - | 0 | 0 | 没有规则结果 | - |" in fallback_markdown
