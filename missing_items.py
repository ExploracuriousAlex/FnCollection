import argparse
import json
import os
import re
import sys
from functools import lru_cache
from datetime import datetime
from pathlib import Path

from rich import print as rprint

import epic_api
import state as state_mod
from data_loader import AVAILABLE_LANGS, _loc, get_collection_book, lookup_item_definition_name

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
BACKUP_DIR = BASE_DIR / "backups"
RAW_DIR = BASE_DIR / "raw_data"
GAME_GROWTH_BOUNDS_FILE = (
    BASE_DIR
    / "FortniteGame"
    / "Plugins"
    / "GameFeatures"
    / "SaveTheWorld"
    / "Content"
    / "Balance"
    / "Datatables"
    / "GameDifficultyGrowthBounds.json"
)
MISSIONGEN_DIR = BASE_DIR / "FortniteGame" / "Plugins" / "GameFeatures" / "SaveTheWorld" / "Content" / "World" / "MissionGens"
DEFAULT_OUTPUT_FILE = BASE_DIR / "missing_items.txt"

# Resolved at startup via --lang argument (default: en)
OUTPUT_LANG: str = "en"

# Maps EN theater/zone name → localized name (populated from world_info)
_THEATER_LOC_MAP: dict[str, str] = {}

RARITY_LABELS = {
    "rarity-c": "Common",
    "rarity-uc": "Uncommon",
    "rarity-r": "Rare",
    "rarity-vr": "Epic",
    "rarity-sr": "Legendary",
    "rarity-myt": "Mythic",
}


def _sanitize_filename(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip())
    return cleaned or "unknown"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _save_raw_snapshot(
    *,
    payload: dict,
    available: dict[tuple[str, str], list[tuple[str, str, str, str]]],
    source_label: str = "epic-world-info",
) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = RAW_DIR / f"{timestamp}_{_sanitize_filename(source_label)}"
    run_dir.mkdir(parents=True, exist_ok=True)

    _write_json(
        run_dir / "meta.json",
        {
            "timestamp": timestamp,
            "source": source_label,
            "payload_keys": sorted(payload.keys()) if isinstance(payload, dict) else [],
            "available_entries": len(available),
        },
    )
    _write_json(run_dir / "world_info.json", payload)
    _write_json(
        run_dir / "available_today.json",
        [
            {
                "name": name,
                "rarity": rarity,
                "contexts": [
                    {"zone": zone, "mission": mission, "pl": pl, "missionAlertGuid": mission_alert_guid}
                    for zone, mission, pl, mission_alert_guid in sorted(contexts)
                ],
            }
            for (name, rarity), contexts in sorted(
                available.items(), key=lambda item: (item[0][0].casefold(), item[0][1].casefold())
            )
        ],
    )
    return run_dir


def _parse_available_today_payload(payload: object) -> dict[tuple[str, str], list[tuple[str, str, str, str]]]:
    parsed: dict[tuple[str, str], list[tuple[str, str, str, str]]] = {}
    if not isinstance(payload, list):
        return {}

    for entry in payload:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        rarity = str(entry.get("rarity", "")).strip()
        if not name or not rarity:
            continue
        key = (_normalize_name(name), _normalize_rarity(rarity))

        contexts_raw = entry.get("contexts", [])
        if not isinstance(contexts_raw, list):
            contexts_raw = []

        for ctx in contexts_raw:
            if not isinstance(ctx, dict):
                continue
            zone = str(ctx.get("zone", ""))
            mission = str(ctx.get("mission", ""))
            pl = str(ctx.get("pl", ""))
            mission_alert_guid = str(ctx.get("missionAlertGuid", ""))
            parsed.setdefault(key, []).append((zone, mission, pl, mission_alert_guid))

    return _sort_available_contexts(parsed)


def _find_latest_raw_snapshot(base_dir: Path, required_file: str) -> Path | None:
    """Find the most recent subdirectory that actually contains *required_file*."""
    if not base_dir.exists() or not base_dir.is_dir():
        return None
    candidates = [
        p for p in base_dir.iterdir()
        if p.is_dir() and (p / required_file).exists()
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: p.name, reverse=True)[0]


def _load_latest_available_today_snapshot() -> tuple[dict[tuple[str, str], list[tuple[str, str, str, str]]], Path | None]:
    latest_dir = _find_latest_raw_snapshot(RAW_DIR, "available_today.json")
    if latest_dir is None:
        return {}, None

    latest_file = latest_dir / "available_today.json"
    try:
        with open(latest_file, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}, None

    # Populate theater loc map from world_info in same snapshot dir.
    world_info_file = latest_dir / "world_info.json"
    if world_info_file.exists():
        try:
            with open(world_info_file, encoding="utf-8") as f:
                _populate_theater_loc_map(json.load(f))
        except (OSError, json.JSONDecodeError):
            pass

    return _parse_available_today_payload(payload), latest_file




def _is_weapon_slot(slot_id: str, category_id: str, page_id: str) -> bool:
    if not slot_id.startswith("SID."):
        return False
    # Exclude trap schematics (wall/ceiling/floor pages and trap category).
    if category_id == "Core_Schematics_Traps" or page_id.startswith("pageTraps_"):
        return False
    trap_prefixes = ("SID.Wall.", "SID.Ceiling.", "SID.Floor.")
    return not slot_id.startswith(trap_prefixes)


def _slot_group(slot_id: str, page_id: str, category_id: str) -> str | None:
    if page_id == "pagePeople_UniqueLeads":
        return "mythic_leads"
    if slot_id.startswith("HID."):
        return "heroes"
    if _is_weapon_slot(slot_id, category_id, page_id):
        return "weapons"
    return None


def _passes_filter(in_collection: bool, in_inventory: bool, rarity_css: str) -> bool:
    is_legendary_or_mythic = rarity_css in {"rarity-sr", "rarity-myt"}
    condition_1 = not in_collection
    condition_2 = is_legendary_or_mythic and ((not in_inventory) or (not in_collection))
    return condition_1 or condition_2


def _missing_flags(in_collection: bool, in_inventory: bool) -> str:
    flags = ""
    if not in_inventory:
        flags += "I"
    if not in_collection:
        flags += "C"
    return flags


def _normalize_name(name: str) -> str:
    return " ".join(name.strip().split()).casefold()


def _normalize_rarity(rarity: str) -> str:
    return rarity.strip().casefold()


def _find_today_contexts(
    *,
    name_en: str,
    rarity: str,
    group: str,
    available_today: dict[tuple[str, str], list[tuple[str, str, str, str]]],
) -> list[tuple[str, str, str, str]]:
    key_today = (_normalize_name(name_en), _normalize_rarity(rarity or ""))
    contexts = available_today.get(key_today)
    if contexts:
        return contexts

    # Epic world-info can occasionally report Mythic leads as Legendary rewards.
    # For unique leads, fall back to a name-only lookup to avoid missing valid hits.
    if group == "mythic_leads":
        name_key = _normalize_name(name_en)
        for (candidate_name, _candidate_rarity), candidate_contexts in available_today.items():
            if candidate_name == name_key:
                return candidate_contexts

    return []


def _title_case_rarity(rarity: str) -> str:
    value = rarity.strip().casefold()
    mapping = {
        "common": "Common",
        "uncommon": "Uncommon",
        "rare": "Rare",
        "epic": "Epic",
        "legendary": "Legendary",
        "mythic": "Mythic",
    }
    return mapping.get(value, rarity.strip())


def _rarity_from_token(token: str) -> str:
    t = token.lower()
    m = re.search(r"_(c|uc|r|vr|sr|myt)_", t)
    if not m:
        return ""
    code_map = {
        "c": "Common",
        "uc": "Uncommon",
        "r": "Rare",
        "vr": "Epic",
        "sr": "Legendary",
        "myt": "Mythic",
    }
    return code_map.get(m.group(1), "")


def _create_backup_file(path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_file = BACKUP_DIR / f"{path.stem}.backup_{timestamp}{path.suffix}"
    if path.exists():
        backup_file.write_bytes(path.read_bytes())
    else:
        backup_file.write_text("", encoding="utf-8")
    return backup_file


def _iter_dicts(obj: object):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _iter_dicts(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _iter_dicts(value)


def _sort_available_contexts(
    available: dict[tuple[str, str], list[tuple[str, str, str, str]]],
) -> dict[tuple[str, str], list[tuple[str, str, str, str]]]:
    result: dict[tuple[str, str], list[tuple[str, str, str, str]]] = {}
    for key, contexts in available.items():
        result[key] = sorted(
            contexts,
            key=lambda c: (
                c[0].casefold(),
                c[1].casefold(),
                int(c[2].split()[0]) if c[2].split() and c[2].split()[0].isdigit() else 10**9,
                c[2],
                c[3],
            ),
        )
    return result


def _build_theater_name_map(payload: dict) -> dict[str, str]:
    result: dict[str, str] = {}
    theaters = payload.get("theaters", []) if isinstance(payload, dict) else []
    if not isinstance(theaters, list):
        return result

    for theater in theaters:
        if not isinstance(theater, dict):
            continue
        unique_id = theater.get("uniqueId", "")
        display_name = theater.get("displayName", {})
        if not isinstance(unique_id, str) or not unique_id.strip():
            continue
        if isinstance(display_name, dict):
            en_name = display_name.get("en", "")
            if isinstance(en_name, str) and en_name.strip():
                result[unique_id.strip()] = en_name.strip()
    return result


def _populate_theater_loc_map(payload: dict) -> None:
    global _THEATER_LOC_MAP
    if OUTPUT_LANG == "en":
        return
    theaters = payload.get("theaters", []) if isinstance(payload, dict) else []
    if not isinstance(theaters, list):
        return
    for theater in theaters:
        if not isinstance(theater, dict):
            continue
        display_name = theater.get("displayName", {})
        if not isinstance(display_name, dict):
            continue
        en_name = display_name.get("en", "")
        loc_name = display_name.get(OUTPUT_LANG, "")
        if isinstance(en_name, str) and en_name.strip() and isinstance(loc_name, str) and loc_name.strip():
            _THEATER_LOC_MAP[en_name.strip()] = loc_name.strip()


@lru_cache(maxsize=1)
def _load_difficulty_ratings() -> dict[str, int]:
    if not GAME_GROWTH_BOUNDS_FILE.exists():
        rprint(f"[yellow]Warning: Difficulty growth bounds file not found: [cyan]{GAME_GROWTH_BOUNDS_FILE}[/cyan][/yellow]")
        return {}

    try:
        with open(GAME_GROWTH_BOUNDS_FILE, encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return {}

    rows = {}
    if isinstance(payload, list) and payload:
        first_entry = payload[0]
        if isinstance(first_entry, dict):
            rows = first_entry.get("Rows", {})
    elif isinstance(payload, dict):
        rows = payload.get("Rows", {})

    if not isinstance(rows, dict):
        return {}

    ratings: dict[str, int] = {}
    for row_name, row in rows.items():
        if not isinstance(row_name, str) or not isinstance(row, dict):
            continue
        rating = row.get("RecommendedRating")
        if isinstance(rating, (int, float)):
            ratings[row_name] = int(rating)
    return ratings


@lru_cache(maxsize=1)
def _load_missiongen_display_names() -> dict[str, str]:
    names: dict[str, str] = {}
    parents: dict[str, str] = {}
    if not MISSIONGEN_DIR.exists():
        rprint(f"[yellow]Warning: MissionGens directory not found: [cyan]{MISSIONGEN_DIR}[/cyan][/yellow]")
        return names

    for json_path in MISSIONGEN_DIR.rglob("*.json"):
        try:
            with open(json_path, encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            continue

        if not isinstance(payload, list):
            continue

        default_obj = next(
            (
                obj
                for obj in payload
                if isinstance(obj, dict)
                and isinstance(obj.get("Name"), str)
                and obj.get("Name", "").startswith("Default__")
            ),
            None,
        )
        if not isinstance(default_obj, dict):
            continue

        properties = default_obj.get("Properties", {})
        if not isinstance(properties, dict):
            continue

        stem = json_path.stem.strip()
        default_name = default_obj.get("Name", "")
        class_name = ""
        if isinstance(default_name, str) and default_name.startswith("Default__"):
            class_name = default_name[len("Default__"):].strip()

        mission_name = properties.get("MissionName", {})
        if isinstance(mission_name, dict):
            display = ""
            for key in ("LocalizedString", "SourceString"):
                value = mission_name.get(key)
                if isinstance(value, str) and value.strip():
                    display = value.strip()
                    break
            if display:
                if stem:
                    names[stem.casefold()] = display
                if class_name:
                    names[class_name.casefold()] = display

        template = default_obj.get("Template", {})
        template_path = ""
        if isinstance(template, dict):
            template_path = str(template.get("ObjectPath", "")).strip()
        if template_path:
            parent_key = template_path.rsplit("/", 1)[-1].split(".", 1)[0].strip()
            if parent_key:
                if stem:
                    parents[stem.casefold()] = parent_key.casefold()
                if class_name:
                    parents[class_name.casefold()] = parent_key.casefold()

    # Resolve inherited mission names through template parent links.
    for _ in range(8):
        changed = False
        for child_key, parent_key in parents.items():
            if child_key in names:
                continue
            parent_name = names.get(parent_key)
            if parent_name:
                names[child_key] = parent_name
                changed = True
        if not changed:
            break

    return names


def _normalize_epic_mission_name(value: str) -> str:
    full_token = value.rsplit("/", 1)[-1].strip()
    path_token = full_token.split(".", 1)[0].strip()
    class_token = full_token.split(".", 1)[1].strip() if "." in full_token else ""

    missiongen_names = _load_missiongen_display_names()
    for candidate in (path_token, class_token):
        if not candidate:
            continue
        mapped = missiongen_names.get(candidate.casefold())
        if mapped:
            return mapped

    return ""


def _get_epic_access_token() -> str:
    refresh_token = os.environ.get("EPIC_REFRESH_TOKEN", "").strip()
    if not refresh_token:
        raise ValueError("EPIC_REFRESH_TOKEN not set")
    return epic_api.get_access_token(refresh_token)


def _extract_epic_reward_name_and_rarity(reward: dict) -> tuple[str, str]:
    if not isinstance(reward, dict):
        return "", ""

    name_val = reward.get("name")
    rarity_val = reward.get("rarity")
    if isinstance(name_val, str) and name_val.strip() and isinstance(rarity_val, str) and rarity_val.strip():
        candidate = rarity_val.strip()
        if "::" in candidate:
            candidate = candidate.rsplit("::", 1)[-1]
        return name_val.strip(), _title_case_rarity(candidate)

    item_type = reward.get("itemType")
    if not isinstance(item_type, str) or ":" not in item_type:
        return "", ""

    token = item_type.split(":", 1)[-1].strip()
    if not token:
        return "", ""

    rarity = _rarity_from_token(token)
    token_rarity = rarity

    resolved = lookup_item_definition_name(token)
    if not isinstance(resolved, dict):
        return "", ""

    en_name = resolved.get("en")
    if not isinstance(en_name, str) or not en_name.strip() or en_name == "-":
        return "", ""

    resolved_rarity_raw = resolved.get("rarity", "")
    resolved_rarity = ""
    if isinstance(resolved_rarity_raw, str) and resolved_rarity_raw.strip():
        resolved_rarity = _title_case_rarity(resolved_rarity_raw)

    final_rarity = resolved_rarity or token_rarity
    if not final_rarity:
        return "", ""

    return en_name.strip(), final_rarity


def _extract_mission_context(entry: dict, theater_names: dict[str, str]) -> tuple[str, str, str, str]:
    if not isinstance(entry, dict):
        return "", "", "", ""

    zone = ""
    theater_id = entry.get("theaterId")
    if isinstance(theater_id, str) and theater_id.strip():
        zone = theater_names.get(theater_id.strip(), "")

    mission_name = ""
    mission_generator = entry.get("missionGenerator")
    if isinstance(mission_generator, str) and mission_generator.strip():
        mission_name = _normalize_epic_mission_name(mission_generator.strip())

    pl = ""
    difficulty_info = entry.get("missionDifficultyInfo")
    if isinstance(difficulty_info, dict):
        row_name = difficulty_info.get("rowName")
        if isinstance(row_name, str) and row_name.strip():
            recommended_rating = _load_difficulty_ratings().get(row_name.strip())
            if recommended_rating is not None:
                pl = str(recommended_rating)

    mission_alert_guid = ""
    mission_alert_guid_raw = entry.get("missionAlertGuid")
    if isinstance(mission_alert_guid_raw, str) and mission_alert_guid_raw.strip():
        mission_alert_guid = mission_alert_guid_raw.strip()

    return zone, mission_name, pl, mission_alert_guid


def _iter_reward_objects(obj: object):
    for data in _iter_dicts(obj):
        items = data.get("items") if isinstance(data, dict) else None
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    yield item
        for key, value in data.items():
            if "reward" not in str(key).lower():
                continue
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        yield item
            elif isinstance(value, dict):
                yield value


def _fetch_today_available_items() -> dict[tuple[str, str], list[tuple[str, str, str, str]]]:
    access_token = _get_epic_access_token()
    payload = epic_api.query_world_info(access_token)

    available: dict[tuple[str, str], list[tuple[str, str, str, str]]] = {}
    theater_names = _build_theater_name_map(payload)
    _populate_theater_loc_map(payload)

    missions = payload.get("missions", []) if isinstance(payload, dict) else []
    mission_lookup: dict[tuple[str, object], dict] = {}
    if isinstance(missions, list):
        for wrapper in missions:
            if not isinstance(wrapper, dict):
                continue
            theater_id = wrapper.get("theaterId", "")
            available_missions = wrapper.get("availableMissions", [])
            if not isinstance(available_missions, list):
                continue
            for mission in available_missions:
                if not isinstance(mission, dict):
                    continue
                tile_index = mission.get("tileIndex")
                if isinstance(theater_id, str) and theater_id.strip() and tile_index is not None:
                    mission_lookup[(theater_id.strip(), tile_index)] = mission
                context_entry = dict(wrapper)
                context_entry.update(mission)
                context_entry["theaterId"] = theater_id
                context = _extract_mission_context(context_entry, theater_names)
                # if not context[3]:
                #     continue
                for reward in _iter_reward_objects(mission):
                    name, rarity = _extract_epic_reward_name_and_rarity(reward)
                    if not name or not rarity:
                        continue
                    norm_key = (_normalize_name(name), _normalize_rarity(rarity))
                    available.setdefault(norm_key, []).append(context)

    mission_alerts = payload.get("missionAlerts", []) if isinstance(payload, dict) else []
    if isinstance(mission_alerts, list):
        for wrapper in mission_alerts:
            if not isinstance(wrapper, dict):
                continue
            theater_id = wrapper.get("theaterId", "")
            available_mission_alerts = wrapper.get("availableMissionAlerts", [])
            if not isinstance(available_mission_alerts, list):
                continue
            for alert in available_mission_alerts:
                if not isinstance(alert, dict):
                    continue
                context_entry = dict(wrapper)
                context_entry.update(alert)
                context_entry["theaterId"] = theater_id
                tile_index = alert.get("tileIndex")
                if isinstance(theater_id, str) and theater_id.strip() and tile_index is not None:
                    matching_mission = mission_lookup.get((theater_id.strip(), tile_index))
                    if isinstance(matching_mission, dict):
                        context_entry.update(matching_mission)
                context = _extract_mission_context(context_entry, theater_names)
                if not context[3]:
                    continue
                for reward in _iter_reward_objects(alert):
                    name, rarity = _extract_epic_reward_name_and_rarity(reward)
                    if not name or not rarity:
                        continue
                    norm_key = (_normalize_name(name), _normalize_rarity(rarity))
                    available.setdefault(norm_key, []).append(context)

    _save_raw_snapshot(payload=payload, available=available)
    return _sort_available_contexts(available)


def _build_loc_name_map() -> dict[str, str]:
    if OUTPUT_LANG == "en" or OUTPUT_LANG not in AVAILABLE_LANGS:
        return {}

    data_loc = get_collection_book(OUTPUT_LANG)
    result: dict[str, str] = {}
    for page in data_loc["pages"].values():
        for section in page["sections"]:
            for slot in section["slots"]:
                slot_id = slot.get("slot_id", "")
                name = slot.get("name", "")
                if slot_id and isinstance(name, str):
                    result[slot_id] = name
    return result


def _format_context_suffix(context: tuple[str, str, str, str]) -> str:
    zone, mission, pl, _guid = context
    zone_loc = (_THEATER_LOC_MAP.get(zone) or _loc(zone, OUTPUT_LANG)) if zone else ""
    mission_loc = _loc(mission, OUTPUT_LANG) if mission else ""
    right = f"{mission_loc} ({pl})" if mission_loc and pl else (mission_loc or pl)
    if zone_loc and right:
        part = f"{zone_loc} - {right}"
    elif zone_loc:
        part = zone_loc
    elif right:
        part = right
    else:
        return ""
    return " --- " + part


def build_report(
    available_today: dict[tuple[str, str], list[tuple[str, str, str, str]]] | None = None,
) -> str:
    current = state_mod.load()
    data_en = get_collection_book("en")
    loc_names = _build_loc_name_map()
    available_today = available_today or {}

    grouped: dict[str, list[dict]] = {
        "heroes": [],
        "weapons": [],
        "mythic_leads": [],
    }
    available_all_by_group: dict[str, list[dict]] = {
        "heroes": [],
        "weapons": [],
        "mythic_leads": [],
    }

    for page in data_en["pages"].values():
        category_id = page.get("category_id", "")
        page_id = page.get("id", "")

        for section in page["sections"]:
            for slot in section["slots"]:
                slot_id = slot.get("slot_id", "")
                if not isinstance(slot_id, str) or not slot_id:
                    continue

                group = _slot_group(slot_id, page_id, category_id)
                if group is None:
                    continue

                in_collection = bool(current["col"].get(slot_id))
                inv_count_raw = current["inv"].get(slot_id, 0)
                inv_count = inv_count_raw if isinstance(inv_count_raw, int) else 0
                in_inventory = inv_count > 0

                rarity_css = slot.get("rarity_css", "")
                if not _passes_filter(in_collection, in_inventory, rarity_css):
                    continue

                name_en = str(slot.get("name", "")).strip() or slot_id
                name_loc = loc_names.get(slot_id, "")

                rarity_label = RARITY_LABELS.get(rarity_css, slot.get("rarity_label", "")) or ""
                today_contexts = _find_today_contexts(
                    name_en=name_en,
                    rarity=rarity_label,
                    group=group,
                    available_today=available_today,
                )

                if today_contexts:
                    available_all_by_group[group].append(
                        {
                            "name_en": name_en,
                            "name_loc": name_loc,
                            "rarity": rarity_label,
                            "in_collection": in_collection,
                            "in_inventory": in_inventory,
                            "slot_id": slot_id,
                            "today_contexts": today_contexts,
                        }
                    )

                grouped[group].append(
                    {
                        "name_en": name_en,
                        "name_loc": name_loc,
                        "rarity": rarity_label,
                        "in_collection": in_collection,
                        "in_inventory": in_inventory,
                        "slot_id": slot_id,
                        "available_today": bool(today_contexts),
                        "today_contexts": today_contexts,
                    }
                )

    available_all_slot_ids = {
        item["slot_id"]
        for items in available_all_by_group.values()
        for item in items
        if isinstance(item.get("slot_id"), str)
    }

    for page in data_en["pages"].values():
        category_id = page.get("category_id", "")
        page_id = page.get("id", "")

        for section in page["sections"]:
            for slot in section["slots"]:
                slot_id = slot.get("slot_id", "")
                if not isinstance(slot_id, str) or not slot_id:
                    continue

                group = _slot_group(slot_id, page_id, category_id)
                if group is None or slot_id in available_all_slot_ids:
                    continue

                name_en = str(slot.get("name", "")).strip() or slot_id
                rarity_css = slot.get("rarity_css", "")
                rarity_label = RARITY_LABELS.get(rarity_css, slot.get("rarity_label", "")) or ""
                today_contexts = _find_today_contexts(
                    name_en=name_en,
                    rarity=rarity_label,
                    group=group,
                    available_today=available_today,
                )
                if not today_contexts:
                    continue

                in_collection = bool(current["col"].get(slot_id))
                inv_count_raw = current["inv"].get(slot_id, 0)
                inv_count = inv_count_raw if isinstance(inv_count_raw, int) else 0
                in_inventory = inv_count > 0

                available_all_by_group[group].append(
                    {
                        "name_en": name_en,
                        "name_loc": loc_names.get(slot_id, ""),
                        "rarity": rarity_label,
                        "in_collection": in_collection,
                        "in_inventory": in_inventory,
                        "slot_id": slot_id,
                        "today_contexts": today_contexts,
                    }
                )
                available_all_slot_ids.add(slot_id)

    for items in grouped.values():
        items.sort(key=lambda item: (item["name_en"].casefold(), item["slot_id"]))

    for items in available_all_by_group.values():
        items.sort(key=lambda item: (item["name_en"].casefold(), item["slot_id"]))

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines: list[str] = []
    lines.append("Fortnite Collection - Missing Items Checklist")
    lines.append(f"Generated: {now}")
    lines.append("Source: collection.json")
    lines.append("Filter:")
    lines.append("1) Not in Collection Book")
    lines.append("2) (Legendary or Mythic) and ((not in Inventory) or (not in Collection Book))")
    lines.append("")
    lines.append("Legend:  I = not in Inventory  |  C = not in Collection Book  |  IC = not owned at all")
    lines.append("")

    labels = {
        "heroes": _loc("Heroes", OUTPUT_LANG).upper(),
        "weapons": _loc("Weapons", OUTPUT_LANG).upper(),
        "mythic_leads": _loc("Mythic Leads", OUTPUT_LANG).upper(),
    }

    available_by_group: dict[str, list[dict]] = {
        "heroes": [],
        "weapons": [],
        "mythic_leads": [],
    }

    for key in ("heroes", "weapons", "mythic_leads"):
        items = grouped[key]
        lines.append(f"{labels[key]} ({len(items)})")
        lines.append("-" * 80)
        if not items:
            lines.append("---")
            lines.append("")
            continue

        for idx, item in enumerate(items, start=1):
            rarity = _loc(item["rarity"], OUTPUT_LANG) if item["rarity"] else "Unknown"
            flags = _missing_flags(item["in_collection"], item["in_inventory"])
            name = item["name_loc"] or item["name_en"]
            if item.get("available_today"):
                available_by_group[key].append(item)

            line = f"{idx:>3}. {name} | {rarity} | {flags}"

            lines.append(line)

        lines.append("")

    total_available = sum(len(item.get("today_contexts", [])) for v in available_by_group.values() for item in v)
    lines.append(f"AVAILABLE TODAY ({total_available})")
    lines.append("-" * 80)
    if total_available == 0:
        lines.append("No matches from the above list are available today.")
        lines.append("")
    else:
        for key in ("heroes", "weapons", "mythic_leads"):
            avail_items = available_by_group[key]
            group_count = sum(len(item.get("today_contexts", [])) for item in avail_items)
            lines.append(f"{labels[key]} ({group_count})")
            if not avail_items:
                lines.append("---")
                lines.append("")
                continue

            idx = 0
            for item in avail_items:
                rarity = _loc(item["rarity"], OUTPUT_LANG) if item["rarity"] else "Unknown"
                flags = _missing_flags(item["in_collection"], item["in_inventory"])
                for context in item.get("today_contexts", []):
                    idx += 1
                    mission_suffix = _format_context_suffix(context)
                    name = item["name_loc"] or item["name_en"]
                    line = f"{idx:>3}. {name} | {rarity} | {flags}{mission_suffix}"
                    lines.append(line)
            lines.append("")

    total_available_all = sum(len(item.get("today_contexts", [])) for v in available_all_by_group.values() for item in v)
    lines.append(f"AVAILABLE TODAY (ALL) ({total_available_all})")
    lines.append("-" * 80)
    if total_available_all == 0:
        lines.append("No matches found.")
        lines.append("")
    else:
        for key in ("heroes", "weapons", "mythic_leads"):
            avail_items = available_all_by_group[key]
            group_count = sum(len(item.get("today_contexts", [])) for item in avail_items)
            lines.append(f"{labels[key]} ({group_count})")
            if not avail_items:
                lines.append("---")
                lines.append("")
                continue

            idx = 0
            for item in avail_items:
                rarity = _loc(item["rarity"], OUTPUT_LANG) if item["rarity"] else "Unknown"
                flags = _missing_flags(item["in_collection"], item["in_inventory"])
                for context in item.get("today_contexts", []):
                    idx += 1
                    mission_suffix = _format_context_suffix(context)
                    name = item["name_loc"] or item["name_en"]
                    line = f"{idx:>3}. {name} | {rarity} | {flags}{mission_suffix}"
                    lines.append(line)
            lines.append("")

    return "\n".join(lines)


def main() -> None:
    global OUTPUT_LANG

    parser = argparse.ArgumentParser(description="Missing items report for STW collection book")
    parser.add_argument("--lang", default="en",
                        help=f"Output language ({', '.join(AVAILABLE_LANGS)}). Default: en")
    args = parser.parse_args()

    lang = args.lang.lower()
    if lang not in AVAILABLE_LANGS:
        rprint(f"[red]ERROR: Language '{lang}' is not available. "
               f"Available: {', '.join(AVAILABLE_LANGS)}[/red]")
        sys.exit(1)
    OUTPUT_LANG = lang

    available_today: dict[tuple[str, str], list[tuple[str, str, str, str]]] = {}
    refresh_token = os.environ.get("EPIC_REFRESH_TOKEN", "").strip()

    if not refresh_token:
        rprint("[yellow]EPIC_REFRESH_TOKEN not set. Using local snapshot.[/yellow]")
        fallback_data, fallback_file = _load_latest_available_today_snapshot()
        if fallback_data:
            available_today = fallback_data
            rprint(f"[green]Availability loaded from snapshot ([cyan]{fallback_file}[/cyan]).[/green]")
        else:
            rprint("[yellow]No snapshot available. Availability data will be empty.[/yellow]")
    else:
        try:
            available_today = _fetch_today_available_items()
        except (TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
            rprint(f"[yellow]Notice: Could not fetch Epic availability data ({exc}).[/yellow]")
            fallback_data, fallback_file = _load_latest_available_today_snapshot()
            if fallback_data:
                available_today = fallback_data
                rprint(f"[green]Fallback: Availability loaded from snapshot ([cyan]{fallback_file}[/cyan]).[/green]")
            else:
                rprint("[yellow]No availability data will be used.[/yellow]")

    report = build_report(available_today=available_today)
    backup_file = _create_backup_file(DEFAULT_OUTPUT_FILE)
    rprint(f"[dim]Backup created: [cyan]{backup_file}[/cyan][/dim]")
    DEFAULT_OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DEFAULT_OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(report)

    rprint(f"[green]Report written: [cyan]{DEFAULT_OUTPUT_FILE}[/cyan][/green]")


if __name__ == "__main__":
    main()
