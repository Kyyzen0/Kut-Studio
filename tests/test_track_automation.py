"""``Track.automation`` n'a qu'une forme : une :class:`TrackAutomation`.

Avant, le champ valait une liste de points après un chargement ou une affectation, et une ``TrackAutomation`` une fois le
service d'automation passé : le plan de rendu, ``project_io``, ``sequences`` et plusieurs tests devaient lire les deux
formes (``getattr(automation, "points", automation)``). Désormais le modèle normalise à l'écriture (un seul point d'entrée :
``Track.__setattr__``), ``project_io`` fabrique la forme canonique à la lecture du fichier, et le format ``.kut`` ne change pas.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from core.audio_automation import (
    AudioAutomationService,
    AutomationPoint,
    TrackAutomation,
    coerce_track_automation,
)
from core.edit_history import ProjectHistory
from core.export_engine import _build_audio_filter
from core.project_io import CURRENT_VERSION, SUPPORTED_VERSIONS, load_project, project_payload, save_project
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.sequences import create_sequence_from_selection

from rich_project import build_rich_project
from test_sequences import _clip, _two_level_project

ROOT = Path(__file__).resolve().parent.parent


def _points(automation: TrackAutomation) -> list[tuple[float, float, float]]:
    return [(p.time_seconds, p.gain_db, p.fade_seconds) for p in automation.points]


def _unsorted() -> list[AutomationPoint]:
    return [AutomationPoint(3.0, -12.0, 1.0), AutomationPoint(0.5, -6.0, 0.25)]


# --- La forme canonique -----------------------------------------------------------------------------------------


def test_a_new_track_carries_an_empty_automation_bound_to_it():
    track = Track("A1", "A1", "audio")
    assert isinstance(track.automation, TrackAutomation)
    assert track.automation.is_empty() and track.automation.track_id == "A1"


@pytest.mark.parametrize("how", ["constructor", "assignment"])
def test_the_legacy_list_form_is_normalised_however_it_is_written(how):
    """Ancien code et anciens tests écrivent une liste ; le modèle la range, triée, dans une ``TrackAutomation``."""
    if how == "constructor":
        track = Track("A1", "A1", "audio", automation=_unsorted())
    else:
        track = Track("A1", "A1", "audio")
        track.automation = _unsorted()
    assert isinstance(track.automation, TrackAutomation) and track.automation.track_id == "A1"
    assert _points(track.automation) == [(0.5, -6.0, 0.25), (3.0, -12.0, 1.0)]


def test_none_and_tuples_are_normalised_and_anything_else_is_refused():
    track = Track("A1", "A1", "audio")
    track.automation = tuple(_unsorted())
    assert [p.time_seconds for p in track.automation.points] == [0.5, 3.0]
    track.automation = None
    assert track.automation.is_empty()
    with pytest.raises(TypeError):
        track.automation = "pas une courbe"
    assert track.automation.is_empty()                        # l'affectation refusée n'a rien changé


def test_an_assigned_automation_keeps_its_identity_so_in_place_edits_stay_visible():
    track = Track("A1", "A1", "audio")
    curve = TrackAutomation(track_id="A1")
    track.automation = curve
    curve.add_point(1.0, -3.0)
    assert track.automation is curve and len(track.automation.points) == 1


def test_the_track_label_does_not_take_part_in_the_comparison():
    """Deux courbes de mêmes points sont égales : l'historique peut partager une séquence inchangée."""
    assert TrackAutomation("a", _unsorted()) == TrackAutomation("b", _unsorted())
    assert TrackAutomation("a", _unsorted()) != TrackAutomation("a", _unsorted()[:1])


def test_the_coercion_is_idempotent():
    once = coerce_track_automation(_unsorted(), "A1")
    assert coerce_track_automation(once, "A1") is once
    assert _points(coerce_track_automation(once, "A1")) == _points(once)


def test_a_track_id_the_old_validator_refused_no_longer_breaks_anything(tmp_path):
    """``TrackAutomation`` validait l'identifiant de piste (``[A-Za-z0-9._:-]``). Maintenant qu'*toute* piste en porte
    une, un projet dont une piste s'appelle « Piste 1 » doit continuer à s'ouvrir, et son automation à s'éditer."""
    project = Project(name="id", tracks=[Track("Piste 1", "Musique", "audio")])
    AudioAutomationService().add_automation_point(project, "Piste 1", 1.0, -6.0)
    path = tmp_path / "id.kut"
    save_project(project, str(path))
    loaded = load_project(str(path))
    assert _points(loaded.tracks[0].automation) == [(1.0, -6.0, 0.0)]
    AudioAutomationService().add_automation_point(loaded, "Piste 1", 2.0, -3.0)
    assert len(loaded.tracks[0].automation.points) == 2


# --- Chargement : anciens fichiers, format inchangé ------------------------------------------------------------


def _file_with_automation(tmp_path, automation, *, version: int = CURRENT_VERSION) -> Path:
    project = Project(name="a", media_assets=[], tracks=[Track("M1", "M1", "audio")])
    path = tmp_path / "a.kut"
    save_project(project, str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = version
    track = data["project"]["sequences"][0]["tracks"][0]
    if automation is None:
        track.pop("automation", None)
    else:
        track["automation"] = automation
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_the_points_stored_in_a_kut_load_as_a_sorted_automation_and_bad_ones_are_dropped(tmp_path):
    stored = [
        {"time_seconds": 3.0, "gain_db": -12.0, "fade_seconds": 1.0},
        {"time_seconds": -1.0, "gain_db": -3.0, "fade_seconds": 0.0},          # invalide : écarté
        {"time_seconds": 0.5, "gain_db": -6.0, "fade_seconds": 0.25},
    ]
    track = load_project(str(_file_with_automation(tmp_path, stored))).tracks[0]
    assert isinstance(track.automation, TrackAutomation) and track.automation.track_id == "M1"
    assert _points(track.automation) == [(0.5, -6.0, 0.25), (3.0, -12.0, 1.0)]


@pytest.mark.parametrize("version", sorted(SUPPORTED_VERSIONS))
def test_every_supported_version_loads_an_automation_in_canonical_form(tmp_path, version):
    """Les versions antérieures à l'automation n'ont pas la clé ; les autres la portent : même forme à la sortie."""
    for stored in (None, [], [{"time_seconds": 1.0, "gain_db": -3.0, "fade_seconds": 0.0}]):
        track = load_project(str(_file_with_automation(tmp_path, stored, version=version))).tracks[0]
        assert isinstance(track.automation, TrackAutomation)
        assert len(track.automation.points) == (1 if stored else 0)


def test_the_format_is_unchanged(tmp_path):
    assert CURRENT_VERSION == 16
    project = Project(name="f", tracks=[Track("M1", "M1", "audio")])
    AudioAutomationService().add_automation_point(project, "M1", 1.0, -6.0, 0.2)
    track = project_payload(project)["project"]["sequences"][0]["tracks"][0]
    assert track["automation"] == [{"time_seconds": 1.0, "gain_db": -6.0, "fade_seconds": 0.2}]


def test_save_load_save_is_identical_with_automation_on_audio_and_video_tracks(tmp_path):
    project = build_rich_project()
    assert any(not track.automation.is_empty() for track in project.tracks if track.type == "video")
    first, second = tmp_path / "1.kut", tmp_path / "2.kut"
    save_project(project, str(first))
    save_project(load_project(str(first)), str(second))
    assert json.loads(first.read_text(encoding="utf-8")) == json.loads(second.read_text(encoding="utf-8"))


# --- Historique et copies ---------------------------------------------------------------------------------------


def test_history_snapshots_keep_their_own_automation():
    project = Project(name="h", tracks=[Track("M1", "M1", "audio")])
    service = AudioAutomationService()
    service.add_automation_point(project, "M1", 1.0, -6.0)
    history = ProjectHistory()
    history.reset(project)
    service.add_automation_point(project, "M1", 2.0, -3.0)
    history.record(project, "Ajouter un point")
    service.add_automation_point(project, "M1", 3.0, 0.0)               # modification non enregistrée

    restored = history.undo()

    assert restored is not None
    assert isinstance(restored.tracks[0].automation, TrackAutomation)
    assert [p.time_seconds for p in restored.tracks[0].automation.points] == [1.0]
    assert [p.time_seconds for p in project.tracks[0].automation.points] == [1.0, 2.0, 3.0]
    assert restored.tracks[0].automation is not project.tracks[0].automation


def test_copies_of_a_track_keep_the_canonical_form():
    track = Track("M1", "M1", "audio", automation=_unsorted())
    deep = copy.deepcopy(track)
    shallow = copy.copy(track)
    assert isinstance(deep.automation, TrackAutomation) and deep.automation is not track.automation
    assert deep == track
    deep.automation.add_point(9.0, 0.0)
    assert len(track.automation.points) == 2                              # la copie profonde est indépendante
    assert isinstance(shallow.automation, TrackAutomation)


# --- Séquences imbriquées ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("legacy_list", [False, True])
def test_nesting_a_selection_moves_the_automation_and_realigns_it(legacy_list):
    """L'automation d'une piste recopiée dans la séquence imbriquée est recalée sur le début de la sélection."""
    project = _two_level_project()
    main = project.get_sequence("main")
    main.tracks[1].clips.append(_clip("t1", "red", "V2", 3.0, 2.0))
    main.tracks[2].clips.append(_clip("a1", "music", "A1", 4.0, 3.0))
    points = [AutomationPoint(1.0, -9.0), AutomationPoint(4.5, -6.0, 0.5), AutomationPoint(6.0, -3.0)]
    if legacy_list:
        main.tracks[2].automation = points
    else:
        for point in points:
            AudioAutomationService().add_automation_point(
                project, "A1", point.time_seconds, point.gain_db, point.fade_seconds
            )

    inner = create_sequence_from_selection(project, ["t1", "a1"], "Scène").sequence

    audio = next(track for track in inner.tracks if track.id == "A1")
    assert isinstance(audio.automation, TrackAutomation) and audio.automation.track_id == "A1"
    # la sélection commence à 3 s : le point à 1 s précède la sélection et disparaît, les autres sont décalés de 3 s
    assert _points(audio.automation) == [(1.5, -6.0, 0.5), (3.0, -3.0, 0.0)]
    assert len(main.tracks[2].automation.points) == 3                    # la piste d'origine n'est pas touchée
    host = next(track for track in inner.tracks if track.id == "V2")
    assert host.automation.is_empty() and isinstance(host.automation, TrackAutomation)


# --- Mixage et export identiques --------------------------------------------------------------------------------

# Chaînes FFmpeg de ce projet (service pour M1 et V1, liste non triée pour A2). Relevées avant la migration, puis
# réécrites quand ``linear_interp`` (fonction inconnue de FFmpeg) a laissé place à une vraie expression : M1 tient -6 dB
# jusqu'à 0,5 + 1 s de fondu puis rejoint -12 dB à 3 s ; A2 saute à -3 dB juste après 0 s (fondu nul) ; V1 est constant.
GOLDEN_FILTERS = [
    "[3:a]atrim=start=0.0:end=6.0,asetpts=PTS-STARTPTS,aformat=channel_layouts=stereo:sample_rates=48000,"
    "asetnsamples=n=256:p=0,volume='pow(10,(lte(t,1.500000)*(-6.0000)+gt(t,1.500000)*lte(t,3.000000)*"
    "(-6.0000+(-6.0000)*(t-1.500000)/1.500000)+gt(t,3.000000)*(-12.0000))/20)':eval=frame,asetpts=PTS+0.0/TB[a0]",
    # Deux points de maintien nul : une ligne droite de 0 à -3 dB (jusqu'au 2026-10-09, un saut juste après 0 s).
    "[3:a]atrim=start=0.0:end=6.0,asetpts=PTS-STARTPTS,aformat=channel_layouts=stereo:sample_rates=48000,"
    "asetnsamples=n=256:p=0,volume='pow(10,(lte(t,0.000000)*0.0000+gt(t,0.000000)*lte(t,1.000000)*"
    "(0.0000+(-3.0000)*(t-0.000000)/1.000000)+gt(t,1.000000)*(-3.0000))/20)':eval=frame,asetpts=PTS+0.0/TB[a1]",
    "[3:a]atrim=start=0.0:end=6.0,asetpts=PTS-STARTPTS,aformat=channel_layouts=stereo:sample_rates=48000,"
    "volume=-9.000dB,asetpts=PTS+0.0/TB[a2]",
]


def _mix_project(*, legacy_lists: bool) -> Project:
    def clip(clip_id, asset_id, track_id):
        return Clip(id=clip_id, asset_id=asset_id, track_id=track_id, timeline_start=0.0, source_in=0.0, source_out=6.0)

    project = Project(
        name="Auto",
        media_assets=[
            MediaAsset("a1", "/nonexistent/a1.wav", "a1", 10.0, 0, 0, 0.0, "audio", True),
            MediaAsset("a2", "/nonexistent/a2.wav", "a2", 10.0, 0, 0, 0.0, "audio", True),
            MediaAsset("v1", "/nonexistent/v1.mp4", "v1", 10.0, 1920, 1080, 30.0, "video", True),
        ],
        tracks=[
            Track("M1", "M1", "audio", clips=[clip("m", "a1", "M1")]),
            Track("A2", "A2", "audio", clips=[clip("n", "a2", "A2")]),
            Track("V1", "V1", "video", clips=[clip("v", "v1", "V1")]),
        ],
    )
    service = AudioAutomationService()
    if legacy_lists:
        project.tracks[0].automation = [AutomationPoint(0.5, -6.0, 0.25), AutomationPoint(3.0, -12.0, 1.0)]
        project.tracks[2].automation = [AutomationPoint(2.0, -9.0)]
    else:
        service.add_automation_point(project, "M1", 0.5, -6.0, 0.25)
        service.add_automation_point(project, "M1", 3.0, -12.0, 1.0)
        service.add_automation_point(project, "V1", 2.0, -9.0)
    project.tracks[1].automation = [AutomationPoint(1.0, -3.0, 0.0), AutomationPoint(0.0, 0.0, 0.0)]
    return project


@pytest.mark.parametrize("legacy_lists", [False, True])
def test_the_export_filters_are_the_ones_computed_before_the_migration(legacy_lists):
    """Mixage identique avant et après : mêmes chaînes FFmpeg, que l'automation vienne du service ou d'une liste."""
    plan = build_render_plan(_mix_project(legacy_lists=legacy_lists))
    filters = [
        _build_audio_filter(index, layer, len(plan.audio_layers), plan.duration)
        for index, layer in enumerate(plan.audio_layers)
    ]
    assert filters == GOLDEN_FILTERS
    # les points arrivent au plan triés quelle que soit la forme d'entrée (avant, la liste non triée passait telle quelle)
    unsorted_layer = next(layer for layer in plan.audio_layers if layer.clip_id == "n")
    assert [p.time_seconds for p in unsorted_layer.track_automation] == [0.0, 1.0]


# --- Plus de double lecture --------------------------------------------------------------------------------------


def test_no_reader_still_handles_two_forms():
    """Aucun module de production ne teste plus « liste ou ``TrackAutomation`` »."""
    pattern = re.compile(r"getattr\([^)]*automation|hasattr\([^)]*[\"']points[\"']|_iter_automation_points")
    offenders = [
        f"{path.relative_to(ROOT).as_posix()}:{number}"
        for package in ("core", "ui")
        for path in sorted((ROOT / package).rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert not offenders, f"Double lecture de Track.automation : {offenders}"
