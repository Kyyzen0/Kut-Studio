"""Parcours (c) « motion design + 3D » : un fond vidéo à étalonnage et effets animés, des calques graphiques animés (texte,
formes, groupe, classement), une scène Kut-3D (formes de base et texte 3D extrudé) animée par images clés.

L'étalonnage animé (``color.*``) et l'effet animé (vignette, correction de couleur) sont ceux de l'ADR-0008 ; la scène est le
fichier ``.kut3d`` écrit par ``tools.recette.scene3d`` (ADR-0009). Tout est posé par un script déclaratif.
"""

from __future__ import annotations

from typing import Any

from ..media import MediaItem
from . import Capabilities, ExportSpec, Journey, check_scale, snap

FPS = 30.0
SCENE_FILE = "media/titre.kut3d"


def length(scale: str) -> float:
    return 30.0 if check_scale(scale) == "full" else 10.0


def media(scale: str) -> list[MediaItem]:
    total = length(scale)
    return [
        MediaItem("bg", "media/fond.mp4", "video", {"width": 1920, "height": 1080, "fps": 30, "duration": total, "seed": 51,
                                                    "audio": {"kind": "ambient", "seed": 51, "duration": total}}),
        MediaItem("bed", "media/nappe.wav", "wav", {"signal": "pad", "seed": 2, "duration": total}),
        MediaItem("scene", SCENE_FILE, "scene3d", {"title": "KUT", "segments": 24}),
    ]


def _keys(clip: str, prop: str, points: list[tuple[float, float]], interpolation: str = "ease_in_out",
          length: float | None = None) -> list[dict[str, Any]]:
    """Images clés d'une propriété. ``at`` est relatif au clip ; avec ``length`` (durée du clip), ``at`` est une FRACTION de cette
    durée (de 0 à 1) et l'instant est aligné sur une image, jamais au-delà du clip."""
    moments = [(round(min(snap(at * length, FPS), length), 6) if length else at, value) for at, value in points]
    return [{"op": "add_keyframe", "clip": clip, "property": prop, "at": at, "value": value, "interpolation": interpolation}
            for at, value in moments]


# NB : le ``start`` d'une animation de texte se mesure depuis le DÉBUT DU CALQUE (core/text_animations.py), jamais depuis la timeline.


def script(scale: str, caps: Capabilities) -> dict[str, Any]:
    total = length(scale)

    def t(seconds: float) -> float:
        """Un instant du parcours complet (30 s) ramené à l'échelle, aligné sur une image."""
        return snap(seconds * total / 30.0, FPS)

    ops: list[dict[str, Any]] = [
        {"op": "import_media", "path": "media/fond.mp4", "as": "bg"},
        {"op": "import_media", "path": "media/nappe.wav", "as": "bed"},
        {"op": "add_clip", "track": "V1", "asset": {"ref": "bg"}, "at": 0.0, "id": "c_bg"},
        {"op": "add_clip", "track": "A1", "asset": {"ref": "bed"}, "at": 0.0, "id": "c_bed"},
    ]
    # Étalonnage animé du fond (images clés color.*) et effets animés.
    ops += _keys("c_bg", "color.exposure", [(0.0, -0.3), (0.5, 0.3), (0.98, 0.0)], length=total)
    ops += _keys("c_bg", "color.temperature", [(0.0, -30.0), (0.5, 40.0), (0.98, 0.0)], length=total)
    ops += _keys("c_bg", "color.saturation", [(0.0, 0.8), (0.5, 1.3), (0.98, 1.0)], length=total)
    ops += _keys("c_bg", "color.hue", [(0.0, 0.0), (0.98, 25.0)], "linear", total)
    ops += _keys("c_bg", "color.gain.r", [(0.0, 0.0), (0.66, 0.12)], "linear", total)
    ops += [{"op": "add_effect", "clip": "c_bg", "type": "vignette", "as": "vig"}]
    ops += [{"op": "add_effect_keyframe", "clip": "c_bg", "effect": {"ref": "vig"}, "parameter": "intensity", "at": at, "value": value,
             "interpolation": "ease_in_out"} for at, value in ((0.0, 0.1), (snap(total * 0.5, FPS), 0.7), (snap(total * 0.98, FPS), 0.2))]
    # Calques graphiques : titre à révélation animée, sous-titre à machine à écrire, formes en orbite, classement.
    ops += [
        {"op": "add_graphic", "graphic": "text", "at": t(0.5), "duration": round(total - t(1.0), 6), "text": "MOTION DESIGN", "as": "titre"},
        {"op": "set_graphic", "clip": {"ref": "titre"}, "font_size": 150, "fill_color": "#FFFFFF", "stroke_color": "#202020",
         "stroke_width": 4},
    ]
    title_length = round(total - t(1.0), 6)
    ops += _keys("titre", "graphic.reveal", [(0.0, 0.0), (0.08, 1.0)], "ease_out", title_length)
    ops += _keys("titre", "position_y", [(0.0, -0.25), (0.06, -0.25), (0.12, -0.32)], "ease_out", title_length)
    ops += _keys("titre", "graphic.tracking", [(0.0, 40.0), (0.12, 0.0)], "ease_out", title_length)
    ops += [
        {"op": "add_graphic", "graphic": "text", "at": t(2.0), "duration": round(total - t(3.0), 6),
         "text": "calques animés · 3D · étalonnage", "as": "sous_titre"},
        {"op": "set_graphic", "clip": {"ref": "sous_titre"}, "font_size": 60, "fill_color": "#FFE08A"},
        {"op": "apply_text_animation", "clip": {"ref": "sous_titre"}, "animation": "typewriter", "start": 0.0},
    ]
    colors = ("#E63946", "#2A9D8F", "#F4A261")
    orbit_length = round(total - t(3.0), 6)
    for index, color in enumerate(colors, start=1):
        shape = f"orbite{index}"
        ops += [{"op": "add_graphic", "graphic": "shape", "shape": "ellipse", "at": t(1.0 + index * 0.5), "duration": round(total - t(3.0), 6),
                 "as": shape},
                {"op": "set_graphic", "clip": {"ref": shape}, "fill_color": color, "width": 140 + 40 * index, "height": 140 + 40 * index}]
        ops += _keys(shape, "position_x", [(0.0, -0.35 * index), (0.5, 0.35 * index), (1.0, -0.35 * index)], length=orbit_length)
        ops += _keys(shape, "scale", [(0.0, 0.6), (0.3, 1.1), (0.6, 0.7)], length=orbit_length)
        ops += _keys(shape, "opacity", [(0.0, 0.0), (0.08, 0.85)], "linear", orbit_length)
    ops += [{"op": "group_layers", "clips": [{"ref": "orbite1"}, {"ref": "orbite2"}, {"ref": "orbite3"}], "name": "Orbites", "as": "orbites"}]
    ops += _keys("orbites", "rotation", [(0.0, 0.0), (0.6, 360.0)], "linear", orbit_length - t(2.0))
    ops += [
        {"op": "add_leaderboard", "rows": [{"rank": "1", "name": "Alpha", "value": "92", "color": "#E63946"},
                                           {"rank": "2", "name": "Bravo", "value": "75", "color": "#2A9D8F"},
                                           {"rank": "3", "name": "Charlie", "value": "61", "color": "#F4A261"},
                                           {"rank": "4", "name": "Delta", "value": "48", "color": "#457B9D"},
                                           {"rank": "5", "name": "Echo", "value": "30", "color": "#A8DADC"}],
         "start": t(18), "duration": round(t(10), 6), "as": "classement"},
        {"op": "apply_layer_preset", "preset": "Titre espacé animé", "at": t(4.0), "as": "preset_titre"},
    ]
    # Scène 3D (formes de base et texte 3D) posée en calque et animée par images clés.
    scene_duration = round(total - t(8.0), 6)
    ops += [{"op": "add_scene3d_clip", "path": SCENE_FILE, "at": t(3.0), "duration": scene_duration, "as": "scene"}]
    ops += _keys("scene", "scale", [(0.0, 0.5), (0.1, 1.0), (0.9, 1.0), (1.0, 0.9)], "ease_out", scene_duration)
    ops += _keys("scene", "opacity", [(0.0, 0.0), (0.05, 1.0), (0.95, 1.0), (1.0, 0.0)], "linear", scene_duration)
    ops += _keys("scene", "rotation", [(0.0, -8.0), (1.0, 8.0)], "linear", scene_duration)
    ops += [{"op": "add_marker", "at": t(15.0), "name": "Milieu", "category": "chapter"}]
    return {"kut_script": 1, "description": f"Recette : motion design et 3D {total:g} s ({scale})", "ops": ops}


def exports(scale: str, caps: Capabilities) -> list[ExportSpec]:
    return [ExportSpec("1080p", "motion3d-1080p.mp4", "h264_1080p", None, (1920, 1080))]


JOURNEY = Journey(
    name="motion3d", title="Motion design + 3D", base=(1920, 1080, FPS), tracks=(("V1", "video"), ("A1", "audio")),
    media=media, script=script, exports=exports,
    description="Fond à étalonnage et vignette animés, calques graphiques animés, classement, scène Kut-3D animée.",
)
