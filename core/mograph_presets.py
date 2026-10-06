"""Bibliothèque légère de presets motion graphics.

Un preset est un petit ensemble de calques (texte, formes, groupe,
contrôleur…) avec leurs transforms, masques, effets et animations : la même
forme JSON que le presse-papiers des calques
(:class:`core.mograph_layers.LayerClipboard`). L'appliquer revient à
**coller** ces calques à la tête de lecture.

- presets intégrés : construits par du code (toujours à jour avec le modèle), **sur la toile du projet** où on les
  applique : en vertical, le bandeau se recentre et tient dans un cadre 1080 px de large ; la catégorie « Vertical »
  ajoute les titres d'une vidéo sociale (contour extérieur, mot en couleur, karaoké) ;
- presets utilisateur : fichiers JSON dans ``<config>/mograph_presets`` ;
  « Enregistrer comme preset » sur un calque ou un groupe en crée un.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .animation import InterpolationType, Keyframe
from .graphics import GraphicType, ShapeKind, update_graphic
from .mograph_layers import LayerClipboard, add_layer, copy_layers, group_layers, paste_layers, set_parent
from .platform_paths import user_config_dir
from .project_model import Clip, Project
from .visual_effects import ClipTransform, TransformKeyframe

PRESET_FORMAT = "kut-mograph-preset"
PRESET_VERSION = 1


@dataclass(frozen=True)
class MographPreset:
    name: str
    category: str
    clipboard: LayerClipboard
    builtin: bool = False
    path: str = ""


# ---------------------------------------------------------------------------
# Presets intégrés
# ---------------------------------------------------------------------------


def _scratch(width: int = 1920, height: int = 1080) -> Project:
    return Project("preset", int(width), int(height), 30.0)


def _portrait(project: Project) -> bool:
    return project.height > project.width


def _kf(name: str, t: float, value: float, interpolation=InterpolationType.EASE_OUT) -> TransformKeyframe:
    return TransformKeyframe(name, t, value, interpolation)


def _simple_title(project: Project) -> list[str]:
    title = add_layer(project, GraphicType.TEXT, at=0.0, duration=4.0)
    update_graphic(title, "text", "Titre principal")
    update_graphic(title, "font_size", 72)
    update_graphic(title, "bold", True)
    title.transform_keyframes = [
        _kf("opacity", 0.0, 0.0), _kf("opacity", 0.5, 1.0),
        _kf("scale", 0.0, 0.9), _kf("scale", 0.6, 1.0),
    ]
    title.label = "Titre"
    return [title.id]


def _boxed_title(project: Project) -> list[str]:
    title = add_layer(project, GraphicType.TEXT, at=0.0, duration=4.0)
    for name, value in (
        ("text", "Titre encadré"), ("font_size", 48), ("autosize", True), ("background_enabled", True),
        ("background_color", "#101820DD"), ("background_padding", 24), ("background_radius", 12),
    ):
        update_graphic(title, name, value)
    title.transform = ClipTransform(position_y=0.3)
    title.transform_keyframes = [_kf("opacity", 0.0, 0.0), _kf("opacity", 0.4, 1.0)]
    title.label = "Titre encadré"
    return [title.id]


def _lower_third(project: Project) -> list[str]:
    width = 860 if _portrait(project) else 720
    bar = add_layer(project, GraphicType.SHAPE, at=0.0, duration=5.0, shape=ShapeKind.ROUNDED_RECTANGLE)
    for name, value in (("width", width), ("height", 140), ("corner_radius", 16.0), ("fill_color", "#36E6C3E6")):
        update_graphic(bar, name, value)
    bar.label = "Bandeau"
    name = add_layer(project, GraphicType.TEXT, at=0.0, duration=5.0)
    for field, value in (
        ("text", "Prénom Nom"), ("font_size", 40), ("bold", True), ("align_h", "left"),
        ("width", width - 60), ("height", 60), ("fill_color", "#061514"),
    ):
        update_graphic(name, field, value)
    name.transform = ClipTransform(position_y=-0.012)
    name.label = "Nom"
    role = add_layer(project, GraphicType.TEXT, at=0.0, duration=5.0)
    for field, value in (
        ("text", "Fonction"), ("font_size", 26), ("align_h", "left"), ("width", width - 60), ("height", 40),
        ("fill_color", "#0B2B27"),
    ):
        update_graphic(role, field, value)
    role.transform = ClipTransform(position_y=0.03)
    role.label = "Fonction"
    group = group_layers(project, [bar.id, name.id, role.id], name="Lower third")
    # Vertical : centré, au-dessus de la légende des plateformes (qui couvre le bas du cadre).
    x, y, from_x = (0.0, 0.22, -1.0) if _portrait(project) else (-0.28, 0.33, -0.75)
    group.transform = ClipTransform(position_x=x, position_y=y)
    group.transform_keyframes = [
        _kf("position_x", 0.0, from_x), _kf("position_x", 0.6, x),
        _kf("opacity", 0.0, 0.0), _kf("opacity", 0.3, 1.0),
    ]
    return [group.id]


def _social_text(project: Project, text: str, size: int, y: float, duration: float = 3.0) -> Clip:
    """Texte de vidéo sociale : Anton, contour noir **extérieur**, ombre portée, centré, taille au texte."""
    clip = add_layer(project, GraphicType.TEXT, at=0.0, duration=duration)
    for field, value in (
        ("text", text), ("font_family", "Anton"), ("font_size", size), ("autosize", True), ("stroke_width", 6),
        ("stroke_color", "#05060A"), ("stroke_position", "outside"), ("shadow_offset_x", 0), ("shadow_offset_y", 5),
        ("shadow_blur", 14.0), ("shadow_color", "#000000E0"),
    ):
        update_graphic(clip, field, value)
    clip.transform = ClipTransform(position_y=y)
    return clip


def _tiktok_title(project: Project) -> list[str]:
    from .text_animations import apply_text_animation

    title = _social_text(project, "TITRE", 140, -0.25)
    apply_text_animation(title, "pop_in")
    title.label = "Titre TikTok"
    return [title.id]


def _highlighted_word(project: Project) -> list[str]:
    from .text_animations import apply_text_animation

    line = _social_text(project, "un mot en couleur", 88, -0.15)
    update_graphic(line, "highlight_words", (2,))
    update_graphic(line, "highlight_color", "#22B8FF")
    apply_text_animation(line, "word_by_word")
    line.label = "Mot en couleur"
    return [line.id]


def _karaoke_caption(project: Project) -> list[str]:
    from .text_animations import apply_text_animation

    caption = _social_text(project, "les paroles suivent la voix", 64, 0.27, duration=4.0)
    update_graphic(caption, "highlight_color", "#FFD84D")
    apply_text_animation(caption, "karaoke")
    caption.label = "Sous-titre karaoké"
    return [caption.id]


def _callout(project: Project) -> list[str]:
    controller = add_layer(project, GraphicType.NULL, at=0.0, duration=4.0)
    controller.label = "Contrôle call-out"
    ring = add_layer(project, GraphicType.SHAPE, at=0.0, duration=4.0, shape=ShapeKind.ELLIPSE)
    for field, value in (("width", 160), ("height", 160), ("fill_enabled", False), ("stroke_width", 6),
                         ("stroke_color", "#FFFFFF")):
        update_graphic(ring, field, value)
    ring.label = "Cercle"
    ring.transform_keyframes = [_kf("scale", 0.0, 0.05), _kf("scale", 0.4, 1.0)]
    line = add_layer(project, GraphicType.SHAPE, at=0.0, duration=4.0, shape=ShapeKind.LINE)
    update_graphic(line, "width", 240)
    update_graphic(line, "stroke_width", 4)
    line.transform = ClipTransform(position_x=0.098, position_y=-0.05, anchor_x=0.0, rotation=-25)
    line.label = "Trait"
    label = add_layer(project, GraphicType.TEXT, at=0.0, duration=4.0)
    for field, value in (("text", "Détail"), ("font_size", 36), ("autosize", True), ("align_h", "left")):
        update_graphic(label, field, value)
    label.transform = ClipTransform(position_x=0.22, position_y=-0.11, anchor_x=0.0)
    label.transform_keyframes = [_kf("opacity", 0.3, 0.0), _kf("opacity", 0.7, 1.0)]
    label.label = "Libellé"
    for child in (ring, line, label):
        set_parent(project, child.id, controller.id, keep_visual=False)
    return [controller.id, ring.id, line.id, label.id]


def _tracking_reveal(project: Project) -> list[str]:
    title = add_layer(project, GraphicType.TEXT, at=0.0, duration=4.0)
    update_graphic(title, "text", "RÉVÉLATION")
    update_graphic(title, "font_size", 64)
    update_graphic(title, "box_text", False)
    title.animation = [
        Keyframe("graphic.tracking", 0.0, 40.0, InterpolationType.EASE_OUT),
        Keyframe("graphic.tracking", 1.5, 4.0),
    ]
    title.transform_keyframes = [_kf("opacity", 0.0, 0.0), _kf("opacity", 0.8, 1.0)]
    title.label = "Titre espacé"
    return [title.id]


_BUILTINS: tuple[tuple[str, str, Callable[[Project], list[str]]], ...] = (
    ("Titre simple", "Titres", _simple_title),
    ("Titre encadré", "Titres", _boxed_title),
    ("Titre espacé animé", "Titres", _tracking_reveal),
    ("Lower third", "Habillage", _lower_third),
    ("Call-out", "Annotations", _callout),
    ("Titre TikTok", "Vertical", _tiktok_title),
    ("Mot en couleur", "Vertical", _highlighted_word),
    ("Sous-titre karaoké", "Vertical", _karaoke_caption),
)


def builtin_presets(width: int = 1920, height: int = 1080) -> list[MographPreset]:
    """Presets intégrés construits sur une toile ``width × height`` (celle du projet qui les reçoit)."""
    presets = []
    for name, category, build in _BUILTINS:
        project = _scratch(width, height)
        ids = build(project)
        presets.append(MographPreset(name, category, copy_layers(project, ids), builtin=True))
    return presets


# ---------------------------------------------------------------------------
# Presets utilisateur
# ---------------------------------------------------------------------------


def presets_directory(settings_dir=None) -> Path:
    return user_config_dir(settings_dir) / "mograph_presets"


def _slug(name: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "-", name.strip()).strip("-").lower()
    return text or "preset"


def clipboard_to_dict(clipboard: LayerClipboard) -> dict:
    return {
        "clips": list(clipboard.clips), "assets": list(clipboard.assets),
        "track_ids": list(clipboard.track_ids), "sequence_id": clipboard.sequence_id,
        "base_time": clipboard.base_time, "canvas": list(clipboard.canvas),
    }


def clipboard_from_dict(raw: dict) -> LayerClipboard:
    clips = tuple(c for c in raw.get("clips", []) if isinstance(c, dict))
    track_ids = tuple(str(t) for t in raw.get("track_ids", []))
    if len(track_ids) != len(clips):
        track_ids = ("",) * len(clips)
    return LayerClipboard(
        clips=clips, assets=tuple(a for a in raw.get("assets", []) if isinstance(a, dict)),
        track_ids=track_ids, sequence_id="", base_time=float(raw.get("base_time", 0.0)),
        canvas=tuple(int(v) for v in (raw.get("canvas") or (0, 0))[:2]) or (0, 0),
    )


def save_preset(project: Project, clip_ids: list[str], name: str, *, category: str = "Mes presets",
                settings_dir=None) -> MographPreset:
    """Enregistre les calques (un groupe emporte son contenu) comme preset utilisateur."""
    clipboard = copy_layers(project, clip_ids)
    directory = presets_directory(settings_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{_slug(name)}.json"
    payload = {
        "format": PRESET_FORMAT, "version": PRESET_VERSION, "name": name, "category": category,
        "layers": clipboard_to_dict(clipboard),
    }
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return MographPreset(name, category, clipboard, path=str(target))


def load_user_presets(settings_dir=None) -> list[MographPreset]:
    directory = presets_directory(settings_dir)
    presets = []
    if not directory.is_dir():
        return presets
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("format") != PRESET_FORMAT:
                continue
            presets.append(MographPreset(
                str(raw.get("name") or path.stem), str(raw.get("category") or "Mes presets"),
                clipboard_from_dict(raw.get("layers") or {}), path=str(path),
            ))
        except (OSError, ValueError, TypeError, AttributeError):
            continue  # un fichier illisible n'empêche pas les autres
    return presets


def delete_user_preset(preset: MographPreset) -> None:
    if preset.builtin or not preset.path:
        raise ValueError("Un preset intégré ne peut pas être supprimé.")
    Path(preset.path).unlink(missing_ok=True)


def all_presets(settings_dir=None) -> list[MographPreset]:
    return [*builtin_presets(), *load_user_presets(settings_dir)]


def apply_preset(project: Project, preset: MographPreset, *, at: float) -> list[Clip]:
    """Ajoute les calques du preset à ``at`` (nouveaux identifiants) ; un preset intégré est reconstruit sur la toile
    du projet (un bandeau 16:9 sortirait d'un cadre vertical)."""
    if preset.builtin:
        preset = next((item for item in builtin_presets(project.width, project.height) if item.name == preset.name),
                      preset)
    return paste_layers(project, preset.clipboard, at=at)


__all__ = [
    "MographPreset", "PRESET_FORMAT", "all_presets", "apply_preset", "builtin_presets",
    "clipboard_from_dict", "clipboard_to_dict", "delete_user_preset", "load_user_presets",
    "presets_directory", "save_preset",
]
