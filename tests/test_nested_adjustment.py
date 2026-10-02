"""Un adjustment layer dans une séquence imbriquée ne doit pas masquer la piste parente.

Le cadre d'une séquence imbriquée est transparent là où elle est vide. L'ajustement (effets opaques,
noir et blanc, étalonnage…) rendait ces zones opaques : le parent, en dessous, disparaissait derrière du noir.
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

from core.effects_model import EffectType, create_effect
from core.graphics import add_graphic_clip
from core.project_model import Clip
from core.render_plan import build_render_plan
from core.sequences import insert_sequence_clip
from core.visual_effects import ClipTransform

pytestmark = pytest.mark.usefixtures("qapp")

CORNER = (5, 5)                 # hors du rouge (réduit de moitié) : transparent dans la séquence imbriquée
CENTER = (W // 2, H // 2)       # sur le rouge de la séquence imbriquée
BLUE = (0, 0, 255)
RED_GRAY = (74, 74, 74)         # rouge pur passé en noir et blanc (luma BT.709)


def _build(media, *, in_sequence: str, opacity: float = 1.0):
    """Parent : bleu + séquence ``intro`` (rouge réduit de moitié) ; un ajustement N&B dans ``in_sequence``."""
    project = _project(media, inner_scale=0.5)
    project.tracks[0].clips.append(Clip("b", "blue", "V1", 0.0, 0.0, 4.0))
    insert_sequence_clip(project, "intro", "V2", 0.0)
    project.active_sequence_id = in_sequence
    adjustment = add_graphic_clip(project, "adjustment", timeline_start=0, duration=3)
    adjustment.effects = [create_effect(EffectType.BLACK_AND_WHITE)]
    adjustment.transform = ClipTransform(opacity=opacity)
    project.active_sequence_id = "main"
    return project


def _frame(project, tmp_path, name: str):
    out = tmp_path / f"{name}.mp4"
    _render(build_render_plan(project), out)
    return out


def test_an_adjustment_inside_a_nested_sequence_keeps_the_parent_visible(media, tmp_path):
    out = _frame(_build(media, in_sequence="intro"), tmp_path, "inside")
    assert _close(_pixel(out, 1.0, *CORNER), BLUE, 12), "le parent doit rester visible autour du contenu imbriqué"
    assert _close(_pixel(out, 1.0, *CENTER), RED_GRAY, 12), "le contenu imbriqué doit être passé en noir et blanc"


def test_an_adjustment_inside_a_nested_sequence_honours_its_opacity(media, tmp_path):
    out = _frame(_build(media, in_sequence="intro", opacity=0.5), tmp_path, "half")
    assert _close(_pixel(out, 1.0, *CORNER), BLUE, 12)
    # Moitié rouge (255, 0, 0), moitié noir et blanc (74, 74, 74).
    assert _close(_pixel(out, 1.0, *CENTER), (165, 37, 37), 16)


def test_an_adjustment_in_the_parent_still_grades_everything_below(media, tmp_path):
    """Comportement inchangé : l'ajustement du parent agit sur toute la composition, nested compris."""
    out = _frame(_build(media, in_sequence="main"), tmp_path, "parent")
    assert _close(_pixel(out, 1.0, *CORNER), (29, 29, 29), 12)    # le bleu du parent, en noir et blanc
    assert _close(_pixel(out, 1.0, *CENTER), RED_GRAY, 12)


def test_the_root_graph_is_unchanged_and_only_nested_graphs_preserve_alpha(media):
    from core.export_engine import ExportEngine

    # Séquence racine seule : pas de multiplication d'alpha (le fond est opaque, rien ne change).
    root = _build(media, in_sequence="main")
    graph = ExportEngine._build_filter_complex(build_render_plan(root), W, H, 25, None)[0]
    assert "multiply" not in graph
    # La séquence imbriquée porte l'ajustement : son graphe, lui, préserve l'alpha du dessous.
    nested = _build(media, in_sequence="intro")
    graph = ExportEngine._build_filter_complex(build_render_plan(nested), W, H, 25, None)[0]
    assert "blend=all_mode=multiply" in graph
