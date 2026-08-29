"""WordprocessingML equation and embedded-object inspection."""

from __future__ import annotations

from typing import Any

from .package import DocxPackage, R_NS, W_NS, qname


M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
O_NS = "urn:schemas-microsoft-com:office:office"
_OLE_MARKERS = (
    "mathtype",
    "equation.3",
    "equation3",
    "mtef",
    "application/vnd.ms-equation",
    "application/x-mathtype",
)


def _object_text(value: str | None) -> str:
    return (value or "").strip()


def _object_evidence(package: DocxPackage, root: Any, paragraph_ids: dict[int, str]) -> list[dict[str, Any]]:
    """Collect OLE/MathType evidence from object nodes and relationships."""

    relationships = package.relationships("word/document.xml")
    used_relationships: set[str] = set()
    records: list[dict[str, Any]] = []
    object_nodes = list(root.iter(qname(W_NS, "object")))

    def classify(
        *,
        relationship_id: str | None,
        relation: Any | None,
        prog_id: str | None,
        object_index: int | None,
        object_node: Any | None,
    ) -> dict[str, Any]:
        target = None
        payload = b""
        if relation is not None:
            target = relation.target if relation.target_mode == "External" else package.resolve_target("word/document.xml", relation.target)
            if relation.target_mode != "External":
                payload = package.read(target) or b""
        content_type = package.content_type(target) if target and relation is not None and relation.target_mode != "External" else None
        payload_text = payload[:4096].decode("latin1", errors="ignore")
        evidence: list[str] = []
        fields = [prog_id or "", target or "", content_type or "", payload_text]
        haystack = " ".join(fields).casefold()
        if prog_id:
            evidence.append("prog_id")
        if content_type:
            evidence.append("content_type")
        if target:
            evidence.append("relationship_target")
        if any(marker in haystack for marker in _OLE_MARKERS):
            if prog_id and any(marker in prog_id.casefold() for marker in _OLE_MARKERS):
                evidence.append("prog_id_mathtype")
            if content_type and any(marker in content_type.casefold() for marker in _OLE_MARKERS):
                evidence.append("content_type_mathtype")
            if payload_text and any(marker in payload_text.casefold() for marker in _OLE_MARKERS):
                evidence.append("payload_mathtype")
        editor = "mathtype" if any(
            marker in value.casefold()
            for marker in _OLE_MARKERS
            for value in (prog_id or "", target or "", content_type or "", payload_text)
        ) else "ole_unknown"
        paragraph = next(
            (ancestor for ancestor in object_node.iterancestors() if ancestor.tag == qname(W_NS, "p")),
            None,
        ) if object_node is not None else None
        record: dict[str, Any] = {
            "id": f"ole-{len(records):06d}",
            "editor": editor,
            "kind": "ole_object",
            "paragraph_id": paragraph_ids.get(id(paragraph)) if paragraph is not None else None,
            "object_index": object_index,
            "relationship_id": relationship_id,
            "part": target,
            "content_type": content_type,
            "prog_id": prog_id,
            "evidence": sorted(set(evidence)) or ["ole_relationship"],
            "relationship_only": object_node is None,
        }
        return record

    for object_index, object_node in enumerate(object_nodes):
        ole_nodes = list(object_node.iter(qname(O_NS, "OLEObject")))
        relationship_id: str | None = None
        relation = None
        for candidate in ole_nodes or [object_node]:
            candidate_id = candidate.get(qname(R_NS, "id")) or candidate.get(qname(R_NS, "embed"))
            candidate_relation = relationships.get(candidate_id or "")
            if candidate_relation is not None and (
                candidate_relation.rel_type.endswith("/oleObject")
                or candidate_relation.rel_type.endswith("/package")
            ):
                relationship_id = candidate_id
                relation = candidate_relation
                break
        prog_id = None
        if ole_nodes:
            for key in ("ProgID", "progId", "progid"):
                prog_id = ole_nodes[0].get(key) or ole_nodes[0].get(qname(O_NS, key))
                if prog_id:
                    break
        record = classify(
            relationship_id=relationship_id,
            relation=relation,
            prog_id=prog_id,
            object_index=object_index,
            object_node=object_node,
        )
        if relationship_id:
            used_relationships.add(relationship_id)
        records.append(record)

    # Some producers retain the OLE relationship but omit the visible
    # ``w:object`` wrapper.  Count that evidence rather than reporting zero.
    for relationship_id, relation in relationships.items():
        if relationship_id in used_relationships:
            continue
        if not (relation.rel_type.endswith("/oleObject") or relation.rel_type.endswith("/package")):
            continue
        records.append(
            classify(
                relationship_id=relationship_id,
                relation=relation,
                prog_id=None,
                object_index=None,
                object_node=None,
            )
        )
    return records


def inspect_equations(package: DocxPackage, paragraph_ids: dict[int, str]) -> dict[str, Any]:
    root = package.document_root
    omath_nodes = list(root.iter(qname(M_NS, "oMath")))
    omath_para_nodes = list(root.iter(qname(M_NS, "oMathPara")))
    paragraph_ids_with_equation: list[str] = []
    items: list[dict[str, Any]] = []
    for node in omath_nodes:
        paragraph = next((ancestor for ancestor in node.iterancestors() if ancestor.tag == qname(W_NS, "p")), None)
        paragraph_id = paragraph_ids.get(id(paragraph)) if paragraph is not None else None
        if paragraph_id and paragraph_id not in paragraph_ids_with_equation:
            paragraph_ids_with_equation.append(paragraph_id)
        text = "".join((element.text or "") for element in node.iter(qname(M_NS, "t")))
        items.append({
            "id": f"eq-{len(items):06d}",
            "paragraph_id": paragraph_id,
            "editor": "Word Equation",
            "kind": "oMathPara" if any(ancestor.tag == qname(M_NS, "oMathPara") for ancestor in node.iterancestors()) else "oMath",
            "text_preview": text[:80],
        })

    objects = _object_evidence(package, root, paragraph_ids)
    items.extend(objects)
    mathtype_count = sum(item["editor"] == "mathtype" for item in objects)
    ole_unknown_count = sum(item["editor"] == "ole_unknown" for item in objects)
    return {
        "omath_count": len(omath_nodes),
        "omath_para_count": len(omath_para_nodes),
        "paragraph_ids": paragraph_ids_with_equation,
        "items": items,
        "object_count": len(objects),
        "editors": {
            "word_equation": len(omath_nodes),
            "mathtype": mathtype_count,
            "ole_unknown": ole_unknown_count,
            # Keep the legacy aggregate while exposing the reason for each
            # non-OMML object above.
            "mathtype_or_ole": len(objects),
        },
    }


__all__ = ["inspect_equations"]
