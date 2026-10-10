"""Graphe de filtres FFmpeg partage entre apercu et export (tache 30).

Unique source de verite : l'apercu et l'export appellent les memes
fonctions, donc le moniteur affiche ce que l'export produira.
L'implementation delegue a core.export_engine (zero duplication).
"""

from __future__ import annotations

import hashlib
import json
import os


def build_filter_complex(plan, output_width, output_height, fps, srt_path=None, quality="export", prepared=None,
                         origin=0.0):
    """Construit le -filter_complex via le moteur d'export (parite).

    ``quality`` ne regle que le flou de mouvement des calques motion
    graphics (brouillon : desactive) ; le reste du graphe est identique.
    ``prepared`` : flux d'images intermediaires deja fabriques (voir
    ``core.retime_layers``), par identifiant de clip.
    ``origin`` : premier instant utile (debut d'un segment) ; voir
    ``ExportEngine._build_filter_complex``.
    """
    from .export_engine import ExportEngine

    return ExportEngine._build_filter_complex(
        plan, output_width, output_height, fps, srt_path, quality=quality, prepared=prepared, origin=origin
    )


def build_input_list(plan):
    """Inputs dedupliques + mapping path -> index (meme code export)."""
    from .export_engine import _build_input_list

    return _build_input_list(plan)


def preview_output_size(width, height, quality):
    """Resolution d'apercu selon la qualite (draft/standard/high)."""
    from .preview_render import preview_scale_factor

    factor = preview_scale_factor(quality)
    return (max(2, int(width * factor)), max(2, int(height * factor)))


def write_subtitle_file(plan):
    """Ecrit le SRT/ASS temporaire d'un plan (meme code que l'export).

    Returns:
        Le chemin du fichier ecrit, ou ``None`` si le plan n'a aucun
        sous-titre actif. L'appelant supprime le fichier.
    """
    from .export_engine import write_subtitle_file as _write

    return _write(plan)


def ffmpeg_supports_subtitles():
    """La build FFmpeg sait-elle incruster des sous-titres (libass) ?

    Sans libass, le filtre ``subtitles`` est absent : tout plan portant
    des sous-titres echouerait. L'apercu s'en sert pour refuser un
    segment au lieu de relancer sans fin un rendu impossible.
    """
    from .export_engine import _ffmpeg_supports_subtitles

    return bool(_ffmpeg_supports_subtitles())


def normalize_fps(value, default=30):
    """Cadence propre : entière si elle l'est (``30``, pas ``30.0``), sinon flottante (``29.97``).

    ``int(29.97)`` valait 29 : l'aperçu était rendu à une cadence qui n'est pas celle du projet.
    """
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return default
    if not rate > 0.0 or rate != rate:
        return default
    return int(rate) if rate.is_integer() else rate


def build_preview_command(plan, **kwargs):
    """Commande FFmpeg d'un segment d'apercu, graphe identique a l'export.

    ``temporary_files`` (liste) recoit le fichier du graphe quand il est trop
    long pour la ligne de commande : l'appelant le supprime apres le rendu.

    ``input_args`` (``chemin -> options``) place des options **avant** chaque
    ``-i`` : c'est par la que passe le decodage materiel (``-hwaccel``, voir
    :mod:`core.decode_policy`). Les images decodees sont telechargees en
    memoire systeme : le graphe, lui, ne change pas.

    ``keyframe_interval`` (images) fixe la distance entre images clés : un
    fichier long, lu et parcouru par le moniteur (cache d'une composition),
    se positionne vite partout.
    """
    from .export_engine import (
        OUTPUT_COLOR_TAGS,
        _ffmpeg_command_prefix,
        decoder_threads,
        filter_graph_arguments,
        input_arguments,
        with_output_color_stage,
    )
    from .preview_render import preview_crf, preview_preset

    width = int(kwargs.get("width", 1920))
    height = int(kwargs.get("height", 1080))
    fps = normalize_fps(kwargs.get("fps", 30))
    quality = str(kwargs.get("quality", "standard"))
    start = float(kwargs.get("start", 0.0) or 0.0)
    duration = kwargs.get("duration", None)
    output_path = str(kwargs.get("output_path", ""))
    srt_path = kwargs.get("srt_path", None)
    out_w, out_h = preview_output_size(width, height, quality)
    # Le segment ne compose qu'à partir de son début : son coût ne dépend plus de sa position sur la timeline.
    result = build_filter_complex(plan, out_w, out_h, fps, srt_path, quality=quality, prepared=kwargs.get("prepared"),
                                  origin=max(0.0, start))
    filter_complex, video_label, audio_label, input_paths = result
    # Même conversion et mêmes balises que l'export (BT.709) : l'aperçu montre les couleurs de l'export.
    filter_complex, video_label = with_output_color_stage(filter_complex, video_label)
    command = [*_ffmpeg_command_prefix(), "-y", "-hide_banner", "-loglevel", "error"]
    input_args = kwargs.get("input_args")
    threads = decoder_threads(input_paths)
    for path in input_paths:
        if callable(input_args):
            command.extend(input_args(path))
        command.extend(input_arguments(path, threads=threads))
    temporary_files = kwargs.get("temporary_files")
    command.extend(filter_graph_arguments(filter_complex, temporary_files if temporary_files is not None else []))
    command.extend(["-map", "[" + video_label + "]"])
    command.extend(["-map", "[" + audio_label + "]"])
    # Reglages x264 propres a la qualite d'apercu (brouillon rapide,
    # haute fidelite plus lente). Le graphe de filtres, lui, reste
    # exactement celui de l'export.
    command.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            preview_preset(quality),
            "-crf",
            str(preview_crf(quality)),
            *OUTPUT_COLOR_TAGS,
        ]
    )
    keyframe_interval = kwargs.get("keyframe_interval")
    if keyframe_interval:
        command.extend(["-g", str(int(keyframe_interval)), "-keyint_min", str(int(keyframe_interval))])
    command.extend(["-c:a", "aac", "-ac", "2", "-ar", "48000", "-b:a", "128k"])
    if start > 0:
        command.extend(["-ss", "%.3f" % max(0.0, start)])
    if duration is not None and float(duration) > 0:
        command.extend(["-t", "%.3f" % float(duration)])
    command.append(output_path)
    return command


def _graphic_source_key(graphic) -> str:
    """Signature du fichier d'un calque image (modifié sur disque → nouveau rendu)."""
    path = getattr(graphic, "source_path", "")
    if not path:
        return ""
    try:
        stat = os.stat(path)
    except OSError:
        return "missing"
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _interpolation_identity(plan, preference: str = "auto"):
    """Identité du moteur d'images intermédiaires si un clip du plan (ou d'une séquence imbriquée) en demande, sinon ``None``.

    ``preference`` est le backend **demandé** par l'utilisateur (``auto``, ``cpu``, ``gpu``) : c'est lui qui désigne le backend
    réellement utilisé, donc celui qui entre dans l'empreinte."""
    from .time_remapping import TimeInterpolation

    layers = list(getattr(plan, "video_layers", ()))
    for entry in getattr(plan, "nested_sequences", ()) or ():
        layers.extend(entry.plan.video_layers)
    if not any(
        getattr(layer, "time_map", None) is not None and layer.time_remapping.interpolation is not TimeInterpolation.SAMPLING
        for layer in layers
    ):
        return None
    from .optical_flow import ENGINE_VERSION, BackendPreference, BackendUnavailable, classification_key, select_backend
    from .retime_prepare import PREPARE_VERSION

    try:
        backend = select_backend(BackendPreference(preference))
    except (ValueError, BackendUnavailable):
        backend = select_backend(BackendPreference.AUTO)             # le rendu fera de même : mêmes images, même empreinte
    return [ENGINE_VERSION, PREPARE_VERSION, backend.name, backend.version, *classification_key()]


def _raster_version() -> int:
    from .mograph_raster import RASTER_VERSION

    return RASTER_VERSION


RENDER_ENGINE_VERSION = 10
"""Version du rendu d'aperçu, incluse dans toute empreinte de segment.

3 : un clip audio qui ne commence pas à 0 est retardé par ``adelay`` (``amix`` ignore les horodatages : avant, il jouait
depuis le début de la timeline) ; les segments d'aperçu mis en cache avec l'ancien son sont ignorés.
4 : le temps d'un clip remappé vient de son ``TimeMap`` (courbe de vitesse, échantillonnage à l'image la plus proche au lieu
de ``setpts`` + ``fps``, arrêt sur image muet pendant exactement sa durée) ; le décalage d'une couche vidéo sur la timeline
porte une garde d'un millième de tick (``setpts`` tronque : une couche posée sur une image arrivait une image trop tôt) ;
les anciens segments sont ignorés.
5 : le mixage audio additionne (``amix=…:normalize=0``) au lieu de diviser chaque entrée par leur nombre, puis un limiteur à
0 dBFS ferme le mixage ; un segment mis en cache avec l'ancien son (6 dB trop bas pour un clip seul) est ignoré.
6 : le Master coupé rend du silence (``volume=0``, facteur linéaire) : ``volume=0dB`` est le gain unité et laissait passer le
son ; un segment mis en cache avec un Master coupé et l'ancien son (non muet) est ignoré.
7 : le ducking et l'automation de piste produisent enfin un graphe que FFmpeg accepte (seuil linéaire, attaque et
relâchement en ms, clé écrêtée à ``reduction_db``, courbe ``gain_at`` en temps du clip, gain linéaire et non des dB lus comme
un facteur) ; un segment mis en cache avec l'ancien graphe (qui n'a jamais pu être rendu tel quel) est ignoré.
8 : un effet réglé en pixels (σ du flou) suit la taille de rendu (pixels de sortie par pixel de la séquence) ; un segment
d'aperçu réduit mis en cache avec un flou deux ou quatre fois trop large est ignoré.
9 : une échelle animée donne à ``rotate`` un cadre fixe, celui de la plus grande image du clip (il valait la taille de la
première image : un clip qui grandissait était rogné) ; les segments mis en cache avec l'ancien cadre sont ignorés.
10 : les listes d'images de calques déclarent leur cadence (à 30 i/s, une image d'animation sur six était sautée) et un
plan qui ne tourne jamais n'a plus de ``rotate`` (un demi-pixel de flou) ; les segments mis en cache avec la saccade ou
le flou sont ignorés."""


def fingerprint_plan(plan, **kwargs):
    """Empreinte SHA-256 deterministe d'un plan + parametres de rendu."""
    from .export_engine import _build_clip_effect_filters
    from .export_engine import _build_color_grade_filters

    def _grade_key(grade):
        if grade is None:
            return None
        try:
            return _build_color_grade_filters(grade)
        except Exception:
            return repr(grade)

    def _effects_key(effects):
        try:
            return _build_clip_effect_filters(tuple(effects or ()))
        except Exception:
            return repr(effects)

    width = int(kwargs.get("width", 1920))
    height = int(kwargs.get("height", 1080))
    fps = float(kwargs.get("fps", 30))
    quality = str(kwargs.get("quality", "standard"))
    extra = str(kwargs.get("extra", ""))
    flow_preference = str(getattr(kwargs.get("flow_preference", "auto"), "value", kwargs.get("flow_preference", "auto")))
    payload = {
        # Moteur d'images intermédiaires (mélange d'images, flux optique) : présent seulement si un clip en demande, pour qu'une
        # nouvelle version de l'algorithme invalide les segments qui en dépendent, et eux seuls.
        "interpolation": _interpolation_identity(plan, flow_preference),
        # À incrémenter quand le rendu change sans que le plan change : le cache d'aperçu est persistant
        # (7 jours) et resservirait sinon des segments produits par l'ancien rendu.
        "engine": RENDER_ENGINE_VERSION,
        # Dessin des calques graphiques : ses images sont nommées par leur état, pas par leur rendu.
        "raster": _raster_version() if getattr(plan, "graphics_layers", ()) else None,
        "width": width,
        "height": height,
        "fps": fps,
        "quality": quality,
        "extra": extra,
        "duration": float(getattr(plan, "duration", 0.0)),
        "video": [
            {
                "clip_id": layer.clip_id,
                "asset_id": layer.asset_id,
                "source_path": layer.source_path,
                "source_in": layer.source_in,
                "source_out": layer.source_out,
                "timeline_start": layer.timeline_start,
                "timeline_end": layer.timeline_end,
                "transform": repr(getattr(layer, "transform", None)),
                "keyframes": repr(getattr(layer, "transform_keyframes", ())),
                "time_remapping": repr(getattr(layer, "time_remapping", None)),
                "time_map": repr(getattr(layer, "time_map", None)),
                "source_frames": getattr(layer, "source_frames", 0),
                "effects": _effects_key(getattr(layer, "effects", ())),
                "grade": _grade_key(getattr(layer, "color_grade", None)),
                "compositing": repr(getattr(layer, "compositing", None)),
                "animation": repr(getattr(layer, "animation", ())),
                "nested": getattr(layer, "nested_key", ""),
            }
            for layer in getattr(plan, "video_layers", ())
        ],
        "audio": [
            {
                "clip_id": layer.clip_id,
                "source_in": layer.source_in,
                "source_out": layer.source_out,
                "timeline_start": layer.timeline_start,
                "gain_db": getattr(layer, "gain_db", 0.0),
                "time_remapping": repr(getattr(layer, "time_remapping", None)),
                "nested": getattr(layer, "nested_key", ""),
                # Tout le mixage (pan, fondus, volume / pan de piste, effets, automation, ducking) :
                # la liste à la main avait oublié ces champs et le cache resservait un son périmé.
                # Le repr de la couche suit aussi les champs qu'on ajoutera ; au pire il invalide trop.
                "mix": repr(layer),
            }
            for layer in getattr(plan, "audio_layers", ())
        ],
        "master": [float(getattr(plan, "master_gain_db", 0.0)), bool(getattr(plan, "master_muted", False)),
                   getattr(plan, "loudness_gain_db", None)],
        "subtitles": [
            {"start": float(c.start), "end": float(c.end), "text": c.text}
            for c in getattr(plan, "subtitle_cues", ())
        ],
        "subtitle_styles": [repr(style) for style in getattr(plan, "subtitle_styles", ())],
        "transitions": [
            {
                "from": t.from_clip_id,
                "to": t.to_clip_id,
                "type": getattr(getattr(t, "type", None), "value", str(getattr(t, "type", ""))),
                "duration": float(t.duration),
            }
            for t in getattr(plan, "transitions", ())
        ],
        "graphics": [
            {
                "clip_id": layer.clip_id,
                "start": layer.timeline_start,
                "end": layer.timeline_end,
                "graphic": repr(layer.graphic),
                "transform": repr(layer.transform),
                "keyframes": repr(layer.transform_keyframes),
                # Motion graphics : animation générique, masques / fusion,
                # effets, rôle (« rig » = parent hors fenêtre) et image source.
                "animation": repr(getattr(layer, "animation", ())),
                "compositing": repr(getattr(layer, "compositing", None)),
                "effects": _effects_key(getattr(layer, "effects", ())),
                "grade": _grade_key(getattr(layer, "color_grade", None)),
                "role": getattr(layer, "role", "draw"),
                "source": _graphic_source_key(layer.graphic),
            }
            for layer in getattr(plan, "graphics_layers", ())
        ],
        "motion_blur": repr(getattr(plan, "motion_blur", None)),
        # Séquences imbriquées : l'empreinte d'un sous-plan ne couvre que la
        # plage qu'il lit dans ce segment. Modifier « Intro » change donc les
        # segments parents qui la montrent, et eux seuls.
        "nested": [
            {
                "key": entry.key,
                "plan": fingerprint_plan(
                    entry.plan,
                    width=entry.plan.width,
                    height=entry.plan.height,
                    fps=entry.plan.fps,
                    quality=quality,
                    flow_preference=flow_preference,
                ),
                # Composition nodale : son graphe (nœuds, réglages, liens) et l'animation de ses masques.
                "composition": repr(getattr(entry, "composition", None)),
            }
            for entry in getattr(plan, "nested_sequences", ()) or ()
        ],
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


__all__ = [
    "build_filter_complex",
    "build_input_list",
    "build_preview_command",
    "ffmpeg_supports_subtitles",
    "fingerprint_plan",
    "preview_output_size",
    "write_subtitle_file",
]
