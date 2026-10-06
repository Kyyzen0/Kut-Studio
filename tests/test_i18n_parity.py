"""Parité FR / EN / ES stricte de **toutes** les clés i18n (module principal et modules de domaine).

Aucun repli silencieux : chaque clé doit exister, non vide, dans les trois langues, avec les mêmes champs
``{nom}``, et une valeur anglaise ou espagnole ne doit pas être le français recopié par paresse (liste blanche
courte et justifiée pour les sigles, noms propres, emprunts et cognats identiques).
"""

from __future__ import annotations

import ast
import re
import string
from collections import Counter
from pathlib import Path

import pytest

from ui import i18n
from ui.i18n import MissingTranslationError, strict_translations, translate, translate_strict

ROOT = Path(__file__).resolve().parent.parent
LANGUAGES = ("fr", "en", "es")
TABLE_FILES = sorted((ROOT / "ui").glob("i18n*.py"))


@pytest.fixture(autouse=True)
def _i18n_reset():
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}


def _words(text: str) -> list[str]:
    """Mots (≥ 3 lettres) hors champs ``{nom}`` : ce qui est réellement à traduire."""
    stripped = re.sub(r"\{[^{}]*\}", " ", text)
    return [word for word in re.findall(r"[^\W\d_]+", stripped) if len(word) >= 3]


ALL_KEYS = sorted(i18n._TRANSLATIONS)


# ---------------------------------------------------------------------------
# Structure des tables
# ---------------------------------------------------------------------------


def test_the_tables_are_not_empty_and_the_merge_loses_no_key():
    domain_keys = [key for table in i18n.DOMAIN_TABLES.values() for key in table]
    assert len(domain_keys) == len(set(domain_keys)), "une clé de domaine est définie dans deux modules"
    assert set(domain_keys) <= set(ALL_KEYS)
    assert len(ALL_KEYS) > 900


def test_merging_a_duplicated_key_is_refused_instead_of_overwriting_silently():
    with pytest.raises(ValueError, match="action.open"):
        i18n._merge_table("ui.i18n_test", {"action.open": {"fr": "x", "en": "x", "es": "x"}})
    assert translate_strict("action.open", "en") == "Open…"  # la clé d'origine n'a pas bougé


@pytest.mark.parametrize("path", TABLE_FILES, ids=lambda path: path.name)
def test_no_dictionary_literal_defines_a_key_twice(path):
    """Un littéral de dict qui répète une clé garde la dernière valeur sans rien dire : on le refuse."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    duplicates: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = [key.value for key in node.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)]
            duplicates.extend(key for key, count in Counter(keys).items() if count > 1)
    assert not duplicates, f"clés répétées dans {path.name} : {sorted(set(duplicates))}"


def test_every_key_is_ascii_lowercase_dotted():
    malformed = [key for key in ALL_KEYS if not re.fullmatch(r"[a-z0-9_]+(\.[a-z0-9_]+)*", key)]
    assert not malformed, f"clés mal formées : {malformed}"


# ---------------------------------------------------------------------------
# Parité des langues
# ---------------------------------------------------------------------------


def test_every_key_has_exactly_the_three_languages():
    wrong = {key: sorted(entry) for key, entry in i18n._TRANSLATIONS.items() if set(entry) != set(LANGUAGES)}
    assert not wrong, f"langues manquantes ou en trop : {wrong}"


def test_no_value_is_empty():
    empty = [(key, lang) for key, entry in i18n._TRANSLATIONS.items() for lang, text in entry.items() if not text.strip()]
    assert not empty, f"valeurs vides : {empty}"


def test_every_value_is_a_valid_format_string_with_the_same_fields_in_all_languages():
    problems: list[str] = []
    for key, entry in i18n._TRANSLATIONS.items():
        fields: dict[str, set[str]] = {}
        for lang, text in entry.items():
            try:
                fields[lang] = _fields(text)
            except ValueError as exc:
                problems.append(f"{key} [{lang}] : accolades mal formées ({exc})")
        if len({frozenset(names) for names in fields.values()}) > 1:
            problems.append(f"{key} : champs différents {fields}")
    assert not problems, "\n".join(problems)


def test_the_shape_of_a_text_is_the_same_in_all_languages():
    """Une phrase finissant par « … » ou « : » (ou sur plusieurs lignes) le fait dans les trois langues."""
    problems: list[str] = []
    for key, entry in i18n._TRANSLATIONS.items():
        if set(entry) != set(LANGUAGES):
            continue
        for ending in ("…", ":", "?", "!", "."):
            if len({entry[lang].rstrip().endswith(ending) for lang in LANGUAGES}) > 1:
                problems.append(f"{key} : la fin « {ending} » n'est pas la même partout {entry}")
        if len({"\n" in entry[lang] for lang in LANGUAGES}) > 1:
            problems.append(f"{key} : retours à la ligne différents")
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# Pas de français recopié
# ---------------------------------------------------------------------------

# Sigles, noms propres et mots qui s'écrivent pareil dans les trois langues (« AUDIO », « clip »).
_ACRONYMS_AND_PROPER_NAMES = {
    "CPU", "GPU", "Kut-Studio", "ProRes Master", "PREVIEW", "TikTok / Vertical 1080×1920", "TikTok 60 fps", "YouTube", "Night Race", "City Lights", "PTS",
    "Auto (≤ 1080p)", "MOTION GRAPHICS", "AUDIO", "slip {delta}s", "{count} clip", "{count} clips",
    "Clip", "SCOPES",
    # Plateformes et techniques de la vidéo sociale (noms de marque, nom propre d'un effet, sigle).
    "TikTok", "Instagram Reels", "YouTube Shorts", "Ken Burns", "SFX", " BPM", "Night Look", "Neon Rush",
}
# Emprunts à l'anglais que le français (et l'espagnol) emploient tels quels.
_LOANWORDS = {
    "Cache", "Chroma Key", "Ease In-Out", "Export", "Film noir", "Guides", "Look", "Master", "Mix", "Motion graphics", "Normal",
    "Multicam", "MULTICAM", "Parade", "Preset", "Timecode", "timecode", "Presets", "preset", "presets", "Proxies", "Proxy", "Reverse", "Roll", "Slide", "Slip", "Solo", "Stop", "Tags", "Timeline",
    "Trackers", "Template", "Tempo", "Light leak", "Flash", "Bloom", "Grain", "Whoosh", "Drop", "SPRINT 🔥",
    "Tracking {direction} ({trackers})", "Vectorscope", "Vignette", "Vintage", "Viewer", "Waveform", "Zoom",
    "{label} · transform",
}
# Cognats : le mot s'écrit pareil en français et en anglais.
_COGNATES_EN = {
    "Animation", "Audio", "Auto", "Bézier", "Compensation", "Format", "Gain", "Gain (dB)", "Interpolation",
    "Navigation", "Performance", "Position", "Position + rotation", "Position X", "Position Y", "Ratio", "Rotation",
    "Saturation", "Stabilisation", "Standard", "Type", "Volume",
    "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)", "Rectangle", "Image", "Image…", "Ellipse", "Intersection",
    "Parent", "Source", "Transform", "Transitions", "TRANSITION", "Pause", "Compositing", "Transition",
    "Source: --", "Timeline: --", "Source: {seconds}s", "Timeline: {seconds}s", "Images", "audio",
    *(f"Angle {number}" for number in range(1, 10)), "Sources", "Angle", "Angles",
    "Vertical", "Portrait", "Impact", "Amplitude",
}
# Cognats français / espagnol.
_COGNATES_ES = {"Audio", "Auto", "Bézier", "Contraste", "Ratio", "Tangentes", "audio", "audios", "Vertical", "Variante"}

ALLOWED_IDENTICAL = {
    "en": _ACRONYMS_AND_PROPER_NAMES | _LOANWORDS | _COGNATES_EN,
    "es": _ACRONYMS_AND_PROPER_NAMES | _LOANWORDS | _COGNATES_ES,
}


def _identical(language: str) -> dict[str, str]:
    """``{clé: texte français}`` des clés dont la traduction est le français recopié."""
    return {
        key: entry["fr"]
        for key, entry in i18n._TRANSLATIONS.items()
        if entry.get(language, "").strip().lower() == entry["fr"].strip().lower() and _words(entry["fr"])
    }


@pytest.mark.parametrize("language", ["en", "es"])
def test_a_translation_is_not_the_french_text_copied_by_laziness(language):
    copied = {key: text for key, text in _identical(language).items() if text not in ALLOWED_IDENTICAL[language]}
    assert not copied, (
        f"valeur {language} identique au français : traduisez-la, ou (sigle, nom propre, emprunt, cognat) "
        f"ajoutez-la aux listes de tests/test_i18n_parity.py avec sa justification : {copied}"
    )


def test_the_identical_word_allowlist_has_no_stale_entry():
    used = set(_identical("en").values()) | set(_identical("es").values())
    allowed = set().union(*ALLOWED_IDENTICAL.values())
    assert not allowed - used, f"entrées de la liste blanche devenues inutiles : {sorted(allowed - used)}"


# ---------------------------------------------------------------------------
# Traduction stricte
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_key_resolves_strictly_in_every_language(language):
    """Aucune clé ne dépend du repli : chacune se traduit, avec ses champs, dans la langue demandée."""
    for key, entry in i18n._TRANSLATIONS.items():
        values = {name: "x" for name in _fields(entry[language])}
        text = translate_strict(key, language, **values)
        assert text.strip(), (key, language)
        assert not (text.startswith("[") and text.endswith("]")), (key, language, text)


def test_translate_strict_answers_in_the_current_language_by_default():
    i18n.set_language("es")
    assert translate_strict("action.open") == "Abrir…"
    assert translate_strict("action.open", "en") == "Open…"


def test_translate_strict_refuses_an_unknown_key_where_translate_returns_a_marker():
    assert translate("does.not.exist") == "[does.not.exist]"
    with pytest.raises(MissingTranslationError, match="does.not.exist"):
        translate_strict("does.not.exist")


def test_translate_strict_does_not_fall_back_to_french(monkeypatch):
    monkeypatch.setitem(i18n._TRANSLATIONS, "test.only_french", {"fr": "valeur", "en": "value"})
    i18n.set_language("es")
    assert translate("test.only_french") == "valeur"  # repli silencieux du mode normal
    with pytest.raises(MissingTranslationError, match="absente en 'es'"):
        translate_strict("test.only_french")


def test_translate_strict_refuses_an_empty_value_and_a_missing_field(monkeypatch):
    monkeypatch.setitem(i18n._TRANSLATIONS, "test.empty", {"fr": "x", "en": "  ", "es": "x"})
    monkeypatch.setitem(i18n._TRANSLATIONS, "test.field", {"fr": "{n} clips", "en": "{n} clips", "es": "{n} clips"})
    with pytest.raises(MissingTranslationError, match="vide"):
        translate_strict("test.empty", "en")
    assert translate("test.field") == "{n} clips"  # le mode normal affiche le champ brut
    with pytest.raises(MissingTranslationError, match="champ"):
        translate_strict("test.field", "fr")
    assert translate_strict("test.field", "fr", n=3) == "3 clips"


def test_translate_strict_refuses_an_unknown_language():
    with pytest.raises(ValueError, match="langue"):
        translate_strict("action.open", "de")


def test_strict_mode_makes_translate_raise_instead_of_falling_back(monkeypatch):
    monkeypatch.setitem(i18n._TRANSLATIONS, "test.only_french", {"fr": "valeur", "en": "value"})
    i18n.set_language("es")
    with strict_translations():
        with pytest.raises(MissingTranslationError):
            translate("test.only_french")
        with pytest.raises(MissingTranslationError):
            translate("does.not.exist")
        assert translate("action.open") == "Abrir…"
    assert translate("does.not.exist") == "[does.not.exist]"  # le mode normal revient à la sortie du bloc
