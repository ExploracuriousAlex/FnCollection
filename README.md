
# Fortnite STW Collection Book Tracker

A Flask web app that visualises the Fortnite Save the World Collection Book, including icons, names, rarities, and inventory tracking.

---

## Prerequisites

- Python 3.14+ (recommended: via [uv](https://github.com/astral-sh/uv))
- [FModel](https://fmodel.app/) for exporting game data
- A Fortnite account for inventory data

---

## 1. Exporting game files (FModel)

Download [FModel](https://fmodel.app/), open the Fortnite directory, and export the folders listed below. Exports are placed in `FModel/Output/Exports/` by default — copy the contents into the project folder, preserving the directory structure 1:1.

> **Important:** FModel provides two relevant export modes:
> - **"Save Folder's Packages Properties (.json)"** → for JSON data files
> - **"Save Folder's Packages Textures"** → for PNG image files

### 1.1 JSON data

| Purpose | Path in FModel |
|---|---|
| Set bonus & personality labels | `FortniteGame/Content/Items/ItemCategories.json` *(single file)* |
| Rarity label translations | `FortniteGame/Content/Localization/Fortnite_locchunk10/` |
| Set bonus & personality label translations | `FortniteGame/Content/Localization/Fortnite_locchunk20/` |
| Collection Book tables | `FortniteGame/Plugins/GameFeatures/SaveTheWorld/Content/CollectionBook/Data/` |
| Hero definitions (HID) | `FortniteGame/Plugins/GameFeatures/SaveTheWorld/Content/Heroes/` |
| Crafting recipes (SID → WID/TID mapping) | `FortniteGame/Plugins/GameFeatures/SaveTheWorld/Content/Items/DataTables/CraftingRecipes_New.json` *(single file)* |
| Defender definitions (DID) | `FortniteGame/Plugins/GameFeatures/SaveTheWorld/Content/Items/Defenders/` |
| Schematic definitions (SID) | `FortniteGame/Plugins/GameFeatures/SaveTheWorld/Content/Items/Schematics/` |
| Trap definitions (TID) | `FortniteGame/Plugins/GameFeatures/SaveTheWorld/Content/Items/Traps/` |
| Alteration definitions (perk labels for trap recommendations) | `FortniteGame/Plugins/GameFeatures/SaveTheWorld/Content/Items/Alteration_v2/AttributeAlterations/` |
| Weapon definitions (WID) | `FortniteGame/Plugins/GameFeatures/SaveTheWorld/Content/Items/Weapons/` |
| Survivor definitions | `FortniteGame/Plugins/GameFeatures/SaveTheWorld/Content/Items/Workers/` |
| STW localization | `FortniteGame/Plugins/GameFeatures/SaveTheWorld/Content/Localization/SaveTheWorld/` |
| Mythic leader portrait definitions | `FortniteGame/Plugins/GameFeatures/SaveTheWorld/Content/UI/Icons/Icon-Worker/IconDefinitions/` |
| Mission generator definitions (for `missing_items.py`) | `FortniteGame/Plugins/GameFeatures/SaveTheWorld/Content/World/MissionGens/` |
| Difficulty growth table (for PL mapping in `missing_items.py`) | `FortniteGame/Plugins/GameFeatures/SaveTheWorld/Content/Balance/Datatables/GameDifficultyGrowthBounds.json` *(single file)* |

> **Note on localization:** Each localization folder contains language sub-folders (`en`, `de`, `es`, …). Export only the languages you want to support, or export all sub-folders for full multi-language support.

### 1.2 Textures / Icons

| Purpose | Path in FModel |
|---|---|
| Campfire icon | `FortniteGame/Content/Athena/Items/Traps/Campfire/` |
| Personality badge icons | `FortniteGame/Content/UI/Foundation/Textures/Icons/Cards/Personalities/` |
| Survivor set-bonus stat icons | `FortniteGame/Content/UI/Foundation/Textures/Icons/Stats/` |
| Weapon icons (BR base game) | `FortniteGame/Content/UI/Foundation/Textures/Icons/Weapons/` |
| Generic survivor portraits | `FortniteGame/Content/UI/Foundation/Textures/Icons/Workers/Generic/` |
| BR hero icons – Athena Soldiers | `FortniteGame/Plugins/GameFeatures/BRCosmetics/Content/UI/Foundation/Textures/Icons/Heroes/Athena/Soldier/` |
| BR hero icons – Outlander portraits | `FortniteGame/Plugins/GameFeatures/BRCosmetics/Content/UI/Foundation/Textures/Icons/Heroes/Outlander/Portrait/` |
| BR hero icons – Soldier portraits | `FortniteGame/Plugins/GameFeatures/BRCosmetics/Content/UI/Foundation/Textures/Icons/Heroes/Soldier/Portrait/` |
| BR hero skin variants | `FortniteGame/Plugins/GameFeatures/BRCosmetics/Content/UI/Foundation/Textures/Icons/Heroes/Variants/` |
| Weapon icons (BRCosmetics) | `FortniteGame/Plugins/GameFeatures/BRCosmetics/Content/UI/Foundation/Textures/Icons/Weapons/Items/` |
| STW weapon icons (from item defs) | `FortniteGame/Plugins/GameFeatures/SaveTheWorld/Content/Items/Weapons/` |
| STW hero icons | `FortniteGame/Plugins/GameFeatures/SaveTheWorld/Content/UI/Foundation/Textures/Icons/Heroes/` |
| STW weapon icons | `FortniteGame/Plugins/GameFeatures/SaveTheWorld/Content/UI/Foundation/Textures/Icons/Weapons/` |
| STW survivor/leader portraits | `FortniteGame/Plugins/GameFeatures/SaveTheWorld/Content/UI/Foundation/Textures/Icons/Workers/` |
| STW quest icons | `FortniteGame/Plugins/GameFeatures/SaveTheWorld/Content/UI/Foundation/Textures/Quest/` |
| STW class icons | `FortniteGame/Plugins/GameFeatures/SaveTheWorld/Content/UI/Icons/Classes/` |
| STW defender icons | `FortniteGame/Plugins/GameFeatures/SaveTheWorld/Content/UI/Icons/Defenders/` |
| Mythic leader portrait icons | `FortniteGame/Plugins/GameFeatures/SaveTheWorld/Content/UI/Icons/Icon-Worker/IconDefinitions/` |

### 1.3 Diagnosing missing game files

When the app starts, it writes a `warnings.log` file to the project root. Any item whose definition JSON (WID/TID/HID) or icon PNG could not be found on disk is logged there with the asset name and — where applicable — the expected file path(s). Check this file after adding new FModel exports to confirm all slots resolve correctly.

---

## 2. Directory structure after export

After copying the FModel exports, the project should look like this:

```
FortniteCollection/
├── FortniteGame/
│   ├── Content/
│   │   ├── Athena/Items/Traps/Campfire/  ← campfire icon
│   │   ├── Items/
│   │   │   └── ItemCategories.json  ← set bonus & personality data
│   │   ├── Localization/
│   │   │   ├── Fortnite_locchunk10/  ← rarity label translations
│   │   │   └── Fortnite_locchunk20/  ← set bonus & personality translations
│   │   └── UI/Foundation/Textures/Icons/
│   │       ├── Cards/Personalities/  ← personality badge icons
│   │       ├── Stats/  ← set-bonus stat icons
│   │       ├── Weapons/  ← weapon icons (BR)
│   │       └── Workers/Generic/  ← generic survivor portraits
│   └── Plugins/GameFeatures/
│       ├── BRCosmetics/Content/UI/Foundation/Textures/Icons/
│       │   ├── Heroes/
│       │   │   ├── Athena/Soldier/  ← BR hero icons (Athena)
│       │   │   ├── Outlander/Portrait/  ← BR hero icons (Outlander)
│       │   │   ├── Soldier/Portrait/  ← BR hero icons (Soldier)
│       │   │   └── Variants/  ← BR hero skin variants
│       │   └── Weapons/Items/  ← weapon icons (BRCosmetics)
│       └── SaveTheWorld/Content/
│           ├── Balance/Datatables/  ← GameDifficultyGrowthBounds.json
│           ├── CollectionBook/Data/  ← Collection Book tables (JSON)
│           ├── Heroes/  ← hero definitions (HID_*.json)
│           ├── Items/
│           │   ├── Alteration_v2/AttributeAlterations/  ← perk localization source for trap recommendations
│           │   ├── DataTables/CraftingRecipes_New.json  ← SID→WID/TID mapping
│           │   ├── Defenders/  ← DID_*.json
│           │   ├── Schematics/  ← SID_*.json
│           │   ├── Traps/  ← TID_*.json
│           │   ├── Weapons/  ← WID_*.json + weapon icons
│           │   └── Workers/  ← survivor definitions
│           ├── Localization/SaveTheWorld/  ← STW localization
│           ├── World/MissionGens/  ← mission names (missing_items.py)
│           └── UI/
│               ├── Foundation/Textures/
│               │   ├── Icons/
│               │   │   ├── Heroes/  ← STW hero icons
│               │   │   ├── Weapons/  ← STW weapon icons
│               │   │   └── Workers/  ← STW survivor/leader portraits
│               │   └── Quest/  ← STW quest icons
│               └── Icons/
│                   ├── Classes/  ← STW class icons
│                   ├── Defenders/  ← STW defender icons
│                   └── Icon-Worker/IconDefinitions/  ← mythic leader portrait definitions
├── static/
├── templates/
├── app.py  ← Flask web application
├── data_loader.py  ← collection book data resolution
├── epic_api.py  ← Epic Games API client
├── epic_login.ps1  ← helper to get/set EPIC_REFRESH_TOKEN
├── get_data.py  ← inventory importer (Epic MCP)
├── missing_items.py  ← generates missing_items.txt
├── squads.py  ← generates squads.txt
├── state.py  ← collection.json read/write
├── data/
│   └── collection.json  ← created automatically (internal)
└── backups/  ← auto-generated backup files
```

---

## 3. Configuration (.env)

Copy the example file and fill in your values:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `EPIC_CLIENT_ID` | Fortnite PC client ID |
| `EPIC_CLIENT_SECRET` | Fortnite PC client secret |
| `EPIC_DISPLAY_NAME` | Your Epic Games display name (used by `get_data.py` to resolve account ID) |
| `EPIC_REFRESH_TOKEN` | Refresh token obtained via `epic_login.ps1` (rotates on each use) |

> **Note:** `.env` is git-ignored and will never be committed.

---

## 4. Retrieving inventory data (Epic MCP)

The importer fetches inventory directly from Epic profile APIs (MCP).
It resolves the display name (from `EPIC_DISPLAY_NAME`) to an account id and then queries the `campaign`, `collection_book_people0`, and `collection_book_schematics0` profiles.

---

## 5. Managing inventory

Inventory data is managed in two ways:

- **Inventory quantities (`inv`)** are imported via the Epic importer (see below). Running the importer overwrites all existing inventory quantities.
- **Inventory details (`inv_details`)** are imported via the importer for schematic/weapon variants (e.g. material tier + power level).
- **Collection Book checkmarks (`col`)** are now imported from Epic Collection-Book profiles.
- **Max-strength flags (`col_max`)** are maintained through the web UI and are not touched by the importer.
- The web UI treats inventory as **read-only**. Inventory edits are no longer done manually in the UI.

### Importing inventory via Epic MCP

Run once to obtain and set `EPIC_REFRESH_TOKEN` in the current PowerShell terminal:

```bash
.\epic_login.ps1
```

Then run the import (no parameters required):

```bash
uv run python get_data.py
```

Notes:
- `EPIC_CLIENT_ID` and `EPIC_CLIENT_SECRET` are read from `.env`.
- If `EPIC_REFRESH_TOKEN` is missing, `get_data.py` falls back to the latest folder in `raw_data`.
- On Epic API errors, `get_data.py` also falls back to the latest `raw_data` snapshot.

The importer:

- Fully replaces all `inv` entries (inventory quantities)
- Fully replaces all `inv_details` entries (variant details like `1x Copper PL 30`)
- Imports and updates `col` entries (collection book checkmarks)
- Leaves all `col_max` entries (max-strength flags) unchanged
- Creates a safety backup on every run (`backups/collection.backup_YYYYmmdd_HHMMSS.json`)
- Imports heroes, defenders, schematics, survivors, and Halloween workers
- Prints a summary with statistics and any unmatched items

---

## 6. Text output files and backups

The scripts now use fixed output names and harmonized backup naming:

- `missing_items.py` writes `missing_items.txt`
- `squads.py` writes `squads.txt`
- Before overwrite, backups are created in `backups/` as `<name>.backup_YYYYmmdd_HHMMSS<ext>`
  - `backups/missing_items.backup_YYYYmmdd_HHMMSS.txt`
  - `backups/squad.backup_YYYYmmdd_HHMMSS.txt`
  - `backups/collection.backup_YYYYmmdd_HHMMSS.json`

---

## 7. Additional tools

### 7.1 `missing_items.py` — Today's available collection book items

Queries the Epic World Info API to find mission alert rewards that correspond to collection book items you don't yet own. Requires `EPIC_REFRESH_TOKEN` (see section 4).

```bash
uv run python missing_items.py
uv run python missing_items.py --lang de
```

Output: `missing_items.txt` — a list of missing items available today, grouped by zone and mission, including power level and rarity.

The `--lang` option translates item names, rarities, zone names, and mission types into the specified language. Supported values correspond to the exported localization sub-folders (e.g. `en`, `de`, `es`, `fr`, …). Default: `en`.

### 7.2 `squads.py` — Optimal survivor squad composition

Reads your survivor inventory and computes the optimal squad layout across all 8 squads. Evaluates all 40 320 personality→synergy permutations to maximise squad power with activated set bonuses as tie-breakers.

```bash
uv run python squads.py
uv run python squads.py --lang de
```

Output: `squads.txt` — two suggestions (potential mode and current mode) with full squad assignments.

The `--lang` option translates squad names, personalities, set bonuses, and rarity labels. Supported values match the exported localization sub-folders. Default: `en`.

Priority order: Tech → Offense → Total. Trap Durability bonuses are ranked above other activated bonuses as tie-breakers.

### 7.3 `Trap Recommendations/Trap Recommandations.md` — Perk setups reference

A static Markdown document with recommended perk configurations for all STW traps (ceiling, wall, floor). Each entry shows the trap icon, element, and optimal perk rolls for different build strategies (max damage, max durability, reload speed, etc.).

View directly in VS Code or any Markdown viewer — the embedded images reference the exported game textures via relative paths.

### 7.4 `create_trap_recommendations.py` — Generate localized trap recommendation files

Compares your latest normalized inventory snapshot with the recommendation model in `config/trap_recommendations.json` and generates localized Markdown outputs for all recommended builds and missing builds.

```bash
uv run python create_trap_recommendations.py --lang en
uv run python create_trap_recommendations.py --lang de
```

Output files are written to the project root:

- `trap_recommendations.md`
- `trap_recommendations_missing.md`

No backups are created for these generated recommendation files.

---

## 8. Starting the web app

```bash
uv run python app.py
```

The app is available at [http://localhost:5000](http://localhost:5000).

---

## 9. Supported languages

The app supports all languages for which localization data has been exported. Language selection is available via the dropdown in the top-right corner. A language is available as soon as its sub-folder exists under `Localization/SaveTheWorld/` (e.g. `de/`, `en/`, `es/`).

For rarity labels, set bonus and personality labels to be translated correctly, the corresponding language sub-folder must also exist inside `Fortnite_locchunk10/` and `Fortnite_locchunk20/` (e.g. `FortniteGame/Content/Localization/Fortnite_locchunk10/de/`).

---

## 10. Acknowledgements

- **[FModel](https://fmodel.app/)** — Without this tool, extracting and browsing the game's asset packages (JSON definitions, textures, localization) would not be feasible. All game data used by this project is exported via FModel. Built on top of **[CUE4Parse](https://github.com/FabianFG/CUE4Parse)** by FabianFG.
- **[FortniteEndpointsDocumentation](https://github.com/LeleDerGrasshalmi/FortniteEndpointsDocumentation)** by LeleDerGrasshalmi — The comprehensive documentation of Epic's undocumented API endpoints made the automated inventory and world-info retrieval possible.
