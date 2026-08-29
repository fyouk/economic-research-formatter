"""Real OOXML fixtures for PR #1 second-round formatting regressions.

These helpers deliberately construct only the OOXML pieces that ``python-docx``
does not expose.  Every test using them starts at ``inspect_docx`` and proceeds
through classification and linting; no final Inspector dictionary is injected.
"""

from __future__ import annotations

from pathlib import Path

from tests.inspector.pr1_docx_factory import (
    W,
    footnotes_xml,
    paragraph,
    run,
    styles_xml,
    table,
    theme_xml,
    write_docx,
)


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _theme_run(
    text: str,
    *,
    east_asia_theme: str | None = None,
    ascii_theme: str | None = None,
    hansi_theme: str | None = None,
    east_asia: str | None = None,
    size_pt: float | None = None,
    lang_east_asia: str | None = None,
) -> str:
    attributes: list[str] = []
    for name, value in (
        ("eastAsiaTheme", east_asia_theme),
        ("asciiTheme", ascii_theme),
        ("hAnsiTheme", hansi_theme),
        ("eastAsia", east_asia),
    ):
        if value is not None:
            attributes.append(f'w:{name}="{_escape(value)}"')
    properties = ""
    if attributes or size_pt is not None or lang_east_asia is not None:
        properties = "<w:rPr>"
        if attributes:
            properties += f"<w:rFonts {' '.join(attributes)}/>"
        if size_pt is not None:
            properties += f'<w:sz w:val="{size_pt * 2:g}"/>'
        if lang_east_asia is not None:
            properties += f'<w:lang w:eastAsia="{_escape(lang_east_asia)}"/>'
        properties += "</w:rPr>"
    return f'<w:r>{properties}<w:t xml:space="preserve">{_escape(text)}</w:t></w:r>'


def _doc_defaults(*, lang_east_asia: str | None = None) -> str:
    lang = f'<w:lang w:eastAsia="{lang_east_asia}"/>' if lang_east_asia else ""
    return (
        f'<w:docDefaults xmlns:w="{W}"><w:rPrDefault><w:rPr>{lang}</w:rPr>'
        "</w:rPrDefault><w:pPrDefault><w:pPr/></w:pPrDefault></w:docDefaults>"
    )


def make_unresolved_theme_title_docx(tmp_path: Path) -> Path:
    """Create a title whose Hans/Hant theme token has no script evidence."""

    body = paragraph(
        _theme_run("题目：研究标题", east_asia_theme="majorEastAsia", size_pt=16),
        extra_ppr='<w:jc w:val="center"/>',
    )
    return write_docx(
        tmp_path,
        body=body,
        parts={
            "word/styles.xml": styles_xml(default_paragraph="Normal"),
            "word/theme/theme1.xml": theme_xml(hans="简体主题", hant="繁體主題"),
        },
        filename="pr2-unresolved-theme-title.docx",
    )


def make_mixed_mismatch_and_unresolved_theme_title_docx(tmp_path: Path) -> Path:
    """Create one definite wrong run plus one unresolved theme run."""

    body = paragraph(
        _theme_run("题目：确定错误", east_asia="黑体", size_pt=16),
        _theme_run("与未知主题", east_asia_theme="majorEastAsia", size_pt=16),
        extra_ppr='<w:jc w:val="center"/>',
    )
    return write_docx(
        tmp_path,
        body=body,
        parts={"word/theme/theme1.xml": theme_xml(hans="简体主题", hant="繁體主題")},
        filename="pr2-mixed-mismatch-unresolved-theme-title.docx",
    )


def make_doc_defaults_theme_title_docx(tmp_path: Path, *, language: str) -> Path:
    """Create a title resolved from ``docDefaults/w:lang`` (Hans or Hant)."""

    body = paragraph(
        _theme_run("题目：研究标题", east_asia_theme="majorEastAsia", size_pt=16),
        extra_ppr='<w:jc w:val="center"/>',
    )
    theme_kwargs = {"hans": None, "hant": None}
    theme_kwargs["hans" if language.casefold().startswith(("zh-cn", "zh-sg", "zh-hans")) else "hant"] = "宋体"
    return write_docx(
        tmp_path,
        body=body,
        parts={
            "word/styles.xml": styles_xml(
                default_paragraph="Normal",
                doc_defaults=_doc_defaults(lang_east_asia=language),
            ),
            "word/theme/theme1.xml": theme_xml(**theme_kwargs),
        },
        filename=f"pr2-doc-defaults-theme-{language.casefold().replace('-', '_')}.docx",
    )


def make_concrete_override_theme_title_docx(tmp_path: Path) -> Path:
    """Create a direct concrete font over a lower unresolved theme token."""

    styles = styles_xml(
        styles=(
            f'<w:style xmlns:w="{W}" w:type="paragraph" w:styleId="ThemeStyle">'
            '<w:name w:val="Theme Style"/><w:rPr>'
            '<w:rFonts w:eastAsiaTheme="majorEastAsia"/>'
            "</w:rPr></w:style>"
        )
    )
    body = paragraph(
        _theme_run("题目：直接字体标题", east_asia_theme="majorEastAsia", east_asia="宋体", size_pt=16),
        style="ThemeStyle",
        extra_ppr='<w:jc w:val="center"/>',
    )
    return write_docx(
        tmp_path,
        body=body,
        parts={"word/styles.xml": styles, "word/theme/theme1.xml": theme_xml(hans="主题简体", hant="主题繁體")},
        filename="pr2-concrete-over-theme-title.docx",
    )


def make_unresolved_latin_theme_docx(tmp_path: Path) -> Path:
    """Create body Latin text with unresolved ascii/hAnsi theme tokens."""

    body = paragraph(
        _theme_run("analysis 123", ascii_theme="majorAscii", hansi_theme="majorHAnsi"),
    )
    return write_docx(tmp_path, body=body, filename="pr2-unresolved-latin-theme.docx")


def make_mixed_latin_mismatch_and_unresolved_theme_docx(tmp_path: Path) -> Path:
    """Create one definite Latin-font mismatch plus one unresolved theme run."""

    body = paragraph(
        run("bad Latin", font="Arial"),
        _theme_run(" unknown Latin", ascii_theme="majorAscii", hansi_theme="majorHAnsi"),
    )
    return write_docx(tmp_path, body=body, filename="pr2-mixed-latin-mismatch-theme.docx")


def make_title_star_docx(tmp_path: Path, *, reference_first: bool) -> Path:
    """Create title + adjacent literal star while customMarkFollows is false."""

    reference = '<w:footnoteReference w:id="1" w:customMarkFollows="0"/>'
    literal = '<w:t>*</w:t>'
    marker_run = f"<w:r>{reference}{literal}</w:r>" if reference_first else f"<w:r>{literal}{reference}</w:r>"
    body = paragraph(run("题目：作者信息") + marker_run)
    return write_docx(
        tmp_path,
        body=body,
        parts={"word/footnotes.xml": footnotes_xml((1, run("作者信息")))},
        filename=f"pr2-title-star-{int(reference_first)}.docx",
    )


def make_font_only_table_note_docx(tmp_path: Path) -> Path:
    """Create a table-note with concrete 宋体 but unknown size relation."""

    body = table((run("数据", font="宋体"),)) + paragraph(run("注：说明", font="宋体", size_pt=9))
    return write_docx(tmp_path, body=body, filename="pr2-font-only-table-note.docx")


def make_repeated_unknown_table_notes_docx(tmp_path: Path) -> Path:
    """Create several note cells in one table with no resolvable size data."""

    body = table((run("注：A"), run("注：B"), run("注：C"), run("注：D")))
    return write_docx(tmp_path, body=body, filename="pr2-repeated-unknown-table-notes.docx")


def make_mismatched_table_note_docx(tmp_path: Path) -> Path:
    """Create a table note with resolved but non-required concrete font."""

    body = table((run("数据", font="宋体", size_pt=10.5),)) + paragraph(
        run("注：说明", font="黑体", size_pt=9)
    )
    return write_docx(tmp_path, body=body, filename="pr2-mismatched-table-note.docx")


__all__ = [
    "make_concrete_override_theme_title_docx",
    "make_doc_defaults_theme_title_docx",
    "make_font_only_table_note_docx",
    "make_mismatched_table_note_docx",
    "make_mixed_mismatch_and_unresolved_theme_title_docx",
    "make_mixed_latin_mismatch_and_unresolved_theme_docx",
    "make_repeated_unknown_table_notes_docx",
    "make_title_star_docx",
    "make_unresolved_latin_theme_docx",
    "make_unresolved_theme_title_docx",
]
