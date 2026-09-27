"""Tests unitaires du module ``core.visual_effects``.

Ce fichier couvre l'intégralité des exigences de la tâche 13 :

- valeurs par défaut ;
- validation des bornes ;
- tri, remplacement et déduplication des images-clés ;
- interpolation linéaire et valeurs de base avant / après ;
- génération d'expressions FFmpeg (sans keyframe / une keyframe / segments multiples, sans notation scientifique) ;
- absence d'import PySide6 ou FFmpeg.
"""

from __future__ import annotations

import re

import pytest

from core.visual_effects import (
    ANIMATABLE_PROPERTIES,
    ClipTransform,
    EvaluatedTransform,
    TransformKeyframe,
    build_ffmpeg_expression,
    escape_filter_complex_commas,
    evaluate_transform,
)


# ---------------------------------------------------------------------------
# Valeurs par défaut et validation
# ---------------------------------------------------------------------------


def test_default_clip_transform_is_identity():
    transform = ClipTransform()
    assert transform.position_x == 0.0
    assert transform.position_y == 0.0
    assert transform.scale == 1.0
    assert transform.rotation == 0.0
    assert transform.opacity == 1.0


def test_clip_transform_rejects_out_of_bounds_values():
    with pytest.raises(ValueError):
        ClipTransform(scale=0.0)
    with pytest.raises(ValueError):
        ClipTransform(scale=11.0)
    with pytest.raises(ValueError):
        ClipTransform(rotation=-3601.0)
    with pytest.raises(ValueError):
        ClipTransform(rotation=3601.0)
    with pytest.raises(ValueError):
        ClipTransform(opacity=-0.01)
    with pytest.raises(ValueError):
        ClipTransform(opacity=1.01)
    with pytest.raises(ValueError):
        ClipTransform(position_x=4.5)


def test_clip_transform_accepts_boundary_values():
    ClipTransform(scale=0.05)
    ClipTransform(scale=10.0)
    ClipTransform(rotation=-3600.0)
    ClipTransform(rotation=3600.0)
    ClipTransform(opacity=0.0)
    ClipTransform(opacity=1.0)
    ClipTransform(position_x=-4.0)
    ClipTransform(position_x=4.0)


def test_keyframe_validates_property_name():
    with pytest.raises(ValueError):
        TransformKeyframe(property_name="bogus", time_seconds=0.0, value=1.0)


def test_keyframe_validates_negative_time():
    with pytest.raises(ValueError):
        TransformKeyframe(property_name="scale", time_seconds=-0.1, value=1.0)


def test_keyframe_validates_value_bounds():
    with pytest.raises(ValueError):
        TransformKeyframe(property_name="scale", time_seconds=0.0, value=20.0)
    with pytest.raises(ValueError):
        TransformKeyframe(property_name="opacity", time_seconds=0.0, value=1.5)


def test_clip_transform_is_frozen():
    transform = ClipTransform()
    with pytest.raises(Exception):
        transform.scale = 0.5  # type: ignore[misc]


def test_animatable_properties_are_complete():
    expected = {"position_x", "position_y", "scale", "rotation", "opacity"}
    assert set(ANIMATABLE_PROPERTIES) == expected


# ---------------------------------------------------------------------------
# Tri, remplacement, déduplication
# ---------------------------------------------------------------------------


def test_keyframes_are_sorted_by_property_then_time():
    kfs = [
        TransformKeyframe(property_name="scale", time_seconds=2.0, value=0.5),
        TransformKeyframe(property_name="position_x", time_seconds=0.0, value=0.1),
        TransformKeyframe(property_name="scale", time_seconds=1.0, value=2.0),
    ]
    evaluated = evaluate_transform(ClipTransform(), kfs, clip_local_time=0.5)
    assert isinstance(evaluated, EvaluatedTransform)
    # L'évaluation déduplique et trie silencieusement : on teste
    # uniquement le résultat à des instants particuliers.
    at_t1 = evaluate_transform(ClipTransform(), kfs, clip_local_time=1.0)
    assert at_t1.scale == pytest.approx(2.0)
    at_t1_5 = evaluate_transform(ClipTransform(), kfs, clip_local_time=1.5)
    # Interpolation entre scale(1.0→2.0) et scale(2.0→0.5).
    assert 0.5 < at_t1_5.scale < 2.0


def test_replacing_keyframe_at_same_instant_keeps_latest_value():
    kfs = [
        TransformKeyframe(property_name="scale", time_seconds=1.0, value=2.0),
        TransformKeyframe(property_name="scale", time_seconds=1.0, value=0.7),
    ]
    result = evaluate_transform(ClipTransform(), kfs, clip_local_time=1.0)
    assert result.scale == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# Interpolation
# ---------------------------------------------------------------------------


def test_evaluate_uses_base_before_first_keyframe():
    transform = ClipTransform(scale=1.0)
    kfs = [TransformKeyframe(property_name="scale", time_seconds=2.0, value=0.5)]
    # Avant la première keyframe (t < 2.0) : valeur de base 1.0.
    result = evaluate_transform(transform, kfs, clip_local_time=0.5)
    assert result.scale == pytest.approx(1.0)
    # À exactement t == 2.0 : la keyframe s'applique (valeur 0.5).
    result_at_first = evaluate_transform(transform, kfs, clip_local_time=2.0)
    assert result_at_first.scale == pytest.approx(0.5)


def test_evaluate_uses_last_keyframe_after_final_time():
    transform = ClipTransform(scale=1.0)
    kfs = [
        TransformKeyframe(property_name="scale", time_seconds=1.0, value=0.5),
        TransformKeyframe(property_name="scale", time_seconds=3.0, value=0.8),
    ]
    result = evaluate_transform(transform, kfs, clip_local_time=10.0)
    assert result.scale == pytest.approx(0.8)


def test_evaluate_linear_interpolation_midpoint():
    transform = ClipTransform(scale=1.0)
    kfs = [
        TransformKeyframe(property_name="scale", time_seconds=0.0, value=0.5),
        TransformKeyframe(property_name="scale", time_seconds=2.0, value=1.5),
    ]
    result = evaluate_transform(transform, kfs, clip_local_time=1.0)
    assert result.scale == pytest.approx(1.0)


def test_evaluate_position_x_interpolation():
    transform = ClipTransform(position_x=0.0)
    kfs = [
        TransformKeyframe(property_name="position_x", time_seconds=0.0, value=0.0),
        TransformKeyframe(property_name="position_x", time_seconds=2.0, value=2.0),
    ]
    result = evaluate_transform(transform, kfs, clip_local_time=1.0)
    assert result.position_x == pytest.approx(1.0)


def test_evaluate_rotation_interpolation():
    transform = ClipTransform(rotation=0.0)
    kfs = [
        TransformKeyframe(property_name="rotation", time_seconds=0.0, value=0.0),
        TransformKeyframe(property_name="rotation", time_seconds=4.0, value=360.0),
    ]
    result = evaluate_transform(transform, kfs, clip_local_time=2.0)
    assert result.rotation == pytest.approx(180.0)


def test_evaluate_opacity_interpolation():
    transform = ClipTransform(opacity=1.0)
    kfs = [
        TransformKeyframe(property_name="opacity", time_seconds=0.0, value=1.0),
        TransformKeyframe(property_name="opacity", time_seconds=4.0, value=0.0),
    ]
    result = evaluate_transform(transform, kfs, clip_local_time=2.0)
    assert result.opacity == pytest.approx(0.5)


def test_evaluate_clamps_clip_local_time_above_duration():
    transform = ClipTransform(scale=1.0)
    kfs = [TransformKeyframe(property_name="scale", time_seconds=1.0, value=0.5)]
    result = evaluate_transform(transform, kfs, clip_local_time=99.0, clip_duration=2.0)
    # Clampé à 2s : on est au-delà de la dernière keyframe → 0.5.
    assert result.scale == pytest.approx(0.5)


def test_evaluate_clamps_clip_local_time_below_zero():
    transform = ClipTransform(scale=0.5)
    kfs = [TransformKeyframe(property_name="scale", time_seconds=1.0, value=2.0)]
    result = evaluate_transform(transform, kfs, clip_local_time=-1.0, clip_duration=2.0)
    # Clampé à 0s : strictement avant la première keyframe (t=1.0) →
    # valeur de base 0.5.
    assert result.scale == pytest.approx(0.5)


def test_evaluate_drops_keyframes_beyond_duration():
    transform = ClipTransform(scale=1.0)
    kfs = [
        TransformKeyframe(property_name="scale", time_seconds=5.0, value=0.5),
        TransformKeyframe(property_name="scale", time_seconds=20.0, value=0.2),
    ]
    result = evaluate_transform(transform, kfs, clip_local_time=3.0, clip_duration=4.0)
    # Les deux keyframes sont > clip_duration → retour à la base.
    assert result.scale == pytest.approx(1.0)


def test_evaluated_transform_as_transform_round_trip():
    base = ClipTransform()
    kfs = [
        TransformKeyframe(property_name="scale", time_seconds=1.0, value=0.5),
    ]
    evaluated = evaluate_transform(base, kfs, clip_local_time=1.0)
    rebuilt = evaluated.as_transform()
    # ``rebuilt`` doit avoir exactement les mêmes champs que ``evaluated``.
    assert rebuilt.position_x == evaluated.position_x
    assert rebuilt.position_y == evaluated.position_y
    assert rebuilt.scale == evaluated.scale
    assert rebuilt.rotation == evaluated.rotation
    assert rebuilt.opacity == evaluated.opacity


# ---------------------------------------------------------------------------
# Expression FFmpeg
# ---------------------------------------------------------------------------


def test_ffmpeg_expression_without_keyframes_is_constant():
    expr = build_ffmpeg_expression("scale", 1.5, [])
    assert expr == "1.5"


def test_ffmpeg_expression_with_single_keyframe():
    expr = build_ffmpeg_expression(
        "scale",
        1.0,
        [TransformKeyframe(property_name="scale", time_seconds=0.5, value=2.0)],
    )
    # Forme ``if(lt(T, t0), A, B)`` attendue.
    assert "lt(T,0.5)" in expr
    assert "if(" in expr
    assert "1.0" in expr
    assert "2" in expr


def test_ffmpeg_expression_with_multiple_segments():
    kfs = [
        TransformKeyframe(property_name="scale", time_seconds=0.0, value=1.0),
        TransformKeyframe(property_name="scale", time_seconds=1.0, value=2.0),
        TransformKeyframe(property_name="scale", time_seconds=2.0, value=0.5),
    ]
    expr = build_ffmpeg_expression("scale", 1.0, kfs)
    # Au moins deux ``if(lt(`` (un par segment).
    assert expr.count("if(lt(T,") >= 2
    # Interpolation linéaire présente : ``((T)-t)/span``.
    assert "((T)-" in expr
    # Une virgule par segment (entre les branches du ``if``).
    assert expr.count(",") >= 2


def test_ffmpeg_expression_no_scientific_notation():
    kfs = [
        TransformKeyframe(property_name="scale", time_seconds=0.000001, value=1.0),
        TransformKeyframe(property_name="scale", time_seconds=0.000002, value=2.0),
    ]
    expr = build_ffmpeg_expression("scale", 1.0, kfs)
    # Aucune notation scientifique.
    assert "e-" not in expr
    assert "e+" not in expr
    assert "if(" in expr


def test_ffmpeg_expression_filters_irrelevant_keyframes():
    kfs = [
        TransformKeyframe(property_name="opacity", time_seconds=0.0, value=0.5),
        TransformKeyframe(property_name="opacity", time_seconds=1.0, value=0.2),
    ]
    expr = build_ffmpeg_expression("scale", 1.0, kfs)
    # Aucune keyframe ``scale`` → valeur constante de base ``1.0``.
    assert expr == "1.0"


def test_ffmpeg_expression_balanced_parentheses():
    kfs = [
        TransformKeyframe(property_name="scale", time_seconds=0.0, value=1.0),
        TransformKeyframe(property_name="scale", time_seconds=1.0, value=2.0),
        TransformKeyframe(property_name="scale", time_seconds=2.0, value=0.5),
    ]
    expr = build_ffmpeg_expression("scale", 1.0, kfs)
    # Équilibre des parenthèses ouvrantes / fermantes.
    assert expr.count("(") == expr.count(")")


def test_ffmpeg_expression_position_uses_canvas_normalised():
    expr = build_ffmpeg_expression("position_x", 0.0, [])
    assert expr == "0"


def test_ffmpeg_expression_uses_time_var_for_referencing_t():
    expr = build_ffmpeg_expression(
        "scale",
        1.0,
        [TransformKeyframe(property_name="scale", time_seconds=1.0, value=2.0)],
    )
    # L'expression contient ``T`` (variable de temps standard FFmpeg).
    assert "T" in expr


def test_ffmpeg_expression_never_collides_with_filter_graph_commas():
    """La couche -filter_complex de FFmpeg sépare ses options par ``,``.

    Notre expression utilise la forme ``if(lt(T,t),A,B)``, qui
    contient nécessairement des virgules. Le consommateur doit donc
    échapper ces virgules via :func:`escape_filter_complex_commas`
    avant d'injecter l'expression dans la commande FFmpeg. Ce test
    vérifie que l'échappement est correct et idempotent.
    """
    kfs = [
        TransformKeyframe(property_name="scale", time_seconds=0.0, value=1.0),
        TransformKeyframe(property_name="scale", time_seconds=1.0, value=2.0),
        TransformKeyframe(property_name="scale", time_seconds=2.0, value=0.5),
    ]
    expr = build_ffmpeg_expression("scale", 1.0, kfs)
    # L'expression ``brute`` contient des virgules (top-level vs nested).
    assert "," in expr
    # L'échappement produit ``\\,`` à chaque virgule.
    escaped = escape_filter_complex_commas(expr)
    # Pas de virgules nues restantes.
    assert "," not in escaped.replace("\\,", "")
    # L'échappement est idempotent.
    assert escape_filter_complex_commas(escaped) == escaped


def test_escape_filter_complex_commas_preserves_existing_escapes():
    """Les ``\\,`` déjà présents sont préservés (pas de double échappement)."""
    # ``a\\,b`` représente déjà un échappement ; on ne veut pas de
    # ``a\\\\,b`` après re-échappement.
    out = escape_filter_complex_commas("a\\,b")
    assert out == "a\\,b"


# ---------------------------------------------------------------------------
# Conformité aux contraintes architecturales
# ---------------------------------------------------------------------------


def test_module_does_not_import_pyside6():
    """``visual_effects`` ne dépend pas de Qt."""
    import ast
    from pathlib import Path

    from core import visual_effects

    source = Path(visual_effects.__file__)
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("PySide6"), alias.name
                assert not alias.name.startswith("PyQt"), alias.name
        elif isinstance(node, ast.ImportFrom):
            assert node.module is None or not node.module.startswith("PySide6"), node.module
            assert node.module is None or not node.module.startswith("PyQt"), node.module


def test_module_does_not_subprocess_ffmpeg():
    """``visual_effects`` ne lance aucun sous-processus externe."""
    import ast
    from pathlib import Path

    from core import visual_effects

    source = Path(visual_effects.__file__)
    tree = ast.parse(source.read_text(encoding="utf-8"))
    forbidden = {"subprocess", "Popen"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden, alias.name
        elif isinstance(node, ast.ImportFrom):
            assert node.module not in forbidden, node.module


def test_realistic_animated_opacity_expression():
    """Cas d'usage réaliste : un fondu de 1.0 vers 0.0 entre 0s et 4s."""
    kfs = [
        TransformKeyframe(property_name="opacity", time_seconds=0.0, value=1.0),
        TransformKeyframe(property_name="opacity", time_seconds=4.0, value=0.0),
    ]
    expr = build_ffmpeg_expression("opacity", 1.0, kfs)
    # Doit contenir l'expression d'interpolation ``((T)-0)``.
    pattern = re.compile(r"\(\(T\)-0\)")
    assert pattern.search(expr)
    # L'expression brute contient au moins une virgule (de
    # ``if(lt(T,t),A,B)``).
    assert "," in expr
