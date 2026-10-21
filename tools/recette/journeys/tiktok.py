"""Parcours (a) « TikTok 60 s » : plans verticaux et horizontaux courts, musique à tempo détectable, voix, coupes sur le rythme,
sous-titres animés, version carrée, export 9:16.

Média : 3 plans verticaux (1080x1920, 30 i/s) et 2 horizontaux (1920x1080, 25 i/s : cadences mélangées), une musique à
120 battements par minute et une voix synthétique (parole de syllabes : whisper n'y trouvera pas de texte, voir
``docs/recette/README.md``). Les sous-titres suivent un texte connu, posé par le script : ils ne dépendent pas de whisper.
"""

from __future__ import annotations

from typing import Any

from ..media import MediaItem
from . import AnalysisSpec, Capabilities, ExportSpec, Journey, check_scale, snap

FPS = 30.0
BPM = 120.0
# (clé, largeur, hauteur, cadence, durée utilisée à l'échelle « full », graine)
PLAN = (
    ("v1", 1080, 1920, 30, 12.0, 11), ("v2", 1080, 1920, 30, 14.0, 12), ("h1", 1920, 1080, 25, 10.0, 13),
    ("v3", 1080, 1920, 30, 12.0, 14), ("h2", 1920, 1080, 25, 12.0, 15),
)
HANDLE = 2.0
"""Marge de média au-delà de la durée utilisée (rognage de la fin, poignée de transition)."""
WORDS = ("Voici", "trois", "astuces", "pour", "monter", "vite", "sur", "Kut", "Studio", "sans", "perdre", "de", "temps", "et",
         "garder", "le", "rythme", "de", "la", "musique")
SECOND_SEQUENCE = "Version carrée"
TRANSITIONS = (("v1", "v2", "crossfade", 0.4), ("h1", "v3", "wipe_left", 0.5))
"""(plan sortant, plan entrant, type, durée) : la durée d'une transition est un chevauchement ; le plan entrant est donc posé
DÉJÀ en avance (sinon ``add_transition`` le recale seul et laisse un trou noir après lui, voir ``docs/recette/defauts-connus.md``)."""
OVERLAP = {incoming: length for _out, incoming, _kind, length in TRANSITIONS}


def _factor(scale: str) -> float:
    return 1.0 if check_scale(scale) == "full" else 1.0 / 3.0


def uses(scale: str) -> list[tuple[str, float]]:
    """(clé, durée utilisée) de chaque plan à l'échelle donnée, alignées sur une image."""
    factor = _factor(scale)
    return [(key, snap(max(2.0, use * factor), FPS)) for key, _w, _h, _f, use, _s in PLAN]


def positions(scale: str) -> dict[str, float]:
    """Début de chaque plan sur la timeline (les plans qui suivent une transition commencent en avance de sa durée)."""
    start, found = 0.0, {}
    for key, use in uses(scale):
        start -= OVERLAP.get(key, 0.0)
        found[key] = round(start, 6)
        start += use
    return found


def duration(scale: str) -> float:
    last, use = uses(scale)[-1][0], uses(scale)[-1][1]
    return round(positions(scale)[last] + use, 6)


def media(scale: str) -> list[MediaItem]:
    total = duration(scale)
    items: list[MediaItem] = []
    for (key, width, height, fps, _use, seed), (_key, use) in zip(PLAN, uses(scale), strict=True):
        items.append(MediaItem(key, f"media/{key}.mp4", "video", {
            "width": width, "height": height, "fps": fps, "duration": snap(use + HANDLE, FPS), "seed": seed,
            "audio": {"kind": "ambient", "seed": seed, "duration": snap(use + HANDLE, FPS)},
        }))
    items.append(MediaItem("music", "media/musique.wav", "wav", {"signal": "music", "seed": 2, "bpm": BPM, "duration": total}))
    items.append(MediaItem("voice", "media/voix.wav", "wav", {"signal": "speech", "seed": 5, "duration": total}))
    return items


def caption_lines(scale: str) -> list[dict[str, Any]]:
    """Les lignes de sous-titres : trois mots toutes les 2,5 s, un instant par mot, relatif au début de la ligne (texte connu, déterministe)."""
    total = duration(scale)
    lines: list[dict[str, Any]] = []
    index = 0
    while 0.4 + index * 2.5 + 2.2 <= total - 0.5:
        start = round(0.4 + index * 2.5, 3)
        words = [WORDS[(index * 3 + offset) % len(WORDS)] for offset in range(3)]
        lines.append({"start": start, "end": round(start + 2.2, 3), "text": " ".join(words),
                      "word_starts": [round(offset * 0.7, 3) for offset in range(3)]})
        index += 1
    return lines


def script(scale: str, caps: Capabilities) -> dict[str, Any]:
    total = duration(scale)
    ops: list[dict[str, Any]] = []
    for key, *_rest in PLAN:
        ops.append({"op": "import_media", "path": f"media/{key}.mp4", "as": key})
    ops += [{"op": "import_media", "path": "media/musique.wav", "as": "music"},
            {"op": "import_media", "path": "media/voix.wav", "as": "voice"}]
    # Les plans bout à bout sur V1 : poser, puis rogner la fin avant de poser le suivant (un média pose toute sa durée).
    where = positions(scale)
    for (key, width, _height, _fps, _use, _seed), (_key, use) in zip(PLAN, uses(scale), strict=True):
        clip = f"c_{key}"
        ops.append({"op": "add_clip", "track": "V1", "asset": {"ref": key}, "at": where[key], "id": clip})
        ops.append({"op": "trim_clip_end", "clip": clip, "at": round(where[key] + use, 6)})
        if width > 1080:                                   # un plan horizontal remplit le cadre vertical (recadré)
            ops.append({"op": "set_transform", "clip": clip, "fill": True})
    # Son : musique et voix, la musique baisse sous la voix.
    ops += [
        {"op": "set_track_role", "track": "A1", "role": "music"},
        {"op": "add_track", "type": "audio", "name": "Voix", "as": "voix_track"},
        {"op": "set_track_role", "track": "Voix", "role": "voice"},
        {"op": "add_clip", "track": "A1", "asset": {"ref": "music"}, "at": 0, "id": "c_music"},
        {"op": "add_clip", "track": "Voix", "asset": {"ref": "voice"}, "at": 0, "id": "c_voice"},
        {"op": "duck_music_under_voice", "preset": "social_punchy"},
    ]
    # Transitions avant les coupes (le clip entrant est déjà posé en chevauchement).
    ops += [{"op": "add_transition", "from_clip": f"c_{out}", "to_clip": f"c_{incoming}", "transition": kind, "duration": length}
            for out, incoming, kind, length in TRANSITIONS]
    # Coupes sur le rythme (120 battements par minute : un temps toutes les 0,5 s ; un temps sur quatre = 2 s).
    ops += [{"op": "set_beat_grid", "bpm": BPM, "offset": 0.0, "beats_per_bar": 4},
            {"op": "cut_clips_on_beats", "clips": ["c_v2", "c_v3"], "every": 4}]
    # Effets d'impact, flash, titre animé.
    ops += [
        {"op": "apply_impact_zoom", "clip": "c_v1", "strength": 0.15, "start": 1.0, "length": 0.3},
        {"op": "apply_camera_shake", "clip": "c_h1", "start": 2.0, "length": 0.45, "amplitude": 0.01, "rotation": 1.0, "seed": 3},
        {"op": "add_flash", "at": where["h1"], "length": 0.12, "color": "#FFFFFF"},
        {"op": "apply_layer_preset", "preset": "Titre TikTok", "at": 0.2, "as": "titre"},
        {"op": "apply_text_animation", "clip": {"ref": "titre"}, "animation": "pop_in", "start": 0.0},
    ]
    # Sous-titres : la première moitié sur une piste de sous-titres (libass) si disponible, le reste en karaoké (Qt).
    lines = caption_lines(scale)
    split = int(len(lines) * 0.4) if caps.libass else 0
    if split:
        ops.append({"op": "add_subtitle_lines", "lines": [{k: v for k, v in line.items() if k != "word_starts"} for line in lines[:split]]})
    ops.append({"op": "add_karaoke_lines", "lines": lines[split:]})
    # Version carrée du même montage.
    ops.append({"op": "create_format_version", "sequence": "seq-main", "format": "square", "name": SECOND_SEQUENCE})
    return {"kut_script": 1, "description": f"Recette : TikTok {total:g} s ({scale})", "ops": ops}


def exports(scale: str, caps: Capabilities) -> list[ExportSpec]:
    return [
        ExportSpec("9x16", "tiktok-9x16.mp4", "tiktok", None, (1080, 1920)),
        ExportSpec("1x1", "tiktok-1x1.mp4", "square", SECOND_SEQUENCE, (1080, 1080)),
    ]


def analyses(scale: str) -> list[AnalysisSpec]:
    return [AnalysisSpec("tempo", {"media": "media/musique.wav", "expected_bpm": BPM})]


JOURNEY = Journey(
    name="tiktok", title="TikTok 60 s", base=(1080, 1920, FPS), tracks=(("V1", "video"), ("A1", "audio")),
    media=media, script=script, exports=exports, analyses=analyses,
    description="Plans verticaux et horizontaux, musique à 120 BPM, voix, coupes sur le rythme, sous-titres animés, version carrée.",
)
