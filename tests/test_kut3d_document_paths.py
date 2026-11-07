"""Conflit d'enregistrement : le même fichier sous une autre graphie du chemin est reconnu (ADR-0010)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import kut3d
from kut3d import Cube, DocumentConflict, SceneDocument, save_scene
from kut3d import document_commands as cmd
from tests.test_kut3d_document_file import authored_scene


@pytest.fixture
def saved(tmp_path: Path) -> Path:
    path = tmp_path / "scene.kut3d"
    save_scene(authored_scene(), path)
    return path


def _external_edit(path: Path) -> None:
    other = SceneDocument.load(path)
    other.execute(cmd.Rename(other.state.objects[1].id, "externe"))
    other.save()


def _spellings(path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    out: dict[str, Path] = {
        "absolu": path,
        "avec ..": path.parent / "sub" / ".." / path.name,
    }
    (path.parent / "sub").mkdir(exist_ok=True)
    monkeypatch.chdir(path.parent)
    out["relatif"] = Path(path.name)
    link = path.parent / "lien.kut3d"
    try:
        link.symlink_to(path)
        out["lien symbolique"] = link
    except (OSError, NotImplementedError):
        pass  # Windows sans droit de créer des liens : les autres graphies restent testées
    return out


def test_a_changed_file_is_a_conflict_under_every_spelling_of_the_path(
    saved: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for label, spelling in _spellings(saved, monkeypatch).items():
        doc = SceneDocument.load(saved)
        doc.execute(cmd.Rename(doc.state.objects[0].id, f"A-{label}"))
        _external_edit(saved)
        before = saved.read_bytes()
        with pytest.raises(DocumentConflict):
            doc.save(spelling)
        assert saved.read_bytes() == before, label
        doc.save(spelling, overwrite=True)


def test_the_document_opened_under_one_spelling_conflicts_when_saved_under_another(
    saved: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(saved.parent)
    doc = SceneDocument.load(Path(saved.name))  # ouvert en relatif
    doc.execute(cmd.Rename(doc.state.objects[0].id, "A"))
    _external_edit(saved)
    with pytest.raises(DocumentConflict):
        doc.save(saved)  # enregistré en absolu


def test_an_unchanged_file_saves_under_any_spelling(
    saved: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for spelling in _spellings(saved, monkeypatch).values():
        doc = SceneDocument.load(saved)
        doc.execute(cmd.Rename(doc.state.objects[0].id, "B"))
        doc.save(spelling)
        assert not doc.is_dirty


def _case_insensitive(folder: Path) -> bool:
    probe = folder / "Casse.tmp"
    probe.write_text("x")
    try:
        return (folder / "casse.tmp").exists()
    finally:
        probe.unlink()


def test_another_case_is_the_same_file_on_a_case_insensitive_filesystem(
    saved: Path,
) -> None:
    if not _case_insensitive(saved.parent):
        pytest.skip(
            "système de fichiers sensible à la casse : « Scene » et « scene » sont deux fichiers"
        )
    doc = SceneDocument.load(saved)
    doc.execute(cmd.Rename(doc.state.objects[0].id, "A"))
    _external_edit(saved)
    with pytest.raises(DocumentConflict):
        doc.save(saved.parent / saved.name.upper())


def test_a_new_file_is_saved_without_conflict(saved: Path, tmp_path: Path) -> None:
    doc = SceneDocument.load(saved)
    doc.save(tmp_path / "nouveau.kut3d")
    assert (
        tmp_path / "nouveau.kut3d"
    ).exists() and doc.path == tmp_path / "nouveau.kut3d"
    fresh = SceneDocument()
    fresh.execute(cmd.AddPrimitive(Cube()))
    fresh.save(tmp_path / "autre.kut3d")


def test_saving_under_another_existing_file_is_refused_without_overwrite(
    saved: Path, tmp_path: Path
) -> None:
    other = tmp_path / "autre.kut3d"
    other.write_bytes(b"precieux")
    doc = SceneDocument.load(saved)
    with pytest.raises(DocumentConflict, match="existe déjà"):
        doc.save(other)
    assert other.read_bytes() == b"precieux"
    fresh = (
        SceneDocument()
    )  # document neuf (sans fichier d'origine) vers un fichier existant
    with pytest.raises(DocumentConflict):
        fresh.save(other)
    assert other.read_bytes() == b"precieux"
    doc.save(other, overwrite=True)
    assert other.read_bytes() != b"precieux"


def test_a_file_deleted_since_reading_is_a_conflict(saved: Path) -> None:
    doc = SceneDocument.load(saved)
    saved.unlink()
    with pytest.raises(DocumentConflict):
        doc.save()
    assert not saved.exists()
    doc.save(overwrite=True)
    assert saved.exists() and kut3d.load_scene(saved)


def test_hard_link_is_the_same_file(saved: Path, tmp_path: Path) -> None:
    link = tmp_path / "dur.kut3d"
    try:
        os.link(saved, link)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("liens physiques indisponibles")
    doc = SceneDocument.load(saved)
    doc.execute(cmd.Rename(doc.state.objects[0].id, "A"))
    _external_edit(saved)
    with pytest.raises(DocumentConflict):
        doc.save(link)
