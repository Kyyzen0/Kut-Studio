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

import math
import re

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
    # Automation : une TrackAutomation de AutomationPoint.
    auto = loaded.tracks[0].automation.points
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
    for track in raw["project"]["sequences"][0]["tracks"]:
        track.pop("audio_role", None)
        track.pop("automation", None)
        track.pop("ducking_config", None)
    raw["project"].pop("ducking_sidechains", None)
    target.write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_project(str(target))
    assert loaded.tracks[0].audio_role == "other"
    assert loaded.tracks[0].automation.points == []
    assert loaded.tracks[0].ducking_config is None
    assert loaded.ducking_sidechains == []


def test_invalid_role_falls_back_to_other() -> None:
    """Un rôle inconnu est silencieusement remplacé par ``other``."""
    import json

    project = _project_with_music_and_voice()
    target = "/tmp/invalid_role.kut"  # noqa: S108
    save_project(project, target)
    raw = json.loads(open(target, encoding="utf-8").read())
    raw["project"]["sequences"][0]["tracks"][0]["audio_role"] = "alien_role"
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
    raw["project"]["sequences"][0]["tracks"][0]["automation"] = [
        {"time_seconds": -1.0, "gain_db": -3.0, "fade_seconds": 0.0},  # invalide
        {"time_seconds": 0.5, "gain_db": -6.0, "fade_seconds": 0.5},  # OK
    ]
    (tmp_path / "auto.kut").write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_project(str(tmp_path / "auto.kut"))
    auto = loaded.tracks[0].automation.points
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


def test_audio_layer_carries_a_legacy_list_assigned_to_the_track() -> None:
    """Un ancien code qui affecte une liste de points voit sa courbe normalisée par le modèle (``TrackAutomation``),
    puis transportée en tuple par le plan de rendu."""
    project = _project_with_audio_clips()
    project.tracks[0].automation = [
        AutomationPoint(time_seconds=0.0, gain_db=-6.0)
    ]
    assert isinstance(project.tracks[0].automation, TrackAutomation)

    plan = build_render_plan(project)
    layer = next(l for l in plan.audio_layers if l.track_id == "M1")
    assert len(layer.track_automation) == 1


# ---------------------------------------------------------------------------
# Filtres FFmpeg
# ---------------------------------------------------------------------------


def test_volume_envelope_returns_none_when_automation_empty() -> None:
    assert _build_track_volume_envelope((), 0.0, 5.0) is None


def _envelope_gain(filter_str: str | None, t: float) -> float:
    """Facteur que le filtre émis applique à l'instant ``t`` du clip, calculé comme FFmpeg le ferait.

    L'expression n'emploie que ``t``, l'arithmétique, ``lte``, ``gt`` et ``pow`` : Python l'évalue tel quel. Un filtre
    ``volume=<g>dB`` est un gain constant ; pas de filtre, le gain unité.
    """
    if filter_str is None:
        return 1.0
    constant = re.fullmatch(r"volume=(-?\d+\.\d+)dB", filter_str)
    if constant:
        return 10.0 ** (float(constant.group(1)) / 20.0)
    match = re.fullmatch(r"asetnsamples=n=256:p=0,volume='(.*)':eval=frame", filter_str)
    assert match, filter_str
    names = {"t": t, "lte": lambda a, b: float(a <= b), "gt": lambda a, b: float(a > b), "pow": pow}
    return eval(match.group(1), {"__builtins__": {}}, names)


CURVES = [
    pytest.param([AutomationPoint(0.0, -6.0), AutomationPoint(2.0, 0.0)], id="saut"),
    pytest.param([AutomationPoint(1.0, -3.0), AutomationPoint(2.0, 0.0, 0.5)], id="fondu"),
    pytest.param(
        [AutomationPoint(1.0, 0.0), AutomationPoint(2.0, -12.0, 0.5), AutomationPoint(4.0, -12.0),
         AutomationPoint(5.0, 0.0, 0.5)],
        id="creux",
    ),
    pytest.param([AutomationPoint(1.0, 0.0), AutomationPoint(1.5, -9.0, 2.0)], id="fondu-plus-long-que-l-ecart"),
    pytest.param([AutomationPoint(3.0, -9.0)], id="un-seul-point"),
]


@pytest.mark.parametrize("points", CURVES)
@pytest.mark.parametrize(("timeline_start", "duration"), [(0.0, 6.0), (1.25, 3.0), (2.5, 4.0), (7.0, 2.0)])
def test_volume_envelope_applies_gain_at_in_clip_time(points, timeline_start, duration) -> None:
    """Le filtre émis donne, à chaque instant du clip, le gain de ``gain_at`` à l'instant correspondant de la timeline.

    Le ``t`` du filtre part de 0 au début du clip (le filtre est avant ``adelay``) : un clip posé à 2,5 s lit la courbe
    à partir de 2,5 s. Le gain est un facteur linéaire, jamais des dB lus comme un facteur.
    """
    automation = TrackAutomation(track_id="M1", points=list(points))
    filter_str = _build_track_volume_envelope(tuple(automation.points), timeline_start, duration)
    # Échantillonné entre les instants ronds : à l'instant exact d'un saut, ``gain_at`` donne le gain d'après pour le
    # dernier point et celui d'avant pour un point intérieur, une différence qu'aucune trame audio ne peut entendre.
    for step in range(240):
        local = duration * (step + 0.5) / 240
        expected = 10.0 ** (automation.gain_at(timeline_start + local) / 20.0)
        assert _envelope_gain(filter_str, local) == pytest.approx(expected, rel=1e-4), (local, filter_str)


def test_a_constant_gain_is_a_plain_db_volume() -> None:
    """Un gain qui ne bouge pas sur le clip : ``volume=<g>dB`` (un nombre nu serait un facteur : -6 deviendrait ×-6)."""
    assert _build_track_volume_envelope((AutomationPoint(0.0, -6.0),), 0.0, 5.0) == "volume=-6.000dB"
    assert _build_track_volume_envelope((AutomationPoint(0.0, 0.0),), 0.0, 5.0) is None


def test_a_clip_after_the_last_point_takes_the_last_gain() -> None:
    """Hors des points, le gain est celui du point le plus proche *dans le temps* : le dernier après la courbe."""
    points = (AutomationPoint(0.0, -3.0), AutomationPoint(1.0, -9.0))
    assert _build_track_volume_envelope(points, 4.0, 2.0) == "volume=-9.000dB"
    assert _build_track_volume_envelope(points, -3.0, 2.0) == "volume=-3.000dB"


def test_volume_envelope_stays_flat_with_many_points() -> None:
    """Une somme de morceaux disjoints, pas d'imbrication : la longueur croît avec les points, pas la profondeur."""
    points = tuple(AutomationPoint(float(i), -12.0 * (i % 2), 0.1) for i in range(200))
    filter_str = _build_track_volume_envelope(points, 0.0, 200.0)
    assert filter_str is not None and "if(" not in filter_str
    curve = TrackAutomation(track_id="M1", points=list(points))
    for t in (0.05, 50.5, 51.05, 120.7, 199.5):
        assert _envelope_gain(filter_str, t) == pytest.approx(10.0 ** (curve.gain_at(t) / 20.0), rel=1e-4)


def _options(chain: str) -> dict[str, str]:
    compressor = re.search(r"sidechaincompress=([^\[]*)\[", chain)
    assert compressor, chain
    return dict(item.split("=", 1) for item in compressor.group(1).split(":"))


def _sidechain(config: DuckingConfig | None = None) -> DuckingSidechain:
    if config is None:
        return DuckingSidechain(id="duck-1", music_track_id="M1", voice_track_id="V1")
    return DuckingSidechain(id="duck-1", music_track_id="M1", voice_track_id="V1", config=config)


def test_ducking_chain_takes_the_voice_on_its_second_input() -> None:
    """``sidechaincompress`` n'a pas d'option ``sidechain`` : la clé est son entrée 1, la musique son entrée 0."""
    chain = _build_ducking_chain(
        _sidechain(DuckingConfig(threshold_db=-25.0, reduction_db=10.0)),
        voice_label="av0", main_label="a0", output_label="a0_duck", key_label="a0_sc0",
    )
    key, compressor = chain.split(";")
    assert key.startswith("[av0]aeval=") and key.endswith("[a0_sc0]")
    assert compressor.startswith("[a0][a0_sc0]sidechaincompress=") and compressor.endswith("[a0_duck]")
    assert "sidechain=" not in chain


def test_ducking_chain_speaks_ffmpeg_units() -> None:
    """Seuil en amplitude linéaire, attaque et relâchement en millisecondes, pas de gain de compensation."""
    options = _options(_build_ducking_chain(
        _sidechain(), voice_label="av0", main_label="a0", output_label="o", key_label="k",
    ))
    assert float(options["threshold"]) == pytest.approx(0.1, rel=1e-4)       # -20 dB
    assert float(options["ratio"]) == pytest.approx(20.0)
    assert float(options["attack"]) == pytest.approx(50.0)                    # 0,05 s
    assert float(options["release"]) == pytest.approx(400.0)                  # 0,4 s
    assert float(options["makeup"]) == 1.0


@pytest.mark.parametrize("config", [
    DuckingConfig(),
    DuckingConfig(threshold_db=MIN_DUCKING_THRESHOLD_DB, reduction_db=MAX_DUCKING_REDUCTION_DB,
                  attack_seconds=MAX_DUCKING_ATTACK_S, release_seconds=MAX_DUCKING_RELEASE_S),
    DuckingConfig(threshold_db=MAX_DUCKING_THRESHOLD_DB, reduction_db=MIN_DUCKING_REDUCTION_DB,
                  attack_seconds=MIN_DUCKING_ATTACK_S, release_seconds=MIN_DUCKING_RELEASE_S),
])
def test_every_ducking_setting_stays_inside_ffmpeg_ranges(config) -> None:
    """Une option hors bornes refuse le graphe entier : ``ffmpeg -h filter=sidechaincompress``."""
    options = _options(_build_ducking_chain(
        _sidechain(config), voice_label="av0", main_label="a0", output_label="o", key_label="k",
    ))
    assert 0.000976563 <= float(options["threshold"]) <= 1.0
    assert 1.0 <= float(options["ratio"]) <= 20.0
    assert 0.01 <= float(options["attack"]) <= 2000.0
    assert 0.01 <= float(options["release"]) <= 9000.0
    assert 1.0 <= float(options["makeup"]) <= 64.0


def test_the_key_is_clipped_where_the_reduction_reaches_its_maximum() -> None:
    """Le compresseur atténue de ``dépassement * (1 - 1/ratio)`` : écrêter la clé à ``seuil + R*ratio/(ratio-1)``
    plafonne l'atténuation à ``reduction_db`` exactement."""
    config = DuckingConfig(threshold_db=-20.0, reduction_db=12.0)
    chain = _build_ducking_chain(_sidechain(config), voice_label="av0", main_label="a0", output_label="o", key_label="k")
    ceiling = float(re.search(r"clip\(val\(0\),-([\d.]+),", chain).group(1))
    overshoot = 20.0 * math.log10(ceiling / 0.1)
    assert overshoot * (1.0 - 1.0 / config.ratio) == pytest.approx(12.0, abs=1e-3)
    assert "clip(val(1)," in chain                                         # les deux canaux


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
    # ``ProjectHistory`` restaure une copie du projet : la courbe est une ``TrackAutomation``, comme avant.
    assert isinstance(restored.tracks[0].automation, TrackAutomation)
    assert restored.tracks[0].automation.points == []


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