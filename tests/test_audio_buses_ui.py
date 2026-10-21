"""Bus audio depuis l'interface : « + Bus », tranche de bus, sortie de chaque piste, inserts du bus, historique."""

from __future__ import annotations

import pytest
from main_window_harness import build_window, install_dialogs
from PySide6.QtWidgets import QInputDialog

from core.audio_effects_model import AudioEffectType


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    install_dialogs(monkeypatch)
    window = build_window(qtbot, monkeypatch, tmp_path / "config")
    window.history.reset(window.project)
    return window


def _steps(window) -> int:
    return len(window.history._undo_stack)


def _track(window, track_id: str):
    return next(t for t in window.project.tracks if t.id == track_id)


def _add_bus(window):
    window.mixer_panel.add_bus_button.click()
    return window.project.buses[-1]


def test_the_add_bus_button_creates_a_bus_with_its_own_strip_as_one_undo_step(window):
    before = _steps(window)
    bus = _add_bus(window)
    assert [b.name for b in window.project.buses] == ["Bus 1"] and _steps(
        window
    ) == before + 1
    strip = window.mixer_panel.bus_strip_for(bus.id)
    assert strip is not None and strip.is_bus
    assert not strip.pan_slider.isVisibleTo(
        strip
    ) and not strip.solo_button.isVisibleTo(strip)
    # La tranche d'une piste propose le nouveau bus parmi ses sorties.
    combo = window.mixer_panel.strip_for("A1").route_combo
    assert [combo.itemText(i) for i in range(combo.count())] == ["Master", "Bus 1"]
    window.undo_last()
    assert (
        window.project.buses == [] and window.mixer_panel.bus_strip_for(bus.id) is None
    )


def test_choosing_a_bus_in_a_track_strip_routes_the_track_and_is_undoable(window):
    bus = _add_bus(window)
    combo = window.mixer_panel.strip_for("A1").route_combo
    combo.setCurrentIndex(1)
    combo.activated.emit(1)
    assert _track(window, "A1").bus_id == bus.id
    assert window.mixer_panel.strip_for("A1").route_combo.currentData() == bus.id, (
        "le choix survit à la reconstruction"
    )
    window.undo_last()
    assert _track(window, "A1").bus_id == ""


def test_a_fader_gesture_on_a_bus_is_one_step_and_mute_follows(window):
    bus = _add_bus(window)
    before = _steps(window)
    for value in (-1.0, -3.0, -6.0):
        window.mixer_panel.bus_volume_changed.emit(bus.id, value)
    assert window.project.buses[0].volume_db == -6.0 and _steps(window) == before + 1
    window.mixer_panel.bus_mute_toggled.emit(bus.id, True)
    assert window.project.buses[0].muted is True and _steps(window) == before + 2
    assert window.mixer_panel.bus_strip_for(bus.id).mute_button.isChecked()


def test_deleting_a_bus_sends_its_tracks_back_to_the_master_and_undo_restores_both(
    window,
):
    bus = _add_bus(window)
    window.mixer_panel.track_route_changed.emit("A1", bus.id)
    assert _track(window, "A1").bus_id == bus.id
    window.mixer_panel.bus_strip_for(bus.id).remove_button.click()
    assert window.project.buses == [] and _track(window, "A1").bus_id == ""
    window.undo_last()
    assert [b.id for b in window.project.buses] == [bus.id] and _track(
        window, "A1"
    ).bus_id == bus.id


def test_renaming_a_bus_asks_for_the_name(window, monkeypatch):
    bus = _add_bus(window)
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: ("Voix", True))
    )
    window.mixer_panel.bus_rename_requested.emit(bus.id)
    assert window.project.buses[0].name == "Voix"
    assert window.mixer_panel.bus_strip_for(bus.id).name_label.text() == "Voix"
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: ("Autre", False))
    )
    window.mixer_panel.bus_rename_requested.emit(bus.id)
    assert window.project.buses[0].name == "Voix", "annuler le dialogue ne change rien"


def test_the_bus_fx_button_opens_the_bus_chain_and_edits_it(window):
    bus = _add_bus(window)
    window.mixer_panel.bus_strip_for(bus.id).inserts_button.click()
    dialog = window._bus_inserts_dialog
    assert dialog.isVisible() and dialog.track_id == bus.id
    before = _steps(window)
    dialog.add_requested.emit(bus.id, AudioEffectType.LIMITER.value)
    assert [e.type for e in window.project.buses[0].inserts] == [
        AudioEffectType.LIMITER
    ]
    assert _steps(window) == before + 1
    assert window.mixer_panel.bus_strip_for(bus.id).inserts_button.text() == "FX 1"
    spin = dialog.spin_boxes["limit_db"]
    for value in (-2.0, -3.0, -4.0):
        spin.setValue(value)
    assert (
        dialog.spin_boxes["limit_db"] is spin
        and window.project.buses[0].inserts[0].params["limit_db"] == -4.0
    )
    assert _steps(window) == before + 2, "un geste de réglage est une seule étape"
    window.undo_last()
    window.undo_last()
    assert window.project.buses[0].inserts == [] and dialog.list.count() == 0


def test_a_locked_track_cannot_be_routed(window):
    bus = _add_bus(window)
    _track(window, "A1").locked = True
    window.mixer_panel.track_route_changed.emit("A1", bus.id)
    assert _track(window, "A1").bus_id == ""
