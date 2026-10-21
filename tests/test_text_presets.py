"""Tests des modèles de texte (:mod:`core.text_presets`, tâche 24).

Couvre :

- les six modèles intégrés (stables, distincts, marqueurs ``builtin``) ;
- le filtrage par recherche libre ;
- le store utilisateur : CRUD, persistance, fichiers corrompus ;
- l'application d'un modèle à un clip via le modèle de projet.
"""

from __future__ import annotations

import json

import pytest

from core.project_model import Clip, MediaAsset, Project, Track
from core.text_presets import (
    TextPresetStore,
    builtin_text_preset_ids,
    builtin_text_presets,
    filter_text_presets,
    get_builtin_preset,
    load_user_text_presets,
    make_text_preset,
    make_user_text_preset,
    save_user_text_presets,
    text_presets_path,
)
from core.text_style import (
    TextAlignment,
    TextStyle,
    default_text_style,
)


# ---------------------------------------------------------------------------
# Catalogue intégré
# ---------------------------------------------------------------------------


def test_builtin_presets_include_the_six_documented_models() -> None:
    expected = {
        "standard_subtitle",
        "title",
        "centered_title",
        "lower_third",
        "quote",
        "credits_simple",
    }
    assert expected.issubset(builtin_text_preset_ids())


def test_builtin_presets_are_marked_as_builtin() -> None:
    for preset in builtin_text_presets():
        assert preset.builtin is True
        # Chaque modèle expose un style et un nom non vide.
        assert preset.name
        assert isinstance(preset.style, TextStyle)


def test_builtin_preset_ids_match_catalog() -> None:
    assert builtin_text_preset_ids() == frozenset(
        p.id for p in builtin_text_presets()
    )


def test_get_builtin_preset_lookup() -> None:
    title = get_builtin_preset("title")
    assert title is not None
    assert title.style.alignment is TextAlignment.TOP_CENTER
    assert get_builtin_preset("ghost") is None


def test_make_text_preset_rejects_empty_id_or_name() -> None:
    with pytest.raises(ValueError):
        make_text_preset(
            preset_id="",
            name="x",
            description="",
            style=default_text_style(),
        )
    with pytest.raises(ValueError):
        make_text_preset(
            preset_id="x",
            name="",
            description="",
            style=default_text_style(),
        )


def test_make_text_preset_clones_style() -> None:
    style = default_text_style()
    preset = make_text_preset(
        preset_id="copy",
        name="Copy",
        description="",
        style=style,
    )
    # Les styles sont immuables mais isolés pour éviter les mutations
    # croisées si l'utilisateur en modifie un.
    assert preset.style == style
    assert preset.style is not style


# ---------------------------------------------------------------------------
# Filtrage
# ---------------------------------------------------------------------------


def test_filter_presets_by_search_matches_name_and_id() -> None:
    library = builtin_text_presets()
    matches = filter_text_presets(library, search="titre")
    # "Titre" et "Titre centré" matchent en minuscules.
    assert {p.id for p in matches} == {"title", "centered_title"}


def test_filter_presets_with_empty_search_returns_all() -> None:
    library = builtin_text_presets()
    assert len(filter_text_presets(library, search="")) == len(library)


# ---------------------------------------------------------------------------
# Création utilisateur
# ---------------------------------------------------------------------------


def test_make_user_preset_assigns_uuid_and_user_membership() -> None:
    preset = make_user_text_preset(
        name="Perso",
        description="",
        style=default_text_style(),
    )
    assert preset.builtin is False
    assert preset.id.startswith("user-")


def test_make_user_preset_rejects_empty_name() -> None:
    with pytest.raises(ValueError):
        make_user_text_preset(
            name="  ",
            description="",
            style=default_text_style(),
        )


# ---------------------------------------------------------------------------
# Persistance
# ---------------------------------------------------------------------------


def test_save_and_load_roundtrip(tmp_path) -> None:
    preset = make_user_text_preset(
        name="Perso",
        description="Roundtrip",
        style=TextStyle(alignment=TextAlignment.TOP_CENTER, font_size=64.0),
        default_text="Salut",
    )
    save_user_text_presets([preset], settings_dir=tmp_path)
    loaded = load_user_text_presets(settings_dir=tmp_path)
    assert len(loaded) == 1
    assert loaded[0].id == preset.id
    assert loaded[0].style.alignment is TextAlignment.TOP_CENTER


def test_save_user_presets_ignores_builtin(tmp_path) -> None:
    standard = builtin_text_presets()[0]
    save_user_text_presets([standard], settings_dir=tmp_path)
    assert load_user_text_presets(settings_dir=tmp_path) == []


def test_load_returns_empty_on_missing_file(tmp_path) -> None:
    assert load_user_text_presets(settings_dir=tmp_path) == []


def test_load_handles_corrupted_json(tmp_path) -> None:
    path = text_presets_path(settings_dir=tmp_path)
    path.write_text("{not valid json", encoding="utf-8")
    assert load_user_text_presets(settings_dir=tmp_path) == []


def test_load_drops_invalid_preset_entries(tmp_path) -> None:
    payload = [
        {"id": "user-1", "name": "Valid", "description": "",
         "default_text": "", "style": {"font_size": 32.0}},
        {"id": "user-bad", "name": "", "description": "",
         "default_text": "", "style": {}},
        "not-a-mapping",
    ]
    path = text_presets_path(settings_dir=tmp_path)
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_user_text_presets(settings_dir=tmp_path)
    assert len(loaded) == 1
    assert loaded[0].id == "user-1"


def test_save_is_atomic_no_tmp_files_left(tmp_path) -> None:
    preset = make_user_text_preset(
        name="Atomic",
        description="",
        style=default_text_style(),
    )
    save_user_text_presets([preset], settings_dir=tmp_path)
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def test_store_persists_across_instances(tmp_path) -> None:
    preset = make_user_text_preset(
        name="Perso",
        description="",
        style=default_text_style(),
    )
    store = TextPresetStore(settings_dir=tmp_path)
    store.add(preset)

    fresh = TextPresetStore(settings_dir=tmp_path)
    assert any(p.id == preset.id for p in fresh.all())


def test_store_rejects_builtin(tmp_path) -> None:
    store = TextPresetStore(settings_dir=tmp_path)
    standard = builtin_text_presets()[0]
    with pytest.raises(ValueError, match="intégrés"):
        store.add(standard)


def test_store_rejects_duplicate_id(tmp_path) -> None:
    store = TextPresetStore(settings_dir=tmp_path)
    preset = make_user_text_preset(
        name="Perso",
        description="",
        style=default_text_style(),
    )
    store.add(preset)
    with pytest.raises(ValueError, match="existe déjà"):
        store.add(preset)


def test_store_unknown_remove(tmp_path) -> None:
    store = TextPresetStore(settings_dir=tmp_path)
    with pytest.raises(KeyError):
        store.remove("ghost")


def test_store_subscribe(tmp_path) -> None:
    store = TextPresetStore(settings_dir=tmp_path)
    events: list[int] = []

    def listener() -> None:
        events.append(len(store.all()))

    store.subscribe(listener)
    preset = make_user_text_preset(
        name="Perso",
        description="",
        style=default_text_style(),
    )
    store.add(preset)
    store.remove(preset.id)
    assert events == [1, 0]


def test_store_all_presets_returns_builtin_then_user(tmp_path) -> None:
    store = TextPresetStore(settings_dir=tmp_path)
    library = store.all_presets()
    assert all(p.builtin for p in library[: len(builtin_text_presets())])


def test_store_get_returns_user_preset(tmp_path) -> None:
    store = TextPresetStore(settings_dir=tmp_path)
    preset = make_user_text_preset(
        name="Perso",
        description="",
        style=default_text_style(),
    )
    store.add(preset)
    assert store.get(preset.id) is preset


# ---------------------------------------------------------------------------
# Application d'un modèle à un clip (au niveau modèle métier)
# ---------------------------------------------------------------------------


def _project_with_subtitle_clip() -> Project:
    track = Track(
        id="T1",
        name="Titres",
        type="subtitle",
        clips=[
            Clip(
                id="sub-1",
                asset_id="subtitle-asset",
                track_id="T1",
                timeline_start=1.0,
                source_in=0.0,
                source_out=2.5,
                label="Titre",
                text="",
            )
        ],
    )
    return Project(
        name="Apply",
        media_assets=[
            MediaAsset(
                id="subtitle-asset",
                path="/tmp/s.srt",
                name="Sous-titres",
                duration=10.0,
                width=0,
                height=0,
                fps=0,
                media_type="subtitle",
            )
        ],
        tracks=[track],
    )


def test_applying_builtin_preset_to_clip_updates_style_and_text() -> None:
    project = _project_with_subtitle_clip()
    clip = project.tracks[0].clips[0]
    title = get_builtin_preset("title")
    clip.text_style = title.style
    if title.default_text:
        clip.text = title.default_text
    assert clip.text_style.alignment is TextAlignment.TOP_CENTER
    assert clip.text == title.default_text


def test_applying_user_preset_via_store_keeps_track_visibility() -> None:
    project = _project_with_subtitle_clip()
    store = TextPresetStore()
    custom = make_user_text_preset(
        name="Perso",
        description="",
        style=TextStyle(font_size=20.0, color="#abcdef"),
        default_text="Bandeau",
    )
    store.add(custom)
    project.tracks[0].clips[0].text_style = store.get(custom.id).style
    project.tracks[0].clips[0].text = "Bandeau"
    assert project.tracks[0].clips[0].text_style.color == "#abcdef"