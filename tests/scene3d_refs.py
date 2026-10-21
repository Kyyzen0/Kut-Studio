"""Trois projets de référence SANS calque 3D, à chemins fixes, pour figer l'empreinte d'un plan (lot B2b).

Leurs empreintes (:data:`REFERENCE_FINGERPRINTS`) ont été calculées sur le code d'avant le lot B2b : un plan sans 3D doit
garder exactement la même empreinte (pas d'incrément de ``RENDER_ENGINE_VERSION``, les segments d'aperçu déjà en cache restent valables).
"""

from __future__ import annotations

from core.blend_modes import BlendMode
from core.compositing import Compositing
from core.graphics import GraphicType, add_graphic_clip, update_graphic
from core.effects_model import ClipEffect, EffectType
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.sequences import create_sequence_from_selection
from core.visual_effects import TransformKeyframe


def _base(name: str) -> Project:
    asset = MediaAsset("m1", "/ref/a.mp4", "a", 20.0, 64, 36, 25.0, "video")
    clips = [Clip("a", "m1", "V1", 0.0, 0.0, 4.0), Clip("b", "m1", "V1", 4.0, 2.0, 6.0)]
    return Project(
        name,
        width=64,
        height=36,
        fps=25.0,
        media_assets=[asset],
        tracks=[Track("V1", "V1", "video", clips=clips)],
    )


def titles_project() -> Project:
    """Vidéo, titre, forme colorée et calque de lumière."""
    project = _base("titres")
    title = add_graphic_clip(
        project, GraphicType.TEXT, timeline_start=0.5, duration=3.0, clip_id="title"
    )
    update_graphic(title, "text", "Kut")
    shape = add_graphic_clip(
        project, GraphicType.SHAPE, timeline_start=1.0, duration=2.0, clip_id="shape"
    )
    update_graphic(shape, "fill_color", "#3060F0")
    add_graphic_clip(
        project, GraphicType.LIGHT, timeline_start=2.0, duration=2.0, clip_id="light"
    )
    return project


def nested_project() -> Project:
    """Une séquence imbriquée qui contient un titre, plus un aplat au-dessus."""
    project = _base("imbriquee")
    title = add_graphic_clip(
        project, GraphicType.TEXT, timeline_start=0.0, duration=3.0, clip_id="inner"
    )
    update_graphic(title, "text", "Dedans")
    create_sequence_from_selection(
        project,
        [title.id],
        "Imbriquée",
        sequence_id="seq_ref",
        nested_clip_id="nest_ref",
    )
    solid = add_graphic_clip(
        project, GraphicType.SOLID, timeline_start=5.0, duration=2.0, clip_id="solid"
    )
    update_graphic(solid, "fill_color", "#102030")
    return project


def effects_project() -> Project:
    """Images clés de transformation, effet de clip, fusion et calque d'ajustement."""
    project = _base("effets")
    clip = project.tracks[0].clips[0]
    clip.transform_keyframes = [
        TransformKeyframe(
            property_name="opacity", time_seconds=0.0, value=1.0, id="k1"
        ),
        TransformKeyframe(
            property_name="opacity", time_seconds=3.0, value=0.5, id="k2"
        ),
    ]
    clip.effects = [ClipEffect(id="fx1", type=EffectType.BLUR)]
    shape = add_graphic_clip(
        project, GraphicType.SHAPE, timeline_start=1.0, duration=2.0, clip_id="shape"
    )
    shape.compositing = Compositing(blend_mode=BlendMode.MULTIPLY)
    add_graphic_clip(
        project,
        GraphicType.ADJUSTMENT,
        timeline_start=2.0,
        duration=3.0,
        clip_id="adjust",
    )
    return project


PROJECTS = {
    "titles": titles_project,
    "nested": nested_project,
    "effects": effects_project,
}


def fingerprints() -> dict[str, str]:
    """Empreinte de chaque projet de référence, à une qualité et une cadence fixes."""
    from core.filter_graph import fingerprint_plan

    return {
        name: fingerprint_plan(
            build_render_plan(make()), width=64, height=36, fps=25.0, quality="standard"
        )
        for name, make in PROJECTS.items()
    }


REFERENCE_FINGERPRINTS = {
    "titles": "052794b17cc95d55c779bf0ded9e5fe61bbefc4a5028e7c0066806590705680a",
    "nested": "99d02e74d5235df0bc5dcdfda5601d0bdd5e4a183c2859651f8dcc68d7310892",
    "effects": "e9ec6ae98c131d1004ee4c171fda38764916d27ae97fd0b29a98a11640978c86",
}
"""Calculées avec ``fingerprints()`` sur le code de HEAD (cb493de, avant le lot B2b), dans une copie propre."""
