"""Read-only packaging profiles for the measured style_03 and style_17 assets."""

from __future__ import annotations

import re
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional

from common import ASSETS_DIR, CANVAS_WIDTH, SUBTITLE_SYMMETRIC_MAX_W, hex_to_rgba, read_json

_SPECTRUM_DIR = ASSETS_DIR / "style_spectrum"
_COLOR_METRICS = ("y_mean_255", "dark_ratio_pct", "dynamic_range_255", "warmth_index")
_SAFE_PROFILE = MappingProxyType({
    "style_id": "",
    "template_family": "",
    "color_baseline": MappingProxyType(dict.fromkeys(_COLOR_METRICS)),
    "broll_overlay_ratio_target": None,
    "font_candidates": ("SourceHanSerifCN-Heavy.otf", "SourceHanSerifCN-Bold.otf"),
    "font_size_px": 60,
    "giant_mode": "",
    "giant_font_size_px": 88,
    "flower_layout": "",
    "stroke_mode": "none",
    "stroke_px": 0,
    "shadow_color": "#00000000",
    "shadow": MappingProxyType({
        "shadow_rgba": (0, 0, 0, 0), "shadow_dx": 0, "shadow_dy": 0, "shadow_blur": 0.0,
    }),
    "persistent_top_bar": False,
    "quote_citation_card": False,
})


def _load_profiles() -> Mapping[str, Mapping[str, Any]]:
    library_path = _SPECTRUM_DIR / "style_spectrum_library.json"
    blueprint_path = _SPECTRUM_DIR / "template_blueprints.json"
    entries = read_json(library_path).get("styles", []) if library_path.is_file() else []
    blueprints = read_json(blueprint_path).get("blueprints", {}) if blueprint_path.is_file() else {}
    styles = {s["style_id"]: s for s in entries}
    profiles = {}
    for style_id in ("style_03", "style_17"):
        entry = styles.get(style_id, {})
        blueprint = blueprints.get(style_id, {})
        overlay = blueprint.get("overlay_style", {})
        subtitles = blueprint.get("subtitle_override", {})
        measured_color = entry.get("color_and_lut", {})
        flower = entry.get("flower_text_spec", {})
        giant = entry.get("giant_text_spec", {})
        packaging = entry.get("packaging_techniques", {})
        shadow_color = flower.get("shadow_color") or "#00000000"
        shadow_dx, shadow_dy = flower.get("shadow_offset") or (0, 0)
        fonts = (overlay.get("flower_font"), *subtitles.get("font_candidates", ()), *_SAFE_PROFILE["font_candidates"])
        profile = {
            **_SAFE_PROFILE,
            "style_id": style_id,
            "template_family": blueprint.get("template_family", ""),
            "color_baseline": MappingProxyType({key: measured_color.get(key) for key in _COLOR_METRICS}),
            "broll_overlay_ratio_target": entry.get("broll_overlay_ratio_target"),
            "font_candidates": tuple(dict.fromkeys(font for font in fonts if font)),
            "font_size_px": flower.get("font_size_1080p_px", _SAFE_PROFILE["font_size_px"]),
            "giant_mode": giant.get("mode", ""),
            "giant_font_size_px": giant.get("font_size_px", _SAFE_PROFILE["giant_font_size_px"]),
            "flower_layout": overlay.get("flower_layout") or overlay.get("flower_mode", ""),
            "stroke_mode": flower.get("stroke_mode", "none"),
            "stroke_px": int(flower.get("stroke_width") or 0) if flower.get("stroke_mode") == "stroke" else 0,
            "persistent_top_bar": bool(packaging.get("persistent_top_bar")),
            "quote_citation_card": bool(packaging.get("quote_citation_card")),
        }
        if flower.get("shadow"):
            profile["shadow_color"] = shadow_color
            profile["shadow"] = MappingProxyType({
                "shadow_rgba": hex_to_rgba(shadow_color, round(float(flower.get("shadow_alpha", 1)) * 255)),
                "shadow_dx": shadow_dx,
                "shadow_dy": shadow_dy,
                "shadow_blur": flower.get("shadow_blur_px", 0.0),
            })
        if style_id == "style_03":
            profile.update(
                bar_width_px=CANVAS_WIDTH - 120,
                quote_width_px=608,
                text_width_px=SUBTITLE_SYMMETRIC_MAX_W,
                bar_hex=overlay.get("bg_slab", "#B91C1C"),
                quote_hex=(measured_color.get("top5_palette_hex") or ["#110C0A"])[0],
                main_hex=subtitles.get("primary_color", "#FFFFFF"),
                sub_hex=subtitles.get("keyword_color", "#FFF589"),
                quote_triggers=("quote_citation", "quote_citation_card", "persistent_top_bar", "top_bar"),
            )
        else:
            profile.update(
                width_px=SUBTITLE_SYMMETRIC_MAX_W,
                bg_hex=subtitles.get("bg_box_color", "#000000B2"),
                main_hex=giant.get("fg_hex", "#FFBF17"),
                sub_hex=giant.get("accent_hex", "#FFFFFF"),
                line_hex=overlay.get("stroke_color", "#FFDE00"),
                line_width_px=2,
                compare_kind="center_burst_title",
                compare_separator=r"(?<![a-z])vs\.?(?![a-z])|对比|[｜|/／]",
            )
        profiles[style_id] = MappingProxyType(profile)
    return MappingProxyType(profiles)


STYLE_DNA_PROFILES = _load_profiles()


def get_style_dna_profile(style_id: str) -> Mapping[str, Any]:
    """Return a read-only view, including a safe neutral profile for unknown styles."""
    return STYLE_DNA_PROFILES.get(style_id, _SAFE_PROFILE)


def resolve_style03_bar_layout(
    main_text: str, sub_text: str = "", elem_kind: str = "", display_role: str = "",
) -> Dict[str, Any]:
    profile = get_style_dna_profile("style_03")
    triggers = profile["quote_triggers"]
    quote = elem_kind in triggers or display_role in triggers
    bg_hex = profile["quote_hex"] if quote else profile["bar_hex"]
    return {
        **profile,
        "bar_mode": "quote_citation_card" if quote else "full_width_crimson_bar",
        "anchor": "top" if quote else "center",
        "width_px": profile["quote_width_px"] if quote else profile["bar_width_px"],
        "bg_hex": bg_hex,
        "bg_rgba": hex_to_rgba(bg_hex, 238),
        "radius_px": 4,
        "main_text": main_text.strip(),
        "sub_text": sub_text.strip(),
    }


def resolve_style17_frame_layout(
    main_text: str, sub_text: str = "", elem_kind: str = "", compare_mode: Optional[bool] = None,
) -> Dict[str, Any]:
    profile = get_style_dna_profile("style_17")
    main_text, sub_text = main_text.strip(), sub_text.strip()
    separator = re.compile(profile["compare_separator"], re.IGNORECASE)
    compare = compare_mode is True or (
        elem_kind == profile["compare_kind"]
        and separator.search(f"{main_text} {sub_text}") is not None
    )
    if compare and not sub_text:
        parts = [part.strip() for part in separator.split(main_text, maxsplit=1)]
        if len(parts) == 2 and all(parts):
            main_text, sub_text = parts
    return {
        **profile,
        "layout_mode": "dual_gold_hairline_grid" if compare else "clean_fullscreen_gold",
        "line_width_px": profile["line_width_px"] if compare else 0,
        "bg_rgba": hex_to_rgba(profile["bg_hex"]),
        "radius_px": 0,
        "main_text": main_text,
        "sub_text": sub_text,
    }
