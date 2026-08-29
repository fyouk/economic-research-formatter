"""Word field extraction and TOC/citation-manager evidence.

Complex Word fields are not guaranteed to keep their instruction in one
``w:instrText`` node.  Word may split even a field name (for example ``TO``
and ``C``) over several runs, so this module parses the field character
boundary and assembles the instruction before classifying it.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any

from lxml import etree

from .package import DocxPackage, W_NS, qname


KNOWN_FIELD_TYPES = (
    "TOC",
    "PAGEREF",
    "PAGE",
    "REF",
    "SEQ",
    "STYLEREF",
    "HYPERLINK",
    "ADDIN",
    "CITATION",
    "MERGEFIELD",
    "IF",
)
_FIELD_RE = re.compile(r"\b(TOC|PAGEREF|PAGE|REF|SEQ|STYLEREF|HYPERLINK|ADDIN|CITATION|MERGEFIELD|IF)\b", re.I)

# The compact report intentionally does not expose field instructions.  This
# substitution handles the common local paths in Word HYPERLINK fields while
# retaining a useful, bounded description for diagnostics.
_LOCAL_PATH_RE = re.compile(
    r"(?:file://|file:)?(?:[A-Za-z]:[\\/]|\\\\|/(?:Users|home|private|var|tmp|Volumes|mnt|opt|srv)(?:/|$))[^\s\"']*",
    re.I,
)


def _instruction_text(node: etree._Element) -> str:
    return "".join(node.itertext())


def _normalize_instruction(value: str) -> str:
    # Joining fragments before collapsing whitespace is what turns ``TO`` +
    # ``C`` into ``TOC`` while still normalising XML indentation.
    return " ".join(value.replace("\u00a0", " ").split())


def _redact_instruction(value: str) -> str:
    return _LOCAL_PATH_RE.sub("[local-path]", value)


def _field_type(instruction: str) -> str | None:
    match = _FIELD_RE.search(instruction)
    return match.group(1).upper() if match else None


def _evidence_record(
    *,
    instruction: str,
    fragments: list[str],
    kind: str,
    boundary: dict[str, Any],
    include_text: bool,
    order: int,
) -> dict[str, Any] | None:
    normalized = _normalize_instruction(instruction)
    field_type = _field_type(normalized)
    if field_type is None:
        return None
    redacted = _redact_instruction(normalized)
    record: dict[str, Any] = {
        "type": field_type,
        "kind": kind,
        "instruction_hash": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "instruction_preview": redacted[:80],
        "boundary": boundary,
        "_order": order,
    }
    if include_text:
        record["instruction"] = normalized
        record["instruction_fragments"] = list(fragments)
    else:
        record["instruction_fragments"] = [
            {
                "hash": hashlib.sha256(fragment.encode("utf-8")).hexdigest(),
                "preview": _redact_instruction(_normalize_instruction(fragment))[:80],
            }
            for fragment in fragments
        ]
    return record


def fields_in_paragraph(paragraph: etree._Element, include_text: bool = False) -> list[dict[str, Any]]:
    """Return field evidence in one paragraph, preserving field boundaries.

    ``w:fldSimple`` is emitted as one field.  Complex fields are parsed with a
    stack so nested fields do not merge their instructions into the parent.
    Unterminated fields remain visible with ``boundary.complete=False`` rather
    than silently disappearing.
    """

    fields: list[dict[str, Any]] = []
    order = 0
    token_index = 0
    stack: list[dict[str, Any]] = []

    def append_state(state: dict[str, Any], *, end_index: int | None, complete: bool) -> None:
        boundary = {
            "begin": bool(state.get("begin")),
            "separate": bool(state.get("separate")),
            "end": bool(state.get("end")) and complete,
            "complete": complete,
            "begin_index": state.get("begin_index"),
            "separate_index": state.get("separate_index"),
            "end_index": end_index,
        }
        raw_instruction = "".join(state.get("fragments", []))
        record = _evidence_record(
            instruction=raw_instruction,
            fragments=list(state.get("fragments", [])),
            kind="complex",
            boundary=boundary,
            include_text=include_text,
            order=int(state.get("order", 0)),
        )
        if record is not None:
            fields.append(record)

    for node in paragraph.iter():
        # Do not re-parse instruction/field-character descendants of a simple
        # field as an additional complex field.  Ancestor checks are used in
        # preference to proxy-object ``id`` values because lxml may materialise
        # a fresh Python wrapper while walking the same XML node.
        if any(ancestor.tag == qname(W_NS, "fldSimple") for ancestor in node.iterancestors()):
            continue
        name = node.tag
        if name == qname(W_NS, "fldSimple"):
            instruction = node.get(qname(W_NS, "instr"), "")
            record = _evidence_record(
                instruction=instruction,
                fragments=[instruction],
                kind="simple",
                boundary={
                    "begin": True,
                    "separate": True,
                    "end": True,
                    "complete": True,
                    "begin_index": token_index,
                    "separate_index": None,
                    "end_index": token_index,
                },
                include_text=include_text,
                order=order,
            )
            order += 1
            if record is not None:
                fields.append(record)
        elif name == qname(W_NS, "fldChar"):
            field_kind = (node.get(qname(W_NS, "fldCharType")) or "").casefold()
            if field_kind == "begin":
                stack.append(
                    {
                        "begin": True,
                        "separate": False,
                        "end": False,
                        "begin_index": token_index,
                        "separate_index": None,
                        "fragments": [],
                        "order": order,
                    }
                )
                order += 1
            elif field_kind == "separate" and stack:
                stack[-1]["separate"] = True
                stack[-1]["separate_index"] = token_index
            elif field_kind == "end" and stack:
                state = stack.pop()
                state["end"] = True
                append_state(state, end_index=token_index, complete=True)
            token_index += 1
        elif name == qname(W_NS, "instrText"):
            fragment = _instruction_text(node)
            if stack:
                stack[-1]["fragments"].append(fragment)
            else:
                # A malformed/producer-specific field may omit fldChar
                # boundaries.  Keep the instruction as incomplete evidence.
                record = _evidence_record(
                    instruction=fragment,
                    fragments=[fragment],
                    kind="complex",
                    boundary={
                        "begin": False,
                        "separate": False,
                        "end": False,
                        "complete": False,
                        "begin_index": None,
                        "separate_index": None,
                        "end_index": None,
                    },
                    include_text=include_text,
                    order=order,
                )
                order += 1
                if record is not None:
                    fields.append(record)
            token_index += 1

    # Preserve evidence for an unclosed field; this is useful both for
    # diagnostics and for conservative classification of a damaged package.
    while stack:
        append_state(stack.pop(), end_index=None, complete=False)

    fields.sort(key=lambda item: (int(item.pop("_order", 0)), item.get("boundary", {}).get("begin_index") or -1))
    return fields


def inspect_fields(root: etree._Element, package: DocxPackage, include_text: bool = False) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    items: list[dict[str, Any]] = []
    for paragraph_index, paragraph in enumerate(root.iter(qname(W_NS, "p"))):
        for field in fields_in_paragraph(paragraph, include_text=include_text):
            counts[field["type"]] += 1
            # Use a document-order index rather than Python's object id: the
            # latter changes between runs and would make JSON reports
            # nondeterministic.
            items.append({**field, "paragraph_index": paragraph_index})
    normalized_counts = {field_type: counts.get(field_type, 0) for field_type in KNOWN_FIELD_TYPES}
    custom_xml_parts = [name for name in package.parts if name.startswith("customXml/") or "/customXml/" in name]
    citation_evidence = []
    for part_name, content in package.parts.items():
        if part_name.startswith("word/customXml/") or part_name.startswith("customXml/"):
            lowered = content.decode("utf-8", errors="ignore").lower()
            if any(marker in lowered for marker in ("zotero", "endnote", "csl_citation", "citation")):
                citation_evidence.append(part_name)
    manager_fields = [item for item in items if item["type"] in {"ADDIN", "CITATION"}]
    return {
        "counts": normalized_counts,
        "items": items,
        "toc_field_count": normalized_counts["TOC"],
        "citation_manager": {
            "presence": bool(citation_evidence or manager_fields),
            "unknown": bool(custom_xml_parts) and not bool(citation_evidence or manager_fields),
            "evidence_parts": citation_evidence,
            "evidence_fields": len(manager_fields),
        },
    }


__all__ = ["KNOWN_FIELD_TYPES", "fields_in_paragraph", "inspect_fields"]
