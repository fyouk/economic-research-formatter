"""Image relationship and pixel/color analysis for DOCX drawings."""

from __future__ import annotations

from io import BytesIO
from typing import Any
import warnings

from PIL import Image

from ..models.inspection import DocxInspectionError
from .package import DocxPackage, R_NS, W_NS, qname


DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
# An ordinary paper image is far below this canvas size.  Checking dimensions
# immediately after reading the image header prevents ``convert`` and
# ``getdata`` from allocating an unbounded pixel list.
MAX_IMAGE_PIXELS = 25_000_000


def _emu(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def analyze_image(data: bytes) -> dict[str, Any]:
    thresholds = {
        "channel_delta_for_color": 10,
        "nonwhite_channel_threshold": 245,
        "grayscale_mean_delta": 2,
        "alpha_composite_background": "white",
    }
    try:
        with Image.open(BytesIO(data)) as source:
            width, height = source.size
            if width * height > MAX_IMAGE_PIXELS:
                raise DocxInspectionError(
                    f"Image exceeds pixel limit: {width}x{height} > {MAX_IMAGE_PIXELS}",
                    kind="resource_limit",
                )
            image = source.convert("RGBA")
            # Composite transparent pixels onto white before calculating color
            # channels.  Transparent black line-art should remain grayscale.
            white = Image.new("RGBA", image.size, (255, 255, 255, 255))
            composite = Image.alpha_composite(white, image).convert("RGB")
            with warnings.catch_warnings():
                # Pillow 14 will expose get_flattened_data; older supported
                # versions only provide getdata and emit a forward-looking
                # deprecation warning.
                warnings.simplefilter("ignore", category=DeprecationWarning)
                pixels = list(composite.getdata())
            colored = 0
            nonwhite = 0
            grayscale = True
            for red, green, blue in pixels:
                if min(red, green, blue) < thresholds["nonwhite_channel_threshold"]:
                    nonwhite += 1
                if max(red, green, blue) - min(red, green, blue) > thresholds["channel_delta_for_color"]:
                    colored += 1
                if max(red, green, blue) - min(red, green, blue) > thresholds["grayscale_mean_delta"]:
                    grayscale = False
            ratio = colored / len(pixels) if pixels else 0.0
            return {
                "format": (source.format or "").lower() or None,
                "mode": source.mode,
                "pixel_width": width,
                "pixel_height": height,
                "has_alpha": "A" in source.getbands(),
                "is_probably_grayscale": grayscale,
                "colored_nonwhite_pixel_ratio": ratio,
                "nonwhite_pixel_ratio": nonwhite / len(pixels) if pixels else 0.0,
                "thresholds": thresholds,
            }
    except DocxInspectionError:
        raise
    except Exception as exc:  # Pillow uses several exception classes by format.
        return {
            "format": None,
            "mode": None,
            "pixel_width": None,
            "pixel_height": None,
            "has_alpha": None,
            "is_probably_grayscale": None,
            "colored_nonwhite_pixel_ratio": None,
            "nonwhite_pixel_ratio": None,
            "thresholds": thresholds,
            "error": type(exc).__name__,
        }


def inspect_images(package: DocxPackage, paragraph_ids: dict[int, str], paragraph_texts: dict[int, str]) -> list[dict[str, Any]]:
    relationships = package.relationships("word/document.xml")
    images: list[dict[str, Any]] = []
    for paragraph in package.document_root.iter(qname(W_NS, "p")):
        paragraph_id = paragraph_ids.get(id(paragraph))
        for drawing in paragraph.iter(qname(W_NS, "drawing")):
            inline = drawing.find(f"{{{DRAWING_NS}}}inline")
            anchor = drawing.find(f"{{{DRAWING_NS}}}anchor")
            container = inline if inline is not None else anchor
            placement = "inline" if inline is not None else "anchor" if anchor is not None else "unknown"
            extent = container.find(f"{{{DRAWING_NS}}}extent") if container is not None else None
            display_width = _emu(extent.get("cx")) if extent is not None else None
            display_height = _emu(extent.get("cy")) if extent is not None else None
            for blip in drawing.iter(qname(A_NS, "blip")):
                relationship_id = blip.get(qname(R_NS, "embed")) or blip.get(qname(R_NS, "link"))
                relation = relationships.get(relationship_id or "")
                if relation is None or not relation.rel_type.endswith("/image"):
                    continue
                media_part = package.resolve_target("word/document.xml", relation.target)
                data = package.read(media_part) if relation.target_mode != "External" else None
                analysis = analyze_image(data) if data is not None else {
                    "is_probably_grayscale": None,
                    "colored_nonwhite_pixel_ratio": None,
                    "thresholds": {},
                    "external": True,
                }
                images.append({
                    "id": f"img-{len(images):06d}",
                    "paragraph_id": paragraph_id,
                    "relationship_id": relationship_id,
                    "media_part": media_part if relation.target_mode != "External" else relation.target,
                    "file_type": package.content_type(media_part) if relation.target_mode != "External" else None,
                    "placement": placement,
                    "inline": placement == "inline",
                    "anchor": placement == "anchor",
                    "display_width_emu": display_width,
                    "display_height_emu": display_height,
                    "display_width_pt": display_width / 12700 if display_width is not None else None,
                    "display_height_pt": display_height / 12700 if display_height is not None else None,
                    "color_analysis": analysis,
                    "caption_candidates": [],
                })
    # Associate nearby short caption paragraphs without changing paragraph
    # semantics; this is evidence only, never a classifier decision.
    ordered = list(paragraph_texts.items())
    index_by_xml_id = {xml_id: idx for idx, (xml_id, _) in enumerate(ordered)}
    for image in images:
        xml_id = next((xml_id for xml_id, stable in paragraph_ids.items() if stable == image["paragraph_id"]), None)
        if xml_id is None:
            continue
        position = index_by_xml_id.get(xml_id)
        if position is None:
            continue
        candidates = []
        for neighbor in (position - 1, position + 1):
            if 0 <= neighbor < len(ordered):
                text = ordered[neighbor][1].strip()
                if text and (text.startswith("图") or text.lower().startswith("figure")):
                    candidates.append({"paragraph_id": paragraph_ids[ordered[neighbor][0]], "text_preview": text[:80]})
        image["caption_candidates"] = candidates
    return images


__all__ = ["MAX_IMAGE_PIXELS", "analyze_image", "inspect_images"]
