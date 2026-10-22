"""Recherche de la palette de commandes (``core.command_search``) : pure, sans Qt."""

from __future__ import annotations

import time

import pytest

from core.command_search import (
    MAX_QUERY_CHARS, MAX_RESULTS, SYNONYMS, SearchEntry, clean_label, normalize, recent_results, search, tokens,
)
from core.usage_level import UsageLevel

SIMPLE, STUDIO, ADVANCED = UsageLevel.SIMPLE, UsageLevel.STUDIO, UsageLevel.ADVANCED


def entry(entry_id: str, title: str, **kw) -> SearchEntry:
    return SearchEntry(entry_id, title, **kw)


CATALOG = [
    entry("cut", "Couper", title_en="Cut at playhead", hint="Séquence"),
    entry("grade", "Étalonnage", title_en="Color grading", hint="Fenêtre › Pages", level=STUDIO),
    entry("track", "Panneau de suivi", title_en="Tracking panel", hint="Calques", level=ADVANCED),
    entry("srt", "Importer des sous-titres SRT", title_en="Import SRT subtitles", hint="Fichier"),
    entry("speed", "Vitesse du clip", title_en="Clip speed", hint="Inspecteur", level=STUDIO),
    entry("export", "Exporter", title_en="Export", hint="Fichier"),
]


def ids(results) -> list[str]:
    return [r.entry.id for r in results]


def test_normalize_ignores_case_accents_ligatures_and_punctuation():
    assert normalize("  ÉTALONNAGE  ") == normalize("etalonnage") == "etalonnage"
    assert normalize("Sous‑titres… (SRT)") == "sous titres srt"
    assert normalize("Œuvre ÇA") == "oeuvre ca"
    assert normalize("") == "" and normalize("***") == ""


def test_clean_label_drops_mnemonics_shortcut_and_ellipsis():
    assert clean_label("&Fichier") == "Fichier"
    assert clean_label("Enregistrer sous…\tCtrl+Maj+S") == "Enregistrer sous"
    assert clean_label("Fenêtre & Co &&") == "Fenêtre & Co &"


@pytest.mark.parametrize("query", ["etalonnage", "ÉTALONNAGE", "Étalonnage", "étalo", "ETALO"])
def test_search_ignores_accents_and_case(query):
    assert ids(search(CATALOG, query, ADVANCED)) == ["grade"]


@pytest.mark.parametrize("query,expected", [
    ("cut", "cut"), ("split", "cut"), ("trancher", "cut"), ("track", "track"), ("tracking", "track"), ("captions", "srt"),
    ("srt", "srt"), ("color", "grade"), ("couleur", "grade"), ("speed", "speed"), ("ralenti", "speed"), ("exporter", "export"),
])
def test_synonyms_french_english_find_the_entry(query, expected):
    assert expected in ids(search(CATALOG, query, ADVANCED))


def test_english_title_is_searched_next_to_the_current_language():
    assert ids(search(CATALOG, "playhead", ADVANCED)) == ["cut"]
    assert ids(search(CATALOG, "grading", ADVANCED)) == ["grade"]


def test_hint_and_keywords_are_searched():
    assert ids(search(CATALOG, "inspecteur", ADVANCED)) == ["speed"]
    keyworded = [entry("x", "Outil", keywords=("lueur", "glow"))]
    assert ids(search(keyworded, "glow", SIMPLE)) == ["x"]


def test_all_words_must_match():
    assert ids(search(CATALOG, "importer srt", ADVANCED)) == ["srt"]
    assert search(CATALOG, "importer vitesse", ADVANCED) == []


def test_title_beats_keyword_beats_hint_and_exact_beats_prefix():
    entries = [
        entry("hint", "Autre", hint="Marqueur"),
        entry("kw", "Autre chose", keywords=("marqueur",)),
        entry("title_later", "Ajouter un marqueur"),
        entry("title_first", "Marqueur rapide"),
        entry("exact", "Marqueur"),
    ]
    assert ids(search(entries, "marqueur", ADVANCED)) == ["exact", "title_first", "title_later", "kw", "hint"]


def test_synonym_weighs_less_than_a_direct_match():
    entries = [entry("syn", "Cut rapide"), entry("direct", "Couper net")]
    assert ids(search(entries, "couper", ADVANCED)) == ["direct", "syn"]


def test_level_marks_higher_entries_locked_without_dropping_them():
    results = {r.entry.id: r for r in search(CATALOG, "suivi", SIMPLE)}
    assert results["track"].locked and not results["track"].available
    assert not {r.entry.id: r for r in search(CATALOG, "suivi", ADVANCED)}["track"].locked
    assert search(CATALOG, "etalonnage", SIMPLE)[0].locked
    assert not search(CATALOG, "etalonnage", STUDIO)[0].locked
    assert not search(CATALOG, "couper", SIMPLE)[0].locked


def test_available_entries_come_before_locked_ones_at_equal_score():
    entries = [entry("hi", "Régler", level=ADVANCED), entry("lo", "Régler")]
    assert ids(search(entries, "regler", SIMPLE)) == ["lo", "hi"]
    assert ids(search(list(reversed(entries)), "regler", SIMPLE)) == ["lo", "hi"]


def test_disabled_entries_come_after_enabled_at_equal_score():
    entries = [entry("off", "Régler", enabled=False), entry("on", "Régler")]
    assert ids(search(entries, "regler", ADVANCED)) == ["on", "off"]


def test_results_are_limited_to_six():
    many = [entry(f"e{i}", f"Marqueur {i}") for i in range(40)]
    assert len(search(many, "marqueur", ADVANCED)) == MAX_RESULTS == 6
    assert len(search(many, "marqueur", ADVANCED, limit=3)) == 3
    assert search(many, "marqueur", ADVANCED, limit=0) == []


@pytest.mark.parametrize("query", ["", "   ", "***", "zzzzzz"])
def test_no_result(query):
    assert search(CATALOG, query, ADVANCED) == []


def test_huge_input_is_bounded():
    start = time.perf_counter()
    assert ids(search(CATALOG, "couper " * 1_000_000, ADVANCED)) == ["cut"]   # 7 Mo : tronqué avant tout traitement
    assert len(tokens("a " * 100_000)) <= 8
    assert len(normalize("x" * 10)) == 10
    assert time.perf_counter() - start < 30          # borne large : le but est de ne pas allouer 7 Mo de mots
    assert MAX_QUERY_CHARS == 200


def test_recent_results_keep_order_and_ignore_vanished_ids():
    got = recent_results(CATALOG, ["export", "gone", "cut"], SIMPLE)
    assert ids(got) == ["export", "cut"]
    assert recent_results(CATALOG, [], SIMPLE) == []
    assert recent_results(CATALOG, ["track"], SIMPLE)[0].locked


def test_synonym_families_are_normalized():
    for group in SYNONYMS:
        assert all(term == normalize(term) and " " not in term for term in group), group
