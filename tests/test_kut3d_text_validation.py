"""Texte 3D : bornes et refus (texte, police, dimensions, biseau, plafonds de triangles), tous AVANT de générer quoi que ce soit."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from kut3d import Text3D, primitive_instance, primitive_model
from kut3d import primitives as prim
from kut3d import text_rules
from kut3d.primitive_mesh import primitive_arrays


def _refused(match: str, **kw: Any) -> None:
    with pytest.raises(ValueError, match=match):
        Text3D(**kw)


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("", "aucun caractère visible"),
        ("   ", "aucun caractère visible"),
        ("\n \n", "aucun caractère visible"),
        ("a\tb", r"U\+0009"),
        ("a\rb", r"U\+000D"),
        ("a\x00b", r"U\+0000"),
        ("a​b", r"U\+200B"),  # espace de largeur nulle
        ("a­b", r"U\+00AD"),  # trait d'union conditionnel
        ("é", r"U\+0301"),  # accent combinant : écrire « é » en un caractère
        ("😀", r"U\+1F600"),
        ("a🙂", r"U\+1F642"),
        ("中文", r"U\+4E2D"),
        ("مرحبا", r"U\+0645"),
        ("नमस्ते", r"U\+0928"),
        ("שלום", r"U\+05E9"),
        ("สวัสดี", r"U\+0E2A"),
        ("", r"U\+E000"),  # zone d'usage privé
        ("\ud800", r"U\+D800"),  # demi-paire de substitution isolée
        ("x" * 41, "plus que le maximum"),
    ],
)
def test_texts_that_cannot_be_extruded_are_refused_with_their_code(
    text: str, match: str
) -> None:
    _refused(match, text=text)


def test_texts_at_the_limit_and_with_supported_scripts_are_accepted() -> None:
    assert Text3D("x" * text_rules.MAX_TEXT_CHARS).text == "x" * 40
    for text in (
        "Café ÀÉÎÕÜ ñ ç ß",
        "Привет мир",
        "Αθήνα",
        "1 234,56 € £ $",
        "« guillemets » – tiret — long",
        "a\nb\nc",
    ):
        assert Text3D(text).text == text


def test_the_refusal_never_copies_a_huge_text() -> None:
    with pytest.raises(ValueError) as info:
        Text3D("x" * 5_000_000)
    assert len(str(info.value)) < 200


@pytest.mark.parametrize("bad", [None, 3, b"abc", ["a"], True])
def test_text_must_be_a_string(bad: Any) -> None:
    _refused("un texte est attendu", text=bad)


@pytest.mark.parametrize("name", ["size", "depth"])
@pytest.mark.parametrize(
    "bad", [0, 0.0, -1.0, math.nan, math.inf, -math.inf, 1e-9, 1e9, True, "1", None]
)
def test_size_and_depth_must_be_finite_and_in_range(name: str, bad: Any) -> None:
    _refused(name, **{name: bad})


@pytest.mark.parametrize("bad", [-0.01, math.nan, math.inf, True, "0.01", None, 1e-9])
def test_bevel_must_be_zero_or_a_small_positive_number(bad: Any) -> None:
    _refused("bevel", bevel=bad)


def test_bevel_is_bounded_by_the_depth_and_by_the_size() -> None:
    assert (
        Text3D(size=1.0, depth=1.0, bevel=0.01).bevel == 0.01
    )  # exactement la borne de taille
    _refused("bevel", size=1.0, depth=1.0, bevel=0.0101)
    _refused("bevel", size=10.0, depth=0.02, bevel=0.0101)  # moitié de la profondeur
    assert Text3D(size=10.0, depth=0.02, bevel=0.01).bevel == 0.01
    # le message dit la borne réelle : on n'affiche jamais une valeur que la validation refuserait
    with pytest.raises(ValueError, match=r"au plus 0\.01 ") as info:
        Text3D(size=1.0, depth=1.0, bevel=0.5)
    assert "1%" in str(info.value)


@pytest.mark.parametrize("bad", ["", "middle", "LEFT", None, 1, "left "])
def test_align_is_one_of_three_words(bad: Any) -> None:
    _refused("align", align=bad)


@pytest.mark.parametrize(
    "bad", ["", "  ", " Anton", "Anton\n", "x" * 65, None, 3, "A\x00B"]
)
def test_font_must_be_a_plain_family_name(bad: Any) -> None:
    _refused("font", font=bad)


def test_a_font_name_cannot_be_a_path() -> None:
    spec = Text3D(font="../../etc/passwd")  # nom accepté par la description...
    with pytest.raises(
        ValueError, match="introuvable"
    ):  # ... mais jamais lu comme un fichier
        primitive_arrays(spec)


def test_defaults_are_valid_and_pass_the_same_checks_as_written_values() -> None:
    d = Text3D()
    assert (d.text, d.font, d.size, d.depth, d.bevel, d.align) == (
        "Texte",
        "Anton",
        1.0,
        0.2,
        0.0,
        "center",
    )
    assert Text3D(**d.params()) == d  # ce que le modèle écrit se relit à l'identique
    assert d.params() == {
        "text": "Texte",
        "font": "Anton",
        "size": 1.0,
        "depth": 0.2,
        "bevel": 0.0,
        "align": "center",
    }


def test_integers_are_accepted_as_numbers_and_stored_as_floats() -> None:
    spec = Text3D(size=2, depth=1, bevel=0)
    assert (spec.size, spec.depth, spec.bevel) == (2.0, 1.0, 0.0)
    assert all(isinstance(getattr(spec, n), float) for n in ("size", "depth", "bevel"))


def test_the_description_is_immutable_and_hashable() -> None:
    spec = Text3D("A")
    with pytest.raises(AttributeError):
        spec.text = "B"  # type: ignore[misc]
    assert hash(spec) == hash(Text3D("A")) and spec != Text3D("B")


# --- plafonds calculés avant de générer -----------------------------------------------------------------


def test_the_triangle_bound_is_computed_without_opening_a_font(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("la description ne doit pas ouvrir de police")

    monkeypatch.setattr("kut3d.text_font._face", boom)
    spec = Text3D("x" * 40, bevel=0.01)
    assert spec.triangle_count == 40 * text_rules.glyph_triangle_bound(True)
    assert spec.triangle_count <= prim.MAX_PRIMITIVE_TRIANGLES
    assert Text3D("a b\nc").triangle_count == 3 * text_rules.glyph_triangle_bound(
        False
    )  # espaces et sauts : aucun triangle
    assert spec.vertex_count > 0


def test_the_worst_case_text_fits_the_per_primitive_cap() -> None:
    worst = Text3D("W" * text_rules.MAX_TEXT_CHARS, bevel=0.01)
    assert worst.triangle_count <= prim.MAX_PRIMITIVE_TRIANGLES
    assert (
        9 * worst.triangle_count > prim.MAX_SCENE_PRIMITIVE_TRIANGLES
    )  # le plafond de fichier se remplit vite


def test_a_glyph_that_is_too_detailed_even_when_decimated_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kut3d import text_font

    monkeypatch.setattr(text_font, "MAX_GLYPH_POINTS", 3)
    text_font.glyph.cache_clear()
    try:
        with pytest.raises(ValueError, match="trop détaillé"):
            primitive_arrays(Text3D("O"))
    finally:
        text_font.glyph.cache_clear()


def test_a_character_missing_from_the_font_is_refused_never_replaced() -> None:
    with pytest.raises(ValueError, match=r"pas de glyphe pour U\+03B1"):
        primitive_arrays(
            Text3D("α")
        )  # grec : accepté par les règles, mais Anton ne l'a pas
    # une famille inconnue n'est pas remplacée en silence par la police du système
    with pytest.raises(ValueError, match="introuvable sur cette machine"):
        primitive_arrays(Text3D("A", font="Police Qui N'Existe Pas"))


def test_a_bevel_the_outline_cannot_hold_is_refused_with_the_character() -> None:
    # limite connue : le contour fusionné du « e » de Saira Black a un interstice plus fin que le biseau le plus petit
    # (voir docs/kut3d-scene.md) ; le refus nomme le caractère et ne change aucune valeur
    spec = Text3D(
        "e", font="Saira ExtraCondensed Black", size=1.0, depth=1.0, bevel=0.002
    )
    with pytest.raises(ValueError, match=r"biseau .* « e »"):
        primitive_arrays(spec)
    assert (
        len(primitive_arrays(Text3D("e", font="Saira ExtraCondensed Black"))[3]) > 0
    )  # sans biseau : aucun souci
    assert (
        len(primitive_arrays(Text3D("e", size=1.0, depth=1.0, bevel=0.01))[3]) > 0
    )  # Anton, biseau maximal


def test_bevel_and_text_defaults_never_collide_with_a_forbidden_shape() -> None:
    # le modèle généré porte le genre « text », jamais « angle-N » ni un nom corrigé en silence
    model = primitive_model(Text3D("A"))
    assert (
        model.primitive == Text3D("A")
        and model.nodes[0].name == "text"
        and model.meshes[0].name == "text"
    )
    inst = primitive_instance("text", text="A", name="titre")
    assert inst.name == "titre" and inst.model.primitive == Text3D("A")


def test_unknown_parameters_of_a_text_are_refused_in_french() -> None:
    with pytest.raises(ValueError, match="paramètres invalides pour « text »"):
        primitive_instance("text", texte="A")
    with pytest.raises(ValueError, match="des paramètres nommés ne s'ajoutent"):
        primitive_instance(Text3D("A"), size=2.0)


def test_the_text_kind_is_registered_and_old_kinds_are_untouched() -> None:
    assert list(prim.PRIMITIVE_KINDS) == [
        "cube",
        "sphere",
        "plane",
        "cylinder",
        "cone",
        "torus",
        "text",
    ]
    assert prim.Cube().params() == {"width": 1.0, "height": 1.0, "depth": 1.0}


def test_arrays_of_two_equal_descriptions_are_the_same_values() -> None:
    a, b = primitive_arrays(Text3D("Kut")), primitive_arrays(Text3D("Kut"))
    assert all(np.array_equal(x, y) for x, y in zip(a, b, strict=True))


def test_a_family_that_qt_would_replace_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une famille annoncée installée mais que Qt remplace par une autre à l'ouverture : refusée, jamais substituée en silence."""
    from PySide6.QtGui import QFontDatabase

    from kut3d import text_font

    real = QFontDatabase.families
    monkeypatch.setattr(
        QFontDatabase,
        "families",
        staticmethod(lambda *a, **k: [*real(*a, **k), "Fantome Sans"]),
    )
    monkeypatch.setattr(text_font, "_FACES", {})
    text_font.glyph.cache_clear()
    try:
        with pytest.raises(ValueError, match="remplacée par Qt"):
            primitive_arrays(Text3D("A", font="Fantome Sans"))
    finally:
        text_font.glyph.cache_clear()


@pytest.mark.parametrize(
    ("char", "category"),
    [
        (" ", "Zl"),
        (" ", "Zp"),
        (" ", "Zs"),
        (" ", "Zs"),
        ("　", "Zs"),
        (" ", "Zs"),
        ("\t", "Cc"),
        ("\x85", "Cc"),
        ("​", "Cf"),
        ("­", "Cf"),
        ("\ud800", "Cs"),
        ("", "Co"),
        ("͸", "Cn"),
        ("́", "Mn"),
        ("ः", "Mc"),
        ("⃝", "Me"),
    ],
)
def test_every_invisible_separator_control_or_combining_category_is_refused(
    char: str, category: str
) -> None:
    import unicodedata

    assert unicodedata.category(char) == category
    with pytest.raises(ValueError, match=rf"U\+{ord(char):04X} \({category}\)"):
        Text3D("a" + char + "b")


def test_only_the_plain_space_the_nbsp_and_newline_are_blank() -> None:
    assert Text3D("a b c\nd").text == "a b c\nd"
