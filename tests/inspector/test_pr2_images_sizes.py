from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from economic_research_formatter.classify.classifier import classify_inspection
from economic_research_formatter.docx import images as images_module
from economic_research_formatter.docx.images import analyze_image
from economic_research_formatter.docx.inspector import inspect_docx
from economic_research_formatter.docx.inspector import _size_relation
from economic_research_formatter.lint.engine import lint_inspection
from economic_research_formatter.models.inspection import DocxInspectionError

from inspector.pr1_docx_factory import paragraph, run, table, write_docx
from tests.docx_factory import make_synthetic_docx


def _png_bytes(
    size: tuple[int, int],
    *,
    mode: str = "RGB",
    color: tuple[int, ...] = (255, 255, 255),
) -> bytes:
    output = BytesIO()
    Image.new(mode, size, color).save(output, format="PNG")
    return output.getvalue()


def _chain_docx(path: Path) -> tuple[dict, dict, dict]:
    inspection = inspect_docx(path)
    classification = classify_inspection(inspection)
    audit = lint_inspection(inspection)
    return inspection, classification, audit


def test_image_at_configured_pixel_limit_never_materializes_getdata_as_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(images_module, "MAX_IMAGE_PIXELS", 64 * 64)

    def reject_materialization(_values: object) -> list[object]:
        raise AssertionError("pixel data must not be materialized as a Python list")

    # ``images.py`` previously resolved the built-in name directly.  A module
    # override makes the regression fail only if that full-list path is used.
    monkeypatch.setattr(images_module, "list", reject_materialization, raising=False)

    result = analyze_image(_png_bytes((64, 64), color=(10, 20, 30)))

    assert result["pixel_width"] == 64
    assert result["pixel_height"] == 64
    assert result["is_probably_grayscale"] is False


def test_image_over_limit_is_rejected_before_conversion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(images_module, "MAX_IMAGE_PIXELS", 64 * 64)

    def reject_convert(self: Image.Image, *args: object, **kwargs: object) -> Image.Image:
        raise AssertionError("conversion must not run above the pixel limit")

    monkeypatch.setattr(Image.Image, "convert", reject_convert)

    with pytest.raises(DocxInspectionError, match=r"65x65") as exc_info:
        analyze_image(_png_bytes((65, 65)))

    assert exc_info.value.kind == "resource_limit"
    assert "/Users/" not in str(exc_info.value)


def test_transparent_black_line_art_is_deterministically_grayscale() -> None:
    image = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    for coordinate in range(8):
        image.putpixel((coordinate, coordinate), (0, 0, 0, 255))
    output = BytesIO()
    image.save(output, format="PNG")

    first = analyze_image(output.getvalue())
    second = analyze_image(output.getvalue())

    assert first == second
    assert first["is_probably_grayscale"] is True
    assert first["colored_nonwhite_pixel_ratio"] == 0.0
    assert first["nonwhite_pixel_ratio"] == pytest.approx(8 / 64)


def test_monochrome_color_and_sparse_color_pixels_are_distinguished() -> None:
    monochrome = analyze_image(_png_bytes((4, 4), color=(32, 32, 32)))
    color = analyze_image(_png_bytes((4, 4), color=(200, 20, 20)))
    sparse = Image.new("RGB", (4, 4), (255, 255, 255))
    sparse.putpixel((0, 0), (255, 0, 0))
    output = BytesIO()
    sparse.save(output, format="PNG")
    sparse_result = analyze_image(output.getvalue())

    assert monochrome["is_probably_grayscale"] is True
    assert monochrome["colored_nonwhite_pixel_ratio"] == 0.0
    assert color["is_probably_grayscale"] is False
    assert color["colored_nonwhite_pixel_ratio"] == 1.0
    assert sparse_result["is_probably_grayscale"] is False
    assert sparse_result["colored_nonwhite_pixel_ratio"] == pytest.approx(1 / 16)


def test_real_docx_image_analysis_runs_through_classifier_and_linter(
    tmp_path: Path,
) -> None:
    inspection = inspect_docx(make_synthetic_docx(tmp_path))
    classification = classify_inspection(inspection)
    audit = lint_inspection(inspection)

    assert inspection["images"]
    assert all(
        image["color_analysis"]["analysis"]["sampling_strategy"]
        == "full_scan_streaming"
        for image in inspection["images"]
    )
    assert classification["summary"]["paragraph_count"] == len(
        inspection["paragraphs"]
    )
    findings = [
        item
        for item in audit["findings"]
        if item["rule_id"] == "ER-MS-FIGURE-002"
    ]
    assert len(findings) == len(inspection["images"])


@pytest.mark.parametrize("near_standard", [10.509, 10.491, 9.009])
def test_near_standard_non_exact_chinese_sizes_remain_unknown(
    near_standard: float,
) -> None:
    relation, evidence = _size_relation(
        9.0,
        near_standard,
        classify_one_cn_step=True,
    )

    assert relation == "unknown"
    assert evidence["reason"] == "nonstandard_cn_font_size"


@pytest.mark.parametrize(
    ("table_size", "note_size"),
    [
        (16.0, 15.0),
        (14.0, 12.0),
        (12.0, 10.5),
        (10.5, 9.0),
        (9.0, 7.5),
        (7.5, 6.5),
        (6.5, 5.5),
    ],
)
def test_real_docx_all_adjacent_chinese_sizes_are_one_step_smaller(
    tmp_path: Path,
    table_size: float,
    note_size: float,
) -> None:
    body = (
        paragraph(run("正文基准", size_pt=10.5))
        + table((run("表格数据", size_pt=table_size, font="仿宋"),))
        + paragraph(run("注：表格说明", size_pt=note_size, font="宋体"))
    )
    inspection = inspect_docx(
        write_docx(
            tmp_path,
            body=body,
            filename=f"cn-size-{table_size:g}-{note_size:g}.docx",
        )
    )
    classification = classify_inspection(inspection)
    audit = lint_inspection(inspection)

    note = inspection["paragraphs"][1]
    assert note["font_size_relation_to_table"] == "one_cn_size_smaller"
    assert note["font_size_relation_to_table_comparison"]["baseline_pt"] == table_size
    assert note["font_size_relation_to_table_comparison"]["target_pt"] == note_size
    assert any(item["source_id"] == note["id"] for item in classification["items"])
    finding = next(
        item
        for item in audit["findings"]
        if item["rule_id"] == "ER-MS-TABLE-NOTE-001"
    )
    assert finding["status"] == "PASS"


@pytest.mark.parametrize(
    ("table_size", "note_size", "expected_relation"),
    [
        (10.5, 10.5, "equal"),
        (12.0, 9.0, "smaller"),
        (11.0, 9.5, "unknown"),
    ],
)
def test_real_docx_non_adjacent_or_nonstandard_sizes_never_pass_as_one_step(
    tmp_path: Path,
    table_size: float,
    note_size: float,
    expected_relation: str,
) -> None:
    body = (
        paragraph(run("正文基准", size_pt=10.5))
        + table((run("表格数据", size_pt=table_size),))
        + paragraph(run("注：表格说明", size_pt=note_size))
    )
    inspection, classification, audit = _chain_docx(
        write_docx(tmp_path, body=body)
    )

    assert inspection["paragraphs"][1]["font_size_relation_to_table"] == expected_relation
    assert classification["summary"]["paragraph_count"] == len(
        inspection["paragraphs"]
    )
    finding = next(
        item
        for item in audit["findings"]
        if item["rule_id"] == "ER-MS-TABLE-NOTE-001"
    )
    assert finding["status"] != "PASS"


def test_real_docx_mixed_note_runs_keep_chinese_size_relation_unknown(
    tmp_path: Path,
) -> None:
    body = (
        paragraph(run("正文基准", size_pt=10.5))
        + table((run("表格数据", size_pt=9.0),))
        + paragraph(
            run("注：", size_pt=7.5),
            run("混合字号", size_pt=6.5),
        )
    )
    inspection, classification, audit = _chain_docx(
        write_docx(tmp_path, body=body)
    )

    note = inspection["paragraphs"][1]
    assert classification["summary"]["paragraph_count"] == len(
        inspection["paragraphs"]
    )
    assert note["font_size_relation_to_table"] == "unknown"
    assert note["font_size_relation_to_table_comparison"]["reason"] == "mixed_runs"
    finding = next(
        item
        for item in audit["findings"]
        if item["rule_id"] == "ER-MS-TABLE-NOTE-001"
    )
    assert finding["status"] == "MANUAL_REVIEW"


def test_body_baseline_uses_only_classified_body_text_and_reports_dominance(
    tmp_path: Path,
) -> None:
    body = (
        paragraph(run("摘要", size_pt=16.0))
        + paragraph(run("摘要内容", size_pt=12.0))
        + paragraph(run("关键词：人工智能", size_pt=12.0))
        + paragraph(run("第1章 绪论", size_pt=16.0))
        + paragraph(run("正文甲", size_pt=10.5))
        + paragraph(run("正文乙", size_pt=10.5))
        + paragraph(run("图1 研究框架", size_pt=9.0))
        + table((run("表格数据", size_pt=9.0),))
    )
    inspection, classification, audit = _chain_docx(
        write_docx(tmp_path, body=body)
    )

    table_record = inspection["tables"][0]
    baseline = table_record["font_size_evidence"]["body_baseline"]
    assert table_record["font_size_relation_to_body"] == "smaller"
    assert baseline["status"] == "resolved"
    assert baseline["baseline_pt"] == 10.5
    assert baseline["size_frequency"] == {"10.5": 2}
    assert baseline["candidate_paragraph_count"] == 2
    assert baseline["dominant_share"] == 1.0
    assert baseline["confidence"] == 1.0
    assert baseline["exclusion_reasons"]["abstract_heading"] == 1
    assert baseline["exclusion_reasons"]["abstract_text"] == 1
    assert baseline["exclusion_reasons"]["keywords"] == 1
    assert baseline["exclusion_reasons"]["heading_level_1"] == 1
    assert baseline["exclusion_reasons"]["figure_caption"] == 1
    assert classification["summary"]["paragraph_count"] == len(
        inspection["paragraphs"]
    )
    table_finding = next(
        item for item in audit["findings"] if item["rule_id"] == "ER-MS-TABLE-001"
    )
    assert table_finding["status"] == "WARNING"
    assert table_finding["observed"]["font_size_relation_to_body"] == "smaller"


def test_body_baseline_without_clear_dominant_size_stays_unknown(tmp_path: Path) -> None:
    body = (
        paragraph(run("第1章 绪论", size_pt=16.0))
        + paragraph(run("正文甲", size_pt=10.5))
        + paragraph(run("正文乙", size_pt=12.0))
        + table((run("表格数据", size_pt=9.0),))
    )
    inspection, classification, audit = _chain_docx(
        write_docx(tmp_path, body=body)
    )

    baseline = inspection["tables"][0]["font_size_evidence"]["body_baseline"]
    assert baseline["status"] == "unknown"
    assert baseline["reason"] == "no_dominant_body_size"
    assert baseline["size_frequency"] == {"10.5": 1, "12": 1}
    assert baseline["candidate_paragraph_count"] == 2
    assert baseline["dominant_share"] == 0.5
    assert inspection["tables"][0]["font_size_relation_to_body"] == "unknown"
    assert classification["summary"]["paragraph_count"] == len(
        inspection["paragraphs"]
    )
    table_finding = next(
        item for item in audit["findings"] if item["rule_id"] == "ER-MS-TABLE-001"
    )
    assert table_finding["status"] == "MANUAL_REVIEW"
