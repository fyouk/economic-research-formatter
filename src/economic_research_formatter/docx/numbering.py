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
                pstyle = level.find(qname(W_NS, "pStyle"))
                parsed_level: dict[str, Any] = {
                    "format": fmt.get(qname(W_NS, "val")) if fmt is not None else None,
                    "text": text.get(qname(W_NS, "val")) if text is not None else None,
                }
                if pstyle is not None and pstyle.get(qname(W_NS, "val")) is not None:
                    parsed_level["p_style"] = pstyle.get(qname(W_NS, "val"))
                    parsed_level["p_style_source"] = "abstract"
                start = level.find(qname(W_NS, "start"))
                if start is not None:
                    parsed_level["start"] = self._int(start.get(qname(W_NS, "val")))
                levels[ilvl] = parsed_level
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
                    pstyle = level.find(qname(W_NS, "pStyle"))
                    if pstyle is not None and pstyle.get(qname(W_NS, "val")) is not None:
                        overrides[override_level]["p_style"] = pstyle.get(qname(W_NS, "val"))
                        overrides[override_level]["p_style_source"] = "override"
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
    def _numpr_values(cls, ppr: etree._Element | None) -> dict[str, int]:
        """Return only the explicitly present ``numPr`` children.

        A ``w:numPr`` is a sparse property group.  Returning ``None`` for a
        missing child made an otherwise useful direct ``ilvl`` look like a
        complete override and prevented style inheritance from supplying the
        ``numId``.  Keeping absent children out of the mapping lets the
        resolver merge each child independently.
        """

        if ppr is None:
            return {}
        numpr = ppr.find(qname(W_NS, "numPr"))
        if numpr is None:
            return {}
        values: dict[str, int] = {}
        num_id_element = numpr.find(qname(W_NS, "numId"))
        ilvl_element = numpr.find(qname(W_NS, "ilvl"))
        if num_id_element is not None:
            num_id = cls._int(num_id_element.get(qname(W_NS, "val")))
            if num_id is not None:
                values["num_id"] = num_id
        if ilvl_element is not None:
            ilvl = cls._int(ilvl_element.get(qname(W_NS, "val")))
            if ilvl is not None:
                values["ilvl"] = ilvl
        return values

    def _style_numpr(
        self,
        style_id: str | None,
    ) -> tuple[dict[str, int], str | None, tuple[str, ...], bool]:
        """Merge sparse paragraph-style ``numPr`` values through ``basedOn``.

        The returned style chain is also used to resolve ``abstractNum``
        ``lvl/pStyle`` associations.  A cycle is surfaced to the caller
        rather than silently treating whichever style happened to be visited
        first as authoritative.
        """

        resolver = self.style_resolver
        if resolver is None or style_id is None:
            return {}, None, (), False
        seen: set[str] = set()
        chain: list[str] = []
        current = style_id
        cycle = False
        while current:
            if current in seen:
                cycle = True
                break
            seen.add(current)
            chain.append(current)
            style = resolver.styles.get(current)
            if style is None:
                break
            current = style.get("based_on")
        merged: dict[str, int] = {}
        for current in reversed(chain):
            style = resolver.styles.get(current)
            if style is None:
                continue
            # Parent values are applied first; a child style only overrides
            # the children it actually declares.
            merged.update(self._numpr_values(style.get("ppr")))
        source: str | None = None
        for index, current in enumerate(chain):
            style = resolver.styles.get(current)
            if style is None or not self._numpr_values(style.get("ppr")):
                continue
            source = f"paragraph_style:{current}" if index == 0 else f"basedOn:{current}"
            break
        return merged, source, tuple(chain), cycle

    def _unresolved(
        self,
        *,
        num_id: int | None,
        ilvl: int | None,
        source: str | None,
        reason: str,
        abstract_num_id: int | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "num_id": num_id,
            "ilvl": ilvl,
            "format": None,
            "text": None,
            "resolved": False,
            "numbered": None,
            "removes_numbering": False,
            "reason": reason,
        }
        if abstract_num_id is not None:
            result["abstract_num_id"] = abstract_num_id
        if source and source != "direct":
            result["source"] = source
        return result

    @staticmethod
    def _style_level_candidates(
        levels: dict[int, dict[str, Any]],
        style_ids: tuple[str, ...],
    ) -> list[tuple[int, str, dict[str, Any]]]:
        style_set = set(style_ids)
        candidates: list[tuple[int, str, dict[str, Any]]] = []
        for level_index, level in sorted(levels.items()):
            p_style = level.get("p_style")
            if isinstance(p_style, str) and p_style in style_set:
                candidates.append((level_index, p_style, level))
        return candidates

    def resolve(self, ppr: etree._Element | None) -> dict[str, Any] | None:
        direct_values = self._numpr_values(ppr)
        direct_numpr = ppr is not None and ppr.find(qname(W_NS, "numPr")) is not None
        style_id: str | None = None
        style_values: dict[str, int] = {}
        style_source: str | None = None
        style_ids: tuple[str, ...] = ()
        style_cycle = False
        if self.style_resolver is not None:
            style_id = self.style_resolver.paragraph_style_id(ppr)
            if style_id is None:
                style_id = self.style_resolver.default_style_id("paragraph")
            style_values, style_source, style_ids, style_cycle = self._style_numpr(style_id)

        # Direct formatting is a sparse overlay over the effective paragraph
        # style.  An explicit ``numId=0`` is handled before merging because it
        # is the OOXML cancellation sentinel and must remove inherited list
        # numbering rather than select a numbering instance.
        if direct_values.get("num_id") == 0:
            result: dict[str, Any] = {
                "numbered": False,
                "removes_numbering": True,
                "num_id": 0,
                "ilvl": direct_values.get("ilvl"),
                "format": None,
                "text": None,
                "resolved": True,
                "source": "direct",
            }
            return result

        values = dict(style_values)
        values.update(direct_values)
        source = "direct" if direct_numpr and direct_values else style_source
        if values.get("num_id") == 0:
            return {
                "numbered": False,
                "removes_numbering": True,
                "num_id": 0,
                "ilvl": values.get("ilvl"),
                "format": None,
                "text": None,
                "resolved": True,
                "source": source or "style",
            }
        if not values:
            if style_cycle:
                return self._unresolved(
                    num_id=None,
                    ilvl=None,
                    source=style_source,
                    reason="style_cycle",
                )
            return None
        # A style cycle means the effective style-level numPr is not
        # authoritative.  Direct paragraph values remain safe because they
        # completely specify the source of those fields.
        direct_values_complete = "num_id" in direct_values and "ilvl" in direct_values
        if style_cycle and not direct_values_complete:
            num_id = values.get("num_id")
            ilvl = values.get("ilvl")
            return self._unresolved(
                num_id=num_id,
                ilvl=ilvl,
                source=source,
                reason="style_cycle",
            )

        num_id = values.get("num_id")
        ilvl = values.get("ilvl")
        if num_id is None:
            return self._unresolved(
                num_id=None,
                ilvl=ilvl,
                source=source,
                reason="sparse_numPr_without_resolvable_numId",
            )

        abstract_id = self.nums.get(num_id)
        if abstract_id is None:
            return self._unresolved(
                num_id=num_id,
                ilvl=ilvl,
                source=source,
                reason="missing_num_definition",
            )
        levels = self.abstract.get(abstract_id, {})

        # ``ilvl`` declared by a paragraph style is not a reliable final
        # level.  When the abstract numbering definition links a level to one
        # of the style IDs in the inheritance chain, that pStyle association
        # is the standard evidence.  Direct paragraph ``ilvl`` remains a
        # genuine paragraph-level override and therefore takes precedence.
        level_source: str | None = None
        level_evidence: dict[str, Any] | None = None
        direct_ilvl = direct_values.get("ilvl")
        style_numpr_used = bool(style_values) and direct_ilvl is None
        effective_levels = {level_index: dict(level) for level_index, level in levels.items()}
        for level_index, override_values in self.overrides.get(num_id, {}).items():
            effective_levels.setdefault(level_index, {}).update(
                {
                    key: value
                    for key, value in override_values.items()
                    if value is not None
                }
            )
        candidates = (
            self._style_level_candidates(effective_levels, style_ids)
            if style_numpr_used
            else []
        )
        if candidates:
            if len(candidates) > 1:
                return self._unresolved(
                    num_id=num_id,
                    ilvl=ilvl,
                    source=source,
                    reason="ambiguous_style_pStyle_level",
                    abstract_num_id=abstract_id,
                )
            level_index, matched_style_id, candidate_level = candidates[0]
            level_source = (
                "override_pStyle"
                if candidate_level.get("p_style_source") == "override"
                else "abstract_pStyle"
            )
            level_evidence = {
                "style_id": matched_style_id,
                "style_chain": list(style_ids),
                "abstract_num_id": abstract_id,
                "ilvl": level_index,
            }
            if candidate_level.get("p_style_source") == "override":
                level_evidence["p_style_source"] = "override"
        elif style_numpr_used:
            # A paragraph style's ilvl is not itself a paragraph-level
            # selection.  Without one unique abstract lvl/pStyle association
            # there is no defensible way to choose a level, even when the
            # abstract definition happens to contain a level at that ilvl.
            return self._unresolved(
                num_id=num_id,
                ilvl=ilvl,
                source=source,
                reason="style_numpr_without_unique_pStyle",
                abstract_num_id=abstract_id,
            )
        else:
            level_index = ilvl if ilvl is not None else 0

        # ``abstractNumId=0`` is a valid OOXML identifier.  Using
        # ``abstract_id or -1`` silently discarded that definition and made
        # otherwise resolvable lists look like missing numbering.
        abstract_level = levels.get(level_index)
        override = self.overrides.get(num_id, {}).get(level_index)
        if override is None:
            level = abstract_level or {}
        else:
            # A ``lvlOverride`` may carry only a start override or only one
            # formatting child.  Merge it over the abstract level instead of
            # treating the omitted children as an unresolved definition.
            level = dict(abstract_level or {})
            level.update({key: value for key, value in override.items() if value is not None})
        if not level or not (level.get("format") or level.get("text")):
            return self._unresolved(
                num_id=num_id,
                ilvl=level_index,
                source=source,
                reason="missing_abstract_level",
                abstract_num_id=abstract_id,
            )
        result = {
            "num_id": num_id,
            "ilvl": level_index,
            "abstract_num_id": abstract_id,
            "format": level.get("format"),
            "text": level.get("text"),
            "resolved": bool(level.get("format") or level.get("text")),
            "numbered": True,
            "removes_numbering": False,
        }
        if level and "start" in level:
            result["start"] = level["start"]
        if level and "start_override" in level:
            result["start_override"] = level["start_override"]
        if level_source is not None and level_evidence is not None:
            result["level_source"] = level_source
            result["level_evidence"] = level_evidence
        if source and source != "direct":
            result["source"] = source
        return result


__all__ = ["NumberingResolver"]
