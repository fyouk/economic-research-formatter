"""Read automatic numbering definitions without changing the DOCX."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from lxml import etree

from .package import DocxPackage, W_NS, qname

if TYPE_CHECKING:
    from .styles import StyleResolver


class NumberingResolver:
    def __init__(self, package: DocxPackage, style_resolver: "StyleResolver | None" = None) -> None:
        self.style_resolver = style_resolver
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
                    start_override = level.find(qname(W_NS, "start"))
                    if start_override is not None:
                        overrides[override_level]["start"] = self._int(
                            start_override.get(qname(W_NS, "val"))
                        )
                # A level override may change only the starting value.  It is
                # still evidence that the ``num`` definition overrides the
                # abstract level, so retain it rather than silently dropping
                # the override because no nested ``w:lvl`` exists.
                for override in num.findall(qname(W_NS, "lvlOverride")):
                    override_level = self._int(override.get(qname(W_NS, "ilvl")))
                    start_override = override.find(qname(W_NS, "startOverride"))
                    if override_level is None or start_override is None:
                        continue
                    entry = overrides.setdefault(override_level, {})
                    if "start_override" not in entry:
                        entry["start_override"] = self._int(
                            start_override.get(qname(W_NS, "val"))
                        )
                if overrides:
                    self.overrides[num_id] = overrides

    @staticmethod
    def _int(value: str | None) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _numpr_values(cls, ppr: etree._Element | None) -> dict[str, int | None]:
        if ppr is None:
            return {}
        numpr = ppr.find(qname(W_NS, "numPr"))
        if numpr is None:
            return {}
        num_id_element = numpr.find(qname(W_NS, "numId"))
        ilvl_element = numpr.find(qname(W_NS, "ilvl"))
        return {
            "num_id": cls._int(num_id_element.get(qname(W_NS, "val")) if num_id_element is not None else None),
            "ilvl": cls._int(ilvl_element.get(qname(W_NS, "val")) if ilvl_element is not None else None),
        }

    def _style_numpr(self, style_id: str | None) -> tuple[dict[str, int | None], str | None]:
        resolver = self.style_resolver
        if resolver is None or style_id is None:
            return {}, None
        seen: set[str] = set()
        current = style_id
        first = True
        while current and current not in seen:
            seen.add(current)
            style = resolver.styles.get(current)
            if style is None:
                break
            values = self._numpr_values(style.get("ppr"))
            if values:
                source = (
                    f"paragraph_style:{current}"
                    if first
                    else f"basedOn:{current}"
                )
                return values, source
            current = style.get("based_on")
            first = False
        return {}, None

    def resolve(self, ppr: etree._Element | None) -> dict[str, Any] | None:
        direct_values = self._numpr_values(ppr)
        source = "direct" if direct_values else None
        values = direct_values
        if not values and self.style_resolver is not None:
            style_id = self.style_resolver.paragraph_style_id(ppr)
            if style_id is None:
                style_id = self.style_resolver.default_style_id("paragraph")
            values, source = self._style_numpr(style_id)
        if not values:
            return None
        num_id = values.get("num_id")
        ilvl = values.get("ilvl")
        if num_id is None:
            result = {"num_id": None, "ilvl": ilvl, "format": None, "text": None, "resolved": False}
            if source and source != "direct":
                result["source"] = source
            return result
        abstract_id = self.nums.get(num_id)
        level_index = ilvl if ilvl is not None else 0
        # ``abstractNumId=0`` is a valid OOXML identifier.  Using
        # ``abstract_id or -1`` silently discarded that definition and made
        # otherwise resolvable lists look like missing numbering.
        abstract_level = self.abstract.get(
            abstract_id if abstract_id is not None else -1, {}
        ).get(level_index)
        override = self.overrides.get(num_id, {}).get(level_index)
        if override is None:
            level = abstract_level or {}
        else:
            # A ``lvlOverride`` may carry only a start override or only one
            # formatting child.  Merge it over the abstract level instead of
            # treating the omitted children as an unresolved definition.
            level = dict(abstract_level or {})
            level.update({key: value for key, value in override.items() if value is not None})
        result = {
            "num_id": num_id,
            "ilvl": ilvl if ilvl is not None else 0,
            "abstract_num_id": abstract_id,
            "format": level.get("format"),
            "text": level.get("text"),
            "resolved": bool(level.get("format") or level.get("text")),
        }
        if level and "start" in level:
            result["start"] = level["start"]
        if level and "start_override" in level:
            result["start_override"] = level["start_override"]
        if source and source != "direct":
            result["source"] = source
        return result


__all__ = ["NumberingResolver"]
