"""Changer de langue rafraîchit aussi les textes qui dépendent d'un **état** (relecture de la PR i18n).

Deux endroits ne suivaient pas : les entrées synthétiques de l'arbre des dossiers (« Tous », « Racine », « Manquants »,
avec leur compteur) et les catégories des effets audio, qui venaient d'un tableau français du cœur. Un troisième point
soulevé (l'indicateur « Enregistré / Non enregistré » et le nom par défaut du projet, écrits par ``_update_top_bar``)
était déjà rafraîchi : ``_retranslate_ui`` finit par ``_refresh_undo_redo_state``, qui appelle ``_update_top_bar``. Les
deux tests correspondants verrouillent ce comportement (ils passent sans changement de code).
"""

from __future__ import annotations

import pytest
from test_scopes import _window

from core.audio_effects_library import AudioEffectPresetCategory, builtin_audio_effect_presets
from core.library_organization import LibraryOrganization
from core.project_model import Project
from ui import i18n
from ui.audio_effects_library import AudioEffectPresetCard, AudioEffectsLibraryView
from ui.library_organization_widgets import FolderTreeWidget


@pytest.fixture(autouse=True)
def _language_reset():
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def test_the_saved_and_dirty_indicators_follow_the_language(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    for dirty, key in ((False, "topbar.saved"), (True, "topbar.dirty")):
        if dirty:                                   # un vrai changement : l'état « modifié » vient de l'historique
            window._record_history("Test")
            window._mark_dirty()
        window._update_top_bar()
        for language in ("en", "es", "fr"):
            i18n.set_language(language)
            window._retranslate_ui()
            assert window.project_dirty is dirty
            assert window.saved_indicator.text() == i18n.translate(key), (language, key)


def test_the_default_project_name_follows_the_language(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    window.project.name = "Projet sans titre"  # i18n-ignore: nom par défaut du cœur, comparé tel quel
    window._update_top_bar()
    i18n.set_language("en")
    window._retranslate_ui()
    assert window.project_label.text() == i18n.translate("topbar.default_name") != "Projet sans titre"  # i18n-ignore: idem


def test_the_synthetic_folder_entries_are_retranslated_with_their_counts_and_selection(qtbot):
    tree = FolderTreeWidget()
    qtbot.addWidget(tree)
    organization = LibraryOrganization(Project(name="p", width=320, height=180, fps=30.0))
    tree.set_organization(organization, has_missing=True)
    tree.set_folder_counts({"__all__": 4, "__root__": 3, "__missing__": 1})
    tree.tree.setCurrentItem(tree.tree.topLevelItem(1))                       # « Racine »

    i18n.set_language("en")
    tree.retranslate()

    labels = [tree.tree.topLevelItem(i).text(0) for i in range(tree.tree.topLevelItemCount())]
    assert i18n.translate("library.filter.all") in labels[0] and "4" in labels[0]
    assert i18n.translate("library.folder.root") in labels[1] and "3" in labels[1]
    assert i18n.translate("library.filter.missing") in labels[2] and "1" in labels[2]
    assert tree.tree.currentItem() is tree.tree.topLevelItem(1)               # la sélection est conservée
    assert tree.header_label.text() == i18n.translate("library.folders.title")


def test_the_project_panel_retranslates_its_populated_folder_tree(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    panel = window.project_panel
    organization = LibraryOrganization(window.project)
    panel.folder_tree.set_organization(organization, has_missing=False)
    panel.folder_tree.set_folder_counts({"__all__": 2, "__root__": 2})

    i18n.set_language("es")
    panel.retranslate()

    assert i18n.translate("library.filter.all") in panel.folder_tree.tree.topLevelItem(0).text(0)


@pytest.mark.parametrize("language", ["fr", "en", "es"])
def test_every_audio_effect_category_is_translated_in_the_row_and_on_the_cards(qtbot, language):
    i18n.set_language(language)
    view = AudioEffectsLibraryView()
    qtbot.addWidget(view)

    texts = [button.text() for button in view.category_buttons]
    expected = [i18n.translate("library.audio_effects.all")] + [
        i18n.translate(f"audio_effects.category.{category.value}").upper() for category in AudioEffectPresetCategory
    ]
    assert texts == expected
    preset = builtin_audio_effect_presets()[0]
    card = AudioEffectPresetCard(preset)
    qtbot.addWidget(card)
    assert i18n.translate(f"audio_effects.category.{preset.category.value}").upper() in _all_texts(card)
    if language != "fr":                                                       # plus aucune étiquette française du cœur
        assert not {"DYNAMIQUE", "NETTOYAGE", "ÉGALISEUR", "SPATIALISATION"} & (set(texts) | set(_all_texts(card)))


def _all_texts(widget) -> list[str]:
    from PySide6.QtWidgets import QLabel

    return [label.text() for label in widget.findChildren(QLabel)]
