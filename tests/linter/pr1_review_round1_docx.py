"""Real DOCX fixtures for PR1 review-round-one regressions.

The helpers produce genuine OOXML packages and keep the tests on the public
``inspect_docx`` boundary.  They intentionally expose structural heading
metadata and note-reference metadata in the package so the classifier and
linter cannot be bypassed with hand-written Inspector dictionaries.
"""

from __future__ import annotations

from pathlib import Path

from tests.inspector.pr1_docx_factory import (
    footnotes_xml,
    numbering_xml,
    paragraph,
    run,
    styles_xml,
    write_docx,
)


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_heading_prefix_swap_docx(tmp_path: Path) -> Path:
    """Create L2/L3/L4 structure whose visible L3/L4 prefixes are swapped.

    The structural metadata remains valid and independent: the paragraph
    styles, Word outline levels, and resolved numbering levels all agree on
    L2, L3, and L4 respectively.  Only the visible lower-level markers are
    wrong, which is the producer-chain case that must not be classified as a
    self-consistent PASS.
    """

    styles = styles_xml(
        styles=(
            f'<w:style xmlns:w="{W}" w:type="paragraph" w:styleId="Heading2">'
            '<w:name w:val="Heading 2"/><w:pPr><w:outlineLvl w:val="1"/></w:pPr></w:style>'
            f'<w:style xmlns:w="{W}" w:type="paragraph" w:styleId="Heading3">'
            '<w:name w:val="Heading 3"/><w:pPr><w:outlineLvl w:val="2"/></w:pPr></w:style>'
            f'<w:style xmlns:w="{W}" w:type="paragraph" w:styleId="Heading4">'
            '<w:name w:val="Heading 4"/><w:pPr><w:outlineLvl w:val="3"/></w:pPr></w:style>'
        )
    )
    numbering = numbering_xml(
        f'<w:abstractNum xmlns:w="{W}" w:abstractNumId="0">'
        '<w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>'
        '<w:lvl w:ilvl="1"><w:numFmt w:val="decimal"/><w:lvlText w:val="%1.%2"/></w:lvl>'
        '<w:lvl w:ilvl="2"><w:numFmt w:val="decimal"/><w:lvlText w:val="%1.%2.%3"/></w:lvl>'
        '<w:lvl w:ilvl="3"><w:numFmt w:val="decimal"/><w:lvlText w:val="%1.%2.%3.%4"/></w:lvl>'
        "</w:abstractNum>",
        nums=f'<w:num xmlns:w="{W}" w:numId="7"><w:abstractNumId w:val="0"/></w:num>',
    )
    body = (
        paragraph(run("  （一）理论分析"), style="Heading2", num_id=7, ilvl=1)
        + paragraph(run("（1）机制分析"), style="Heading3", num_id=7, ilvl=2)
        + paragraph(run("1. 变量定义"), style="Heading4", num_id=7, ilvl=3)
    )
    return write_docx(
        tmp_path,
        body=body,
        parts={"word/styles.xml": styles, "word/numbering.xml": numbering},
        filename="pr1-heading-prefix-swap.docx",
    )


def make_visible_only_heading_prefix_swap_docx(tmp_path: Path) -> Path:
    """Create the same visible swap without independent structure metadata."""

    body = (
        paragraph(run("  （一）理论分析"))
        + paragraph(run("（1）机制分析"))
        + paragraph(run("1. 变量定义"))
    )
    return write_docx(tmp_path, body=body, filename="pr1-visible-only-prefix-swap.docx")


def make_title_marker_docx(tmp_path: Path, marker_case: str) -> Path:
    """Create a title footnote reference with custom, numbered, or unknown marker evidence."""

    marker_xml = {
        "custom": '<w:r><w:footnoteReference w:id="1" w:customMarkFollows="1"/><w:t>*</w:t></w:r>',
        "numbered": '<w:r><w:footnoteReference w:id="1"/></w:r>',
        "unknown": '<w:r><w:footnoteReference w:id="1" w:customMarkFollows="1"/></w:r>',
    }[marker_case]
    body = paragraph(run("题目：研究标题") + marker_xml)
    return write_docx(
        tmp_path,
        body=body,
        parts={"word/footnotes.xml": footnotes_xml((1, run("作者信息")))},
        filename=f"pr1-title-marker-{marker_case}.docx",
    )


def make_author_information_docx(tmp_path: Path, text: str, filename: str) -> Path:
    """Create a real body paragraph containing the requested author-info text."""

    return write_docx(tmp_path, body=paragraph(run(text)), filename=filename)


__all__ = [
    "make_author_information_docx",
    "make_heading_prefix_swap_docx",
    "make_title_marker_docx",
    "make_visible_only_heading_prefix_swap_docx",
]
