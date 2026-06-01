import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from rich import print as rprint

from data_loader import AVAILABLE_LANGS, _get_loc_key_maps, _loc, lookup_item_definition_name
from get_data import _build_template_slot_map, _lookup_template_slot_ids, get_collection_book
from missing_items import BASE_DIR, RAW_DIR, _find_latest_raw_snapshot


RECOMMENDATION_MODEL_FILE = BASE_DIR / "config" / "trap_recommendations.json"
DEFAULT_OUTPUT_DIR = BASE_DIR
ALTERATION_V2_DIR = (
    BASE_DIR
    / "FortniteGame"
    / "Plugins"
    / "GameFeatures"
    / "SaveTheWorld"
    / "Content"
    / "Items"
    / "Alteration_v2"
    / "AttributeAlterations"
)


@dataclass(frozen=True)
class BuildDefinition:
    description: str
    alterations: tuple[str, ...]


@dataclass(frozen=True)
class TrapDefinition:
    trap_template: str
    builds: tuple[BuildDefinition, ...]


@dataclass(frozen=True)
class RecommendationModel:
    traps: tuple[TrapDefinition, ...]


@dataclass(frozen=True)
class AlterationAssetText:
    loc_key: str
    source: str


def _normalized_trap_template(template_id: str) -> str:
    token = template_id.split(":", 1)[-1].lower().strip()
    return token


def _load_model(path: Path) -> RecommendationModel:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    if not isinstance(raw, dict):
        raise ValueError("Recommendation model must be a JSON object.")

    raw_traps = raw.get("traps", [])

    if not isinstance(raw_traps, list):
        raise ValueError("'traps' must be a list.")

    traps: list[TrapDefinition] = []
    for trap_entry in raw_traps:
        if not isinstance(trap_entry, dict):
            continue
        trap_template = str(trap_entry.get("trap_template", "")).strip()
        builds_raw = trap_entry.get("builds", [])

        if not trap_template:
            raise ValueError(f"Trap entry is incomplete: {trap_entry}")
        if not isinstance(builds_raw, list):
            raise ValueError(f"Trap entry has invalid builds: {trap_template}")

        builds: list[BuildDefinition] = []
        for build_entry in builds_raw:
            if not isinstance(build_entry, dict):
                continue
            build_description = str(build_entry.get("description", "")).strip()
            alterations = build_entry.get("alterations", [])
            if not build_description or not isinstance(alterations, list):
                raise ValueError(f"Build entry is invalid for trap '{trap_template}': {build_entry}")
            alterations_t = tuple(str(a).strip().lower() for a in alterations if str(a).strip())
            builds.append(BuildDefinition(build_description, alterations_t))

        traps.append(TrapDefinition(trap_template, tuple(builds)))

    return RecommendationModel(tuple(traps))


def _load_latest_inventory() -> tuple[dict, Path]:
    latest_dir = _find_latest_raw_snapshot(RAW_DIR, "normalized_inventory.json")
    if latest_dir is None:
        raise FileNotFoundError("No raw_data snapshot with normalized_inventory.json found.")
    latest_file = latest_dir / "normalized_inventory.json"
    with open(latest_file, encoding="utf-8") as f:
        return json.load(f), latest_file


def _load_collection_book_context(lang: str) -> tuple[dict[str, list[str]], dict[str, dict]]:
    collection_book = get_collection_book(lang)
    template_map = _build_template_slot_map(collection_book)
    slot_lookup: dict[str, dict] = {}
    for page in collection_book.get("pages", {}).values():
        for section in page.get("sections", []):
            for slot in section.get("slots", []):
                slot_id = slot.get("slot_id")
                if isinstance(slot_id, str) and slot_id:
                    slot_lookup[slot_id] = slot
    return template_map, slot_lookup


def _load_alteration_asset_texts() -> dict[str, AlterationAssetText]:
    result: dict[str, AlterationAssetText] = {}
    if not ALTERATION_V2_DIR.exists():
        return result

    for json_path in ALTERATION_V2_DIR.rglob("AID_*.json"):
        try:
            with open(json_path, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue

        if not isinstance(payload, list) or not payload:
            continue
        root = payload[0]
        if not isinstance(root, dict):
            continue

        raw_name = root.get("Name", "")
        if not isinstance(raw_name, str) or not raw_name.strip():
            continue

        properties = root.get("Properties", {})
        if not isinstance(properties, dict):
            continue

        item_description = properties.get("ItemDescription", {})
        if not isinstance(item_description, dict):
            continue

        loc_key = item_description.get("Key", "")
        source = item_description.get("SourceString", "")
        if not isinstance(loc_key, str):
            loc_key = ""
        if not isinstance(source, str):
            source = ""

        alteration_id = raw_name.strip().lower()
        result[alteration_id] = AlterationAssetText(loc_key=loc_key.strip(), source=source.strip())

    return result


def _item_level(item: dict) -> int:
    attrs = item.get("attributes", {})
    if not isinstance(attrs, dict):
        return 0
    for key in ("level", "starting_level"):
        value = attrs.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
    return 0


def _item_alterations(item: dict) -> Counter[str]:
    attrs = item.get("attributes", {})
    if not isinstance(attrs, dict):
        return Counter()
    alterations = attrs.get("alterations", [])
    if not isinstance(alterations, list):
        return Counter()
    tokens: list[str] = []
    for alteration in alterations:
        if not isinstance(alteration, str):
            continue
        tokens.append(alteration.split(":", 1)[-1].lower().strip())
    return Counter(tokens)


def _matches_build(item: dict, trap: TrapDefinition, build: BuildDefinition) -> bool:
    template_id = item.get("templateId", "")
    if not isinstance(template_id, str):
        return False
    if _normalized_trap_template(template_id) != _normalized_trap_template(trap.trap_template):
        return False

    if _item_level(item) < 50:
        return False

    actual = _item_alterations(item)
    required = Counter(build.alterations)
    for alteration_id, required_count in required.items():
        if not alteration_id.endswith("_t05"):
            return False
        if actual.get(alteration_id, 0) < required_count:
            return False
    return True


def _localized_trap_name(trap: TrapDefinition, lang: str) -> str:
    resolved = lookup_item_definition_name(trap.trap_template)
    if isinstance(resolved, dict):
        value = resolved.get(lang)
        if isinstance(value, str) and value.strip() and value.strip() != "-":
            return value.strip()
    return _normalized_trap_template(trap.trap_template)


def _build_description(build: BuildDefinition) -> str:
    return build.description


def _markdown_icon_src(icon_url: str, output_dir: Path) -> str:
    icon_url = icon_url.strip()
    if not icon_url.startswith("/gameicon/"):
        return icon_url

    relative = icon_url[len("/gameicon/"):]
    content_dir, _, img_path = relative.partition("/")
    if not content_dir or not img_path:
        return icon_url

    if content_dir == "FortniteGame":
        target = BASE_DIR / "FortniteGame" / "Content" / img_path
    else:
        target = BASE_DIR / "FortniteGame" / "Plugins" / "GameFeatures" / content_dir / "Content" / img_path

    return Path(os.path.relpath(target, output_dir)).as_posix()


def _resolve_trap_slot(trap: TrapDefinition, template_map: dict[str, list[str]], slot_lookup: dict[str, dict]) -> dict | None:
    template_token = trap.trap_template.lower().strip()
    slot_ids = _lookup_template_slot_ids(template_map, template_token)
    for slot_id in slot_ids:
        slot = slot_lookup.get(slot_id)
        if isinstance(slot, dict):
            return slot
    return None


def _localized_text_by_loc_key(loc_key: str, fallback_en: str, lang: str) -> str:
    if not loc_key:
        return ""
    localized = _get_loc_key_maps().get(lang, {}).get(loc_key)
    if isinstance(localized, str) and localized.strip():
        return localized.strip()
    return ""


def _localized_alteration_label(
    alteration_id: str,
    lang: str,
    asset_texts: dict[str, AlterationAssetText],
) -> str:
    asset_hit = asset_texts.get(alteration_id)
    if asset_hit:
        resolved = _localized_text_by_loc_key(asset_hit.loc_key, asset_hit.source or alteration_id, lang)
        if resolved.strip():
            return resolved.strip()

    return alteration_id


def _render_build_block(
    build: BuildDefinition,
    lang: str,
    asset_texts: dict[str, AlterationAssetText],
) -> list[str]:
    lines: list[str] = [f"### {_build_description(build)}"]
    lines.append(_loc("Perks:", lang))
    lines.append("")
    for alteration_id in build.alterations:
        lines.append(f"- {_localized_alteration_label(alteration_id, lang, asset_texts)}")
    lines.append("")
    return lines


def find_missing_builds(inventory: dict, model: RecommendationModel) -> list[tuple[TrapDefinition, BuildDefinition]]:
    schematics = inventory.get("schematics", {})
    if not isinstance(schematics, dict):
        return [(trap, build) for trap in model.traps for build in trap.builds]

    items = [item for item in schematics.values() if isinstance(item, dict)]
    missing: list[tuple[TrapDefinition, BuildDefinition]] = []
    for trap in model.traps:
        for build in trap.builds:
            if any(_matches_build(item, trap, build) for item in items):
                continue
            missing.append((trap, build))
    return missing


def _render_general_markdown(
    model: RecommendationModel,
    lang: str,
    asset_texts: dict[str, AlterationAssetText],
    template_map: dict[str, list[str]],
    slot_lookup: dict[str, dict],
    output_dir: Path,
) -> str:
    lines: list[str] = []
    lines.append(f"# {_loc('Fortnite STW - Trap Recommendations', lang)}")
    lines.append("")
    lines.append(f"_Language: {lang}_")
    lines.append("")

    for trap in model.traps:
        trap_slot = _resolve_trap_slot(trap, template_map, slot_lookup)
        trap_name = str(trap_slot.get("name", "")).strip() if isinstance(trap_slot, dict) else ""
        trap_icon = str(trap_slot.get("icon_url", "")).strip() if isinstance(trap_slot, dict) else ""
        lines.append(f"## {trap_name or _localized_trap_name(trap, lang)}")
        if trap_icon:
            lines.append(f'<img src="{_markdown_icon_src(trap_icon, output_dir)}" width="120">')
        lines.append("")
        for build in trap.builds:
            lines.extend(_render_build_block(build, lang, asset_texts))

    return "\n".join(lines).rstrip() + "\n"


def _render_missing_markdown(
    model: RecommendationModel,
    missing: list[tuple[TrapDefinition, BuildDefinition]],
    lang: str,
    inventory_file: Path,
    asset_texts: dict[str, AlterationAssetText],
    template_map: dict[str, list[str]],
    slot_lookup: dict[str, dict],
    output_dir: Path,
) -> str:
    lines: list[str] = []
    lines.append(f"# {_loc('Fortnite STW - Missing Recommended Trap Builds', lang)}")
    lines.append("")
    lines.append(f"_Language: {lang}_")
    lines.append(f"_Inventory snapshot: {inventory_file}_")
    lines.append("")

    if not missing:
        lines.append(_loc("All recommended trap builds are present.", lang))
        lines.append("")
        return "\n".join(lines)

    lines.append(f"{_loc('Missing trap builds', lang)}: {len(missing)}")
    lines.append("")

    current_trap_key = ""
    for trap, build in missing:
        trap_group_key = _normalized_trap_template(trap.trap_template)
        if trap_group_key != current_trap_key:
            current_trap_key = trap_group_key
            trap_slot = _resolve_trap_slot(trap, template_map, slot_lookup)
            trap_name = str(trap_slot.get("name", "")).strip() if isinstance(trap_slot, dict) else ""
            trap_icon = str(trap_slot.get("icon_url", "")).strip() if isinstance(trap_slot, dict) else ""
            lines.append(f"## {trap_name or _localized_trap_name(trap, lang)}")
            if trap_icon:
                lines.append(f'<img src="{_markdown_icon_src(trap_icon, output_dir)}" width="120">')
            lines.append("")
        lines.extend(_render_build_block(build, lang, asset_texts))

    return "\n".join(lines).rstrip() + "\n"


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _print_missing_summary(missing: list[tuple[TrapDefinition, BuildDefinition]], lang: str) -> None:
    if not missing:
        rprint(f"[green]{_loc('All recommended trap builds are present.', lang)}[/green]")
        return
    rprint(f"[yellow]{_loc('Missing trap builds', lang)}: {len(missing)}[/yellow]")
    for trap, build in missing:
        trap_name = _localized_trap_name(trap, lang)
        build_name = _build_description(build)
        rprint(f"  - {trap_name}, {build_name}")


def _validate_lang(lang: str) -> str:
    value = lang.lower().strip()
    if value not in AVAILABLE_LANGS:
        rprint(
            f"[red]ERROR: Language '{value}' is not available. Available: {', '.join(AVAILABLE_LANGS)}[/red]"
        )
        sys.exit(1)
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compare trap recommendations against normalized inventory and generate localized markdown outputs."
        )
    )
    parser.add_argument("--lang", default="en", help=f"Output language ({', '.join(AVAILABLE_LANGS)}). Default: en")
    args = parser.parse_args()

    model = _load_model(RECOMMENDATION_MODEL_FILE)
    asset_texts = _load_alteration_asset_texts()
    inventory: dict | None = None
    inventory_file: Path | None = None
    try:
        inventory, inventory_file = _load_latest_inventory()
    except FileNotFoundError:
        rprint("[yellow]No inventory data found. Only general trap recommendations will be generated.[/yellow]")

    lang = _validate_lang(args.lang)
    template_map, slot_lookup = _load_collection_book_context(lang)
    missing: list[tuple[TrapDefinition, BuildDefinition]] = []
    if inventory is not None:
        missing = find_missing_builds(inventory, model)

    general_md = _render_general_markdown(model, lang, asset_texts, template_map, slot_lookup, DEFAULT_OUTPUT_DIR)
    general_file = DEFAULT_OUTPUT_DIR / "trap_recommendations.md"
    _write_text(general_file, general_md)
    rprint(f"[green]Written:[/green] {general_file}")

    if inventory_file is not None:
        missing_md = _render_missing_markdown(
            model,
            missing,
            lang,
            inventory_file,
            asset_texts,
            template_map,
            slot_lookup,
            DEFAULT_OUTPUT_DIR,
        )
        missing_file = DEFAULT_OUTPUT_DIR / "trap_recommendations_missing.md"
        _write_text(missing_file, missing_md)
        rprint(f"[green]Written:[/green] {missing_file}")

    if inventory_file is not None:
        _print_missing_summary(missing, lang)


if __name__ == "__main__":
    main()
