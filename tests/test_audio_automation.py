"""Tests pour le module :mod:`core.audio_automation` (tâche 28).

Couvre :

- le modèle de données : :class:`TrackRole`, :class:`AutomationPoint`,
  :class:`TrackAutomation`, :class:`DuckingConfig`,
  :class:`DuckingSidechain` ;
- les fonctions d'évaluation de la courbe d'automation
  (:meth:`TrackAutomation.gain_at`) ;
- le service :class:`AudioAutomationService` (CRUD sur les rôles, les
  points d'automation et les associations de ducking) ;
- la persistance ``.kut`` (sérialisation + désérialisation + rétro‑
  compatibilité v10‑ et v11.0) ;
- la conversion ``AudioLayer`` (transport de l'automation et du ducking
  dans le plan de rendu) ;
- les filtres FFmpeg ``volume`` (enveloppe de gain) et
  ``sidechaincompress`` (ducking) ;
- Undo/Redo via :class:`ProjectHistory` pour toutes les opérations
  d'orchestration.
"""

from __future__ import annotations

import pytest

from core.audio_automation import (
    AudioAutomationError,
    AudioAutomationRangeError,
    AudioAutomationService,
    AutomationPoint,
    DEFAULT_GAIN_MIN_DB,
    DuckingConfig,
    DuckingSidechain,
    MAX_DUCKING_ATTACK_S,
    MAX_DUCKING_REDUCTION_DB,
    MAX_DUCKING_RELEASE_S,
    MAX_DUCKING_THRESHOLD_DB,
    MAX_POINT_FADE_SECONDS,
    MIN_DUCKING_ATTACK_S,
    MIN_DUCKING_REDUCTION_DB,
    MIN_DUCKING_RELEASE_S,
    MIN_DUCKING_THRESHOLD_DB,
    MIN_POINT_FADE_SECONDS,
    TRACK_ROLE_LABELS,
    TrackAutomation,
    TrackRole,
)
from core.edit_history import ProjectHistory
from core.export_engine import (
    _build_ducking_chain,
    _build_track_volume_envelope,
    _format_db,
    _format_seconds,
)
from core.project_io import load_project, save_project
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import _build_audio_layer, build_render_plan


# Petit alias de silence pour ne pas casser l'import si la constante
# ``track_role`` n'existe pas : on garde simplement ``TrackRole``.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_with_music_and_voice() -> Project:
    asset = MediaAsset(
        id="asset-audio",
        path="/tmp/a.mp3",
        name="A",
        duration=10.0,
        width=0,
        height=0,
        fps=0.0,
        media_type="audio",
        has_audio=True,
    )
    music = Track(id="M1", name="Musique", type="audio")
    voice = Track(id="V1", name="Voix", type="audio")
    return Project(name="mix", media_assets=[asset], tracks=[music, voice])


def _project_with_audio_clips() -> Project:
    """Projet avec deux pistes audio et un clip sur chacune."""
    project = _project_with_music_and_voice()
    project.tracks[0].clips.append(
        Clip(
            id="c-music",
            asset_id="asset-audio",
            track_id="M1",
            timeline_start=0.0,
            source_in=0.0,
            source_out=5.0,
        )
    )
    project.tracks[1].clips.append(
        Clip(
            id="c-voice",
            asset_id="asset-audio",
            track_id="V1",
            timeline_start=0.0,
            source_in=0.0,
            source_out=5.0,
        )
    )
    return project


# ---------------------------------------------------------------------------
# Modèle : TrackRole
# ---------------------------------------------------------------------------


def test_track_role_has_four_values() -> None:
    assert len(TrackRole) == 4


def test_track_role_strings() -> None:
    assert TrackRole("voice").value == "voice"
    assert TrackRole("music").value == "music"
    assert TrackRole("sfx").value == "sfx"
    assert TrackRole("other").value == "other"


def test_track_role_labels_complete() -> None:
    assert set(TRACK_ROLE_LABELS) == set(TrackRole)


# ---------------------------------------------------------------------------
# Modèle : AutomationPoint
# ---------------------------------------------------------------------------


def test_automation_point_defaults_to_zero_fade() -> None:
    point = AutomationPoint(time_seconds=1.0, gain_db=-3.0)
    assert point.time_seconds == 1.0
    assert point.gain_db == -3.0
    assert point.fade_seconds == 0.0


def test_automation_point_rejects_negative_time() -> None:
    with pytest.raises(AudioAutomationRangeError):
        AutomationPoint(time_seconds=-1.0, gain_db=0.0)


def test_automation_point_rejects_negative_fade() -> None:
    with pytest.raises(AudioAutomationRangeError):
        AutomationPoint(time_seconds=1.0, gain_db=0.0, fade_seconds=-1.0)


def test_automation_point_rejects_too_long_fade() -> None:
    with pytest.raises(AudioAutomationRangeError):
        AutomationPoint(
            time_seconds=1.0, gain_db=0.0,
            fade_seconds=MAX_POINT_FADE_SECONDS + 0.01,
        )


def test_automation_point_rejects_non_finite_values() -> None:
    with pytest.raises(AudioAutomationRangeError):
        AutomationPoint(time_seconds=float("nan"), gain_db=0.0)
    with pytest.raises(AudioAutomationRangeError):
        AutomationPoint(time_seconds=1.0, gain_db=float("inf"))


# ---------------------------------------------------------------------------
# TrackAutomation : tri, ajout, suppression, mise à jour
# ---------------------------------------------------------------------------


def test_track_automation_initializes_empty() -> None:
    automation = TrackAutomation(track_id="T1")
    assert automation.points == []
    assert automation.is_empty() is True


def test_track_automation_sorts_points_on_construction() -> None:
    automation = TrackAutomation(
        track_id="T1",
        points=[
            AutomationPoint(time_seconds=2.0, gain_db=0.0),
            AutomationPoint(time_seconds=1.0, gain_db=0.0),
            AutomationPoint(time_seconds=3.0, gain_db=0.0),
        ],
    )
    times = [p.time_seconds for p in automation.points]
    assert times == [1.0, 2.0, 3.0]


def test_track_automation_add_point_inserts_in_order() -> None:
    automation = TrackAutomation(track_id="T1")
    automation.add_point(1.0, -3.0)
    automation.add_point(0.5, -6.0)
    automation.add_point(2.0, 0.0)
    times = [p.time_seconds for p in automation.points]
    assert times == [0.5, 1.0, 2.0]


def test_track_automation_add_point_replaces_same_time() -> None:
    automation = TrackAutomation(track_id="T1")
    automation.add_point(1.0, -3.0)
    automation.add_point(1.0, -6.0)
    assert len(automation.points) == 1
    assert automation.points[0].gain_db == -6.0


def test_track_automation_remove_point_drops() -> None:
    automation = TrackAutomation(track_id="T1")
    automation.add_point(1.0, -3.0)
    automation.add_point(2.0, 0.0)
    removed = automation.remove_point(1.0)
    assert [p.time_seconds for p in automation.points] == [2.0]
    assert removed.time_seconds == 1.0


def test_track_automation_remove_unknown_raises() -> None:
    automation = TrackAutomation(track_id="T1")
    with pytest.raises(AudioAutomationError):
        automation.remove_point(99.0)


def test_track_automation_update_point() -> None:
    automation = TrackAutomation(track_id="T1")
    automation.add_point(1.0, -3.0, fade_seconds=0.5)
    updated = automation.update_point(1.0, gain_db=-6.0, fade_seconds=1.0)
    assert updated.gain_db == -6.0
    assert updated.fade_seconds == 1.0


def test_track_automation_update_unknown_raises() -> None:
    automation = TrackAutomation(track_id="T1")
    with pytest.raises(AudioAutomationError):
        automation.update_point(99.0, gain_db=-3.0)


def test_track_automation_update_preserves_unchanged_fields() -> None:
    automation = TrackAutomation(track_id="T1")
    automation.add_point(1.0, -3.0, fade_seconds=0.5)
    updated = automation.update_point(1.0, gain_db=-9.0)
    assert updated.fade_seconds == 0.5


def test_track_automation_clear() -> None:
    automation = TrackAutomation(track_id="T1")
    automation.add_point(1.0, -3.0)
    automation.add_point(2.0, 0.0)
    automation.points.clear()
    assert automation.is_empty() is True


# ---------------------------------------------------------------------------
# TrackAutomation.gain_at : interpolation
# ---------------------------------------------------------------------------


def test_gain_at_before_first_point_returns_first_gain() -> None:
    automation = TrackAutomation(
        track_id="T1",
        points=[
            AutomationPoint(time_seconds=2.0, gain_db=-6.0),
            AutomationPoint(time_seconds=4.0, gain_db=0.0),
        ],
    )
    assert automation.gain_at(0.0) == -6.0
    assert automation.gain_at(1.999) == -6.0


def test_gain_at_after_last_point_returns_last_gain() -> None:
    automation = TrackAutomation(
        track_id="T1",
        points=[
            AutomationPoint(time_seconds=2.0, gain_db=-6.0),
            AutomationPoint(time_seconds=4.0, gain_db=0.0),
        ],
    )
    assert automation.gain_at(5.0) == 0.0
    assert automation.gain_at(4.001) == 0.0


def test_gain_at_zero_fade_returns_destination_gain() -> None:
    automation = TrackAutomation(
        track_id="T1",
        points=[
            AutomationPoint(time_seconds=2.0, gain_db=-6.0),
            AutomationPoint(time_seconds=4.0, gain_db=0.0, fade_seconds=0.0),
        ],
    )
    assert automation.gain_at(3.0) == 0.0


def test_gain_at_with_fade_keeps_previous_gain_during_fade() -> None:
    """Pendant la fenêtre de fondu déclarée sur le point destination,
    on conserve le gain du point précédent."""
    automation = TrackAutomation(
        track_id="T1",
        points=[
            AutomationPoint(time_seconds=2.0, gain_db=-6.0),
            AutomationPoint(time_seconds=4.0, gain_db=0.0, fade_seconds=1.0),
        ],
    )
    # Avant la fin du fondu (t=2 + 1 = 3.0) : on reste à -6.0.
    assert automation.gain_at(2.5) == -6.0
    assert automation.gain_at(3.0) == -6.0


def test_gain_at_after_fade_interpolates_linearly() -> None:
    """Après la fenêtre de fondu, on interpole linéairement jusqu'au
    point destination."""
    automation = TrackAutomation(
        track_id="T1",
        points=[
            AutomationPoint(time_seconds=2.0, gain_db=-6.0),
            AutomationPoint(time_seconds=4.0, gain_db=0.0, fade_seconds=1.0),
        ],
    )
    # Fin du fondu : t = 2 + 1 = 3.0
    # Point destination : t = 4.0
    # Span : 1.0 (de t=3.0 à t=4.0)
    assert automation.gain_at(4.0) == pytest.approx(0.0)
    assert automation.gain_at(3.5) == pytest.approx(-3.0)


def test_gain_at_empty_automation_returns_zero() -> None:
    automation = TrackAutomation(track_id="T1")
    assert automation.gain_at(2.0) == 0.0


# ---------------------------------------------------------------------------
# DuckingConfig
# ---------------------------------------------------------------------------


def test_ducking_config_default_values() -> None:
    config = DuckingConfig()
    assert config.threshold_db == -20.0
    assert config.reduction_db == 12.0
    assert config.attack_seconds == 0.05
    assert config.release_seconds == 0.4
    assert config.ratio == 20.0


def test_ducking_config_rejects_out_of_range() -> None:
    with pytest.raises(AudioAutomationRangeError):
        DuckingConfig(threshold_db=12.0)
    with pytest.raises(AudioAutomationRangeError):
        DuckingConfig(reduction_db=-1.0)
    with pytest.raises(AudioAutomationRangeError):
        DuckingConfig(attack_seconds=MIN_DUCKING_ATTACK_S - 0.001)
    with pytest.raises(AudioAutomationRangeError):
        DuckingConfig(release_seconds=MAX_DUCKING_RELEASE_S + 1.0)


def test_ducking_config_ratio_capped_at_20() -> None:
    """Le ratio est volontairement élevé pour atteindre la réduction
    demandée avec un dépassement modéré du seuil."""
    config = DuckingConfig()
    assert config.ratio == 20.0


# ---------------------------------------------------------------------------
# DuckingSidechain
# ---------------------------------------------------------------------------


def test_ducking_sidechain_rejects_same_track() -> None:
    with pytest.raises(AudioAutomationError):
        DuckingSidechain(
            id="duck-1",
            music_track_id="T1",
            voice_track_id="T1",
        )


def test_ducking_sidechain_rejects_invalid_id() -> None:
    with pytest.raises(AudioAutomationError):
        DuckingSidechain(
            id="",
            music_track_id="M1",
            voice_track_id="V1",
        )


def test_ducking_sidechain_default_enabled() -> None:
    sidechain = DuckingSidechain(
        id="duck-1",
        music_track_id="M1",
        voice_track_id="V1",
    )
    assert sidechain.enabled is True


# ---------------------------------------------------------------------------
# AudioAutomationService : rôles
# ---------------------------------------------------------------------------


def test_service_set_role_returns_normalized_role() -> None:
    project = _project_with_music_and_voice()
    service = AudioAutomationService()
    role = service.set_track_role(project, "M1", "music")
    assert role is TrackRole.MUSIC
    assert service.get_track_role(project, "M1") is TrackRole.MUSIC


def test_service_get_role_defaults_to_other() -> None:
    project = _project_with_music_and_voice()
    service = AudioAutomationService()
    assert service.get_track_role(project, "M1") is TrackRole.OTHER


def test_service_set_role_rejects_subtitle_track() -> None:
    project = Project(
        name="x",
        tracks=[Track(id="S1", name="S1", type="subtitle")],
    )
    service = AudioAutomationService()
    with pytest.raises(AudioAutomationError):
        service.set_track_role(project, "S1", "voice")


def test_service_set_role_rejects_unknown_track() -> None:
    project = _project_with_music_and_voice()
    service = AudioAutomationService()
    with pytest.raises(AudioAutomationError):
        service.set_track_role(project, "unknown", "voice")


# ---------------------------------------------------------------------------
# AudioAutomationService : automation
# ---------------------------------------------------------------------------


def test_service_add_automation_point_creates_automation() -> None:
    project = _project_with_music_and_voice()
    service = AudioAutomationService()
    point = service.add_automation_point(project, "M1", 1.0, -3.0, 0.5)
    assert point.time_seconds == 1.0
    # L'automation est désormais une TrackAutomation.
    assert isinstance(project.tracks[0].automation, TrackAutomation)
    assert len(project.tracks[0].automation.points) == 1


def test_service_ensure_automation_is_idempotent() -> None:
    project = _project_with_music_and_voice()
    service = AudioAutomationService()
    automation_a = service.ensure_automation(project, "M1")
    automation_b = service.ensure_automation(project, "M1")
    assert automation_a is automation_b


def test_service_update_automation_point_changes_field() -> None:
    project = _project_with_music_and_voice()
    service = AudioAutomationService()
    service.add_automation_point(project, "M1", 1.0, -3.0)
    updated = service.update_automation_point(project, "M1", 1.0, gain_db=-9.0)
    assert updated.gain_db == -9.0


def test_service_remove_automation_point() -> None:
    project = _project_with_music_and_voice()
    service = AudioAutomationService()
    service.add_automation_point(project, "M1", 1.0, -3.0)
    service.remove_automation_point(project, "M1", 1.0)
    assert project.tracks[0].automation.points == []


def test_service_clear_automation_keeps_object() -> None:
    project = _project_with_music_and_voice()
    service = AudioAutomationService()
    service.add_automation_point(project, "M1", 1.0, -3.0)
    automation = service.ensure_automation(project, "M1")
    service.clear_automation(project, "M1")
    assert automation.points == []


# ---------------------------------------------------------------------------
# AudioAutomationService : ducking
# ---------------------------------------------------------------------------


def test_service_add_ducking_sidechain_stored_on_project() -> None:
    project = _project_with_music_and_voice()
    service = AudioAutomationService()
    sidechain = service.add_ducking_sidechain(
        project, "M1", "V1", DuckingConfig(threshold_db=-25.0)
    )
    assert sidechain.id.startswith("duck-")
    assert len(service.all_sidechains(project)) == 1
    assert len(service.sidechains_for(project, "M1")) == 1
    assert len(service.sidechains_from(project, "V1")) == 1


def test_service_add_ducking_rejects_same_track_pair() -> None:
    project = _project_with_music_and_voice()
    service = AudioAutomationService()
    service.add_ducking_sidechain(project, "M1", "V1")
    with pytest.raises(AudioAutomationError):
        service.add_ducking_sidechain(project, "M1", "V1")


def test_service_remove_ducking_sidechain() -> None:
    project = _project_with_music_and_voice()
    service = AudioAutomationService()
    sidechain = service.add_ducking_sidechain(project, "M1", "V1")
    service.remove_ducking_sidechain(project, sidechain.id)
    assert service.all_sidechains(project) == []


def test_service_update_ducking_config() -> None:
    project = _project_with_music_and_voice()
    service = AudioAutomationService()
    sidechain = service.add_ducking_sidechain(project, "M1", "V1")
    new_config = DuckingConfig(threshold_db=-10.0, reduction_db=20.0)
    updated = service.update_ducking_config(project, sidechain.id, new_config)
    assert updated.config.threshold_db == -10.0
    assert updated.config.reduction_db == 20.0


def test_service_set_ducking_enabled() -> None:
    project = _project_with_music_and_voice()
    service = AudioAutomationService()
    sidechain = service.add_ducking_sidechain(project, "M1", "V1")
    updated = service.set_ducking_enabled(project, sidechain.id, False)
    assert updated.enabled is False


# ---------------------------------------------------------------------------
# Persistance : roundtrip .kut
# ---------------------------------------------------------------------------


def test_roundtrip_preserves_roles_automation_and_ducking(tmp_path) -> None:
    project = _project_with_music_and_voice()
    service = AudioAutomationService()
    service.set_track_role(project, "M1", TrackRole.MUSIC)
    service.set_track_role(project, "V1", TrackRole.VOICE)
    service.add_automation_point(project, "M1", 0.0, -6.0, 0.5)
    service.add_automation_point(project, "M1", 2.0, -3.0, 1.0)
    service.add_ducking_sidechain(
        project, "M1", "V1",
        DuckingConfig(threshold_db=-25.0, reduction_db=10.0),
    )

    target = tmp_path / "mix.kut"
    save_project(project, str(target))
    loaded = load_project(str(target))

    # Rôles.
    assert loaded.tracks[0].audio_role == "music"
    assert loaded.tracks[1].audio_role == "voice"
    # Automation : ce sont des AutomationPoint.
    auto = loaded.tracks[0].automation
    assert len(auto) == 2
    assert auto[0].time_seconds == 0.0
    assert auto[1].fade_seconds == 1.0
    # Ducking.
    assert len(loaded.ducking_sidechains) == 1
    sc = loaded.ducking_sidechains[0]
    assert sc.music_track_id == "M1"
    assert sc.voice_track_id == "V1"
    assert sc.config.threshold_db == -25.0
    assert sc.config.reduction_db == 10.0


def test_legacy_project_without_automation_loads_with_defaults(tmp_path) -> None:
    """Un projet v11.0 (sans ``audio_role`` / ``automation`` /
    ``ducking_sidechains``) charge avec valeurs neutres."""
    project = _project_with_music_and_voice()
    target = tmp_path / "legacy.kut"
    save_project(project, str(target))
    import json

    raw = json.loads(target.read_text(encoding="utf-8"))
    # On retire les nouvelles clés au niveau piste et projet.
    for track in raw["project"]["tracks"]:
        track.pop("audio_role", None)
        track.pop("automation", None)
        track.pop("ducking_config", None)
    raw["project"].pop("ducking_sidechains", None)
    target.write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_project(str(target))
    assert loaded.tracks[0].audio_role == "other"
    assert loaded.tracks[0].automation == []
    assert loaded.tracks[0].ducking_config is None
    assert loaded.ducking_sidechains == []


def test_invalid_role_falls_back_to_other() -> None:
    """Un rôle inconnu est silencieusement remplacé par ``other``."""
    import json

    project = _project_with_music_and_voice()
    target = "/tmp/invalid_role.kut"  # noqa: S108
    save_project(project, target)
    raw = json.loads(open(target, encoding="utf-8").read())
    raw["project"]["tracks"][0]["audio_role"] = "alien_role"
    open(target, "w", encoding="utf-8").write(json.dumps(raw))

    loaded = load_project(target)
    assert loaded.tracks[0].audio_role == "other"


def test_ducking_with_unknown_track_is_dropped(tmp_path) -> None:
    """Une association de ducking pointant vers une piste inconnue
    est silencieusement écartée."""
    import json

    project = _project_with_music_and_voice()
    save_project(project, str(tmp_path / "duck.kut"))
    raw = json.loads((tmp_path / "duck.kut").read_text(encoding="utf-8"))
    raw["project"]["ducking_sidechains"] = [
        {
            "id": "duck-x",
            "music_track_id": "M1",
            "voice_track_id": "V-missing",
            "config": {
                "threshold_db": -20.0, "reduction_db": 10.0,
                "attack_seconds": 0.05, "release_seconds": 0.4,
            },
            "enabled": True,
        }
    ]
    (tmp_path / "duck.kut").write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_project(str(tmp_path / "duck.kut"))
    assert loaded.ducking_sidechains == []


def test_ducking_with_same_music_and_voice_is_rejected(tmp_path) -> None:
    """Une association qui se ducking elle-même est rejetée."""
    import json

    project = _project_with_music_and_voice()
    save_project(project, str(tmp_path / "self_duck.kut"))
    raw = json.loads((tmp_path / "self_duck.kut").read_text(encoding="utf-8"))
    raw["project"]["ducking_sidechains"] = [
        {
            "id": "duck-x",
            "music_track_id": "M1",
            "voice_track_id": "M1",
            "config": {
                "threshold_db": -20.0, "reduction_db": 10.0,
                "attack_seconds": 0.05, "release_seconds": 0.4,
            },
            "enabled": True,
        }
    ]
    (tmp_path / "self_duck.kut").write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_project(str(tmp_path / "self_duck.kut"))
    assert loaded.ducking_sidechains == []


def test_invalid_automation_point_is_dropped(tmp_path) -> None:
    """Une entrée d'automation invalide (temps négatif, fade hors
    bornes) est silencieusement écartée."""
    import json

    project = _project_with_music_and_voice()
    save_project(project, str(tmp_path / "auto.kut"))
    raw = json.loads((tmp_path / "auto.kut").read_text(encoding="utf-8"))
    raw["project"]["tracks"][0]["automation"] = [
        {"time_seconds": -1.0, "gain_db": -3.0, "fade_seconds": 0.0},  # invalide
        {"time_seconds": 0.5, "gain_db": -6.0, "fade_seconds": 0.5},  # OK
    ]
    (tmp_path / "auto.kut").write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_project(str(tmp_path / "auto.kut"))
    auto = loaded.tracks[0].automation
    assert len(auto) == 1
    assert auto[0].time_seconds == 0.5


# ---------------------------------------------------------------------------
# RenderPlan : transport vers AudioLayer
# ---------------------------------------------------------------------------


def test_audio_layer_carries_automation_and_ducking() -> None:
    project = _project_with_audio_clips()
    service = AudioAutomationService()
    service.set_track_role(project, "M1", TrackRole.MUSIC)
    service.set_track_role(project, "V1", TrackRole.VOICE)
    service.add_automation_point(project, "M1", 0.0, -6.0)
    service.add_ducking_sidechain(project, "M1", "V1", DuckingConfig())

    plan = build_render_plan(project)
    music_layer = next(l for l in plan.audio_layers if l.track_id == "M1")
    voice_layer = next(l for l in plan.audio_layers if l.track_id == "V1")

    assert len(music_layer.track_automation) == 1
    assert len(music_layer.ducking_sidechains) == 1
    assert voice_layer.ducking_sidechains == ()


def test_audio_layer_falls_back_to_list_for_legacy_automation() -> None:
    """Un projet pré-tâche 28 peut porter une automation sous forme
    de liste : le layer la transporte en tuple."""
    project = _project_with_audio_clips()
    project.tracks[0].automation = [
        AutomationPoint(time_seconds=0.0, gain_db=-6.0)
    ]

    plan = build_render_plan(project)
    layer = next(l for l in plan.audio_layers if l.track_id == "M1")
    assert len(layer.track_automation) == 1


# ---------------------------------------------------------------------------
# Filtres FFmpeg
# ---------------------------------------------------------------------------


def test_volume_envelope_returns_none_when_automation_empty() -> None:
    assert _build_track_volume_envelope((), 0.0, 5.0) is None


def test_volume_envelope_returns_linear_interp_filter() -> None:
    automation = (
        AutomationPoint(time_seconds=0.0, gain_db=-6.0),
        AutomationPoint(time_seconds=2.0, gain_db=0.0),
    )
    filter_str = _build_track_volume_envelope(automation, 0.0, 5.0)
    assert filter_str is not None
    assert "volume=" in filter_str
    assert "linear_interp" in filter_str
    assert "-6.0000" in filter_str
    assert "0.0000" in filter_str


def test_volume_envelope_extends_to_clip_edges() -> None:
    """Si l'automation ne couvre qu'une portion, on fige le gain aux
    bords du clip avec une valeur constante."""
    automation = (
        AutomationPoint(time_seconds=1.0, gain_db=-3.0),
        AutomationPoint(time_seconds=2.0, gain_db=0.0),
    )
    # Le clip va de t=0 à t=5.
    filter_str = _build_track_volume_envelope(automation, 0.0, 5.0)
    assert filter_str is not None
    # Le premier segment doit explicitement épingler t=0.0 à -3.0 dB
    # (gain du premier point projeté au bord gauche du clip).
    assert "0.000000 -3.0000" in filter_str


def test_ducking_chain_uses_sidechain_label() -> None:
    """Le filtre ``sidechaincompress`` cite le label du mix vocal."""
    config = DuckingConfig(threshold_db=-25.0, reduction_db=10.0)
    sidechain = DuckingSidechain(
        id="duck-1",
        music_track_id="M1",
        voice_track_id="V1",
        config=config,
    )
    object.__setattr__(sidechain, "_voice_mix_label", "av0")
    chain = _build_ducking_chain(sidechain)
    assert "sidechaincompress" in chain
    assert "threshold=" in chain
    assert "ratio=" in chain
    assert "sidechain=[av0]" in chain


def test_ducking_chain_handles_default_config() -> None:
    """Un ``sidechain`` sans config explicite utilise les défauts."""
    sidechain = DuckingSidechain(
        id="duck-1",
        music_track_id="M1",
        voice_track_id="V1",
    )
    object.__setattr__(sidechain, "_voice_mix_label", "av0")
    chain = _build_ducking_chain(sidechain)
    assert "sidechaincompress" in chain


def test_volume_envelope_and_ducking_chain_compose_without_errors() -> None:
    """Le layer audio reçoit à la fois un filtre d'enveloppe et un
    filtre de ducking, sans collision."""
    project = _project_with_audio_clips()
    service = AudioAutomationService()
    service.set_track_role(project, "M1", TrackRole.MUSIC)
    service.set_track_role(project, "V1", TrackRole.VOICE)
    service.add_automation_point(project, "M1", 0.0, -6.0)
    service.add_ducking_sidechain(project, "M1", "V1", DuckingConfig())

    layer = _build_audio_layer(
        project.tracks[0].clips[0],
        project.media_assets[0],
        "M1",
        0,
        project.tracks[0],
        ducking_sidechains=service.all_sidechains(project),
    )
    assert len(layer.track_automation) == 1
    assert len(layer.ducking_sidechains) == 1


# ---------------------------------------------------------------------------
# Undo / Redo
# ---------------------------------------------------------------------------


def test_undo_redo_for_set_track_role() -> None:
    project = _project_with_music_and_voice()
    history = ProjectHistory()
    history.reset(project)

    service = AudioAutomationService()
    service.set_track_role(project, "M1", TrackRole.MUSIC)
    history.record(project, "Définir le rôle musique")
    assert project.tracks[0].audio_role == "music"

    restored = history.undo()
    assert restored is not None
    assert restored.tracks[0].audio_role == "other"


def test_undo_redo_for_add_automation_point() -> None:
    project = _project_with_music_and_voice()
    history = ProjectHistory()
    history.reset(project)

    service = AudioAutomationService()
    service.add_automation_point(project, "M1", 1.0, -3.0)
    history.record(project, "Ajouter un point d'automation")
    assert len(project.tracks[0].automation.points) == 1

    restored = history.undo()
    assert restored is not None
    # ``ProjectHistory`` capture le projet tel quel : la liste est
    # restaurée sous forme de liste (et non de ``TrackAutomation``).
    assert list(restored.tracks[0].automation) == []


def test_undo_redo_for_remove_automation_point() -> None:
    project = _project_with_music_and_voice()
    service = AudioAutomationService()
    service.add_automation_point(project, "M1", 1.0, -3.0)

    history = ProjectHistory()
    history.reset(project)

    service.remove_automation_point(project, "M1", 1.0)
    history.record(project, "Supprimer un point d'automation")
    assert project.tracks[0].automation.points == []

    restored = history.undo()
    assert restored is not None
    # Après undo, l'automation (liste ou ``TrackAutomation``) est
    # restaurée avec le point supprimé.
    automation = restored.tracks[0].automation
    if hasattr(automation, "points"):
        assert len(automation.points) == 1
    else:
        assert len(automation) == 1


def test_undo_redo_for_add_ducking_sidechain() -> None:
    project = _project_with_music_and_voice()
    history = ProjectHistory()
    history.reset(project)

    service = AudioAutomationService()
    service.add_ducking_sidechain(project, "M1", "V1", DuckingConfig())
    history.record(project, "Ajouter un ducking")
    assert len(project.ducking_sidechains) == 1

    restored = history.undo()
    assert restored is not None
    assert restored.ducking_sidechains == []


def test_undo_redo_for_remove_ducking_sidechain() -> None:
    project = _project_with_music_and_voice()
    service = AudioAutomationService()
    sidechain = service.add_ducking_sidechain(
        project, "M1", "V1", DuckingConfig()
    )

    history = ProjectHistory()
    history.reset(project)

    service.remove_ducking_sidechain(project, sidechain.id)
    history.record(project, "Supprimer un ducking")
    assert project.ducking_sidechains == []

    restored = history.undo()
    assert restored is not None
    assert len(restored.ducking_sidechains) == 1