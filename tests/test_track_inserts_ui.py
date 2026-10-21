"""Inserts de piste depuis l'interface : le bouton FX de la tranche, la boîte de la chaîne, l'historique."""

from __future__ import annotations

import pytest
from main_window_harness import build_window, install_dialogs

from core.audio_effects_model import AudioEffectType


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    install_dialogs(monkeypatch)
    window = build_window(qtbot, monkeypatch, tmp_path / "config")
    window.history.reset(window.project)
    return window


def _steps(window) -> int:
    return len(window.history._undo_stack)


def _dialog(window):
    window.mixer_panel.inserts_requested.emit("A1")
    return window._inserts_dialog


def test_the_fx_button_opens_the_chain_of_its_track_and_counts_its_inserts(window):
    strip = window.mixer_panel.strip_for("A1")
    assert strip.inserts_button.text() == "FX"
    dialog = _dialog(window)
    assert dialog.isVisible() and dialog.track_id == "A1"
    dialog.add_requested.emit("A1", AudioEffectType.COMPRESSOR.value)
    dialog.add_requested.emit("A1", AudioEffectType.LIMITER.value)
    track = next(t for t in window.project.tracks if t.id == "A1")
    assert [e.type for e in track.inserts] == [
        AudioEffectType.COMPRESSOR,
        AudioEffectType.LIMITER,
    ]
    assert window.mixer_panel.strip_for("A1").inserts_button.text() == "FX 2"
    assert dialog.list.count() == 2 and dialog.list.currentRow() == 1, (
        "l'insert ajouté est sélectionné"
    )


def test_each_structural_edit_is_one_undo_step_and_undo_updates_the_open_dialog(window):
    dialog = _dialog(window)
    before = _steps(window)
    dialog.add_requested.emit("A1", AudioEffectType.LIMITER.value)
    assert _steps(window) == before + 1
    track = next(t for t in window.project.tracks if t.id == "A1")
    insert_id = track.inserts[0].id
    dialog.enabled_changed.emit("A1", insert_id, False)
    assert track.inserts[0].enabled is False and _steps(window) == before + 2

    window.undo_last()
    track = next(t for t in window.project.tracks if t.id == "A1")
    assert track.inserts[0].enabled is True
    assert "·" not in dialog.list.item(0).text(), (
        "l'insert n'est plus contourné dans la boîte ouverte"
    )
    window.undo_last()
    assert next(t for t in window.project.tracks if t.id == "A1").inserts == []
    assert (
        dialog.list.count() == 0
        and window.mixer_panel.strip_for("A1").inserts_button.text() == "FX"
    )


def test_turning_a_parameter_is_one_undo_step_and_keeps_the_spin_box(window):
    dialog = _dialog(window)
    dialog.add_requested.emit("A1", AudioEffectType.LIMITER.value)
    before = _steps(window)
    spin = dialog.spin_boxes["limit_db"]
    for value in (-2.0, -3.0, -4.0):
        spin.setValue(value)
    assert dialog.spin_boxes["limit_db"] is spin, (
        "la boîte ne reconstruit pas les réglages sous la main"
    )
    track = next(t for t in window.project.tracks if t.id == "A1")
    assert track.inserts[0].params["limit_db"] == -4.0
    assert _steps(window) == before + 1


def test_a_locked_track_refuses_inserts_and_the_dialog_says_nothing_changed(window):
    dialog = _dialog(window)
    track = next(t for t in window.project.tracks if t.id == "A1")
    track.locked = True
    dialog.add_requested.emit("A1", AudioEffectType.LIMITER.value)
    assert track.inserts == []
