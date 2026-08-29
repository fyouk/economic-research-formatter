from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path

import pytest

from economic_research_formatter.classify.classifier import classify_inspection
from economic_research_formatter.docx.inspector import inspect_docx
from economic_research_formatter.lint.engine import lint_inspection


EXPECTED_SHA256 = "246f82094b9448dd7a0a6b5ae195d3666f873e6ea2376ffdc792b9575f619065"

pytestmark = pytest.mark.private


@pytest.fixture(scope="module")
def private_fixture() -> Path:
    configured = os.environ.get("ER_PRIVATE_FIXTURE")
    if not configured:
        pytest.skip("ER_PRIVATE_FIXTURE is not configured")
    path = Path(configured)
    if not path.is_file():
        pytest.skip("ER_PRIVATE_FIXTURE does not exist")
    return path


@pytest.fixture(scope="module")
def private_inspection(private_fixture: Path) -> dict:
    before = (private_fixture.stat().st_mtime_ns, private_fixture.read_bytes())
    inspection = inspect_docx(private_fixture)
    after = (private_fixture.stat().st_mtime_ns, private_fixture.read_bytes())
    assert after == before
    return inspection


@pytest.fixture(scope="module")
def private_analysis_inspection(private_fixture: Path) -> dict:
    """Keep full text in memory for linting; never serialize this fixture."""

    before = (private_fixture.stat().st_mtime_ns, private_fixture.read_bytes())
    inspection = inspect_docx(private_fixture, include_text=True)
    after = (private_fixture.stat().st_mtime_ns, private_fixture.read_bytes())
    assert after == before
    return inspection


@pytest.fixture(scope="module")
def private_audit(private_analysis_inspection: dict) -> dict:
    return lint_inspection(private_analysis_inspection)


def test_private_fixture_identity_and_structure(private_fixture, private_inspection):
    assert hashlib.sha256(private_fixture.read_bytes()).hexdigest() == EXPECTED_SHA256
    assert private_inspection["input"]["sha256"] == EXPECTED_SHA256
    assert len(private_inspection["sections"]) == 4
    assert sum(section["orientation"] == "landscape" for section in private_inspection["sections"]) == 1
    assert len(private_inspection["tables"]) == 11
    assert len(private_inspection["images"]) == 3
    assert sum(image["color_analysis"]["is_probably_grayscale"] for image in private_inspection["images"]) == 2
    assert private_inspection["equations"]["omath_count"] == 12
    assert private_inspection["equations"]["omath_para_count"] == 7
    assert len(private_inspection["equations"]["paragraph_ids"]) == 12
    assert private_inspection["notes"]["footnotes"]["actual_count"] == 0
    assert private_inspection["fields"]["counts"]["TOC"] == 1
    assert private_inspection["fields"]["counts"]["PAGEREF"] == 50
    core_properties = private_inspection["core_properties"]
    assert set(core_properties) <= {"creator", "last_modified_by"}
    assert all(set(value) == {"present", "sha256"} for value in core_properties.values())
    serialized = json.dumps(private_inspection, ensure_ascii=False)
    assert str(private_fixture.parent) not in serialized

    preview_lengths: list[int] = []

    def collect_previews(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.endswith("preview") and isinstance(item, str):
                    preview_lengths.append(len(item))
                collect_previews(item)
        elif isinstance(value, list):
            for item in value:
                collect_previews(item)

    collect_previews(private_inspection)
    assert preview_lengths and max(preview_lengths) <= 80


def test_private_fixture_classification_false_positive_defenses(private_inspection):
    classification = classify_inspection(private_inspection)
    roles = Counter(item["role"] for item in classification["items"])

    assert roles["heading_level_1"] == 6
    assert roles["figure_caption"] == 3
    assert roles["reference_entry"] == 53
    assert roles["author_name"] == 0
    assert all(
        item["role"] == "toc"
        for item in classification["items"]
        if item.get("in_toc")
    )


def test_private_fixture_required_audit_conclusions(private_audit):
    audit = private_audit
    statuses_by_rule: dict[str, set[str]] = {}
    for finding in audit["findings"]:
        statuses_by_rule.setdefault(finding["rule_id"], set()).add(finding["status"])

    required_errors = {
        "ER-MS-TITLE-001",
        "ER-MS-ABSTRACT-001",
        "ER-MS-HEADING1-001",
        "ER-MS-HEADING-HIERARCHY-001",
        "ER-MS-FIGURE-001",
        "ER-MS-REF-LAYOUT-001",
        "ER-MS-REF-PAGERANGE-001",
        "ER-REF-LANGUAGE-GROUP-001",
        "ER-CIT-MULTI-AUTHORSOURCES-001",
        "ER-CIT-EN-TWOAUTHORS-001",
        "ER-CIT-ZH-TWOAUTHORS-001",
        "ER-CIT-REF-FULLAUTHORS-001",
    }
    for rule_id in required_errors:
        assert "ERROR" in statuses_by_rule[rule_id]

    assert statuses_by_rule["ER-MS-EQUATION-001"] == {"PASS"}
    assert "MANUAL_REVIEW" in statuses_by_rule["ER-MS-FIGURE-002"]
    assert statuses_by_rule["ER-MS-FOOTNOTE-001"] == {"NOT_APPLICABLE"}
    assert statuses_by_rule["ER-MS-FOOTNOTE-002"] == {"NOT_APPLICABLE"}
    assert statuses_by_rule["ER-CONFLICT-001"] == {"MANUAL_REVIEW"}
    assert statuses_by_rule["ER-CONFLICT-002"] == {"MANUAL_REVIEW"}
    assert audit["capabilities"]["not_checked"]


def test_private_toc_and_references_are_excluded_from_body_citations(
    private_analysis_inspection, private_audit
):
    audit = private_audit
    body_citation_findings = [
        finding
        for finding in audit["findings"]
        if finding["rule_id"].startswith("ER-CIT-")
        and finding["target"]["kind"] == "paragraph"
    ]
    paragraph_by_id = {
        paragraph["id"]: paragraph
        for paragraph in private_analysis_inspection["paragraphs"]
    }

    assert all(not paragraph_by_id[item["target"]["id"]].get("in_toc") for item in body_citation_findings)
    assert all(item["target"].get("role") != "reference_entry" for item in body_citation_findings)


def test_private_narrative_citations_do_not_trigger_general_errors(
    private_analysis_inspection, private_audit
):
    cases = (
        (
            "Abis and Veldkamp (2022)",
            "ER-CIT-EN-TWOAUTHORS-001",
            "and",
        ),
        (
            "何威风与刘启亮（2010）",
            "ER-CIT-ZH-TWOAUTHORS-001",
            "与",
        ),
    )
    paragraphs = private_analysis_inspection["paragraphs"]
    for literal, connector_rule, connector in cases:
        paragraph_id = next(
            paragraph["id"] for paragraph in paragraphs if literal in paragraph.get("text", "")
        )
        general = [
            finding
            for finding in private_audit["findings"]
            if finding["rule_id"] in {"ER-CIT-GENERAL-001", "ER-CIT-GENERAL-002"}
            and finding["target"].get("id") == paragraph_id
        ]
        assert general and all(finding["status"] == "PASS" for finding in general)
        assert any(
            candidate.get("kind") == "narrative"
            and candidate.get("raw_preview") == literal
            for finding in general
            for candidate in finding["observed"].get("candidates", [])
        )
        connector_findings = [
            finding
            for finding in private_audit["findings"]
            if finding["rule_id"] == connector_rule
            and finding["target"].get("id") == paragraph_id
            and finding["observed"].get("author_connector") == connector
        ]
        assert connector_findings
        assert all(finding["status"] == "ERROR" for finding in connector_findings)


def test_private_multi_source_separator_is_attached_to_the_exact_candidate(
    private_analysis_inspection, private_audit
):
    literal = "(Dechow et al., 2011; Kothari et al., 2005)"
    paragraph_id = next(
        paragraph["id"]
        for paragraph in private_analysis_inspection["paragraphs"]
        if literal in paragraph.get("text", "")
    )
    findings = [
        finding
        for finding in private_audit["findings"]
        if finding["rule_id"] == "ER-CIT-MULTI-AUTHORSOURCES-001"
        and finding["target"].get("id") == paragraph_id
    ]
    assert findings
    assert any(
        finding["status"] == "ERROR" and finding["observed"].get("separator") == ";"
        for finding in findings
    )


def test_private_reference_numbering_and_page_range_target_are_precise(
    private_analysis_inspection, private_audit
):
    classification = classify_inspection(private_analysis_inspection)
    reference_ids = {
        item["source_id"]
        for item in classification["items"]
        if item["role"] == "reference_entry"
    }
    reference_paragraphs = [
        paragraph
        for paragraph in private_analysis_inspection["paragraphs"]
        if paragraph["id"] in reference_ids
    ]
    assert len(reference_paragraphs) == 53
    assert sum(bool(paragraph.get("numPr", {}).get("resolved")) for paragraph in reference_paragraphs) == 53

    literal = "58(1-2): 3-27"
    paragraph_id = next(
        paragraph["id"]
        for paragraph in reference_paragraphs
        if literal in paragraph.get("text", "")
    )
    finding = next(
        finding
        for finding in private_audit["findings"]
        if finding["rule_id"] == "ER-MS-REF-PAGERANGE-001"
        and finding["target"].get("id") == paragraph_id
    )
    assert finding["status"] == "ERROR"
    assert finding["observed"]["selected_span"] == "3-27"
    assert finding["observed"]["selected_span"] != "1-2"


def test_private_tables_feed_latin_font_and_note_binding_from_inspector(
    private_analysis_inspection, private_audit
):
    table_findings = [
        finding
        for finding in private_audit["findings"]
        if finding["rule_id"] == "ER-MS-LATIN-FONT-001"
        and finding["target"].get("scope") == "table"
    ]
    table_ids = {
        finding["target"].get("table_id", finding["target"].get("table_index"))
        for finding in table_findings
    }
    assert len(table_ids) == 11
    assert any(finding["status"] == "ERROR" for finding in table_findings)

    candidates = [
        candidate
        for table in private_analysis_inspection["tables"]
        for candidate in table.get("note_candidates", [])
    ]
    assert candidates
    assert all(candidate["table_id"] and candidate["paragraph_id"] for candidate in candidates)
    assert all(candidate["distance"] == 1 for candidate in candidates)
    assert all(candidate["reason"] == "first_nonempty_post_table_note" for candidate in candidates)
