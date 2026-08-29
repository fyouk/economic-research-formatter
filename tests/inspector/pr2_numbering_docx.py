"""Real OOXML numbering fixtures for the PR1 second-round regressions.

The helpers intentionally write a DOCX ZIP directly because numbering
semantics such as ``w:lvl/w:pStyle`` and sparse ``w:numPr`` values are not
available through the public ``python-docx`` API.  Every test still begins at
``inspect_docx`` so the producer boundary is exercised end to end.
"""

from __future__ import annotations

from pathlib import Path

from inspector.pr1_docx_factory import W, numbering_xml, paragraph, run, styles_xml, write_docx


def paragraph_style(
    style_id: str,
    *,
    based_on: str | None = None,
    num_id: int | None = None,
    ilvl: int | None = None,
) -> str:
    """Build one paragraph style with an optional sparse ``numPr``."""

    based_on_xml = f'<w:basedOn w:val="{based_on}"/>' if based_on is not None else ""
    num_pr = ""
    if num_id is not None or ilvl is not None:
        num_pr = "<w:numPr>"
        if ilvl is not None:
            num_pr += f'<w:ilvl w:val="{ilvl}"/>'
        if num_id is not None:
            num_pr += f'<w:numId w:val="{num_id}"/>'
        num_pr += "</w:numPr>"
    ppr = f"<w:pPr>{num_pr}</w:pPr>" if num_pr else ""
    return (
        f'<w:style xmlns:w="{W}" w:type="paragraph" w:styleId="{style_id}">'
        f'<w:name w:val="{style_id}"/>{based_on_xml}{ppr}</w:style>'
    )


def abstract_num(
    abstract_id: int,
    *levels: str,
) -> str:
    return (
        f'<w:abstractNum xmlns:w="{W}" w:abstractNumId="{abstract_id}">'
        + "".join(levels)
        + "</w:abstractNum>"
    )


def level(
    ilvl: int,
    *,
    fmt: str | None = "decimal",
    text: str | None = "%1.",
    pstyle: str | None = None,
    start: int | None = None,
) -> str:
    children = ""
    if start is not None:
        children += f'<w:start w:val="{start}"/>'
    if fmt is not None:
        children += f'<w:numFmt w:val="{fmt}"/>'
    if text is not None:
        children += f'<w:lvlText w:val="{text}"/>'
    if pstyle is not None:
        children += f'<w:pStyle w:val="{pstyle}"/>'
    return f'<w:lvl xmlns:w="{W}" w:ilvl="{ilvl}">{children}</w:lvl>'


def num(
    num_id: int,
    abstract_id: int,
    *,
    overrides: str = "",
) -> str:
    return (
        f'<w:num xmlns:w="{W}" w:numId="{num_id}">'
        f'<w:abstractNumId w:val="{abstract_id}"/>{overrides}</w:num>'
    )


def start_override(ilvl: int, value: int) -> str:
    return (
        f'<w:lvlOverride xmlns:w="{W}" w:ilvl="{ilvl}">'
        f'<w:startOverride w:val="{value}"/></w:lvlOverride>'
    )


def level_override(
    ilvl: int,
    *,
    fmt: str | None = None,
    text: str | None = None,
    start: int | None = None,
    start_value: int | None = None,
    pstyle: str | None = None,
) -> str:
    children = ""
    if fmt is not None or text is not None or start is not None or pstyle is not None:
        nested = ""
        if start is not None:
            nested += f'<w:start w:val="{start}"/>'
        if fmt is not None:
            nested += f'<w:numFmt w:val="{fmt}"/>'
        if text is not None:
            nested += f'<w:lvlText w:val="{text}"/>'
        if pstyle is not None:
            nested += f'<w:pStyle w:val="{pstyle}"/>'
        children += f'<w:lvl>{nested}</w:lvl>'
    if start_value is not None:
        children += f'<w:startOverride w:val="{start_value}"/>'
    return f'<w:lvlOverride xmlns:w="{W}" w:ilvl="{ilvl}">{children}</w:lvlOverride>'


def write_numbering_docx(
    tmp_path: Path,
    *,
    body: str,
    styles: str = "",
    abstract_nums: tuple[str, ...] = (),
    nums: str = "",
    filename: str = "pr2-numbering-fixture.docx",
) -> Path:
    parts = {
        "word/styles.xml": styles_xml(styles=styles),
        "word/numbering.xml": numbering_xml(*abstract_nums, nums=nums),
    }
    return write_docx(tmp_path, body=body, parts=parts, filename=filename)


__all__ = [
    "abstract_num",
    "level",
    "level_override",
    "num",
    "paragraph",
    "paragraph_style",
    "run",
    "start_override",
    "write_numbering_docx",
]
