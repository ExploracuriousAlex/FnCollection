import csv
import json
import re
from dataclasses import dataclass
from itertools import product
from pathlib import Path

from data_loader import lookup_item_definition_name


BASE_DIR = Path(__file__).parent
TRAP_ITEM_DIR = (
    BASE_DIR
    / "FortniteGame"
    / "Plugins"
    / "GameFeatures"
    / "SaveTheWorld"
    / "Content"
    / "Items"
    / "Traps"
)
TRAPS_TABLE_PATH = BASE_DIR / "FortniteGame" / "Content" / "Items" / "DataTables" / "Traps.json"
ALTERATION_V2_DIR = (
    BASE_DIR
    / "FortniteGame"
    / "Plugins"
    / "GameFeatures"
    / "SaveTheWorld"
    / "Content"
    / "Items"
    / "Alteration_v2"
)
COMBAT_EFFECT_MAGNITUDE_PATH = (
    BASE_DIR / "FortniteGame" / "Content" / "Balance" / "DataTables" / "CombatEffectMagnitude.json"
)
WEAPON_DURABILITY_PATH = BASE_DIR / "FortniteGame" / "Content" / "Items" / "DataTables" / "WeaponDurabilityRarity.json"

# Parent blueprint directories containing alteration attribute definitions
ALTERATION_PARENT_ASSET_DIRS = [
    BASE_DIR / "FortniteGame" / "Content" / "Items" / "Alteration_v2" / "ParentAssets",
    BASE_DIR
    / "FortniteGame"
    / "Plugins"
    / "GameFeatures"
    / "SaveTheWorld"
    / "Content"
    / "Items"
    / "Alteration_v2"
    / "ParentAssets",
]


def _build_combat_row_mapping() -> dict[str, str]:
    """
    Programmatically extract AID-to-CombatEffectMagnitude row name mappings from parent blueprint files.

    Searches for GET_Alteration_Attribute_*.json files and extracts the RowName from their
    ModifierMagnitude.ScalableFloatMagnitude.Curve definitions.

    For attributes with multiple modifiers, prioritizes Trap-specific versions.

    Returns:
        dict: Mapping from AID name substring (lowercase) to CombatEffectMagnitude row name.
              Example: {"damage": "Item.All.Damage.Normal", ...}
    """
    mapping: dict[str, str] = {}

    for parent_dir in ALTERATION_PARENT_ASSET_DIRS:
        if not parent_dir.exists():
            continue

        # Find all GET_Alteration_Attribute_*.json files
        for json_file in sorted(parent_dir.glob("GET_Alteration_Attribute_*.json")):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                # Extract attribute name from filename: "GET_Alteration_Attribute_Damage.json" -> "damage"
                attribute_name = json_file.stem.replace("GET_Alteration_Attribute_", "").lower()

                # Find the row name in any object with Modifiers -> ModifierMagnitude -> ScalableFloatMagnitude -> Curve
                # Collect all valid RowNames from this file
                row_names: list[str] = []
                for obj in data:
                    properties = obj.get("Properties", {})
                    modifiers = properties.get("Modifiers", [])

                    for modifier in modifiers:
                        magnitude = modifier.get("ModifierMagnitude", {})
                        scalable_float = magnitude.get("ScalableFloatMagnitude", {})
                        curve = scalable_float.get("Curve", {})
                        row_name = curve.get("RowName", "")

                        if row_name and row_name != "None":
                            row_names.append(row_name)

                if row_names:
                    # Prefer Trap versions if available, otherwise take the first one
                    trap_version = next((r for r in row_names if "Item.Trap" in r), None)
                    mapping[attribute_name] = trap_version if trap_version else row_names[0]

            except (json.JSONDecodeError, IOError, KeyError):
                # Skip files that can't be read or parsed
                continue

    return mapping


RELEVANT_TRAP_ATTRIBUTES_EN: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Ceiling Drop Trap",
        (
            "Damage",
            "Arming Delay",
            "Reload Duration",
            "Crit Chance",
            "Crit Damage",
            "Impact",
            "Durability",
        ),
    ),
    (
        "Broadside",
        (
            "Damage",
            "Arming Delay",
            "Reload Duration",
            "Stun Time",
            "Knockback",
            "Crit Chance",
            "Crit Damage",
            "Durability",
            "Impact",
        ),
    ),
    ("Floor Freeze Trap", ("Arming Delay", "Reload Duration", "Stun Time", "Durability")),
    (
        "Floor Launcher",
        ("Arming Delay", "Reload Duration", "Stun Time", "Knockback", "Durability", "Impact"),
    ),
    (
        "Ceiling Electric Field",
        (
            "Damage",
            "Arming Delay",
            "Reload Duration",
            "Stun Time",
            "Crit Chance",
            "Crit Damage",
            "Durability",
        ),
    ),
    (
        "Ceiling Gas Trap",
        (
            "Damage",
            "Arming Delay",
            "Reload Duration",
            "Stun Time",
            "Crit Chance",
            "Crit Damage",
            "Durability",
        ),
    ),
    (
        "Ceiling Zapper",
        (
            "Damage",
            "Arming Delay",
            "Reload Duration",
            "Stun Time",
            "Crit Chance",
            "Crit Damage",
            "Durability",
        ),
    ),
    (
        "Retractable Floor Spikes",
        (
            "Damage",
            "Arming Delay",
            "Reload Duration",
            "Stun Time",
            "Knockback",
            "Crit Chance",
            "Crit Damage",
            "Durability",
        ),
    ),
    (
        "Wooden Floor Spikes",
        (
            "Damage",
            "Arming Delay",
            "Reload Duration",
            "Stun Time",
            "Crit Chance",
            "Crit Damage",
            "Durability",
        ),
    ),
    ("Cozy Campfire", ("Reload Duration", "Durability")),
    (
        "Anti-Air Trap",
        (
            "Damage",
            "Arming Delay",
            "Reload Duration",
            "Crit Chance",
            "Crit Damage",
            "Durability",
        ),
    ),
    ("Sound Wall", ("Arming Delay", "Reload Duration", "Stun Time", "Durability", "Impact")),
    (
        "Tar Pit",
        (
            "Damage",
            "Arming Delay",
            "Reload Duration",
            "Stun Time",
            "Crit Chance",
            "Crit Damage",
            "Durability",
        ),
    ),
    (
        "Wall Dynamo",
        (
            "Damage",
            "Arming Delay",
            "Reload Duration",
            "Stun Time",
            "Crit Chance",
            "Crit Damage",
            "Durability",
        ),
    ),
    (
        "Wall Launcher",
        ("Arming Delay", "Reload Duration", "Stun Time", "Knockback", "Durability", "Impact"),
    ),
    ("Wall Lights", ("Arming Delay", "Reload Duration", "Stun Time", "Durability", "Impact")),
    (
        "Wall Darts",
        (
            "Damage",
            "Arming Delay",
            "Reload Duration",
            "Stun Time",
            "Crit Chance",
            "Crit Damage",
            "Durability",
        ),
    ),
    ("Healing Pad", ("Healing", "Reload Duration", "Durability")),
    (
        "Flame Grill Floor Trap",
        (
            "Damage",
            "Arming Delay",
            "Reload Duration",
            "Stun Time",
            "Crit Chance",
            "Crit Damage",
            "Durability",
        ),
    ),
)

RARITY_KEYS = ("EFortRarity::Epic", "EFortRarity::Legendary")
RARITY_LABELS = {
    "EFortRarity::Epic": "T4",
    "EFortRarity::Legendary": "T5",
}


@dataclass(frozen=True)
class TrapBaseStats:
    row_name: str
    fire_delay: float
    arming_delay: float
    reload_time: float
    damage_pb: float
    impact_damage_pb: float
    crit_chance: float
    crit_damage_bonus: float
    stun_time: float
    knockback_magnitude: float
    durability: float


@dataclass(frozen=True)
class AlterationEffect:
    slot_index: int
    slot_definition_row: str
    rarity_key: str
    aid_token: str
    label: str
    description: str
    primary_effect_value: float = 1.0
    damage_mult: float = 1.0
    reload_speed_mult: float = 1.0
    crit_chance_bonus: float = 0.0
    crit_damage_bonus: float = 0.0
    durability_mult: float = 1.0


@dataclass(frozen=True)
class DetailedLoadoutRow:
    trap_name: str
    relevant_attributes: frozenset[str]
    perks: tuple[str, ...]
    slot_values: tuple[float, ...]
    damage_per_second: float
    damage_per_shot: float
    total_trap_damage: float
    trap_damage: float
    trap_impact: float
    trap_arming_delay: float
    trap_stun_time: float
    trap_knockback: float
    crit_chance: float
    crit_damage_bonus_percent: float
    trap_durability: float
    trap_reload_time: float


def _read_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _first_obj(payload):
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        raise ValueError("Unexpected JSON structure in exported game file.")
    return payload[0]


def _norm_aid_token(value: str) -> str:
    return value.split(":", 1)[-1].strip().lower()


def _effective_crit_chance(crit: float) -> float:
    """
    Berechnet die effektive Crit Chance nach Game-Formel.
    
    Parameter:
        crit: Critical Hit Chance in Dezimalform (z.B. 0.54 für 54%)
    
    Rückgabe:
        Effektive Crit Chance
    """
    value = (3 * crit) / (4 * crit + 2)
    return round(value * 2, 2) / 2


def _norm_label(value: str) -> str:
    return " ".join(value.split()).casefold()


def _require_combat_row(combat_rows: dict, row_name: str, aid_name: str) -> dict:
    row = combat_rows.get(row_name)
    if not isinstance(row, dict):
        raise FileNotFoundError(
            f"CombatEffectMagnitude export row missing: {row_name} (required by {aid_name})"
        )
    return row


def _attribute_column_order() -> list[str]:
    seen: set[str] = set()
    order: list[str] = []
    for _, attrs in RELEVANT_TRAP_ATTRIBUTES_EN:
        for attr in attrs:
            if attr not in seen:
                seen.add(attr)
                order.append(attr)
    return order


def _build_combat_row_mapping() -> dict[str, str]:
    """
    Programmatically extract AID-to-CombatEffectMagnitude row name mappings from parent blueprint files.

    Searches for GET_Alteration_Attribute_*.json files and extracts the RowName from their
    ModifierMagnitude.ScalableFloatMagnitude.Curve definitions.

    For attributes with multiple modifiers, prioritizes Trap-specific versions.

    Returns:
        dict: Mapping from attribute name (lowercase) to CombatEffectMagnitude row name.
              Example: {"damage": "Item.All.Damage.Normal", ...}
    """
    mapping: dict[str, str] = {}

    for parent_dir in ALTERATION_PARENT_ASSET_DIRS:
        if not parent_dir.exists():
            continue

        # Find all GET_Alteration_Attribute_*.json files
        for json_file in sorted(parent_dir.glob("GET_Alteration_Attribute_*.json")):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                # Extract attribute name from filename: "GET_Alteration_Attribute_Damage.json" -> "damage"
                attribute_name = json_file.stem.replace("GET_Alteration_Attribute_", "").lower()

                # Find the row name in any object with Modifiers -> ModifierMagnitude -> ScalableFloatMagnitude -> Curve
                # Collect all valid RowNames from this file
                row_names: list[str] = []
                for obj in data:
                    properties = obj.get("Properties", {})
                    modifiers = properties.get("Modifiers", [])

                    for modifier in modifiers:
                        magnitude = modifier.get("ModifierMagnitude", {})
                        scalable_float = magnitude.get("ScalableFloatMagnitude", {})
                        curve = scalable_float.get("Curve", {})
                        row_name = curve.get("RowName", "")

                        if row_name and row_name != "None":
                            row_names.append(row_name)

                if row_names:
                    # Prefer Trap versions if available, otherwise take the first one
                    trap_version = next((r for r in row_names if "Item.Trap" in r), None)
                    mapping[attribute_name] = trap_version if trap_version else row_names[0]

            except (json.JSONDecodeError, IOError, KeyError):
                # Skip files that can't be read or parsed
                continue

    return mapping


def _build_aid_to_attribute_mapping() -> dict[str, str]:
    """
    Build exact 1:1 mapping from AID token to attribute name by reading AID properties.

    For each AID file, extracts the attribute from its AlterationAbilitySet reference,
    which contains the attribute name in the asset name (e.g., AS_Att_BuildingHeal_T01 → "BuildingHeal").
    
    Falls back to directory name if direct extraction fails.

    Returns:
        dict: Mapping from normalized AID token to attribute name (lowercase).
              Example: {"aid_att_damage_t01": "damage", "aid_att_damage_physical_t01": "physical"}
    """
    mapping: dict[str, str] = {}

    # Recursively search for all AID_*.json files in AttributeAlterations
    alteration_base = ALTERATION_V2_DIR / "AttributeAlterations"
    if not alteration_base.exists():
        return mapping

    for aid_file in alteration_base.rglob("AID_*.json"):
        try:
            aid_root = _first_obj(_read_json(aid_file))
            aid_properties = aid_root.get("Properties", {})
            aid_token = aid_file.stem.lower()

            # Try to extract attribute name from AlterationAbilitySet reference
            ability_set_ref = aid_properties.get("AlterationAbilitySet", {})
            ability_asset = str(ability_set_ref.get("AssetPathName", ""))
            
            # Extract name from path like "/SaveTheWorld/Items/Alteration_v2/AttributeAlterations/Damage/AS_Att_Damage_T01"
            # Get the part after the last "/" and before any "."
            ability_name = ability_asset.rsplit("/", 1)[-1].split(".", 1)[0]  # e.g., "AS_Att_Damage_T01"
            
            # Extract attribute from "AS_Att_Damage_T01" → "Damage" or "AS_Att_Damage_Physical_T01" → "Damage_Physical"
            # Pattern: AS_Att_<ATTRIBUTE>_T<TIER>
            if ability_name.startswith("AS_Att_"):
                # Remove "AS_Att_" prefix and "_T<number>" suffix
                attribute_with_tier = ability_name[7:]  # Remove "AS_Att_"
                # Find the last underscore followed by T and digits
                import re
                match = re.match(r"^(.+?)_T\d+$", attribute_with_tier)
                if match:
                    attribute_name = match.group(1).lower()
                    mapping[aid_token] = attribute_name
                    continue
            
            # Fallback: use directory name if extraction fails
            # For cases like "Elemental/Physical", use the deepest directory name
            relative_path = aid_file.parent.relative_to(alteration_base)
            # Get the last directory component(s) that makes sense for the attribute
            parts = list(relative_path.parts)
            if len(parts) == 1:
                # Simple case: Damage/AID_*.json
                attribute_name = parts[0].lower()
            elif len(parts) > 1:
                # Nested case: Elemental/Physical/AID_*.json → use "physical"
                attribute_name = parts[-1].lower()
            else:
                continue
            
            mapping[aid_token] = attribute_name

        except Exception:
            # Skip files that can't be parsed
            continue

    return mapping


def _build_trap_token_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for path in TRAP_ITEM_DIR.rglob("TID_*.json"):
        try:
            payload = _read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        root = _first_obj(payload)
        token = str(root.get("Name", "")).strip().lower()
        if not token:
            continue
        localized = lookup_item_definition_name(token)
        if not isinstance(localized, dict):
            continue
        en_name = localized.get("en")
        if isinstance(en_name, str) and en_name.strip():
            index[_norm_label(en_name)] = token
    return index


def _resolve_trap_token(trap_name: str, trap_token_index: dict[str, str]) -> str | None:
    return trap_token_index.get(_norm_label(trap_name))


def _interpolate_curve(curve_row: dict, x: float) -> float:
    keys = curve_row.get("Keys", [])
    if not isinstance(keys, list) or not keys:
        raise ValueError("Curve row has no keys.")

    points: list[tuple[float, float]] = []
    for key in keys:
        if not isinstance(key, dict):
            continue
        time = key.get("Time")
        value = key.get("Value")
        if isinstance(time, (int, float)) and isinstance(value, (int, float)):
            points.append((float(time), float(value)))

    if not points:
        raise ValueError("Curve row has no numeric points.")

    points.sort(key=lambda p: p[0])
    if x <= points[0][0]:
        return points[0][1]

    for index in range(1, len(points)):
        x0, y0 = points[index - 1]
        x1, y1 = points[index]
        if x <= x1:
            if x1 == x0:
                return y1
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)

    return points[-1][1]


def _build_aid_file_map() -> dict[str, Path]:
    mapping: dict[str, Path] = {}
    root = ALTERATION_V2_DIR / "AttributeAlterations"
    for path in root.rglob("AID_*.json"):
        mapping[path.stem.strip().lower()] = path
    return mapping


def _build_alteration_json_map() -> dict[str, Path]:
    mapping: dict[str, Path] = {}
    root = ALTERATION_V2_DIR / "AttributeAlterations"
    for path in root.rglob("*.json"):
        key = path.stem.strip().lower()
        if key and key not in mapping:
            mapping[key] = path
    return mapping


def _build_exported_json_map() -> dict[str, Path]:
    mapping: dict[str, Path] = {}
    for path in (BASE_DIR / "FortniteGame").rglob("*.json"):
        mapping[path.stem.strip().lower()] = path
    return mapping


def _candidate_asset_names(asset_ref: str) -> list[str]:
    if not isinstance(asset_ref, str) or not asset_ref or asset_ref.startswith("/Script/"):
        return []
    tail = asset_ref.rsplit("/", 1)[-1]
    if "." not in tail:
        return [tail]
    left, right = tail.split(".", 1)
    candidates = [left]
    if right and not right.isdigit():
        candidates.append(right)
    result: list[str] = []
    for candidate in candidates:
        candidate = candidate.strip()
        if candidate and candidate not in result:
            result.append(candidate)
    return result


def _resolve_json_from_asset_ref(asset_ref: str, exported_json_map: dict[str, Path]) -> Path | None:
    for candidate in _candidate_asset_names(asset_ref):
        path = exported_json_map.get(candidate.lower())
        if path is not None:
            return path
    return None


def _walk_json(node, visit):
    if isinstance(node, dict):
        visit(node)
        for value in node.values():
            _walk_json(value, visit)
    elif isinstance(node, list):
        for item in node:
            _walk_json(item, visit)


def _resolve_gameplay_ability_paths(trap_root: dict, exported_json_map: dict[str, Path]) -> list[Path]:
    blueprint_ref = str(trap_root.get("Properties", {}).get("BlueprintClass", {}).get("AssetPathName", "")).strip()
    blueprint_path = _resolve_json_from_asset_ref(blueprint_ref, exported_json_map)
    if blueprint_path is None:
        return []

    blueprint_payload = _read_json(blueprint_path)
    ability_set_refs: list[str] = []

    def collect_ability_set_refs(node: dict):
        if "AbilitySet" not in node:
            return
        ability_set = node.get("AbilitySet")
        if isinstance(ability_set, dict):
            asset_ref = str(ability_set.get("ObjectPath", "")).strip()
            if asset_ref:
                ability_set_refs.append(asset_ref)

    _walk_json(blueprint_payload, collect_ability_set_refs)

    gameplay_ability_paths: list[Path] = []
    for ability_set_ref in ability_set_refs:
        ability_set_path = _resolve_json_from_asset_ref(ability_set_ref, exported_json_map)
        if ability_set_path is None:
            continue
        ability_set_root = _first_obj(_read_json(ability_set_path))
        for ability in ability_set_root.get("Properties", {}).get("GameplayAbilities", []):
            if not isinstance(ability, dict):
                continue
            ability_ref = str(ability.get("ObjectPath", "")).strip()
            if not ability_ref:
                continue
            ability_path = _resolve_json_from_asset_ref(ability_ref, exported_json_map)
            if ability_path is not None:
                gameplay_ability_paths.append(ability_path)

    return gameplay_ability_paths


def _collect_referenced_ge_paths(gameplay_ability_path: Path, exported_json_map: dict[str, Path]) -> list[Path]:
    ge_paths: list[Path] = []
    payload = _read_json(gameplay_ability_path)
    root = _first_obj(payload)

    default_name = str(root.get("ClassDefaultObject", {}).get("ObjectName", ""))
    default_marker = "'Default__"
    default_class = ""
    if default_marker in default_name:
        default_class = default_name.split(default_marker, 1)[1].split("'", 1)[0]

    default_object: dict | None = None
    if default_class:
        for obj in payload:
            if isinstance(obj, dict) and str(obj.get("Name", "")) == f"Default__{default_class}":
                default_object = obj
                break

    if not isinstance(default_object, dict):
        return ge_paths

    properties = default_object.get("Properties", {})
    if not isinstance(properties, dict):
        return ge_paths

    # 1) Explicit GE_* properties in GA defaults (e.g. GE_Freeze)
    for key, value in properties.items():
        if not key.startswith("GE_"):
            continue
        if not isinstance(value, dict):
            continue
        ge_ref = str(value.get("ObjectPath", "")).strip()
        if not ge_ref:
            continue
        ge_path = _resolve_json_from_asset_ref(ge_ref, exported_json_map)
        if ge_path is not None:
            ge_paths.append(ge_path)

    # 2) GE classes inside effect containers
    containers = properties.get("EffectContainers", [])
    if isinstance(containers, list):
        for container in containers:
            if not isinstance(container, dict):
                continue
            classes = container.get("TargetGameplayEffectClasses", [])
            if not isinstance(classes, list):
                continue
            for ge in classes:
                if not isinstance(ge, dict):
                    continue
                ge_ref = str(ge.get("ObjectPath", "")).strip()
                if not ge_ref:
                    continue
                ge_path = _resolve_json_from_asset_ref(ge_ref, exported_json_map)
                if ge_path is not None:
                    ge_paths.append(ge_path)

    dedup: list[Path] = []
    seen: set[Path] = set()
    for ge_path in ge_paths:
        if ge_path in seen:
            continue
        seen.add(ge_path)
        dedup.append(ge_path)
    return dedup


def _gameplay_effect_has_duration(ge_path: Path) -> bool:
    payload = _read_json(ge_path)
    found = False

    def inspect(node: dict):
        nonlocal found
        if found:
            return
        if "DurationMagnitude" in node:
            found = True
            return
        duration_policy = node.get("DurationPolicy")
        if isinstance(duration_policy, str) and "HasDuration" in duration_policy:
            found = True

    _walk_json(payload, inspect)
    return found


def _trap_supports_effect_duration(trap_root: dict, exported_json_map: dict[str, Path]) -> bool:
    for ga_path in _resolve_gameplay_ability_paths(trap_root, exported_json_map):
        for ge_path in _collect_referenced_ge_paths(ga_path, exported_json_map):
            if _gameplay_effect_has_duration(ge_path):
                return True
    return False


def _trap_rarity_column(trap_name: str) -> str:
    match = re.search(r"_(C|UC|R|VR|SR|MYT)_T\d+$", trap_name, flags=re.IGNORECASE)
    if not match:
        return "Legendary"
    token = match.group(1).upper()
    mapping = {
        "C": "Common",
        "UC": "Uncommon",
        "R": "Rare",
        "VR": "Epic",
        "SR": "Legendary",
        "MYT": "Mythic",
    }
    return mapping.get(token, "Legendary")


def _resolve_trap_definition(trap_name: str) -> tuple[dict, Path]:
    target = trap_name.strip().lower()
    for path in TRAP_ITEM_DIR.rglob("TID_*.json"):
        try:
            payload = _read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        root = _first_obj(payload)
        name = str(root.get("Name", "")).strip().lower()
        if name == target:
            return root, path
    raise FileNotFoundError(f"Trap item definition not found: {trap_name}")


def _resolve_base_durability(trap_root: dict, trap_row: dict) -> float:
    durability_rows = _first_obj(_read_json(WEAPON_DURABILITY_PATH)).get("Rows", {})
    if not isinstance(durability_rows, dict):
        raise ValueError("WeaponDurabilityRarity rows are missing")

    row_name_key = str(trap_row.get("DurabilityRowName", "")).strip()
    durability_row = durability_rows.get(row_name_key)
    if not isinstance(durability_row, dict):
        raise ValueError(f"Durability row not found: {row_name_key}")

    rarity_column = _trap_rarity_column(str(trap_root.get("Name", "")))
    durability_value = durability_row.get(rarity_column)
    if not isinstance(durability_value, (int, float)):
        raise ValueError(f"Durability value missing for rarity {rarity_column} in {row_name_key}")
    return float(durability_value)


def _resolve_trap_base_stats(trap_root: dict) -> TrapBaseStats:
    properties = trap_root.get("Properties", {})
    weapon_stat_handle = properties.get("WeaponStatHandle", {})
    row_name = weapon_stat_handle.get("RowName")
    if not isinstance(row_name, str) or not row_name.strip():
        raise ValueError("Trap has no WeaponStatHandle.RowName")

    trap_rows = _first_obj(_read_json(TRAPS_TABLE_PATH)).get("Rows", {})
    row = trap_rows.get(row_name)
    if not isinstance(row, dict):
        raise ValueError(f"Traps.json row not found: {row_name}")

    return TrapBaseStats(
        row_name=row_name,
        fire_delay=float(row.get("FireDelay", 0.0) or 0.0),
        arming_delay=float(row.get("ArmTime", row.get("FireDelay", 0.0)) or 0.0),
        reload_time=float(row.get("ReloadTime", 0.0) or 0.0),
        damage_pb=float(row.get("DmgPB", 0.0) or 0.0),
        impact_damage_pb=float(row.get("ImpactDmgPB", 0.0) or 0.0),
        crit_chance=float(row.get("DiceCritChance", 0.0) or 0.0),
        crit_damage_bonus=float(row.get("DiceCritDamageMultiplier", 0.0) or 0.0),
        stun_time=float(row.get("StunTime", 0.0) or 0.0),
        knockback_magnitude=float(row.get("KnockbackMagnitude", 0.0) or 0.0),
        durability=_resolve_base_durability(trap_root, row),
    )


def _resolve_ge_level(aid_path: Path, aid_properties: dict, alteration_json_map: dict[str, Path]) -> float:
    ability_set_ref = aid_properties.get("AlterationAbilitySet", {})
    ability_asset = str(ability_set_ref.get("AssetPathName", ""))
    ability_name = ability_asset.rsplit("/", 1)[-1].split(".", 1)[0]
    ability_path = alteration_json_map.get(ability_name.strip().lower())
    if ability_path is None:
        raise FileNotFoundError(
            f"Alteration ability-set export not found: {ability_name}.json (from {aid_path.name})"
        )
    ability_root = _first_obj(_read_json(ability_path))
    granted = ability_root.get("Properties", {}).get("GrantedGameplayEffects", [])
    if not isinstance(granted, list) or not granted:
        return 0.0
    first_ge = granted[0]
    if not isinstance(first_ge, dict):
        return 0.0
    return float(first_ge.get("Level", 0.0) or 0.0)


def _load_alteration_effect(
    aid_token: str,
    aid_map: dict[str, Path],
    combat_rows: dict,
    alteration_json_map: dict[str, Path],
    aid_combat_row_mapping: dict[str, str],
) -> AlterationEffect:
    aid_name = _norm_aid_token(aid_token)
    aid_path = aid_map.get(aid_name)
    if aid_path is None:
        raise FileNotFoundError(f"AID file not found for token: {aid_token}")

    aid_root = _first_obj(_read_json(aid_path))
    aid_properties = aid_root.get("Properties", {})
    ge_level = _resolve_ge_level(aid_path, aid_properties, alteration_json_map)
    label = str(aid_properties.get("ItemName", {}).get("SourceString", aid_name)).strip() or aid_name
    description = str(aid_properties.get("ItemDescription", {}).get("SourceString", "")).strip()
    rarity_key = ""
    for item in aid_properties.get("DataList", []):
        if isinstance(item, dict) and isinstance(item.get("Rarity"), str):
            rarity_key = item["Rarity"]
            break

    damage_mult = 1.0
    reload_speed_mult = 1.0
    crit_chance_bonus = 0.0
    crit_damage_bonus = 0.0
    durability_mult = 1.0

    # Get the combat row name for this AID
    combat_row_name = aid_combat_row_mapping.get(aid_name)

    if combat_row_name and ge_level > 0.0:
        combat_value = _interpolate_curve(
            _require_combat_row(combat_rows, combat_row_name, aid_name),
            ge_level,
        )

        # Determine which attribute this AID affects based on the row name
        if "reloadspeed" in combat_row_name.lower():
            reload_speed_mult = combat_value
        elif "durability" in combat_row_name.lower():
            durability_mult = combat_value
        elif "critdamage" in combat_row_name.lower():
            crit_damage_bonus = combat_value
        elif "critrating" in combat_row_name.lower():
            crit_chance_bonus = combat_value
        elif "damage" in combat_row_name.lower() or "armor" in combat_row_name.lower():
            # Damage, ElementalDamage, PhysicalDamage, etc.
            damage_mult = combat_value

    primary_effect_value = 1.0
    if reload_speed_mult != 1.0:
        primary_effect_value = reload_speed_mult
    elif durability_mult != 1.0:
        primary_effect_value = durability_mult
    elif crit_damage_bonus != 0.0:
        primary_effect_value = crit_damage_bonus
    elif crit_chance_bonus != 0.0:
        primary_effect_value = crit_chance_bonus
    elif damage_mult != 1.0:
        primary_effect_value = damage_mult

    return AlterationEffect(
        slot_index=-1,
        slot_definition_row="",
        rarity_key=rarity_key,
        aid_token=aid_token,
        label=label,
        description=description,
        primary_effect_value=primary_effect_value,
        damage_mult=damage_mult,
        reload_speed_mult=reload_speed_mult,
        crit_chance_bonus=crit_chance_bonus,
        crit_damage_bonus=crit_damage_bonus,
        durability_mult=durability_mult,
    )


def _load_slot_options(
    loadout_row_name: str,
    aid_map: dict[str, Path],
    combat_rows: dict,
    supports_effect_duration: bool,
    alteration_json_map: dict[str, Path],
    aid_combat_row_mapping: dict[str, str],
) -> list[list[AlterationEffect]]:
    slot_loadouts = _first_obj(_read_json(ALTERATION_V2_DIR / "SlotLoadouts.json")).get("Rows", {})
    slot_defs = _first_obj(_read_json(ALTERATION_V2_DIR / "SlotDefs.json")).get("Rows", {})
    alteration_groups = _first_obj(_read_json(ALTERATION_V2_DIR / "AlterationGroups.json")).get("Rows", {})

    loadout = slot_loadouts.get(loadout_row_name)
    if not isinstance(loadout, dict):
        raise ValueError(f"SlotLoadout row not found: {loadout_row_name}")

    slot_options: list[list[AlterationEffect]] = []
    for slot_index, slot in enumerate(loadout.get("AlterationSlots", [])):
        if not isinstance(slot, dict):
            continue
        slot_def_row = str(slot.get("SlotDefinitionRow", "")).strip()
        slot_def = slot_defs.get(slot_def_row)
        if not isinstance(slot_def, dict):
            continue

        group_name = str(slot_def.get("InitTierGroup", "")).strip()
        group = alteration_groups.get(group_name)
        if not isinstance(group, dict):
            continue

        options_for_slot: list[AlterationEffect] = []
        for rarity_key in RARITY_KEYS:
            rarity_mapping = group.get("RarityMapping", [])
            if not isinstance(rarity_mapping, list):
                continue
            for rarity_entry in rarity_mapping:
                if not isinstance(rarity_entry, dict):
                    continue
                if rarity_entry.get("Key") != rarity_key:
                    continue
                weight_data = rarity_entry.get("Value", {}).get("WeightData", [])
                if not isinstance(weight_data, list):
                    continue
                for item in weight_data:
                    if not isinstance(item, dict):
                        continue
                    aid_token = str(item.get("AID", "")).strip()
                    if not aid_token:
                        continue
                    aid_normalized = _norm_aid_token(aid_token)
                    # Check if this AID affects effect duration (skip if not supported)
                    aid_row_name = aid_combat_row_mapping.get(aid_normalized)
                    if aid_row_name and "effectduration" in aid_row_name.lower() and not supports_effect_duration:
                        continue
                    base_effect = _load_alteration_effect(aid_token, aid_map, combat_rows, alteration_json_map, aid_combat_row_mapping)
                    options_for_slot.append(
                        AlterationEffect(
                            slot_index=slot_index,
                            slot_definition_row=slot_def_row,
                            rarity_key=rarity_key,
                            aid_token=aid_token,
                            label=base_effect.label,
                            description=base_effect.description,
                            primary_effect_value=base_effect.primary_effect_value,
                            damage_mult=base_effect.damage_mult,
                            reload_speed_mult=base_effect.reload_speed_mult,
                            crit_chance_bonus=base_effect.crit_chance_bonus,
                            crit_damage_bonus=base_effect.crit_damage_bonus,
                            durability_mult=base_effect.durability_mult,
                        )
                    )
                break

        if not options_for_slot:
            raise ValueError(f"No T4/T5 options found for slot {slot_index} ({slot_def_row})")
        slot_options.append(options_for_slot)

    return slot_options


def _format_csv_perk(effect: AlterationEffect) -> str:
    rarity_label = RARITY_LABELS.get(effect.rarity_key, effect.rarity_key)
    text = effect.description or effect.label
    return f"{text} ({rarity_label})"


def _build_detailed_loadout_rows(
    slot_options: list[list[AlterationEffect]],
    stats: TrapBaseStats,
    trap_name: str,
    relevant_attributes: set[str],
) -> list[DetailedLoadoutRow]:
    rows: list[DetailedLoadoutRow] = []
    seen_perk_sets: set[tuple[str, ...]] = set()

    for loadout in product(*slot_options):
        loadout_perks = tuple(_format_csv_perk(effect) for effect in loadout)
        canonical_perks = tuple(sorted(loadout_perks))
        if canonical_perks in seen_perk_sets:
            continue
        seen_perk_sets.add(canonical_perks)

        damage_mult = 1.0
        reload_speed_mult = 1.0
        crit_chance = stats.crit_chance
        crit_damage_bonus = stats.crit_damage_bonus
        durability_mult = 1.0

        for effect in loadout:
            damage_mult *= effect.damage_mult
            reload_speed_mult *= effect.reload_speed_mult
            crit_chance += effect.crit_chance_bonus
            crit_damage_bonus += effect.crit_damage_bonus
            durability_mult *= effect.durability_mult

        crit_chance = min(max(crit_chance, 0.0), 1.0)
        trap_reload_time = stats.reload_time / reload_speed_mult if reload_speed_mult > 0 else 0.0
        cycle_time = stats.fire_delay + trap_reload_time
        trap_impact = stats.impact_damage_pb * damage_mult
        trap_damage = (stats.damage_pb + stats.impact_damage_pb) * damage_mult
        eff_crit = _effective_crit_chance(crit_chance)
        crit_multiplier = 1 + eff_crit * crit_damage_bonus
        damage_per_shot = trap_damage * crit_multiplier
        damage_per_second = damage_per_shot / cycle_time if cycle_time > 0 else 0.0
        trap_durability = stats.durability * durability_mult
        total_trap_damage = damage_per_shot * trap_durability

        slot_values = tuple(effect.primary_effect_value for effect in loadout)

        rows.append(
            DetailedLoadoutRow(
                trap_name=trap_name,
                relevant_attributes=frozenset(relevant_attributes),
                perks=loadout_perks,
                slot_values=slot_values,
                damage_per_second=damage_per_second,
                damage_per_shot=damage_per_shot,
                total_trap_damage=total_trap_damage,
                trap_damage=trap_damage,
                trap_impact=trap_impact,
                trap_arming_delay=stats.arming_delay,
                trap_stun_time=stats.stun_time,
                trap_knockback=stats.knockback_magnitude,
                crit_chance=crit_chance,
                crit_damage_bonus_percent=crit_damage_bonus * 100.0,
                trap_durability=trap_durability,
                trap_reload_time=trap_reload_time,
            )
        )

    rows.sort(
        key=lambda item: (
            item.trap_name,
            -item.damage_per_second,
            -item.damage_per_shot,
            item.trap_reload_time,
            item.perks,
        )
    )
    return rows


def _default_csv_path() -> Path:
    return BASE_DIR / "all_trap_alterations.csv"


def _attribute_value(row: DetailedLoadoutRow, attribute_name: str) -> float | None:
    key = _norm_label(attribute_name)
    mapping: dict[str, float] = {
        "damage": row.trap_damage,
        "arming delay": row.trap_arming_delay,
        "reload duration": row.trap_reload_time,
        "crit chance": row.crit_chance,
        "crit damage": row.crit_damage_bonus_percent,
        "impact": row.trap_impact,
        "durability": row.trap_durability,
        "stun time": row.trap_stun_time,
        "knockback": row.trap_knockback,
    }
    return mapping.get(key)


def _write_csv(path: Path, rows: list[DetailedLoadoutRow], attribute_columns: list[str]) -> Path:
    headers = ["Trap"]
    for i in range(1, 7):
        headers.append(f"Slot {i}")
        headers.append(f"Slot {i} Value")
    headers.extend(attribute_columns)
    headers.extend(["Damage per Second", "Damage per Shot", "Total Trap Damage"])

    def format_de(value: float) -> str:
        return f"{value:.6f}".replace(".", ",")

    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(headers)
        for row in rows:
            perks = list(row.perks)
            while len(perks) < 6:
                perks.append("")

            row_data = [row.trap_name]
            for i in range(6):
                row_data.append(perks[i])
                if i < len(row.slot_values):
                    row_data.append(format_de(row.slot_values[i]))
                else:
                    row_data.append("")

            attr_cells: list[str] = []
            for attr_name in attribute_columns:
                if attr_name not in row.relevant_attributes:
                    attr_cells.append("")
                    continue
                value = _attribute_value(row, attr_name)
                attr_cells.append(format_de(value) if isinstance(value, float) else "")

            has_damage = "Damage" in row.relevant_attributes
            writer.writerow(
                row_data
                + attr_cells
                + [
                    format_de(row.damage_per_second) if has_damage else "",
                    format_de(row.damage_per_shot) if has_damage else "",
                    format_de(row.total_trap_damage) if has_damage else "",
                ]
            )

    return path


def main() -> int:
    attribute_columns = _attribute_column_order()
    trap_token_index = _build_trap_token_index()

    combat_rows = _first_obj(_read_json(COMBAT_EFFECT_MAGNITUDE_PATH)).get("Rows", {})
    if not isinstance(combat_rows, dict):
        raise ValueError("CombatEffectMagnitude rows are missing")

    aid_map = _build_aid_file_map()
    alteration_json_map = _build_alteration_json_map()
    exported_json_map = _build_exported_json_map()

    # Build attribute-to-CombatRow mapping from parent blueprints
    attribute_combat_row_mapping = _build_combat_row_mapping()

    # Build AID-to-attribute mapping from directory structure
    aid_to_attribute_mapping = _build_aid_to_attribute_mapping()

    # Combine: AID -> Attribute -> CombatRow
    aid_combat_row_mapping: dict[str, str] = {}
    for aid_name, attribute_name in aid_to_attribute_mapping.items():
        row_name = attribute_combat_row_mapping.get(attribute_name)
        if row_name:
            aid_combat_row_mapping[aid_name] = row_name

    csv_rows: list[DetailedLoadoutRow] = []
    missing_traps: list[str] = []

    for trap_name, attributes in RELEVANT_TRAP_ATTRIBUTES_EN:
        trap_token = _resolve_trap_token(trap_name, trap_token_index)
        if not trap_token:
            missing_traps.append(trap_name)
            continue

        trap_root, _ = _resolve_trap_definition(trap_token)
        trap_properties = trap_root.get("Properties", {})
        loadout_row_name = str(trap_properties.get("AlterationSlotsLoadoutRow", "")).strip()
        if not loadout_row_name:
            continue

        stats = _resolve_trap_base_stats(trap_root)
        supports_effect_duration = _trap_supports_effect_duration(trap_root, exported_json_map)
        slot_options = _load_slot_options(
            loadout_row_name,
            aid_map,
            combat_rows,
            supports_effect_duration,
            alteration_json_map,
            aid_combat_row_mapping,
        )
        csv_rows.extend(
            _build_detailed_loadout_rows(
                slot_options,
                stats,
                trap_name=trap_name,
                relevant_attributes=set(attributes),
            )
        )

    csv_path = _default_csv_path()
    _write_csv(csv_path, csv_rows, attribute_columns)

    if missing_traps:
        print("Warning: trap definitions not found for:")
        for name in missing_traps:
            print(f"- {name}")
    print(f"Written: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
