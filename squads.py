#!/usr/bin/env python3
"""
squads.py

Reads survivor data and determines the optimal squad compositions to maximise
overall squad potential power while still preferring activated set bonuses
as tie-breakers.

The script writes two suggestions:
1) Potential mode (max potential manager/survivor power)
2) Current mode (same formula, but using actual item levels)

Rules
-----
- 8 squads, each with 1 manager (matching synergy) + 7 regular survivors.
- A survivor activates their set bonus only when their personality matches
  the squad manager's personality.
- Set bonuses require a minimum number of matching survivors per squad to
  activate (e.g. IsTrapDurabilityHigh needs 2).  Bonuses activate in
  multiples of the threshold (2 → 2 active, 4 → 4 active, 5 → 4 active).
- Squad assignment is chosen by potential power with this priority order:
    1. Tech      = Engineering + Inventor
    2. Offense   = Soldier + Martial Arts
    3. Total     = all squads combined
- Worker selection is power-first. Activated set bonuses are only used as
    tie-breakers between equal-power worker selections.
- Among bonus tie-breakers, active Trap Durability is ranked above every other
    activated bonus. Inactive trap bonuses are still not preferred.

Strategy
--------
All 8! = 40 320 personality→synergy permutations are evaluated.
For each personality, the worker composition is chosen power-first from the
available matching survivors. Across permutations, the chosen manager affects
both the squad's manager power and the grouped Tech / Offense / Total scoring.
"""

import argparse
import itertools
import json
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from rich import print as rprint

from data_loader import AVAILABLE_LANGS, _get_loc_key_maps, _get_set_bonus_data, _loc, get_collection_book
from get_data import (
    _build_name_map,
    _build_slot_display_name_map,
    _build_template_slot_map,
    _resolve_leader_slot_ids,
    _resolve_template_display_names,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

RESPONSE_FILE = Path(__file__).parent / "response.json"
RAW_DATA_DIR = Path(__file__).parent / "raw_data"
RAW_INVENTORY_FILE = "normalized_inventory.json"
OUTPUT_FILE = Path(__file__).parent / "squads.txt"
BACKUP_DIR = Path(__file__).parent / "backups"
TARGET_SET_BONUS = "Homebase.Worker.SetBonus.IsTrapDurabilityHigh"
SQUAD_SLOTS = 7  # worker slots per squad (excluding the manager)

# Resolved at startup via --lang argument (default: en)
OUTPUT_LANG: str = "en"

# English labels for personality/synergy keys (fed through _loc for translation)
_PERSONALITY_EN: dict[str, str] = {
    "IsAdventurous": "Adventurous",
    "IsAnalytical":  "Analytical",
    "IsCompetitive": "Competitive",
    "IsCooperative": "Cooperative",
    "IsCurious":     "Curious",
    "IsDependable":  "Dependable",
    "IsDreamer":     "Dreamer",
    "IsPragmatic":   "Pragmatic",
}

_SYNERGY_EN: dict[str, str] = {
    "IsDoctor":        "Doctor",
    "IsEngineer":      "Engineer",
    "IsExplorer":      "Explorer",
    "IsGadgeteer":     "Gadgeteer",
    "IsInventor":      "Inventor",
    "IsMartialArtist": "Martial Artist",
    "IsSoldier":       "Soldier",
    "IsTrainer":       "Personal Trainer",
}

SYNERGY_TYPES = list(_SYNERGY_EN)
PERSONALITIES = list(_PERSONALITY_EN)

RARITY_ORDER = {
    "Mythic": 5,
    "Legendary": 4,
    "Epic": 3,
    "Rare": 2,
    "Uncommon": 1,
    "Common": 0,
}

MANAGER_POTENTIAL_POWER = {
    "Mythic": 270,
    "Legendary": 238,
    "Epic": 208,
}

SURVIVOR_BASE_VALUE = {
    "Mythic": 25,
    "Legendary": 20,
    "Epic": 15,
    "Rare": 10,
    "Uncommon": 5,
    "Common": 1,
}

SURVIVOR_LEVEL_MULTIPLIER = {
    "Mythic": 1.645,
    "Legendary": 1.5,
    "Epic": 1.374,
    "Rare": 1.245,
    "Uncommon": 1.08,
    "Common": 1,
}

SURVIVOR_TIER_BONUS = {
    "Mythic": 9.85,
    "Legendary": 9,
    "Epic": 8,
    "Rare": 7,
    "Uncommon": 6.35,
    "Common": 5,
}

MATCHED_SURVIVOR_MANAGER_BONUS = {
    "Mythic": 8,
    "Legendary": 5,
    "Epic": 4,
    "None": 0,
}
_cb_context_cache: dict | None = None
_cb_context_lang: str | None = None

UNMATCHED_SURVIVOR_MANAGER_BONUS = {
    "Mythic": -2,
    "Legendary": 0,
    "Epic": 0,
    "None": 0,
}

SURVIVOR_LEVEL = 60

SET_BONUS_THRESHOLDS: dict[str, int] = {
    "IsTrapDurabilityHigh": 2,
    "IsFortitudeLow":       2,
    "IsResistanceLow":      2,
    "IsShieldRegenLow":     2,
    "IsAbilityDamageLow":   3,
    "IsMeleeDamageLow":     3,
    "IsRangedDamageLow":    3,
    "IsTrapDamageLow":      3,
}

# Squad labels: synergy key → localization key (from SaveTheWorld.json)
SQUAD_LABELS: dict[str, str] = {
    "IsDoctor":        "Squad_Attribute_Medicine_EMTSquad_DisplayName",
    "IsEngineer":      "Squad_Attribute_Synthesis_CorpsofEngineering_DisplayName",
    "IsExplorer":      "Squad_Attribute_Scavenging_ScoutingParty_DisplayName",
    "IsGadgeteer":     "Squad_Attribute_Scavenging_Gadgeteers_DisplayName",
    "IsInventor":      "Squad_Attribute_Synthesis_TheThinkTank_DisplayName",
    "IsMartialArtist": "Squad_Attribute_Arms_CloseAssaultSquad_DisplayName",
    "IsSoldier":       "Squad_Attribute_Arms_FireTeamAlpha_DisplayName",
    "IsTrainer":       "Squad_Attribute_Medicine_TrainingTeam_DisplayName",
}

# Set bonus labels loaded from game data (ItemCategories.json)
SET_BONUS_LABELS: dict[str, str] = {
    key: info["en_string"] for key, info in _get_set_bonus_data().items()
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def short(s: str) -> str:
    """Return the last segment after the final '.' (strips namespace prefix)."""
    return s.rsplit(".", 1)[-1] if s else ""

TARGET_BONUS_SHORT = short(TARGET_SET_BONUS)

def _loc_squad(synergy_key: str) -> str:
    """Translate a synergy key to the localized squad name via loc key."""
    loc_key = SQUAD_LABELS.get(synergy_key, "")
    if loc_key:
        hit = _get_loc_key_maps().get(OUTPUT_LANG, {}).get(loc_key)
        if hit:
            return hit
    return synergy_key


def _loc_personality(key: str) -> str:
    """Translate a personality key like 'IsAdventurous' to the output language."""
    return _loc(_PERSONALITY_EN.get(key, key.replace("Is", "")), OUTPUT_LANG)


def _loc_synergy(key: str) -> str:
    """Translate a synergy key like 'IsDoctor' to the output language."""
    return _loc(_SYNERGY_EN.get(key, key.replace("Is", "")), OUTPUT_LANG)


def _loc_rarity(rarity: str) -> str:
    """Translate a rarity label like 'Legendary' to the output language."""
    return _loc(rarity, OUTPUT_LANG)


def _ensure_manager_name_context() -> dict:
    """Lazily build and cache collection-book maps for EN + OUTPUT_LANG."""
    global _cb_context_cache, _cb_context_lang

    if _cb_context_cache is not None and _cb_context_lang == OUTPUT_LANG:
        return _cb_context_cache

    cb_en = get_collection_book("en")
    ctx: dict = {
        "template_map_en":  _build_template_slot_map(cb_en),
        "slot_name_map_en": _build_slot_display_name_map(cb_en),
        "name_map_en":      _build_name_map(cb_en),
    }
    if OUTPUT_LANG != "en":
        cb_loc = get_collection_book(OUTPUT_LANG)
        ctx["template_map_loc"]  = _build_template_slot_map(cb_loc)
        ctx["slot_name_map_loc"] = _build_slot_display_name_map(cb_loc)
    else:
        ctx["template_map_loc"]  = ctx["template_map_en"]
        ctx["slot_name_map_loc"] = ctx["slot_name_map_en"]

    _cb_context_cache = ctx
    _cb_context_lang = OUTPUT_LANG
    return ctx


def _resolve_manager_display_name(manager: dict) -> str:
    explicit_name = manager.get("name")
    if isinstance(explicit_name, str) and explicit_name.strip():
        return explicit_name

    ctx = _ensure_manager_name_context()
    template_id = manager.get("templateId", "")
    token = ""
    if isinstance(template_id, str) and template_id:
        token = template_id.split(":", 1)[-1].lower().strip()

    slot_ids, _ = _resolve_leader_slot_ids(
        manager, ctx["name_map_en"], ctx["template_map_en"]
    )
    if slot_ids:
        for slot_id in slot_ids:
            localized = ctx["slot_name_map_loc"].get(slot_id)
            if localized:
                return localized
        for slot_id in slot_ids:
            fallback = ctx["slot_name_map_en"].get(slot_id)
            if fallback:
                return fallback

    if token:
        en_name, loc_name, _ = _resolve_template_display_names(
            token,
            ctx["template_map_en"],
            ctx["slot_name_map_en"],
            ctx["template_map_loc"],
            ctx["slot_name_map_loc"],
        )
        if OUTPUT_LANG != "en" and loc_name and loc_name != "-":
            return loc_name
        if en_name and en_name != "-":
            return en_name

    if isinstance(template_id, str) and template_id:
        return template_id.split(":", 1)[-1]
    return "Manager"


def _rarity(s: dict) -> str:
    """Return the effective in-game rarity (starting_rarity takes precedence)."""
    return s.get("attributes", {}).get("starting_rarity") or s.get("rarity", "")


def _manager_potential_power(manager: dict) -> int:
    """Return a manager's max potential power based on effective rarity."""
    return MANAGER_POTENTIAL_POWER.get(_rarity(manager), 0)


def _item_level(item: dict, default: int = 1) -> int:
    """Return the current item level from attributes.level (fallback: starting_level)."""
    attrs = item.get("attributes", {})
    raw = attrs.get("level", attrs.get("starting_level", default))
    try:
        return max(int(raw), 1)
    except (TypeError, ValueError):
        return default


def _rarity_level_score(rarity: str, level: int) -> int:
    """Shared rarity/level score used by survivor and manager power formulas."""
    base_value = SURVIVOR_BASE_VALUE.get(rarity, 0)
    multiplier = SURVIVOR_LEVEL_MULTIPLIER.get(rarity, 0)
    tier_bonus = SURVIVOR_TIER_BONUS.get(rarity, 0)
    return round(
        base_value
        + ((level - 1) * multiplier)
        + (((level - 1) // 10) * tier_bonus)
    )


def _survivor_potential_power(survivor: dict, manager_rarity: str,
                              personality_match: bool = True) -> int:
    """Return a survivor's max potential power for level 60 under a manager."""
    rarity = _rarity(survivor)
    result = _rarity_level_score(rarity, SURVIVOR_LEVEL)

    if personality_match:
        result += MATCHED_SURVIVOR_MANAGER_BONUS.get(manager_rarity, 0)
    else:
        result += UNMATCHED_SURVIVOR_MANAGER_BONUS.get(manager_rarity, 0)

    return max(result, 0)


def _survivor_current_power(survivor: dict, manager_rarity: str,
                            personality_match: bool = True) -> int:
    """Return survivor power using actual survivor level and the same formula rules."""
    rarity = _rarity(survivor)
    level = _item_level(survivor)
    result = _rarity_level_score(rarity, level)

    if personality_match:
        result += MATCHED_SURVIVOR_MANAGER_BONUS.get(manager_rarity, 0)
    else:
        result += UNMATCHED_SURVIVOR_MANAGER_BONUS.get(manager_rarity, 0)

    return max(result, 0)


def _manager_current_power(manager: dict) -> int:
    """Return manager power using actual manager level and rarity-based scaling."""
    rarity = _rarity(manager)
    max_power = MANAGER_POTENTIAL_POWER.get(rarity, 0)
    if max_power <= 0:
        return 0

    baseline = _rarity_level_score(rarity, SURVIVOR_LEVEL)
    if baseline <= 0:
        return 0

    scale = max_power / baseline
    level_score = _rarity_level_score(rarity, _item_level(manager))
    return max(round(level_score * scale), 0)


def _power_level(s: dict, manager_rarity: str | None = None,
                 personality_match: bool = True,
                 mode: str = "potential") -> int:
    """Return power in selected mode: 'potential' or 'current'."""
    if mode == "current":
        if s.get("attributes", {}).get("managerSynergy"):
            return _manager_current_power(s)
        return _survivor_current_power(
            s,
            manager_rarity or "None",
            personality_match=personality_match,
        )

    if s.get("attributes", {}).get("managerSynergy"):
        return _manager_potential_power(s)

    return _survivor_potential_power(
        s,
        manager_rarity or "None",
        personality_match=personality_match,
    )


def _is_trap(w: dict) -> bool:
    return w["attributes"].get("set_bonus") == TARGET_SET_BONUS


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

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


def _resolve_input_file() -> tuple[Path, str]:
    """Return input file path and source label for survivor data."""
    if RESPONSE_FILE.exists():
        return RESPONSE_FILE, RESPONSE_FILE.name

    latest = _find_latest_raw_snapshot(RAW_DATA_DIR, RAW_INVENTORY_FILE)
    if latest:
        candidate = latest / RAW_INVENTORY_FILE
        return candidate, f"{latest.name}/{RAW_INVENTORY_FILE}"

    raise FileNotFoundError(
        "No survivor input found. Expected response.json or raw_data/<latest>/normalized_inventory.json"
    )

def load_survivors(path: Path) -> tuple[list, list]:
    """Return (managers, workers) lists from a normalized survivor payload."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    managers, workers = [], []
    for uid, s in data["survivors"].items():
        item = {**s, "uid": uid}
        if s.get("attributes", {}).get("managerSynergy"):
            managers.append(item)
        else:
            workers.append(item)
    return managers, workers


def build_mgr_index(managers: list, power_mode: str = "potential") -> dict:
    """Index: (synergy_short, personality_short) → [manager, …] sorted best-first."""
    idx: dict = defaultdict(list)
    for m in managers:
        s = short(m["attributes"]["managerSynergy"])
        p = short(m["attributes"].get("personality", ""))
        idx[(s, p)].append(m)
    for key in idx:
        idx[key].sort(
            key=lambda m: (_power_level(m, mode=power_mode), RARITY_ORDER.get(_rarity(m), 0)),
            reverse=True,
        )
    return idx


def build_worker_buckets(workers: list, power_mode: str = "potential") -> dict:
    """Group workers by personality, sorted by power descending."""
    buckets: dict = defaultdict(list)
    for w in workers:
        p = short(w["attributes"].get("personality", ""))
        buckets[p].append(w)
    for p in buckets:
        buckets[p].sort(
            key=lambda w: (_power_level(w, mode=power_mode), RARITY_ORDER.get(_rarity(w), 0)),
            reverse=True,
        )
    return buckets


# ---------------------------------------------------------------------------
# Optimiser – find best personality→synergy mapping
# ---------------------------------------------------------------------------

def find_optimal_assignment(mgr_idx: dict, worker_buckets: dict,
                            power_mode: str = "potential") -> tuple:
    """
    Brute-force over all 8! permutations.

    The permutation chooses which personality goes to each synergy, affecting both
    manager power and the workers' potential power because survivor bonuses depend
    on manager rarity.

    Priority order for choosing the best mapping:
      1) Maximise Engineering + Inventor squad power levels
      2) Then maximise Soldier + Martial Arts squad power levels
      3) Then maximise total squad power levels
    """
    mgr_power: dict[tuple, int] = {}
    mgr_rarity: dict[tuple, str] = {}
    for s in SYNERGY_TYPES:
        for p in PERSONALITIES:
            cands = mgr_idx.get((s, p), [])
            mgr_power[(s, p)] = _power_level(cands[0], mode=power_mode) if cands else -1
            mgr_rarity[(s, p)] = _rarity(cands[0]) if cands else "None"

    # Hot-path cache: squad composition depends only on personality + mode.
    squad_workers_by_personality: dict[str, list[dict]] = {
        p: compose_squad(p, worker_buckets, power_mode=power_mode)
        for p in PERSONALITIES
    }

    manager_rarities = {mr for mr in mgr_rarity.values() if mr != "None"}
    manager_rarities.add("None")

    worker_power_lookup: dict[tuple[str, str], int] = {}
    for p in PERSONALITIES:
        workers_for_p = squad_workers_by_personality[p]
        for mr in manager_rarities:
            worker_power_lookup[(p, mr)] = sum(
                _power_level(
                    w,
                    manager_rarity=mr,
                    personality_match=True,
                    mode=power_mode,
                )
                for w in workers_for_p
            )

    squad_power_lookup: dict[tuple[str, str], int] = {}
    for s in SYNERGY_TYPES:
        for p in PERSONALITIES:
            mp = mgr_power[(s, p)]
            if mp < 0:
                squad_power_lookup[(s, p)] = -1
                continue
            mr = mgr_rarity[(s, p)]
            squad_power_lookup[(s, p)] = mp + worker_power_lookup[(p, mr)]

    priority_synergies_1 = {"IsEngineer", "IsInventor"}
    priority_synergies_2 = {"IsSoldier", "IsMartialArtist"}

    best_score: tuple[int, int, int] = (-1, -1, -1)
    best_perm: tuple | None = None

    for perm in itertools.permutations(range(8)):
        p1_sum = 0
        p2_sum = 0
        total = 0
        valid = True
        for i, j in enumerate(perm):
            synergy = SYNERGY_TYPES[i]
            personality = PERSONALITIES[j]
            squad_power = squad_power_lookup[(synergy, personality)]
            if squad_power < 0:
                valid = False
                break
            total += squad_power
            if synergy in priority_synergies_1:
                p1_sum += squad_power
            if synergy in priority_synergies_2:
                p2_sum += squad_power

        score = (p1_sum, p2_sum, total)
        if valid and score > best_score:
            best_score = score
            best_perm = perm

    return best_perm, best_score[2]


# ---------------------------------------------------------------------------
# Squad composition – fill worker slots per squad
# ---------------------------------------------------------------------------

def compose_squad(personality: str, worker_buckets: dict,
                  power_mode: str = "potential") -> list[dict]:
    """Return ordered list of up to SQUAD_SLOTS workers for a squad.

    Placement order:
      1. Maximise total worker potential power
            2. Break ties by maximising active Trap Durability first
            3. Then maximise other activated set bonuses
            4. Prefer fewer wasted Trap Durability survivors
    """
    all_workers = worker_buckets.get(personality, [])

    return _select_optimal_fill(all_workers, SQUAD_SLOTS, power_mode=power_mode)


def _select_optimal_fill(candidates: list, slots: int,
                         power_mode: str = "potential") -> list[dict]:
    """Pick *slots* workers from *candidates* using power-first tie-breaks.

    Uses recursive search over all possible slot allocations per bonus type
    (bounded knapsack).  With ≤ 7 bonus types and ≤ 7 slots the search space
    is tiny (< 2 000 nodes).
    """
    if not candidates or slots <= 0:
        return []

    by_bonus: dict[str, list] = defaultdict(list)
    for w in candidates:
        sb = short(w["attributes"].get("set_bonus", ""))
        by_bonus[sb].append(w)
    for sb in by_bonus:
        by_bonus[sb].sort(
            key=lambda w: (_power_level(w, mode=power_mode), RARITY_ORDER.get(_rarity(w), 0)),
            reverse=True,
        )

    # Prefix sums make "top-N power" O(1) instead of repeated O(N) summation in recursion.
    power_prefix: dict[str, list[int]] = {}
    for sb, ws in by_bonus.items():
        pref = [0]
        running = 0
        for w in ws:
            running += _power_level(w, mode=power_mode)
            pref.append(running)
        power_prefix[sb] = pref

    bonus_types = list(by_bonus.keys())
    # best = [power_sum, trap_effective, effective_count, neg_wasted_trap, allocation_dict]
    best: list = [-1, -1, -1, 0, {}]

    def _power_sum(sb: str, take: int) -> int:
        """Total worker power for the top *take* workers of a bonus type."""
        return power_prefix[sb][take]

    def recurse(idx: int, remaining: int, power_sum: int,
                effective: int, trap_effective: int,
                wasted_trap: int, alloc: dict) -> None:
        if idx == len(bonus_types) or remaining <= 0:
            score = (power_sum, trap_effective, effective, -wasted_trap)
            if score > (best[0], best[1], best[2], best[3]):
                best[0] = power_sum
                best[1] = trap_effective
                best[2] = effective
                best[3] = -wasted_trap
                best[4] = dict(alloc)
            return

        sb = bonus_types[idx]
        threshold = SET_BONUS_THRESHOLDS.get(sb, 99)
        available = len(by_bonus[sb])
        max_take = min(available, remaining)

        for take in range(max_take + 1):
            eff_total = (take // threshold) * threshold
            trap_eff = eff_total if sb == TARGET_BONUS_SHORT else trap_effective
            trap_waste = (take % threshold) if sb == TARGET_BONUS_SHORT else wasted_trap
            alloc[sb] = take
            recurse(
                idx + 1,
                remaining - take,
                power_sum + _power_sum(sb, take),
                effective + eff_total,
                trap_eff,
                trap_waste,
                alloc,
            )

        alloc.pop(sb, None)

    recurse(0, slots, 0, 0, 0, 0, {})

    # Build the selected worker list from the best allocation
    selected: list[dict] = []
    for sb, count in best[4].items():
        selected.extend(by_bonus[sb][:count])
        by_bonus[sb] = by_bonus[sb][count:]

    # Fill any remaining slots with highest-power leftover workers
    leftover_slots = slots - len(selected)
    if leftover_slots > 0:
        leftovers = [w for ws in by_bonus.values() for w in ws]
        leftovers.sort(
            key=lambda w: (_power_level(w, mode=power_mode), RARITY_ORDER.get(_rarity(w), 0)),
            reverse=True,
        )
        selected.extend(leftovers[:leftover_slots])

    return selected


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def _bonus_summary(workers: list[dict]) -> str:
    """One-line summary of activated / inactive set bonuses in a squad."""
    counts: dict[str, int] = defaultdict(int)
    for w in workers:
        sb = short(w["attributes"].get("set_bonus", ""))
        if sb:
            counts[sb] += 1

    # Show target bonus first, then others sorted by count descending
    order = sorted(counts, key=lambda sb: (
        0 if sb == TARGET_BONUS_SHORT else 1, -counts[sb], sb))

    parts: list[str] = []
    for sb in order:
        count = counts[sb]
        threshold = SET_BONUS_THRESHOLDS.get(sb, 99)
        effective = (count // threshold) * threshold
        label = _loc(SET_BONUS_LABELS.get(sb, sb), OUTPUT_LANG)
        if effective > 0:
            groups = count // threshold
            parts.append(f"{count}× {label} ({groups}×{threshold} ✓)")
        else:
            parts.append(f"{count}× {label} (–)")
    return "  ·  ".join(parts)


def format_results(squads: list[dict], power_mode: str,
                   scenario_title: str, scenario_desc: str) -> str:
    lines: list[str] = []

    def squad_power(sq: dict) -> int:
        manager_rarity = _rarity(sq["manager"])
        return _power_level(sq["manager"], mode=power_mode) + sum(
            _power_level(
                w,
                manager_rarity=manager_rarity,
                personality_match=True,
                mode=power_mode,
            )
            for w in sq["workers"]
        )

    total_trap = sum(
        sum(1 for w in sq["workers"] if _is_trap(w)) for sq in squads)
    total_effective_trap = sum(
        (sum(1 for w in sq["workers"] if _is_trap(w)) // 2) * 2
        for sq in squads)

    squad_power_by_synergy = {sq["synergy"]: squad_power(sq) for sq in squads}
    total_power = sum(squad_power_by_synergy.values())
    fortitude_power = squad_power_by_synergy.get("IsDoctor", 0) + squad_power_by_synergy.get("IsTrainer", 0)
    offense_power = squad_power_by_synergy.get("IsSoldier", 0) + squad_power_by_synergy.get("IsMartialArtist", 0)
    resistance_power = squad_power_by_synergy.get("IsGadgeteer", 0) + squad_power_by_synergy.get("IsExplorer", 0)
    tech_power = squad_power_by_synergy.get("IsEngineer", 0) + squad_power_by_synergy.get("IsInventor", 0)

    lines.append("=" * 72)
    lines.append(f"  {scenario_title}")
    lines.append("=" * 72)
    lines.append(f"  {scenario_desc}")
    lines.append(f"  Total squad power               : {total_power}")
    lines.append("  Grouped power overview:")
    doc = _loc_synergy("IsDoctor")
    tra = _loc_synergy("IsTrainer")
    sol = _loc_synergy("IsSoldier")
    mar = _loc_synergy("IsMartialArtist")
    gad = _loc_synergy("IsGadgeteer")
    exp = _loc_synergy("IsExplorer")
    eng = _loc_synergy("IsEngineer")
    inv = _loc_synergy("IsInventor")
    grouped = [
        (f"{_loc('Fortitude', OUTPUT_LANG)} ({doc} + {tra})", fortitude_power),
        (f"{_loc('Offense', OUTPUT_LANG)} ({sol} + {mar})", offense_power),
        (f"{_loc('Resistance', OUTPUT_LANG)} ({gad} + {exp})", resistance_power),
        (f"{_loc('Tech', OUTPUT_LANG)} ({eng} + {inv})", tech_power),
    ]
    gw = max(len(g[0]) for g in grouped)
    for lbl, pwr in grouped:
        lines.append(f"    {lbl:<{gw}} : {pwr}")
    lines.append("  Power per squad:")
    per_squad = [(f"{_loc_squad(sq['synergy'])} ({_loc_synergy(sq['synergy'])})", sq["synergy"]) for sq in squads]
    sw = max(len(p[0]) for p in per_squad)
    for lbl, syn in per_squad:
        lines.append(f"    {lbl:<{sw}} : {squad_power_by_synergy[syn]}")
    lines.append("=" * 72)
    trap_label = _loc(SET_BONUS_LABELS.get(TARGET_BONUS_SHORT, TARGET_BONUS_SHORT), OUTPUT_LANG)
    lines.append(f"  {trap_label}: {total_trap}"
                 f"  ({total_effective_trap} ✓)")
    lines.append("=" * 72)

    for sq in squads:
        m     = sq["manager"]
        syn   = sq["synergy"]
        per   = sq["personality"]
        label = _loc_squad(syn)
        p_lbl = _loc_personality(per)
        syn_lbl = _loc_synergy(syn)

        trap_n = sum(1 for w in sq["workers"] if _is_trap(w))

        lines.append("")
        lines.append(f"┌─ {label.upper()} ({syn_lbl})"
                 f"  -  Personality: {p_lbl}")
        manager_name = _resolve_manager_display_name(m)
        lead_label = _loc("Lead Survivor", OUTPUT_LANG)
        lines.append(
            f"│  {lead_label}: {manager_name}  "
            f"[{_loc_rarity(_rarity(m))} | PL {_power_level(m, mode=power_mode)}]"
        )
        lines.append(f"│  Trap survivors: {trap_n} / {SQUAD_SLOTS}")
        lines.append(f"│  Squad power: {squad_power(sq)}")
        lines.append("│")

        for idx, w in enumerate(sq["workers"], 1):
            sb_key  = short(w["attributes"].get("set_bonus", ""))
            sb_lbl  = _loc(SET_BONUS_LABELS.get(sb_key, sb_key), OUTPUT_LANG)
            is_trap = _is_trap(w)
            marker  = "★" if is_trap else " "
            name    = w.get("name") or _loc("Survivor", OUTPUT_LANG)
            lines.append(
                f"│  [{marker}] {idx}. {name}"
                f"  [{_loc_rarity(_rarity(w))} | PL {_power_level(w, manager_rarity=_rarity(m), personality_match=True, mode=power_mode)}]  {sb_lbl}")

        lines.append("│")
        lines.append(f"│  Bonuses: {_bonus_summary(sq['workers'])}")
        lines.append("└" + "─" * 70)

    lines.append("")
    lines.append(f"Legend:  ★ = {trap_label}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    global OUTPUT_LANG

    parser = argparse.ArgumentParser(description="Optimal STW survivor squad assignment")
    parser.add_argument("--lang", default="en",
                        help=f"Output language ({', '.join(AVAILABLE_LANGS)}). Default: en")
    args = parser.parse_args()

    lang = args.lang.lower()
    if lang not in AVAILABLE_LANGS:
        rprint(f"[red]ERROR: Language '{lang}' is not available. "
               f"Available: {', '.join(AVAILABLE_LANGS)}[/red]")
        sys.exit(1)
    OUTPUT_LANG = lang

    input_file, input_label = _resolve_input_file()
    managers, workers = load_survivors(input_file)
    outputs: list[str] = []

    for power_mode, scenario_title, scenario_desc in [
        ("potential",
         "BEST POSSIBLE SQUAD ASSIGNMENT",
         "All survivors at max level (60). Shows the theoretical optimum."),
        ("current",
         "SQUAD ASSIGNMENT AT CURRENT LEVELS",
         "Uses actual survivor levels. Shows what you achieve right now."),
    ]:
        mgr_idx = build_mgr_index(managers, power_mode=power_mode)
        worker_buckets = build_worker_buckets(workers, power_mode=power_mode)

        best_perm, _best_total_power = find_optimal_assignment(
            mgr_idx, worker_buckets, power_mode=power_mode)

        if best_perm is None:
            rprint("[red]ERROR: No valid squad assignment found. "
                f"Please check manager data in {input_label}.[/red]")
            return

        squads: list[dict] = []
        for i, j in enumerate(best_perm):
            s, p = SYNERGY_TYPES[i], PERSONALITIES[j]
            manager = mgr_idx[(s, p)][0]
            squad_workers = compose_squad(p, worker_buckets, power_mode=power_mode)
            squads.append({
                "synergy":     s,
                "personality": p,
                "manager":     manager,
                "workers":     squad_workers,
            })

        outputs.append(format_results(squads, power_mode, scenario_title, scenario_desc))

    header = "Based on the survivors currently available in your homebase."
    output = header + "\n\n" + ("\n\n" + ("=" * 72) + "\n\n").join(outputs)

    # Back up existing result file before overwriting
    if OUTPUT_FILE.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup = BACKUP_DIR / f"{OUTPUT_FILE.stem}.backup_{timestamp}{OUTPUT_FILE.suffix}"
        shutil.copy2(OUTPUT_FILE, backup)
        rprint(f"[dim]Backup: [cyan]{backup.name}[/cyan][/dim]")

    OUTPUT_FILE.write_text(output, encoding="utf-8")
    rprint(f"[green]Result written to [cyan]{OUTPUT_FILE.name}[/cyan][/green]")


if __name__ == "__main__":
    main()
