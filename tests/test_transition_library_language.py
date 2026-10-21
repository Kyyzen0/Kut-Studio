"""Bibliothèque de transitions : cartes, recherche, boîte « Enregistrer » et inspecteur suivent tous les types et la langue."""

from __future__ import annotations

import dataclasses

import pytest

from core.transition_presets import (
    builtin_transition_presets,
    make_user_transition_preset,
)
from core.transitions import TRANSITION_FAMILIES, TransitionType, transition_type_values
from ui import i18n
from ui.project_panel_widgets.transition_library import (
    SaveTransitionPresetDialog,
    TransitionLibraryView,
)
from ui.properties_panel import PropertiesPanel


@pytest.fixture(autouse=True)
def _language():
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _expected(value: str, language: str, part: str) -> str:
    return i18n.translate_strict(f"transitions.preset.{value}.{part}", language)


def _view(qtbot, presets=None) -> TransitionLibraryView:
    view = TransitionLibraryView()
    qtbot.addWidget(view)
    view.set_presets(
        list(presets if presets is not None else builtin_transition_presets())
    )
    return view


@pytest.mark.parametrize("language", ["fr", "en", "es"])
def test_the_43_cards_show_the_interface_language(qtbot, language):
    i18n.set_language(language)
    view = _view(qtbot)
    assert view.preset_count() == 43
    for preset_id, card in view._cards.items():
        assert card._title.text() == _expected(preset_id, language, "name")
        description = _expected(preset_id, language, "description")
        shown = card._description.text()
        assert shown == (
            description if len(description) <= 80 else description[:79].rstrip() + "…"
        )


def test_the_english_cards_are_not_in_french(qtbot):
    i18n.set_language("en")
    view = _view(qtbot)
    french = {p.id: p.name for p in builtin_transition_presets()}
    assert {
        pid: card._title.text()
        for pid, card in view._cards.items()
        if card._title.text() == french[pid]
    } == {}


@pytest.mark.parametrize(
    ("language", "needle", "expected"),
    [
        ("en", "Cover left", "cover_left"),
        ("es", "Cubrir a la izquierda", "cover_left"),
        ("fr", "Recouvrement gauche", "cover_left"),
        ("en", "Crossfade", "crossfade"),
    ],
)
def test_search_uses_the_displayed_name(qtbot, language, needle, expected):
    i18n.set_language(language)
    view = _view(qtbot)
    view.search_field.setText(needle.lower())
    assert expected in view._cards


def test_a_user_preset_is_shown_as_typed_and_never_modified(qtbot):
    i18n.set_language("en")
    user = make_user_transition_preset(
        "Mon effet", "Ma description", TransitionType.CROSSFADE, 0.5
    )
    original = dataclasses.replace(user)
    view = _view(qtbot, [*builtin_transition_presets(), user])
    assert view._cards[user.id]._title.text() == "Mon effet"
    assert user == original
    assert (
        builtin_transition_presets()[0].name == "Fondu enchaîné"
    )  # le contenu de core reste en français


@pytest.mark.parametrize("member", list(TransitionType), ids=lambda t: t.value)
def test_the_save_dialog_preselects_and_returns_every_type(qtbot, member):
    dialog = SaveTransitionPresetDialog(default_name="x", default_type=member.value)
    qtbot.addWidget(dialog)
    assert dialog.type_combo.currentData() == member.value
    assert dialog.result_data()[2] == member.value


def test_type_values_follow_the_families_and_cover_everything():
    values = transition_type_values()
    assert len(values) == len(set(values)) == 43 == len(TransitionType)
    assert values[:3] == tuple(t.value for t in TRANSITION_FAMILIES["fade"][:3])


def test_the_inspector_offers_the_43_types(qtbot, monkeypatch, tmp_path):
    monkeypatch.setenv("KUT_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
    panel = PropertiesPanel(lambda *_: None)
    qtbot.addWidget(panel)
    combo = panel.transition_type_combo
    assert [combo.itemData(i) for i in range(combo.count())] == list(
        transition_type_values()
    )
    for i in range(combo.count()):
        assert combo.itemText(i) == i18n.translate(
            f"transitions.preset.{combo.itemData(i)}.name"
        )
