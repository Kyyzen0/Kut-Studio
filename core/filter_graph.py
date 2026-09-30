"""Graphe de filtres FFmpeg partage entre apercu et export (tache 30).

Unique source de verite : l'apercu et l'export appellent les memes
fonctions, donc le moniteur affiche ce que l'export produira.
L'implementation delegue a core.export_engine (zero duplication).
"""

from __future__ import annotations

import hashlib
import json


def build_filter_complex(plan, output_width, output_height, fps, srt_path=None):
    """Construit le -filter_complex via le moteur d'export (parite)."""
    from .export_engine import ExportEngine

    return ExportEngine._build_filter_complex(
        plan, output_width, output_height, fps, srt_path
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


def build_preview_command(plan, **kwargs):
    """Commande FFmpeg d'un segment d'apercu, graphe identique a l'export."""
    from .export_engine import require_ffmpeg
    from .preview_render import preview_crf, preview_preset

    width = int(kwargs.get("width", 1920))
    height = int(kwargs.get("height", 1080))
    fps = int(kwargs.get("fps", 30))
    quality = str(kwargs.get("quality", "standard"))
    start = float(kwargs.get("start", 0.0) or 0.0)
    duration = kwargs.get("duration", None)
    output_path = str(kwargs.get("output_path", ""))
    srt_path = kwargs.get("srt_path", None)
    out_w, out_h = preview_output_size(width, height, quality)
    result = build_filter_complex(plan, out_w, out_h, fps, srt_path)
    filter_complex, video_label, audio_label, input_paths = result
    command = [require_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error"]
    for path in input_paths:
        command.extend(["-i", path])
    command.extend(["-filter_complex", filter_complex])
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
            }
            for layer in getattr(plan, "audio_layers", ())
        ],
        "subtitles": [
            {"start": float(c.start), "end": float(c.end), "text": c.text}
            for c in getattr(plan, "subtitle_cues", ())
        ],
        "transitions": [
            {
                "from": t.from_clip_id,
                "to": t.to_clip_id,
                "type": getattr(t.transition_type, "value", str(t.transition_type)),
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
            }
            for layer in getattr(plan, "graphics_layers", ())
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
