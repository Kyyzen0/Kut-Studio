"""Lecture seule dans la vraie fenêtre (ADR-0013, étape 12) : menus, raccourcis, palette, glisser-déposer, export, garde du modèle.

Chaque test construit une ``MainWindow`` hors écran, configuration isolée. Hors Store, rien ne change.
"""

from __future__ import annotations

import pytest
from main_window_harness import build_window, install_dialogs, menu_action, track_ids

from core import entitlements
from core.entitlements import READ_ONLY_COMMANDS, READ_ONLY_MENU_KEYS
from core.shortcuts import COMMANDS
from tests.entitlements_support import T0, expired, lifetime, make_service, store_flavor, trial  # noqa: F401 - fixtures


@pytest.fixture
def make_window(qtbot, monkeypatch, tmp_path, tmp_path_factory):
    """``make_window(record)`` : construit la fenêtre avec un service installé (contrôle exigé) ; sans ``record``, hors Store."""
    install_dialogs(monkeypatch)
    state_dir = tmp_path_factory.mktemp("state")
    created = []

    def build(record, enforced: bool = True):
        if enforced:
            entitlements.set_enforced_provider(lambda: True)
            service, _store, _clock = make_service(state_dir)
            service.publish(record)
            entitlements.install_service(service)
        window = build_window(qtbot, monkeypatch, tmp_path / "config")
        created.append(window)
        return window

    yield build
    entitlements.set_enforced_provider(None)
    entitlements.install_service(None)


def _actions(window):
    actions = {id(a): a for a in window.shortcuts.command_actions()}
    actions.update({id(a): a for a, _key in window._translated_actions})
    return list(actions.values())


def test_outside_the_store_the_window_is_untouched(make_window):
    window = make_window(None, enforced=False)
    assert not hasattr(window, "_entitlement_timer"), "aucun minuteur hors Store"
    assert window._entitlement_disabled == [] and window.properties_panel.isEnabled()
    assert window.export_panel.launch_button.isEnabled() and window.export_panel.add_button.isEnabled()
    before = [(a.text(), a.isEnabled()) for a in _actions(window)]
    window.refresh_entitlements()
    assert [(a.text(), a.isEnabled()) for a in _actions(window)] == before
    count = len(window.project.tracks)
    window.on_add_track_requested("video")
    assert len(window.project.tracks) == count + 1 and window.history.can_undo
    window.undo_last()
    assert len(window.project.tracks) == count


def test_read_only_greys_menus_and_panels_and_export_buttons(make_window):
    window = make_window(expired())
    assert hasattr(window, "_entitlement_timer")
    assert not window.delete_action.isEnabled() and not window.undo_action.isEnabled()
    assert not menu_action(window, "tracks.add_video_long").isEnabled()
    assert not menu_action(window, "menu.item.export_srt").isEnabled()
    assert not window.properties_panel.isEnabled() and not window.mixer_panel.isEnabled()
    assert not window.export_panel.launch_button.isEnabled() and not window.export_panel.add_button.isEnabled()
    assert window.export_panel.launch_button.toolTip(), "l'explication est en infobulle"
    # Ouvrir, enregistrer, naviguer, afficher : permis.
    for command_id in ("project_open", "project_new", "project_save", "quit", "page_color", "toggle_scopes", "preferences"):
        assert window.shortcuts.action(command_id).isEnabled(), command_id
    assert menu_action(window, "update.menu.about").isEnabled()


def test_a_shortcut_cannot_run_an_edit_command_even_on_an_enabled_action(make_window):
    window = make_window(expired())
    ran = []
    window.shortcuts._handlers["delete_clip"] = lambda: ran.append("delete")
    window.shortcuts._handlers["page_color"] = lambda: ran.append("page")
    window.delete_action.setEnabled(True)                 # une mise à jour d'état l'a réactivée : le garde tient
    window.delete_action.trigger()
    window.shortcuts.trigger("delete_clip")
    window.shortcuts.trigger("page_color")
    assert ran == ["page"]
    assert "lecture seule" in window.statusBar().currentMessage()


def test_the_model_guard_catches_a_signal_that_reaches_the_model(make_window):
    window = make_window(lifetime())
    window.on_add_track_requested("video")                # permis : l'utilisateur est abonné
    window._mark_clean()
    tracks = track_ids(window)
    project, revision = window.project, window.history.revision
    assert window.project_dirty is False
    entitlements.install_service(make_service_for_expired(window))
    window.on_add_track_requested("audio")                # un signal atteint le modèle malgré les menus grisés
    assert track_ids(window) == tracks, "la modification est remise en place"
    assert window.project is project, "même objet"
    assert window.history.revision == revision, "rien n'est entré dans l'historique"
    assert window.project_dirty is False, "pas « modifié » : rien à enregistrer, aucune sauvegarde automatique armée"
    assert "lecture seule" in window.statusBar().currentMessage()


def make_service_for_expired(window):
    from tests.entitlements_support import Clock
    import tempfile
    from pathlib import Path

    service, _store, _clock = make_service(Path(tempfile.mkdtemp()), Clock(T0))
    service.publish(expired())
    return service


def test_undo_redo_and_history_jump_are_refused(make_window):
    window = make_window(lifetime())
    window.on_add_track_requested("video")
    entitlements.install_service(make_service_for_expired(window))
    tracks = track_ids(window)
    window.undo_last()
    window.redo_last()
    window.go_to_history_state(0)
    assert track_ids(window) == tracks


def test_drops_and_imports_are_refused(make_window, tmp_path, monkeypatch):
    window = make_window(expired())
    import ui.main_window_mixins.media_import as media_import
    monkeypatch.setattr(media_import, "cached_probe", lambda *_a, **_k: pytest.fail("le média est sondé en lecture seule"))
    media = tmp_path / "x.mp4"
    media.write_bytes(b"")
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_a, **_k: pytest.fail("le dialogue d'import s'ouvre en lecture seule"))
    from core.project_model import MediaAsset
    asset = MediaAsset("known", str(media), "x", 5.0, 640, 360, 25.0, "video", has_audio=False)
    window.project.media_assets.append(asset)
    track_id = window.project.tracks[0].id
    monkeypatch.setattr(window, "_record_history", lambda *_a, **_k: pytest.fail("une édition atteint l'historique"))
    monkeypatch.setattr(window.history, "record", lambda *_a, **_k: pytest.fail("une édition atteint l'historique"))
    assets, clips = len(window.project.media_assets), len(window.project.tracks[0].clips)
    assert window.import_media_to_project(str(media)) is False
    window.import_media_via_dialog()
    window.on_asset_dropped("known", track_id, 0.0)
    window.on_sequence_dropped("nope", track_id, 0.0)
    window.fill_slots_from_photo_drop([str(media)], track_id, 0.0)
    assert len(window.project.media_assets) == assets and len(window.project.tracks[0].clips) == clips
    assert "lecture seule" in window.statusBar().currentMessage()


def test_export_entries_are_refused_without_a_dialog(make_window, monkeypatch):
    window = make_window(expired())
    asked = []
    monkeypatch.setattr(type(window), "_ask_export_path", lambda self, spec: asked.append(spec) or "")
    assert window._enqueue_current_export(start=True) is None
    window.export_format_versions()
    window.export_subtitles_via_dialog()
    assert asked == [] and window.render_queue.jobs == ()
    assert "bloqué" in window.statusBar().currentMessage()
    with pytest.raises(entitlements.ExportBlockedError):
        window.export_subtitles_to_path("/nonexistent/x.srt")


def test_the_render_queue_panel_greys_the_start_buttons(make_window):
    window = make_window(expired())
    panel = window.export_panel.queue_panel
    panel._refresh_state()
    assert not panel.start_all_button.isEnabled() and not panel.start_button.isEnabled()


def test_leaving_read_only_restores_the_widgets(make_window):
    window = make_window(expired())
    assert not window.properties_panel.isEnabled()
    service, _store, _clock = make_service(__import__("pathlib").Path(__import__("tempfile").mkdtemp()))
    service.publish(lifetime())                           # l'utilisateur achète
    entitlements.install_service(service)
    greyed = list(window._entitlement_disabled)
    assert greyed and not any(action.isEnabled() for action in greyed)
    window.refresh_entitlements()
    assert window.properties_panel.isEnabled() and window.mixer_panel.isEnabled()
    assert all(action.isEnabled() for action in greyed), "tout ce que la lecture seule a grisé est rendu"
    assert window.export_panel.launch_button.isEnabled() and window.export_panel.launch_button.toolTip() == ""
    assert window._entitlement_disabled == []


def test_every_command_is_classified_and_the_whitelist_has_no_ghost():
    ids = {command.id for command in COMMANDS}
    assert READ_ONLY_COMMANDS <= ids, f"commande inconnue dans la liste blanche : {sorted(READ_ONLY_COMMANDS - ids)}"
    # Une commande qui change le projet n'est jamais dans la liste blanche (revue : cette liste est courte et relue).
    editing = {"undo", "redo", "delete_clip", "duplicate_clip", "ripple_delete", "cut_at_playhead", "toggle_clip_enabled",
               "keyframe_add", "keyframe_remove", "keyframe_paste", "marker_add", "audio_record_toggle", "time_add_speed_point",
               "layer_add_text", "layer_paste_attributes", "social_fill_frame", "multicam_angle_1", "track_toggle_mute"}
    assert not (editing & READ_ONLY_COMMANDS)


def test_the_menu_whitelist_matches_real_menu_keys(make_window):
    window = make_window(expired())
    keys = {key for _action, key in window._translated_actions}
    assert READ_ONLY_MENU_KEYS <= keys, f"clé sans entrée de menu : {sorted(READ_ONLY_MENU_KEYS - keys)}"


def test_every_menu_entry_not_whitelisted_is_disabled_in_read_only(make_window):
    window = make_window(expired())
    by_cmd = {id(a): c for c, a in window._command_actions_by_id().items()}
    keys = {id(a): k for a, k in window._translated_actions}
    for action in _actions(window):
        allowed = (entitlements.is_read_only_command(by_cmd[id(action)]) if id(action) in by_cmd
                   else keys.get(id(action)) in READ_ONLY_MENU_KEYS)
        if not allowed:
            assert not action.isEnabled(), action.text()


# -- éditions différées et dialogues qui modifient le projet vivant (revue Codex, PR 295) ---------------------------------------


def _set_service(record):
    import tempfile
    from pathlib import Path

    service, _store, _clock = make_service(Path(tempfile.mkdtemp()))
    service.publish(record)
    entitlements.install_service(service)


def _saved_window(make_window, tmp_path):
    from core.project_io import load_project, project_payload, save_project

    window = make_window(lifetime())
    path = str(tmp_path / "p.kut")
    save_project(window.project, path)
    window.current_project_path = path
    window.history.reset(window.project)
    window._mark_clean()
    return window, path, lambda: project_payload(load_project(path))


@pytest.mark.parametrize("save", ["save_project_file", "autosave"])
def test_a_deferred_color_edit_never_reaches_the_file_in_read_only(make_window, tmp_path, monkeypatch, save):
    window, path, on_disk = _saved_window(make_window, tmp_path)
    clean = on_disk()
    window.on_clip_selected("intro")
    _set_service(expired())                               # l'essai finit ; la page Couleur est encore ouverte
    window.on_color_grade_field_changed("intro", "saturation", 1.7)    # l'enregistrement est différé de 400 ms
    assert window._color_session_active, "l'édition est bien différée (sinon ce test ne prouve rien)"
    assert window.history.is_dirty is False
    if save == "save_project_file":
        window.save_project_file()                        # Enregistrer tout de suite, avant les 400 ms
    else:
        captured = []
        monkeypatch.setattr(window._autosave, "submit", lambda project, target: captured.append(
            __import__("core.project_io", fromlist=["x"]).project_payload(project)))
        window.project_dirty = True
        window._write_autosave()
        assert all(item == clean for item in captured), "la sauvegarde auto n'écrit jamais l'édition interdite"
    assert on_disk() == clean, "l'ajustement interdit n'est pas dans le fichier"
    assert window.project_dirty is False
    assert window._color_session_active is False


def test_a_gesture_without_timer_does_not_reach_the_file_either(make_window, tmp_path):
    window, path, on_disk = _saved_window(make_window, tmp_path)
    clean = on_disk()
    _set_service(expired())
    window.project.name = "geste en cours"                # modifié en place, rien d'enregistré (glisser sans minuteur)
    window.save_project_file()
    assert on_disk() == clean and window.project.name != "geste en cours"


def test_saving_is_unchanged_when_editing_is_allowed(make_window, tmp_path):
    window, path, on_disk = _saved_window(make_window, tmp_path)
    clean = on_disk()
    window.on_clip_selected("intro")
    window.on_color_grade_field_changed("intro", "saturation", 1.7)
    window.save_project_file()
    assert on_disk() != clean and window.history.is_dirty is False


def _tag_dialog(monkeypatch, *, switch_to_expired: bool, result):
    from ui.library_organization_widgets import TagManagerDialog

    calls = []

    def fake_exec(self):
        calls.append("opened")
        self._organization.create_tag("interdit")
        if switch_to_expired:
            _set_service(expired())
        return result

    monkeypatch.setattr(TagManagerDialog, "exec", fake_exec)
    return calls


def test_the_tag_manager_does_not_open_in_read_only(make_window, monkeypatch):
    from core.project_io import project_payload
    from PySide6.QtWidgets import QDialog

    window = make_window(expired())
    calls = _tag_dialog(monkeypatch, switch_to_expired=False, result=QDialog.Rejected)
    before = project_payload(window.project)
    window._on_tag_manager_requested()
    assert calls == [] and project_payload(window.project) == before
    assert "lecture seule" in window.statusBar().currentMessage()


@pytest.mark.parametrize("result_name", ["Rejected", "Accepted"])
def test_tag_changes_do_not_survive_a_close_when_rights_ended_while_open(make_window, monkeypatch, result_name):
    from core.project_io import project_payload
    from PySide6.QtWidgets import QDialog

    window = make_window(lifetime())
    before = project_payload(window.project)
    calls = _tag_dialog(monkeypatch, switch_to_expired=True, result=getattr(QDialog, result_name))
    window._on_tag_manager_requested()
    assert calls == ["opened"] and project_payload(window.project) == before, "Fermer ne laisse pas les changements"


def test_the_tag_manager_is_unchanged_outside_read_only(make_window, monkeypatch):
    from core.project_io import project_payload
    from PySide6.QtWidgets import QDialog

    window = make_window(lifetime())
    before = project_payload(window.project)
    _tag_dialog(monkeypatch, switch_to_expired=False, result=QDialog.Accepted)
    window._on_tag_manager_requested()
    assert project_payload(window.project) != before and window.history.can_undo
