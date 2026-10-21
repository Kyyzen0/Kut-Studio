"""Parcours (b) « YouTube 20 min multicam » : trois angles longs au son synchronisable, narration, coupes d'angle, étalonnage,
repères de chapitres, export long.

Les trois angles (1920x1080, 25 i/s) enregistrent **le même son de salle** (parole synthétique commune) avec un bruit de micro
propre à chaque caméra, des « claps » communs, et des départs décalés de valeurs connues (0, 2,4 et 5,12 s) : le moteur de
synchronisation audio les retrouve (``tools.recette.analyze sync``). Le script pose les décalages **connus** (le projet est donc
le même à chaque exécution) ; la justesse de la synchronisation est mesurée à part.
"""

from __future__ import annotations

from typing import Any

from ..audio import unit
from ..media import MediaItem
from . import AnalysisSpec, Capabilities, ExportSpec, Journey, check_scale, snap

FPS = 25.0
ANGLES = (("Large", "cam_a", 0.0, 31), ("Serré", "cam_b", 2.4, 32), ("Côté", "cam_c", 5.12, 33))
"""(nom, clé, décalage connu de départ dans le plateau en secondes, graine du micro)."""
ROOM_SEED = 41
HEAD = 6.0
"""Le montage commence à HEAD s de plateau : après le départ de la dernière caméra (5,12 s)."""
SWITCH_SEED = 77


def length(scale: str) -> float:
    return 1200.0 if check_scale(scale) == "full" else 90.0


def master_seconds(scale: str) -> float:
    return length(scale) + HEAD + 2.0


def claps(scale: str) -> list[float]:
    return [8.0, 600.0, 1190.0] if check_scale(scale) == "full" else [8.0, 60.0]


def media(scale: str) -> list[MediaItem]:
    total, master = length(scale), master_seconds(scale)
    items: list[MediaItem] = []
    for _name, key, offset, mic in ANGLES:
        seconds = snap(master - offset, FPS)
        items.append(MediaItem(key, f"media/{key}.mp4", "video", {
            "width": 1920, "height": 1080, "fps": 25, "duration": seconds, "seed": mic,
            "audio": {"kind": "room", "seed": ROOM_SEED, "master_seconds": master, "claps": claps(scale), "start": offset,
                      "duration": seconds, "mic_seed": mic},
        }))
    items.append(MediaItem("narration", "media/narration.wav", "wav", {"signal": "speech", "seed": 21, "duration": total}))
    items.append(MediaItem("bed", "media/nappe.wav", "wav", {"signal": "pad", "seed": 1, "duration": total}))
    return items


def switches(scale: str) -> list[tuple[float, str]]:
    """Les changements d'angle : toutes les 14 à 50 s environ, jamais deux fois le même angle de suite (déterministe)."""
    total = length(scale)
    names = [name for name, *_rest in ANGLES]
    moments: list[tuple[float, str]] = []
    when, current, index = 0.0, 0, 0
    while True:
        when += 14.0 + 36.0 * unit(SWITCH_SEED, index)
        if when > total - 3.0:
            return moments
        current = (current + 1 + int(2 * unit(SWITCH_SEED, 1000 + index))) % len(names)
        moments.append((snap(when, FPS), names[current]))
        index += 1


def chapters(scale: str) -> list[tuple[float, str]]:
    total = length(scale)
    count = 10 if scale == "full" else 3
    return [(snap(total * i / count, FPS), f"Chapitre {i + 1}") for i in range(count)]


def script(scale: str, caps: Capabilities) -> dict[str, Any]:
    total = length(scale)
    ops: list[dict[str, Any]] = [{"op": "import_media", "path": f"media/{key}.mp4", "as": key} for _n, key, _o, _m in ANGLES]
    ops += [{"op": "import_media", "path": "media/narration.wav", "as": "narration"},
            {"op": "import_media", "path": "media/nappe.wav", "as": "bed"}]
    ops.append({"op": "create_multicam_source", "as": "plateau", "name": "Plateau", "angles": [
        {"asset": key, "name": name, "offset": offset} for name, key, offset, _m in ANGLES]})
    ops.append({"op": "insert_multicam_clip", "sequence": {"ref": "plateau"}, "track": "V1", "at": 0.0, "angle": "Large",
                "source_in": HEAD, "source_out": round(HEAD + total, 6), "as": "plan0"})
    ops.append({"op": "set_multicam_audio", "sequence": {"ref": "plateau"}, "mode": "fixed", "angles": ["Large"]})
    for index, (when, angle) in enumerate(switches(scale), start=1):
        ops.append({"op": "switch_multicam_angle", "at": when, "angle": angle, "as": f"plan{index}"})
    # Narration par-dessus, nappe sous la voix.
    ops += [
        {"op": "add_track", "type": "audio", "name": "Narration"},
        {"op": "add_track", "type": "audio", "name": "Nappe"},
        {"op": "set_track_role", "track": "Narration", "role": "voice"},
        {"op": "set_track_role", "track": "Nappe", "role": "music"},
        {"op": "add_clip", "track": "Narration", "asset": {"ref": "narration"}, "at": 0.0, "id": "c_narration"},
        {"op": "add_clip", "track": "Nappe", "asset": {"ref": "bed"}, "at": 0.0, "id": "c_bed"},
        {"op": "add_track_insert", "track": "Narration", "effect": "compressor", "params": {"ratio": 3}},
        {"op": "duck_music_under_voice", "preset": "voice_over_music"},
    ]
    # Étalonnage d'ensemble (un calque d'effets au-dessus de tout le montage) et titre, chapitres.
    ops += [
        {"op": "add_graphic", "graphic": "adjustment", "at": 0.0, "duration": total, "as": "etalonnage"},
        {"op": "set_color_grade", "clip": {"ref": "etalonnage"}, "exposure": 0.15, "contrast": 0.1, "saturation": 1.1,
         "temperature": 6},
        {"op": "apply_layer_preset", "preset": "Lower third", "at": 4.0, "as": "bandeau"},
    ]
    ops += [{"op": "add_marker", "at": when, "name": name, "category": "chapter"} for when, name in chapters(scale)]
    return {"kut_script": 1, "description": f"Recette : YouTube multicam {total:g} s ({scale})", "ops": ops}


def exports(scale: str, caps: Capabilities) -> list[ExportSpec]:
    return [ExportSpec("1080p", "youtube-1080p.mp4", "youtube", None, (1920, 1080))]


def analyses(scale: str) -> list[AnalysisSpec]:
    return [AnalysisSpec("sync", {"reference": "cam_a", "angles": [
        {"key": key, "media": f"media/{key}.mp4", "expected_offset": offset} for _n, key, offset, _m in ANGLES]})]


JOURNEY = Journey(
    name="youtube", title="YouTube 20 min multicam", base=(1920, 1080, FPS), tracks=(("V1", "video"), ("A1", "audio")),
    media=media, script=script, exports=exports, analyses=analyses,
    description="Trois angles longs au son synchronisable, narration, coupes d'angle, étalonnage, chapitres, export long.",
)
