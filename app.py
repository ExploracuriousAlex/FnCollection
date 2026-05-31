import logging
from pathlib import Path

from flask import Flask, jsonify, make_response, redirect, render_template, request, send_from_directory, url_for

logging.basicConfig(level=logging.WARNING, format="%(levelname)s:%(name)s: %(message)s")

_log_file = Path(__file__).parent / "warnings.log"
_fh = logging.FileHandler(_log_file, encoding="utf-8")
_fh.setLevel(logging.WARNING)
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s:%(name)s: %(message)s"))
logging.getLogger().addHandler(_fh)

import state
from data_loader import AVAILABLE_LANGS, BASE_DIR, GAME_CONTENT_DIR, get_collection_book, localize_term

app = Flask(__name__)


# Known language display names; falls back to the language code for unknown languages.
_LANG_NAMES: dict[str, str] = {
    "af": "Afrikaans", "ar": "العربية", "bg": "Български", "cs": "Čeština",
    "da": "Dansk", "de": "Deutsch", "el": "Ελληνικά", "en": "English",
    "es": "Español", "et": "Eesti", "fi": "Suomi", "fr": "Français",
    "hr": "Hrvatski", "hu": "Magyar", "id": "Indonesia", "it": "Italiano",
    "ja": "日本語", "ko": "한국어", "lt": "Lietuvių", "lv": "Latviešu",
    "ms": "Melayu", "nl": "Nederlands", "no": "Norsk", "pl": "Polski",
    "pt": "Português", "pt-BR": "Português (Brasil)", "ro": "Română",
    "ru": "Русский", "sk": "Slovenčina", "sl": "Slovenščina", "sr": "Srpski",
    "sv": "Svenska", "th": "ภาษาไทย", "tr": "Türkçe", "uk": "Українська",
    "vi": "Tiếng Việt", "zh-Hans": "中文(简体)", "zh-Hant": "中文(繁體)",
}

_DEFAULT_LANG = "en" if "en" in AVAILABLE_LANGS else AVAILABLE_LANGS[0]
_MATERIAL_KEYS = ["Copper", "Silver", "Malachite", "Obsidian", "Shadowshard", "Brightcore", "Sunbeam"]


def _get_lang() -> str:
    lang = request.cookies.get("lang", _DEFAULT_LANG)
    return lang if lang in AVAILABLE_LANGS else _DEFAULT_LANG


def _build_page_slot_index(data: dict) -> dict:
    """For each page build a list of slot descriptors used by JS sidebar filtering.

    Each descriptor: {"col": col_slot_id, "inv": [stepper_slot_id, ...]}
    - col: the slot ID used for the collection checkbox
    - inv: all stepper IDs that must be > 0 for the slot to be "fully in inventory"
    """
    result: dict[str, list] = {}
    for page_id, page in data["pages"].items():
        slots = []
        for sec in page["sections"]:
            for slot in sec["slots"]:
                inv_ids: list[str] = []
                if "portraits" in slot:
                    for portrait in slot["portraits"]:
                        for sb in portrait["set_bonuses"]:
                            inv_ids.append(sb["slot_id"])
                elif "genders" in slot:
                    for gender in slot["genders"]:
                        for p in gender["personalities"]:
                            inv_ids.append(p["slot_id"])
                else:
                    inv_ids.append(slot["slot_id"])
                slots.append({"col": slot["slot_id"], "inv": inv_ids, "rarity": slot.get("rarity_css", ""), "name": slot.get("name", "").lower()})
        result[page_id] = slots
    return result


def _render(template: str, *, data: dict | None = None, **kwargs):
    lang = _get_lang()
    if data is None:
        data = get_collection_book(lang)
    material_labels = {key: localize_term(key, lang) for key in _MATERIAL_KEYS}
    return render_template(
        template,
        current_lang=lang,
        available_langs=[(code, _LANG_NAMES.get(code, code)) for code in AVAILABLE_LANGS],
        page_slot_index=_build_page_slot_index(data),
        material_labels=material_labels,
        data=data,
        **kwargs,
    )


@app.route("/set-lang/<lang_code>")
def set_lang(lang_code: str):
    referrer = request.referrer or url_for("index")
    resp = make_response(redirect(referrer))
    if lang_code in AVAILABLE_LANGS:
        resp.set_cookie("lang", lang_code, max_age=365 * 24 * 3600)
    return resp


@app.route("/api/state")
def api_state_get():
    return jsonify(state.load())


@app.route("/gameicon/<content_dir>/<path:img_path>")
def gameicon(content_dir: str, img_path: str):
    """Serve PNG icon files from a game content directory.

    content_dir values:
      SaveTheWorld / BRCosmetics → FortniteGame/Plugins/GameFeatures/<name>/Content
      FortniteGame               → FortniteGame/Content  (base game assets)
    """
    if content_dir == "FortniteGame":
        serve_dir = GAME_CONTENT_DIR
    else:
        serve_dir = BASE_DIR / "FortniteGame" / "Plugins" / "GameFeatures" / content_dir / "Content"
    return send_from_directory(serve_dir, img_path)


@app.route("/")
def index():
    lang = _get_lang()
    data = get_collection_book(lang)
    # Redirect to first page of first non-empty category
    for cat in data["categories"]:
        if cat["pages"]:
            return redirect(url_for("page_detail", page_id=cat["pages"][0]["id"]))
    return _render("index.html", data=data, current_page=None)


@app.route("/page/<page_id>")
def page_detail(page_id: str):
    lang = _get_lang()
    data = get_collection_book(lang)
    current_page = data["pages"].get(page_id)
    if not current_page:
        return redirect(url_for("index"))
    return _render("index.html", data=data, current_page=current_page)


if __name__ == "__main__":
    app.run(debug=True, host="localhost", port=5000)
