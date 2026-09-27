"""Tests du modèle audio, du mixeur métier et du panneau Mixeur.

Répartition :

- les bornes de valeurs et les setters de fondu ;
- le module métier : audibilité, gains, panoramique, fondus ;
- la persistance ``.kut`` version 6 et la migration des anciennes
  versions ;
- le panneau Qt : construction, verrouillage, signaux, i18n.

Le panneau n'affiche volontairement **aucun vumètre** : tant qu'aucun
niveau n'est réellement mesuré, en afficher un serait une indication
fausse. Un test verrouille ce point.
"""

import json
import os
from pathlib import Path

import pytest

from core.audio_mixer import (
    MixEntry,
    MixSpec,
    audio_tracks,
    audible_track_ids,
    db_to_linear,
    fade_envelope,
    has_solo,
    is_audible,
    linear_to_db,
    mix_at,
    mix_range,
    pan_needs_filter,
    pan_to_gains,
)
from core.project_io import CURRENT_VERSION, load_project, save_project
from core.project_model import (
    MAX_GAIN_DB,
    MIN_GAIN_DB,
    Clip,
    MediaAsset,
    Project,
    Track,
    clamp_fade,
    clamp_gain_db,
    clamp_pan,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_audio_project() -> Project:
    """Projet minimal : une piste audio, un clip de 4 s."""
    asset = MediaAsset(
        id="m1",
        path="/tmp/a.wav",
        name="a",
        duration=10.0,
        width=0,
        height=0,
        fps=0.0,
        media_type="audio",
        has_audio=True,
    )
    track = Track(id="A1", name="A1", type="audio")
    track.clips.append(
        Clip(
            id="c1",
            asset_id="m1",
            track_id="A1",
            timeline_start=0.0,
            source_in=0.0,
            source_out=4.0,
        )
    )
    return Project(name="mix", media_assets=[asset], tracks=[track])


# ---------------------------------------------------------------------------
# Bornes de valeurs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [0.0, -6.0, -60.0, 12.0])
def test_gain_inside_bounds_is_untouched(value):
    assert clamp_gain_db(value) == value


def test_gain_is_clamped():
    assert clamp_gain_db(999.0) == MAX_GAIN_DB
    assert clamp_gain_db(-9999.0) == MIN_GAIN_DB


def test_pan_is_clamped():
    assert clamp_pan(5.0) == 1.0
    assert clamp_pan(-5.0) == -1.0


def test_fade_cannot_be_negative():
    assert clamp_fade(-3.0) == 0.0


@pytest.mark.parametrize("bad", [None, "abc", object(), float("nan")])
def test_non_numeric_audio_values_fall_back_to_neutral(bad):
    """Un projet chargé ne doit jamais casser sur un champ douteux."""
    assert clamp_gain_db(bad) == 0.0
    assert clamp_pan(bad) == 0.0
    assert clamp_fade(bad) == 0.0


def test_clip_normalises_out_of_range_audio():
    clip = Clip(
        id="c", asset_id="m", track_id="A1", timeline_start=0.0,
        source_in=0.0, source_out=4.0,
        gain_db=99.0, pan=-9.0, fade_in=-2.0, fade_out=99.0,
    )
    assert clip.gain_db == MAX_GAIN_DB
    assert clip.pan == -1.0
    assert clip.fade_in == 0.0
    assert clip.fade_out == 4.0


def test_fades_never_exceed_clip_duration():
    clip = Clip(
        id="c", asset_id="m", track_id="A1", timeline_start=0.0,
        source_in=0.0, source_out=2.0, fade_in=5.0, fade_out=5.0,
    )
    assert clip.fade_in + clip.fade_out <= clip.duration + 1e-9


def test_setting_one_fade_never_moves_the_other():
    clip = Clip(
        id="c", asset_id="m", track_id="A1", timeline_start=0.0,
        source_in=0.0, source_out=4.0,
    )
    clip.set_fade_in(3.0)
    clip.set_fade_out(3.0)
    assert clip.fade_in == pytest.approx(3.0)
    assert clip.fade_out == pytest.approx(1.0)
    assert clip.fade_in + clip.fade_out == pytest.approx(4.0)


def test_fade_setters_return_applied_value():
    clip = Clip(
        id="c", asset_id="m", track_id="A1", timeline_start=0.0,
        source_in=0.0, source_out=2.0,
    )
    assert clip.set_fade_in(99.0) == pytest.approx(2.0)
    clip.reset_fades()
    assert clip.fade_in == 0.0 and clip.fade_out == 0.0


def test_track_audio_fields_are_clamped():
    track = Track(id="A1", name="A1", type="audio", volume_db=-900, pan=42)
    assert track.volume_db == MIN_GAIN_DB
    assert track.pan == 1.0
    track.reset_audio()
    assert track.volume_db == 0.0 and track.pan == 0.0


def test_video_and_subtitle_clips_keep_audio_defaults():
    clip = Clip(
        id="v", asset_id="m", track_id="V1", timeline_start=0.0,
        source_in=0.0, source_out=2.0,
    )
    assert clip.gain_db == 0.0
    assert clip.fade_in == 0.0
    assert clip.is_audio_affected is False


# ---------------------------------------------------------------------------
# Conversions
# ---------------------------------------------------------------------------


def test_db_conversions_round_trip():
    assert db_to_linear(0.0) == pytest.approx(1.0)
    assert db_to_linear(-6.0) == pytest.approx(0.5012, abs=1e-3)
    assert db_to_linear(MIN_GAIN_DB) == 0.0
    assert linear_to_db(0.0) == MIN_GAIN_DB
    assert linear_to_db(1.0) == pytest.approx(0.0, abs=1e-6)


def test_pan_is_constant_power():
    left, right = pan_to_gains(0.0)
    assert left == pytest.approx(right, abs=1e-9)
    assert left == pytest.approx(0.7071, abs=1e-3)
    assert pan_to_gains(-1.0) == pytest.approx((1.0, 0.0), abs=1e-6)
    assert pan_to_gains(1.0) == pytest.approx((0.0, 1.0), abs=1e-6)


def test_pan_never_amplifies():
    for value in (-1.0, -0.5, 0.0, 0.5, 1.0):
        for gain in pan_to_gains(value):
            assert gain <= 1.0 + 1e-9


def test_centre_pan_needs_no_filter():
    assert pan_needs_filter(0.0) is False
    assert pan_needs_filter(0.4) is True


# ---------------------------------------------------------------------------
# Audibilité (mute / solo)
# ---------------------------------------------------------------------------


def test_muted_track_is_not_audible():
    project = make_audio_project()
    project.tracks[0].muted = True
    assert has_solo(project) is False
    assert is_audible(project.tracks[0], solo_active=False) is False
    assert audible_track_ids(project) == ()


def test_unmuted_track_is_audible():
    project = make_audio_project()
    assert audible_track_ids(project) == ("A1",)


def test_solo_excludes_other_tracks():
    project = make_audio_project()
    project.tracks[0].solo = True
    project.tracks.append(Track(id="A2", name="A2", type="audio"))
    assert has_solo(project) is True
    assert audible_track_ids(project) == ("A1",)


def test_solo_wins_over_mute():
    """Une piste solo reste entendue même si elle est mutée."""
    project = make_audio_project()
    track = project.tracks[0]
    track.solo = True
    track.muted = True
    assert is_audible(track, solo_active=True) is True
    assert audible_track_ids(project) == ("A1",)


def test_only_audio_tracks_are_listed():
    project = make_audio_project()
    project.tracks.append(Track(id="V1", name="V1", type="video"))
    project.tracks.append(Track(id="S1", name="S1", type="subtitle"))
    assert [t.id for t in audio_tracks(project)] == ["A1"]


# ---------------------------------------------------------------------------
# Fondus
# ---------------------------------------------------------------------------


def test_fade_envelope_shape():
    assert fade_envelope(0.0, 4.0, 1.0, 0.0) == 0.0
    assert fade_envelope(0.5, 4.0, 1.0, 0.0) == pytest.approx(0.5)
    assert fade_envelope(1.0, 4.0, 1.0, 0.0) == pytest.approx(1.0)
    assert fade_envelope(2.0, 4.0, 0.0, 0.0) == pytest.approx(1.0)
    assert fade_envelope(3.5, 4.0, 0.0, 1.0) == pytest.approx(0.5)
    assert fade_envelope(4.0, 4.0, 0.0, 1.0) == 0.0


def test_fade_envelope_of_zero_length_is_zero():
    assert fade_envelope(0.0, 0.0, 0.0, 0.0) == 0.0


# ---------------------------------------------------------------------------
# MixSpec
# ---------------------------------------------------------------------------


def test_mix_spec_is_deterministic():
    project = make_audio_project()
    assert mix_at(project, 1.0) == mix_at(project, 1.0)


def test_mix_entry_carries_resolved_gains():
    project = make_audio_project()
    track = project.tracks[0]
    track.volume_db = -6.0
    clip = track.clips[0]
    clip.gain_db = -3.0
    spec = mix_at(project, 1.0)
    entry = spec.entries[0]
    assert entry.gain_db == pytest.approx(-9.0)
    assert entry.gain_linear == pytest.approx(db_to_linear(-9.0))


def test_track_pan_and_clip_pan_add_up():
    project = make_audio_project()
    project.tracks[0].pan = 0.3
    project.tracks[0].clips[0].pan = 0.4
    entry = mix_at(project, 1.0).entries[0]
    assert entry.gain_right > entry.gain_left


def test_muted_track_yields_silent_spec():
    project = make_audio_project()
    project.tracks[0].muted = True
    assert mix_at(project, 1.0).is_silent is True


def test_master_mute_yields_silent_spec():
    project = make_audio_project()
    assert mix_at(project, 1.0).is_silent is False
    assert mix_at(project, 1.0, master_muted=True).is_silent is True


def test_clip_outside_range_is_absent():
    project = make_audio_project()
    assert mix_at(project, 10.0).entries == ()
    assert mix_at(project, 10.0).is_silent is True


def test_disabled_clip_is_ignored():
    project = make_audio_project()
    project.tracks[0].clips[0].enabled = False
    assert mix_at(project, 1.0).entries == ()


def test_clip_with_missing_asset_is_skipped_not_fatal():
    project = make_audio_project()
    project.tracks[0].clips[0].asset_id = "absent"
    spec = mix_at(project, 1.0)
    assert spec.entries == ()


def test_mix_range_samples_deterministically():
    project = make_audio_project()
    samples = mix_range(project, 0.0, 2.0, step_seconds=1.0)
    assert len(samples) == 2
    assert all(isinstance(s, MixSpec) for s in samples)


def test_mix_range_of_empty_span_still_returns_one_sample():
    project = make_audio_project()
    assert len(mix_range(project, 1.0, 1.0)) == 1


def test_entries_are_sorted_stably():
    project = make_audio_project()
    track = project.tracks[0]
    track.clips.append(
        Clip(id="c0", asset_id="m1", track_id="A1",
             timeline_start=0.0, source_in=0.0, source_out=4.0)
    )
    entries = mix_at(project, 1.0).entries
    assert [e.clip_id for e in entries] == sorted(e.clip_id for e in entries)


def test_total_gain_reports_silence_when_muted():
    project = make_audio_project()
    project.tracks[0].muted = True
    assert mix_at(project, 1.0).total_gain_db() == MIN_GAIN_DB


# ---------------------------------------------------------------------------
# Persistance ``.kut`` v6
# ---------------------------------------------------------------------------


def test_audio_fields_round_trip(tmp_path):
    project = make_audio_project()
    track = project.tracks[0]
    track.volume_db = -4.5
    track.pan = 0.25
    clip = track.clips[0]
    clip.gain_db = -3.0
    clip.pan = -0.5
    clip.fade_in = 0.5
    clip.fade_out = 1.0
    path = str(tmp_path / "p.kut")
    save_project(project, path)
    back = load_project(path)
    bt, bc = back.tracks[0], back.tracks[0].clips[0]
    assert bt.volume_db == pytest.approx(-4.5)
    assert bt.pan == pytest.approx(0.25)
    assert bc.gain_db == pytest.approx(-3.0)
    assert bc.pan == pytest.approx(-0.5)
    assert bc.fade_in == pytest.approx(0.5)
    assert bc.fade_out == pytest.approx(1.0)


def test_saved_file_declares_current_version(tmp_path):
    path = str(tmp_path / "p.kut")
    save_project(make_audio_project(), path)
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    assert payload["version"] == CURRENT_VERSION


def _legacy_payload(version: int) -> dict:
    return {
        "format": "kut-studio-project",
        "version": version,
        "project": {
            "name": "ancien",
            "width": 1280,
            "height": 720,
            "fps": 25.0,
            "media_assets": [
                {
                    "id": "m1", "path": "/tmp/v.mp4", "name": "v",
                    "duration": 5, "width": 1280, "height": 720,
                    "fps": 25.0, "media_type": "video", "has_audio": True,
                }
            ],
            "markers": [],
            "tracks": [
                {
                    "id": "A1", "name": "A1", "type": "audio",
                    "clips": [
                        {
                            "id": "c1", "asset_id": "m1", "track_id": "A1",
                            "timeline_start": 0, "source_in": 0,
                            "source_out": 2.0, "enabled": True,
                            "label": "x", "text": "",
                        }
                    ],
                }
            ],
        },
    }


@pytest.mark.parametrize("version", [1, 2, 3, 4, 5, 6])
def test_older_versions_load_with_neutral_audio(tmp_path, version):
    path = tmp_path / f"v{version}.kut"
    path.write_text(json.dumps(_legacy_payload(version)), encoding="utf-8")
    project = load_project(str(path))
    track = project.tracks[0]
    clip = track.clips[0]
    assert track.volume_db == 0.0
    assert track.pan == 0.0
    assert (clip.gain_db, clip.pan, clip.fade_in, clip.fade_out) == (
        0.0, 0.0, 0.0, 0.0
    )


def test_invalid_audio_values_in_file_are_clamped(tmp_path):
    payload = _legacy_payload(6)
    track = payload["project"]["tracks"][0]
    track["volume_db"] = "loud"
    track["pan"] = 99.0
    track["clips"][0]["gain_db"] = 1e9
    track["clips"][0]["fade_in"] = -5.0
    path = tmp_path / "bad.kut"
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_project(str(path))
    assert loaded.tracks[0].volume_db == 0.0
    assert loaded.tracks[0].pan == 1.0
    assert loaded.tracks[0].clips[0].gain_db == MAX_GAIN_DB
    assert loaded.tracks[0].clips[0].fade_in == 0.0


def test_unknown_clip_field_is_ignored(tmp_path):
    payload = _legacy_payload(6)
    payload["project"]["tracks"][0]["clips"][0]["champ_inconnu"] = 42
    path = tmp_path / "x.kut"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_project(str(path)) is not None


# ---------------------------------------------------------------------------
# Plan de rendu et export
# ---------------------------------------------------------------------------


def test_render_plan_carries_audio_settings():
    from core.render_plan import build_render_plan

    project = make_audio_project()
    project.tracks[0].volume_db = -6.0
    project.tracks[0].pan = 0.2
    clip = project.tracks[0].clips[0]
    clip.gain_db = -3.0
    clip.pan = 0.4
    clip.fade_in = 0.5
    clip.fade_out = 1.0
    plan = build_render_plan(project, master_gain_db=-2.0)
    layer = plan.audio_layers[0]
    assert layer.total_gain_db == pytest.approx(-9.0)
    assert layer.total_pan == pytest.approx(0.6)
    assert layer.fade_in == pytest.approx(0.5)
    assert plan.master_gain_db == -2.0
    assert plan.is_audio_silent is False


def test_render_plan_reports_silence_without_audio():
    from core.render_plan import build_render_plan

    project = make_audio_project()
    project.tracks[0].muted = True
    assert build_render_plan(project).is_audio_silent is True


def test_export_filter_applies_gain_fades_and_pan():
    from core.export_engine import _build_audio_filter
    from core.render_plan import build_render_plan

    project = make_audio_project()
    project.tracks[0].volume_db = -6.0
    clip = project.tracks[0].clips[0]
    clip.gain_db = -3.0
    clip.pan = 0.4
    clip.fade_in = 0.5
    clip.fade_out = 1.0
    plan = build_render_plan(project)
    chain = _build_audio_filter(0, plan.audio_layers[0], 1, plan.duration)
    assert "volume=-9.000dB" in chain
    assert "afade=t=in" in chain
    assert "afade=t=out" in chain
    assert "stereotools" in chain
    # Le séparateur de graphe casserait la commande.
    assert "|" not in chain


def test_export_filter_omits_useless_stages():
    from core.export_engine import _build_audio_filter
    from core.render_plan import build_render_plan

    project = make_audio_project()
    project.tracks[0].volume_db = 0.0
    plan = build_render_plan(project)
    chain = _build_audio_filter(0, plan.audio_layers[0], 1, plan.duration)
    assert "afade" not in chain
    assert "stereotools" not in chain
    assert "volume=" not in chain


def test_master_filter():
    from core.export_engine import _build_master_filter
    from core.render_plan import build_render_plan

    project = make_audio_project()
    assert _build_master_filter(build_render_plan(project)) == ""
    assert "volume=-2.000dB" in _build_master_filter(
        build_render_plan(project, master_gain_db=-2.0)
    )
    assert _build_master_filter(
        build_render_plan(project, master_muted=True)
    ) == "volume=0dB"


def test_export_stays_valid_without_audio():
    from core.render_plan import build_render_plan

    project = make_audio_project()
    project.tracks[0].muted = True
    plan = build_render_plan(project, master_muted=True)
    assert plan.audio_layers == ()
    assert plan.is_audio_silent is True


# ---------------------------------------------------------------------------
# Panneau Mixeur
# ---------------------------------------------------------------------------


@pytest.fixture
def mixer(qtbot):
    from PySide6.QtWidgets import QApplication

    from ui.mixer_panel import MixerPanel
    from ui.theme import ThemeManager

    ThemeManager("dark").apply_to(QApplication.instance())
    panel = MixerPanel()
    panel.resize(600, 360)
    qtbot.addWidget(panel)
    panel.show()
    return panel


def test_mixer_creates_one_strip_per_audio_track(mixer):
    project = make_audio_project()
    project.tracks.append(Track(id="A2", name="A2", type="audio"))
    mixer.set_project(project)
    assert mixer.strip_count() == 2


def test_mixer_ignores_non_audio_tracks(mixer):
    project = make_audio_project()
    project.tracks.append(Track(id="V1", name="V1", type="video"))
    mixer.set_project(project)
    assert mixer.strip_count() == 1


def test_mixer_shows_empty_state_without_tracks(mixer):
    mixer.set_project(Project(name="vide"))
    assert mixer.strip_count() == 0
    assert mixer.empty_label.text()


def test_mixer_disables_controls_on_locked_track(mixer):
    project = make_audio_project()
    project.tracks[0].locked = True
    mixer.set_project(project)
    strip = mixer.strip_for("A1")
    assert strip.is_locked() is True
    assert strip.volume_slider.isEnabled() is False
    assert strip.mute_button.isEnabled() is False


def test_mixer_displays_track_values(mixer):
    project = make_audio_project()
    project.tracks[0].volume_db = -4.0
    project.tracks[0].pan = 0.5
    mixer.set_project(project)
    strip = mixer.strip_for("A1")
    assert strip.volume_slider.value() == -4
    assert strip.pan_slider.value() == 50


def test_mixer_emits_volume_change(mixer):
    from core.project_model import MAX_GAIN_DB

    mixer.set_project(make_audio_project())
    received = []
    mixer.volume_changed.connect(lambda t, v: received.append((t, v)))
    mixer.strip_for("A1").volume_slider.setValue(MAX_GAIN_DB)
    assert received and received[0][0] == "A1"


def test_mixer_emits_pan_change(mixer):
    mixer.set_project(make_audio_project())
    received = []
    mixer.pan_changed.connect(lambda t, v: received.append((t, v)))
    mixer.strip_for("A1").pan_slider.setValue(50)
    assert received and received[0][1] == pytest.approx(0.5)


def test_mixer_emits_state_toggles(mixer):
    mixer.set_project(make_audio_project())
    events = []
    mixer.mute_toggled.connect(lambda t, v: events.append(("mute", t, v)))
    mixer.solo_toggled.connect(lambda t, v: events.append(("solo", t, v)))
    mixer.arm_toggled.connect(lambda t, v: events.append(("arm", t, v)))
    strip = mixer.strip_for("A1")
    strip.mute_button.setChecked(True)
    strip.solo_button.setChecked(True)
    strip.arm_button.setChecked(True)
    assert [e[0] for e in events] == ["mute", "solo", "arm"]


def test_mixer_emits_reset(mixer):
    mixer.set_project(make_audio_project())
    received = []
    mixer.reset_requested.connect(lambda t: received.append(t))
    mixer.strip_for("A1").reset_button.click()
    assert received == ["A1"]


def test_mixer_master_controls(mixer):
    mixer.set_project(make_audio_project())
    received = []
    mixer.master_volume_changed.connect(received.append)
    mixer.master_bar().volume_slider.setValue(-5)
    assert received == [-5.0]
    mixer.set_master(-9.0, True)
    assert mixer.master_bar().mute_button.isChecked() is True


def test_mixer_shows_solo_hint(mixer):
    project = make_audio_project()
    project.tracks[0].solo = True
    mixer.set_project(project)
    assert mixer.solo_hint.text() != ""


def test_mixer_has_no_fake_vumeter(mixer):
    """Aucun vumètre tant qu'aucun niveau réel n'est mesuré."""
    from PySide6.QtWidgets import QProgressBar

    mixer.set_project(make_audio_project())
    assert mixer.level_provider is None
    assert mixer.findChildren(QProgressBar) == []


def test_mixer_is_translated_in_three_languages(qtbot):
    from ui.i18n import available_languages, set_language
    from ui.mixer_panel import MixerPanel

    for language in available_languages():
        set_language(language)
        panel = MixerPanel()
        qtbot.addWidget(panel)
        assert panel.title_label.text()
        assert panel.empty_label.text()
        assert panel.master_bar().name_label.text()
        assert "[mixer." not in panel.title_label.text()
    set_language("fr")


def test_mixer_tooltips_are_translated(qtbot):
    from ui.i18n import set_language
    from ui.mixer_panel import MixerPanel

    set_language("en")
    panel = MixerPanel()
    qtbot.addWidget(panel)
    assert "Mute" in panel.master_bar().mute_button.toolTip()
    set_language("fr")


def test_mixer_refresh_track(qtbot):
    from ui.mixer_panel import MixerPanel

    panel = MixerPanel()
    qtbot.addWidget(panel)
    project = make_audio_project()
    panel.set_project(project)
    project.tracks[0].volume_db = -7.0
    panel.refresh_track(project.tracks[0])
    assert panel.strip_for("A1").volume_slider.value() == -7


def test_mixer_survives_project_change(qtbot):
    from ui.mixer_panel import MixerPanel

    panel = MixerPanel()
    qtbot.addWidget(panel)
    panel.set_project(make_audio_project())
    panel.set_project(Project(name="vide"))
    assert panel.strip_count() == 0
