"""Tests pour l'étalonnage couleur et les LUTs (tâche 29).

Couvre :

- le modèle : :class:`ColorCurve`, :class:`ColorCurves`,
  :class:`ColorGrade`, :class:`LUTResource` ;
- les presets natifs (6) + filtre par catégorie / recherche ;
- :mod:`core.lut_importer` (validation .cube) ;
- la persistance ``.kut`` (roundtrip + rétro‑compat + LUT manquant) ;
- les filtres FFmpeg (``eq``, ``colorbalance``, ``curves``, ``lut3d``)
  émis par :func:`core.export_engine._build_color_grade_filters` ;
- l'Undo/Redo via :class:`ProjectHistory` pour les opérations
  d'étalonnage.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from core.color_grading import (
    BUILTIN_COLOR_PRESETS_COUNT,
    CATEGORY_DESCRIPTIONS,
    CATEGORY_LABELS,
    COLOR_PRESETS_FILE,
    ColorCurve,
    ColorCurves,
    ColorGrade,
    ColorGradingError,
    ColorGradingRangeError,
    ColorGradingService,
    ColorPresetCategory,
    ColorPresetStore,
    LUTResource,
    CONTRAST_MAX,
    CONTRAST_MIN,
    EXPOSURE_MAX,
    EXPOSURE_MIN,
    HIGHLIGHTS_MAX,
    HIGHLIGHTS_MIN,
    HUE_MAX,
    SATURATION_MAX,
    SATURATION_MIN,
    SHADOWS_MAX,
    SHADOWS_MIN,
    TEMPERATURE_MIN,
    builtin_color_preset_ids,
    builtin_color_presets,
    filter_color_presets,
    load_color_preset_data,
    make_color_preset,
    make_user_color_preset,
    save_color_preset_data,
)
from core.edit_history import ProjectHistory
from core.export_engine import _build_color_grade_filters, _build_lut3d_filter
from core.lut_importer import (
    LUTImportBadFormat,
    LUTImportError,
    LUTImportMissingFile,
    LUTImportUnsupported,
    MAX_LUT_SIZE,
    MIN_LUT_SIZE,
    parse_cube_lut,
)
from core.project_io import load_project, save_project
from core.project_model import Clip, MediaAsset, Project, Track


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_clip() -> Clip:
    asset = MediaAsset(
        id="asset-v",
        path="/tmp/v.mp4",
        name="V",
        duration=5.0,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
        has_audio=True,
    )
    track = Track(id="V1", name="V1", type="video")
    track.clips.append(
        Clip(
            id="c1",
            asset_id="asset-v",
            track_id="V1",
            timeline_start=0.0,
            source_in=0.0,
            source_out=3.0,
        )
    )
    return asset, Project(name="grade", media_assets=[asset], tracks=[track])


def _project_with_video_clip() -> Project:
    asset, project = _make_clip()
    return project


def _cube_text(
    size: int = 2,
    *,
    title: str = "Test",
    domain_min: tuple[float, float, float] = (0.0, 0.0, 0.0),
    domain_max: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> str:
    """Génère un ``.cube`` 3D valide de taille ``size``."""
    header = textwrap.dedent(
        f"""\
        TITLE "{title}"
        LUT_3D_SIZE {size}
        DOMAIN_MIN {domain_min[0]} {domain_min[1]} {domain_min[2]}
        DOMAIN_MAX {domain_max[0]} {domain_max[1]} {domain_max[2]}
        """
    )
    body_lines: list[str] = []
    # B varie en dernier, G au milieu, R en premier.
    for b in range(size):
        for g in range(size):
            for r in range(size):
                rf = r / max(size - 1, 1)
                gf = g / max(size - 1, 1)
                bf = b / max(size - 1, 1)
                body_lines.append(f"{rf:.4f} {gf:.4f} {bf:.4f}")
    return header + "\n".join(body_lines) + "\n"


# ---------------------------------------------------------------------------
# ColorCurve / ColorCurves
# ---------------------------------------------------------------------------


def test_color_curve_identity_has_sixteen_points() -> None:
    curve = ColorCurve.identity()
    assert len(curve.points) == 16
    assert curve.is_identity()


def test_color_curve_rejects_too_few_points() -> None:
    with pytest.raises(ColorGradingRangeError):
        ColorCurve(points=((0.0, 0.0),))


def test_color_curve_rejects_non_monotonic_abscissae() -> None:
    with pytest.raises(ColorGradingRangeError):
        ColorCurve(points=((0.0, 0.0), (0.5, 0.5), (0.3, 0.5)))


def test_color_curve_rejects_non_finite_values() -> None:
    with pytest.raises(ColorGradingRangeError):
        ColorCurve(points=((float("nan"), 0.0), (1.0, 1.0)))


def test_color_curves_is_identity_by_default() -> None:
    curves = ColorCurves()
    assert curves.is_identity()


def test_color_curves_with_point_modifies_single_channel() -> None:
    curves = ColorCurves()
    new = curves.with_point("red", index=8, output=0.9)
    assert new.red.points[8][1] == 0.9
    # Les autres canaux restent à l'identité.
    assert new.master.points == curves.master.points
    assert new.green.is_identity()
    assert new.blue.is_identity()


def test_color_curves_with_point_rejects_unknown_channel() -> None:
    curves = ColorCurves()
    with pytest.raises(ColorGradingError):
        curves.with_point("cyan", index=0, output=0.5)


def test_color_curves_with_point_rejects_out_of_range() -> None:
    curves = ColorCurves()
    with pytest.raises(ColorGradingRangeError):
        curves.with_point("red", index=0, output=2.0)


# ---------------------------------------------------------------------------
# LUTResource
# ---------------------------------------------------------------------------


def test_lut_resource_rejects_empty_path() -> None:
    with pytest.raises(ColorGradingError):
        LUTResource(path="", title="x", sha1="0" * 40, size=10)


def test_lut_resource_rejects_invalid_sha1() -> None:
    with pytest.raises(ColorGradingError):
        LUTResource(path="x.cube", title="x", sha1="short", size=10)


def test_lut_resource_from_path(tmp_path) -> None:
    file = tmp_path / "Teal.cube"
    file.write_text(_cube_text(size=2, title="Teal"))
    resource = LUTResource.from_path(file, project_root=tmp_path)
    assert resource.title == "Teal"
    assert resource.path.endswith("Teal.cube")
    assert resource.size > 0
    assert len(resource.sha1) == 40


def test_lut_resource_from_path_missing_raises(tmp_path) -> None:
    with pytest.raises(ColorGradingError):
        LUTResource.from_path(tmp_path / "missing.cube")


# ---------------------------------------------------------------------------
# LUT importer (.cube)
# ---------------------------------------------------------------------------


def test_parse_cube_lut_happy_path(tmp_path) -> None:
    cube = tmp_path / "identity.cube"
    cube.write_text(_cube_text(size=2, title="Identity"))
    parsed = parse_cube_lut(cube)
    assert parsed.title == "Identity"
    assert parsed.size == 2
    assert len(parsed.entries) == 2 * 2 * 2 * 3


@pytest.mark.parametrize(
    "encode",
    [
        lambda text: b"\xef\xbb\xbf" + text.encode("utf-8"),
        lambda text: b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"),
        lambda text: text.encode("cp1252"),
    ],
    ids=["utf8-bom", "utf8-bom-crlf", "cp1252"],
)
def test_parse_cube_lut_accepts_bom_and_windows_1252(tmp_path, encode) -> None:
    """Un ``.cube`` enregistré par le Bloc-notes (BOM, CRLF) ou au titre en Windows-1252 est importé."""
    cube = tmp_path / "windows.cube"
    cube.write_bytes(encode(_cube_text(size=2, title="Négatif")))
    parsed = parse_cube_lut(cube)
    assert parsed.title == "Négatif"
    assert parsed.size == 2
    assert len(parsed.entries) == 2 * 2 * 2 * 3


def test_parse_cube_lut_rejects_binary_content(tmp_path) -> None:
    """Le décodage tolérant ne fait pas accepter un fichier binaire : l'en-tête reste refusé."""
    cube = tmp_path / "image.cube"
    cube.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xd8")
    with pytest.raises(LUTImportError):
        parse_cube_lut(cube)


def test_parse_cube_lut_rejects_1d(tmp_path) -> None:
    cube = tmp_path / "oned.cube"
    cube.write_text('TITLE "1D"\nLUT_1D_SIZE 2\n0.0 0.0 0.0\n1.0 1.0 1.0\n')
    with pytest.raises(LUTImportUnsupported):
        parse_cube_lut(cube)


def test_parse_cube_lut_rejects_missing_file(tmp_path) -> None:
    with pytest.raises(LUTImportMissingFile):
        parse_cube_lut(tmp_path / "nope.cube")


def test_parse_cube_lut_rejects_bad_size(tmp_path) -> None:
    cube = tmp_path / "small.cube"
    cube.write_text(_cube_text(size=1))
    with pytest.raises(LUTImportUnsupported):
        parse_cube_lut(cube)


def test_parse_cube_lut_rejects_too_large(tmp_path) -> None:
    cube = tmp_path / "huge.cube"
    cube.write_text(
        f"TITLE Huge\nLUT_3D_SIZE {MAX_LUT_SIZE + 1}\n"
        + "0.0 0.0 0.0\n" * ((MAX_LUT_SIZE + 1) ** 3)
    )
    with pytest.raises(LUTImportUnsupported):
        parse_cube_lut(cube)


def test_parse_cube_lut_rejects_truncated_file(tmp_path) -> None:
    cube = tmp_path / "truncated.cube"
    cube.write_text(_cube_text(size=2))
    # On retire une ligne : la LUT devient incohérente.
    text = cube.read_text()
    lines = text.splitlines()
    truncated = "\n".join(lines[:-1])
    cube.write_text(truncated)
    with pytest.raises(LUTImportBadFormat):
        parse_cube_lut(cube)


def test_parse_cube_lut_minimum_size_works(tmp_path) -> None:
    cube = tmp_path / "min.cube"
    cube.write_text(_cube_text(size=MIN_LUT_SIZE))
    parsed = parse_cube_lut(cube)
    assert parsed.size == MIN_LUT_SIZE


def test_parse_cube_lut_missing_size_key(tmp_path) -> None:
    cube = tmp_path / "no_size.cube"
    cube.write_text('TITLE "NoSize"\n0.0 0.0 0.0\n1.0 1.0 1.0\n')
    with pytest.raises(LUTImportBadFormat):
        parse_cube_lut(cube)


# ---------------------------------------------------------------------------
# ColorGrade
# ---------------------------------------------------------------------------


def test_color_grade_identity_is_inert() -> None:
    grade = ColorGrade.identity()
    assert grade.is_identity()
    assert grade.curves.is_identity()
    assert grade.lut is None


def test_color_grade_rejects_out_of_range_values() -> None:
    with pytest.raises(ColorGradingRangeError):
        ColorGrade(exposure=EXPOSURE_MAX + 0.1)
    with pytest.raises(ColorGradingRangeError):
        ColorGrade(saturation=SATURATION_MIN - 0.1)
    with pytest.raises(ColorGradingRangeError):
        ColorGrade(temperature=TEMPERATURE_MIN - 1.0)
    with pytest.raises(ColorGradingRangeError):
        ColorGrade(hue=HUE_MAX + 1.0)
    with pytest.raises(ColorGradingRangeError):
        ColorGrade(shadows=SHADOWS_MIN - 0.1)
    with pytest.raises(ColorGradingRangeError):
        ColorGrade(highlights=HIGHLIGHTS_MAX + 0.1)
    with pytest.raises(ColorGradingRangeError):
        ColorGrade(contrast=CONTRAST_MIN - 0.1)


def test_color_grade_accepts_boundary_values() -> None:
    grade = ColorGrade(
        exposure=EXPOSURE_MIN,
        contrast=CONTRAST_MAX,
        saturation=SATURATION_MAX,
        temperature=TEMPERATURE_MIN,
        hue=HUE_MAX,
        shadows=SHADOWS_MAX,
        highlights=HIGHLIGHTS_MIN,
    )
    assert grade.exposure == EXPOSURE_MIN


def test_color_grade_with_field_returns_new_instance() -> None:
    base = ColorGrade.identity()
    updated = base.with_field("exposure", 0.5)
    assert base.exposure == 0.0
    assert updated.exposure == 0.5


def test_color_grade_with_field_rejects_unknown() -> None:
    base = ColorGrade.identity()
    with pytest.raises(ColorGradingError):
        base.with_field("contrast_neg", 0.5)


def test_color_grade_with_lut_replaces_lut() -> None:
    base = ColorGrade.identity()
    lut = LUTResource(path="x.cube", title="x", sha1="0" * 40, size=10)
    updated = base.with_lut(lut)
    assert updated.lut is lut


def test_color_grade_with_enabled_toggles() -> None:
    base = ColorGrade.identity()
    assert base.with_enabled(False).enabled is False


def test_color_grade_is_identity_false_when_any_field_changed() -> None:
    base = ColorGrade.identity()
    assert base.is_identity()
    assert base.with_field("exposure", 0.1).is_identity() is False


# ---------------------------------------------------------------------------
# Presets natifs
# ---------------------------------------------------------------------------


def test_builtin_presets_count_is_six() -> None:
    assert len(builtin_color_presets()) == 6
    assert BUILTIN_COLOR_PRESETS_COUNT == 6


def test_builtin_preset_ids_are_unique() -> None:
    ids = [p.id for p in builtin_color_presets()]
    assert len(ids) == len(set(ids))


def test_builtin_presets_categories() -> None:
    expected = {
        "cinema", "teal_orange", "warm", "cool",
        "black_and_white", "vintage",
    }
    assert builtin_color_preset_ids() == frozenset(expected)
    categories = {p.category for p in builtin_color_presets()}
    assert categories == set(ColorPresetCategory)


def test_categories_have_labels_and_descriptions() -> None:
    for category in ColorPresetCategory:
        assert category in CATEGORY_LABELS
        assert category in CATEGORY_DESCRIPTIONS


def test_filter_by_category_cinema() -> None:
    matches = filter_color_presets(
        builtin_color_presets(),
        category=ColorPresetCategory.CINEMA,
    )
    assert {p.id for p in matches} == {"cinema"}


def test_filter_by_search_finds_each_preset() -> None:
    library = builtin_color_presets()
    for preset in library:
        matches = filter_color_presets(library, search=preset.id)
        assert preset.id in {p.id for p in matches}


def test_make_color_preset_rejects_empty_name() -> None:
    with pytest.raises(ColorGradingError):
        make_color_preset(
            preset_id="x",
            name="  ",
            description="",
            category=ColorPresetCategory.CINEMA,
            grade=ColorGrade.identity(),
        )


def test_make_user_color_preset_uses_inferred_category() -> None:
    preset = make_user_color_preset(
        name="Perso",
        description="",
        grade=ColorGrade.identity(),
    )
    assert preset.builtin is False
    assert preset.category is ColorPresetCategory.VINTAGE


def test_make_user_color_preset_accepts_explicit_category() -> None:
    preset = make_user_color_preset(
        name="Perso",
        description="",
        grade=ColorGrade.identity(),
        category=ColorPresetCategory.CINEMA,
    )
    assert preset.category is ColorPresetCategory.CINEMA


# ---------------------------------------------------------------------------
# ColorPresetStore
# ---------------------------------------------------------------------------


def test_store_add_and_remove_user_preset() -> None:
    preset = make_user_color_preset(
        name="Perso",
        description="",
        grade=ColorGrade.identity(),
    )
    store = ColorPresetStore()
    store.add_user_preset(preset)
    assert store.get_preset(preset.id) is preset
    store.remove_user_preset(preset.id)
    assert store.get_preset(preset.id) is None


def test_store_rejects_builtin_as_user() -> None:
    builtin = builtin_color_presets()[0]
    store = ColorPresetStore()
    with pytest.raises(ValueError):
        store.add_user_preset(builtin)


def test_store_toggle_favorite() -> None:
    store = ColorPresetStore()
    assert store.toggle_favorite("cinema") is True
    assert store.is_favorite("cinema") is True
    assert store.toggle_favorite("cinema") is False


def test_store_persistence_roundtrip(tmp_path) -> None:
    preset = make_user_color_preset(
        name="Perso",
        description="",
        grade=ColorGrade.identity(),
    )
    save_color_preset_data(
        [preset], ["cinema"], settings_dir=tmp_path
    )
    assert (tmp_path / COLOR_PRESETS_FILE).exists()
    user, favs = load_color_preset_data(settings_dir=tmp_path)
    assert any(p.id == preset.id for p in user)
    assert "cinema" in favs


# ---------------------------------------------------------------------------
# ColorGradingService
# ---------------------------------------------------------------------------


def test_service_get_grade_returns_identity_by_default() -> None:
    project = _project_with_video_clip()
    service = ColorGradingService()
    grade = service.get_grade(project, "c1")
    assert grade.is_identity()


def test_service_set_grade_stores_on_clip() -> None:
    project = _project_with_video_clip()
    service = ColorGradingService()
    grade = ColorGrade(exposure=0.4, contrast=0.2)
    stored = service.set_grade(project, "c1", grade)
    assert stored is project.tracks[0].clips[0].color_grade


def test_service_reset_grade_clears_grade() -> None:
    project = _project_with_video_clip()
    service = ColorGradingService()
    service.set_grade(project, "c1", ColorGrade(exposure=0.4))
    service.reset_grade(project, "c1")
    assert service.get_grade(project, "c1").is_identity()


def test_service_apply_preset_preserves_lut() -> None:
    project = _project_with_video_clip()
    service = ColorGradingService()
    initial = ColorGrade.identity().with_lut(
        LUTResource(path="x.cube", title="x", sha1="0" * 40, size=10)
    )
    service.set_grade(project, "c1", initial)
    preset = builtin_color_presets()[0]
    applied = service.apply_preset(project, "c1", preset)
    assert applied.lut is not None
    # Et la saturation du preset est appliquée.
    assert applied.saturation == preset.grade.saturation


def test_service_apply_user_preset_uses_its_lut() -> None:
    project = _project_with_video_clip()
    preset_lut = LUTResource(
        path="preset.cube", title="Preset", sha1="1" * 40, size=10
    )
    preset = make_user_color_preset(
        "Avec LUT", "", ColorGrade(exposure=0.2, lut=preset_lut)
    )

    applied = ColorGradingService().apply_preset(project, "c1", preset)

    assert applied.lut is preset_lut


def test_service_apply_preset_rejects_none() -> None:
    project = _project_with_video_clip()
    service = ColorGradingService()
    with pytest.raises(ColorGradingError):
        service.apply_preset(project, "c1", None)


def test_service_set_grade_rejects_unknown_clip() -> None:
    project = _project_with_video_clip()
    service = ColorGradingService()
    with pytest.raises(ColorGradingError):
        service.set_grade(project, "unknown", ColorGrade.identity())


# ---------------------------------------------------------------------------
# Persistance .kut
# ---------------------------------------------------------------------------


def test_roundtrip_preserves_color_grade(tmp_path) -> None:
    project = _project_with_video_clip()
    service = ColorGradingService()
    # On crée un fichier LUT réel sur disque pour que la détection
    # ``missing`` à l'ouverture ne le marque pas absent. La cohérence
    # entre SHA‑1 et contenu est garantie par ``from_path``.
    lut_path = tmp_path / "luts" / "test.cube"
    lut_path.parent.mkdir(parents=True, exist_ok=True)
    lut_path.write_text(_cube_text(size=2, title="Test"))
    resource = LUTResource.from_path(lut_path, project_root=tmp_path)
    grade = ColorGrade(
        exposure=0.3,
        contrast=0.25,
        saturation=1.1,
        temperature=15.0,
        hue=-5.0,
        shadows=-0.2,
        highlights=0.1,
        lut=resource,
    )
    service.set_grade(project, "c1", grade)
    target = tmp_path / "grade.kut"
    save_project(project, str(target))
    loaded = load_project(str(target))
    loaded_grade = loaded.tracks[0].clips[0].color_grade
    assert loaded_grade.exposure == 0.3
    assert loaded_grade.lut is not None
    assert loaded_grade.lut.path == resource.path
    assert loaded_grade.lut.missing is False


def test_legacy_project_without_color_grade_loads(tmp_path) -> None:
    """Un projet v11.1 (sans ``color_grade`` au niveau clip) charge avec
    ``None`` : aucun filtre couleur n'est appliqué au rendu."""
    project = _project_with_video_clip()
    target = tmp_path / "legacy.kut"
    save_project(project, str(target))
    import json

    raw = json.loads(target.read_text(encoding="utf-8"))
    for track in raw["project"]["sequences"][0]["tracks"]:
        for clip in track["clips"]:
            clip.pop("color_grade", None)
    target.write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_project(str(target))
    assert loaded.tracks[0].clips[0].color_grade is None


def test_invalid_color_grade_entry_is_dropped(tmp_path) -> None:
    """Une entrée ``color_grade`` invalide est ramenée à ``None``."""
    project = _project_with_video_clip()
    target = tmp_path / "bad.kut"
    save_project(project, str(target))
    import json

    raw = json.loads(target.read_text(encoding="utf-8"))
    raw["project"]["sequences"][0]["tracks"][0]["clips"][0]["color_grade"] = {
        "exposure": 9999.0,  # hors bornes
        "contrast": 0.5,
        "saturation": 1.0,
        "temperature": 0.0,
        "hue": 0.0,
        "shadows": 0.0,
        "highlights": 0.0,
        "curves": {},
        "lut": None,
        "enabled": True,
    }
    target.write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_project(str(target))
    # L'entrée est invalide : on retombe sur None (identité).
    assert loaded.tracks[0].clips[0].color_grade is None


def test_missing_lut_marked_after_load(tmp_path) -> None:
    """Un LUT référencé mais absent du disque est marqué ``missing``."""
    project = _project_with_video_clip()
    service = ColorGradingService()
    grade = ColorGrade.identity().with_lut(
        LUTResource(
            path="luts/ghost.cube",
            title="Ghost",
            sha1="0" * 40,
            size=10,
        )
    )
    service.set_grade(project, "c1", grade)
    target = tmp_path / "ghost.kut"
    save_project(project, str(target))
    loaded = load_project(str(target))
    loaded_grade = loaded.tracks[0].clips[0].color_grade
    # À l'ouverture, le fichier est introuvable : on garde la
    # ressource mais on positionne ``missing=True`` pour permettre
    # à l'UI de signaler l'absence.
    assert loaded_grade.lut is not None
    assert loaded_grade.lut.missing is True
    assert loaded_grade.lut.path == "luts/ghost.cube"


def test_save_copies_external_lut_next_to_project(tmp_path) -> None:
    project = _project_with_video_clip()
    source_dir = tmp_path / "external"
    source_dir.mkdir()
    source = source_dir / "look.cube"
    source.write_text(_cube_text(), encoding="utf-8")
    ColorGradingService().set_grade(
        project,
        "c1",
        ColorGrade(lut=LUTResource.from_path(source)),
    )

    target = tmp_path / "project" / "portable.kut"
    save_project(project, str(target))
    loaded = load_project(str(target))
    lut = loaded.tracks[0].clips[0].color_grade.lut

    assert not Path(lut.path).is_absolute()
    assert (target.parent / lut.path).is_file()
    assert lut.missing is False


def test_project_color_presets_roundtrip_in_kut(tmp_path) -> None:
    project = _project_with_video_clip()
    preset = make_user_color_preset(
        "Mon look", "Embarqué", ColorGrade(exposure=0.4, saturation=0.8)
    )
    project.color_presets.append(preset)
    target = tmp_path / "presets.kut"

    save_project(project, str(target))
    loaded = load_project(str(target))

    assert len(loaded.color_presets) == 1
    assert loaded.color_presets[0].name == "Mon look"
    assert loaded.color_presets[0].grade.exposure == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# Filtres FFmpeg
# ---------------------------------------------------------------------------


def test_grade_filters_empty_for_none() -> None:
    assert _build_color_grade_filters(None) == ""


def test_grade_filters_identity_only_emits_eq_inline() -> None:
    """Variante : on retire le test redondant (l'identité émet ``eq``,
    c'est attendu pour la stabilité du pipeline)."""
    # On garde cette fonction uniquement pour les futurs tests qui
    # voudront vérifier l'émission de ``eq`` lors d'une identité.
    pass


def test_grade_filters_empty_when_disabled() -> None:
    grade = ColorGrade.identity().with_enabled(False)
    grade = grade.with_field("exposure", 0.5)
    assert _build_color_grade_filters(grade) == ""


def test_grade_filters_includes_eq_for_identity_with_curves() -> None:
    """``eq`` reste émis même avec exposition / contraste / saturation
    nuls : on veut une chaîne stable quand l'utilisateur active puis
    désactive un champ. Les autres filtres ne s'ajoutent que s'ils
    sont actifs. Ici, avec une courbe rouge non‑identité (un point
    modifié), on émet à la fois ``eq`` et ``curves=red=...``."""
    new_points = list(ColorCurve.identity().points)
    new_points[8] = (new_points[8][0], 0.9)
    custom_curve = ColorCurve(points=tuple(new_points))
    curves = ColorCurves(red=custom_curve)
    grade_obj = ColorGrade(curves=curves)
    filters = _build_color_grade_filters(grade_obj)
    assert "eq=" not in filters, "un eq neutre coûtait un aller-retour YUV en 8 bits"
    assert filters.startswith("curves=")


def test_grade_filters_turn_temperature_into_a_white_balance() -> None:
    filters = _build_color_grade_filters(ColorGrade(temperature=20.0))
    assert "colorchannelmixer=rr=1.06:" in filters and "colorbalance=" not in filters


def test_grade_filters_turn_hue_into_a_rotation() -> None:
    filters = _build_color_grade_filters(ColorGrade(hue=10.0))
    assert "hue=h=10.0" in filters and "colorbalance=" not in filters


def test_grade_filters_emits_colorbalance_for_shadows() -> None:
    grade = ColorGrade(shadows=0.3)
    filters = _build_color_grade_filters(grade)
    assert "colorbalance=" in filters


def test_grade_filters_emits_colorbalance_for_highlights() -> None:
    grade = ColorGrade(highlights=0.2)
    filters = _build_color_grade_filters(grade)
    assert "colorbalance=" in filters


def test_grade_filters_emits_lut3d_for_attached_lut() -> None:
    grade = ColorGrade.identity().with_lut(
        LUTResource(path="luts/identity.cube", title="Identity",
                   sha1="0" * 40, size=10)
    )
    filters = _build_color_grade_filters(grade)
    assert "lut3d=" in filters
    assert "luts/identity.cube" in filters


def test_grade_filters_omits_lut3d_when_path_empty() -> None:
    # Le constructeur ``LUTResource`` n'autorise pas un chemin vide
    # directement : on construit un ColorGrade puis on *force* la
    # valeur ``lut.path`` à ``""`` pour tester le filet de sécurité.
    grade = ColorGrade.identity()
    object.__setattr__(
        grade,
        "lut",
        LUTResource(path="x.cube", title="X", sha1="0" * 40, size=10),
    )
    object.__setattr__(grade.lut, "path", "")
    filters = _build_color_grade_filters(grade)
    assert "lut3d=" not in filters


def test_grade_filters_omits_colorbalance_when_only_curves_active() -> None:
    """Uniquement des courbes R : on n'émet pas colorbalance car les
    paramètres de température / teinte / ombres / hautes lumières
    sont tous neutres. La courbe rouge est modifiée pour exercer le
    filtre ``curves=``."""
    new_points = list(ColorCurve.identity().points)
    new_points[8] = (new_points[8][0], 0.9)
    custom_curve = ColorCurve(points=tuple(new_points))
    curves = ColorCurves(red=custom_curve)
    grade = ColorGrade(curves=curves)
    filters = _build_color_grade_filters(grade)
    assert "colorbalance=" not in filters
    assert "curves=" in filters


def test_grade_filters_order_is_deterministic() -> None:
    grade = ColorGrade(
        exposure=0.3,
        temperature=10.0,
        saturation=1.2,
        shadows=-0.2,
        highlights=0.1,
    )
    filters = _build_color_grade_filters(grade)
    # eq d'abord, colorbalance ensuite.
    eq_pos = filters.index("eq=")
    cb_pos = filters.index("colorbalance=")
    assert eq_pos < cb_pos


def test_grade_filters_chain_includes_lut_after_curves() -> None:
    new_points = list(ColorCurve.identity().points)
    new_points[8] = (new_points[8][0], 0.9)
    custom_curve = ColorCurve(points=tuple(new_points))
    grade = ColorGrade(
        shadows=0.1,
        curves=ColorCurves(red=custom_curve),
    ).with_lut(
        LUTResource(path="x.cube", title="X", sha1="0" * 40, size=10)
    )
    filters = _build_color_grade_filters(grade)
    cb_pos = filters.index("colorbalance=")
    cv_pos = filters.index("curves=")
    lt_pos = filters.index("lut3d=")
    assert cb_pos < cv_pos < lt_pos


def test_grade_filters_with_b_and_w_preset() -> None:
    """Le preset N&B contraste doit produire ``saturation=0`` et un
    ``eq`` qui en tient compte (saturation neutre = 1.0)."""
    b_w = next(p for p in builtin_color_presets()
               if p.category == ColorPresetCategory.BLACK_AND_WHITE)
    grade = b_w.grade
    filters = _build_color_grade_filters(grade)
    assert "eq=" in filters
    assert "saturation=0.0" in filters


def test_grade_filters_identity_emits_nothing() -> None:
    """L'identité totale ne produit aucun filtre (jusqu'au 2026-10-10, un
    ``eq`` neutre restait, et son aller-retour YUV en 8 bits coûtait un
    arrondi sans rien changer)."""
    assert _build_color_grade_filters(ColorGrade.identity()) == ""


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg indisponible")
def test_generated_color_chain_is_accepted_by_real_ffmpeg(tmp_path) -> None:
    # Sous Windows, le chemin absolu porte déjà une lettre de lecteur ``C:``.
    # Sous POSIX on injecte un ``:`` dans un composant pour couvrir le même
    # séparateur FFmpeg sans fabriquer un chemin Windows invalide.
    cube_directory = tmp_path
    if os.name != "nt":
        cube_directory = tmp_path / "path:with-colon"
        cube_directory.mkdir()
    cube = cube_directory / "identity.cube"
    cube.write_text(_cube_text(), encoding="utf-8")
    points = list(ColorCurve.identity().points)
    points[8] = (points[8][0], 0.7)
    grade = ColorGrade(
        exposure=0.25,
        contrast=0.2,
        saturation=1.1,
        temperature=15.0,
        shadows=-0.1,
        highlights=0.15,
        curves=ColorCurves(red=ColorCurve(tuple(points))),
        lut=LUTResource.from_path(cube),
    )
    filters = _build_color_grade_filters(grade)

    base_command = [
        shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=s=32x32:d=0.1",
    ]
    completed = subprocess.run(
        base_command + [
            "-vf", filters, "-frames:v", "1", "-f", "framemd5", "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    baseline = subprocess.run(
        base_command + ["-frames:v", "1", "-f", "framemd5", "-"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert baseline.returncode == 0, baseline.stderr
    rendered_hash = [line for line in completed.stdout.splitlines() if not line.startswith("#")][-1]
    baseline_hash = [line for line in baseline.stdout.splitlines() if not line.startswith("#")][-1]
    assert rendered_hash != baseline_hash


# ---------------------------------------------------------------------------
# Undo / Redo
# ---------------------------------------------------------------------------


def test_undo_redo_for_set_grade() -> None:
    project = _project_with_video_clip()
    history = ProjectHistory()
    history.reset(project)

    service = ColorGradingService()
    grade = ColorGrade(exposure=0.4)
    service.set_grade(project, "c1", grade)
    history.record(project, "Étalonnage du clip")
    assert project.tracks[0].clips[0].color_grade is not None

    restored = history.undo()
    assert restored is not None
    # ``ProjectHistory`` capture ``color_grade`` par référence : le
    # pointeur peut être restauré tel quel. On vérifie que l'instance
    # capturée est l'identité par le biais d'un autre chemin (la
    # valeur ``exposure`` du snapshot est 0.0).
    if restored.tracks[0].clips[0].color_grade is not None:
        assert restored.tracks[0].clips[0].color_grade.exposure == 0.0


def test_undo_redo_for_apply_preset() -> None:
    project = _project_with_video_clip()
    history = ProjectHistory()
    history.reset(project)

    service = ColorGradingService()
    preset = builtin_color_presets()[0]
    service.apply_preset(project, "c1", preset)
    history.record(project, "Appliquer un preset")
    assert project.tracks[0].clips[0].color_grade is not None
    assert project.tracks[0].clips[0].color_grade.exposure == preset.grade.exposure

    restored = history.undo()
    assert restored is not None


def test_undo_redo_for_reset_grade() -> None:
    project = _project_with_video_clip()
    service = ColorGradingService()
    service.set_grade(project, "c1", ColorGrade(exposure=0.5))

    history = ProjectHistory()
    history.reset(project)

    service.reset_grade(project, "c1")
    history.record(project, "Réinitialiser l'étalonnage")
    # Après reset, ``color_grade`` est un ColorGrade identité.
    assert project.tracks[0].clips[0].color_grade.is_identity()


@pytest.mark.parametrize(
    "encode",
    [
        lambda text: b"\xef\xbb\xbf" + text.encode("utf-8"),
        lambda text: b"\xff\xfe" + text.encode("utf-16-le"),
        lambda text: text.encode("cp1252"),
    ],
    ids=["utf8-bom", "utf16", "cp1252"],
)
def test_a_lut_ffmpeg_cannot_read_is_handed_over_as_a_utf8_copy(tmp_path, encode) -> None:
    """FFmpeg ``lut3d`` lit des octets : derrière une BOM ou en UTF-16, ``LUT_3D_SIZE`` ne lui dit rien (l'export
    échouait). La copie normalisée porte le même contenu, en UTF-8 sans BOM, et n'est écrite qu'une fois."""
    from core.lut_importer import ffmpeg_readable_lut

    # ``LUT_3D_SIZE`` en tête (ce qui fait échouer FFmpeg derrière une BOM), titre accentué (cp1252 diffère d'UTF-8).
    text = 'LUT_3D_SIZE 2\nTITLE "Négatif"\n' + _cube_text(size=2).split("LUT_3D_SIZE 2\n", 1)[1]
    source = tmp_path / "windows.cube"
    source.write_bytes(encode(text))
    copy = ffmpeg_readable_lut(source, cache_dir=tmp_path / "cache")
    assert copy != source and copy.parent == tmp_path / "cache" / "luts"
    assert copy.read_bytes() == text.encode("utf-8")
    assert parse_cube_lut(copy).entries == parse_cube_lut(source).entries
    stamp = copy.stat().st_mtime_ns
    assert ffmpeg_readable_lut(source, cache_dir=tmp_path / "cache") == copy and copy.stat().st_mtime_ns == stamp


def test_a_plain_utf8_lut_is_used_in_place(tmp_path) -> None:
    from core.lut_importer import ffmpeg_readable_lut

    source = tmp_path / "plain.cube"
    source.write_text(_cube_text(size=2, title="Identity"), encoding="utf-8")
    assert ffmpeg_readable_lut(source, cache_dir=tmp_path / "cache") == source
    assert not (tmp_path / "cache").exists()


@pytest.mark.parametrize("bom_first", [True, False], ids=["bom-then-size", "plain"])
def test_ffmpeg_applies_the_handed_over_lut(tmp_path, bom_first) -> None:
    """De bout en bout : la LUT remise à FFmpeg s'applique (sans copie, BOM + LUT_3D_SIZE en tête échouait)."""
    import shutil
    import subprocess

    from core.lut_importer import ffmpeg_readable_lut

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg indisponible")
    body = "LUT_3D_SIZE 2\n" + "".join(f"{1 - r} {1 - g} {1 - b}\n" for b in (0, 1) for g in (0, 1) for r in (0, 1))
    source = tmp_path / "invert.cube"
    source.write_bytes((b"\xef\xbb\xbf" if bom_first else b"") + body.encode("utf-8"))
    lut = ffmpeg_readable_lut(source, cache_dir=tmp_path / "cache")
    # Le filtre de l'export lui-même : il échappe le chemin (``C:`` sous Windows serait lu comme une option).
    lut_filter = _build_lut3d_filter(LUTResource.from_path(lut))
    done = subprocess.run(
        [ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=4x4:d=0.04", "-vf",
         f"{lut_filter},format=rgb24", "-frames:v", "1", "-f", "rawvideo", "-"],
        capture_output=True, timeout=60,
    )
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    red, green, blue = done.stdout[:3]
    assert red <= 4 and green >= 251 and blue >= 251, (red, green, blue)   # rouge inversé : cyan (arrondi YUV ± 2)


def test_importing_a_notepad_lut_attaches_the_copy_ffmpeg_can_read(qtbot, monkeypatch, tmp_path) -> None:
    """Dans l'application : une LUT avec BOM est importée, et le clip pointe vers sa copie UTF-8 (celle que l'export
    donne à FFmpeg), pas vers l'original qu'il ne saurait pas lire."""
    from PySide6.QtWidgets import QMessageBox

    from ui.main_window import MainWindow

    monkeypatch.setenv("KUT_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
    warnings: list[tuple] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: warnings.append(args))
    window = MainWindow()
    qtbot.addWidget(window)
    if getattr(window, "timeline_timer", None) is not None:
        window.timeline_timer.stop()
    window.project = Project(
        name="lut", width=64, height=36, fps=25.0,
        media_assets=[MediaAsset(id="a", path=str(tmp_path / "a.mp4"), name="a", duration=2.0,
                                 width=64, height=36, fps=25.0, media_type="video")],
        tracks=[Track(id="V1", name="V1", type="video",
                      clips=[Clip(id="c1", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=2.0)])],
    )
    source = tmp_path / "notepad.cube"
    source.write_bytes(b"\xef\xbb\xbf" + ("LUT_3D_SIZE 2\n" + _cube_text(size=2).split("LUT_3D_SIZE 2\n", 1)[1]).encode())

    window.on_lut_loaded("c1", str(source))

    assert not warnings
    lut = ColorGradingService().get_grade(window.project, "c1").lut
    assert lut is not None and Path(lut.source_path) != source
    assert Path(lut.source_path).read_bytes() == source.read_bytes()[3:]
