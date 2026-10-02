"""Un geste continu (glissement d'un fader) ne forme qu'une entrée d'annulation.

Chaque cran de fader enregistrait un instantané complet du projet : dix crans = dix entrées à annuler une par une,
dix copies du projet, et la pile plafonnée à 100 chassait de vraies éditions.
"""

from __future__ import annotations

from core.edit_history import MAX_HISTORY, ProjectHistory
from core.project_factory import create_default_project


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _history() -> tuple[ProjectHistory, _Clock]:
    clock = _Clock()
    history = ProjectHistory(clock=clock)
    history.reset(create_default_project())
    return history, clock


def _set_volume(project, value: float) -> None:
    project.name = f"volume {value}"


def test_a_burst_with_the_same_key_is_one_entry_holding_the_final_state():
    history, _clock = _history()
    project = create_default_project()
    for value in range(10):
        _set_volume(project, value)
        history.record(project, "Volume", merge_key="fader")
    assert len(history) == 2                                     # état initial + une entrée
    assert history.current_project().name == "volume 9"          # type: ignore[union-attr]


def test_undoing_a_merged_gesture_goes_back_to_before_the_gesture_in_one_step():
    history, _clock = _history()
    initial_name = history.current_project().name                # type: ignore[union-attr]
    project = create_default_project()
    for value in range(10):
        _set_volume(project, value)
        history.record(project, "Volume", merge_key="fader")
    restored = history.undo()
    assert restored is not None and restored.name == initial_name
    assert not history.can_undo
    assert history.redo().name == "volume 9"                     # type: ignore[union-attr]


def test_a_pause_longer_than_the_window_starts_a_new_entry():
    history, clock = _history()
    project = create_default_project()
    history.record(project, "Volume", merge_key="fader")
    clock.now += 5.0                                             # l'utilisateur reprend plus tard : autre réglage
    history.record(project, "Volume", merge_key="fader")
    assert len(history) == 3


def test_a_different_key_or_no_key_never_merges():
    history, _clock = _history()
    project = create_default_project()
    history.record(project, "Volume A", merge_key="fader-a")
    history.record(project, "Volume B", merge_key="fader-b")
    history.record(project, "Autre")
    history.record(project, "Autre")
    assert len(history) == 5


def test_an_unrelated_edit_between_two_gestures_with_the_same_key_separates_them():
    history, _clock = _history()
    project = create_default_project()
    history.record(project, "Volume", merge_key="fader")
    history.record(project, "Couper")                            # autre action entre les deux
    history.record(project, "Volume", merge_key="fader")
    assert len(history) == 4


def test_undo_closes_the_gesture():
    history, _clock = _history()
    project = create_default_project()
    history.record(project, "Volume", merge_key="fader")
    history.record(project, "Volume", merge_key="fader")
    history.undo()
    history.redo()
    history.record(project, "Volume", merge_key="fader")         # nouveau geste, pas la suite de l'ancien
    assert len(history) == 3


def test_an_entry_just_saved_is_never_overwritten():
    """Sinon le projet modifié passerait pour « enregistré » : l'indicateur mentirait."""
    history, _clock = _history()
    project = create_default_project()
    history.record(project, "Volume", merge_key="fader")
    history.mark_saved()
    assert not history.is_dirty
    _set_volume(project, 42.0)
    history.record(project, "Volume", merge_key="fader")
    assert history.is_dirty and len(history) == 3


def test_the_initial_state_is_never_merged_away():
    history, _clock = _history()
    project = create_default_project()
    history.record(project, "Volume", merge_key="fader")
    assert history.undo() is not None                            # on retrouve l'état initial, intact
    assert history.undo_label is None


def test_a_gesture_does_not_push_real_edits_out_of_the_bounded_stack():
    history, _clock = _history()
    project = create_default_project()
    for index in range(20):
        history.record(project, f"Édition {index}")
    for value in range(500):                                     # un très long glissement
        history.record(project, "Volume", merge_key="fader")
    assert len(history) == 22 and len(history) < MAX_HISTORY


def test_the_window_is_configurable_per_call():
    history, clock = _history()
    project = create_default_project()
    history.record(project, "Volume", merge_key="fader", merge_window=0.1)
    clock.now += 0.5
    history.record(project, "Volume", merge_key="fader", merge_window=0.1)
    assert len(history) == 3


# --- fenêtre -------------------------------------------------------------------------------------------


def test_dragging_a_mixer_fader_is_one_undo_step(qtbot, monkeypatch):
    from test_scopes import _window

    window = _window(qtbot, monkeypatch)
    audio = next(track for track in window.project.tracks if track.type == "audio")
    original = audio.volume_db
    before = len(window.history)

    for value in range(-1, -11, -1):                             # un glissement : dix crans
        window.on_track_volume_changed(audio.id, float(value))
    assert len(window.history) == before + 1

    window.undo_last()
    audio_after_undo = next(track for track in window.project.tracks if track.type == "audio")
    assert audio_after_undo.volume_db == original                # un seul Ctrl+Z rend le volume d'origine
