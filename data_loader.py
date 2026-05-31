"""
Loads and resolves Fortnite STW Collection Book data from game JSON exports.
Data hierarchy: Category → Page → Section → Slot (with resolved item name)
"""

import json
import logging
import re
from pathlib import Path

_log = logging.getLogger(__name__)

# Rarity code → English label (translated at runtime via _loc)
_RARITY_EN: dict[str, str] = {
    "C":   "Common",
    "UC":  "Uncommon",
    "R":   "Rare",
    "VR":  "Epic",
    "SR":  "Legendary",
    "MYT": "Mythic",
}
# Rarity code → CSS class (language-independent)
_RARITY_CSS: dict[str, str] = {
    "C":   "rarity-c",
    "UC":  "rarity-uc",
    "R":   "rarity-r",
    "VR":  "rarity-vr",
    "SR":  "rarity-sr",
    "MYT": "rarity-myt",
}

# EFortRarity → internal rarity code (used to override the slot-key rarity)
_EFORT_RARITY_MAP: dict[str, str] = {
    "EFortRarity::Common":       "C",
    "EFortRarity::Uncommon":     "UC",
    "EFortRarity::Rare":         "R",
    "EFortRarity::Epic":         "VR",
    "EFortRarity::Legendary":    "SR",
    "EFortRarity::Mythic":       "MYT",
    "EFortRarity::Transcendent": "MYT",
    "EFortRarity::Unattainable": "MYT",
}
_RARITY_RE = re.compile(r'\.(C|UC|R|VR|SR)\.(?:\w+\.)?T\d+$', re.IGNORECASE)


def _parse_rarity(slot_key: str, lang: str = "en") -> tuple[str, str]:
    """Extract (label, css_class) from a slot key, e.g. 'DID.X.Basic.SR.T01' → ('Epic', 'rarity-sr')."""
    m = _RARITY_RE.search(slot_key)
    if m:
        code = m.group(1).upper()
        return (_loc(_RARITY_EN.get(code, code), lang), _RARITY_CSS.get(code, ""))
    return ("", "")

BASE_DIR = Path(__file__).parent
CONTENT_DIR = (
    BASE_DIR
    / "FortniteGame"
    / "Plugins"
    / "GameFeatures"
    / "SaveTheWorld"
    / "Content"
)
CB_DATA_DIR = CONTENT_DIR / "CollectionBook" / "Data"

# BRCosmetics lives in a separate plugin/content directory next to SaveTheWorld
BR_CONTENT_DIR = BASE_DIR / "FortniteGame" / "Plugins" / "GameFeatures" / "BRCosmetics" / "Content"

# Base game content (FortniteGame/Content) – used for /Game/ prefixed assets not in SaveTheWorld
GAME_CONTENT_DIR = BASE_DIR / "FortniteGame" / "Content"
GAME_LOC_DIR = GAME_CONTENT_DIR / "Localization"

# Ordered list of (url_prefix, content_dir) to search for icon assets.
# /Game/ is checked against SaveTheWorld first (most assets), then the base game Content folder.
_ICON_ROOTS: list[tuple[str, Path]] = [
    ("/SaveTheWorld/", CONTENT_DIR),
    ("/BRCosmetics/",  BR_CONTENT_DIR),
    ("/Game/",         CONTENT_DIR),
    ("/Game/",         GAME_CONTENT_DIR),
]

_cache: dict = {}
_item_data_cache: dict[tuple, dict] = {}

# --- Localization ---
_LOC_DIR = CONTENT_DIR / "Localization" / "SaveTheWorld"
# Game-wide localization chunks that supplement STW strings (e.g. ItemCategories set-bonus labels)
# locchunk10 contains rarity labels (CommonText, UncommonText, RareText, EpicText, LegendaryText, MythicText)
_GAME_LOC_CHUNKS = ["Fortnite_locchunk10", "Fortnite_locchunk20"]

AVAILABLE_LANGS: list[str] = sorted(
    d.name for d in _LOC_DIR.iterdir()
    if d.is_dir() and (d / "SaveTheWorld.json").exists()
) if _LOC_DIR.exists() else ["en"]

_translations: dict[str, dict[str, str]] = {}


def _merge_loc_file(en_file: Path, target_file: Path, mapping: dict[str, str]) -> None:
    """Merge English→target string pairs from a localization file pair into mapping."""
    if not en_file.exists() or not target_file.exists():
        return
    with open(en_file, encoding="utf-8") as f:
        loc_en = json.load(f)
    with open(target_file, encoding="utf-8") as f:
        loc_tgt = json.load(f)
    for ns, entries in loc_en.items():
        if not isinstance(entries, dict):
            continue
        tgt_ns = loc_tgt.get(ns, {})
        for key, en_val in entries.items():
            tgt_val = tgt_ns.get(key)
            if isinstance(en_val, str) and isinstance(tgt_val, str) and en_val and tgt_val:
                if en_val not in mapping:
                    mapping[en_val] = tgt_val


def _build_translation(lang: str) -> dict[str, str]:
    """Build a flat English → target-language lookup from all relevant localization files."""
    if lang == "en":
        return {}
    mapping: dict[str, str] = {}
    _merge_loc_file(
        _LOC_DIR / "en" / "SaveTheWorld.json",
        _LOC_DIR / lang / "SaveTheWorld.json",
        mapping,
    )
    for chunk in _GAME_LOC_CHUNKS:
        _merge_loc_file(
            GAME_LOC_DIR / chunk / "en" / f"{chunk}.json",
            GAME_LOC_DIR / chunk / lang / f"{chunk}.json",
            mapping,
        )
    return mapping


def _get_translation(lang: str) -> dict[str, str]:
    if lang not in _translations:
        _translations[lang] = _build_translation(lang)
    return _translations[lang]


def _loc(en_term: str, lang: str = "en") -> str:
    """Translate an English game term to the target language."""
    if lang == "en":
        return en_term
    return _get_translation(lang).get(en_term, en_term)


def localize_term(en_term: str, lang: str = "en") -> str:
    """Public wrapper for translating English STW terms via loaded localization data."""
    return _loc(en_term, lang)


# Manager asset type names that differ from their EN localization string
_MANAGER_TYPE_EN: dict[str, str] = {
    "MartialArtist": "Martial Artist",
    "Trainer":       "Personal Trainer",
}

def _loc_manager_type(manager_type: str, lang: str = "en") -> str:
    """Translate a manager type extracted from an asset name (e.g. 'MartialArtist')."""
    en_name = _MANAGER_TYPE_EN.get(manager_type, manager_type)
    return _loc(en_name, lang)

# Slot-key regex for basic workers: worker_C_Adventurous
_WORKER_SLOT_RE = re.compile(r'^worker_(C|UC|R|VR|SR)_(\w+)$', re.IGNORECASE)
# Slot-key regex for type name lookup: ManagerDoctor.*
_MANAGER_TYPE_RE = re.compile(r'^Manager(\w+)\.', re.IGNORECASE)
# Generic worker/manager assets that have no FixedPortrait (icon comes from slot-key logic)
_GENERIC_PORTRAIT_RE = re.compile(r'^(WorkerBasic_(C|UC|R|VR|SR)|Manager\w+_(C|UC|R|VR))_T\d+$')

# Lead-slot expansion constants
_LEAD_PAGE_ID = "pagePeople_Leads"
_UNIQUE_LEAD_PAGE_ID = "pagePeople_UniqueLeads"
_LEAD_PERSONALITIES = [
    "Adventurous", "Analytical", "Competitive", "Cooperative",
    "Curious", "Dependable", "Dreamer", "Pragmatic",
]
_LEAD_GENDERS = [("M", "Male"), ("F", "Female")]

# Survivor-slot expansion constants
_SURVIVOR_PAGE_ID = "pagePeople_Survivors"
_HALLOWEEN_WORKERS_PAGE_ID = "PageSpecial_Halloween2017_Workers"
_WORKER_PORTRAITS = ["M01", "M02", "M03", "F01", "F02", "F03"]
# Slot-key regex for Halloween event workers: Worker.Halloween.Husk.C.T01
_HALLOWEEN_WORKER_SLOT_RE = re.compile(r'^Worker\.Halloween\.(\w+)\.(C|UC|R|VR|SR)\.T\d+$', re.IGNORECASE)
# Set-bonus keys, labels and icons are loaded dynamically from ItemCategories.json


def _set_bonus_label(key: str, lang: str) -> str:
    """Return the localized label for a worker set-bonus key."""
    data = _get_set_bonus_data().get(key, {})
    return _resolve_name({"Key": data.get("loc_key", ""), "SourceString": data.get("en_string", key)}, lang)


def _load_json(path: Path) -> list:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# --- Set-bonus metadata (loaded from ItemCategories.json) ---

_set_bonus_data_cache: dict | None = None


def _get_set_bonus_data() -> dict[str, dict]:
    """Load and cache set-bonus metadata from ItemCategories.json.

    Returns an ordered dict: {sb_key → {"en_string": str, "icon_asset": str}}.
    """
    global _set_bonus_data_cache
    if _set_bonus_data_cache is not None:
        return _set_bonus_data_cache
    result: dict[str, dict] = {}
    item_cats_file = GAME_CONTENT_DIR / "Items" / "ItemCategories.json"
    if item_cats_file.exists():
        _extract_set_bonuses_from(_load_json(item_cats_file), result)
    _set_bonus_data_cache = result
    return result


def _extract_set_bonuses_from(obj: object, result: dict) -> None:
    """Recursively extract Worker.SetBonus entries from an ItemCategories data structure."""
    if isinstance(obj, dict):
        tags = obj.get("TagContainer", [])
        if isinstance(tags, list):
            for tag in tags:
                if "Worker.SetBonus." in tag:
                    sb_key = tag.rsplit(".", 1)[-1]
                    if sb_key not in result:
                        name_obj = obj.get("CategoryName", {})
                        icon_path = (
                            obj.get("CategoryBrush", {})
                            .get("Brush_XXS", {})
                            .get("ResourceObject", {})
                            .get("ObjectPath", "")
                        )
                        result[sb_key] = {
                            "en_string": name_obj.get("SourceString", sb_key),
                            "loc_key": name_obj.get("Key", ""),
                            "icon_asset": icon_path,
                        }
        for v in obj.values():
            _extract_set_bonuses_from(v, result)
    elif isinstance(obj, list):
        for item in obj:
            _extract_set_bonuses_from(item, result)


def _set_bonus_icon_url(key: str) -> str:
    """Return the icon URL for a set-bonus key, resolved from ItemCategories.json."""
    asset = _get_set_bonus_data().get(key, {}).get("icon_asset", "")
    return _icon_asset_to_url(asset) if asset else ""


# --- Personality metadata (loaded from ItemCategories.json) ---

_personality_data_cache: dict | None = None


def _get_personality_data() -> dict[str, dict]:
    """Load and cache personality metadata from ItemCategories.json.

    Returns an ordered dict: {p_key → {"en_string": str, "icon_asset": str}}.
    e.g. {"IsAdventurous": {"en_string": "Adventurous", "icon_asset": "/Game/..."}}
    """
    global _personality_data_cache
    if _personality_data_cache is not None:
        return _personality_data_cache
    result: dict[str, dict] = {}
    item_cats_file = GAME_CONTENT_DIR / "Items" / "ItemCategories.json"
    if item_cats_file.exists():
        _extract_personalities_from(_load_json(item_cats_file), result)
    _personality_data_cache = result
    return result


def _extract_personalities_from(obj: object, result: dict) -> None:
    """Recursively extract Worker.Personality entries from an ItemCategories structure."""
    if isinstance(obj, dict):
        tags = obj.get("TagContainer", [])
        if isinstance(tags, list):
            for tag in tags:
                if "Worker.Personality." in tag:
                    p_key = tag.rsplit(".", 1)[-1]
                    if p_key not in result:
                        name_obj = obj.get("CategoryName", {})
                        icon_path = (
                            obj.get("CategoryBrush", {})
                            .get("Brush_XXS", {})
                            .get("ResourceObject", {})
                            .get("ObjectPath", "")
                        )
                        result[p_key] = {
                            "en_string": name_obj.get("SourceString", p_key),
                            "loc_key": name_obj.get("Key", ""),
                            "icon_asset": icon_path,
                        }
        for v in obj.values():
            _extract_personalities_from(v, result)
    elif isinstance(obj, list):
        for item in obj:
            _extract_personalities_from(item, result)


def _personality_badge_icon_url(key: str) -> str:
    """Return the small badge icon URL for a personality key from ItemCategories.json."""
    asset = _get_personality_data().get(key, {}).get("icon_asset", "")
    return _icon_asset_to_url(asset) if asset else ""


def _personality_label(key: str, lang: str) -> str:
    """Return the localized label for a personality key."""
    data = _get_personality_data().get(key, {})
    return _resolve_name({"Key": data.get("loc_key", ""), "SourceString": data.get("en_string", key)}, lang)


def _get_cb_rows(filename: str) -> dict:
    data = _load_json(CB_DATA_DIR / filename)
    return data[0]["Rows"]


def _resolve_name(obj: dict, lang: str = "en") -> str:
    """Extract the display name, using the localization Key if available."""
    key = obj.get("Key")
    if isinstance(key, str) and key.strip():
        loc_maps = _get_loc_key_maps()
        lang_map = loc_maps.get(lang, {})
        hit = lang_map.get(key.strip())
        if hit:
            return hit
    source = obj.get("SourceString", "")
    if not source:
        return obj.get("LocalizedString", "")
    return _loc(source, lang)


def _workers_icons_dirs() -> list[Path]:
    """Return ordered list of directories to search for worker/leader portrait icons."""
    return [
        CONTENT_DIR / "UI" / "Foundation" / "Textures" / "Icons" / "Workers",
        GAME_CONTENT_DIR / "UI" / "Foundation" / "Textures" / "Icons" / "Workers",
    ]


def _find_workers_icon(name: str) -> Path | None:
    """Search for an icon by filename across all workers icon directories.

    Tries the -L (large) variant first, then falls back to the plain version.
    Searches SaveTheWorld content first, then FortniteGame/Content.
    """
    base, ext = name.rsplit(".", 1)
    candidates = [f"{base}-L.{ext}", name]
    for candidate in candidates:
        for d in _workers_icons_dirs():
            p = d / candidate
            if p.exists():
                return p
    return None


def _personality_icon_url(personality: str) -> str:
    """Return the M01 portrait URL for a given worker personality."""
    name = f"T-Icon-Workers-Portrait-Worker-{personality}-M01.png"
    path = _find_workers_icon(name)
    if path:
        return _png_file_to_url(path)
    return ""


def _worker_portrait_url(personality: str, variant: str) -> str:
    """Return portrait URL for a specific personality + variant (e.g. 'Adventurous', 'M01').
    Uses the small version (no -L suffix) for use in compact table headers.
    """
    name = f"T-Icon-Workers-Portrait-Worker-{personality}-{variant}.png"
    # Search all workers icon dirs, prefer exact name (no -L) for compact display
    for d in _workers_icons_dirs():
        p = d / name
        if p.exists():
            return _png_file_to_url(p)
    return ""


def _manager_type_icon_url(manager_type: str) -> str:
    """Return the Female portrait URL for a generic manager type."""
    name = f"T-Icon-Leaders-Portrait-{manager_type}-Female.png"
    path = _find_workers_icon(name)
    if path:
        return _png_file_to_url(path)
    return ""


def _manager_gender_icon_url(manager_type: str, gender_key: str) -> str:
    """Return portrait URL for a manager type + gender ('M' or 'F')."""
    gender_label = "Male" if gender_key == "M" else "Female"
    name = f"T-Icon-Leaders-Portrait-{manager_type}-{gender_label}.png"
    path = _find_workers_icon(name)
    if path:
        return _png_file_to_url(path)
    return ""


def _asset_to_json_file(asset_path: str) -> Path | None:
    """Convert a /SaveTheWorld/... AssetPathName to a local JSON file path."""
    if not asset_path.startswith("/SaveTheWorld/"):
        return None
    package = asset_path[len("/SaveTheWorld/"):].rsplit(".", 1)[0]
    return (CONTENT_DIR / package).with_suffix(".json")


_CRAFTING_RECIPES_FILE = CONTENT_DIR / "Items" / "DataTables" / "CraftingRecipes_New.json"
_GAME_BASE_DIR = BASE_DIR / "FortniteGame"


def _get_crafting_recipes() -> dict[str, dict]:
    """Load and cache the CraftingRecipes_New.json rows."""
    if "crafting_recipes" not in _cache:
        if _CRAFTING_RECIPES_FILE.exists():
            _cache["crafting_recipes"] = _load_json(_CRAFTING_RECIPES_FILE)[0]["Rows"]
        else:
            _log.warning("CraftingRecipes_New.json not found")
            _cache["crafting_recipes"] = {}
    return _cache["crafting_recipes"]


def _get_item_def_index() -> dict[str, Path]:
    """Build and cache a lowercase filename → Path index of all JSONs under FortniteGame/."""
    if "item_def_index" not in _cache:
        idx: dict[str, Path] = {}
        for f in _GAME_BASE_DIR.rglob("*.json"):
            idx[f.stem.lower()] = f
        _cache["item_def_index"] = idx
    return _cache["item_def_index"]


def _sid_to_item_def_file(asset_path: str, slot_type: str = "") -> Path | None:
    """For a schematic SID asset path, return the matching item definition JSON.

    Resolves via CraftingRecipe → CraftingRecipes_New.json → PrimaryAssetName → file lookup.
    """
    if "/Items/Schematics/" not in asset_path:
        return None

    package = asset_path[len("/SaveTheWorld/"):].rsplit(".", 1)[0]
    _, stem = package.rsplit("/", 1)
    if not stem.startswith("SID_"):
        return None

    if not slot_type:
        _log.warning("No slot_type for schematic '%s' – cannot resolve item definition", stem)
        return None

    # Read the SID JSON to get CraftingRecipe.RowName
    sid_json = _asset_to_json_file(asset_path)
    if not sid_json or not sid_json.exists():
        return None

    try:
        props = _load_json(sid_json)[0].get("Properties", {})
    except Exception:
        return None

    row_name = props.get("CraftingRecipe", {}).get("RowName", "")
    if not row_name:
        _log.warning("No CraftingRecipe.RowName in schematic '%s'", stem)
        return None

    # Look up the recipe in CraftingRecipes_New.json
    recipes = _get_crafting_recipes()
    recipe = recipes.get(row_name)
    if not recipe:
        _log.warning("Recipe '%s' not found in CraftingRecipes_New.json for schematic '%s'", row_name, stem)
        return None

    results = recipe.get("RecipeResults", [])
    if not results:
        return None

    asset_name = results[0].get("ItemPrimaryAssetId", {}).get("PrimaryAssetName", "")
    if not asset_name:
        return None

    # Find the definition file by name in the index
    return _get_item_def_index().get(asset_name.lower())


def _png_file_to_url(path: Path) -> str:
    """Convert an absolute PNG path to a /gameicon/... URL (relative to its content root)."""
    for _, content_dir in _ICON_ROOTS:
        try:
            rel = path.relative_to(content_dir)
            # content_dir is e.g. .../GameFeatures/SaveTheWorld/Content
            # → plugin name is content_dir.parent.name ("SaveTheWorld" / "BRCosmetics")
            return "/gameicon/" + content_dir.parent.name + "/" + rel.as_posix()
        except ValueError:
            continue
    # Fallback: use path relative to BASE_DIR
    return "/gameicon/" + path.relative_to(BASE_DIR).as_posix()


# def _icon_asset_expected_paths(icon_asset_path: str) -> list[Path]:
#     """Return the filesystem paths that _icon_asset_to_url would search for a given asset path."""
#     stem = icon_asset_path.rsplit(".", 1)[0]  # strip UE instance suffix
#     paths: list[Path] = []
#     for prefix, content_dir in _ICON_ROOTS:
#         if stem.startswith(prefix):
#             rel_path = Path(stem[len(prefix):])
#             base = content_dir / rel_path.parent / rel_path.name
#             paths.append(Path(str(base) + "-L.png"))
#             paths.append(Path(str(base) + ".png"))
#     return paths


def _icon_asset_to_url(icon_asset_path: str, item_id: str = "") -> str:
    """Resolve an Icon AssetPathName to a /gameicon/... URL, or empty string."""
    stem = icon_asset_path.rsplit(".", 1)[0]  # strip UE instance suffix

    searched: list[str] = []
    for prefix, content_dir in _ICON_ROOTS:
        if stem.startswith(prefix):
            rel_path = Path(stem[len(prefix):])
            # Exact match
            exact = content_dir / rel_path.parent / (rel_path.name + ".png")
            if exact.exists():
                return _png_file_to_url(exact)
            searched.append(str(exact))
            # Fuzzy: file whose name starts with the stem (e.g. GenericWorker_128.png)
            candidates = sorted(
                (content_dir / rel_path.parent).glob(f"{rel_path.name}*.png")
            )
            if candidates:
                return _png_file_to_url(candidates[0])
    if searched:
        ctx = f" (item '{item_id}')" if item_id else ""
        _log.warning("Icon PNG not found%s: asset '%s', searched %s", ctx, icon_asset_path, searched)
    elif item_id:
        _log.warning("Icon asset path has no known prefix for '%s': '%s'", item_id, icon_asset_path)
    return ""


def _get_item_data(asset_path: str, lang: str = "en", slot_type: str = "") -> dict:
    """Return {name, icon_url, rarity_override, personality, gender} for a UE asset path. Results are cached per language."""
    cache_key = (asset_path, lang)
    if cache_key in _item_data_cache:
        return _item_data_cache[cache_key]

    asset_stem = asset_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    name = ""
    icon_url = ""
    rarity_override = ""
    personality = ""
    gender = ""

    # Determine which JSON to load based on item type prefix
    if asset_stem.startswith("SID_"):
        json_file = _sid_to_item_def_file(asset_path, slot_type)
        if not json_file:
            _log.warning("Item definition not found for schematic '%s'", asset_stem)
            json_file = _asset_to_json_file(asset_path)
    else:
        json_file = _asset_to_json_file(asset_path)

    props = {}
    if json_file and json_file.exists():
        try:
            props = _load_json(json_file)[0].get("Properties", {})
        except Exception:
            _log.warning("Error loading JSON '%s' for '%s'", json_file.name, asset_stem)
    elif json_file:
        _log.warning("Definition file not found for '%s': %s", asset_stem, json_file)

    # --- SID_ (Schematic → Weapon/Trap): name + icon from definition file ---
    if asset_stem.startswith("SID_"):
        name = _resolve_name(props.get("ItemName", {}), lang)
        icon_url = _extract_datalist_icon(props, asset_stem)
        rarity_override = _extract_datalist_rarity(props)

    # --- HID_ (Hero): name + icon from hero definition ---
    elif asset_stem.startswith("HID_"):
        name = _resolve_name(props.get("ItemName", {}), lang)
        icon_url = _extract_datalist_icon(props, asset_stem)
        rarity_override = _extract_datalist_rarity(props)

    # --- DID_ (Defender): name + icon from defender definition ---
    elif asset_stem.startswith("DID_"):
        name = _resolve_name(props.get("ItemName", {}), lang) or _resolve_name(props.get("DisplayName", {}), lang)
        icon_url = _extract_datalist_icon(props, asset_stem)
        rarity_override = _extract_datalist_rarity(props)

    # --- Manager (Lead Survivor): name from ItemName or SearchTags, icon from FixedPortrait ---
    elif asset_stem.startswith("Manager"):
        name = _resolve_name(props.get("ItemName", {}), lang)
        if not name:
            for entry in props.get("DataList", []):
                candidate = _resolve_name(entry.get("SearchTags", {}), lang)
                if candidate:
                    name = candidate
                    break
        if not _GENERIC_PORTRAIT_RE.match(asset_stem):
            icon_url = _extract_fixed_portrait_icon(props, asset_stem)
        if not icon_url:
            icon_url = _extract_datalist_icon(props, asset_stem)
        rarity_override = _extract_datalist_rarity(props)
        personality = _extract_personality(props)
        gender = _extract_gender(props)

    # --- Worker (Survivor): name from ItemName, icon from FixedPortrait ---
    elif asset_stem.startswith("Worker"):
        name = _resolve_name(props.get("ItemName", {}), lang)
        if not _GENERIC_PORTRAIT_RE.match(asset_stem):
            icon_url = _extract_fixed_portrait_icon(props, asset_stem)
        if not icon_url:
            icon_url = _extract_datalist_icon(props, asset_stem)
        rarity_override = _extract_datalist_rarity(props)
        personality = _extract_personality(props)
        gender = _extract_gender(props)

    else:
        _log.warning("Unknown item type prefix for '%s'", asset_stem)

    result = {
        "name": name or asset_stem,
        "icon_url": icon_url,
        "rarity_override": rarity_override,
        "personality": personality,
        "gender": gender,
    }
    _item_data_cache[cache_key] = result
    return result


def _extract_datalist_icon(props: dict, item_id: str = "") -> str:
    """Extract the first Icon AssetPathName from DataList entries."""
    for entry in props.get("DataList", []):
        icon_path = entry.get("Icon", {}).get("AssetPathName", "")
        if icon_path:
            url = _icon_asset_to_url(icon_path, item_id)
            if url:
                return url
            return ""  # _icon_asset_to_url already logged the details
    if item_id:
        _log.warning("No Icon.AssetPathName in DataList for '%s'", item_id)
    return ""


def _extract_datalist_rarity(props: dict) -> str:
    """Extract EFortRarity from the first DataList entry that has one."""
    for entry in props.get("DataList", []):
        efort = entry.get("Rarity", "")
        if efort:
            return _EFORT_RARITY_MAP.get(efort, "")
    return ""


def _extract_fixed_portrait_icon(props: dict, item_id: str = "") -> str:
    """Resolve icon from FixedPortrait → SmallImage (used by Workers/Managers)."""
    fp_path = props.get("FixedPortrait", {}).get("AssetPathName", "")
    if not fp_path:
        if item_id:
            _log.warning("No FixedPortrait.AssetPathName for '%s'", item_id)
        return ""
    fp_json = _asset_to_json_file(fp_path)
    if not fp_json or not fp_json.exists():
        if item_id:
            _log.warning("Portrait file not found for '%s': %s", item_id, fp_json or fp_path)
        return ""
    try:
        fp_data = _load_json(fp_json)
        small_img = fp_data[0].get("Properties", {}).get("SmallImage", {}).get("AssetPathName", "")
        if small_img:
            return _icon_asset_to_url(small_img, item_id)
        if item_id:
            _log.warning("No SmallImage in portrait '%s' for '%s'", fp_json.name, item_id)
    except Exception:
        if item_id:
            _log.warning("Error reading portrait '%s' for '%s'", fp_json.name, item_id)
    return ""


def _extract_personality(props: dict) -> str:
    """Extract personality from FixedPersonalityTag."""
    fp_tags = props.get("FixedPersonalityTag", [])
    if not fp_tags:
        return ""
    tag = fp_tags[0] if isinstance(fp_tags, list) else fp_tags
    part = tag.rsplit(".", 1)[-1]
    if part.startswith("Is"):
        part = part[2:]
    return part


def _extract_gender(props: dict) -> str:
    """Extract gender from EFortCustomGender field."""
    g_raw = props.get("Gender", "")
    return "M" if "Male" in g_raw else "F"


def get_collection_book(lang: str = "en") -> dict:
    """
    Returns the full collection book hierarchy, cached after first call.

    Structure:
    {
        "categories": [
            {
                "id": str,
                "name": str,
                "sort_priority": int,
                "pages": [
                    {
                        "id": str,
                        "name": str,
                        "category_id": str,
                        "sort_priority": int,
                        "sections": [
                            {
                                "id": str,
                                "name": str,
                                "slots": [
                                    {
                                        "id": str,
                                        "name": str,
                                        "source_id": str,
                                        "source_label": str,
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
        "pages": {page_id: page_entry},
    }
    """
    if f"data_{lang}" in _cache:
        return _cache[f"data_{lang}"]

    cat_rows = _get_cb_rows("CollectionCategoryTable.json")
    page_rows = _get_cb_rows("CollectionBookPages.json")
    sec_rows = _get_cb_rows("CollectionBookSections.json")
    slot_rows = _get_cb_rows("CollectionBookSlots.json")

    # Build categories
    categories: dict[str, dict] = {}
    for cat_id, cat in cat_rows.items():
        categories[cat_id] = {
            "id": cat_id,
            "name": _resolve_name(cat.get("Name", {}), lang) or cat_id,
            "sort_priority": cat.get("SortPriority", float("inf")),
            "pages": [],
        }

    # Build pages → sections → slots
    pages: dict[str, dict] = {}
    for page_id, page in page_rows.items():
        cat_id = page.get("CategoryId", "")

        sections = []
        for sec_key in page.get("SectionRowNames", []):
            sec = sec_rows.get(sec_key)
            if not sec:
                continue

            slots = []
            for slot_key in sec.get("SlotRowNames", []):
                slot = slot_rows.get(slot_key)
                if not slot:
                    continue

                allowed = slot.get("AllowedItems", [])
                slot_type = slot.get("SlotXpWeightName", "")
                name = ""
                icon_url = ""
                rarity_override = ""
                item_personality = ""
                item_gender = ""
                if allowed:
                    first_path = allowed[0].get("AssetPathName", "")
                    if first_path:
                        item_data = _get_item_data(first_path, lang, slot_type)
                        icon_url = item_data["icon_url"]
                        resolved = item_data["name"]
                        rarity_override = item_data["rarity_override"]
                        item_personality = item_data["personality"]
                        item_gender = item_data["gender"]
                        # Only use resolved name if it differs from raw asset stem
                        asset_stem = first_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                        if resolved != asset_stem:
                            name = resolved

                rarity_label, rarity_css = _parse_rarity(slot_key, lang)

                # Override rarity from item JSON when available (e.g. Mythic heroes have SR in slot key)
                if rarity_override:
                    rarity_label = _loc(_RARITY_EN.get(rarity_override, rarity_override), lang)
                    rarity_css = _RARITY_CSS.get(rarity_override, "")

                # Special case: generic leader slots on pagePeople_Leads have no EFortRarity
                # in their item JSON for the C tier, but the game displays them as Uncommon.
                if page_id == _LEAD_PAGE_ID and not rarity_override and rarity_css == "rarity-c":
                    rarity_label = _loc(_RARITY_EN["UC"], lang)
                    rarity_css = _RARITY_CSS["UC"]

                # --- Basic worker slots: worker_C_Adventurous ---
                worker_m = _WORKER_SLOT_RE.match(slot_key)
                if worker_m:
                    rarity_code = worker_m.group(1).upper()
                    rarity_label = _loc(_RARITY_EN.get(rarity_code, ""), lang)
                    rarity_css = _RARITY_CSS.get(rarity_code, "")
                    personality = worker_m.group(2)
                    if not name:
                        name = _loc(personality, lang)
                    # Always prefer personality portrait over generic worker icon
                    p_icon = _personality_icon_url(personality)
                    if p_icon:
                        icon_url = p_icon
                else:
                    # --- Generic manager name + icon: ManagerDoctor.C.T01 ---
                    mt = _MANAGER_TYPE_RE.match(slot_key)
                    if mt:
                        manager_type = mt.group(1)
                        if not name:
                            name = _loc_manager_type(manager_type, lang)
                        # Use type portrait only when no specific icon was resolved (generic managers)
                        if not icon_url or "Generic" in icon_url:
                            m_icon = _manager_type_icon_url(manager_type)
                            if m_icon:
                                icon_url = m_icon

                if not name:
                    name = rarity_label or slot_key

                slot_entry: dict = {
                    "slot_id": slot_key,
                    "name": name,
                    "icon_url": icon_url,
                    "rarity_label": rarity_label,
                    "rarity_css": rarity_css,
                }

                # Expand generic lead slots (pagePeople_Leads) into gender × personality variants
                if page_id == _LEAD_PAGE_ID:
                    mt_lead = _MANAGER_TYPE_RE.match(slot_key)
                    if mt_lead:
                        lead_type = mt_lead.group(1)
                        lead_genders = []
                        for gender_key, _ in _LEAD_GENDERS:
                            g_icon = _manager_gender_icon_url(lead_type, gender_key)
                            personalities = [
                                {
                                    "slot_id": f"{slot_key}.{gender_key}.{p}",
                                    "personality": _loc(p, lang),
                                    "personality_icon_url": _personality_badge_icon_url(f"Is{p}"),
                                }
                                for p in _LEAD_PERSONALITIES
                            ]
                            lead_genders.append({
                                "key": gender_key,
                                "icon_url": g_icon,
                                "personalities": personalities,
                            })
                        slot_entry["is_lead_slot"] = True
                        slot_entry["genders"] = lead_genders

                # Add personality/gender info for named Mythic leads (pagePeople_UniqueLeads)
                if page_id == _UNIQUE_LEAD_PAGE_ID:
                    if item_personality:
                        slot_entry["personality"] = _loc(item_personality, lang)
                        slot_entry["personality_icon_url"] = _personality_badge_icon_url(f"Is{item_personality}")
                    if item_gender:
                        slot_entry["gender"] = item_gender

                # Expand basic survivor slots (pagePeople_Survivors) into portrait × set-bonus variants
                if page_id == _SURVIVOR_PAGE_ID:
                    worker_sm = _WORKER_SLOT_RE.match(slot_key)
                    if worker_sm:
                        personality = worker_sm.group(2)
                        slot_entry["personality"] = _loc(personality, lang)
                        slot_entry["personality_icon_url"] = _personality_badge_icon_url(f"Is{personality}")
                        set_bonus_keys = list(_get_set_bonus_data().keys())
                        portraits = [
                            {
                                "key": v,
                                "icon_url": _worker_portrait_url(personality, v),
                                "set_bonuses": [
                                    {
                                        "slot_id": f"{slot_key}.{v}.{sb}",
                                        "label": _set_bonus_label(sb, lang),
                                    }
                                    for sb in set_bonus_keys
                                ],
                            }
                            for v in _WORKER_PORTRAITS
                        ]
                        slot_entry["is_survivor_slot"] = True
                        slot_entry["portraits"] = portraits
                        slot_entry["set_bonus_labels"] = [
                            _set_bonus_label(sb, lang) for sb in set_bonus_keys
                        ]
                        slot_entry["set_bonus_icons"] = [
                            _set_bonus_icon_url(sb) for sb in set_bonus_keys
                        ]

                # Expand Halloween event worker slots: personalities as columns, set-bonuses as rows
                if page_id == _HALLOWEEN_WORKERS_PAGE_ID:
                    hw_m = _HALLOWEEN_WORKER_SLOT_RE.match(slot_key)
                    if hw_m:
                        set_bonus_keys = list(_get_set_bonus_data().keys())
                        p_data = _get_personality_data()
                        portraits = [
                            {
                                "key": p_key,
                                "icon_url": _personality_badge_icon_url(p_key),
                                "label": _personality_label(p_key, lang),
                                "set_bonuses": [
                                    {
                                        "slot_id": f"{slot_key}.{p_key}.{sb}",
                                        "label": _set_bonus_label(sb, lang),
                                    }
                                    for sb in set_bonus_keys
                                ],
                            }
                            for p_key in p_data
                        ]
                        slot_entry["is_survivor_slot"] = True
                        slot_entry["wide_table"] = True
                        slot_entry["portraits"] = portraits
                        slot_entry["set_bonus_labels"] = [
                            _set_bonus_label(sb, lang) for sb in set_bonus_keys
                        ]
                        slot_entry["set_bonus_icons"] = [
                            _set_bonus_icon_url(sb) for sb in set_bonus_keys
                        ]

                slots.append(slot_entry)

            sections.append(
                {
                    "id": sec_key,
                    "name": _resolve_name(sec.get("Name", {}), lang) or sec_key,
                    "slots": slots,
                }
            )

        page_entry = {
            "id": page_id,
            "name": _resolve_name(page.get("Name", {}), lang) or page_id,
            "category_id": cat_id,
            "sort_priority": page.get("SortPriority", float("inf")),
            "sections": sections,
            "slot_count": sum(len(s["slots"]) for s in sections),
        }
        pages[page_id] = page_entry

        if cat_id in categories:
            categories[cat_id]["pages"].append(page_entry)

    # Sort pages within each category by sort priority
    for cat in categories.values():
        cat["pages"].sort(key=lambda p: p["sort_priority"])

    # Sort categories by sort priority
    sorted_cats = sorted(categories.values(), key=lambda c: c["sort_priority"])

    result = {"categories": sorted_cats, "pages": pages}
    _cache[f"data_{lang}"] = result
    return result


# ---------------------------------------------------------------------------
# Template-based name resolution (moved from get_data.py)
# ---------------------------------------------------------------------------

_loc_key_maps: dict[str, dict[str, str]] | None = None


def _get_loc_key_maps() -> dict[str, dict[str, str]]:
    """Build lang→{loc_key: string} maps from all locchunk files and STW localization."""
    global _loc_key_maps
    if _loc_key_maps is not None:
        return _loc_key_maps

    _loc_key_maps = {}

    def _merge(file_path: Path, target: dict[str, str]) -> None:
        try:
            with open(file_path, encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        for bucket in payload.values():
            if not isinstance(bucket, dict):
                continue
            for key, value in bucket.items():
                if isinstance(key, str) and isinstance(value, str) and key and value:
                    target[key] = value

    for lang in AVAILABLE_LANGS:
        target: dict[str, str] = {}
        # Global locchunks
        for chunk in _GAME_LOC_CHUNKS:
            lang_file = GAME_LOC_DIR / chunk / lang / f"{chunk}.json"
            if lang_file.exists():
                _merge(lang_file, target)
        # STW localization
        stw_lang_dir = _LOC_DIR / lang
        if stw_lang_dir.exists():
            for file_path in sorted(stw_lang_dir.glob("*.json")):
                _merge(file_path, target)
        _loc_key_maps[lang] = target

    return _loc_key_maps


_item_def_name_map: dict[str, dict[str, str]] | None = None


def _get_item_def_name_map() -> dict[str, dict[str, str]]:
    """Build template_token → {lang: name, "rarity": code} from STW item definition files."""
    global _item_def_name_map
    if _item_def_name_map is not None:
        return _item_def_name_map

    _item_def_name_map = {}
    loc_maps = _get_loc_key_maps()

    candidate_dirs = [CONTENT_DIR / "Heroes", CONTENT_DIR / "Items"]

    for base_dir in candidate_dirs:
        if not base_dir.exists():
            continue
        for file_path in base_dir.rglob("*.json"):
            try:
                with open(file_path, encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception:
                continue

            if not isinstance(payload, list) or not payload:
                continue
            first = payload[0]
            if not isinstance(first, dict):
                continue

            name_value = first.get("Name")
            if not isinstance(name_value, str) or not name_value:
                continue
            token = name_value.lower().strip()
            if not token:
                continue

            props = first.get("Properties")
            if not isinstance(props, dict):
                continue

            # Extract rarity
            def _extract_rarity(obj: object) -> str:
                if isinstance(obj, dict):
                    r = obj.get("Rarity")
                    if isinstance(r, str) and r.strip():
                        v = r.strip()
                        if "::" in v:
                            v = v.rsplit("::", 1)[-1]
                        return v.lower()
                    for nested in obj.values():
                        nr = _extract_rarity(nested)
                        if nr:
                            return nr
                elif isinstance(obj, list):
                    for nested in obj:
                        nr = _extract_rarity(nested)
                        if nr:
                            return nr
                return ""

            rarity_value = _extract_rarity(props)

            item_name = props.get("ItemName")
            if not isinstance(item_name, dict):
                continue

            key = item_name.get("Key")
            key_value = key.strip() if isinstance(key, str) else ""

            source_string = item_name.get("SourceString")
            localized_string = item_name.get("LocalizedString")
            fallback_name = ""
            if isinstance(source_string, str) and source_string.strip():
                fallback_name = source_string.strip()
            elif isinstance(localized_string, str) and localized_string.strip():
                fallback_name = localized_string.strip()

            entry: dict[str, str] = {"rarity": rarity_value}
            for lang in AVAILABLE_LANGS:
                lang_map = loc_maps.get(lang, {})
                entry[lang] = lang_map.get(key_value, fallback_name) if key_value else fallback_name

            if any(entry.get(lang) for lang in AVAILABLE_LANGS):
                _item_def_name_map[token] = entry

    return _item_def_name_map


def _name_alias_candidates(template_token: str) -> list[str]:
    """Build name-resolution aliases (e.g. SID_ → TID_/WID_)."""
    token = template_token.lower().strip()
    aliases: list[str] = []
    if token.startswith("sid_"):
        aliases.append("tid_" + token[4:])
        aliases.append("wid_" + token[4:])
    return aliases


def lookup_item_definition_name(template_id: str) -> dict[str, str] | None:
    """Resolve a template ID to localized names from item definition files.

    Returns {lang: name, "rarity": code} or None if not found.
    """
    token = template_id.lower().strip()
    if not token:
        return None
    name_map = _get_item_def_name_map()
    direct = name_map.get(token)
    if direct:
        return direct
    for alias in _name_alias_candidates(token):
        hit = name_map.get(alias)
        if hit:
            return hit
    return None
