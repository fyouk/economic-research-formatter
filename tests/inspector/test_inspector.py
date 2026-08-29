from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from economic_research_formatter.docx.inspector import inspect_docx
from economic_research_formatter.models.inspection import DocxInspectionError

from docx_factory import make_synthetic_docx


def test_inspect_docx_reports_deterministic_document_inventory(tmp_path: Path) -> None:
    path = make_synthetic_docx(tmp_path)

    report = inspect_docx(path)

    assert report["schema_version"] == "1.0"
    assert report["input"]["filename"] == path.name
    assert report["input"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert report["input"]["size_bytes"] == path.stat().st_size
    assert len(report["sections"]) == 4
    assert any(section["orientation"] == "landscape" for section in report["sections"])
    assert report["summary"]["table_count"] == 1
    assert len(report["tables"]) == 1
    assert report["summary"]["image_count"] == 3
    assert report["summary"]["equation_count"] == 2
    assert report["notes"]["footnotes"]["actual_count"] == 1
    assert report["notes"]["footnotes"]["separator_count"] == 2
    assert report["fields"]["counts"]["TOC"] == 1
    assert report["fields"]["counts"]["PAGEREF"] == 1
    assert report["tracked_changes"]["insertions"] == 1
    assert report["tracked_changes"]["deletions"] == 1
    assert report["comments"]["count"] == 1
    assert report["embedded_objects"]["count"] == 1
    assert report["paragraphs"][-3]["numPr"]["num_id"] == 1
    assert report["paragraphs"][-3]["numPr"]["format"] == "bullet"
    assert report["images"][0]["color_analysis"]["has_alpha"] is True
    assert report["images"][0]["color_analysis"]["is_probably_grayscale"] is True
    assert report["images"][2]["color_analysis"]["is_probably_grayscale"] is False

    ids = [paragraph["id"] for paragraph in report["paragraphs"]]
    assert ids == [f"p-{index:06d}" for index in range(len(ids))]
    toc_paragraphs = [paragraph for paragraph in report["paragraphs"] if paragraph["toc"]["is_toc"]]
    assert toc_paragraphs
    assert not any(paragraph["id"] in {p["id"] for p in toc_paragraphs} and paragraph["toc"]["is_body_heading"] for paragraph in report["paragraphs"])

    # The body inventory contains only direct body paragraphs.  Table cell
    # paragraphs are represented under tables and are not duplicated here.
    body_text = "\n".join(paragraph.get("text", paragraph["text_preview"]) for paragraph in report["paragraphs"])
    assert "表1" not in body_text
    assert report["tables"][0]["cells"][0]["paragraphs"][0]["text_preview"] == "表1"
    all_run_ids = [
        run["id"]
        for paragraph in report["paragraphs"]
        for run in paragraph["runs"]
    ]
    all_run_ids.extend(
        run["id"]
        for table in report["tables"]
        for cell in table["cells"]
        for paragraph in cell["paragraphs"]
        for run in paragraph["runs"]
    )
    assert len(all_run_ids) == len(set(all_run_ids))


def test_inspect_docx_reports_no_actual_notes_when_only_no_note_part_exists(tmp_path: Path) -> None:
    path = make_synthetic_docx(tmp_path, with_real_footnote=False, with_metadata_objects=False)

    report = inspect_docx(path)

    assert report["notes"]["footnotes"]["actual_count"] == 0
    assert report["notes"]["footnotes"]["separator_count"] == 0
    assert report["summary"]["footnote_count"] == 0


def test_inspect_docx_omits_full_text_by_default_and_supports_opt_in(tmp_path: Path) -> None:
    path = make_synthetic_docx(tmp_path, with_metadata_objects=False)

    compact = inspect_docx(path)
    expanded = inspect_docx(path, include_text=True)

    assert all("text" not in paragraph for paragraph in compact["paragraphs"])
    assert all("text" not in run for paragraph in compact["paragraphs"] for run in paragraph["runs"])
    title_compact = compact["paragraphs"][0]["text_preview"]
    assert len(title_compact) <= 80
    assert expanded["paragraphs"][0]["text"].startswith("题目：")
    assert expanded["paragraphs"][0]["runs"][1]["text"] == "混合字体标题"


@pytest.mark.parametrize("bad_name,content", [("not.docx", b"plain text"), ("broken.docx", b"PK\x03\x04bad")])
def test_inspect_docx_rejects_non_docx_and_corrupt_zip(tmp_path: Path, bad_name: str, content: bytes) -> None:
    path = tmp_path / bad_name
    path.write_bytes(content)

    with pytest.raises(DocxInspectionError):
        inspect_docx(path)


def test_inspect_docx_rejects_missing_core_parts(tmp_path: Path) -> None:
    from zipfile import ZIP_DEFLATED, ZipFile

    path = tmp_path / "missing-core.docx"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")

    with pytest.raises(DocxInspectionError, match="core part"):
        inspect_docx(path)
