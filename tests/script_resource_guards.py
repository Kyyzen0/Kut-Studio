"""Inventaire des verbes de script qui CRÉENT quelque chose, et de la garde de plafond de chacun (ADR-0005 ; ``test_script_resource_guards.py``).

Six revues de suite ont trouvé le même défaut : un plafond (calques, images clés, associations de ducking, images suivies, clips d'une
séquence…) vérifié par un chemin de création mais pas par les autres. Ce module est la **décision écrite** de chaque verbe créateur :

* ``CEILINGS`` : le verbe refuse AVANT de créer quand le projet est au plafond de sa ressource (preuve comportementale, générique) ;
* ``BOUNDED`` : il ne peut pas faire grossir une ressource sans limite (raison écrite) ;
* ``tests/data/script_resource_debt.json`` : pas de plafond aujourd'hui (dette nommée, avec raison ; ne peut que diminuer — le test
  échoue dès que le verbe se met à refuser, pour qu'on le passe en ``CEILINGS``).

Les créateurs sont **découverts** (champ ``creates`` de la table ``VERBS`` ou appel, direct ou via une fonction de ``core/scripting/``,
d'une fonction de création du modèle : :func:`discovered_creators`) : un verbe nouveau force une entrée ici ; aucun nombre de verbes n'est écrit.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import textwrap
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.audio_automation import DuckingSidechain
from core.audio_buses import add_bus
from core.graphics import add_graphic_clip
from core.library_organization import LibraryOrganization
from core.beat_grid import BeatGrid
from core.multicam_ops import AngleSpec, add_angle, create_multicam_source
from core.tracking_model import ClipTracking, Tracker
from core.visual_effects import TransformKeyframe
from core.project_model import MAIN_SEQUENCE_ID, Clip, MediaAsset, Project, Track
from core.scripting.verbs import VERBS
from core.sequences import create_sequence

DEBT_PATH = Path(__file__).resolve().parent / "data" / "script_resource_debt.json"

CAP = 6
"""Plafond de remplacement quand une constante est abaissée par ``monkeypatch`` (assez bas pour remplir le projet en quelques objets)."""

# --- Découverte des verbes créateurs --------------------------------------------------------------------------------------

_CREATION_NAME = re.compile(r"^(add|create|insert|duplicate|split|cut|paste|nest|group|generate|append|import|make|build|fill|apply|duck)_")
"""Le nom des fonctions du modèle qui créent (``add_clip_to_track``, ``create_sequence``…) : un verbe qui en atteint une est un créateur."""


def _called_names(function: Callable[..., Any], seen: set[Any]) -> set[str]:
    """Les noms appelés par ``function`` et, récursivement, par les fonctions de ``core/scripting/`` qu'elle appelle."""
    if function in seen:
        return set()
    seen.add(function)
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    except (OSError, TypeError):
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        name = callee.id if isinstance(callee, ast.Name) else callee.attr if isinstance(callee, ast.Attribute) else None
        if name is None:
            continue
        found.add(name)
        target = getattr(function, "__globals__", {}).get(name)
        if inspect.isfunction(target) and target.__module__.startswith("core.scripting"):
            found |= _called_names(target, seen)
    return found


def discovered_creators() -> dict[str, str]:
    """``{verbe: pourquoi}`` pour tout verbe de la table ``VERBS`` qui crée : ``creates=…`` ou appel d'une fonction de création du modèle."""
    from tools import scripting_debt as tool

    model = {name.rsplit(".", 1)[-1] for name in tool.candidate_verbs() if _CREATION_NAME.match(name.rsplit(".", 1)[-1])}
    found: dict[str, str] = {}
    for name, verb in VERBS.items():
        reached = sorted(_called_names(verb.build, set()) & model)
        if verb.creates:
            found[name] = f"creates={verb.creates}"
        elif reached:
            found[name] = "appelle " + ", ".join(reached)
    return found


def load_debt(path: Path = DEBT_PATH) -> dict[str, str]:
    return dict(json.loads(path.read_text(encoding="utf-8"))["verbs"])


# --- Le projet de départ ---------------------------------------------------------------------------------------------------


def base_project() -> Project:
    """``main`` : pistes V1 / A1 / G1, un média vidéo ``m`` et un audio ``a``, un clip vidéo ``c1`` (0-4 s) et un clip audio ``a1``."""
    project = Project(
        "Démo", width=1280, height=720, fps=30.0,
        media_assets=[
            MediaAsset("m", "/x/m.mp4", "m", 10.0, 1280, 720, 30.0, "video", has_audio=True),
            MediaAsset("a", "/x/a.wav", "a", 10.0, 0, 0, 0.0, "audio", has_audio=True),
        ],
        tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio"), Track("G1", "G1", "graphics")],
    )
    project.tracks[0].clips.append(Clip("c1", "m", "V1", 0.0, 0.0, 4.0, label="c1"))
    project.tracks[1].clips.append(Clip("a1", "a", "A1", 0.0, 0.0, 4.0, label="a1"))
    return project


# --- Les ressources : comment les compter, les remplir, et quelle constante les plafonne ---------------------------------------


@dataclass(frozen=True)
class Resource:
    """Une ressource plafonnée. ``caps`` : ``(module, constante)`` abaissées à la valeur ``cap`` du cas ; ``count`` : compte INDÉPENDANT
    des gardes (toutes les séquences) ; ``fill(project, n)`` : ajoute ``n`` objets à la séquence ACTIVE ; ``big`` : de quoi dépasser le
    plafond que le verbe devrait avoir (pour prouver qu'un verbe en dette n'en a pas encore) ; ``cross_sequence`` : le plafond est celui
    du projet (toutes séquences), donc le remplissage se teste aussi dans une autre séquence que l'active."""

    caps: tuple[tuple[str, str], ...]
    count: Callable[[Project], int]
    fill: Callable[[Project, int], None]
    big: int
    cross_sequence: bool = True


def _filler_track(project: Project, kind: str) -> Track:
    identifier = "fill-" + kind + "-" + str(project.active_sequence_id)
    for track in project.tracks:
        if track.id == identifier:
            return track
    track = Track(identifier, "Remplissage", kind)
    project.tracks.append(track)
    return track


def _fill_clips(project: Project, n: int) -> None:
    track = _filler_track(project, "video")
    start = len(track.clips)
    for index in range(start, start + n):
        track.clips.append(Clip(f"fc{index}", "m", track.id, 0.0, 0.0, 1.0, label="remplissage"))


def _fill_layers(project: Project, n: int) -> None:
    prefix = "fl" + re.sub(r"\W", "", str(project.active_sequence_id)) + f"{sum(len(t.clips) for t in project.tracks)}x"
    for index in range(n):
        add_graphic_clip(project, "text", timeline_start=0.0, duration=1.0, clip_id=f"{prefix}{index}")


def _fill_sequences(project: Project, n: int) -> None:
    for _ in range(n):
        create_sequence(project, f"Remplissage {len(project.sequences)}", sequence_id=f"fs{len(project.sequences)}")


def _fill_sidechains(project: Project, n: int) -> None:
    chains = project.active_sequence.ducking_sidechains
    for _ in range(n):
        chains.append(DuckingSidechain(f"fd{len(chains)}", f"fm{len(chains)}", f"fv{len(chains)}"))


def _fill_buses(project: Project, n: int) -> None:
    for index in range(n):
        add_bus(project, f"Remplissage {len(project.buses)}", bus_id=f"fb{len(project.buses)}x{index}")


def _fill_trackers(project: Project, n: int) -> None:
    track = _filler_track(project, "video")
    clip = Clip(f"ft{len(track.clips)}", "m", track.id, 0.0, 0.0, 1.0, label="suivi")
    clip.tracking = ClipTracking(trackers=tuple(Tracker(id=f"tr{index}x{clip.id}") for index in range(n)))
    track.clips.append(clip)


def _fill_keyframes(project: Project, n: int) -> None:
    clip = project.tracks[0].clips[0]
    start = len(clip.transform_keyframes)
    clip.transform_keyframes.extend(TransformKeyframe("scale", 0.0005 * (start + index), 1.0) for index in range(n))


def _fx_property() -> str:
    from core.effect_targets import fx_property_id

    return fx_property_id("fx1", "brightness")


def _fill_effect_keyframes(project: Project, n: int) -> None:
    from core.keyframe_editing import add_keyframe

    clip = project.tracks[0].clips[0]
    start = len(_effect_curve(project))
    for index in range(start, start + n):
        add_keyframe(project, clip.id, _fx_property(), 0.0005 * index, 0.1)


def _effect_curve(project: Project):
    from core.animation_targets import get_target

    clip = project.tracks[0].clips[0]
    return get_target(_fx_property(), clip).get_keyframes(clip)


def _fill_points(project: Project, n: int) -> None:
    curve = next(track for track in project.tracks if track.id == "A1").automation
    start = len(curve.points)
    for index in range(start, start + n):
        curve.add_point(0.01 * (index + 1), -3.0)


def _fill_folders(project: Project, n: int) -> None:
    for _ in range(n):
        LibraryOrganization(project).create_folder(f"Dossier {len(project.library_folders)}")


def _fill_tags(project: Project, n: int) -> None:
    for _ in range(n):
        LibraryOrganization(project).create_tag(f"Etiquette {len(project.library_tags)}")


def _fill_angles(project: Project, n: int) -> None:
    for index in range(n):
        add_angle(project, "mc", AngleSpec(asset_id="m", name=f"Remplissage {index}"))


def _count_clips(project: Project) -> int:
    return sum(len(track.clips) for sequence in project.sequences for track in sequence.tracks)


def _count_layers(project: Project) -> int:
    return sum(1 for sequence in project.sequences for track in sequence.tracks for clip in track.clips if clip.graphic is not None)


def _count_trackers(project: Project) -> int:
    return sum(len(clip.tracking.trackers) for sequence in project.sequences for track in sequence.tracks for clip in track.clips
               if clip.tracking is not None)


RESOURCES: dict[str, Resource] = {
    "clips": Resource((("core.scripting.auto_rules", "MAX_CLIPS_IN_PROJECT"),), _count_clips, _fill_clips, 5_001),
    "layers": Resource((("core.scripting.layer_rules", "MAX_LAYERS_IN_PROJECT"),), _count_layers, _fill_layers, 5_001),
    "sequences": Resource((("core.scripting.sequence_rules", "MAX_SEQUENCES_IN_PROJECT"),), lambda p: len(p.sequences), _fill_sequences, 201,
                          cross_sequence=False),
    "sidechains": Resource((("core.scripting.audio_rules", "MAX_SIDECHAINS"),), lambda p: sum(len(s.ducking_sidechains) for s in p.sequences),
                           _fill_sidechains, 257),
    "buses": Resource((("core.audio_buses", "MAX_BUSES"),), lambda p: len(p.buses), _fill_buses, 9, cross_sequence=False),
    "trackers": Resource((("core.scripting.tracking_rules", "MAX_TRACKERS_IN_PROJECT"),), _count_trackers, _fill_trackers, 1_001),
    "keyframes": Resource((("core.scripting.auto_rules", "MAX_KEYFRAMES_PER_CLIP"),), lambda p: len(p.tracks[0].clips[0].transform_keyframes),
                          _fill_keyframes, 2_001, cross_sequence=False),
    "effect_keyframes": Resource((), lambda p: len(_effect_curve(p)), _fill_effect_keyframes, 2_001, cross_sequence=False),
    "points": Resource((("core.scripting.audio_rules", "MAX_AUTOMATION_POINTS"),),
                       lambda p: len(next(t for t in p.tracks if t.id == "A1").automation.points), _fill_points, 1_001, cross_sequence=False),
    "folders": Resource((("core.scripting.library_rules", "MAX_LIBRARY_FOLDERS"),), lambda p: len(p.library_folders), _fill_folders, 1_001,
                        cross_sequence=False),
    "tags": Resource((("core.scripting.library_rules", "MAX_LIBRARY_TAGS"),), lambda p: len(p.library_tags), _fill_tags, 501,
                     cross_sequence=False),
    "angles": Resource((("core.scripting.verbs", "MAX_ANGLES"),), lambda p: len(p.get_sequence("mc").multicam.angles), _fill_angles, 65,
                       cross_sequence=False),
}


# --- Mise en situation -----------------------------------------------------------------------------------------------------

REFUSALS = (ValueError, KeyError, OSError)
"""Ce que la session rend comme « refus » (elle restaure le projet) ; toute autre erreur est un bogue et se propage."""


@dataclass(frozen=True)
class Spec:
    """Un verbe créateur et UNE ressource qui doit le borner.

    ``args`` : les paramètres tels que ``build`` les reçoit ; ``adds`` : combien d'objets de la ressource l'opération crée ;
    ``cap`` : plafond de remplacement ; ``placements`` : où remplir (``active`` : la séquence éditée ; ``other`` : une AUTRE — ``Project.tracks``
    ne montre que l'active) ; ``prepare`` : complète le projet de départ ; ``context`` : le verbe lit un fichier (``p.png`` existe)."""

    resource: str
    args: Mapping[str, Any]
    adds: int = 1
    cap: int = CAP
    placements: tuple[str, ...] = ("active", "other")
    prepare: Callable[[Project], None] | None = None
    context: bool = False


def case(spec: Spec, placement: str, *, total: int) -> Project:
    """Le projet de départ rempli pour que la ressource en compte exactement ``total`` (toutes séquences), les ajouts dans ``placement``."""
    project = base_project()
    if spec.prepare is not None:
        spec.prepare(project)
    resource = RESOURCES[spec.resource]
    missing = total - resource.count(project)
    assert missing >= 0, f"le projet de départ a déjà plus que {total} objets ({resource.count(project)}) : relevez `cap`"
    if placement == "other" and missing:
        main = project.active_sequence_id
        create_sequence(project, "Autre", sequence_id="autre")
        project.active_sequence_id = "autre"
        resource.fill(project, missing)
        project.active_sequence_id = main
    elif missing:
        resource.fill(project, missing)
    assert resource.count(project) == total
    return project


def attempt(verb: str, spec: Spec, project: Project, context: Any = None) -> tuple[Exception | None, bool]:
    """Applique le verbe DIRECTEMENT (sans session) : ``(refus ou None, projet strictement inchangé)``."""
    from copy import deepcopy

    build = VERBS[verb].build
    function = build(dict(spec.args), context) if spec.context else build(dict(spec.args))
    before = deepcopy(project)
    try:
        function(project)
    except REFUSALS as error:
        return error, project == before
    return None, False


def make_context(directory: Path) -> Any:
    """Un dossier de script avec ``p.png`` (une vraie petite image) : de quoi lancer un verbe qui lit un fichier."""
    from PySide6.QtGui import QImage

    from core.scripting.verbs import ScriptContext

    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(0xFF336699)
    assert image.save(str(directory / "p.png"), "PNG")
    from tests.scene3d_render_samples import write_static_scene

    write_static_scene(directory)                                   # ``static.kut3d`` : une scène 3D valide, pour add_scene3d_clip
    return ScriptContext(base_dir=directory)


# --- Fixtures de départ propres à un verbe ---------------------------------------------------------------------------------


def _subtitle_track(project: Project) -> None:
    project.tracks.append(Track("S1", "S1", "subtitle"))


def _layer(project: Project) -> None:
    add_graphic_clip(project, "text", timeline_start=0.0, duration=3.0, clip_id="g1")


def _beat_grid(project: Project) -> None:
    project.active_sequence.beat_grid = BeatGrid(120.0, 0.0, 4)


def _two_shots(project: Project) -> None:
    project.tracks[0].clips.append(Clip("c2", "m", "V1", 4.0, 0.0, 4.0, label="c2"))


def _voice_and_music(project: Project) -> None:
    project.tracks.append(Track("A2", "A2", "audio"))
    project.tracks[1].audio_role = "music"
    project.tracks[3].audio_role = "voice"


def _two_audio(project: Project) -> None:
    project.tracks.append(Track("A2", "A2", "audio"))


def _source(project: Project) -> None:
    create_multicam_source(project, [AngleSpec(asset_id="m", name="A"), AngleSpec(asset_id="a", name="B")], sequence_id="mc")
    project.active_sequence_id = MAIN_SEQUENCE_ID


def _effect(project: Project) -> None:
    VERBS["add_effect"].build({"clip": "c1", "type": "color_correction", "id": "fx1"})(project)


def _nestable(project: Project) -> None:
    inserted = create_sequence(project, "Ins", sequence_id="ins")
    inserted.tracks.append(Track("iv", "iv", "video"))
    inserted.tracks[-1].clips.append(Clip("ic", "m", "iv", 0.0, 0.0, 2.0, label="ic"))
    project.tracks.append(Track("V2", "V2", "video"))


_SEQ = {"sequence": MAIN_SEQUENCE_ID}
_ROW = [{"rank": "1", "name": "A", "value": "1"}]

CEILINGS: dict[str, tuple[Spec, ...]] = {
    # --- Calques graphiques (layer_rules.MAX_LAYERS_IN_PROJECT) ---
    "add_graphic": (Spec("layers", {"graphic": "text", "at": 0.0, "duration": 2.0}),),
    "add_scene3d_clip": (Spec("layers", {"path": "static.kut3d", "at": 0.0, "duration": 2.0}, context=True),),
    "add_image_layer": (Spec("layers", {"path": "p.png", "at": 0.0, "duration": 2.0}, context=True),),
    "duplicate_layers": (Spec("layers", {"clips": ["g1"]}, prepare=_layer),),
    "group_layers": (Spec("layers", {"clips": ["g1"]}, prepare=_layer),),
    "apply_layer_preset": (Spec("layers", {"preset": "Lower third", "at": 0.0}, adds=4),),
    "cut_clip": (Spec("layers", {"clip": "g1", "at": 1.0}, prepare=_layer),),
    "duplicate_clip": (Spec("layers", {"clip": "g1"}, prepare=_layer),),
    # --- Clips du projet (auto_rules.MAX_CLIPS_IN_PROJECT) ---
    "create_composition": (Spec("clips", {"track": "V1", "at": 10.0, "duration": 2.0}),),
    "add_flash": (Spec("clips", {"at": 1.0}),),
    "add_leaderboard": (Spec("clips", {"rows": _ROW, "start": 0.0, "duration": 3.0}, adds=5, cap=20),),
    "cut_clips_on_beats": (Spec("clips", {"clips": ["c1"], "every": 4}, prepare=_beat_grid),),
    "cut_clip_at_scenes": (Spec("clips", {"clip": "c1", "times": [2.0]}),),
    "add_subtitle_lines": (Spec("clips", {"lines": [{"start": 0.0, "end": 1.0, "text": "x"}]}, prepare=_subtitle_track),),
    "add_karaoke_lines": (Spec("clips", {"lines": [{"start": 0.0, "end": 1.0, "text": "x y", "word_starts": [0.0, 0.5]}]},
                               prepare=_subtitle_track),),
    "apply_impact_on_cuts": (Spec("clips", {"track": "V1", "zoom": 0.1, "flash": True}, prepare=_two_shots, placements=("other",)),),
    "add_photo_slideshow": (Spec("clips", {"photos": ["p.png"], "start": 20.0}, context=True),),
    # --- Séquences (sequence_rules) : le plafond de séquences, puis celui de clips que copient certains chemins ---
    "create_sequence": (Spec("sequences", {}),),
    "duplicate_sequence": (Spec("sequences", dict(_SEQ)), Spec("clips", dict(_SEQ), adds=2, placements=("other",))),
    "nest_clips": (Spec("sequences", {"clips": ["c1"]}),),       # clips : voir DEBT_SPECS (nest_clips:clips)
    "create_format_version": (Spec("sequences", {**_SEQ, "format": "vertical"}),
                              Spec("clips", {**_SEQ, "format": "vertical"}, adds=2, placements=("other",))),
    "create_multicam_source": (Spec("sequences", {"angles": [{"asset": "m"}, {"asset": "a"}]}),
                               Spec("clips", {"angles": [{"asset": "m"}, {"asset": "a"}]}, adds=2)),
    "create_multicam_from_clips": (Spec("sequences", {"clips": [{"clip": "c1"}, {"clip": "a1"}]}),
                                   Spec("clips", {"clips": [{"clip": "c1"}, {"clip": "a1"}]})),
    "add_multicam_angle": (Spec("angles", {"sequence": "mc", "asset": "a", "name": "Nouveau"}, prepare=_source, placements=("active",)),),
    # --- Audio ---
    "add_bus": (Spec("buses", {}),),
    "add_ducking_sidechain": (Spec("sidechains", {"music": "A1", "voice": "A2"}, prepare=_two_audio),),
    "duck_music_under_voice": (Spec("sidechains", {}, prepare=_voice_and_music),),
    "add_automation_point": (Spec("points", {"track": "A1", "at": 3.9, "gain_db": -6.0}),),
    # --- Suivi ---
    "add_tracker": (Spec("trackers", {"clip": "c1", "at": 1.0}),),
    # --- Bibliothèque ---
    "create_library_folder": (Spec("folders", {"name": "Nouveau"}),),
    "create_library_tag": (Spec("tags", {"name": "Nouvelle"}),),
    # --- Images clés d'un clip (auto_rules.MAX_KEYFRAMES_PER_CLIP) ---
    "apply_camera_shake": (Spec("keyframes", {"clip": "c1", "length": 1.0}, adds=48, cap=60, placements=("active",)),),
}
"""Verbes créateurs dont le plafond est PROUVÉ : un projet déjà au plafond (constante abaissée à ``cap``) est refusé sans changement, avec les
objets déjà là dans la séquence active comme dans une autre. Un verbe peut avoir plusieurs ressources (une entrée par ressource)."""

_LAYER_IN_CLIPS = "un calque est un clip et les deux plafonds valent la même chose (test_layer_and_clip_ceilings_are_equal) : le plafond de clips est atteint au plus tard en même temps que celui de calques."

EXCLUDED_RESOURCES: dict[tuple[str, str], str] = {
    **{(verb, "clips"): _LAYER_IN_CLIPS for verb in (
        "add_graphic", "add_image_layer", "add_scene3d_clip", "duplicate_layers", "group_layers", "apply_layer_preset", "cut_clip", "duplicate_clip")},
    **{(verb, "layers"): _LAYER_IN_CLIPS for verb in (
        "add_flash", "add_karaoke_lines", "add_leaderboard", "add_photo_slideshow", "apply_impact_on_cuts")},
}
"""``(verbe, ressource)`` dont le compte augmente sans spec propre, avec la raison écrite : le test `test_every_resource_a_verb_consumes_has_a_spec`
mesure les comptes avant / après l'opération et exige une spec (ou une dette, ou une exclusion ici) pour chaque ressource qui augmente."""

BOUNDED: dict[str, str] = {
    "convert_to_composition": "remplace les clips cités (au moins un) par un seul clip : le nombre de clips ne peut pas croître (vérifié : 3 clips -> 2, 2 -> 2).",
    "set_cover_marker": "remplace le repère de couverture existant : au plus un (vérifié : trois appels, un seul repère « cover »).",
    "tag_media": "au plus une affectation par couple (média, étiquette) : trois appels identiques, une seule affectation (vérifié) ; les étiquettes sont plafonnées.",
    "fill_slot": "remplit un emplacement de modèle existant (le clip change de média, rien n'est ajouté ; lu dans core/template_slots.fill_slot).",
    "fill_slot_with_photo": "idem : un emplacement existant change de média, l'ancienne photo devenue inutilisée est retirée ; au plus un média par emplacement.",
    "fill_slots_with_photos": "idem, emplacement par emplacement : le verbe refuse les photos sans emplacement libre ; au plus un média par emplacement.",
    "set_automation_curve": "remplace toute la courbe : le verbe refuse plus de MAX_AUTOMATION_POINTS points (test dédié : test_set_automation_curve_is_capped).",
}
"""Verbes créateurs qui ne peuvent pas faire croître une ressource sans limite : ``{verbe: raison}`` (une raison qui dit comment on l'a vérifié)."""

DEBT_SPECS: dict[str, Spec] = {
    "add_effect_keyframe:effect_keyframes": Spec(
        "effect_keyframes", {"clip": "c1", "effect": "fx1", "parameter": "brightness", "at": 3.9, "value": 0.3}, prepare=_effect,
        placements=("active",)),
    "nest_clips:clips": Spec("clips", {"clips": ["c1"]}, placements=("active",)),
    "add_multicam_angle:clips": Spec("clips", {"sequence": "mc", "asset": "a", "name": "Nouveau"}, prepare=_source, placements=("active",)),
    "add_clip:clips": Spec("clips", {"track": "V1", "asset": "m", "at": 5.0}, placements=("active",)),
    "add_subtitle:clips": Spec("clips", {"text": "x", "at": 5.0, "duration": 1.0}, prepare=_subtitle_track, placements=("active",)),
    "cut_clip:clips": Spec("clips", {"clip": "c1", "at": 2.0}, placements=("active",)),
    "duplicate_clip:clips": Spec("clips", {"clip": "c1"}, placements=("active",)),
    "insert_sequence_clip:clips": Spec("clips", {"sequence": "ins", "track": "V2", "at": 0.0}, prepare=_nestable, placements=("active",)),
    "add_keyframe:keyframes": Spec("keyframes", {"clip": "c1", "property": "scale", "at": 3.9, "value": 1.2}, placements=("active",)),
}
"""Pour une dette que l'on sait sonder : de quoi prouver QU'ELLE EXISTE ENCORE (le verbe n'est pas refusé au-delà de ``big`` objets). Le test
échoue dès que le verbe se met à refuser : la dette est alors payée, il faut l'en retirer et inscrire le verbe dans ``CEILINGS``."""
