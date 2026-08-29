from __future__ import annotations

import hashlib
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


def test_private_fixture_required_audit_conclusions(private_analysis_inspection):
    audit = lint_inspection(private_analysis_inspection)
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

    assert "MANUAL_REVIEW" in statuses_by_rule["ER-MS-EQUATION-001"]
    assert "MANUAL_REVIEW" in statuses_by_rule["ER-MS-FIGURE-002"]
    assert statuses_by_rule["ER-MS-FOOTNOTE-001"] == {"NOT_APPLICABLE"}
    assert statuses_by_rule["ER-MS-FOOTNOTE-002"] == {"NOT_APPLICABLE"}
    assert statuses_by_rule["ER-CONFLICT-001"] == {"MANUAL_REVIEW"}
    assert statuses_by_rule["ER-CONFLICT-002"] == {"MANUAL_REVIEW"}
    assert audit["capabilities"]["not_checked"]


def test_private_toc_and_references_are_excluded_from_body_citations(private_analysis_inspection):
    audit = lint_inspection(private_analysis_inspection)
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
