"""Safe, read-only access to the parts of an OOXML package."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from pathlib import Path
from zipfile import BadZipFile, LargeZipFile, ZipFile

from lxml import etree

from ..models.inspection import DocxInspectionError


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
PKG_REL_TYPE = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {
    "w": W_NS,
    "r": R_NS,
    "pr": REL_NS,
    "ct": CT_NS,
}

# Resource limits are intentionally generous for ordinary manuscripts while
# keeping ZIP bombs and oversized XML/image parts out of memory.  These checks
# run on ZipInfo metadata before any member payload is read.
MAX_ZIP_MEMBERS = 10_000
MAX_MEMBER_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 256 * 1024 * 1024


def safe_xml_fromstring(raw: bytes) -> etree._Element:
    """Parse OOXML with external entities/network and huge trees disabled."""

    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        huge_tree=False,
        recover=False,
    )
    return etree.fromstring(raw, parser=parser)


def local_name(element: etree._Element) -> str:
    return etree.QName(element).localname


def qname(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


@dataclass(frozen=True)
class Relationship:
    id: str
    rel_type: str
    target: str
    target_mode: str | None = None


@dataclass
class DocxPackage:
    """In-memory snapshot of a DOCX package.

    Reading all members once gives deterministic inspection and ensures no
    open archive handle or accidental write operation survives the call.
    """

    path: Path
    parts: dict[str, bytes]
    document_root: etree._Element
    content_types_root: etree._Element
    _xml_cache: dict[str, etree._Element]

    @classmethod
    def open(cls, path: str | Path) -> "DocxPackage":
        source = Path(path)
        if not source.is_file():
            raise DocxInspectionError(f"DOCX input does not exist: {source}", kind="missing_input", path=source)
        if source.suffix.lower() != ".docx":
            raise DocxInspectionError(f"Input is not a DOCX file: {source.name}", kind="not_docx", path=source)
        try:
            with ZipFile(source, "r") as archive:
                # ``filelist`` is ZipFile's already-parsed metadata table;
                # reusing it avoids an additional names list.  Validate names
                # and uncompressed sizes before reading any payload so a
                # compressed bomb cannot force a full parts dictionary.
                infos = archive.filelist
                if len(infos) > MAX_ZIP_MEMBERS:
                    raise DocxInspectionError(
                        f"DOCX ZIP contains too many members ({len(infos)} > {MAX_ZIP_MEMBERS})",
                        kind="resource_limit",
                        path=source,
                    )
                seen_names: set[str] = set()
                total_uncompressed = 0
                for info in infos:
                    name = info.filename
                    if not name or name in seen_names:
                        reason = "empty" if not name else "duplicate"
                        raise DocxInspectionError(
                            f"DOCX ZIP contains {reason} member name: {name!r}",
                            kind="zip_member",
                            path=source,
                        )
                    seen_names.add(name)
                    if name.startswith("/") or any(part == ".." for part in name.replace("\\", "/").split("/")):
                        raise DocxInspectionError(
                            f"DOCX ZIP contains unsafe member path: {name!r}",
                            kind="zip_member",
                            path=source,
                        )
                    member_size = int(info.file_size)
                    if member_size > MAX_MEMBER_UNCOMPRESSED_BYTES:
                        raise DocxInspectionError(
                            f"DOCX ZIP member exceeds uncompressed size limit: {name}",
                            kind="resource_limit",
                            path=source,
                        )
                    total_uncompressed += member_size
                    if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
                        raise DocxInspectionError(
                            "DOCX ZIP exceeds total uncompressed size limit",
                            kind="resource_limit",
                            path=source,
                        )
                parts: dict[str, bytes] = {}
                for info in infos:
                    try:
                        parts[info.filename] = archive.read(info)
                    except (BadZipFile, KeyError, OSError, RuntimeError, EOFError) as exc:
                        raise DocxInspectionError(
                            f"Could not read DOCX ZIP member: {info.filename}",
                            kind="zip_read",
                            path=source,
                        ) from exc
        except DocxInspectionError:
            raise
        except (BadZipFile, LargeZipFile) as exc:
            raise DocxInspectionError(f"Invalid DOCX ZIP package: {source.name}", kind="zip", path=source) from exc
        except (OSError, RuntimeError) as exc:
            raise DocxInspectionError(f"Could not read DOCX input: {source.name}", kind="io", path=source) from exc

        for required in ("[Content_Types].xml", "word/document.xml"):
            if required not in parts:
                raise DocxInspectionError(
                    f"DOCX package is missing core part: {required}", kind="core_part", path=source
                )
        xml_cache: dict[str, etree._Element] = {}
        try:
            for name, raw in parts.items():
                if name.lower().endswith((".xml", ".rels")):
                    xml_cache[name] = safe_xml_fromstring(raw)
            content_types = xml_cache["[Content_Types].xml"]
            document = xml_cache["word/document.xml"]
        except (KeyError, etree.XMLSyntaxError, ValueError) as exc:
            bad_part = next(
                (
                    name
                    for name, raw in parts.items()
                    if name.lower().endswith((".xml", ".rels"))
                    and name not in xml_cache
                ),
                "[Content_Types].xml" if "[Content_Types].xml" not in xml_cache else "word/document.xml",
            )
            raise DocxInspectionError(
                f"DOCX XML part is corrupt: {bad_part}", kind="xml_part", path=source
            ) from exc
        if local_name(document) != "document" or document.find(qname(W_NS, "body")) is None:
            raise DocxInspectionError(
                f"DOCX core part word/document.xml is not a Word document: {source.name}",
                kind="core_part",
                path=source,
            )
        package = cls(source, parts, document, content_types, xml_cache)
        package._validate_declared_parts()
        package._validate_relationship_targets()
        return package

    def read(self, part_name: str) -> bytes | None:
        return self.parts.get(part_name)

    def xml(self, part_name: str) -> etree._Element | None:
        if part_name in self._xml_cache:
            return self._xml_cache[part_name]
        raw = self.parts.get(part_name)
        if raw is None:
            return None
        try:
            parsed = safe_xml_fromstring(raw)
        except (etree.XMLSyntaxError, ValueError) as exc:
            raise DocxInspectionError(
                f"DOCX XML part is corrupt: {part_name}",
                kind="xml_part",
                path=self.path,
            ) from exc
        self._xml_cache[part_name] = parsed
        return parsed

    def _validate_declared_parts(self) -> None:
        """Reject content-type declarations that point to missing parts."""

        for override in self.content_types_root.findall(qname(CT_NS, "Override")):
            part_name = override.get("PartName", "").lstrip("/")
            if part_name and part_name not in self.parts:
                raise DocxInspectionError(
                    f"DOCX content type declares missing part: {part_name}",
                    kind="missing_part",
                    path=self.path,
                )

    def _validate_relationship_targets(self) -> None:
        """Reject local relationship targets that are absent from the ZIP."""

        for rels_name in (name for name in self.parts if name.lower().endswith(".rels")):
            root = self.xml(rels_name)
            if root is None:
                continue
            if "/_rels/" in rels_name:
                prefix, rel_basename = rels_name.split("/_rels/", 1)
                source_part = posixpath.join(prefix, rel_basename.removesuffix(".rels"))
            else:
                source_part = ""
            for relation in root.findall(qname(REL_NS, "Relationship")):
                if relation.get("TargetMode") == "External":
                    continue
                target = relation.get("Target")
                if not target:
                    continue
                resolved = self.resolve_target(source_part, target)
                if resolved not in self.parts:
                    raise DocxInspectionError(
                        f"DOCX relationship references missing part: {resolved}",
                        kind="missing_part",
                        path=self.path,
                    )

    def relationships(self, part_name: str = "word/document.xml") -> dict[str, Relationship]:
        rels_part = f"{posixpath.dirname(part_name)}/_rels/{posixpath.basename(part_name)}.rels"
        root = self.xml(rels_part)
        if root is None:
            return {}
        relationships: dict[str, Relationship] = {}
        for element in root.findall(qname(REL_NS, "Relationship")):
            rel_id = element.get("Id")
            rel_type = element.get("Type")
            target = element.get("Target")
            if rel_id and rel_type and target:
                relationships[rel_id] = Relationship(
                    rel_id,
                    rel_type,
                    target,
                    element.get("TargetMode"),
                )
        return relationships

    def resolve_target(self, source_part: str, target: str) -> str:
        if target.startswith("/"):
            return target.lstrip("/")
        return posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))

    def relationship_target(self, source_part: str, relationship_id: str) -> tuple[Relationship, str | None] | None:
        relation = self.relationships(source_part).get(relationship_id)
        if relation is None:
            return None
        if relation.target_mode == "External":
            return relation, relation.target
        return relation, self.resolve_target(source_part, relation.target)

    def content_type(self, part_name: str) -> str | None:
        for override in self.content_types_root.findall(qname(CT_NS, "Override")):
            if override.get("PartName", "").lstrip("/") == part_name:
                return override.get("ContentType")
        suffix = Path(part_name).suffix.lstrip(".").lower()
        for default in self.content_types_root.findall(qname(CT_NS, "Default")):
            if default.get("Extension", "").lower() == suffix:
                return default.get("ContentType")
        return None


__all__ = [
    "CT_NS",
    "DocxPackage",
    "MAX_MEMBER_UNCOMPRESSED_BYTES",
    "MAX_TOTAL_UNCOMPRESSED_BYTES",
    "MAX_ZIP_MEMBERS",
    "NS",
    "R_NS",
    "REL_NS",
    "W_NS",
    "local_name",
    "qname",
    "safe_xml_fromstring",
]
