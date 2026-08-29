from __future__ import annotations

from pathlib import Path

import pytest

from economic_research_formatter.classify.classifier import classify_inspection
from economic_research_formatter.docx.inspector import inspect_docx
from economic_research_formatter.lint.engine import lint_inspection

from inspector.pr2_numbering_docx import (
    abstract_num,
    level,
    level_override,
    num,
    paragraph,
    paragraph_style,
    run,
    start_override,
    write_numbering_docx,
)


def _chain(path: Path) -> tuple[dict, dict, dict]:
    """Exercise the complete producer → classifier → linter boundary."""

    inspection = inspect_docx(path)
    classification = classify_inspection(inspection)
    audit = lint_inspection(inspection)
    return inspection, classification, audit


def test_direct_num_id_zero_removes_inherited_style_numbering(tmp_path: Path) -> None:
    path = write_numbering_docx(
        tmp_path,
        body=paragraph(run("普通正文"), style="ListStyle", num_id=0),
        styles=paragraph_style("ListStyle", num_id=7, ilvl=0),
        abstract_nums=(abstract_num(3, level(0)),),
        nums=num(7, 3),
    )

    report = inspect_docx(path)
    record = report["paragraphs"][0]["numbering"]

    assert record["numbered"] is False
    assert record["removes_numbering"] is True
    assert record["num_id"] == 0
    assert record["resolved"] is True
    assert record["source"] == "direct"


def test_sparse_direct_num_pr_merges_num_id_from_paragraph_style(tmp_path: Path) -> None:
    path = write_numbering_docx(
        tmp_path,
        body=paragraph(run("第二层"), style="ListStyle", ilvl=1),
        styles=paragraph_style("ListStyle", num_id=7, ilvl=0),
        abstract_nums=(abstract_num(3, level(0), level(1, text="%2.")),),
        nums=num(7, 3),
    )

    report = inspect_docx(path)
    record = report["paragraphs"][0]["numbering"]

    assert record["num_id"] == 7
    assert record["ilvl"] == 1
    assert record["format"] == "decimal"
    assert record["text"] == "%2."
    assert record["numbered"] is True
    assert record["resolved"] is True


def test_resolved_numbering_real_chain_drives_reference_layout(tmp_path: Path) -> None:
    path = write_numbering_docx(
        tmp_path,
        body=(
            paragraph(run("参考文献"))
            + paragraph(run("  Smith, J. (2020). A paper."), num_id=7, ilvl=0)
        ),
        abstract_nums=(abstract_num(3, level(0)),),
        nums=num(7, 3),
    )

    inspection, classification, audit = _chain(path)
    producer = inspection["paragraphs"][1]["numbering"]
    classified = classification["items"][1]
    finding = next(
        item
        for item in audit["findings"]
        if item["rule_id"] == "ER-MS-REF-LAYOUT-001"
    )

    assert producer["resolved"] is True
    assert producer["numbered"] is True
    assert producer["removes_numbering"] is False
    assert classified["role"] == "reference_entry"
    assert "automatic_numbering" in classified["evidence"]
    assert finding["status"] == "ERROR"
    assert finding["observed"]["numbered"] is True


@pytest.mark.parametrize(
    "marker",
    ["[1]", "［1］", "【1】", "1.", "1．", "1、", "(1)", "（1）"],
)
def test_visible_reference_numbering_is_error_from_real_docx(
    tmp_path: Path,
    marker: str,
) -> None:
    path = write_numbering_docx(
        tmp_path,
        body=(
            paragraph(run("参考文献"))
            + paragraph(
                run(f"  {marker} Smith, J. (2020). A paper.", size_pt=7.5, font="宋体")
            )
        ),
        filename=f"visible-reference-{ord(marker[0])}.docx",
    )

    inspection, classification, audit = _chain(path)
    findings = [
        item
        for item in audit["findings"]
        if item["rule_id"] == "ER-MS-REF-LAYOUT-001"
    ]

    assert classification["items"][1]["role"] == "reference_entry"
    assert len(findings) == 1
    assert findings[0]["status"] == "ERROR"
    assert findings[0]["observed"]["automatic_numbering_state"] is False
    assert findings[0]["observed"]["visible_numbering"] is True
    assert findings[0]["observed"]["visible_marker"] == marker
    assert findings[0]["observed"]["visible_marker_span"] == [2, 2 + len(marker)]
    assert findings[0]["observed"]["evidence_source"] == "reference_text_prefix"
    assert inspection["paragraphs"][1]["numbering"] is None


@pytest.mark.parametrize(
    "prefix",
    ["3M Company,", "[J] Smith,", "2020 Smith,"],
)
def test_reference_prefix_false_positive_guards_remain_unnumbered(
    tmp_path: Path,
    prefix: str,
) -> None:
    path = write_numbering_docx(
        tmp_path,
        body=(
            paragraph(run("参考文献"))
            + paragraph(
                run(f"  {prefix} J. (2020). A paper.", size_pt=7.5, font="宋体")
            )
        ),
        filename=f"visible-reference-guard-{prefix[0].encode().hex()}.docx",
    )

    _, _, audit = _chain(path)
    finding = next(
        item
        for item in audit["findings"]
        if item["rule_id"] == "ER-MS-REF-LAYOUT-001"
    )

    assert finding["status"] == "PASS"
    assert finding["observed"]["automatic_numbering_state"] is False
    assert finding["observed"]["visible_numbering"] is False


def test_automatic_and_visible_reference_numbering_emit_one_explained_error(
    tmp_path: Path,
) -> None:
    path = write_numbering_docx(
        tmp_path,
        body=(
            paragraph(run("参考文献"))
            + paragraph(
                run("  [1] Smith, J. (2020). A paper.", size_pt=7.5, font="宋体"),
                num_id=7,
                ilvl=0,
            )
        ),
        abstract_nums=(abstract_num(3, level(0)),),
        nums=num(7, 3),
        filename="automatic-and-visible-reference.docx",
    )

    _, _, audit = _chain(path)
    findings = [
        item
        for item in audit["findings"]
        if item["rule_id"] == "ER-MS-REF-LAYOUT-001"
    ]

    assert len(findings) == 1
    assert findings[0]["status"] == "ERROR"
    assert findings[0]["observed"]["automatic_numbering_state"] is True
    assert findings[0]["observed"]["visible_numbering"] is True


@pytest.mark.parametrize(
    ("text", "expected_status", "expected_visible"),
    [
        ("  [1] Smith, J. (2020). A paper.", "ERROR", True),
        ("  Smith, J. (2020). A paper.", "MANUAL_REVIEW", False),
    ],
)
def test_unresolved_automatic_numbering_defers_to_definite_visible_prefix(
    tmp_path: Path,
    text: str,
    expected_status: str,
    expected_visible: bool,
) -> None:
    path = write_numbering_docx(
        tmp_path,
        body=(
            paragraph(run("参考文献"))
            + paragraph(
                run(text, size_pt=7.5, font="宋体"),
                num_id=7,
                ilvl=0,
            )
        ),
        filename=f"unresolved-reference-{int(expected_visible)}.docx",
    )

    inspection, _, audit = _chain(path)
    finding = next(
        item
        for item in audit["findings"]
        if item["rule_id"] == "ER-MS-REF-LAYOUT-001"
    )

    assert inspection["paragraphs"][1]["numbering"]["numbered"] is None
    assert finding["status"] == expected_status
    assert finding["observed"]["automatic_numbering_state"] is None
    assert finding["observed"]["visible_numbering"] is expected_visible


def test_sparse_child_style_num_pr_merges_based_on_values(tmp_path: Path) -> None:
    styles = (
        paragraph_style("ChildStyle", based_on="BaseStyle", ilvl=1)
        + paragraph_style("BaseStyle", num_id=7)
    )
    path = write_numbering_docx(
        tmp_path,
        body=paragraph(run("基于样式"), style="ChildStyle"),
        styles=styles,
        abstract_nums=(abstract_num(3, level(0), level(1, text="%2.", pstyle="ChildStyle")),),
        nums=num(7, 3),
    )

    report = inspect_docx(path)
    record = report["paragraphs"][0]["numbering"]

    assert record["num_id"] == 7
    assert record["ilvl"] == 1
    assert record["resolved"] is True
    assert record["source"] == "paragraph_style:ChildStyle"


def test_style_ilvl_uses_unique_abstract_pstyle_level_not_style_ilvl(tmp_path: Path) -> None:
    path = write_numbering_docx(
        tmp_path,
        body=paragraph(run("1. 标准关联"), style="HeadingStyle"),
        styles=paragraph_style("HeadingStyle", num_id=7, ilvl=1),
        abstract_nums=(
            abstract_num(
                3,
                level(0, text="%1.", pstyle="HeadingStyle"),
                level(1, text="%2.", pstyle="OtherStyle"),
            ),
        ),
        nums=num(7, 3),
    )

    report = inspect_docx(path)
    record = report["paragraphs"][0]["numbering"]

    assert record["ilvl"] == 0
    assert record["level_source"] == "abstract_pStyle"
    assert record["level_evidence"]["style_id"] == "HeadingStyle"
    assert record["resolved"] is True


def test_ambiguous_abstract_pstyle_levels_remain_unresolved(tmp_path: Path) -> None:
    path = write_numbering_docx(
        tmp_path,
        body=paragraph(run("重复关联"), style="HeadingStyle"),
        styles=paragraph_style("HeadingStyle", num_id=7, ilvl=0),
        abstract_nums=(
            abstract_num(
                3,
                level(0, text="%1.", pstyle="HeadingStyle"),
                level(1, text="%2.", pstyle="HeadingStyle"),
            ),
        ),
        nums=num(7, 3),
    )

    report = inspect_docx(path)
    record = report["paragraphs"][0]["numbering"]

    assert record["resolved"] is False
    assert record["numbered"] is None
    assert record["reason"] == "ambiguous_style_pStyle_level"


def test_style_ilvl_without_abstract_pstyle_association_is_unresolved(tmp_path: Path) -> None:
    path = write_numbering_docx(
        tmp_path,
        body=paragraph(run("无关联"), style="ListStyle"),
        styles=paragraph_style("ListStyle", num_id=7, ilvl=1),
        abstract_nums=(abstract_num(3, level(0), level(1, text="%2.")),),
        nums=num(7, 3),
    )

    report = inspect_docx(path)
    record = report["paragraphs"][0]["numbering"]

    assert record["resolved"] is False
    assert record["numbered"] is None
    assert record["reason"] == "style_numpr_without_unique_pStyle"


def test_abstract_zero_and_start_override_only_are_resolved(tmp_path: Path) -> None:
    path = write_numbering_docx(
        tmp_path,
        body=paragraph(run("覆盖起点"), num_id=8, ilvl=0),
        abstract_nums=(abstract_num(0, level(0, text="%1.", start=1)),),
        nums=num(8, 0, overrides=start_override(0, 3)),
    )

    report = inspect_docx(path)
    record = report["paragraphs"][0]["numbering"]

    assert record["abstract_num_id"] == 0
    assert record["start"] == 1
    assert record["start_override"] == 3
    assert record["resolved"] is True


def test_nested_level_override_merges_abstract_level_evidence(tmp_path: Path) -> None:
    path = write_numbering_docx(
        tmp_path,
        body=paragraph(run("格式覆盖"), num_id=8, ilvl=0),
        abstract_nums=(abstract_num(0, level(0, fmt="decimal", text="%1.")),),
        nums=num(8, 0, overrides=level_override(0, fmt="lowerLetter", start_value=4)),
    )

    report = inspect_docx(path)
    record = report["paragraphs"][0]["numbering"]

    assert record["format"] == "lowerLetter"
    assert record["text"] == "%1."
    assert record["start_override"] == 4
    assert record["resolved"] is True


def test_level_override_pstyle_selects_the_effective_style_level(tmp_path: Path) -> None:
    path = write_numbering_docx(
        tmp_path,
        body=paragraph(run("覆盖样式层级"), style="HeadingStyle"),
        styles=paragraph_style("HeadingStyle", num_id=7, ilvl=0),
        abstract_nums=(
            abstract_num(
                3,
                level(0, text="%1.", pstyle="OtherStyle"),
                level(1, text="%2."),
            ),
        ),
        nums=num(
            7,
            3,
            overrides=level_override(
                1,
                fmt="lowerRoman",
                text="%2)",
                pstyle="HeadingStyle",
            ),
        ),
    )

    record = inspect_docx(path)["paragraphs"][0]["numbering"]

    assert record["resolved"] is True
    assert record["ilvl"] == 1
    assert record["format"] == "lowerRoman"
    assert record["level_source"] == "override_pStyle"
    assert record["level_evidence"]["style_id"] == "HeadingStyle"


def test_style_cycle_returns_bounded_unresolved_numbering_evidence(tmp_path: Path) -> None:
    styles = (
        paragraph_style("CycleA", based_on="CycleB", ilvl=1)
        + paragraph_style("CycleB", based_on="CycleA")
    )
    path = write_numbering_docx(
        tmp_path,
        body=paragraph(run("循环样式"), style="CycleA"),
        styles=styles,
        abstract_nums=(abstract_num(3, level(0)),),
        nums=num(7, 3),
    )

    report = inspect_docx(path)
    record = report["paragraphs"][0]["numbering"]

    assert record["resolved"] is False
    assert record["numbered"] is None
    assert record["reason"] == "style_cycle"


def test_unresolved_numbering_does_not_supply_classifier_heading_level(tmp_path: Path) -> None:
    path = write_numbering_docx(
        tmp_path,
        body=paragraph(run("1.1 研究背景"), ilvl=1),
    )

    report = inspect_docx(path)
    classification = classify_inspection(report)
    item = classification["items"][0]

    assert item["role"] == "heading_level_2"
    assert not any(str(value).startswith("numbering_ilvl=") for value in item["evidence"])
    assert any("unresolved_numbering" in str(value) for value in item["evidence"])


def test_reference_num_id_zero_is_not_reported_as_automatically_numbered(tmp_path: Path) -> None:
    path = write_numbering_docx(
        tmp_path,
        body=(
            paragraph(run("参考文献"))
            + paragraph(
                run("  Smith, J. (2020). A paper.", size_pt=7.5, font="宋体"),
                num_id=0,
                ilvl=0,
            )
        ),
    )

    report, classification, audit = _chain(path)
    producer = report["paragraphs"][1]["numbering"]
    classified = classification["items"][1]
    assert producer["numbered"] is False
    assert producer["removes_numbering"] is True
    assert producer["resolved"] is True
    assert classified["role"] == "reference_entry"
    assert "automatic_numbering" not in classified["evidence"]

    finding = next(item for item in audit["findings"] if item["rule_id"] == "ER-MS-REF-LAYOUT-001")
    assert finding["status"] != "ERROR"
    assert finding["observed"].get("numbered") is False


@pytest.mark.parametrize(
    "numbering_kwargs",
    [
        {"ilvl": 3},
        {"num_id": 0, "ilvl": 3},
    ],
)
def test_unresolved_or_removed_numbering_cannot_validate_visible_hierarchy_jump(
    tmp_path: Path,
    numbering_kwargs: dict[str, int],
) -> None:
    path = write_numbering_docx(
        tmp_path,
        body=(
            paragraph(run("  （一） 二级标题"))
            + paragraph(run("（1） 四级标题"), **numbering_kwargs)
        ),
    )

    inspection, classification, audit = _chain(path)
    producer = inspection["paragraphs"][1]["numbering"]
    finding = next(
        item
        for item in audit["findings"]
        if item["rule_id"] == "ER-MS-HEADING-HIERARCHY-001"
    )

    assert finding["status"] == "MANUAL_REVIEW"
    assert finding["observed"]["hierarchy_evidence"] == "visible_prefix_only"
    assert classification["items"][0]["role"] == "heading_level_2"
    assert classification["items"][1]["role"] == "heading_level_4"
    if numbering_kwargs.get("num_id") == 0:
        assert producer["numbered"] is False
        assert producer["removes_numbering"] is True
    else:
        assert producer["numbered"] is None
        assert producer["resolved"] is False


def test_legacy_raw_num_id_zero_cannot_validate_visible_hierarchy_jump(
    tmp_path: Path,
) -> None:
    path = write_numbering_docx(
        tmp_path,
        body=(
            paragraph(run("  （一） 二级标题"))
            + paragraph(run("（1） 四级标题"))
        ),
    )
    inspection = inspect_docx(path)
    inspection["paragraphs"][1]["numbering"] = {"numId": 0, "ilvl": 3}
    inspection["paragraphs"][1]["numPr"] = {"numId": 0, "ilvl": 3}

    audit = lint_inspection(inspection)
    finding = next(
        item
        for item in audit["findings"]
        if item["rule_id"] == "ER-MS-HEADING-HIERARCHY-001"
    )

    assert finding["status"] == "MANUAL_REVIEW"


def test_one_structural_heading_cannot_validate_an_unresolved_jump_peer(
    tmp_path: Path,
) -> None:
    styles = (
        '<w:style xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'w:type="paragraph" w:styleId="Heading2">'
        '<w:name w:val="Heading 2"/><w:pPr><w:outlineLvl w:val="1"/></w:pPr>'
        "</w:style>"
    )
    path = write_numbering_docx(
        tmp_path,
        body=(
            paragraph(run("  （一） 二级标题"), style="Heading2")
            + paragraph(run("（1） 四级标题"))
        ),
        styles=styles,
    )

    findings = [
        item
        for item in lint_inspection(inspect_docx(path))["findings"]
        if item["rule_id"] == "ER-MS-HEADING-HIERARCHY-001"
    ]

    assert {item["status"] for item in findings} == {"PASS", "MANUAL_REVIEW"}
    manual = next(item for item in findings if item["status"] == "MANUAL_REVIEW")
    assert manual["target"]["id"] == "p-000001"
