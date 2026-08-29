"""OOXML style and effective run-format resolution."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from lxml import etree

from .package import DocxPackage, W_NS, qname


_FONT_ATTRIBUTES = ("eastAsia", "ascii", "hAnsi", "cs")
_FORMAT_KEYS = ("font", "size_pt", "size_cs_pt", "bold", "italic", "underline", "color", "highlight", "vert_align")
_INDENT_KEYS = ("left_twips", "right_twips", "first_line_twips", "hanging_twips")
_SPACING_KEYS = ("before_twips", "after_twips", "line_raw", "line_rule")
_UNRESOLVED_THEME = object()


def _float_or_none(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_property(element: etree._Element | None) -> bool | None:
    if element is None:
        return None
    value = element.get(qname(W_NS, "val"))
    if value in (None, "true", "1", "on"):
        return True
    if value in ("false", "0", "off"):
        return False
    return True


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_ppr(ppr: etree._Element | None) -> dict[str, Any]:
    """Convert direct OOXML paragraph properties to a sparse mapping.

    Child elements are sparse in OOXML: a style may provide only ``left`` in
    ``w:ind`` while a paragraph supplies only ``right``.  The resolver merges
    those nested attributes independently, preserving the source for each
    effective value.
    """

    if ppr is None:
        return {}
    result: dict[str, Any] = {}
    alignment = ppr.find(qname(W_NS, "jc"))
    if alignment is not None:
        result["alignment"] = alignment.get(qname(W_NS, "val"))
    ind = ppr.find(qname(W_NS, "ind"))
    if ind is not None:
        result["indent"] = {
            "left_twips": _int_or_none(ind.get(qname(W_NS, "left"))),
            "right_twips": _int_or_none(ind.get(qname(W_NS, "right"))),
            "first_line_twips": _int_or_none(ind.get(qname(W_NS, "firstLine"))),
            "hanging_twips": _int_or_none(ind.get(qname(W_NS, "hanging"))),
        }
    spacing = ppr.find(qname(W_NS, "spacing"))
    if spacing is not None:
        result["spacing"] = {
            "before_twips": _int_or_none(spacing.get(qname(W_NS, "before"))),
            "after_twips": _int_or_none(spacing.get(qname(W_NS, "after"))),
            "line_raw": _int_or_none(spacing.get(qname(W_NS, "line"))),
            "line_rule": spacing.get(qname(W_NS, "lineRule")),
        }
    outline = ppr.find(qname(W_NS, "outlineLvl"))
    if outline is not None:
        result["outline_level"] = _int_or_none(outline.get(qname(W_NS, "val")))
    for tag, key in (
        ("keepNext", "keep_with_next"),
        ("keepLines", "keep_together"),
        ("pageBreakBefore", "page_break_before"),
        ("widowControl", "widow_control"),
    ):
        value = _bool_property(ppr.find(qname(W_NS, tag)))
        if value is not None:
            result[key] = value
    return result


def parse_rpr(rpr: etree._Element | None) -> dict[str, Any]:
    """Convert an ``w:rPr`` element to a raw, JSON-compatible mapping."""
    if rpr is None:
        return {}
    result: dict[str, Any] = {}
    fonts = rpr.find(qname(W_NS, "rFonts"))
    if fonts is not None:
        font: dict[str, Any] = {}
        for attribute in _FONT_ATTRIBUTES:
            value = fonts.get(qname(W_NS, attribute))
            theme_value = fonts.get(qname(W_NS, f"{attribute}Theme"))
            if value is not None:
                font[attribute] = value
            if theme_value is not None:
                font[f"{attribute}Theme"] = theme_value
        if font:
            result["font"] = font
    size = rpr.find(qname(W_NS, "sz"))
    if size is not None and size.get(qname(W_NS, "val")) is not None:
        raw = size.get(qname(W_NS, "val"))
        result["size_half_points"] = raw
        parsed = _float_or_none(raw)
        result["size_pt"] = parsed / 2 if parsed is not None else None
    size_cs = rpr.find(qname(W_NS, "szCs"))
    if size_cs is not None and size_cs.get(qname(W_NS, "val")) is not None:
        raw = size_cs.get(qname(W_NS, "val"))
        result["size_cs_half_points"] = raw
        parsed = _float_or_none(raw)
        result["size_cs_pt"] = parsed / 2 if parsed is not None else None
    for tag, key in (("b", "bold"), ("i", "italic")):
        value = _bool_property(rpr.find(qname(W_NS, tag)))
        if value is not None:
            result[key] = value
    underline = rpr.find(qname(W_NS, "u"))
    if underline is not None:
        result["underline"] = underline.get(qname(W_NS, "val"), "single")
    color = rpr.find(qname(W_NS, "color"))
    if color is not None:
        result["color"] = color.get(qname(W_NS, "val"))
    highlight = rpr.find(qname(W_NS, "highlight"))
    if highlight is not None:
        result["highlight"] = highlight.get(qname(W_NS, "val"))
    vertical = rpr.find(qname(W_NS, "vertAlign"))
    if vertical is not None:
        result["vert_align"] = vertical.get(qname(W_NS, "val"))
    language = rpr.find(qname(W_NS, "lang"))
    if language is not None:
        values = {
            key: language.get(qname(W_NS, key))
            for key in ("val", "eastAsia", "bidi")
            if language.get(qname(W_NS, key)) is not None
        }
        if values:
            result["lang"] = values
    return result


class StyleResolver:
    """Resolve direct run properties through the OOXML style cascade."""

    def __init__(self, package: DocxPackage) -> None:
        self.package = package
        self.styles: dict[str, dict[str, Any]] = {}
        self.default_style_ids: dict[str, str | None] = {
            "paragraph": None,
            "character": None,
        }
        self.doc_defaults: dict[str, Any] = {}
        self.doc_defaults_ppr: dict[str, Any] = {}
        self.theme_fonts: dict[str, str] = {}
        self.theme_script_fonts: dict[tuple[str, str], str] = {}
        self._load()

    def _load(self) -> None:
        root = self.package.xml("word/styles.xml")
        if root is not None:
            defaults = root.find(qname(W_NS, "docDefaults"))
            if defaults is not None:
                self.doc_defaults = parse_rpr(
                    defaults.find(f"{qname(W_NS, 'rPrDefault')}/{qname(W_NS, 'rPr')}")
                )
                self.doc_defaults_ppr = parse_ppr(
                    defaults.find(f"{qname(W_NS, 'pPrDefault')}/{qname(W_NS, 'pPr')}")
                )
            for style in root.findall(qname(W_NS, "style")):
                style_id = style.get(qname(W_NS, "styleId"))
                if not style_id:
                    continue
                name_element = style.find(qname(W_NS, "name"))
                based_on = style.find(qname(W_NS, "basedOn"))
                self.styles[style_id] = {
                    "id": style_id,
                    "name": name_element.get(qname(W_NS, "val")) if name_element is not None else style_id,
                    "type": style.get(qname(W_NS, "type")),
                    "default": style.get(qname(W_NS, "default")) in {"1", "true", "on"},
                    "based_on": based_on.get(qname(W_NS, "val")) if based_on is not None else None,
                    "rpr": parse_rpr(style.find(qname(W_NS, "rPr"))),
                    "ppr": style.find(qname(W_NS, "pPr")),
                }
                style_type = self.styles[style_id]["type"]
                if self.styles[style_id]["default"] and style_type in self.default_style_ids:
                    # Word permits one default style for each type.  Keep the
                    # first one deterministically if a malformed package
                    # contains duplicates rather than letting ZIP/XML order
                    # change the effective formatting silently.
                    if self.default_style_ids[style_type] is None:
                        self.default_style_ids[style_type] = style_id
        theme = self.package.xml("word/theme/theme1.xml")
        if theme is not None:
            drawingml_ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
            font_scheme = theme.find(
                f".//{{{drawingml_ns}}}fontScheme"
            )
            if font_scheme is not None:
                for group_name, group_key in (("majorFont", "major"), ("minorFont", "minor")):
                    group = font_scheme.find(
                        f"{{{drawingml_ns}}}{group_name}"
                    )
                    if group is None:
                        continue
                    for child_name, language_key in (("latin", "latin"), ("ea", "eastAsia"), ("cs", "cs")):
                        child = group.find(
                            f"{{{drawingml_ns}}}{child_name}"
                        )
                        if child is not None and child.get("typeface"):
                            self.theme_fonts[f"{group_key}{language_key[:1].upper()}{language_key[1:]}"] = child.get("typeface")
                    for child in group.findall(f"{{{drawingml_ns}}}font"):
                        script = child.get("script")
                        typeface = child.get("typeface")
                        if script and typeface:
                            # Script-specific fonts are intentionally kept
                            # separate from the generic eastAsia token.  The
                            # latter does not identify Hans versus Hant, so it
                            # must not select one arbitrarily.
                            self.theme_script_fonts[(group_key, script)] = typeface

    def style_name(self, style_id: str | None) -> str | None:
        if style_id is None:
            return None
        style = self.styles.get(style_id)
        return style.get("name") if style else style_id

    def style_id_for_name(self, name: str) -> str | None:
        for style_id, style in self.styles.items():
            if style.get("name") == name:
                return style_id
        return None

    def paragraph_style_id(self, ppr: etree._Element | None) -> str | None:
        if ppr is None:
            return None
        style = ppr.find(qname(W_NS, "pStyle"))
        return style.get(qname(W_NS, "val")) if style is not None else None

    def run_style_id(self, rpr: etree._Element | None) -> str | None:
        if rpr is None:
            return None
        style = rpr.find(qname(W_NS, "rStyle"))
        return style.get(qname(W_NS, "val")) if style is not None else None

    def default_style_id(self, style_type: str) -> str | None:
        """Return the OOXML ``w:default=1`` style for ``style_type``."""

        return self.default_style_ids.get(style_type)

    def style_chain(self, style_id: str | None) -> tuple[str, ...]:
        """Return a cycle-bounded style chain in child-to-parent order."""

        return tuple(style_id for style_id, _ in self._chain(style_id))

    def _chain(self, style_id: str | None) -> Iterable[tuple[str, dict[str, Any]]]:
        seen: set[str] = set()
        current = style_id
        while current and current not in seen:
            seen.add(current)
            style = self.styles.get(current)
            if style is None:
                break
            yield current, style["rpr"]
            current = style.get("based_on")

    @staticmethod
    def _script_from_language(language: Any) -> str | None:
        if not isinstance(language, dict):
            return None
        value = language.get("eastAsia") or language.get("val")
        if not isinstance(value, str):
            return None
        value = value.casefold()
        if value.startswith(("zh-cn", "zh-sg", "zh-hans")):
            return "Hans"
        if value.startswith(("zh-tw", "zh-hk", "zh-mo", "zh-hant")):
            return "Hant"
        return None

    def _theme_value(self, token: str | None, *, script: str | None = None) -> str | None:
        if token is None:
            return None
        normalized = token
        # OOXML tokens are latin/eastAsia/cs plus major/minor; normalize the
        # common spelling variants found in Word files.
        aliases = {
            "majorEastAsia": "majorEastAsia",
            "minorEastAsia": "minorEastAsia",
            "majorAscii": "majorLatin",
            "minorAscii": "minorLatin",
            "majorHAnsi": "majorLatin",
            "minorHAnsi": "minorLatin",
            "majorCs": "majorCs",
            "minorCs": "minorCs",
        }
        normalized = aliases.get(token, token)
        if script and normalized in {"majorEastAsia", "minorEastAsia"}:
            group = "major" if normalized.startswith("major") else "minor"
            script_value = self.theme_script_fonts.get((group, script))
            if script_value:
                return script_value
        if normalized in {"majorEastAsia", "minorEastAsia"} and script is None:
            group = "major" if normalized.startswith("major") else "minor"
            if any(group_name == group for group_name, _ in self.theme_script_fonts):
                # A generic ``a:ea`` face is not a safe substitute when the
                # theme explicitly supplies script-specific Hans/Hant faces
                # and the run carries no language evidence.
                return None
        return self.theme_fonts.get(normalized)

    def resolve_run(
        self,
        direct_rpr: etree._Element | None,
        paragraph_style_id: str | None,
    ) -> dict[str, Any]:
        direct = parse_rpr(direct_rpr)
        character_style_id = self.run_style_id(direct_rpr)
        sources: dict[str, Any] = {"font": {}, "size_pt": None, "size_cs_pt": None}
        resolution_chain: list[str] = ["run_direct"]
        effective: dict[str, Any] = {key: None for key in _FORMAT_KEYS}
        effective["font"] = {attribute: None for attribute in _FONT_ATTRIBUTES}
        effective["font"]["theme"] = {}
        effective["font"]["theme_evidence"] = {}
        effective["raw"] = deepcopy(direct)
        script_hint: str | None = self._script_from_language(direct.get("lang"))

        def take(key: str, value: Any, source: str) -> None:
            if value is None:
                return
            if effective.get(key) is None:
                effective[key] = value
                sources[key] = source

        def take_font(font_value: dict[str, Any] | None, source: str) -> None:
            if not font_value:
                return
            for attribute in _FONT_ATTRIBUTES:
                value = font_value.get(attribute)
                theme_token = font_value.get(f"{attribute}Theme")
                if effective["font"].get(attribute) is None:
                    if value is not None:
                        effective["font"][attribute] = value
                        sources["font"][attribute] = source
                    elif theme_token is not None:
                        resolved_theme = self._theme_value(theme_token, script=script_hint)
                        # A theme token is an explicit value in the cascade,
                        # even when the available theme data cannot resolve it
                        # without a script hint.  Keep an internal occupied
                        # sentinel so a lower ordinary font cannot fill the
                        # unresolved slot.  The sentinel is converted to
                        # ``None`` before the result leaves this method.
                        effective["font"][attribute] = (
                            resolved_theme if resolved_theme is not None else _UNRESOLVED_THEME
                        )
                        effective["font"]["theme"][attribute] = theme_token
                        group = "major" if theme_token.casefold().startswith("major") else "minor" if theme_token.casefold().startswith("minor") else None
                        available_scripts = sorted(
                            script_name
                            for (group_name, script_name), _font in self.theme_script_fonts.items()
                            if group_name == group
                        )
                        effective["font"]["theme_evidence"][attribute] = {
                            "token": theme_token,
                            "resolved": resolved_theme is not None,
                            "script": script_hint,
                            "available_scripts": available_scripts,
                        }
                        sources["font"][attribute] = "theme" if resolved_theme else "unknown"

        take_font(direct.get("font"), "direct")
        for key in _FORMAT_KEYS:
            if key != "font":
                take(key, direct.get(key), "direct")
        for position, (style_id, values) in enumerate(self._chain(character_style_id)):
            layer = f"character_style:{style_id}" if position == 0 else f"basedOn:{style_id}"
            resolution_chain.append(layer)
            if script_hint is None:
                script_hint = self._script_from_language(values.get("lang"))
            take_font(values.get("font"), layer)
            for key in _FORMAT_KEYS:
                if key != "font":
                    take(key, values.get(key), layer)
        for position, (style_id, values) in enumerate(self._chain(paragraph_style_id)):
            layer = f"paragraph_style:{style_id}" if position == 0 else f"basedOn:{style_id}"
            resolution_chain.append(layer)
            if script_hint is None:
                script_hint = self._script_from_language(values.get("lang"))
            take_font(values.get("font"), layer)
            for key in _FORMAT_KEYS:
                if key != "font":
                    take(key, values.get(key), layer)
        if character_style_id is None:
            default_character_style_id = self.default_style_id("character")
            for position, (style_id, values) in enumerate(self._chain(default_character_style_id)):
                layer = f"default_character_style:{style_id}" if position == 0 else f"basedOn:{style_id}"
                resolution_chain.append(layer)
                if script_hint is None:
                    script_hint = self._script_from_language(values.get("lang"))
                take_font(values.get("font"), layer)
                for key in _FORMAT_KEYS:
                    if key != "font":
                        take(key, values.get(key), layer)
        if paragraph_style_id is None:
            default_paragraph_style_id = self.default_style_id("paragraph")
            for position, (style_id, values) in enumerate(self._chain(default_paragraph_style_id)):
                layer = f"default_paragraph_style:{style_id}" if position == 0 else f"basedOn:{style_id}"
                resolution_chain.append(layer)
                if script_hint is None:
                    script_hint = self._script_from_language(values.get("lang"))
                take_font(values.get("font"), layer)
                for key in _FORMAT_KEYS:
                    if key != "font":
                        take(key, values.get(key), layer)
        take_font(self.doc_defaults.get("font"), "docDefaults")
        resolution_chain.append("docDefaults")
        # ``w:lang`` in ``docDefaults/w:rPrDefault`` participates in the same
        # script-hint cascade as direct, character-style, paragraph-style and
        # default-style language evidence.  It has to be consulted before the
        # final unresolved-theme pass: a high-priority theme token occupies its
        # slot, but can become resolvable once this document-wide hint is known.
        if script_hint is None:
            script_hint = self._script_from_language(self.doc_defaults.get("lang"))
        for key in _FORMAT_KEYS:
            if key != "font":
                take(key, self.doc_defaults.get(key), "docDefaults")
        for attribute in _FONT_ATTRIBUTES:
            if effective["font"].get(attribute) is _UNRESOLVED_THEME:
                theme_token = effective["font"]["theme"].get(attribute)
                resolved_theme = self._theme_value(theme_token, script=script_hint)
                effective["font"][attribute] = resolved_theme
                evidence = effective["font"]["theme_evidence"].get(attribute)
                if isinstance(evidence, dict):
                    evidence["resolved"] = resolved_theme is not None
                    evidence["script"] = script_hint
                if resolved_theme is not None:
                    sources["font"][attribute] = "theme"
            elif effective["font"].get(attribute) is None and effective["font"]["theme"].get(attribute):
                effective["font"][attribute] = self._theme_value(
                    effective["font"]["theme"][attribute], script=script_hint
                )
        if not effective["font"]["theme"]:
            effective["font"].pop("theme", None)
            effective["font"].pop("theme_evidence", None)
        # Keep the structured ``font`` object, but also expose flat aliases.
        # The aliases make the JSON pleasant for simple consumers (and avoid
        # forcing a linter to guess how a nested font object is named).
        for attribute in _FONT_ATTRIBUTES:
            if effective["font"].get(attribute) is not None:
                effective[attribute] = effective["font"][attribute]
        if effective["font"].get("eastAsia") is not None:
            effective["east_asia_font"] = effective["font"]["eastAsia"]
        if effective["font"].get("ascii") is not None:
            effective["ascii_font"] = effective["font"]["ascii"]
        if effective["font"].get("hAnsi") is not None:
            effective["hansi_font"] = effective["font"]["hAnsi"]
        return {
            "raw": direct,
            "effective": effective,
            "source": sources,
            "resolution_chain": resolution_chain,
        }

    def effective_paragraph_rpr(self, paragraph_style_id: str | None) -> dict[str, Any]:
        """Expose paragraph-style rPr resolution for tests and callers."""
        result: dict[str, Any] = deepcopy(self.doc_defaults)
        if paragraph_style_id is None:
            paragraph_style_id = self.default_style_id("paragraph")
        for _, values in reversed(tuple(self._chain(paragraph_style_id))):
            result.update(deepcopy(values))
        return result

    def resolve_paragraph(self, ppr: etree._Element | None) -> dict[str, Any]:
        """Resolve paragraph properties through direct/style/default layers.

        The returned envelope deliberately keeps sparse ``raw`` values apart
        from fully shaped ``effective`` values.  ``source`` mirrors the shape
        of the effective mapping, so a linter can explain whether a value came
        from the paragraph, its style, a ``basedOn`` ancestor, or
        ``docDefaults``.
        """

        direct = parse_ppr(ppr)
        paragraph_style_id = self.paragraph_style_id(ppr)
        effective: dict[str, Any] = {
            "alignment": None,
            "indent": {key: None for key in _INDENT_KEYS},
            "spacing": {key: None for key in _SPACING_KEYS},
            "outline_level": None,
            "keep_with_next": None,
            "keep_together": None,
            "page_break_before": None,
            "widow_control": None,
        }
        source: dict[str, Any] = {
            "alignment": None,
            "indent": {key: None for key in _INDENT_KEYS},
            "spacing": {key: None for key in _SPACING_KEYS},
            "outline_level": None,
            "keep_with_next": None,
            "keep_together": None,
            "page_break_before": None,
            "widow_control": None,
        }

        def apply(values: dict[str, Any], layer: str) -> None:
            for key in (
                "alignment",
                "outline_level",
                "keep_with_next",
                "keep_together",
                "page_break_before",
                "widow_control",
            ):
                value = values.get(key)
                if value is not None and effective[key] is None:
                    effective[key] = value
                    source[key] = layer
            for group, keys in (("indent", _INDENT_KEYS), ("spacing", _SPACING_KEYS)):
                values_group = values.get(group)
                if not isinstance(values_group, dict):
                    continue
                for key in keys:
                    value = values_group.get(key)
                    if value is not None and effective[group][key] is None:
                        effective[group][key] = value
                        source[group][key] = layer

        resolution_chain = ["direct"]
        apply(direct, "direct")
        effective_style_id = paragraph_style_id or self.default_style_id("paragraph")
        for position, (style_id, _rpr_values) in enumerate(self._chain(effective_style_id)):
            style = self.styles.get(style_id, {})
            style_values = parse_ppr(style.get("ppr"))
            prefix = "paragraph_style" if paragraph_style_id is not None else "default_paragraph_style"
            layer = prefix + ":" + style_id if position == 0 else "basedOn:" + style_id
            resolution_chain.append(layer)
            apply(style_values, layer)
        resolution_chain.append("docDefaults")
        apply(self.doc_defaults_ppr, "docDefaults")
        return {
            "raw": direct,
            "effective": effective,
            "source": source,
            "resolution_chain": resolution_chain,
        }


__all__ = ["StyleResolver", "parse_ppr", "parse_rpr"]
