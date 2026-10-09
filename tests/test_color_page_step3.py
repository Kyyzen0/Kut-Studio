"""Page Couleur, étape 3 : les limites levées (outils du moniteur sans GPU, pipette), fenêtres, flou et netteté."""

from __future__ import annotations

import pytest

from core.color_render import Compare, Highlight
from core.workspace_state import PAGE_COLOR, PAGE_EDIT


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    from main_window_harness import build_window, install_dialogs

    install_dialogs(monkeypatch)
    win = build_window(qtbot, monkeypatch, tmp_path / "config")
    win.resize(1440, 900)
    win.show()
    qtbot.waitExposed(win)
    return win


def _clip(window):
    clip = window.project.tracks[0].clips[0]
    window.on_clip_selected(clip.id)
    window.switch_page(PAGE_COLOR)
    return clip.id


def _qualify(window):
    window.color_panel.qualifier.enabled_check.setChecked(True)
    window.color_panel.qualifier.spins["hue_width"].setValue(60)
    window._finalize_color_history()


# --- outils du moniteur, sans GPU ------------------------------------------------------------------------------------


def test_the_monitor_tools_are_available_without_the_gpu_monitor(window):
    """Avant l'étape 3, *Afficher la sélection* et avant / après étaient grisés sans moniteur GPU."""
    _clip(window)
    assert window.color_panel.compare_button.isEnabled()
    assert window.color_panel.qualifier.highlight_button.isEnabled()


def test_the_faithful_preview_shows_the_selection_and_the_comparison(window):
    """Sans GPU (ou pour un montage hors de sa couverture), les segments fidèles montrent ce que le moniteur GPU montre :
    la sélection, l'avant / après ; rien quand les outils sont arrêtés ou hors de la page Couleur."""
    clip_id = _clip(window)
    assert window._preview_grade_overrides() is None
    _qualify(window)
    window.on_color_highlight_toggled(True)
    shown = window._preview_grade_overrides()[clip_id]
    assert isinstance(shown, Highlight) and shown.node_id == "n1"
    window._set_color_compare(0.4)
    compared = window._preview_grade_overrides()[clip_id]
    assert isinstance(compared, Compare) and isinstance(compared.value, Highlight)
    assert compared.split == pytest.approx(0.4), "plan plein cadre : la part du cadre est celle du clip"
    jobs = window._preview_segment_jobs(0.0)
    layer = next(layer for job in jobs for layer in job.plan.video_layers if layer.clip_id == clip_id)
    assert isinstance(layer.color_grade, Compare), "les segments planifiés portent ce qu'on montre"
    window.on_color_highlight_toggled(False)
    assert window._preview_grade_overrides()[clip_id].value is window.project.tracks[0].clips[0].color_grade
    window.switch_page(PAGE_EDIT)
    assert window._preview_grade_overrides() is None, "hors de la page Couleur, le vrai étalonnage"


def test_moving_the_compare_line_refreshes_the_faithful_segments_once_it_stops(qtbot, window, monkeypatch):
    clip_id = _clip(window)
    refreshed: list[str] = []
    monkeypatch.setattr(window, "_refresh_color_monitor", refreshed.append)
    for split in (0.3, 0.35, 0.4):
        window._set_color_compare(split)
    assert refreshed == [], "pas un rendu par mouvement"
    qtbot.waitUntil(lambda: refreshed == [clip_id], timeout=2000)


def test_the_compare_line_is_converted_into_the_picture_of_a_moved_clip(window):
    """Un clip réduit de moitié et centré occupe le cadre de 25 % à 75 % : le trait au quart du cadre tombe au bord
    gauche de son image, au milieu du cadre au milieu de son image."""
    panel = window.preview_panel
    panel._applied_scale = 0.5
    assert panel.compare_split_in_layer(0.5) == pytest.approx(0.5)
    assert panel.compare_split_in_layer(0.25) == pytest.approx(0.0, abs=1e-6)
    assert panel.compare_split_in_layer(0.625) == pytest.approx(0.75)
    panel._applied_scale = 1.0
    assert panel.compare_split_in_layer(0.3) == pytest.approx(0.3)

