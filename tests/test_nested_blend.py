"""Un mode de fusion dans une séquence imbriquée se mélange avec ce qui s'y trouve, pas avec le noir du vide.

Le cadre d'une séquence imbriquée est transparent là où elle est vide. Un calque en mode Produit (Multiply) posé sur
ce vide était multiplié par du noir : il sortait noir au lieu de s'afficher comme en mode Normal. Là où la séquence
contient de la vidéo, le mélange reste celui du mode choisi.
"""

from __future__ import annotations

import pytest
from test_sequences_export import (  # noqa: F401 - fixture ``media`` et aides de rendu partagées
    H,
    W,
    _close,
    _pixel,
    _project,
    _render,
    media,
)

from core.blend_modes import BlendMode
from core.compositing import Compositing
from core.export_engine import ExportEngine
from core.graphics import add_graphic_clip, update_graphic
from core.project_model import Clip
from core.render_plan import build_render_plan
from core.sequences import insert_sequence_clip
from core.visual_effects import ClipTransform

pytestmark = pytest.mark.usefixtures("qapp")

CYAN = (0, 255, 255)
OVER_EMPTY = (32, 18)                       # sous le calque décalé, hors de la vidéo imbriquée (transparent)
OVER_VIDEO = (W // 2, H // 2)               # sous le calque centré, sur le rouge imbriqué


def _build(media, mode: BlendMode, *, in_sequence: str, offset: float):
    """Parent : bleu + séquence ``intro`` (rouge réduit de moitié) ; un calque cyan 40×40 dans ``in_sequence``."""
    project = _project(media, inner_scale=0.5)
    project.tracks[0].clips.append(Clip("b", "blue", "V1", 0.0, 0.0, 4.0))
    insert_sequence_clip(project, "intro", "V2", 0.0)
    project.active_sequence_id = in_sequence
    shape = add_graphic_clip(project, "shape", timeline_start=0, duration=3, shape="rectangle")
    update_graphic(shape, "width", 40)
    update_graphic(shape, "height", 40)
    update_graphic(shape, "fill_color", "#00FFFF")
    shape.transform = ClipTransform(position_x=offset, position_y=offset)
    shape.compositing = Compositing(blend_mode=mode)
    project.active_sequence_id = "main"
    return project


def _render_to(project, tmp_path, name: str):
    out = tmp_path / f"{name}.mp4"
    _render(build_render_plan(project), out)
    return out


def test_a_multiply_layer_over_an_empty_nested_area_shows_its_own_colour(media, tmp_path):
    normal = _render_to(_build(media, BlendMode.NORMAL, in_sequence="intro", offset=-0.3), tmp_path, "normal")
    multiply = _render_to(_build(media, BlendMode.MULTIPLY, in_sequence="intro", offset=-0.3), tmp_path, "multiply")
    assert _close(_pixel(normal, 1.0, *OVER_EMPTY), CYAN, 24)
    assert _close(_pixel(multiply, 1.0, *OVER_EMPTY), _pixel(normal, 1.0, *OVER_EMPTY), 24), \
        "rien à multiplier : le calque doit s'afficher comme en mode Normal, pas en noir"


def test_a_multiply_layer_over_nested_video_still_multiplies(media, tmp_path):
    multiply = _render_to(_build(media, BlendMode.MULTIPLY, in_sequence="intro", offset=0.0), tmp_path, "over_video")
    assert _close(_pixel(multiply, 1.0, *OVER_VIDEO), (0, 0, 0), 24)         # cyan × rouge = noir


def test_the_edge_between_video_and_empty_area_switches_from_one_behaviour_to_the_other(media, tmp_path):
    """Un calque à cheval sur le bord de la vidéo imbriquée : mélangé d'un côté, tel quel de l'autre."""
    project = _build(media, BlendMode.MULTIPLY, in_sequence="intro", offset=0.0)
    shape = next(c for t in project.get_sequence("intro").tracks for c in t.clips if getattr(c, "graphic", None))
    shape.transform = ClipTransform(position_x=-0.25, position_y=0.0)         # centré en x = 40 : à cheval sur x = 40
    out = _render_to(project, tmp_path, "edge")
    assert _close(_pixel(out, 1.0, 30, H // 2), CYAN, 24)                      # côté vide
    assert _close(_pixel(out, 1.0, 50, H // 2), (0, 0, 0), 24)                 # côté vidéo


def test_a_blend_mode_in_the_parent_sequence_is_unchanged(media, tmp_path):
    """Dans la séquence racine le fond est opaque : même graphe qu'avant, même résultat (pas de maskedmerge)."""
    project = _build(media, BlendMode.MULTIPLY, in_sequence="main", offset=0.0)
    graph = ExportEngine._build_filter_complex(build_render_plan(project), W, H, 25, None)[0]
    assert "maskedmerge" not in graph
    out = _render_to(project, tmp_path, "root")
    assert _close(_pixel(out, 1.0, *OVER_VIDEO), (0, 0, 0), 24)               # cyan × (rouge nested sur bleu)


def test_only_nested_graphs_use_the_transparent_backdrop_formula(media):
    project = _build(media, BlendMode.MULTIPLY, in_sequence="intro", offset=0.0)
    graph = ExportEngine._build_filter_complex(build_render_plan(project), W, H, 25, None)[0]
    assert "maskedmerge" in graph
