"""Regression coverage for OOXML theme-font precedence in the Inspector."""

from __future__ import annotations

from pathlib import Path

from economic_research_formatter.docx.inspector import inspect_docx

from inspector.pr1_docx_factory import styles_xml, theme_xml, write_docx


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _direct_theme_run(text: str, *, language: str | None = None) -> str:
    language_xml = f'<w:lang w:eastAsia="{language}"/>' if language else ""
    return (
        "<w:r><w:rPr>"
        f'<w:rFonts w:eastAsiaTheme="majorEastAsia"/>{language_xml}'
        f"</w:rPr><w:t>{text}</w:t></w:r>"
    )


def _styles_with_lower_east_asia_fallback() -> bytes:
    doc_defaults = (
        f'<w:docDefaults xmlns:w="{W}"><w:rPrDefault><w:rPr>'
        '<w:rFonts w:eastAsia="文档默认字体"/>'
        "</w:rPr></w:rPrDefault><w:pPrDefault><w:pPr/></w:pPrDefault>"
        "</w:docDefaults>"
    )
    styles = styles_xml(default_paragraph="Normal", doc_defaults=doc_defaults)
    return styles.replace(
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        '<w:name w:val="Normal"/></w:style>'.encode(),
        (
            '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
            '<w:name w:val="Normal"/><w:rPr><w:rFonts w:eastAsia="默认段落字体"/>'
            "</w:rPr></w:style>"
        ).encode(),
    )


def test_unresolved_direct_theme_font_occupies_east_asia_before_lower_fallback(tmp_path: Path) -> None:
    report = inspect_docx(
        write_docx(
            tmp_path,
            body=f"<w:p>{_direct_theme_run('中文')}</w:p>",
            parts={
                "word/styles.xml": _styles_with_lower_east_asia_fallback(),
                "word/theme/theme1.xml": theme_xml(hans="简体主题", hant="繁體主題"),
            },
        )
    )

    formatting = report["paragraphs"][0]["runs"][0]["formatting"]
    effective = formatting["effective"]
    evidence = effective["font"]["theme_evidence"]["eastAsia"]

    assert effective["font"]["eastAsia"] is None
    assert effective.get("eastAsia") is None
    assert formatting["source"]["font"]["eastAsia"] == "unknown"
    assert effective["font"]["theme"]["eastAsia"] == "majorEastAsia"
    assert evidence == {
        "token": "majorEastAsia",
        "resolved": False,
        "script": None,
        "available_scripts": ["Hans", "Hant"],
    }


def test_direct_theme_font_resolves_to_hans_with_language_evidence(tmp_path: Path) -> None:
    report = inspect_docx(
        write_docx(
            tmp_path,
            body=f"<w:p>{_direct_theme_run('简体中文', language='zh-CN')}</w:p>",
            parts={"word/theme/theme1.xml": theme_xml(hans="简体主题", hant="繁體主題")},
        )
    )

    formatting = report["paragraphs"][0]["runs"][0]["formatting"]
    effective = formatting["effective"]
    evidence = effective["font"]["theme_evidence"]["eastAsia"]

    assert effective["font"]["eastAsia"] == "简体主题"
    assert effective["eastAsia"] == "简体主题"
    assert effective["east_asia_font"] == "简体主题"
    assert formatting["source"]["font"]["eastAsia"] == "theme"
    assert evidence == {
        "token": "majorEastAsia",
        "resolved": True,
        "script": "Hans",
        "available_scripts": ["Hans", "Hant"],
    }


def test_direct_theme_font_resolves_to_hant_with_language_evidence(tmp_path: Path) -> None:
    report = inspect_docx(
        write_docx(
            tmp_path,
            body=f"<w:p>{_direct_theme_run('繁體中文', language='zh-TW')}</w:p>",
            parts={"word/theme/theme1.xml": theme_xml(hans="简体主题", hant="繁體主題")},
        )
    )

    formatting = report["paragraphs"][0]["runs"][0]["formatting"]
    effective = formatting["effective"]
    evidence = effective["font"]["theme_evidence"]["eastAsia"]

    assert effective["font"]["eastAsia"] == "繁體主題"
    assert effective["eastAsia"] == "繁體主題"
    assert formatting["source"]["font"]["eastAsia"] == "theme"
    assert evidence == {
        "token": "majorEastAsia",
        "resolved": True,
        "script": "Hant",
        "available_scripts": ["Hans", "Hant"],
    }


def test_missing_theme_token_keeps_lower_concrete_east_asia_fallback(tmp_path: Path) -> None:
    body = (
        '<w:p><w:r><w:rPr><w:rFonts w:ascii="直接西文"/></w:rPr>'
        '<w:t>中文与 Latin</w:t></w:r></w:p>'
    )
    report = inspect_docx(
        write_docx(
            tmp_path,
            body=body,
            parts={
                "word/styles.xml": _styles_with_lower_east_asia_fallback(),
                "word/theme/theme1.xml": theme_xml(hans="简体主题", hant="繁體主題"),
            },
        )
    )

    formatting = report["paragraphs"][0]["runs"][0]["formatting"]
    effective = formatting["effective"]

    assert effective["font"]["ascii"] == "直接西文"
    assert formatting["source"]["font"]["ascii"] == "direct"
    assert effective["font"]["eastAsia"] == "默认段落字体"
    assert effective["eastAsia"] == "默认段落字体"
    assert formatting["source"]["font"]["eastAsia"] == "default_paragraph_style:Normal"


def test_direct_concrete_east_asia_font_overrides_lower_unresolved_theme(tmp_path: Path) -> None:
    styles = styles_xml(
        styles=(
            f'<w:style xmlns:w="{W}" w:type="paragraph" w:styleId="ThemeStyle">'
            '<w:name w:val="Theme Style"/><w:rPr>'
            '<w:rFonts w:eastAsiaTheme="majorEastAsia"/>'
            "</w:rPr></w:style>"
        )
    )
    body = (
        '<w:p><w:pPr><w:pStyle w:val="ThemeStyle"/></w:pPr><w:r><w:rPr>'
        '<w:rFonts w:eastAsia="直接字体"/>'
        "</w:rPr><w:t>直接中文</w:t></w:r></w:p>"
    )
    report = inspect_docx(
        write_docx(
            tmp_path,
            body=body,
            parts={
                "word/styles.xml": styles,
                "word/theme/theme1.xml": theme_xml(hans="简体主题", hant="繁體主題"),
            },
        )
    )

    formatting = report["paragraphs"][0]["runs"][0]["formatting"]
    effective = formatting["effective"]

    assert effective["font"]["eastAsia"] == "直接字体"
    assert effective["eastAsia"] == "直接字体"
    assert formatting["source"]["font"]["eastAsia"] == "direct"
    assert "eastAsia" not in effective["font"].get("theme", {})
