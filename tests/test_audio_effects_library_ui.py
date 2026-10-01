"""Bibliothèque de préréglages d'effets audio (section Audio)."""

from core.audio_effects_library import builtin_audio_effect_presets
from ui.audio_effects_library import AudioEffectsLibraryView
from ui.project_panel import ProjectPanel


def test_view_lists_all_builtin_presets(qtbot) -> None:
    view = AudioEffectsLibraryView()
    qtbot.addWidget(view)
    assert view.preset_count() == len(builtin_audio_effect_presets())
    assert set(view._cards) == {p.id for p in builtin_audio_effect_presets()}


def test_search_filters_cards(qtbot) -> None:
    view = AudioEffectsLibraryView()
    qtbot.addWidget(view)
    view.search_field.setText("zzz-aucun")
    assert not view._cards
    assert view.selected_preset_id() is None
    view.search_field.setText("")
    assert view._cards


def test_apply_requires_clip_and_emits_selected_preset(qtbot) -> None:
    view = AudioEffectsLibraryView()
    qtbot.addWidget(view)
    preset_id = builtin_audio_effect_presets()[1].id
    assert view.select_preset(preset_id)
    assert not view.apply_button.isEnabled()

    view.set_clip_context(has_audio_clip=True)
    assert view.apply_button.isEnabled()
    with qtbot.waitSignal(view.apply_requested) as blocker:
        view.apply_button.click()
    assert blocker.args == [preset_id]


def test_audio_section_toggles_between_files_and_effects(qtbot) -> None:
    panel = ProjectPanel()
    qtbot.addWidget(panel)
    panel.select_section("audio")
    assert panel.content_stack.currentWidget() is panel.bin_audios
    assert not panel.audio_mode_row.isHidden()

    panel.set_audio_mode("effects")
    assert panel.content_stack.currentWidget() is panel.audio_effects_view
    assert "presets" in panel.media_count.text()

    panel.select_section("media")
    assert panel.audio_mode_row.isHidden()
    panel.select_section("audio")
    assert panel.content_stack.currentWidget() is panel.audio_effects_view
