"""Small real OOXML packages used by the PR1 producer regression tests.

The fixtures intentionally bypass ``python-docx`` for the bits that are
defined by OOXML but not exposed by its public API.  They are still genuine
DOCX ZIP packages and are always exercised through ``inspect_docx``.
"""

from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
DC = "http://purl.org/dc/elements/1.1/"
CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"


def run(text: str, *, size_pt: float | None = None, font: str | None = None) -> str:
    properties = ""
    if size_pt is not None or font is not None:
        properties = "<w:rPr>"
        if size_pt is not None:
            properties += f'<w:sz w:val="{size_pt * 2:g}"/>'
        if font is not None:
            properties += f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:eastAsia="{font}"/>'
        properties += "</w:rPr>"
    escaped = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    return f"<w:r>{properties}<w:t xml:space=\"preserve\">{escaped}</w:t></w:r>"


def paragraph(
    *runs: str,
    style: str | None = None,
    num_id: int | None = None,
    ilvl: int | None = None,
    extra_ppr: str = "",
) -> str:
    ppr = ""
    if style is not None or num_id is not None or ilvl is not None or extra_ppr:
        ppr = "<w:pPr>"
        if style is not None:
            ppr += f'<w:pStyle w:val="{style}"/>'
        if num_id is not None or ilvl is not None:
            ppr += "<w:numPr>"
            if ilvl is not None:
                ppr += f'<w:ilvl w:val="{ilvl}"/>'
            if num_id is not None:
                ppr += f'<w:numId w:val="{num_id}"/>'
            ppr += "</w:numPr>"
        ppr += extra_ppr
        ppr += "</w:pPr>"
    return f"<w:p>{ppr}{''.join(runs)}</w:p>"


def table(*rows: tuple[str, ...]) -> str:
    row_xml = []
    for row in rows:
        row_xml.append(
            "<w:tr>"
            + "".join(f"<w:tc><w:p>{cell}</w:p></w:tc>" for cell in row)
            + "</w:tr>"
        )
    return f"<w:tbl><w:tblPr/><w:tblGrid/>{''.join(row_xml)}</w:tbl>"


def styles_xml(
    *,
    default_paragraph: str | None = None,
    default_character: str | None = None,
    styles: str = "",
    doc_defaults: str = "",
) -> bytes:
    records: list[str] = []
    if default_paragraph is not None:
        records.append(
            f'<w:style w:type="paragraph" w:default="1" w:styleId="{default_paragraph}">'
            f'<w:name w:val="{default_paragraph}"/></w:style>'
        )
    if default_character is not None:
        records.append(
            f'<w:style w:type="character" w:default="1" w:styleId="{default_character}">'
            f'<w:name w:val="{default_character}"/></w:style>'
        )
    xml = f'<w:styles xmlns:w="{W}">{doc_defaults}{"".join(records)}{styles}</w:styles>'
    return xml.encode()


def numbering_xml(*abstract_nums: str, nums: str = "") -> bytes:
    return f'<w:numbering xmlns:w="{W}">{"".join(abstract_nums)}{nums}</w:numbering>'.encode()


def theme_xml(*, hans: str | None = None, hant: str | None = None, ea: str | None = None) -> bytes:
    scripts = ""
    if hans is not None:
        scripts += f'<a:font script="Hans" typeface="{hans}"/>'
    if hant is not None:
        scripts += f'<a:font script="Hant" typeface="{hant}"/>'
    east_asia = f'<a:ea typeface="{ea}"/>' if ea is not None else '<a:ea typeface=""/>'
    return (
        '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:themeElements><a:fontScheme name="fixture">'
        f'<a:majorFont><a:latin typeface="Aptos"/>{east_asia}<a:cs typeface="Arial"/>{scripts}</a:majorFont>'
        '<a:minorFont><a:latin typeface="Aptos"/><a:ea typeface=""/><a:cs typeface="Arial"/></a:minorFont>'
        '</a:fontScheme></a:themeElements></a:theme>'
    ).encode()


def footnotes_xml(*notes: tuple[int, str], note_properties: str = "") -> bytes:
    values = "".join(
        f'<w:footnote w:id="{note_id}"><w:p>{value}</w:p></w:footnote>'
        for note_id, value in notes
    )
    return (
        f'<w:footnotes xmlns:w="{W}">{note_properties}'
        '<w:footnote w:type="separator" w:id="-1"><w:p/></w:footnote>'
        '<w:footnote w:type="continuationSeparator" w:id="0"><w:p/></w:footnote>'
        f"{values}</w:footnotes>"
    ).encode()


def _content_types(parts: set[str]) -> bytes:
    overrides = [
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>',
    ]
    content_types = {
        "word/styles.xml": "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml",
        "word/numbering.xml": "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml",
        "word/theme/theme1.xml": "application/vnd.openxmlformats-officedocument.theme+xml",
        "word/footnotes.xml": "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml",
        "docProps/core.xml": "application/vnd.openxmlformats-package.core-properties+xml",
    }
    for part_name in sorted(parts):
        content_type = content_types.get(part_name)
        if content_type:
            overrides.append(f'<Override PartName="/{part_name}" ContentType="{content_type}"/>')
    return (
        f'<Types xmlns="{CT}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        + "".join(overrides)
        + "</Types>"
    ).encode()


def _relationships(parts: set[str]) -> bytes:
    values = []
    rel_types = {
        "word/styles.xml": f"{R}/styles",
        "word/numbering.xml": f"{R}/numbering",
        "word/theme/theme1.xml": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme",
        "word/footnotes.xml": f"{R}/footnotes",
        "docProps/core.xml": "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
    }
    for index, part_name in enumerate(sorted(parts), start=1):
        if part_name == "word/theme/theme1.xml":
            target = "theme/theme1.xml"
        elif part_name.startswith("word/"):
            target = part_name.removeprefix("word/")
        else:
            continue
        values.append(f'<Relationship Id="rId{index}" Type="{rel_types[part_name]}" Target="{target}"/>')
    return f'<Relationships xmlns="{PR}">{"".join(values)}</Relationships>'.encode()


def write_docx(
    tmp_path: Path,
    *,
    body: str,
    parts: dict[str, bytes] | None = None,
    core_properties: dict[str, str] | None = None,
    filename: str = "pr1-fixture.docx",
) -> Path:
    path = Path(tmp_path) / filename
    members: dict[str, bytes] = {
        "[Content_Types].xml": b"",
        "word/document.xml": (
            f'<w:document xmlns:w="{W}" xmlns:r="{R}">'
            f"<w:body>{body}<w:sectPr/></w:body></w:document>"
        ).encode(),
    }
    if parts:
        members.update(parts)
    if core_properties:
        values = []
        for key, value in core_properties.items():
            namespace = DC if key in {"title", "subject", "creator", "description"} else CP
            tag = {"last_modified_by": "lastModifiedBy"}.get(key, key)
            values.append(f'<p:{tag} xmlns:p="{namespace}">{value}</p:{tag}>')
        members["docProps/core.xml"] = (
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            + "".join(values)
            + "</cp:coreProperties>"
        ).encode()
    part_names = set(members) - {"[Content_Types].xml", "word/document.xml", "word/_rels/document.xml.rels"}
    members["[Content_Types].xml"] = _content_types(part_names)
    if part_names:
        members["word/_rels/document.xml.rels"] = _relationships(part_names)
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name, value in members.items():
            archive.writestr(name, value)
    return path


__all__ = [
    "CP",
    "DC",
    "PR",
    "R",
    "W",
    "footnotes_xml",
    "numbering_xml",
    "paragraph",
    "run",
    "styles_xml",
    "table",
    "theme_xml",
    "write_docx",
]
