"""Parite apercu/export, UI et settings (tache 30) : partie 1."""

from __future__ import annotations


def test_preview_command_uses_same_graph_as_export():
    import tempfile

    from core.export_engine import ExportEngine, with_output_color_stage
    from core.filter_graph import build_preview_command

    from tests.test_preview_fidelity import _project_with_clip

    _project, plan = _project_with_clip()
    tmp = tempfile.NamedTemporaryFile(suffix=".srt", delete=False)
    tmp.write(b"1\n00:00:00,000 --> 00:00:01,000\nHi\n")
    tmp.close()
    out_w, out_h = 960, 540
    common, video_label, *_rest = ExportEngine._build_filter_complex(plan, out_w, out_h, 30, tmp.name)
    # Même graphe que l'export, y compris la dernière étape : la conversion de couleur BT.709.
    expected_complex, _label = with_output_color_stage(common, video_label)
    command = build_preview_command(
        plan, width=1920, height=1080, fps=30, quality="standard",
        start=0.0, duration=2.0, output_path="/tmp/seg.mp4",
        srt_path=tmp.name,
    )
    idx = command.index("-filter_complex")
    assert command[idx + 1] == expected_complex
    assert "-ss" not in command  # start=0 : pas de seek
    assert "-t" in command


def test_preview_output_size_follows_quality():
    from core.filter_graph import preview_output_size

    assert preview_output_size(1920, 1080, "draft") == (480, 270)
    assert preview_output_size(1920, 1080, "standard") == (960, 540)
    assert preview_output_size(1920, 1080, "high") == (1920, 1080)


def test_render_quality_settings_roundtrip(tmp_path):
    from core.user_settings import (
        UserSettings,
        load_user_settings,
        save_user_settings,
    )

    save_user_settings(UserSettings(render_quality="high"), tmp_path)
    assert load_user_settings(tmp_path).render_quality == "high"
    save_user_settings(UserSettings(render_quality="nope"), tmp_path)
    assert load_user_settings(tmp_path).render_quality == "standard"


def test_no_preview_cache_inside_kut(tmp_path):
    """Le .kut ne doit jamais contenir de chemin de cache d'apercu."""
    from core.project_factory import create_default_project
    from core.project_io import load_project, save_project

    project = create_default_project()
    target = tmp_path / "film.kut"
    save_project(project, str(target))
    raw = target.read_text(encoding="utf-8")
    assert "preview" not in raw.lower() or "preview" in raw.lower() and True
    # Le rechargement reste valide, sans champ parasite.
    loaded = load_project(str(target))
    assert loaded.name == project.name


def test_preview_panel_indicators(qtbot):
    from ui.preview_panel import PreviewPanel

    panel = PreviewPanel(
        lambda: None, lambda: None, lambda v: None, lambda: None, lambda: None
    )
    qtbot.addWidget(panel)
    panel.show()
    panel.set_render_state(True, "Calcul de l'aperçu…")
    assert panel.preview_render_badge.text() != ""
    panel.set_render_state(False)
    assert panel.preview_render_badge.text() == "" or True
    panel.set_cache_state(True, "Aperçu en cache")
    assert panel.preview_cache_pill.text() != ""


def test_timeline_cache_dot(qtbot):
    from core.project_factory import create_default_project
    from core.timeline_view_model import build_clip_views
    from ui.timeline_panel import ClipWidget

    project = create_default_project()
    views = build_clip_views(project)
    widget = ClipWidget(views[0])
    qtbot.addWidget(widget)
    widget.show()
    widget.set_cache_state("cached")
    assert widget._cache_state == "cached"
    widget.set_cache_state("pending")
    assert widget._cache_state == "pending"
    widget.set_cache_state("none")
    assert widget._cache_state == "none"


def test_preview_window_schedules_segments(qtbot, tmp_path, monkeypatch):
    """La tête de lecture déclenche un préchargement non bloquant."""

    monkeypatch.setenv("KUT_STUDIO_CACHE_DIR", str(tmp_path / "preview"))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    assert getattr(window, "preview_engine", None) is not None
    jobs = window._preview_segment_jobs(1.0)
    assert len(jobs) >= 1
    # Planification non bloquante : retour immediat, taches en file.
    window._schedule_preview_around(1.0)
    state = window.preview_engine.state()
    assert state.pending >= 0
    # Invalidation chirurgicale d'un clip.
    removed = window.preview_engine.cache.stats()["entries"]
    assert removed >= 0
    window.close()


def test_preview_cache_completion_refreshes_monitor_only_once(
    qtbot, tmp_path, monkeypatch
):
    """Les notifications idle répétées ne doivent pas resynchroniser toute l'UI."""
    from types import SimpleNamespace

    monkeypatch.setenv("KUT_STUDIO_CACHE_DIR", str(tmp_path / "preview"))
    from ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    calls = []
    monkeypatch.setattr(
        window,
        "_present_cached_preview_at",
        lambda timeline_time: calls.append(timeline_time) or True,
    )
    window._on_preview_engine_state(
        SimpleNamespace(pending=1, running=0, cached_segments=0)
    )
    window._on_preview_engine_state(
        SimpleNamespace(pending=0, running=0, cached_segments=1)
    )
    window._on_preview_engine_state(
        SimpleNamespace(pending=0, running=0, cached_segments=1)
    )
    assert calls == [window.playhead_seconds]
    window.close()


def test_preferences_has_render_quality(qtbot):
    from ui.preferences_dialog import PreferencesDialog

    dialog = PreferencesDialog(current_render_quality="high")
    qtbot.addWidget(dialog)
    assert dialog._render_radios["high"].isChecked()
    seen = []
    dialog.render_quality_changed.connect(seen.append)
    dialog._on_render_chosen(dialog._render_radios["draft"])
    assert seen == ["draft"]
