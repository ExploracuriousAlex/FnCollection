"""
Import STW inventory into data/collection.json.

Source is Epic MCP (own account):
    1) authenticate
    2) resolve account id by display name
    3) query STW profiles and normalize item payload

- Replaces all "inv" entries completely.
- Replaces all "inv_details" entries completely.
- Replaces all "col" entries from Epic Collection Book profiles.
- Recomputes and writes "col_max" entries directly.
"""

import json
import os
import re
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from rich import print as rprint

import epic_api
import state
from data_loader import get_collection_book, lookup_item_definition_name

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# API starting_rarity → Collection Book slot rarity code
_API_RARITY: dict[str, str] = {
    "common": "C",
    "uncommon": "UC",
    "rare": "R",
    "epic": "VR",
    "legendary": "SR",
    "mythic": "SR",  # Mythic heroes sit in SR slots in the CB
}

_ROOT_DIR = Path(__file__).parent
_BACKUP_DIR = _ROOT_DIR / "backups"
_DEFAULT_DISPLAY_NAME = os.environ.get("EPIC_DISPLAY_NAME", "")
_MATERIAL_TOKENS: list[tuple[str, str]] = [
    ("brightcore", "Brightcore"),
    ("shadowshard", "Shadowshard"),
    ("malachite", "Malachite"),
    ("sunbeam", "Sunbeam"),
    ("obsidian", "Obsidian"),
    ("silver", "Silver"),
    ("copper", "Copper"),
]

_MAX_RULES_BY_RARITY: dict[str, tuple[int, int]] = {
    "C": (2, 20),
    "UC": (3, 30),
    "R": (4, 40),
    "VR": (5, 50),
    "SR": (5, 50),
}

_INVENTORY_PROFILE_IDS = ("campaign",)
_COLLECTION_PROFILE_IDS = ("collection_book_people0", "collection_book_schematics0")
_DEFAULT_RAW_DIR = _ROOT_DIR / "raw_data"

_SKIPPED_TEMPLATE_WHITELIST: set[str] = {
    "ammo_bulletsheavy",
    "ammo_bulletslight",
    "ammo_bulletsmedium",
    "ammo_energycell",
    "ammo_explosive",
    "ammo_shells",
    "ingredient_blastpowder",
    "ingredient_duct_tape",
}
_HALLOWEEN_PORTRAIT_TYPES = {"Husk", "Husky", "Pitcher", "Lobber", "Smasher", "Troll"}
_VALID_SURVIVOR_PORTRAIT_VARIANTS = {"M01", "M02", "M03", "F01", "F02", "F03"}
_LEADER_RARITY_CODE: dict[str, str] = {
    "uncommon": "C",
    "rare": "UC",
    "epic": "R",
    "legendary": "VR",
}
_SYNERGY_TO_TYPE: dict[str, str] = {
    "IsDoctor": "Doctor",
    "IsEngineer": "Engineer",
    "IsExplorer": "Explorer",
    "IsGadgeteer": "Gadgeteer",
    "IsInventor": "Inventor",
    "IsMartialArtist": "MartialArtist",
    "IsSoldier": "Soldier",
    "IsTrainer": "Trainer",
}

_DEFENDER_NON_CB_TEMPLATES: set[str] = {"jill"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize_filename(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip()) or "unknown"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _create_state_backup() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_file = _BACKUP_DIR / f"collection.backup_{timestamp}.json"
    state_file = _ROOT_DIR / "data" / "collection.json"
    if state_file.exists():
        backup_file.write_bytes(state_file.read_bytes())
    else:
        backup_file.write_text('{"inv":{},"col":{},"col_max":{},"inv_details":{},"col_details":{}}', encoding="utf-8")
    return backup_file


# ---------------------------------------------------------------------------
# Item attribute extraction
# ---------------------------------------------------------------------------


def _extract_material(obj: object) -> str | None:
    if isinstance(obj, str):
        text = obj.lower()
        for token, label in _MATERIAL_TOKENS:
            if token in text:
                return label
    elif isinstance(obj, dict):
        for key, value in obj.items():
            found = _extract_material(key) or _extract_material(value)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _extract_material(value)
            if found:
                return found
    return None


def _extract_material_from_template_id(template_id: str) -> str | None:
    token = template_id.lower()
    if not token.startswith("sid_"):
        return None
    tier_match = re.search(r"_t(\d+)$", token)
    if not tier_match:
        return None
    tier = int(tier_match.group(1))
    if tier == 1:
        return "Copper"
    if tier == 2:
        return "Silver"
    if tier == 3:
        return "Malachite"
    if "_ore_" in token:
        return "Obsidian" if tier == 4 else "Brightcore"
    if "_crystal_" in token:
        return "Shadowshard" if tier == 4 else "Sunbeam"
    return None


def _extract_power_level(item: dict) -> int | None:
    attrs = item.get("attributes", {})
    for container in (attrs, item):
        for key in ("level", "starting_level", "power_level", "powerLevel", "item_power_level", "pl"):
            value = container.get(key)
            if isinstance(value, (int, float)):
                return int(value)
            if isinstance(value, str) and value.isdigit():
                return int(value)
    return None


def _extract_item_tier(item: dict) -> int | None:
    template_id = item.get("templateId", "")
    if isinstance(template_id, str):
        m = re.search(r"_t(\d+)$", template_id.lower())
        if m:
            return int(m.group(1))
    attrs = item.get("attributes", {})
    if isinstance(attrs, dict):
        starting_tier = attrs.get("starting_tier")
        if isinstance(starting_tier, str):
            roman_map = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5}
            mapped = roman_map.get(starting_tier.strip().lower())
            if mapped:
                return mapped
            m = re.search(r"(\d+)", starting_tier)
            if m:
                return int(m.group(1))
        elif isinstance(starting_tier, (int, float)):
            return int(starting_tier)
    return None


def _extract_variant_detail(item: dict, include_material: bool = False) -> dict | None:
    pl = _extract_power_level(item)
    tier = _extract_item_tier(item)
    material = None
    if include_material:
        attrs = item.get("attributes", {})
        crafting_costs = attrs.get("crafting_costs") or item.get("crafting_costs")
        if crafting_costs:
            material = _extract_material(crafting_costs)
        if not material:
            template_id = item.get("templateId", "")
            if isinstance(template_id, str):
                material = _extract_material_from_template_id(template_id)

    if not material and pl is None and tier is None:
        return None
    detail: dict[str, object] = {"count": 1}
    if material:
        detail["material"] = material
    if pl is not None:
        detail["pl"] = pl
    if tier is not None:
        detail["tier"] = tier
    return detail


def _extract_rarity_code_from_slot_id(slot_id: str) -> str | None:
    m = re.search(r"\.(C|UC|R|VR|SR)\.", slot_id, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    m = re.match(r"worker_(c|uc|r|vr|sr)_", slot_id, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return None


def _extract_item_rarity_code(item: dict) -> str:
    attrs = item.get("attributes", {})
    raw_rarity = (
        attrs.get("starting_rarity") or item.get("starting_rarity") or item.get("rarity", "")
    ).lower()
    return _API_RARITY.get(raw_rarity, "")


# ---------------------------------------------------------------------------
# Profile extraction and normalization
# ---------------------------------------------------------------------------


def _extract_profile_items(profile_response: dict) -> dict[str, dict]:
    for change in profile_response.get("profileChanges", []):
        if not isinstance(change, dict):
            continue
        profile = change.get("profile")
        if not isinstance(profile, dict):
            continue
        items = profile.get("items")
        if isinstance(items, dict):
            return items
    return {}


def _normalize_template_id(template_id: str) -> str:
    return template_id.split(":", 1)[1] if ":" in template_id else template_id


def _normalize_epic_item(item: Mapping[str, object]) -> dict:
    template = item.get("templateId")
    attrs = item.get("attributes")
    normalized: dict[str, object] = {
        "templateId": _normalize_template_id(str(template)) if isinstance(template, str) else "",
        "attributes": attrs if isinstance(attrs, dict) else {},
    }
    for key in ("quantity", "starting_rarity", "rarity", "image_link"):
        value = item.get(key)
        if isinstance(value, (str, int, float, bool, dict, list)):
            normalized[key] = value
    return normalized


def _split_epic_items_for_import(epic_items: dict[str, dict]) -> dict[str, dict[str, dict]]:
    buckets: dict[str, dict[str, dict]] = {
        "heroes": {},
        "schematics": {},
        "survivors": {},
        "defenders": {},
    }
    for item_id, raw_item in epic_items.items():
        if not isinstance(raw_item, dict):
            continue
        raw_template = raw_item.get("templateId")
        if not isinstance(raw_template, str) or not raw_template:
            continue
        template_type = raw_template.split(":", 1)[0].lower()
        if template_type not in ("hero", "schematic", "defender", "worker"):
            continue
        normalized = _normalize_epic_item(raw_item)

        if template_type == "hero":
            buckets["heroes"][item_id] = normalized
        elif template_type == "schematic":
            buckets["schematics"][item_id] = normalized
        elif template_type == "defender":
            buckets["defenders"][item_id] = normalized
        elif template_type == "worker":
            buckets["survivors"][item_id] = normalized
    return buckets


# ---------------------------------------------------------------------------
# Collection Book mapping
# ---------------------------------------------------------------------------


def _build_name_map(data: dict) -> dict[str, list[tuple[str, str]]]:
    """Build name → [(rarity_code, slot_id)] mapping from the collection book."""
    name_map: dict[str, list[tuple[str, str]]] = {}
    for cat in data["categories"]:
        for page in cat["pages"]:
            for sec in page["sections"]:
                for slot in sec["slots"]:
                    n = slot["name"].lower().strip()
                    m = re.search(r"\.(C|UC|R|VR|SR)\.", slot["slot_id"], re.IGNORECASE)
                    code = m.group(1).upper() if m else ""
                    name_map.setdefault(n, []).append((code, slot["slot_id"]))
    return name_map


def _build_slot_display_name_map(data: dict) -> dict[str, str]:
    """Build slot_id -> display name mapping from the collection book."""
    slot_name_map: dict[str, str] = {}
    for cat in data["categories"]:
        for page in cat["pages"]:
            for sec in page["sections"]:
                for slot in sec["slots"]:
                    slot_id = slot.get("slot_id")
                    name = slot.get("name")
                    if isinstance(slot_id, str) and slot_id and isinstance(name, str) and name:
                        slot_name_map[slot_id] = name
    return slot_name_map


def _build_template_slot_map(data: dict) -> dict[str, list[str]]:
    """Build template-like token -> slot id mapping."""
    template_map: dict[str, list[str]] = {}
    for cat in data["categories"]:
        for page in cat["pages"]:
            for sec in page["sections"]:
                for slot in sec["slots"]:
                    slot_id = slot.get("slot_id")
                    if not isinstance(slot_id, str):
                        continue
                    token = slot_id.lower().replace(".", "_")
                    template_map.setdefault(token, []).append(slot_id)
    return template_map


def _normalize_template_token(token: str) -> str:
    """Normalize tier suffix for slot matching (T02..T05 → T01)."""
    return re.sub(r"_t\d+$", "_t01", token.lower().strip())


def _lookup_template_slot_ids(template_map: dict[str, list[str]], template_token: str) -> list[str]:
    exact = template_map.get(template_token, [])
    if exact:
        return exact
    normalized = _normalize_template_token(template_token)
    if normalized != template_token:
        hit = template_map.get(normalized, [])
        if hit:
            return hit
    # Schematic slots often use Ore while account templates can be Crystal variants
    material_swapped = normalized
    if "_crystal_" in material_swapped:
        material_swapped = material_swapped.replace("_crystal_", "_ore_")
    elif "_ore_" in material_swapped:
        material_swapped = material_swapped.replace("_ore_", "_crystal_")
    if material_swapped != normalized:
        hit = template_map.get(material_swapped, [])
        if hit:
            return hit
    return []


def _lookup_template_slot_ids_for_display(template_map: dict[str, list[str]], template_token: str) -> list[str]:
    """Resolve slot ids for display names; allows prefix expansion."""
    exact = _lookup_template_slot_ids(template_map, template_token)
    if exact:
        return exact

    tokens_to_try = [template_token]
    normalized = _normalize_template_token(template_token)
    if normalized != template_token:
        tokens_to_try.append(normalized)

    seen: set[str] = set()
    expanded: list[str] = []
    for token in tokens_to_try:
        prefix = token + "_"
        for key, slot_ids in template_map.items():
            if not key.startswith(prefix):
                continue
            for slot_id in slot_ids:
                if slot_id not in seen:
                    seen.add(slot_id)
                    expanded.append(slot_id)
    if expanded:
        return expanded

    # Display-only rarity-agnostic fallback
    for token in tokens_to_try:
        rarity_match = re.search(r"_(c|uc|r|vr|sr)_", token)
        if not rarity_match:
            continue
        left = token[: rarity_match.start()]
        right = token[rarity_match.end() :]
        if not left or not right:
            continue
        right_suffix = "_" + right
        left_prefix = left + "_"
        for key, slot_ids in template_map.items():
            if not key.startswith(left_prefix) or not key.endswith(right_suffix):
                continue
            for slot_id in slot_ids:
                if slot_id not in seen:
                    seen.add(slot_id)
                    expanded.append(slot_id)
    return expanded


def _slot_ids_to_candidates(slot_ids: list[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for slot_id in slot_ids:
        m = re.search(r"\.(C|UC|R|VR|SR)\.", slot_id, re.IGNORECASE)
        code = m.group(1).upper() if m else ""
        out.append((code, slot_id))
    return out


def _lookup_collection_book_candidates(
    template_token: str,
    name_map: dict[str, list[tuple[str, str]]],
    template_map: dict[str, list[str]],
) -> list[tuple[str, str]]:
    candidates = name_map.get(template_token, [])
    if not candidates:
        candidates = _slot_ids_to_candidates(_lookup_template_slot_ids(template_map, template_token))
    return candidates


# ---------------------------------------------------------------------------
# Slot resolution for specific item types
# ---------------------------------------------------------------------------


def _resolve_halloween_survivor_slot_id(item: dict) -> str | None:
    attrs = item.get("attributes", {})
    portrait_raw = attrs.get("portrait", "")
    if ":IconDef-WorkerPortrait-" not in portrait_raw:
        return None
    hw_type = portrait_raw.rsplit("-", 1)[-1]
    if hw_type not in _HALLOWEEN_PORTRAIT_TYPES:
        return None
    personality_raw = attrs.get("personality", "")
    personality_key = personality_raw.rsplit(".", 1)[-1]
    if not personality_key or not personality_key.startswith("Is"):
        return None
    set_bonus_raw = attrs.get("set_bonus", "")
    set_bonus_tag = set_bonus_raw.rsplit(".", 1)[-1]
    if not set_bonus_tag or not set_bonus_tag.startswith("Is"):
        return None
    raw_rarity = (
        attrs.get("starting_rarity") or item.get("starting_rarity", "") or item.get("rarity", "")
    ).lower()
    rarity_code = _API_RARITY.get(raw_rarity, "")
    if not rarity_code:
        return None
    return f"Worker.Halloween.{hw_type}.{rarity_code}.T01.{personality_key}.{set_bonus_tag}"


def _resolve_standard_survivor_slot_id(item: dict) -> tuple[str | None, str | None]:
    attrs = item.get("attributes", {})
    synergy_raw = attrs.get("managerSynergy", "")
    if isinstance(synergy_raw, str) and synergy_raw.strip():
        return None, "manager"
    portrait_raw = attrs.get("portrait", "")
    if ":IconDef-WorkerPortrait-" not in portrait_raw:
        return None, "no_worker_portrait"
    variant = portrait_raw.rsplit("-", 1)[-1]
    if variant in _HALLOWEEN_PORTRAIT_TYPES:
        return None, "halloween"
    if variant not in _VALID_SURVIVOR_PORTRAIT_VARIANTS:
        return None, "non_standard_variant"
    personality_raw = attrs.get("personality", "")
    personality = personality_raw.rsplit(".", 1)[-1]
    if personality.startswith("Is"):
        personality = personality[2:]
    if not personality:
        return None, "missing_personality"
    set_bonus_raw = attrs.get("set_bonus") or ""
    set_bonus_tag = set_bonus_raw.rsplit(".", 1)[-1]
    if not set_bonus_tag or not set_bonus_tag.startswith("Is"):
        return None, "missing_set_bonus"
    raw_rarity = (attrs.get("starting_rarity") or item.get("starting_rarity", "")).lower()
    rarity_code = _API_RARITY.get(raw_rarity, "")
    if not rarity_code:
        return None, "unknown_rarity"
    return f"worker_{rarity_code}_{personality}.{variant}.{set_bonus_tag}", None


def _resolve_leader_slot_ids(
    item: dict,
    name_map: dict[str, list[tuple[str, str]]],
    template_map: dict[str, list[str]],
) -> tuple[list[str] | None, str | None]:
    attrs = item.get("attributes", {})
    synergy_raw = attrs.get("managerSynergy", "")
    if not isinstance(synergy_raw, str) or not synergy_raw.strip():
        return None, None
    synergy_key = synergy_raw.rsplit(".", 1)[-1]
    manager_type = _SYNERGY_TO_TYPE.get(synergy_key)
    if not manager_type:
        return [], "unknown_manager_type"
    raw_rarity = (
        attrs.get("starting_rarity") or item.get("starting_rarity", "") or item.get("rarity", "")
    ).lower()
    if raw_rarity == "mythic":
        template_token = str(item.get("templateId", "")).lower().strip()
        candidates = _lookup_collection_book_candidates(template_token, name_map, template_map)
        if not candidates:
            return [], "no_collection_book_match"
        return [slot_id for _, slot_id in candidates], None
    code = _LEADER_RARITY_CODE.get(raw_rarity)
    if not code:
        return [], "unknown_rarity"
    gender_raw = attrs.get("gender", "1")
    gender_key = "F" if gender_raw == "2" else "M"
    personality_raw = attrs.get("personality", "")
    personality = personality_raw.rsplit(".", 1)[-1].removeprefix("Is")
    if not personality:
        return [], "missing_personality"
    return [f"Manager{manager_type}.{code}.T01.{gender_key}.{personality}"], None


# ---------------------------------------------------------------------------
# Detail bucket helpers
# ---------------------------------------------------------------------------


def _add_detail_to_bucket(
    buckets: dict[str, dict[tuple[str, int, int], int]],
    slot_id: str,
    detail: dict | None,
    amount: int = 1,
) -> None:
    if not detail:
        return
    material = detail.get("material") if isinstance(detail.get("material"), str) else ""
    pl = int(detail["pl"]) if isinstance(detail.get("pl"), int) else -1
    tier = int(detail["tier"]) if isinstance(detail.get("tier"), int) else -1
    key = (material, pl, tier)
    slot_bucket = buckets.setdefault(slot_id, {})
    slot_bucket[key] = slot_bucket.get(key, 0) + amount


def _finalize_detail_buckets(
    buckets: dict[str, dict[tuple[str, int, int], int]],
) -> dict[str, list[dict[str, object]]]:
    result: dict[str, list[dict[str, object]]] = {}
    for slot_id, bucket in buckets.items():
        details: list[dict[str, object]] = []
        for (material, pl, tier), count in sorted(bucket.items(), key=lambda x: (x[0][1], x[0][2], x[0][0])):
            d: dict[str, object] = {"count": count}
            if material:
                d["material"] = material
            if pl >= 0:
                d["pl"] = pl
            if tier >= 0:
                d["tier"] = tier
            details.append(d)
        if details:
            result[slot_id] = details
    return result


# ---------------------------------------------------------------------------
# Diagnostic printing
# ---------------------------------------------------------------------------


def _append_skipped_template(skipped_items: list[str], template_id: object) -> None:
    if not isinstance(template_id, str):
        return
    token = template_id.lower().strip()
    if not token or token in _SKIPPED_TEMPLATE_WHITELIST:
        return
    skipped_items.append(token)


def _resolve_template_display_names(
    template_id: str,
    template_map_en: dict[str, list[str]],
    slot_name_map_en: dict[str, str],
    template_map_de: dict[str, list[str]],
    slot_name_map_de: dict[str, str],
) -> tuple[str, str, str]:
    token = template_id.lower().strip()

    def _names_for_slots(slot_ids: list[str], name_map: dict[str, str]) -> str:
        names = []
        seen: set[str] = set()
        for slot_id in slot_ids:
            name = name_map.get(slot_id, "").strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        return " / ".join(names)

    slot_ids_en = _lookup_template_slot_ids_for_display(template_map_en, token)
    slot_ids_de = _lookup_template_slot_ids_for_display(template_map_de, token)
    en_name = _names_for_slots(slot_ids_en, slot_name_map_en)
    de_name = _names_for_slots(slot_ids_de, slot_name_map_de)
    if en_name or de_name:
        return en_name or "-", de_name or "-", "collection_book"

    fallback = lookup_item_definition_name(token)
    if fallback:
        return fallback.get("en", "-"), fallback.get("de", "-"), "not_in_collection_book"
    return "-", "-", "unresolved"


def _print_skipped_items_with_names(skipped_items: list[str], heading: str | None = "Skipped items:") -> None:
    unique_skipped = sorted(set(skipped_items))
    if not unique_skipped:
        return

    data_en = get_collection_book("en")
    data_de = get_collection_book("de")
    template_map_en = _build_template_slot_map(data_en)
    slot_name_map_en = _build_slot_display_name_map(data_en)
    template_map_de = _build_template_slot_map(data_de)
    slot_name_map_de = _build_slot_display_name_map(data_de)

    if heading:
        rprint(f"  {heading}")
    for template_id in unique_skipped:
        en_name, de_name, _ = _resolve_template_display_names(
            template_id, template_map_en, slot_name_map_en, template_map_de, slot_name_map_de,
        )
        rprint(f"    [dim]{template_id}[/dim] | EN: {en_name} | DE: {de_name}")


def _print_skipped_survivor_details(
    skipped_survivors: list[tuple[str, str]],
    heading: str | None = "Skipped survivor details:",
) -> None:
    if not skipped_survivors:
        return

    reason_labels = {
        "no_worker_portrait": "no worker portrait",
        "non_standard_variant": "non-standard portrait variant",
        "missing_personality": "missing personality",
        "missing_set_bonus": "missing set bonus",
        "unknown_rarity": "unknown rarity",
    }

    if heading:
        rprint(f"  {heading}")

    data_en = get_collection_book("en")
    data_de = get_collection_book("de")
    template_map_en = _build_template_slot_map(data_en)
    slot_name_map_en = _build_slot_display_name_map(data_en)
    template_map_de = _build_template_slot_map(data_de)
    slot_name_map_de = _build_slot_display_name_map(data_de)

    for template_id, reason in skipped_survivors:
        names = lookup_item_definition_name(template_id)
        if names:
            en_name = names.get("en", "-")
            de_name = names.get("de", "-")
        else:
            en_name, de_name, _ = _resolve_template_display_names(
                template_id, template_map_en, slot_name_map_en, template_map_de, slot_name_map_de,
            )
        reason_text = reason_labels.get(reason, reason)
        rprint(f"    [dim]{template_id}[/dim] | EN: {en_name} | DE: {de_name} | reason: {reason_text}")


# ---------------------------------------------------------------------------
# Import logic
# ---------------------------------------------------------------------------


def import_inventory(resp: dict, source_label: str) -> None:
    rprint("[dim]Loading collection book …[/dim]")
    data = get_collection_book("en")
    name_map = _build_name_map(data)
    template_map = _build_template_slot_map(data)

    rprint(f"[dim]Loading {source_label} …[/dim]")

    inv: dict[str, int] = {}
    inv_detail_buckets: dict[str, dict[tuple[str, int, int], int]] = {}

    def _add_inv(slot_id: str, amount: int = 1, detail: dict | None = None) -> None:
        inv[slot_id] = inv.get(slot_id, 0) + amount
        _add_detail_to_bucket(inv_detail_buckets, slot_id, detail, amount)

    skipped_no_cb: list[str] = []
    ambiguous: list[tuple[str, str, list[str]]] = []
    skipped_survivors = 0
    skipped_survivor_details: list[tuple[str, str]] = []

    # Halloween event survivors
    for uuid, item in resp.get("survivors", {}).items():
        detail = _extract_variant_detail(item)
        slot_id = _resolve_halloween_survivor_slot_id(item)
        if slot_id:
            _add_inv(slot_id, detail=detail)

    # Regular survivors
    for uuid, item in resp.get("survivors", {}).items():
        detail = _extract_variant_detail(item)
        template_token = str(item.get("templateId", "") or uuid).lower().strip()

        slot_id, reason = _resolve_standard_survivor_slot_id(item)
        if slot_id:
            _add_inv(slot_id, detail=detail)
            continue
        if reason == "manager":
            continue

        candidates = _lookup_collection_book_candidates(template_token, name_map, template_map)
        if reason == "no_worker_portrait":
            if not candidates:
                _append_skipped_template(skipped_no_cb, item.get("templateId"))
            else:
                skipped_survivors += 1
                skipped_survivor_details.append((template_token, "no_worker_portrait"))
            continue
        if reason == "halloween":
            continue
        if reason in ("non_standard_variant", "missing_personality", "missing_set_bonus", "unknown_rarity"):
            skipped_survivors += 1
            skipped_survivor_details.append((template_token, reason))
            continue

    # Leaders
    skipped_leads = 0
    for uuid, item in resp.get("survivors", {}).items():
        detail = _extract_variant_detail(item)
        slot_ids, reason = _resolve_leader_slot_ids(item, name_map, template_map)
        if slot_ids is None:
            continue
        if not slot_ids:
            if reason == "no_collection_book_match":
                _append_skipped_template(skipped_no_cb, item.get("templateId"))
            else:
                skipped_leads += 1
            continue
        for slot_id in slot_ids:
            _add_inv(slot_id, detail=detail)

    # Defenders
    for uuid, item in resp.get("defenders", {}).items():
        detail = _extract_variant_detail(item)
        n = str(item.get("templateId", "")).lower().strip()
        if n in _DEFENDER_NON_CB_TEMPLATES:
            _append_skipped_template(skipped_no_cb, item.get("templateId"))
            continue
        candidates = _lookup_collection_book_candidates(n, name_map, template_map)
        if not candidates:
            _append_skipped_template(skipped_no_cb, item.get("templateId"))
            continue
        rarity_code = _extract_item_rarity_code(item)
        filtered = [sid for (c, sid) in candidates if c == rarity_code] if rarity_code else [sid for _, sid in candidates]
        if not filtered:
            slots = [sid for _, sid in candidates]
            ambiguous.append((str(item.get("templateId", "")), str(item.get("rarity", "")), slots))
            continue
        for sid in filtered:
            _add_inv(sid, detail=detail)

    # Heroes and schematics
    for section in ("heroes", "schematics"):
        for uuid, item in resp.get(section, {}).items():
            n = item.get("templateId", "").lower().strip()
            raw_rarity = (
                item.get("attributes", {}).get("starting_rarity")
                or item.get("starting_rarity")
                or item.get("rarity", "")
            ).lower()
            code = _API_RARITY.get(raw_rarity, "")
            candidates = _lookup_collection_book_candidates(n, name_map, template_map)
            if not candidates:
                _append_skipped_template(skipped_no_cb, item.get("templateId"))
                continue
            filtered = [sid for (c, sid) in candidates if c == code]
            detail = _extract_variant_detail(item, include_material=(section == "schematics"))
            if len(filtered) == 1:
                _add_inv(filtered[0], detail=detail)
            elif len(filtered) == 0:
                slots = [sid for _, sid in candidates]
                ambiguous.append((item["templateId"], raw_rarity, slots))
            else:
                for sid in filtered:
                    _add_inv(sid, detail=detail)

    inv_details = _finalize_detail_buckets(inv_detail_buckets)

    current = state.load()
    current["inv"] = inv
    current["inv_details"] = inv_details
    state.save(current)

    rprint("\n[green]Done.[/green]")
    rprint(f"  Inventory entries imported: [cyan]{len(inv)}[/cyan]")
    if skipped_no_cb:
        rprint(f"  [yellow]Skipped (not in CB): {len(set(skipped_no_cb))}[/yellow]")
        _print_skipped_items_with_names(skipped_no_cb, heading=None)
    if skipped_survivor_details:
        rprint(f"  [yellow]Survivors skipped (event/no set-bonus): {skipped_survivors}[/yellow]")
        _print_skipped_survivor_details(skipped_survivor_details, heading=None)
    if ambiguous:
        rprint(f"  [yellow]Ambiguous (rarity unknown): {len(ambiguous)}[/yellow]")
        for name, rarity, slots in ambiguous:
            rprint(f"    [dim]{name!r} ({rarity or 'no rarity'}) → skipped (ambiguous slots: {slots})[/dim]")


def import_collection_book(resp: dict, source_label: str) -> None:
    """Import collection book completion flags from collection_book_* profiles."""
    rprint("[dim]Loading collection book …[/dim]")
    data = get_collection_book("en")
    name_map = _build_name_map(data)
    template_map = _build_template_slot_map(data)

    rprint(f"[dim]Loading collection entries from {source_label} …[/dim]")

    marked: set[str] = set()
    col_detail_buckets: dict[str, dict[tuple[str, int, int], int]] = {}
    skipped_no_cb: list[str] = []
    ambiguous: list[tuple[str, str, list[str]]] = []

    for section in ("heroes", "schematics", "survivors", "defenders"):
        for _, item in resp.get(section, {}).items():
            template_id = item.get("templateId", "")
            n = template_id.lower().strip()
            attrs = item.get("attributes", {})
            raw_rarity = (
                attrs.get("starting_rarity") or item.get("starting_rarity") or item.get("rarity", "")
            ).lower()
            code = _API_RARITY.get(raw_rarity, "")

            if section == "survivors":
                detail = _extract_variant_detail(item)
                slot_ids, reason = _resolve_leader_slot_ids(item, name_map, template_map)
                if slot_ids is not None:
                    if not slot_ids:
                        if reason == "no_collection_book_match":
                            _append_skipped_template(skipped_no_cb, template_id)
                        continue
                    for slot_id in slot_ids:
                        marked.add(slot_id)
                        _add_detail_to_bucket(col_detail_buckets, slot_id, detail)
                    continue
                slot_id = _resolve_halloween_survivor_slot_id(item)
                if slot_id:
                    marked.add(slot_id)
                    _add_detail_to_bucket(col_detail_buckets, slot_id, detail)
                    continue
                slot_id, reason = _resolve_standard_survivor_slot_id(item)
                if slot_id:
                    marked.add(slot_id)
                    _add_detail_to_bucket(col_detail_buckets, slot_id, detail)
                    continue

            if section == "defenders":
                if n in _DEFENDER_NON_CB_TEMPLATES:
                    _append_skipped_template(skipped_no_cb, template_id)
                    continue

            candidates = _lookup_collection_book_candidates(n, name_map, template_map)
            if not candidates:
                if isinstance(template_id, str) and template_id:
                    _append_skipped_template(skipped_no_cb, template_id)
                continue
            filtered = [sid for c, sid in candidates if c == code] if code else [sid for _, sid in candidates]
            if not filtered:
                ambiguous.append((str(template_id), raw_rarity, [sid for _, sid in candidates]))
                continue
            detail = _extract_variant_detail(item, include_material=(section == "schematics"))
            for slot_id in filtered:
                marked.add(slot_id)
                _add_detail_to_bucket(col_detail_buckets, slot_id, detail)

    current = state.load()
    current["col"] = {slot_id: True for slot_id in marked}
    current["col_details"] = _finalize_detail_buckets(col_detail_buckets)
    state.save(current)

    rprint("\n[green]Collection import done.[/green]")
    rprint(f"  Collection entries imported: [cyan]{len(marked)}[/cyan]")
    if skipped_no_cb:
        rprint(f"  [yellow]Skipped (not in CB): {len(set(skipped_no_cb))}[/yellow]")
        _print_skipped_items_with_names(skipped_no_cb, heading=None)
    if ambiguous:
        rprint(f"  [yellow]Ambiguous (rarity unknown): {len(ambiguous)}[/yellow]")
        for name, rarity, slots in ambiguous:
            rprint(f"    [dim]{name!r} ({rarity or 'no rarity'}) → skipped (ambiguous slots: {slots})[/dim]")


# ---------------------------------------------------------------------------
# col_max computation
# ---------------------------------------------------------------------------


def _proposed_col_max_from_state(current: dict) -> set[str]:
    col_flags = current.get("col", {})
    if not isinstance(col_flags, dict):
        return set()
    col_details = current.get("col_details", {})
    if isinstance(col_details, dict) and col_details:
        detail_source = col_details
    else:
        detail_source = current.get("inv_details", {})
    if not isinstance(detail_source, dict):
        return set()

    proposed: set[str] = set()
    for slot_id, variants in detail_source.items():
        if not isinstance(slot_id, str) or slot_id not in col_flags:
            continue
        if not isinstance(variants, list):
            continue
        rarity_code = _extract_rarity_code_from_slot_id(slot_id)
        required = _MAX_RULES_BY_RARITY.get(rarity_code or "")
        if not required:
            continue
        required_tier, required_level = required
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            pl = variant.get("pl")
            tier = variant.get("tier")
            if (
                isinstance(pl, int) and isinstance(tier, int)
                and pl >= required_level and tier >= required_tier
            ):
                proposed.add(slot_id)
                break
    return proposed


def apply_col_max_from_state() -> None:
    current = state.load()
    proposed = _proposed_col_max_from_state(current)
    current["col_max"] = {slot_id: True for slot_id in sorted(proposed)}
    state.save(current)


# ---------------------------------------------------------------------------
# Epic API fetch
# ---------------------------------------------------------------------------


def _fetch_epic_inventory(
    display_name: str,
    access_token: str,
    own_account_only: bool,
) -> tuple[dict, dict, str, str, str, dict[str, dict]]:
    verify = epic_api.verify_token(access_token)
    token_account_id = verify.get("account_id") or verify.get("accountId")
    if not isinstance(token_account_id, str) or not token_account_id:
        raise ValueError("OAuth verify response did not contain account_id")

    account = epic_api.lookup_account(display_name, access_token)
    target_account_id = account.get("id")
    resolved_name = account.get("displayName", display_name)
    if not isinstance(target_account_id, str) or not target_account_id:
        raise ValueError(f"Display name '{display_name}' could not be resolved to an account id")

    if own_account_only and target_account_id != token_account_id:
        raise ValueError(
            "Resolved account does not match authenticated account. "
            "Use credentials for the same account as --display-name."
        )

    profile_responses: dict[str, dict] = {}
    merged_items: dict[str, dict] = {}
    for profile_id in _INVENTORY_PROFILE_IDS:
        response = epic_api.query_profile(target_account_id, profile_id, access_token)
        profile_responses[profile_id] = response
        merged_items.update(_extract_profile_items(response))

    merged_collection_items: dict[str, dict] = {}
    for profile_id in _COLLECTION_PROFILE_IDS:
        response = epic_api.query_profile(target_account_id, profile_id, access_token)
        profile_responses[profile_id] = response
        merged_collection_items.update(_extract_profile_items(response))

    normalized_payload = _split_epic_items_for_import(merged_items)
    normalized_collection_payload = _split_epic_items_for_import(merged_collection_items)
    return (
        normalized_payload,
        normalized_collection_payload,
        f"Epic MCP ({resolved_name} / {target_account_id})",
        str(resolved_name),
        target_account_id,
        profile_responses,
    )


# ---------------------------------------------------------------------------
# Raw snapshots
# ---------------------------------------------------------------------------


def _save_raw_snapshot(
    base_dir: Path,
    *,
    display_name: str,
    account_id: str,
    source_label: str,
    profile_responses: dict[str, dict],
) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = base_dir / f"{timestamp}_{_sanitize_filename(display_name)}_{_sanitize_filename(account_id)}"
    run_dir.mkdir(parents=True, exist_ok=True)

    _write_json(
        run_dir / "meta.json",
        {
            "timestamp": timestamp,
            "display_name": display_name,
            "account_id": account_id,
            "source": source_label,
            "profiles": sorted(profile_responses.keys()),
        },
    )
    for profile_id, payload in sorted(profile_responses.items()):
        _write_json(run_dir / f"profile_{profile_id}.json", payload)
    return run_dir


def _find_latest_raw_snapshot(base_dir: Path) -> Path | None:
    if not base_dir.exists() or not base_dir.is_dir():
        return None
    candidates = [
        p for p in base_dir.iterdir()
        if p.is_dir() and (p / "profile_campaign.json").exists()
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: p.name, reverse=True)[0]


def _load_last_raw_payload(base_dir: Path) -> tuple[dict, dict, str]:
    """Load most recent raw snapshot and re-derive normalized payloads from profiles."""
    latest = _find_latest_raw_snapshot(base_dir)
    if latest is None:
        raise FileNotFoundError("No raw_data snapshot directory found")

    merged_items: dict[str, dict] = {}
    for profile_id in _INVENTORY_PROFILE_IDS:
        profile_file = latest / f"profile_{profile_id}.json"
        if profile_file.exists():
            with open(profile_file, encoding="utf-8") as f:
                response = json.load(f)
            if isinstance(response, dict):
                merged_items.update(_extract_profile_items(response))

    merged_collection_items: dict[str, dict] = {}
    for profile_id in _COLLECTION_PROFILE_IDS:
        profile_file = latest / f"profile_{profile_id}.json"
        if profile_file.exists():
            with open(profile_file, encoding="utf-8") as f:
                response = json.load(f)
            if isinstance(response, dict):
                merged_collection_items.update(_extract_profile_items(response))

    if not merged_items:
        raise FileNotFoundError(f"No profile data found in {latest}")

    inv_payload = _split_epic_items_for_import(merged_items)
    col_payload = _split_epic_items_for_import(merged_collection_items)
    return inv_payload, col_payload, f"raw_data fallback ({latest.name})"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    backup_file = _create_state_backup()
    rprint(f"[dim]Backup created: [cyan]{backup_file}[/cyan][/dim]")

    refresh_token = os.environ.get("EPIC_REFRESH_TOKEN", "").strip()

    if not refresh_token:
        rprint("[yellow]Notice: EPIC_REFRESH_TOKEN is not set. Falling back to latest raw_data snapshot.[/yellow]")
        try:
            profile, collection_profile, source_label = _load_last_raw_payload(_DEFAULT_RAW_DIR)
        except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError) as exc:
            rprint(f"[red]Error: No usable raw_data snapshot found ({exc}).[/red]", file=sys.stderr)
            rprint("[red]Please run epic_login.ps1 first and then run get_data.py again.[/red]", file=sys.stderr)
            sys.exit(1)

        import_inventory(profile, source_label)
        import_collection_book(collection_profile, source_label)
        apply_col_max_from_state()
        return

    try:
        access_token = epic_api.get_access_token(refresh_token)

        profile, collection_profile, source_label, resolved_name, target_account_id, profile_responses = _fetch_epic_inventory(
            _DEFAULT_DISPLAY_NAME, access_token, own_account_only=True,
        )
    except Exception as exc:
        rprint(f"[red]Error: Could not read Epic API ({exc}).[/red]", file=sys.stderr)
        rprint("[yellow]Falling back to latest raw_data snapshot.[/yellow]")
        try:
            profile, collection_profile, source_label = _load_last_raw_payload(_DEFAULT_RAW_DIR)
        except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError) as fallback_exc:
            rprint(f"[red]Error: No usable raw_data snapshot found ({fallback_exc}).[/red]", file=sys.stderr)
            sys.exit(1)

        import_inventory(profile, source_label)
        import_collection_book(collection_profile, source_label)
        apply_col_max_from_state()
        return

    raw_dir = _save_raw_snapshot(
        _DEFAULT_RAW_DIR,
        display_name=resolved_name,
        account_id=target_account_id,
        source_label=source_label,
        profile_responses=profile_responses,
    )
    _write_json(raw_dir / "normalized_inventory.json", profile)
    rprint(f"[green]Raw snapshot saved: [cyan]{raw_dir}[/cyan][/green]")

    import_inventory(profile, source_label)
    import_collection_book(collection_profile, source_label)
    apply_col_max_from_state()


if __name__ == "__main__":
    main()
