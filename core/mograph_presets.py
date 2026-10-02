"""Bibliothèque légère de presets motion graphics.

Un preset est un petit ensemble de calques (texte, formes, groupe,
contrôleur…) avec leurs transforms, masques, effets et animations : la même
forme JSON que le presse-papiers des calques
(:class:`core.mograph_layers.LayerClipboard`). L'appliquer revient à
**coller** ces calques à la tête de lecture.

- presets intégrés : construits par du code (toujours à jour avec le modèle) ;
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


def _scratch() -> Project:
    return Project("preset", 1920, 1080, 30.0)


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
    bar = add_layer(project, GraphicType.SHAPE, at=0.0, duration=5.0, shape=ShapeKind.ROUNDED_RECTANGLE)
    for name, value in (("width", 720), ("height", 140), ("corner_radius", 16.0), ("fill_color", "#36E6C3E6")):
        update_graphic(bar, name, value)
    bar.label = "Bandeau"
    name = add_layer(project, GraphicType.TEXT, at=0.0, duration=5.0)
    for field, value in (
        ("text", "Prénom Nom"), ("font_size", 40), ("bold", True), ("align_h", "left"),
        ("width", 660), ("height", 60), ("fill_color", "#061514"),
    ):
        update_graphic(name, field, value)
    name.transform = ClipTransform(position_y=-0.012)
    name.label = "Nom"
    role = add_layer(project, GraphicType.TEXT, at=0.0, duration=5.0)
    for field, value in (
        ("text", "Fonction"), ("font_size", 26), ("align_h", "left"), ("width", 660), ("height", 40),
        ("fill_color", "#0B2B27"),
    ):
        update_graphic(role, field, value)
    role.transform = ClipTransform(position_y=0.03)
    role.label = "Fonction"
    group = group_layers(project, [bar.id, name.id, role.id], name="Lower third")
    group.transform = ClipTransform(position_x=-0.28, position_y=0.33)
    group.transform_keyframes = [
        _kf("position_x", 0.0, -0.75), _kf("position_x", 0.6, -0.28),
        _kf("opacity", 0.0, 0.0), _kf("opacity", 0.3, 1.0),
    ]
    return [group.id]


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
)


def builtin_presets() -> list[MographPreset]:
    presets = []
    for name, category, build in _BUILTINS:
        project = _scratch()
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
    """Ajoute les calques du preset à ``at`` (nouveaux identifiants)."""
    return paste_layers(project, preset.clipboard, at=at)


__all__ = [
    "MographPreset", "PRESET_FORMAT", "all_presets", "apply_preset", "builtin_presets",
    "clipboard_from_dict", "clipboard_to_dict", "delete_user_preset", "load_user_presets",
    "presets_directory", "save_preset",
]
