from __future__ import annotations

from economic_research_formatter.classify.classifier import classify_inspection


def _inspection(*paragraphs: dict) -> dict:
    return {
        "schema_version": "1.0",
        "paragraphs": [
            {"id": f"p-{index:06d}", "index": index, **paragraph}
            for index, paragraph in enumerate(paragraphs)
        ],
        "tables": [],
        "images": [],
        "equations": {},
        "notes": {"footnotes": {"actual_count": 0}},
        "fields": {"counts": {"TOC": 0}},
    }


def _roles(inspection: dict) -> dict[str, str]:
    result = classify_inspection(inspection)
    return {item["source_id"]: item["role"] for item in result["items"]}


def test_classifier_keeps_toc_out_of_heading_and_citation_roles() -> None:
    inspection = _inspection(
        {"text": "题目：AI与财务错报", "style_name": "Title"},
        {"text": "摘要", "style_name": "Heading 1"},
        {"text": "本文研究张三（2020）的证据。"},
        {"text": "关键词：人工智能；财务错报"},
        {"text": "第一章 绪论", "in_toc": True, "toc": True},
        {"text": "第一章 绪论", "style_name": "Heading 1"},
        {"text": "参考文献", "style_name": "Heading 1"},
        {"text": "Smith, J. (2020). A paper.", "numbering": {"numId": 3, "ilvl": 0}},
    )

    roles = _roles(inspection)

    assert roles["p-000000"] == "title"
    assert roles["p-000001"] == "abstract_heading"
    assert roles["p-000002"] == "abstract_text"
    assert roles["p-000003"] == "keywords"
    assert roles["p-000004"] == "toc"
    assert roles["p-000005"] == "heading_level_1"
    assert roles["p-000006"] == "reference_heading"
    assert roles["p-000007"] == "reference_entry"

    assert all(
        item["role"] == "toc"
        for item in classify_inspection(inspection)["items"]
        if item.get("in_toc")
    )


def test_classifier_maps_thesis_numbering_to_heading_levels_without_inventing_author() -> None:
    inspection = _inspection(
        {"text": "题目：研究标题"},
        {"text": "摘要"},
        {"text": "这是摘要正文。"},
        {"text": "第1章 绪论", "numbering": {"numId": 1, "ilvl": 0}},
        {"text": "1.1 研究背景", "numbering": {"numId": 1, "ilvl": 1}},
        {"text": "1.1.1 研究问题", "numbering": {"numId": 1, "ilvl": 2}},
        {"text": "正文首段，不是作者。"},
    )

    result = classify_inspection(inspection)
    roles = _roles(inspection)

    assert roles["p-000003"] == "heading_level_1"
    assert roles["p-000004"] == "heading_level_2"
    assert roles["p-000005"] == "heading_level_3"
    assert not any(item["role"] == "author_name" for item in result["items"])
    assert result == classify_inspection(inspection)


def test_classifier_uses_explicit_author_metadata_but_not_title_or_abstract_text() -> None:
    inspection = _inspection(
        {"text": "题目：研究标题"},
        {"text": "张三", "role_hint": "author_name"},
        {"text": "摘要"},
        {"text": "作者张三的研究摘要。"},
    )

    roles = _roles(inspection)

    assert roles["p-000000"] == "title"
    assert roles["p-000001"] == "author_name"
    assert roles["p-000003"] == "abstract_text"


def test_visible_thesis_numbering_overrides_generic_heading_style() -> None:
    inspection = _inspection(
        {"text": "第1章 绪论", "style_name": "Heading 1"},
        {"text": "1.1 研究背景", "style_name": "Heading 1"},
        {"text": "1.1.1 研究问题", "style_name": "Heading 1"},
        {"text": "1.1.2 研究假设", "style_name": "Heading 1", "in_toc": True},
    )

    result = classify_inspection(inspection)
    roles = {item["source_id"]: item["role"] for item in result["items"]}

    assert roles["p-000000"] == "heading_level_1"
    assert roles["p-000001"] == "heading_level_2"
    assert roles["p-000002"] == "heading_level_3"
    assert roles["p-000003"] == "toc"


def test_chapter_summary_sentence_is_body_text_not_a_chapter_heading() -> None:
    inspection = _inspection(
        {"text": "第一章是绪论。本文本章阐述研究背景。"},
        {"text": "第1章 绪论"},
    )

    roles = _roles(inspection)

    assert roles["p-000000"] == "body_text"
    assert roles["p-000001"] == "heading_level_1"


def test_empty_heading_style_is_not_counted_as_a_heading() -> None:
    inspection = _inspection({"text": "", "style_name": "Heading 1"})

    roles = _roles(inspection)

    assert roles["p-000000"] == "unknown"
