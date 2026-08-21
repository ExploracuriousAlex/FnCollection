#!/usr/bin/env python3
"""Generate a printable HTML checklist for Fortnite sprites.

The script reads the sprite registry from each SpriteLibrary_* plugin:
FortniteGame/Plugins/GameFeatures/<library>/Content/DataTables/DT_SpriteAssetRegistry*.json

It groups entries by ParentMappingID:
- ParentMappingID == "None" -> parent group
- otherwise -> child entry of the referenced parent MappingID

For each sprite it resolves:
- icon PNG path from the sprite definition DataList Icon AssetPathName
- German display name from localization files (fallback to source string)

Output is a single HTML file optimized for printing (A4 portrait).
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).parent
GAME_FEATURES_DIR = BASE_DIR / "FortniteGame" / "Plugins" / "GameFeatures"
SPRITE_CONTENT_DIR = GAME_FEATURES_DIR / "SpriteLibrary_CH7S3" / "Content"
SPRITE_LIBRARY_NAME = "SpriteLibrary_CH7S3"
REGISTRY_FILE = SPRITE_CONTENT_DIR / "DataTables" / "DT_SpriteAssetRegistry.json"
VARIANT_WEIGHTS_FILE = SPRITE_CONTENT_DIR / "DataTables" / "DT_VariantWeights.json"

LOC_ROOTS = [
    BASE_DIR / "FortniteGame" / "Content" / "Localization",
    BASE_DIR / "FortniteGame" / "Plugins" / "GameFeatures" / "SaveTheWorld" / "Content" / "Localization",
]


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _asset_to_json(asset_path: str) -> Path:
    """Convert /SpriteLibrary_CH7S3/... asset reference to exported JSON path."""
    stem = asset_path.rsplit(".", 1)[0]
    prefix = f"/{SPRITE_LIBRARY_NAME}/"
    if not stem.startswith(prefix):
        raise ValueError(f"Unsupported asset prefix in path: {asset_path}")
    rel = stem[len(prefix) :]
    return SPRITE_CONTENT_DIR / (rel + ".json")


def _asset_stem(asset_path: str) -> str:
    return asset_path.rsplit(".", 1)[0]


def _asset_to_png(asset_path: str) -> Path | None:
    """Resolve an icon asset path to an exported PNG file."""
    stem = asset_path.rsplit(".", 1)[0]
    prefix = f"/{SPRITE_LIBRARY_NAME}/"
    if not stem.startswith(prefix):
        return None
    rel = stem[len(prefix) :]
    png = SPRITE_CONTENT_DIR / (rel + ".png")
    return png if png.exists() else None


def _find_icon_asset(props: dict[str, Any]) -> str:
    for entry in props.get("DataList", []):
        if not isinstance(entry, dict):
            continue
        icon = entry.get("Icon", {})
        if isinstance(icon, dict):
            path = icon.get("AssetPathName", "")
            if path:
                return str(path)
    return ""


def _load_definition_props(asset_path: str) -> dict[str, Any]:
    """Load Properties dict from an exported sprite definition asset path."""
    try:
        definition_file = _asset_to_json(asset_path)
    except ValueError:
        return {}
    if not definition_file.exists():
        return {}
    try:
        data = _load_json(definition_file)
    except Exception:
        return {}
    if not isinstance(data, list) or not data:
        return {}
    props = data[0].get("Properties", {})
    return props if isinstance(props, dict) else {}


def _normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _load_lang_lookup(lang: str) -> dict[str, str]:
    """Load localization entries by text key for the selected language."""
    lookup: dict[str, str] = {}
    for root in LOC_ROOTS:
        if not root.exists():
            continue
        for lang_file in root.glob(f"**/{lang}/*.json"):
            try:
                data = _load_json(lang_file)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            for namespace_map in data.values():
                if not isinstance(namespace_map, dict):
                    continue
                for key, text in namespace_map.items():
                    if isinstance(key, str) and isinstance(text, str) and key and text:
                        lookup.setdefault(key, text)
    return lookup


def _resolve_display_name(
    props: dict[str, Any],
    lang_lookup: dict[str, str],
    mapping_id: str,
) -> str:
    name = ""

    item_name = props.get("ItemName", {})
    if isinstance(item_name, dict):
        key = item_name.get("Key", "")
        if isinstance(key, str) and key:
            translated = lang_lookup.get(key)
            if translated:
                name = _normalize_spaces(translated)

        if not name:
            localized = item_name.get("LocalizedString", "")
            if isinstance(localized, str) and localized:
                name = _normalize_spaces(localized)

        if not name:
            source = item_name.get("SourceString", "")
            if isinstance(source, str) and source:
                name = _normalize_spaces(source)

    if not name:
        # Fallback to readable mapping id if no label exists
        name = _normalize_spaces(mapping_id.replace("_", " "))

    return name


def _resolve_localized_text(
    text_obj: Any,
    lang_lookup: dict[str, str],
) -> str:
    """Resolve a localized text object with key/localized/source fallbacks."""
    text = ""

    if isinstance(text_obj, dict):
        key = text_obj.get("Key", "")
        if isinstance(key, str) and key:
            translated = lang_lookup.get(key)
            if translated:
                text = _normalize_spaces(translated)

        if not text:
            localized = text_obj.get("LocalizedString", "")
            if isinstance(localized, str) and localized:
                text = _normalize_spaces(localized)

        if not text:
            source = text_obj.get("SourceString", "")
            if isinstance(source, str) and source:
                text = _normalize_spaces(source)

    return text


def _resolve_item_description(
    props: dict[str, Any],
    lang_lookup: dict[str, str],
    lang: str,
) -> str:
    """Resolve ItemDescription and fall back to parent definition for variants."""
    description = _resolve_localized_text(
        props.get("ItemDescription", {}),
        lang_lookup,
    )
    if description:
        return description

    parent_def = props.get("ParentExtractableDefinition", {})
    if isinstance(parent_def, dict):
        parent_path = str(parent_def.get("AssetPathName", ""))
        if parent_path:
            parent_props = _load_definition_props(parent_path)
            if parent_props:
                return _resolve_localized_text(
                    parent_props.get("ItemDescription", {}),
                    lang_lookup,
                )
    return ""


def _resolve_possible_augments(
    props: dict[str, Any],
    lang_lookup: dict[str, str],
    lang: str,
) -> list[dict[str, str]]:
    """Resolve PossibleAugments into localized name/description pairs."""
    result: list[dict[str, str]] = []
    seen: set[str] = set()

    for entry in props.get("PossibleAugments", []):
        if not isinstance(entry, dict):
            continue
        augment_ref = entry.get("Augment", {})
        if not isinstance(augment_ref, dict):
            continue
        object_path = str(augment_ref.get("ObjectPath", ""))
        if not object_path:
            continue

        augment_asset = object_path.rsplit(".", 1)[0]
        if augment_asset in seen:
            continue
        seen.add(augment_asset)

        augment_props = _load_definition_props(augment_asset)
        if not augment_props:
            continue

        name = _resolve_localized_text(
            augment_props.get("ItemName", {}),
            lang_lookup,
        )
        description = _resolve_localized_text(
            augment_props.get("ItemDescription", {}),
            lang_lookup,
        )

        if name or description:
            result.append({"name": name, "description": description})

    return result


def _read_registry_rows() -> dict[str, dict[str, Any]]:
    data = _load_json(REGISTRY_FILE)
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"Unexpected registry format: {REGISTRY_FILE}")
    rows = data[0].get("Rows", {})
    if not isinstance(rows, dict):
        raise RuntimeError(f"Rows not found in registry: {REGISTRY_FILE}")
    return rows


def _load_variant_weights() -> dict[str, float]:
    """Return mapping from VariantDefinition asset stem -> weight."""
    if not VARIANT_WEIGHTS_FILE.exists():
        return {}

    data = _load_json(VARIANT_WEIGHTS_FILE)
    if not isinstance(data, list) or not data:
        return {}

    rows = data[0].get("Rows", {})
    if not isinstance(rows, dict):
        return {}

    result: dict[str, float] = {}
    for row in rows.values():
        if not isinstance(row, dict):
            continue
        variant_def = row.get("VariantDefinition", {})
        if not isinstance(variant_def, dict):
            continue
        path = str(variant_def.get("AssetPathName", ""))
        if not path:
            continue
        try:
            weight = float(row.get("Weight", 0.0))
        except (TypeError, ValueError):
            weight = 0.0
        result[_asset_stem(path)] = weight
    return result


def _configure_library(library_dir: Path) -> None:
    """Configure asset resolution for one SpriteLibrary plugin."""
    global SPRITE_CONTENT_DIR, SPRITE_LIBRARY_NAME, REGISTRY_FILE, VARIANT_WEIGHTS_FILE

    SPRITE_CONTENT_DIR = library_dir / "Content"
    SPRITE_LIBRARY_NAME = library_dir.name
    data_tables = SPRITE_CONTENT_DIR / "DataTables"
    registries = sorted(data_tables.glob("DT_SpriteAssetRegistry*.json"))
    if not registries:
        raise FileNotFoundError(f"Sprite registry not found in {data_tables}")
    REGISTRY_FILE = registries[0]

    variant_files = sorted(data_tables.glob("DT_VariantWeights*.json"))
    VARIANT_WEIGHTS_FILE = variant_files[0] if variant_files else data_tables / "DT_VariantWeights.json"


def _find_sprite_libraries() -> list[Path]:
    """Find SpriteLibrary plugins that contain an exported sprite registry."""
    libraries: list[Path] = []
    for library_dir in sorted(GAME_FEATURES_DIR.glob("SpriteLibrary_*"), key=lambda p: p.name.lower()):
        if any((library_dir / "Content" / "DataTables").glob("DT_SpriteAssetRegistry*.json")):
            libraries.append(library_dir)
    return libraries


def _is_base_variant(mapping_id: str) -> bool:
    mid = mapping_id.lower()
    return (
        mid.endswith("_variant_a")
        or mid.endswith("_variation_a")
        or mid.endswith("_variant_base")
    )


def _find_pdf_browser() -> str | None:
    candidates = [
        "msedge",
        "chrome",
        "chromium",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for candidate in candidates:
        if Path(candidate).is_absolute():
            if Path(candidate).exists():
                return candidate
        else:
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
    return None


def _write_pdf_from_html(html_file: Path, pdf_file: Path) -> bool:
    browser = _find_pdf_browser()
    if not browser:
        return False

    pdf_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--print-to-pdf-no-header",
        f"--print-to-pdf={str(pdf_file)}",
        html_file.resolve().as_uri(),
    ]
    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except OSError:
        return False
    return result.returncode == 0 and pdf_file.exists()


def _html_path(path: Path, output_file: Path) -> str:
    rel = path.relative_to(output_file.parent)
    return rel.as_posix()


_PREFERRED_VARIANT_ORDER = [
    "Normal",
    "Gold",
    "Gummi",
    "Holofolie",
    "Galaxie",
    "Edelstein",
    "Würfel",
]


def _canonical_variant_label(label: str) -> str:
    """Normalize known variant spellings to stable display labels."""
    normalized = _normalize_spaces(label)
    lowered = normalized.lower()
    mapping = {
        "normal": "Normal",
        "gold": "Gold",
        "gummi": "Gummi",
        "holofolie": "Holofolie",
        "holo": "Holofolie",
        "galaxie": "Galaxie",
        "edelstein": "Edelstein",
        "gem": "Edelstein",
        "wuerfel": "Würfel",
        "würfel": "Würfel",
        "cube": "Würfel",
    }
    return mapping.get(lowered, normalized)


def _extract_variant_label(parent_name: str, item_name: str, is_parent: bool) -> str:
    """Infer a compact variant label from item and parent names."""
    if is_parent:
        return "Normal"

    known_tokens = [
        ("Gold", "Gold"),
        ("Gummi", "Gummi"),
        ("Holofolie", "Holofolie"),
        ("Holo", "Holofolie"),
        ("Galaxie", "Galaxie"),
        ("Edelstein", "Edelstein"),
        ("Gem", "Edelstein"),
        ("Wuerfel", "Würfel"),
        ("Würfel", "Würfel"),
        ("Cube", "Würfel"),
    ]
    lowered_name = item_name.lower()
    for token, canonical in known_tokens:
        if token.lower() in lowered_name:
            return canonical

    parent_re = re.escape(parent_name)
    candidate = item_name
    candidate = re.sub(rf"^\s*{parent_re}\s*[-:]\s*", "", candidate, flags=re.IGNORECASE)
    candidate = re.sub(rf"\s*[-:]\s*{parent_re}\s*$", "", candidate, flags=re.IGNORECASE)
    candidate = re.sub(parent_re, "", candidate, flags=re.IGNORECASE)
    candidate = _normalize_spaces(candidate.strip(" -_"))
    if not candidate:
        return "Normal"
    return _canonical_variant_label(candidate)


def _build_quick_overview(groups: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    """Build row/column data for a compact type x variant overview table."""
    rows: list[dict[str, Any]] = []
    all_variants: set[str] = set()

    for group in groups:
        variants_for_type: set[str] = set()
        title = str(group.get("title", ""))
        items = group.get("items", [])
        if not isinstance(items, list):
            items = []

        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", ""))
            if not name:
                continue
            variant = _extract_variant_label(title, name, is_parent=(index == 0))
            variants_for_type.add(_canonical_variant_label(variant))

        if not variants_for_type:
            variants_for_type.add("Normal")

        all_variants.update(variants_for_type)
        rows.append({"type": title, "variants": variants_for_type})

    ordered_variants: list[str] = []
    for preferred in _PREFERRED_VARIANT_ORDER:
        if preferred in all_variants:
            ordered_variants.append(preferred)

    remaining = sorted(v for v in all_variants if v not in ordered_variants)
    ordered_variants.extend(remaining)

    return ordered_variants, rows


def _render_html(groups: list[dict[str, Any]], output_file: Path, generated_at: str) -> str:
    sections: list[str] = []

    quick_variants, quick_rows = _build_quick_overview(groups)
    quick_header = "".join(f"<th>{html.escape(variant)}</th>" for variant in quick_variants)
    quick_body_rows: list[str] = []
    for row in quick_rows:
        cells: list[str] = []
        row_variants = row["variants"]
        for variant in quick_variants:
            if variant in row_variants:
                cells.append('<td><div class="quick-check" aria-label="vorhanden"></div></td>')
            else:
                cells.append('<td class="na">&ndash;</td>')
        quick_body_rows.append(
            "<tr>"
            f"<th scope=\"row\">{html.escape(row['type'])}</th>"
            + "".join(cells)
            + "</tr>"
        )

    quick_overview_html = (
        "<section class=\"quick-overview\">"
        "<h2>Schnellübersicht</h2>"
        "<table class=\"quick-table\">"
        "<thead><tr><th>Typ</th>"
        f"{quick_header}</tr></thead>"
        "<tbody>"
        + "".join(quick_body_rows)
        + "</tbody></table></section>"
    )

    for group in groups:
        group_description = group.get("description", "")
        group_description_html = (
            f"<div class=\"group-description\">{html.escape(group_description)}</div>"
            if group_description
            else ""
        )

        rows: list[str] = []
        for item in group["items"]:
            icon_cell = (
                f'<img class="icon" src="{html.escape(item["icon_rel"])}" alt="{html.escape(item["name"])}">'
                if item["icon_rel"]
                else '<div class="icon placeholder">Kein Icon</div>'
            )
            specials_html = ""
            if item.get("special_augments"):
                # Show only the 2nd special property (variant-specific one)
                if len(item["special_augments"]) >= 2:
                    aug = item["special_augments"][1]
                    aug_name = html.escape(aug.get("name", ""))
                    aug_desc = html.escape(aug.get("description", ""))
                    if aug_name and aug_desc:
                        specials_html = f'<div class="specials"><strong>{aug_name}:</strong> {aug_desc}</div>'
                    elif aug_name:
                        specials_html = f'<div class="specials"><strong>{aug_name}</strong></div>'
                    elif aug_desc:
                        specials_html = f'<div class="specials">{aug_desc}</div>'
            rows.append(
                "<div class=\"item\">"
                "<div class=\"check\"></div>"
                f"{icon_cell}"
                "<div>"
                f"<div class=\"name\">{html.escape(item['name'])}</div>"
                f"{specials_html}"
                "</div>"
                "</div>"
            )

        sections.append(
            "<section class=\"group\">"
            f"<h2>{html.escape(group['title'])}</h2>"
            f"{group_description_html}"
            "<div class=\"items\">"
            + "\n".join(rows)
            + "</div></section>"
        )

    return f"""<!doctype html>
<html lang=\"de\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>Fortnite Sprite Checkliste</title>
  <style>
    :root {{
      --text: #111;
      --muted: #5e5e5e;
      --line: #d7d7d7;
      --soft: #f6f6f6;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--text);
      background: #fff;
      font-family: "Segoe UI", "Calibri", sans-serif;
      line-height: 1.25;
    }}
    .page {{
      max-width: 980px;
      margin: 0 auto;
      padding: 16mm 12mm;
    }}
    h1 {{
      margin: 0 0 4mm;
      font-size: 26px;
      letter-spacing: 0.2px;
    }}
    .meta {{
      margin-bottom: 7mm;
      color: var(--muted);
      font-size: 12px;
    }}
        .quick-overview {{
            border: 1px solid var(--line);
            border-radius: 10px;
            padding: 4mm;
            margin-bottom: 6mm;
            background: #fff;
        }}
        .quick-overview h2 {{
            margin: 0 0 2mm;
            font-size: 16px;
            border: none;
            padding: 0;
        }}
        .quick-table {{
            width: 100%;
            border-collapse: collapse;
            table-layout: fixed;
            font-size: 10.5px;
        }}
        .quick-table th,
        .quick-table td {{
            border: 1px solid var(--line);
            padding: 1.2mm;
            text-align: center;
            vertical-align: middle;
        }}
        .quick-table thead th {{
            background: var(--soft);
            font-size: 10px;
            font-weight: 700;
        }}
        .quick-table tbody th {{
            text-align: left;
            font-weight: 600;
            font-size: 12.5px;
            width: 28mm;
            background: #fff;
        }}
        .quick-check {{
            width: 4.2mm;
            height: 4.2mm;
            border: 1.2px solid #222;
            border-radius: 0.8mm;
            margin: 0 auto;
        }}
        .quick-table td.na {{
            color: #a3a3a3;
            font-size: 11px;
        }}
    .group {{
      break-inside: avoid;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 4mm;
      margin-bottom: 5mm;
      background: linear-gradient(180deg, #fff 0%, var(--soft) 100%);
    }}
    h2 {{
      margin: 0 0 3mm;
      font-size: 16px;
      border-bottom: 1px solid var(--line);
      padding-bottom: 2mm;
    }}
    .items {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
      gap: 2mm 3mm;
    }}
        .item {{
      display: grid;
      grid-template-columns: 6mm 18mm 1fr;
      gap: 3mm;
      align-items: center;
            min-height: 20mm;
            border: 1px solid var(--line);
            border-radius: 8px;
            padding: 2.5mm;
            background: #fff;
    }}
        .check {{
            width: 5mm;
            height: 5mm;
            border: 1.4px solid #222;
            border-radius: 1mm;
            margin: 0 auto;
        }}
        .icon {{
            width: 16mm;
            height: 16mm;
            object-fit: contain;
            display: block;
        }}
    .icon.placeholder {{
      width: 16mm;
      height: 16mm;
      display: grid;
      place-items: center;
      font-size: 8px;
      border: 1px dashed #aaa;
      color: #666;
      text-align: center;
    }}
    .name {{
      font-size: 13px;
      font-weight: 600;
      word-break: break-word;
    }}
        .specials {{
            margin-top: 1mm;
            padding-top: 1mm;
            border-top: 1px dashed #d3d3d3;
            font-size: 9.5px;
            color: var(--muted);
            line-height: 1.2;
            word-break: break-word;
        }}
        .group-description {{
            margin: 0 0 3mm;
            font-size: 11px;
            color: var(--muted);
            line-height: 1.3;
            word-break: break-word;
        }}

    @media print {{
      @page {{ size: A4 portrait; margin: 8mm; }}
      body {{ print-color-adjust: exact; -webkit-print-color-adjust: exact; }}
      .page {{ max-width: none; margin: 0; padding: 0; }}
      h1 {{ font-size: 22px; }}
      .meta {{ margin-bottom: 4mm; }}
            .quick-overview {{ break-after: page; page-break-after: always; }}
            .quick-table {{ font-size: 9.5px; }}
            .quick-table th,
            .quick-table td {{ padding: 1mm; }}
                        .quick-table tbody th {{ font-size: 11.5px; }}
      .group {{ margin-bottom: 3mm; }}
    }}
  </style>
</head>
<body>
  <main class=\"page\">
    <h1>Fortnite Geister - Checkliste</h1>
        <div class="meta">Stand: {html.escape(generated_at)}</div>
        {quick_overview_html}
    {''.join(sections)}
  </main>
</body>
</html>
"""


def build_checklist(
    lang: str,
    output: Path,
    include_base_variants: bool,
    game_order: bool,
    library_dir: Path | None = None,
) -> tuple[int, int]:
    if library_dir is not None:
        _configure_library(library_dir)

    rows = _read_registry_rows()
    lang_lookup = _load_lang_lookup(lang)
    variant_weights = _load_variant_weights() if game_order else {}

    parents: dict[str, dict[str, Any]] = {}
    children: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row_key, row in rows.items():
        if not isinstance(row, dict):
            continue
        mapping_id = str(row.get("MappingID") or row_key)
        parent_mapping = str(row.get("ParentMappingID", "None"))
        asset = row.get("Asset", {})
        asset_path = ""
        if isinstance(asset, dict):
            asset_path = str(asset.get("AssetPathName", ""))

        if not asset_path:
            continue

        if not include_base_variants and _is_base_variant(mapping_id):
            continue

        try:
            definition_file = _asset_to_json(asset_path)
        except ValueError:
            continue

        if not definition_file.exists():
            continue

        try:
            def_data = _load_json(definition_file)
        except Exception:
            continue

        if not isinstance(def_data, list) or not def_data:
            continue

        props = def_data[0].get("Properties", {})
        if not isinstance(props, dict):
            props = {}

        display_name = _resolve_display_name(props, lang_lookup, mapping_id)
        description = _resolve_item_description(props, lang_lookup, lang)
        is_non_base_variant = parent_mapping != "None" and not _is_base_variant(mapping_id)
        special_augments = _resolve_possible_augments(props, lang_lookup, lang) if is_non_base_variant else []
        dex_number_raw = props.get("DexNumber")
        dex_number: int | None = dex_number_raw if isinstance(dex_number_raw, int) else None

        icon_asset = _find_icon_asset(props)
        icon_file = _asset_to_png(icon_asset) if icon_asset else None
        icon_rel = _html_path(icon_file, output) if icon_file else ""
        asset_stem = _asset_stem(asset_path)

        entry = {
            "mapping_id": mapping_id,
            "name": display_name,
            "description": description,
            "special_augments": special_augments,
            "icon_rel": icon_rel,
            "asset_stem": asset_stem,
            "dex_number": dex_number,
            "variant_weight": variant_weights.get(asset_stem),
        }

        if parent_mapping == "None":
            parents[mapping_id] = entry
        else:
            children[parent_mapping].append(entry)

    # Ensure parents that are only referenced by children still appear
    for parent_id in children:
        parents.setdefault(
            parent_id,
            {
                "mapping_id": parent_id,
                "name": _normalize_spaces(parent_id.replace("_", " ")),
                "description": "",
                "special_augments": [],
                "icon_rel": "",
            },
        )

    groups: list[dict[str, Any]] = []
    def _parent_sort_key(item: tuple[str, dict[str, Any]]) -> tuple[int, int, str]:
        _, parent = item
        dex = parent.get("dex_number")
        if isinstance(dex, int):
            return (0, dex, str(parent.get("name", "")).lower())
        return (1, 999999, str(parent.get("name", "")).lower())

    def _child_sort_key(child: dict[str, Any]) -> tuple[int, float, str]:
        weight = child.get("variant_weight")
        if isinstance(weight, (int, float)):
            return (0, -float(weight), str(child.get("name", "")).lower())
        return (1, 0.0, str(child.get("name", "")).lower())

    parent_iter = sorted(parents.items(), key=_parent_sort_key if game_order else lambda kv: kv[1]["name"].lower())
    for parent_id, parent in parent_iter:
        child_items = children.get(parent_id, [])
        child_sorted = sorted(child_items, key=_child_sort_key if game_order else lambda e: e["name"].lower())
        items = [parent] + child_sorted
        group_description = parent.get("description", "")
        if not group_description:
            for child in child_sorted:
                child_description = child.get("description", "")
                if child_description:
                    group_description = child_description
                    break
        groups.append({"title": parent["name"], "description": group_description, "items": items})

    from datetime import datetime

    html_text = _render_html(groups, output, datetime.now().strftime("%Y-%m-%d %H:%M"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html_text, encoding="utf-8")

    total_items = sum(len(g["items"]) for g in groups)
    return len(groups), total_items


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate printable Sprite checklist HTML")
    parser.add_argument("--lang", default="de", help="Language code for name localization (default: de)")
    parser.add_argument(
        "--output",
        default=str(BASE_DIR / "sprite_checklist.html"),
        help="Output HTML path; with multiple libraries, each library gets its own suffixed file",
    )
    parser.add_argument(
        "--library",
        action="append",
        help="Only process this SpriteLibrary directory name; may be specified more than once",
    )
    parser.add_argument(
        "--include-base-variants",
        action="store_true",
        help="Keep base duplicates like *_Variant_A / *_Variation_A in output",
    )
    parser.add_argument(
        "--no-game-order",
        action="store_true",
        help="Disable game-informed sorting (DexNumber and variant weights)",
    )
    parser.add_argument(
        "--pdf",
        action="store_true",
        help="Also create a PDF next to the output HTML using a headless browser",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    libraries = _find_sprite_libraries()
    if args.library:
        requested = {name.lower() for name in args.library}
        libraries = [library for library in libraries if library.name.lower() in requested]
    if not libraries:
        raise RuntimeError("No SpriteLibrary_* plugin with a sprite registry was found")

    output = Path(args.output)
    for library_dir in libraries:
        library_output = output.with_name(
            f"{output.stem}_{library_dir.name}{output.suffix}"
        )
        groups, items = build_checklist(
            args.lang,
            library_output,
            include_base_variants=args.include_base_variants,
            game_order=not args.no_game_order,
            library_dir=library_dir,
        )
        print(f"Created checklist: {library_output}")
        print(f"Library: {library_dir.name} | Groups: {groups} | Items: {items}")

        if args.pdf:
            pdf_output = library_output.with_suffix(".pdf")
            if _write_pdf_from_html(library_output, pdf_output):
                print(f"Created PDF: {pdf_output}")
            else:
                print(f"PDF export skipped for {library_dir.name}: no supported headless browser found (Edge/Chrome).")


if __name__ == "__main__":
    main()
