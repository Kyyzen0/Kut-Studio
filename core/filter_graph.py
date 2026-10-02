"""Graphe de filtres FFmpeg partage entre apercu et export (tache 30).

Unique source de verite : l'apercu et l'export appellent les memes
fonctions, donc le moniteur affiche ce que l'export produira.
L'implementation delegue a core.export_engine (zero duplication).
"""

from __future__ import annotations

import hashlib
import json
import os


def build_filter_complex(plan, output_width, output_height, fps, srt_path=None, quality="export"):
    """Construit le -filter_complex via le moteur d'export (parite).

    ``quality`` ne regle que le flou de mouvement des calques motion
    graphics (brouillon : desactive) ; le reste du graphe est identique.
    """
    from .export_engine import ExportEngine

    return ExportEngine._build_filter_complex(
        plan, output_width, output_height, fps, srt_path, quality=quality
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
    """
    from .export_engine import _ffmpeg_command_prefix, filter_graph_arguments
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
    result = build_filter_complex(plan, out_w, out_h, fps, srt_path, quality=quality)
    filter_complex, video_label, audio_label, input_paths = result
    command = [*_ffmpeg_command_prefix(), "-y", "-hide_banner", "-loglevel", "error"]
    input_args = kwargs.get("input_args")
    for path in input_paths:
        if callable(input_args):
            command.extend(input_args(path))
        command.extend(["-i", path])
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
        ]
    )
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


RENDER_ENGINE_VERSION = 2
"""Version du rendu d'aperçu, incluse dans toute empreinte de segment."""


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
    payload = {
        # À incrémenter quand le rendu change sans que le plan change : le cache d'aperçu est persistant
        # (7 jours) et resservirait sinon des segments produits par l'ancien rendu.
        "engine": RENDER_ENGINE_VERSION,
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
        "master": [float(getattr(plan, "master_gain_db", 0.0)), bool(getattr(plan, "master_muted", False))],
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
                ),
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
