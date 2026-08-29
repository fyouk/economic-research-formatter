"""Read automatic numbering definitions without changing the DOCX."""

from __future__ import annotations

from typing import Any

from lxml import etree

from .package import DocxPackage, W_NS, qname


class NumberingResolver:
    def __init__(self, package: DocxPackage) -> None:
        self.abstract: dict[int, dict[int, dict[str, Any]]] = {}
        self.nums: dict[int, int] = {}
        self.overrides: dict[int, dict[int, dict[str, Any]]] = {}
        root = package.xml("word/numbering.xml")
        if root is None:
            return
        for abstract in root.findall(qname(W_NS, "abstractNum")):
            abstract_id = self._int(abstract.get(qname(W_NS, "abstractNumId")))
            if abstract_id is None:
                continue
            levels: dict[int, dict[str, Any]] = {}
            for level in abstract.findall(qname(W_NS, "lvl")):
                ilvl = self._int(level.get(qname(W_NS, "ilvl")))
                if ilvl is None:
                    continue
                fmt = level.find(qname(W_NS, "numFmt"))
                text = level.find(qname(W_NS, "lvlText"))
                levels[ilvl] = {
                    "format": fmt.get(qname(W_NS, "val")) if fmt is not None else None,
                    "text": text.get(qname(W_NS, "val")) if text is not None else None,
                }
            self.abstract[abstract_id] = levels
        for num in root.findall(qname(W_NS, "num")):
            num_id = self._int(num.get(qname(W_NS, "numId")))
            abstract_id = num.find(qname(W_NS, "abstractNumId"))
            abstract_value = self._int(abstract_id.get(qname(W_NS, "val")) if abstract_id is not None else None)
            if num_id is not None and abstract_value is not None:
                self.nums[num_id] = abstract_value
                overrides: dict[int, dict[str, Any]] = {}
                for override in num.findall(qname(W_NS, "lvlOverride")):
                    override_level = self._int(override.get(qname(W_NS, "ilvl")))
                    level = override.find(qname(W_NS, "lvl"))
                    if override_level is None or level is None:
                        continue
                    fmt = level.find(qname(W_NS, "numFmt"))
                    text = level.find(qname(W_NS, "lvlText"))
                    overrides[override_level] = {
                        "format": fmt.get(qname(W_NS, "val")) if fmt is not None else None,
                        "text": text.get(qname(W_NS, "val")) if text is not None else None,
                    }
                if overrides:
                    self.overrides[num_id] = overrides

    @staticmethod
    def _int(value: str | None) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def resolve(self, ppr: etree._Element | None) -> dict[str, Any] | None:
        if ppr is None:
            return None
        numpr = ppr.find(qname(W_NS, "numPr"))
        if numpr is None:
            return None
        num_id_element = numpr.find(qname(W_NS, "numId"))
        ilvl_element = numpr.find(qname(W_NS, "ilvl"))
        num_id = self._int(num_id_element.get(qname(W_NS, "val")) if num_id_element is not None else None)
        ilvl = self._int(ilvl_element.get(qname(W_NS, "val")) if ilvl_element is not None else None)
        if num_id is None:
            return {"num_id": None, "ilvl": ilvl, "format": None, "text": None, "resolved": False}
        abstract_id = self.nums.get(num_id)
        level_index = ilvl if ilvl is not None else 0
        level = self.overrides.get(num_id, {}).get(level_index)
        if level is None:
            # ``abstractNumId=0`` is a valid OOXML identifier.  Using
            # ``abstract_id or -1`` silently discarded that definition and
            # made otherwise resolvable lists look like missing numbering.
            level = self.abstract.get(abstract_id if abstract_id is not None else -1, {}).get(level_index, {})
        return {
            "num_id": num_id,
            "ilvl": ilvl if ilvl is not None else 0,
            "abstract_num_id": abstract_id,
            "format": level.get("format"),
            "text": level.get("text"),
            "resolved": bool(level),
        }


__all__ = ["NumberingResolver"]
