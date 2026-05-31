"""Centralized collection.json state management."""

import json
from pathlib import Path

_DATA_DIR = Path(__file__).parent / "data"
_STATE_FILE = _DATA_DIR / "collection.json"


def default_state() -> dict:
    return {"inv": {}, "col": {}, "col_max": {}, "inv_details": {}, "col_details": {}}


def normalize(state: dict) -> dict:
    """Validate and normalize a state dict, discarding invalid entries."""
    out = default_state()
    if not isinstance(state, dict):
        return out

    inv = state.get("inv", {})
    if isinstance(inv, dict):
        for key, value in inv.items():
            if isinstance(key, str) and isinstance(value, int) and value > 0:
                out["inv"][key] = value

    for flag_key in ("col", "col_max"):
        flags = state.get(flag_key, {})
        if isinstance(flags, dict):
            for key, value in flags.items():
                if isinstance(key, str) and bool(value):
                    out[flag_key][key] = True

    for detail_key in ("inv_details", "col_details"):
        details = state.get(detail_key, {})
        if not isinstance(details, dict):
            continue
        for slot_id, variants in details.items():
            if not isinstance(slot_id, str) or not isinstance(variants, list):
                continue
            out_variants = []
            for variant in variants:
                if not isinstance(variant, dict):
                    continue
                count = variant.get("count")
                if not isinstance(count, int) or count <= 0:
                    continue
                entry: dict = {"count": count}
                material = variant.get("material")
                if isinstance(material, str) and material:
                    entry["material"] = material
                pl = variant.get("pl")
                if isinstance(pl, int) and pl >= 0:
                    entry["pl"] = pl
                tier = variant.get("tier")
                if isinstance(tier, int) and tier >= 0:
                    entry["tier"] = tier
                out_variants.append(entry)
            if out_variants:
                out[detail_key][slot_id] = out_variants

    return out


def load() -> dict:
    """Load and normalize state from collection.json."""
    if _STATE_FILE.exists():
        try:
            with open(_STATE_FILE, encoding="utf-8") as f:
                return normalize(json.load(f))
        except Exception:
            pass
    return default_state()


def save(state: dict) -> None:
    """Normalize and save state to collection.json."""
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(normalize(state), f, separators=(",", ":"))
