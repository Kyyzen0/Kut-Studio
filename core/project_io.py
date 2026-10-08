"""Sauvegarde et chargement des projets Kut-Studio au format ``.kut``.

Le format est un fichier JSON UTF-8 lisible, versionné, indépendant de
tout framework graphique. Seule la bibliothèque standard Python est
utilisée : aucune dépendance PySide6, aucun pickle.

Structure du fichier (version 4) :

    {
        "format": "kut-studio-project",
        "version": 4,
        "project": {
            "name": "...",
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "media_assets": [ ... ],
            "tracks": [ ... ]
        }
    }

La version 5 ajoute les marqueurs de timeline et les états de piste
``solo``, ``armed``, ``height_mode`` et ``collapsed``. La version 6
ajoute le mixage audio non destructif : ``volume_db`` / ``pan`` sur les
pistes, ``gain_db`` / ``pan`` / ``fade_in`` / ``fade_out`` sur les clips.
La version 7 ajoute le remappage temporel (``speed``, ``reverse``,
``freeze_mode``...). La version 9 ajoute les effets visuels non
destructifs portés par chaque clip vidéo (``effects``). La version 10
ajoute le style non destructif des sous-titres (``text_style`` par
clip). La version 11 ajoute l'organisation de la bibliothèque :
``library_folders``, ``library_tags`` et ``library_assignments`` (par
média : ``folder_id`` et liste de ``tag_ids``).
La version 12 ajoute les pistes et clips graphiques non destructifs
(``graphic`` : texte, forme, aplat ou image).
La version 14 introduit le **multi-séquence** : le projet porte une liste
``sequences`` (chacune avec ``id``, ``name``, ``width``, ``height``,
``fps``, ``tracks``, ``markers``, ``transitions`` et
``ducking_sidechains``) et ``active_sequence_id``. Un clip imbriqué porte
``sequence_id`` (et un ``asset_id`` vide). Un fichier v1–v13 (timeline
unique) est chargé comme un projet à une séquence principale
(:data:`core.project_model.MAIN_SEQUENCE_ID`), sans perte.
Les versions précédentes restent lisibles : ces champs prennent leurs
valeurs par défaut (liste d'effets vide, style standard pour
``text_style``, organisation de bibliothèque vide). La version 4
avait ajouté ``locked`` / ``visible`` / ``muted``.

Une entrée d'effet invalide ou inconnue est ignorée sans empêcher
l'ouverture du projet : seule l'entrée fautive est écartée, les autres
effets du clip et le reste du projet sont conservés.

L'écriture est atomique : le payload est d'abord écrit dans un fichier
temporaire placé dans le même dossier que la cible, puis déplacé via
``os.replace`` une fois le flush et ``fsync`` réussis. En cas d'erreur
en cours d'écriture, le fichier cible n'est jamais tronqué ni
remplacé par un contenu partiel.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .effects_model import ClipEffect, EffectType
from .animation import Keyframe
from .audio_automation import TrackAutomation
from .beat_grid import beat_grid_from_dict, beat_grid_to_dict
from .canvas_guides import guide_from_dict, guide_to_dict
from .compositing import compositing_from_dict, compositing_to_dict, migrate_legacy_mask_keyframes
from .motion_blur import settings_from_dict as motion_blur_from_dict
from .motion_blur import settings_to_dict as motion_blur_to_dict
from .multicam_model import multicam_from_dict, multicam_to_dict
from .project_model import (
    MAIN_SEQUENCE_ID,
    MAIN_SEQUENCE_NAME,
    Clip,
    Marker,
    MediaAsset,
    Project,
    Sequence,
    Track,
)
from .time_remapping import FlowQuality, FreezeFrameMode, TimeInterpolation, TimeRemapping
from .transitions import Transition, TransitionType
from .visual_effects import (
    ADVANCED_TRANSFORM_PROPERTIES,
    TRANSFORM_PROPERTIES,
    ClipTransform,
    TransformKeyframe,
    migrate_legacy_keyframes,
)

if TYPE_CHECKING:
    from .graphics import GraphicOverlay
    from .library_organization import AssetAssignment
# ``library_organization`` est importé paresseusement dans les helpers
# de sérialisation pour éviter une boucle d'imports (les modèles du
# module ``project_model`` n'en dépendent pas).


# ---------------------------------------------------------------------------
# Constantes du format
# ---------------------------------------------------------------------------

FORMAT_NAME = "kut-studio-project"
"""Identifiant de format écrit à la racine de chaque fichier ``.kut``."""

CURRENT_VERSION = 16
"""Version courante du format public ``.kut``.

La version 16 ajoute le tracking 2D (voir ``docs/tracking.md``) : clé
``tracking`` d'un clip (trackers et leurs données compressées, liaisons
dynamiques reçues, stabilisation). Absente pour un clip sans tracking ; un
fichier v15 ou antérieur s'ouvre donc sans changement.

La version 15 ajoute le moteur motion graphics (voir
``docs/motion-graphics.md``) : transform avancé (ancrage, échelle X/Y,
inclinaison, miroirs), animation générique des clips (``animation``),
calques étendus (formes, groupes, adjustment layers, contrôleurs,
parentage), masques multiples avec opérations, guides et flou de
mouvement des séquences. Un fichier v14 ou antérieur s'ouvre sans
conversion destructive : ses calques gardent leur placement historique
(``layout: legacy``) et les anciennes images-clés de masque passent au
moteur central sans changer leur courbe.

La version 14 ajoute le multi-séquence et les séquences imbriquées (voir
``docs/nested-sequences.md``). Un fichier v13 ou antérieur est lu comme un
projet à séquence unique.

La version 13 ajoute aux images-clés l'interpolation, les pentes Bézier, le
mode des tangentes et un identifiant (voir ``docs/animation.md``). Les
images-clés d'un fichier antérieur sont converties sans changer le rendu
(:func:`core.visual_effects.migrate_legacy_keyframes`).

Le compositing reste un champ optionnel du schéma v12 : son absence produit
l'état neutre, donc il ne justifie pas une rupture de format.
"""

SUPPORTED_VERSIONS: frozenset[int] = frozenset(
    {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16}
)
"""Ensemble des versions que cette version de Kut-Studio sait lire.

Les versions 1 à 8 restent prises en charge ; les champs spécifiques
(transform, keyframes, états de piste, mixage audio, remappage temporel,
effets) y sont comblés par des valeurs par défaut conservatives.
"""

_VERSION_ADDED_AUDIO: int = 6
"""Première version sérialisant le mixage audio non destructif."""

_VERSION_ADDED_TIME_REMAPPING: int = 7
"""Première version sérialisant le remappage temporel (vitesse, reverse, freeze)."""

_VERSION_ADDED_EFFECTS: int = 9
"""Première version sérialisant les effets visuels d'un clip vidéo."""

_VERSION_ADDED_LIBRARY_ORGANIZATION: int = 11
"""Première version sérialisant les dossiers, tags et affectations de la bibliothèque."""

_VERSION_ADDED_SEQUENCES: int = 14
"""Première version sérialisant plusieurs séquences et les clips imbriqués."""

_FORMAT_KEY = "format"
_VERSION_KEY = "version"
_PROJECT_KEY = "project"

_PROJECT_FIELDS = frozenset({"name", "width", "height", "fps"})
_TRACK_FIELDS = frozenset(
    {
        "id",
        "name",
        "type",
        "locked",
        "visible",
        "muted",
        "solo",
        "armed",
        "height_mode",
        "collapsed",
        "volume_db",
        "pan",
        # --- Tâche 28 : automation audio / ducking ---
        "audio_role",
        "automation",
        "ducking_config",
    }
)
_CLIP_AUDIO_FIELDS = frozenset({"gain_db", "pan", "fade_in", "fade_out"})
_CLIP_TIME_REMAPPING_FIELDS = frozenset(
    {"speed", "reverse", "freeze_mode", "freeze_source_time", "freeze_duration"}
)
_CLIP_KNOWN_FIELDS = frozenset(
    {
        "id",
        "asset_id",
        "track_id",
        "timeline_start",
        "source_in",
        "source_out",
        "enabled",
        "label",
        "text",
        "text_style",
        "sequence_id",
    }
) | _CLIP_AUDIO_FIELDS | _CLIP_TIME_REMAPPING_FIELDS


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------


def project_payload(project: Project) -> dict[str, Any]:
    """Photographie sérialisable de ``project``.

    Le dictionnaire est détaché de l'objet vivant : un thread d'écriture
    peut le poser sur disque pendant que l'interface continue d'éditer.
    """
    return _build_payload(project)


def write_project_payload(payload: dict[str, Any], file_path: str) -> None:
    """Écrit un payload déjà construit, de façon atomique."""
    target = Path(file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(payload, target)


def save_project(project: Project, file_path: str) -> None:
    """Sérialise un ``Project`` dans un fichier ``.kut`` JSON UTF-8.

    Le dossier parent est créé si besoin. L'écriture est atomique :
    un échec (disque plein, permissions, JSON non sérialisable...) ne
    laisse ni fichier cible tronqué, ni fichier temporaire résiduel.
    """
    target = Path(file_path)
    _materialize_project_luts(project, target.parent)
    write_project_payload(project_payload(project), file_path)


def _materialize_project_luts(project: Project, project_root: Path) -> None:
    """Rend les LUTs d'un projet portables avant la sauvegarde."""
    from .color_grading import ColorGrade, copy_lut_into_project

    for track in project.all_tracks():
        for clip in track.clips:
            grade = getattr(clip, "color_grade", None)
            if not isinstance(grade, ColorGrade) or grade.lut is None:
                continue
            portable = copy_lut_into_project(grade.lut, project_root)
            clip.color_grade = grade.with_lut(portable)
    portable_presets = []
    for preset in getattr(project, "color_presets", []) or []:
        grade = getattr(preset, "grade", None)
        if isinstance(grade, ColorGrade) and grade.lut is not None:
            grade = grade.with_lut(copy_lut_into_project(grade.lut, project_root))
            from dataclasses import replace

            preset = replace(preset, grade=grade)
        portable_presets.append(preset)
    project.color_presets = portable_presets


def load_project(file_path: str) -> Project:
    """Charge un fichier ``.kut`` et reconstruit le ``Project`` correspondant.

    Lève :
        FileNotFoundError : si le fichier n'existe pas.
        ValueError : si le JSON est invalide, si la racine n'est pas un
            objet, si le couple ``format`` / ``version`` n'est pas
            reconnu, ou si les dataclasses détectent des champs
            manquants ou inconnus, des types incohérents ou des valeurs
            invalides (durée négative, ``source_out <= source_in``...).
    """
    source = Path(file_path)
    try:
        raw = source.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise FileNotFoundError(
            f"Fichier projet Kut-Studio introuvable : {source}"
        ) from error

    try:
        data = json.loads(raw, parse_constant=_reject_non_finite, parse_float=_finite_float)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Le fichier {source} n'est pas un JSON valide ({error.msg})."
        ) from error
    except RecursionError as error:
        raise ValueError(f"Le fichier {source} est trop profondément imbriqué pour être un projet.") from error

    if not isinstance(data, dict):
        raise ValueError(
            f"Le fichier {source} doit contenir un objet JSON à la racine."
        )

    _validate_envelope(data, source)

    project_data = data.get(_PROJECT_KEY)
    if not isinstance(project_data, dict):
        raise ValueError(
            f"Section '{_PROJECT_KEY}' manquante ou invalide dans {source}."
        )

    try:
        return _deserialize_project(project_data, project_root=source.parent)
    except (KeyError, IndexError, AttributeError, ArithmeticError, RecursionError, TypeError) as error:
        # Contrat : un fichier abîmé est refusé par ValueError, jamais par une exception interne
        # (clé manquante ou inconnue, nombre énorme...) que l'interface ne sait pas présenter.
        raise ValueError(
            f"Le fichier {source} est endommagé ({type(error).__name__} : {error})."
        ) from error


def _reject_non_finite(name: str) -> float:
    """``NaN`` / ``Infinity`` : JSON ne les définit pas, Python les accepte. Un projet n'en contient jamais."""
    raise ValueError(f"valeur non finie « {name} » : un projet ne peut contenir ni NaN ni infini")


def _finite_float(text: str) -> float:
    """Réel JSON fini ; « 1e999 » est lu comme l'infini par Python."""
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"nombre hors limites « {text} » : un projet ne peut contenir ni NaN ni infini")
    return value


# ---------------------------------------------------------------------------
# Sérialisation
# ---------------------------------------------------------------------------


def _asset_to_dict(asset: MediaAsset) -> dict[str, Any]:
    """Forme écrite d'un média.

    Les métadonnées optionnelles lues par la sonde (``timecode``, ``timecode_fps``, ``time_reference``, ``reel``,
    ``camera``, ``creation_time``) ne sont écrites que si elles ne sont pas à leur défaut : un projet sans timecode
    produit exactement les mêmes octets qu'avant, et la version du format ne change pas (clés optionnelles, comme les
    ajouts précédents). À la lecture, ``MediaAsset.__post_init__`` ramène une valeur illisible à son défaut. Un lecteur
    *plus ancien* refuserait un fichier qui porte ces clés (``MediaAsset(**item)`` est strict sur les clés inconnues) :
    c'est accepté, on n'ouvre pas un fichier plus récent avec une version plus ancienne.
    """
    data: dict[str, Any] = {
        "id": asset.id,
        "path": asset.path,
        "name": asset.name,
        "duration": asset.duration,
        "width": asset.width,
        "height": asset.height,
        "fps": asset.fps,
        "media_type": asset.media_type,
        "has_audio": asset.has_audio,
    }
    if asset.timecode:
        data["timecode"] = asset.timecode
    if asset.timecode_fps:
        data["timecode_fps"] = asset.timecode_fps
    if asset.time_reference is not None:
        data["time_reference"] = asset.time_reference
    if asset.reel:
        data["reel"] = asset.reel
    if asset.camera:
        data["camera"] = asset.camera
    if asset.creation_time:
        data["creation_time"] = asset.creation_time
    return data


def _build_payload(project: Project) -> dict[str, Any]:
    """Construit la structure JSON-sérialisable représentant un projet."""
    return {
        _FORMAT_KEY: FORMAT_NAME,
        _VERSION_KEY: CURRENT_VERSION,
        _PROJECT_KEY: {
            "name": project.name,
            "media_assets": [_asset_to_dict(asset) for asset in project.media_assets],
            # --- Organisation de la bibliothèque (tâche 25, v11) ---
            # On sérialise uniquement les champs utiles à la
            # reconstruction. Les listes vides restent sérialisées
            # (``[]``) pour rendre explicite l'intention « pas de
            # dossier / tag ». Les affectations manquantes sont
            # implicitement à la racine (``folder_id=None``).
            "library_folders": [
                _folder_to_dict(folder) for folder in project.library_folders
            ],
            "library_tags": [
                _tag_to_dict(tag) for tag in project.library_tags
            ],
            "library_assignments": [
                _assignment_to_dict(assignment)
                for assignment in project.library_assignments.values()
            ],
            "color_presets": [
                _color_preset_to_dict(preset)
                for preset in getattr(project, "color_presets", []) or []
            ],
            # --- Multi-séquence (v14) ---
            "active_sequence_id": project.active_sequence_id,
            "sequences": [_sequence_to_dict(sequence) for sequence in project.sequences],
        },
    }


def _clip_to_dict(clip: Clip) -> dict[str, Any]:
    """Sérialise un clip (partagé par les projets, le presse-papiers et les presets)."""
    return {
        "id": clip.id,
        "asset_id": clip.asset_id,
        "track_id": clip.track_id,
        "timeline_start": clip.timeline_start,
        "source_in": clip.source_in,
        "source_out": clip.source_out,
        "enabled": clip.enabled,
        "label": clip.label,
        "text": clip.text,
        "gain_db": float(clip.gain_db),
        "pan": float(clip.pan),
        "fade_in": float(clip.fade_in),
        "fade_out": float(clip.fade_out),
        "transform": _transform_to_dict(clip.transform),
        "transform_keyframes": [
            _keyframe_to_dict(kf) for kf in clip.transform_keyframes
        ],
        # Animation générique (v15) : forme, texte, masques.
        "animation": [
            _keyframe_to_dict(kf) for kf in getattr(clip, "animation", ()) or ()
        ],
        "time_remapping": _time_remapping_to_dict(clip.time_remapping),
        "effects": [
            _effect_to_dict(effect) for effect in clip.effects
        ],
        # Effets audio non destructifs (tâche 27).
        # Sérialisés comme liste vide pour les
        # projets anciens ou les clips sans effets
        # audio. La désérialisation reste stricte
        # : un effet invalide est ignoré à l'unité,
        # sans casser le chargement du clip.
        "audio_effects": [
            _audio_effect_to_dict(effect)
            for effect in clip.audio_effects
        ],
        # Étalonnage couleur non destructif (tâche 29).
        # ``None`` si l'identité ou si le clip n'a
        # jamais été étalonné : on évite de
        # polluer le ``.kut`` avec une copie
        # identique au défaut. Les versions
        # antérieures à v11.2 ne portent pas
        # cette clé, ce qui donne ``None`` après
        # chargement (rendu sans filtre couleur).
        "color_grade": _color_grade_to_dict(
            getattr(clip, "color_grade", None)
        ),
        "graphic": _graphic_to_dict(
            getattr(clip, "graphic", None)
        ),
        "compositing": compositing_to_dict(clip.compositing),
        "text_style": clip.text_style.to_dict(),
        # Clip imbriqué (v14) : clé présente uniquement
        # quand le clip référence une séquence.
        **({"sequence_id": clip.sequence_id} if clip.sequence_id else {}),
        # Multicam : angle choisi par un segment de source Multicam (absent : le premier angle).
        **({"angle_id": clip.angle_id} if clip.angle_id else {}),
        # Template : emplacement de média (absent : clip ordinaire).
        **({"template_slot": clip.template_slot} if clip.template_slot else {}),
        # Tracking (v16) : présent seulement si le clip en porte.
        **_tracking_entry(getattr(clip, "tracking", None)),
    }


def _tracking_entry(tracking) -> dict[str, Any]:
    if tracking is None or getattr(tracking, "is_empty", True):
        return {}
    return {"tracking": tracking.to_dict()}


def _generated_groups_from(raw: object) -> dict:
    """Groupes générés relus : une entrée abîmée est ignorée, les autres sont gardées."""
    if not isinstance(raw, dict):
        return {}
    groups = {}
    for group_id, group in raw.items():
        if not (isinstance(group_id, str) and isinstance(group, dict) and isinstance(group.get("kind"), str)
                and isinstance(group.get("data"), dict) and isinstance(group.get("clips"), list)):
            continue
        groups[group_id] = {"kind": group["kind"], "data": group["data"],
                            "clips": [clip_id for clip_id in group["clips"] if isinstance(clip_id, str)]}
    return groups


def _sequence_to_dict(sequence: Sequence) -> dict[str, Any]:
    """Sérialise une séquence : réglages, repères, transitions, pistes."""
    return {
        "id": sequence.id,
        "name": sequence.name,
        "width": sequence.width,
        "height": sequence.height,
        "fps": sequence.fps,
        # Motion graphics (v15) : guides du viewer et flou de mouvement.
        "guides": [guide_to_dict(guide) for guide in getattr(sequence, "guides", ()) or ()],
        "motion_blur": motion_blur_to_dict(getattr(sequence, "motion_blur", None)),
        # Multicam : présent seulement pour une source Multicam (angles, politique audio, méthode de synchro).
        **({"multicam": multicam_to_dict(sequence.multicam)} if sequence.multicam is not None else {}),
        # Vidéo sociale : présent seulement si la séquence a une grille rythmique.
        **({"beat_grid": beat_grid_to_dict(sequence.beat_grid)} if getattr(sequence, "beat_grid", None) else {}),
        # Calques générés depuis des données (classement) : présent seulement s'il y en a.
        **({"generated_groups": sequence.generated_groups} if getattr(sequence, "generated_groups", None) else {}),
        "markers": [
            {
                "id": marker.id,
                "time_seconds": marker.time_seconds,
                "name": marker.name,
                "category": marker.category,
            }
            for marker in sequence.markers
        ],
        "transitions": [
            {
                "id": transition.id,
                "from_clip_id": transition.from_clip_id,
                "to_clip_id": transition.to_clip_id,
                "type": transition.type.value,
                "duration": float(transition.duration),
            }
            for transition in sequence.transitions
        ],
        # --- Ducking automatique (tâche 28) ---
        # Liste des associations musique ← voix de la séquence.
        # Une liste vide signifie : aucun ducking automatique.
        "ducking_sidechains": [
            _ducking_sidechain_to_dict(sidechain)
            for sidechain in getattr(sequence, "ducking_sidechains", []) or []
        ],
        "tracks": [
            {
                "id": track.id,
                "name": track.name,
                "type": track.type,
                "locked": track.locked,
                "visible": track.visible,
                "muted": track.muted,
                "solo": track.solo,
                "armed": track.armed,
                "height_mode": track.height_mode,
                "collapsed": track.collapsed,
                "volume_db": float(track.volume_db),
                "pan": float(track.pan),
                # --- Automation audio et ducking (tâche 28) ---
                # ``audio_role`` reste une chaîne pour rester
                # compatible avec les snapshots plus anciens.
                "audio_role": getattr(track, "audio_role", "other"),
                # ``automation`` (une ``TrackAutomation``) est sérialisée
                # comme une liste de points ``{time_seconds, gain_db,
                # fade_seconds}``. Une liste vide correspond à ``pas
                # d'automation`` et reste le comportement par défaut.
                "automation": [
                    {
                        "time_seconds": float(point.time_seconds),
                        "gain_db": float(point.gain_db),
                        "fade_seconds": float(point.fade_seconds),
                    }
                    for point in track.automation.points
                ],
                # ``ducking_config`` est sérialisé comme un dict
                # ``{threshold_db, reduction_db, attack_seconds,
                # release_seconds}`` ou ``None``.
                "ducking_config": _ducking_config_to_dict(
                    getattr(track, "ducking_config", None)
                ),
                "clips": [_clip_to_dict(clip) for clip in track.clips],
            }
            for track in sequence.tracks
        ],
    }


# ---------------------------------------------------------------------------
# Désérialisation
# ---------------------------------------------------------------------------


def _validate_envelope(data: dict[str, Any], source: Path) -> None:
    """Vérifie que ``format`` et ``version`` correspondent au format attendu."""
    fmt = data.get(_FORMAT_KEY)
    if fmt != FORMAT_NAME:
        raise ValueError(
            f"Format de fichier Kut-Studio invalide dans {source} : "
            f"attendu '{FORMAT_NAME}', reçu '{fmt}'."
        )
    version = data.get(_VERSION_KEY)
    if version not in SUPPORTED_VERSIONS:
        raise ValueError(
            f"Version de fichier Kut-Studio non supportée dans {source} : "
            f"{version!r}. Versions acceptées : {sorted(SUPPORTED_VERSIONS)}."
        )


def _deserialize_project(
    data: dict[str, Any], *, project_root: Path | None = None,
) -> Project:
    """Reconstruit un ``Project`` à partir de la section ``project`` du JSON.

    Le chargement reste strict pour les clips, pistes, assets et
    marqueurs : une entrée structurellement invalide fait échouer
    l'ouverture (erreur interceptée par l'interface, projet courant
    conservé) plutôt que de perdre silencieusement du contenu. Seuls
    les sous-objets décoratifs (transitions, effets) sont ignorés à
    l'unité, sans bloquer le fichier.

    L'organisation de la bibliothèque (dossiers, tags, affectations)
    ajoutée en v11 est reconstruite après la création du ``Project`` :
    un fichier plus ancien ne porte simplement pas ces clés et on
    retombe sur des collections vides (rétrocompatibilité stricte).
    """
    assets: list[MediaAsset] = []
    raw_assets = data.get("media_assets", [])
    if raw_assets is None:
        raw_assets = []
    if not isinstance(raw_assets, list):
        raise ValueError("Liste de médias invalide : tableau JSON attendu.")
    for item in raw_assets:
        if not isinstance(item, dict):
            raise ValueError("Média invalide : objet JSON attendu.")
        # Les métadonnées optionnelles (timecode, bobine, caméra…) absentes prennent leur défaut ; illisibles, elles y
        # sont ramenées par ``MediaAsset.__post_init__`` : jamais d'échec d'ouverture à cause d'elles.
        assets.append(MediaAsset(**item))

    raw_sequences = data.get("sequences")
    if raw_sequences is None:
        # Fichier v1–v13 : une seule timeline, portée par le projet. Elle
        # devient la séquence principale, avec un identifiant stable.
        legacy = {
            key: data[key]
            for key in ("width", "height", "fps", "tracks", "markers", "transitions",
                        "ducking_sidechains")
            if key in data
        }
        sequences = [
            _deserialize_sequence(
                {"id": MAIN_SEQUENCE_ID, "name": MAIN_SEQUENCE_NAME, **legacy},
                project_root=project_root,
            )
        ]
    else:
        if not isinstance(raw_sequences, list):
            raise ValueError("Liste de séquences invalide : tableau JSON attendu.")
        sequences = []
        seen: set[str] = set()
        for item in raw_sequences:
            sequence = _deserialize_sequence(item, project_root=project_root)
            if sequence.id in seen:
                # Identifiant dupliqué (fichier retouché) : la copie reçoit
                # un nouvel identifiant ; les références visent la première.
                from .sequences import new_sequence_id

                sequence.id = new_sequence_id()
            seen.add(sequence.id)
            sequences.append(sequence)
        if not sequences:
            sequences = [Sequence(id=MAIN_SEQUENCE_ID, name=MAIN_SEQUENCE_NAME)]
    active_id = data.get("active_sequence_id")
    project = Project(
        media_assets=assets,
        sequences=sequences,
        active_sequence_id=str(active_id) if active_id is not None else None,
        **{key: value for key, value in data.items() if key == "name"},
    )
    # Organisation de la bibliothèque (v11+). Une version antérieure
    # ne porte simplement pas ces clés : on conserve des collections
    # vides, ce qui correspond au comportement historique où tout
    # était implicitement à la racine sans tag.
    project.library_folders = _deserialize_library_folders(
        data.get("library_folders", [])
    )
    project.library_tags = _deserialize_library_tags(
        data.get("library_tags", [])
    )
    project.library_assignments = _deserialize_library_assignments(
        data.get("library_assignments", []),
        valid_asset_ids={asset.id for asset in project.media_assets},
        valid_folder_ids={folder.id for folder in project.library_folders},
        valid_tag_ids={tag.id for tag in project.library_tags},
    )
    project.color_presets = _deserialize_color_presets(
        data.get("color_presets", []), project_root=project_root
    )
    return project


def _deserialize_sequence(
    data: Any, *, project_root: Path | None = None,
) -> Sequence:
    """Reconstruit une :class:`Sequence` (pistes, repères, transitions, ducking).

    Même rigueur que le reste du chargement : une piste ou un clip
    structurellement invalide fait échouer l'ouverture ; transitions et
    associations de ducking invalides sont ignorées à l'unité.
    """
    if not isinstance(data, dict):
        raise ValueError("Séquence invalide : objet JSON attendu.")
    sequence_id = str(data.get("id") or "").strip()
    if not sequence_id:
        from .sequences import new_sequence_id

        sequence_id = new_sequence_id()
    tracks: list[Track] = []
    raw_tracks = data.get("tracks", [])
    if raw_tracks is None:
        raw_tracks = []
    if not isinstance(raw_tracks, list):
        raise ValueError("Liste de pistes invalide : tableau JSON attendu.")
    for item in raw_tracks:
        if not isinstance(item, dict):
            raise ValueError("Piste invalide : objet JSON attendu.")
        tracks.append(_deserialize_track(item, project_root=project_root))
    markers: list[Marker] = []
    raw_markers = data.get("markers", [])
    if raw_markers is None:
        raw_markers = []
    if not isinstance(raw_markers, list):
        raise ValueError("Liste de marqueurs invalide : tableau JSON attendu.")
    for item in raw_markers:
        if not isinstance(item, dict):
            raise ValueError("Marqueur invalide : objet JSON attendu.")
        markers.append(_deserialize_marker(item))
    sequence = Sequence(
        id=sequence_id,
        name=str(data.get("name") or MAIN_SEQUENCE_NAME),
        width=data.get("width", 1920),
        height=data.get("height", 1080),
        fps=data.get("fps", 30.0),
        tracks=tracks,
        markers=markers,
    )
    sequence.transitions = _deserialize_transitions(data.get("transitions", []), sequence)
    # --- Motion graphics (v15) : absent d'un ancien fichier → aucun guide,
    # flou de mouvement par défaut.
    raw_guides = data.get("guides", [])
    sequence.guides = [
        guide for guide in (guide_from_dict(item) for item in (raw_guides if isinstance(raw_guides, list) else []))
        if guide is not None
    ]
    sequence.motion_blur = motion_blur_from_dict(data.get("motion_blur"))
    # --- Multicam : absent d'un ancien fichier → séquence ordinaire ; structure abîmée → ValueError.
    sequence.multicam = multicam_from_dict(data.get("multicam"))
    # --- Grille rythmique : absente d'un ancien fichier (ou abîmée) → pas de grille.
    sequence.beat_grid = beat_grid_from_dict(data.get("beat_grid"))
    sequence.generated_groups = _generated_groups_from(data.get("generated_groups"))
    # --- Ducking automatique (tâche 28) ---
    # Une version antérieure (avant v11.1) ne porte pas cette clé :
    # on retombe sur une liste vide. Les entrées invalides sont
    # silencieusement écartées.
    sequence.ducking_sidechains = _deserialize_ducking_sidechains(
        data.get("ducking_sidechains", []),
        valid_music_ids={t.id for t in tracks if t.type in ("audio", "video")},
        valid_voice_ids={t.id for t in tracks if t.type in ("audio", "video")},
    )
    return sequence


def _deserialize_transitions(raw: Any, project: Project | Sequence) -> list[Transition]:
    """Ignore les entrées de transition invalides sans empêcher l'ouverture."""
    if not isinstance(raw, list):
        return []
    clip_ids = {clip.id for track in project.tracks for clip in track.clips}
    loaded: list[Transition] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            transition = Transition(
                id=str(item["id"]), from_clip_id=str(item["from_clip_id"]),
                to_clip_id=str(item["to_clip_id"]),
                type=TransitionType(item.get("type", "crossfade")),
                duration=float(item.get("duration", 0.5)),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if transition.from_clip_id in clip_ids and transition.to_clip_id in clip_ids:
            loaded.append(transition)
    return loaded


def _deserialize_marker(data: dict[str, Any]) -> Marker:
    """Reconstruit un marqueur. Une catégorie inconnue redevient standard."""
    return Marker(
        id=str(data["id"]),
        time_seconds=float(data.get("time_seconds", 0.0)),
        name=str(data.get("name", "")),
        category=str(data.get("category", "standard")),
    )


@lru_cache(maxsize=256)
def _text_style_from_key(key: tuple):
    from .text_style import TextStyle

    return TextStyle.from_dict(dict(key))


def _shared_text_style(raw):
    """``TextStyle`` partagé entre clips au style identique.

    Presque tous les clips portent le même style (le style standard) :
    le valider et le construire pour chacun des milliers de clips d'un
    montage coûtait ~40 % du chargement. ``TextStyle`` est **immuable**
    (dataclass gelée) : plusieurs clips peuvent donc référencer la même
    instance sans risque d'effet de bord.
    """
    from .text_style import TextStyle

    if isinstance(raw, dict):
        try:
            return _text_style_from_key(tuple(sorted(raw.items())))
        except TypeError:  # valeur non hashable (liste, dict imbriqué) : pas de partage
            pass
    return TextStyle.from_dict(raw)


@lru_cache(maxsize=256)
def _compositing_from_json(text: str):
    return compositing_from_dict(json.loads(text))


def _shared_compositing(raw):
    """``Compositing`` partagé (immuable) entre clips à la composition identique."""
    if isinstance(raw, dict):
        try:
            return _compositing_from_json(json.dumps(raw, sort_keys=True))
        except (TypeError, ValueError):
            pass
    return compositing_from_dict(raw)


def _deserialize_clip(
    raw_clip: Any, track_type: str, *, project_root: Path | None = None,
) -> Clip:
    """Reconstruit un clip ; une structure invalide lève ``ValueError``."""
    if not isinstance(raw_clip, dict):
        raise ValueError("Clip invalide : objet JSON attendu.")
    clip_kwargs = {
        key: value
        for key, value in raw_clip.items()
        if key not in {
            "transform", "transform_keyframes", "time_remapping", "effects",
            "graphic", "compositing", "animation", "tracking", "angle_id", "template_slot",
        }
        and key in _CLIP_KNOWN_FIELDS
    }
    angle_id = raw_clip.get("angle_id", "")
    clip_kwargs["angle_id"] = angle_id if isinstance(angle_id, str) else ""
    slot = raw_clip.get("template_slot", "")
    clip_kwargs["template_slot"] = slot if isinstance(slot, str) else ""
    clip_kwargs["transform"] = _dict_to_transform(
        raw_clip.get("transform")
    )
    keyframes_raw = raw_clip.get("transform_keyframes", [])
    if not isinstance(keyframes_raw, list):
        raise ValueError("Liste d'images-clés invalide : tableau JSON attendu.")
    keyframes = [_dict_to_keyframe(raw) for raw in keyframes_raw]
    if _is_legacy_keyframe_list(keyframes_raw):
        # Ancien moteur : valeur de base jusqu'au premier keyframe. On
        # garde exactement ce rendu (keyframe ``hold`` à 0 si nécessaire).
        keyframes = migrate_legacy_keyframes(clip_kwargs["transform"], keyframes)
    clip_kwargs["transform_keyframes"] = keyframes
    # Gérer le time_remapping (version 7+)
    if "time_remapping" in raw_clip:
        clip_kwargs["time_remapping"] = _dict_to_time_remapping(
            raw_clip.get("time_remapping")
        )
    else:
        # Version antérieure à 7 : utiliser les valeurs par défaut
        clip_kwargs["time_remapping"] = TimeRemapping()
    # Effets (version 9+) : pistes vidéo, et pistes graphiques depuis la
    # v15 (adjustment layers, effets de calque). Une version antérieure
    # n'a pas la clé : la liste reste vide.
    if track_type in ("video", "graphics"):
        clip_kwargs["effects"] = _deserialize_clip_effects(
            raw_clip.get("effects")
        )
    else:
        clip_kwargs["effects"] = []
    # Effets audio (tâche 27, v11+) : acceptés sur les pistes
    # vidéo (qui peuvent porter de l'audio) et audio. Les
    # versions antérieures à la v11 n'ont pas la clé : la liste
    # reste vide, comportement historique préservé.
    if track_type in ("video", "audio"):
        clip_kwargs["audio_effects"] = _deserialize_audio_effects(
            raw_clip.get("audio_effects")
        )
    else:
        clip_kwargs["audio_effects"] = []
    # Étalonnage couleur (tâche 29) : si la clé manque (projet
    # v10‑ ou v11.0/11.1), ``None`` ; les projets neufs portent
    # toujours une clé, potentiellement nulle (identité).
    clip_kwargs["color_grade"] = _deserialize_color_grade(
        raw_clip.get("color_grade"),
        project_root=project_root,
    )
    clip_kwargs["graphic"] = _dict_to_graphic(raw_clip.get("graphic"))
    compositing = _shared_compositing(raw_clip.get("compositing"))
    animation_raw = raw_clip.get("animation", [])
    if not isinstance(animation_raw, list):
        animation_raw = []
    animation = [kf for kf in (_dict_to_generic_keyframe(raw) for raw in animation_raw) if kf is not None]
    # Images-clés de masque du format v12 : converties vers le moteur
    # central, courbe identique (voir ``migrate_legacy_mask_keyframes``).
    compositing, migrated = migrate_legacy_mask_keyframes(compositing)
    clip_kwargs["compositing"] = compositing
    clip_kwargs["animation"] = sorted(
        animation + migrated, key=lambda kf: (kf.property_name, kf.time_seconds)
    )
    # Style texte (tâche 24) : rétrocompatible — un clip sans la
    # clé ``text_style`` reçoit le style standard par défaut, ce
    # qui correspond exactement au rendu historique.
    clip_kwargs["text_style"] = _shared_text_style(raw_clip.get("text_style"))
    # Tracking (v16) : lecture tolérante, un tracker corrompu est ignoré seul.
    if track_type == "video" or raw_clip.get("tracking") is not None:
        from .tracking_model import ClipTracking

        clip_kwargs["tracking"] = ClipTracking.from_dict(raw_clip.get("tracking"))
    return Clip(**clip_kwargs)


def _deserialize_track(
    data: dict[str, Any], *, project_root: Path | None = None,
) -> Track:
    """Reconstruit une ``Track`` (et ses ``Clip``) à partir d'un dict JSON.

    Le chargement reste strict : un clip incomplet ou incohérent fait
    échouer l'ouverture du fichier (erreur interceptée par l'interface,
    projet courant conservé) plutôt que de perdre silencieusement du
    contenu de timeline. Les garde-fous ci-dessous convertissent les
    structures malformées en ``ValueError`` explicites — interceptées
    par l'appelant — au lieu de laisser fuir ``AttributeError``.
    """
    if not isinstance(data, dict):
        raise ValueError("Piste invalide : objet JSON attendu.")
    track_type = str(data.get("type", "video"))
    raw_clips = data.get("clips", [])
    if not isinstance(raw_clips, list):
        raise ValueError("Liste de clips invalide : tableau JSON attendu.")
    clips = []
    for raw_clip in raw_clips:
        clips.append(_deserialize_clip(raw_clip, track_type, project_root=project_root))
    track_kwargs = {
        key: value for key, value in data.items() if key in _TRACK_FIELDS
    }
    # Une version antérieure à 6 ne connaît pas le mixage : on force les
    # valeurs neutres plutôt que de laisser un champ absent وغير défini.
    if "volume_db" not in track_kwargs:
        track_kwargs["volume_db"] = 0.0
    if "pan" not in track_kwargs:
        track_kwargs["pan"] = 0.0
    # Automation audio et ducking (tâche 28). Une version antérieure
    # à v11.1 ne porte pas ces clés : on retombe sur les valeurs
    # neutres (rôle ``other``, automation vide, ducking_config None).
    if "audio_role" not in track_kwargs:
        track_kwargs["audio_role"] = "other"
    elif track_kwargs["audio_role"] not in {"voice", "music", "sfx", "other"}:
        track_kwargs["audio_role"] = "other"
    # Le fichier stocke une liste de points ; en mémoire, la piste porte
    # toujours la forme canonique (``TrackAutomation``), fabriquée ici.
    track_kwargs["automation"] = TrackAutomation(
        track_id=str(track_kwargs.get("id", "")),
        points=_deserialize_automation_points(track_kwargs.get("automation") or []),
    )
    track_kwargs["ducking_config"] = _deserialize_ducking_config(
        track_kwargs.get("ducking_config")
    )
    return Track(clips=clips, **track_kwargs)


def clip_to_dict(clip: Clip) -> dict[str, Any]:
    """Forme JSON publique d'un clip (format ``.kut`` courant)."""
    return _clip_to_dict(clip)


def clip_from_dict(raw: Any, track_type: str) -> Clip:
    """Clip depuis :func:`clip_to_dict` (presse-papiers, presets)."""
    return _deserialize_clip(raw, track_type)


def _transform_to_dict(transform: ClipTransform) -> dict[str, float]:
    data: dict[str, Any] = {
        "position_x": float(transform.position_x),
        "position_y": float(transform.position_y),
        "scale": float(transform.scale),
        "rotation": float(transform.rotation),
        "opacity": float(transform.opacity),
    }
    # Transform avancé (v15) : écrit seulement s'il diffère du neutre, un
    # clip simple garde exactement son ancien JSON.
    for name in ADVANCED_TRANSFORM_PROPERTIES:
        value = getattr(transform, name)
        if value != TRANSFORM_PROPERTIES[name].default:
            data[name] = bool(value) if isinstance(value, bool) else float(value)
    return data


def _graphic_to_dict(graphic: GraphicOverlay | None) -> dict[str, Any] | None:
    from .graphics import graphic_to_dict

    return graphic_to_dict(graphic)


def _dict_to_graphic(raw: object):
    from .graphics import graphic_from_dict

    return graphic_from_dict(raw)


def _dict_to_transform(raw: dict[str, Any] | None) -> ClipTransform:
    """Désérialise un :class:`ClipTransform` (défaut si absent / invalide)."""
    if not isinstance(raw, dict):
        return ClipTransform()
    try:
        advanced: dict[str, Any] = {
            name: (bool(raw[name]) if TRANSFORM_PROPERTIES[name].kind.value == "bool" else float(raw[name]))
            for name in ADVANCED_TRANSFORM_PROPERTIES if name in raw
        }
        return ClipTransform(
            position_x=float(raw.get("position_x", 0.0)),
            position_y=float(raw.get("position_y", 0.0)),
            scale=float(raw.get("scale", 1.0)),
            rotation=float(raw.get("rotation", 0.0)),
            opacity=float(raw.get("opacity", 1.0)),
            **advanced,
        )
    except (TypeError, ValueError):
        return ClipTransform()


def _keyframe_to_dict(keyframe: TransformKeyframe) -> dict[str, Any]:
    data: dict[str, Any] = {
        "property_name": keyframe.property_name,
        "time_seconds": float(keyframe.time_seconds),
        "value": (
            [float(c) for c in keyframe.value]
            if isinstance(keyframe.value, (tuple, list)) else float(keyframe.value)
        ),
        "interpolation": keyframe.interpolation.value,
        "tangent_mode": keyframe.tangent_mode.value,
        "id": keyframe.id,
    }
    # Pentes Bézier : seulement si fixées (``None`` = automatique).
    if keyframe.in_slope is not None:
        data["in_slope"] = keyframe.in_slope
    if keyframe.out_slope is not None:
        data["out_slope"] = keyframe.out_slope
    return data


def _optional_slope(raw: object) -> float | None:
    if raw is None or isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _dict_to_keyframe(raw: dict[str, Any]) -> TransformKeyframe:
    if not isinstance(raw, dict):
        raise ValueError("Image-clé invalide : objet JSON attendu.")
    return TransformKeyframe(
        property_name=str(raw["property_name"]),
        time_seconds=float(raw["time_seconds"]),
        value=float(raw["value"]),
        # Champs v13 : absents d'un ancien fichier → linéaire, pentes automatiques.
        interpolation=raw.get("interpolation", "linear"),
        in_slope=_optional_slope(raw.get("in_slope")),
        out_slope=_optional_slope(raw.get("out_slope")),
        tangent_mode=raw.get("tangent_mode", "linked"),
        id=str(raw.get("id") or ""),
    )


def _dict_to_generic_keyframe(raw: object) -> Keyframe | None:
    """Keyframe de ``Clip.animation`` ; une entrée corrompue est ignorée seule."""
    if not isinstance(raw, dict):
        return None
    try:
        value = raw["value"]
        value = tuple(float(c) for c in value) if isinstance(value, list) else float(value)
        return Keyframe(
            property_name=str(raw["property_name"]),
            time_seconds=float(raw["time_seconds"]),
            value=value,
            interpolation=raw.get("interpolation", "linear"),
            in_slope=_optional_slope(raw.get("in_slope")),
            out_slope=_optional_slope(raw.get("out_slope")),
            tangent_mode=raw.get("tangent_mode", "linked"),
            id=str(raw.get("id") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _is_legacy_keyframe_list(raw_keyframes: list) -> bool:
    """Images-clés écrites par l'ancien moteur (avant le format 13) ?"""
    return bool(raw_keyframes) and all(
        isinstance(raw, dict) and "interpolation" not in raw for raw in raw_keyframes
    )


def _time_remapping_to_dict(time_remapping: TimeRemapping) -> dict[str, Any]:
    """Sérialise un TimeRemapping en dict.

    Les cinq clés historiques sont toujours écrites ; les choix ajoutés par la suite (interpolation des images, qualité du
    flux, hauteur préservée, audio, ancre, durée imposée) ne le sont que s'ils diffèrent du défaut : un clip sans ces
    réglages garde exactement la forme d'avant. La courbe de vitesse n'est pas ici : ce sont des keyframes ``time.speed``
    de ``Clip.animation``.
    """
    data: dict[str, Any] = {
        "speed": float(time_remapping.speed),
        "reverse": bool(time_remapping.reverse),
        "freeze_mode": str(time_remapping.freeze_mode.value),
        "freeze_source_time": float(time_remapping.freeze_source_time),
        "freeze_duration": float(time_remapping.freeze_duration),
    }
    if time_remapping.interpolation is not TimeInterpolation.SAMPLING:
        data["interpolation"] = time_remapping.interpolation.value
    if time_remapping.flow_quality is not FlowQuality.AUTO:
        data["flow_quality"] = time_remapping.flow_quality.value
    if not time_remapping.preserve_pitch:
        data["preserve_pitch"] = False
    if not time_remapping.remap_audio:
        data["remap_audio"] = False
    if time_remapping.anchor is not None:
        data["anchor"] = float(time_remapping.anchor)
    if time_remapping.duration is not None:
        data["duration"] = float(time_remapping.duration)
    return data


def _coerce_enum(enum_type, value: Any, default):
    """Valeur d'énumération, ou ``default`` si elle est inconnue (fichier plus récent, édité à la main)."""
    try:
        return enum_type(value)
    except (ValueError, TypeError):
        return default


def _optional_positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0.0 else None


def _optional_finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _dict_to_time_remapping(raw: dict[str, Any] | None) -> TimeRemapping:
    """Désérialise un TimeRemapping à partir d'un dict.

    Retourne un TimeRemapping par défaut si le dict est None ou invalide. Les choix ajoutés après la version initiale
    sont lus **champ par champ** : une valeur inconnue retombe sur le défaut sûr de ce champ (échantillonnage, qualité
    automatique…) sans perdre la vitesse ni le sens.
    """
    if not isinstance(raw, dict):
        return TimeRemapping()

    try:
        return TimeRemapping(
            speed=float(raw.get("speed", 1.0)),
            reverse=bool(raw.get("reverse", False)),
            freeze_mode=FreezeFrameMode(raw.get("freeze_mode", "none")),
            freeze_source_time=float(raw.get("freeze_source_time", 0.0)),
            freeze_duration=float(raw.get("freeze_duration", 1.0)),
            interpolation=_coerce_enum(TimeInterpolation, raw.get("interpolation", "sampling"), TimeInterpolation.SAMPLING),
            flow_quality=_coerce_enum(FlowQuality, raw.get("flow_quality", "auto"), FlowQuality.AUTO),
            preserve_pitch=bool(raw.get("preserve_pitch", True)),
            remap_audio=bool(raw.get("remap_audio", True)),
            anchor=_optional_finite_float(raw.get("anchor")),
            duration=_optional_positive_float(raw.get("duration")),
        )
    except (ValueError, TypeError, KeyError):
        # Si la désérialisation échoue, retourner les valeurs par défaut
        return TimeRemapping()


def _effect_to_dict(effect: ClipEffect) -> dict[str, Any]:
    """Sérialise un :class:`ClipEffect` en dict JSON."""
    return {
        "id": effect.id,
        "type": effect.type.value,
        "enabled": bool(effect.enabled),
        "params": {name: float(value) for name, value in effect.params.items()},
    }


def _deserialize_clip_effects(raw: Any) -> list[ClipEffect]:
    """Désérialise la liste d'effets d'un clip.

    Une entrée non-dict, un identifiant/type manquant, un type inconnu ou
    des paramètres invalides ne font pas échouer le chargement : seule
    l'entrée fautive est ignorée, les autres sont conservées dans
    l'ordre du fichier. Une version antérieure (clé absente ou ``None``)
    donne une liste vide.
    """
    if not isinstance(raw, list):
        return []
    effects: list[ClipEffect] = []
    seen_ids: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            effect = ClipEffect(
                id=str(item["id"]),
                type=EffectType(item["type"]),
                enabled=bool(item.get("enabled", True)),
                params=item.get("params", {}),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if effect.id in seen_ids:
            # Un identifiant dupliqué rendrait les opérations ambiguës.
            continue
        seen_ids.add(effect.id)
        effects.append(effect)
    return effects


# ---------------------------------------------------------------------------
# Effets audio non destructifs (tâche 27, v11)
# ---------------------------------------------------------------------------


def _audio_effect_to_dict(effect) -> dict[str, Any]:
    """Sérialise un :class:`AudioEffect` en dict JSON."""
    return {
        "id": effect.id,
        "type": effect.type.value,
        "enabled": bool(effect.enabled),
        "params": {name: float(value) for name, value in effect.params.items()},
    }


def _deserialize_audio_effects(raw: Any) -> list:
    """Désérialise la liste d'effets audio d'un clip.

    Une entrée non-dict, un identifiant/type manquant, un type inconnu ou
    des paramètres invalides ne font pas échouer le chargement : seule
    l'entrée fautive est ignorée, les autres sont conservées dans
    l'ordre du fichier. Une version antérieure (clé absente ou
    ``None``) donne une liste vide.
    """
    # Import paresseux pour éviter une dépendance circulaire lors des
    # tests CLI qui n'instancient pas l'audio_effects_model.
    from .audio_effects_model import AudioEffect, AudioEffectType

    if not isinstance(raw, list):
        return []
    effects: list = []
    seen_ids: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            effect = AudioEffect(
                id=str(item["id"]),
                type=AudioEffectType(item["type"]),
                enabled=bool(item.get("enabled", True)),
                params=item.get("params", {}),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if effect.id in seen_ids:
            continue
        seen_ids.add(effect.id)
        effects.append(effect)
    return effects


# ---------------------------------------------------------------------------
# Étalonnage couleur non destructif (tâche 29)
# ---------------------------------------------------------------------------


def _color_grade_to_dict(grade) -> dict[str, Any] | None:
    """Sérialise un :class:`ColorGrade` ou retourne ``None`` si neutre."""
    if grade is None:
        return None
    # Import paresseux pour éviter les cycles d'imports.
    from .color_grading import ColorGrade
    if not isinstance(grade, ColorGrade):
        return None
    return {
        "exposure": float(grade.exposure),
        "contrast": float(grade.contrast),
        "saturation": float(grade.saturation),
        "temperature": float(grade.temperature),
        "hue": float(grade.hue),
        "shadows": float(grade.shadows),
        "highlights": float(grade.highlights),
        "curves": {
            "master": [
                [float(x), float(y)] for x, y in grade.curves.master.points
            ],
            "red": [
                [float(x), float(y)] for x, y in grade.curves.red.points
            ],
            "green": [
                [float(x), float(y)] for x, y in grade.curves.green.points
            ],
            "blue": [
                [float(x), float(y)] for x, y in grade.curves.blue.points
            ],
        },
        "lut": (
            {
                "path": grade.lut.path,
                "title": grade.lut.title,
                "sha1": grade.lut.sha1,
                "size": int(grade.lut.size),
                "missing": bool(grade.lut.missing),
            }
            if grade.lut is not None else None
        ),
        "enabled": bool(grade.enabled),
    }


def _color_preset_to_dict(preset) -> dict[str, Any]:
    return {
        "id": preset.id,
        "name": preset.name,
        "description": preset.description,
        "category": preset.category.value,
        "grade": _color_grade_to_dict(preset.grade),
    }


def _deserialize_color_presets(
    raw: Any, *, project_root: Path | None = None,
) -> list:
    if not isinstance(raw, list):
        return []
    from .color_grading import ColorPresetCategory, make_color_preset

    loaded = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        grade = _deserialize_color_grade(item.get("grade"), project_root=project_root)
        if grade is None:
            continue
        try:
            preset = make_color_preset(
                preset_id=str(item["id"]),
                name=str(item["name"]),
                description=str(item.get("description", "")),
                category=ColorPresetCategory(item.get("category", "vintage")),
                grade=grade,
                builtin=False,
            )
        except (KeyError, TypeError, ValueError):
            continue
        if preset.id in seen:
            continue
        seen.add(preset.id)
        loaded.append(preset)
    return loaded


def _deserialize_color_grade(
    raw: Any, *, project_root: Path | None = None,
):
    """Reconstruit un :class:`ColorGrade` ou retourne ``None``.

    Les entrées invalides (paramètres hors bornes, points de courbe
    invalides, LUT corrompu) sont silencieusement ramenées à
    l'identité : on préfère un clip étalonné neut à un clip qui
    crash au chargement.
    """
    if raw is None:
        return None
    # Import paresseux.
    from .color_grading import (
        ColorCurve,
        ColorCurves,
        ColorGrade,
        ColorGradingError,
        LUTResource,
    )

    if not isinstance(raw, dict):
        return None

    def _curve(points_raw) -> ColorCurve:
        if not isinstance(points_raw, list):
            return ColorCurve.identity()
        pairs: list[tuple[float, float]] = []
        for entry in points_raw:
            if (
                not isinstance(entry, (list, tuple))
                or len(entry) != 2
            ):
                continue
            try:
                pairs.append((float(entry[0]), float(entry[1])))
            except (TypeError, ValueError):
                continue
        if not pairs:
            return ColorCurve.identity()
        try:
            return ColorCurve(points=tuple(pairs))
        except ColorGradingError:
            return ColorCurve.identity()

    curves_raw = raw.get("curves")
    if not isinstance(curves_raw, dict):
        curves = ColorCurves()
    else:
        curves = ColorCurves(
            master=_curve(curves_raw.get("master")),
            red=_curve(curves_raw.get("red")),
            green=_curve(curves_raw.get("green")),
            blue=_curve(curves_raw.get("blue")),
        )

    lut_raw = raw.get("lut")
    lut: LUTResource | None = None
    if isinstance(lut_raw, dict):
        try:
            stored_missing = bool(lut_raw.get("missing", False))
            path = str(lut_raw.get("path", ""))
            # Détection d'un LUT manquant : on relit la valeur
            # ``missing`` du fichier si elle est fausse, puis on
            # vérifie la présence du chemin sur disque. On tente
            # d'abord le chemin tel quel (absolu), puis le chemin
            # résolu relativement au dossier du projet. C'est la
            # combinaison qui couvre la majorité des cas
            # d'utilisation sans coupler le module ``project_io`` à
            # ``LUTResource``.
            computed_missing = stored_missing
            if path and not stored_missing:
                from pathlib import Path

                candidate = Path(path)
                if not candidate.exists():
                    if project_root is not None:
                        candidate = project_root / candidate
                if not candidate.exists():
                    computed_missing = True
            lut = LUTResource(
                path=path,
                title=str(lut_raw.get("title", "") or "LUT"),
                sha1=str(lut_raw.get("sha1", "")),
                size=int(lut_raw.get("size", 0)),
                missing=computed_missing,
                source_path=(
                    str(candidate.resolve())
                    if path and not computed_missing else None
                ),
            )
        except (ColorGradingError, ValueError, TypeError):
            lut = None

    try:
        return ColorGrade(
            exposure=float(raw.get("exposure", 0.0)),
            contrast=float(raw.get("contrast", 0.0)),
            saturation=float(raw.get("saturation", 1.0)),
            temperature=float(raw.get("temperature", 0.0)),
            hue=float(raw.get("hue", 0.0)),
            shadows=float(raw.get("shadows", 0.0)),
            highlights=float(raw.get("highlights", 0.0)),
            curves=curves,
            lut=lut,
            enabled=bool(raw.get("enabled", True)),
        )
    except ColorGradingError:
        return None


# ---------------------------------------------------------------------------
# Organisation de la bibliothèque (tâche 25, v11)
# ---------------------------------------------------------------------------


def _folder_to_dict(folder) -> dict[str, Any]:
    """Sérialise un :class:`LibraryFolder` en dict JSON."""
    return {
        "id": folder.id,
        "name": folder.name,
        "parent_id": folder.parent_id,
        "color": folder.color,
    }


def _tag_to_dict(tag) -> dict[str, Any]:
    """Sérialise un :class:`LibraryTag` en dict JSON."""
    return {
        "id": tag.id,
        "name": tag.name,
        "color": tag.color,
    }


def _assignment_to_dict(assignment) -> dict[str, Any]:
    """Sérialise une :class:`AssetAssignment` en dict JSON."""
    return {
        "asset_id": assignment.asset_id,
        "folder_id": assignment.folder_id,
        "tag_ids": list(assignment.tag_ids),
    }


def _deserialize_library_folders(raw: Any) -> list:
    """Reconstruit les dossiers de bibliothèque, en ignorant les invalides.

    Une entrée invalide (dict manquant, identifiant dupliqué, couleur
    mal formée, cycle) n'est pas traitée comme fatale : on l'ignore
    et on conserve les dossiers sains, comme on le fait déjà pour
    les transitions / effets. Les dossiers invalides qui créeraient
    une boucle sont purgés par construction de l'arbre final.
    """
    from .library_organization import (
        LibraryError,
        LibraryFolder,
    )

    if not isinstance(raw, list):
        return []

    folders: list[LibraryFolder] = []
    seen_ids: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        folder_id = item.get("id")
        name = item.get("name", "")
        parent_id = item.get("parent_id")
        color = item.get("color", "") or ""
        try:
            folder = LibraryFolder(
                id=str(folder_id or ""),
                name=str(name or ""),
                parent_id=(str(parent_id) if parent_id is not None else None),
                color=str(color),
            )
        except LibraryError:
            continue
        if folder.id in seen_ids:
            # Doublon : on saute pour éviter l'ambiguïté.
            continue
        seen_ids.add(folder.id)
        folders.append(folder)

    # Seconde passe : on retire les dossiers dont le parent n'existe
    # pas ou qui créeraient un cycle. Le filtre s'applique tant qu'on
    # trouve une réduction (un dossier racine fantôme peut faire tomber
    # ses descendants).
    parent_map = {f.id: f.parent_id for f in folders}
    changed = True
    while changed:
        changed = False
        kept: list[LibraryFolder] = []
        kept_ids: set[str] = set()
        for folder in folders:
            parent = parent_map.get(folder.id)
            if parent is None:
                kept.append(folder)
                kept_ids.add(folder.id)
                continue
            if parent not in kept_ids:
                # Parent pas encore gardé : on attend la prochaine passe.
                kept.append(folder)
                continue
            # Vérifier qu'on n'introduit pas un cycle en se basant
            # sur les parents *déjà gardés*.
            walker: str | None = parent
            cycle = False
            visited_local: set[str] = set()
            while walker is not None and walker not in visited_local:
                visited_local.add(walker)
                if walker == folder.id:
                    cycle = True
                    break
                walker = parent_map.get(walker)
            if cycle:
                continue
            kept.append(folder)
            kept_ids.add(folder.id)
        if len(kept) != len(folders):
            changed = True
        folders = kept
        parent_map = {f.id: f.parent_id for f in folders}

    # Dernier filet : un parent toujours manquant est rattaché à la
    # racine (organisation logique sans crash). On fait cela *après*
    # la purge pour ne pas recréer de cycle.
    valid_ids = {f.id for f in folders}
    for folder in folders:
        if folder.parent_id is not None and folder.parent_id not in valid_ids:
            folder.parent_id = None

    # Tri déterministe : les dossiers sont stockés tels quels ; c'est
    # :meth:`LibraryOrganization.list_folders` qui se charge du tri
    # à l'affichage.
    return folders


def _deserialize_library_tags(raw: Any) -> list:
    """Reconstruit les tags en ignorant les entrées invalides / doublons."""
    from .library_organization import LibraryError, LibraryTag

    if not isinstance(raw, list):
        return []
    tags: list[LibraryTag] = []
    seen_ids: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        tag_id = item.get("id")
        name = item.get("name", "")
        color = item.get("color") or "#3498db"
        try:
            tag = LibraryTag(
                id=str(tag_id or ""),
                name=str(name or ""),
                color=str(color),
            )
        except LibraryError:
            continue
        if tag.id in seen_ids:
            continue
        seen_ids.add(tag.id)
        tags.append(tag)
    return tags


def _deserialize_library_assignments(
    raw: Any,
    *,
    valid_asset_ids: set[str],
    valid_folder_ids: set[str],
    valid_tag_ids: set[str],
) -> dict[str, "AssetAssignment"]:
    """Reconstruit les affectations média→dossier/tags.

    On *ignore* une affectation dont l'asset_id n'existe pas dans le
    projet (un média a été supprimé entre deux versions, par exemple),
    dont le dossier ou un tag a disparu (purge ci-dessus) ou qui
    n'est pas une liste JSON valide. Les affectations valides sont
    dédoublonnées par ``asset_id`` : la première occurrence gagne.
    """
    from .library_organization import AssetAssignment

    if not isinstance(raw, list):
        return {}
    assignments: dict[str, AssetAssignment] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        asset_id = item.get("asset_id")
        if not asset_id or asset_id not in valid_asset_ids:
            continue
        if asset_id in assignments:
            # Doublon : on garde la première affectation pour rester
            # déterministe.
            continue
        folder_id = item.get("folder_id")
        if folder_id is not None and folder_id not in valid_folder_ids:
            folder_id = None
        raw_tag_ids = item.get("tag_ids", []) or []
        if not isinstance(raw_tag_ids, list):
            raw_tag_ids = []
        # On dédoublonne les tag_ids et on retire les tags invalides,
        # en conservant l'ordre de première apparition.
        seen_tags: set[str] = set()
        tag_ids: list[str] = []
        for tid in raw_tag_ids:
            if not isinstance(tid, str):
                continue
            if tid not in valid_tag_ids:
                continue
            if tid in seen_tags:
                continue
            seen_tags.add(tid)
            tag_ids.append(tid)
        assignments[str(asset_id)] = AssetAssignment(
            asset_id=str(asset_id),
            folder_id=folder_id if folder_id is None else str(folder_id),
            tag_ids=tag_ids,
        )
    return assignments


# ---------------------------------------------------------------------------
# Automation audio et ducking (tâche 28)
# ---------------------------------------------------------------------------


def _ducking_config_to_dict(config) -> dict[str, Any] | None:
    """Sérialise un :class:`DuckingConfig` ou retourne ``None``."""
    if config is None:
        return None
    # On accepte aussi un dict (mode ``raw``) pour les projets qui
    # n'ont pas encore migré : on convertit alors à la volée.
    if isinstance(config, dict):
        return {
            "threshold_db": float(config.get("threshold_db", -20.0)),
            "reduction_db": float(config.get("reduction_db", 12.0)),
            "attack_seconds": float(config.get("attack_seconds", 0.05)),
            "release_seconds": float(config.get("release_seconds", 0.4)),
        }
    return {
        "threshold_db": float(getattr(config, "threshold_db", -20.0)),
        "reduction_db": float(getattr(config, "reduction_db", 12.0)),
        "attack_seconds": float(getattr(config, "attack_seconds", 0.05)),
        "release_seconds": float(getattr(config, "release_seconds", 0.4)),
    }


def _deserialize_ducking_config(raw: Any):
    """Reconstruit un :class:`DuckingConfig` ou retourne ``None``."""
    if raw is None or not isinstance(raw, dict):
        return None
    # Import paresseux pour éviter les cycles d'imports.
    from .audio_automation import (
        AudioAutomationRangeError,
        DuckingConfig,
    )
    try:
        return DuckingConfig(
            threshold_db=float(raw.get("threshold_db", -20.0)),
            reduction_db=float(raw.get("reduction_db", 12.0)),
            attack_seconds=float(raw.get("attack_seconds", 0.05)),
            release_seconds=float(raw.get("release_seconds", 0.4)),
        )
    except (AudioAutomationRangeError, ValueError, TypeError):
        return None


def _ducking_sidechain_to_dict(sidechain) -> dict[str, Any]:
    """Sérialise un :class:`DuckingSidechain`."""
    return {
        "id": sidechain.id,
        "music_track_id": sidechain.music_track_id,
        "voice_track_id": sidechain.voice_track_id,
        "config": _ducking_config_to_dict(sidechain.config),
        "enabled": bool(sidechain.enabled),
    }


def _deserialize_ducking_sidechains(
    raw: Any,
    *,
    valid_music_ids: set[str],
    valid_voice_ids: set[str],
) -> list:
    """Reconstruit la liste des :class:`DuckingSidechain`.

    Les entrées invalides sont silencieusement écartées. Les
    associations qui pointent vers une piste voix / musique
    inconnue sont supprimées : on veut qu'un fichier modifié
    ailleurs ne plante pas à l'ouverture.
    """
    # Import paresseux pour éviter les cycles d'imports.
    from .audio_automation import (
        AudioAutomationError,
        DuckingConfig,
        DuckingSidechain,
    )

    if not isinstance(raw, list):
        return []
    result: list = []
    seen_ids: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        sidechain_id = item.get("id")
        music_id = item.get("music_track_id")
        voice_id = item.get("voice_track_id")
        if (
            not sidechain_id
            or not music_id
            or not voice_id
            or music_id not in valid_music_ids
            or voice_id not in valid_voice_ids
            or music_id == voice_id
        ):
            continue
        if sidechain_id in seen_ids:
            continue
        config_raw = item.get("config")
        if isinstance(config_raw, dict):
            try:
                config = DuckingConfig(
                    threshold_db=float(config_raw.get("threshold_db", -20.0)),
                    reduction_db=float(config_raw.get("reduction_db", 12.0)),
                    attack_seconds=float(config_raw.get("attack_seconds", 0.05)),
                    release_seconds=float(config_raw.get("release_seconds", 0.4)),
                )
            except (AudioAutomationError, ValueError, TypeError):
                config = DuckingConfig()
        else:
            config = DuckingConfig()
        try:
            sidechain = DuckingSidechain(
                id=str(sidechain_id),
                music_track_id=str(music_id),
                voice_track_id=str(voice_id),
                config=config,
                enabled=bool(item.get("enabled", True)),
            )
        except AudioAutomationError:
            continue
        seen_ids.add(sidechain_id)
        result.append(sidechain)
    return result


def _deserialize_automation_points(raw: Any) -> list:
    """Reconstruit la liste des points d'automation d'une piste.

    Les points invalides sont silencieusement écartés ; la liste
    retournée est triée par ``time_seconds`` croissant. Les valeurs
    hors bornes (temps négatif, fade > limite) sont également
    rejetées : on préfère un projet sans point douteux à un projet
    qui crashe au rendu.
    """
    # Import paresseux pour éviter les cycles d'imports.
    from .audio_automation import (
        AudioAutomationError,
        AutomationPoint,
    )

    if not isinstance(raw, list):
        return []
    points: list = []
    for item in raw:
        if isinstance(item, AutomationPoint):
            # Au cas où la sérialisation future passe directement les
            # objets : on garde la cohérence.
            points.append(item)
            continue
        if not isinstance(item, dict):
            continue
        try:
            point = AutomationPoint(
                time_seconds=float(item.get("time_seconds", 0.0)),
                gain_db=float(item.get("gain_db", 0.0)),
                fade_seconds=float(item.get("fade_seconds", 0.0)),
            )
        except (AudioAutomationError, ValueError, TypeError):
            continue
        points.append(point)
    points.sort(key=lambda p: p.time_seconds)
    return points


# ---------------------------------------------------------------------------
# Écriture atomique
# ---------------------------------------------------------------------------


def _atomic_write_json(payload: dict[str, Any], target: Path) -> None:
    """Écrit ``payload`` dans ``target`` de manière atomique.

    Le payload est d'abord écrit dans un fichier temporaire situé dans
    le même répertoire que la cible, puis flush + fsync sont effectués
    avant un ``os.replace`` final. Si une étape échoue, le fichier
    temporaire résiduel est nettoyé et la cible reste intacte.
    """
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            # allow_nan=False : un NaN/Inf (ex. fade corrompu) doit faire
            # échouer la sauvegarde plutôt que d'écrire du JSON invalide
            # ("Infinity") qu'aucun lecteur strict ne peut relire.
            try:
                json.dump(payload, tmp_file, indent=2, ensure_ascii=False, allow_nan=False)
            except ValueError as error:
                raise ValueError(
                    "Le projet contient une valeur non finie (NaN ou infinie) : il ne peut pas être "
                    f"enregistré tel quel ({error})."
                ) from error
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        try:
            os.replace(tmp_path, target)
        except PermissionError:
            # Windows interdit de remplacer un fichier qui est encore
            # ouvert, même avec ``delete=False`` (cas courant d'un fichier
            # temporaire créé par un appelant). Si le fichier reste
            # accessible en écriture, on conserve la sauvegarde plutôt que
            # d'échouer inutilement. Le chemin normal reste atomique ; ce
            # repli n'est employé que lorsque le renommage Windows est
            # explicitement refusé.
            with tmp_path.open("rb") as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)
                destination.flush()
                os.fsync(destination.fileno())
            tmp_path.unlink()
    except Exception:
        # Best-effort cleanup : on ne veut pas masquer l'erreur d'origine.
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
