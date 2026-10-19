"""Format déclaratif, lot 9 (ADR-0005 étape 4) : la bibliothèque de médias (dossiers, étiquettes, rangement, renommer, relier, retirer).

L'organisation de la bibliothèque est un champ du PROJET (enregistré dans le `.kut`) : ce sont des verbes normaux, annulables avec le
reste du script. Chaque test vérifie un refus (du modèle, ou du verbe quand `core/` ne contrôle pas) ou la parité avec les méthodes de
`LibraryOrganization` que l'interface appelle.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from core.library_organization import LibraryOrganization
from core.media_probe import MediaProbeError
from core.project_model import Clip, MediaAsset, Project, Track
from core.scripting import ScriptContext, ScriptInvalid, Session, apply_script, created_ids, json_schema, parse_script
from core.scripting.library_rules import MAX_FOLDER_DEPTH
from core.scripting.verbs import VERBS
from core.sequences import create_sequence
from core.tracking_model import ClipTracking, Stabilization
from ui import i18n

LIBRARY_VERBS = (
    "create_library_folder", "rename_library_folder", "recolor_library_folder", "move_library_folder", "delete_library_folder",
    "create_library_tag", "rename_library_tag", "recolor_library_tag", "delete_library_tag", "move_media_to_folder", "tag_media",
    "untag_media", "rename_media", "relink_media", "remove_media",
)


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _project() -> Project:
    return Project(
        "Démo", width=1280, height=720, fps=30.0,
        media_assets=[
            MediaAsset("m", "/x/intro.mp4", "intro", 10.0, 1280, 720, 30.0, "video", has_audio=True),
            MediaAsset("n", "/x/outro.mp4", "outro", 8.0, 1280, 720, 30.0, "video", has_audio=True),
            MediaAsset("voix", "/x/voix.wav", "voix", 6.0, 0, 0, 0.0, "audio", has_audio=True),
            MediaAsset("tech", "", "Titre", 5.0, 100, 100, 30.0, "graphic"),
        ],
        tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio")],
    )


def _script(*ops: dict) -> dict:
    return {"kut_script": 1, "ops": list(ops)}


def _apply(project: Project, *ops: dict, context: ScriptContext | None = None):
    session = Session(project)
    return session, apply_script(session, parse_script(_script(*ops)), context=context)


def _ok(project: Project, *ops: dict, context: ScriptContext | None = None) -> Session:
    session, report = _apply(project, *ops, context=context)
    assert report.ok, report.error
    return session


def _refused(project: Project, *ops: dict, context: ScriptContext | None = None, message: str = ""):
    before = deepcopy(project)
    session, report = _apply(project, *ops, context=context)
    assert not report.ok, "le script aurait dû être refusé"
    if message:
        assert message in report.error, report.error
    assert session.project.library_folders == before.library_folders and session.project.library_tags == before.library_tags
    assert session.project.library_assignments == before.library_assignments
    assert [a.path for a in session.project.media_assets] == [a.path for a in before.media_assets]
    return report


# --- Le catalogue ------------------------------------------------------------------------------------------------------


def test_the_library_verbs_are_in_the_table_and_the_schema():
    for name in LIBRARY_VERBS:
        assert name in VERBS and VERBS[name].group == "library" and VERBS[name].doc, name
    assert {name for name in LIBRARY_VERBS if VERBS[name].creates} == {"create_library_folder", "create_library_tag"}
    assert VERBS["create_library_folder"].creates == "library_folder" and VERBS["create_library_tag"].creates == "library_tag"
    assert [name for name in LIBRARY_VERBS if VERBS[name].needs_context] == ["relink_media"]
    variants = {item["properties"]["op"]["const"]: item for item in json_schema()["properties"]["ops"]["items"]["oneOf"]}
    assert variants["create_library_folder"]["properties"]["name"]["maxLength"] == 48
    assert "id" in variants["create_library_tag"]["properties"] and "id" not in variants["tag_media"]["properties"]


# --- Parité avec le modèle, de bout en bout ----------------------------------------------------------------------------


def test_a_whole_organisation_script_matches_the_model_calls_the_interface_makes():
    session = _ok(
        _project(),
        {"op": "create_library_folder", "name": "Rushes", "as": "rushes", "color": "#112233"},
        {"op": "create_library_folder", "name": "Jour 1", "parent": {"ref": "rushes"}, "as": "jour1"},
        {"op": "create_library_tag", "name": "Favori", "as": "fav", "color": "#abcdef"},
        {"op": "create_library_tag", "name": "À revoir", "as": "revoir"},
        {"op": "move_media_to_folder", "asset": "m", "folder": {"ref": "jour1"}},
        {"op": "tag_media", "asset": "m", "tag": {"ref": "fav"}},
        {"op": "tag_media", "asset": "m", "tag": "À revoir"},
        {"op": "rename_media", "asset": "n", "name": "Final"},
        {"op": "rename_library_folder", "folder": "Jour 1", "name": "Jour 2"},
        {"op": "recolor_library_tag", "tag": "fav", "color": "#000000"},
    )
    twin = _project()
    org = LibraryOrganization(twin)
    org.create_folder("Rushes", color="#112233", folder_id="rushes")
    org.create_folder("Jour 1", parent_id="rushes", folder_id="jour1")
    org.create_tag("Favori", color="#abcdef", tag_id="fav")
    org.create_tag("À revoir", tag_id="revoir")
    org.move_asset("m", "jour1")
    org.add_tag_to_asset("m", "fav")
    org.add_tag_to_asset("m", "revoir")
    org.rename_asset("n", "Final")
    org.rename_folder("jour1", "Jour 2")
    org.recolor_tag("fav", "#000000")
    project = session.project
    assert project.library_folders == twin.library_folders
    assert project.library_tags == twin.library_tags
    assert project.library_assignments == twin.library_assignments
    assert [a.name for a in project.media_assets] == [a.name for a in twin.media_assets]


def test_created_ids_name_the_folder_and_the_tag_and_an_undo_removes_everything():
    script = parse_script(_script(
        {"op": "create_library_folder", "name": "A", "id": "dossier_a"}, {"op": "create_library_tag", "name": "T", "as": "etiquette"},
        {"op": "create_library_folder", "name": "B"},
    ))
    session = Session(_project())
    report = apply_script(session, script)
    assert report.ok, report.error
    ids = created_ids(script, report)
    assert ids[0] == {"op": "0:create_library_folder", "id": "dossier_a"} and ids[1] == {"op": "1:create_library_tag", "id": "etiquette"}
    assert ids[2]["id"].startswith("lib-"), "sans id, l'identifiant est celui du modèle"
    assert session.undo()
    assert session.project.library_folders == [] and session.project.library_tags == []


def test_a_failing_second_operation_restores_the_first():
    _refused(_project(), {"op": "create_library_folder", "name": "A"}, {"op": "move_media_to_folder", "asset": "m", "folder": "inconnu"})


def test_an_alias_of_the_wrong_kind_is_refused_by_the_decoder():
    with pytest.raises(ScriptInvalid):
        parse_script(_script(
            {"op": "create_library_tag", "name": "T", "as": "t"}, {"op": "move_media_to_folder", "asset": "m", "folder": {"ref": "t"}},
        ))


# --- Dossiers ----------------------------------------------------------------------------------------------------------


def test_folder_names_colours_and_ids_are_checked_like_the_interface():
    for text in ("x" * 49, " A", "A ", "", "A\nB"):
        with pytest.raises(ScriptInvalid):
            parse_script(_script({"op": "create_library_folder", "name": text}))
    _refused(_project(), {"op": "create_library_folder", "name": "A", "color": "#11223344"}, message="Couleur de dossier invalide")
    with pytest.raises(ScriptInvalid):
        parse_script(_script({"op": "create_library_folder", "name": "A", "id": "d"}, {"op": "create_library_folder", "name": "B", "id": "d"}))
    taken = _project()
    LibraryOrganization(taken).create_folder("Déjà", folder_id="d")
    _refused(taken, {"op": "create_library_folder", "name": "B", "id": "d"}, message="existe déjà")
    session = _ok(_project(), {"op": "create_library_folder", "name": "A", "id": "d", "color": "#112233"},
                  {"op": "recolor_library_folder", "folder": "d"})
    assert session.project.library_folders[0].color == "", "sans couleur : plus de couleur propre"


def test_a_name_that_is_another_objects_id_is_refused():
    _refused(_project(), {"op": "create_library_folder", "name": "A", "id": "abc"}, {"op": "create_library_folder", "name": "abc"}, message="identifiant")
    _refused(_project(), {"op": "create_library_tag", "name": "m"}, {"op": "rename_media", "asset": "n", "name": "m"}, message="identifiant")
    _refused(_project(), {"op": "rename_media", "asset": "n", "name": "m"}, message="identifiant")
    _ok(_project(), {"op": "rename_media", "asset": "n", "name": "n"})
    project = _project()
    LibraryOrganization(project).create_tag("Autre", tag_id="x2")
    _refused(project, {"op": "rename_library_tag", "tag": "x2", "name": "x2x"}, {"op": "create_library_tag", "name": "x2"}, message="identifiant")


def test_renaming_to_another_objects_id_is_refused_for_folders_and_tags():
    project = _project()
    org = LibraryOrganization(project)
    org.create_folder("A", folder_id="fa")
    org.create_folder("B", folder_id="fb")
    org.create_tag("T", tag_id="ta")
    org.create_tag("U", tag_id="tb")
    _refused(project, {"op": "rename_library_folder", "folder": "fa", "name": "fb"}, message="identifiant")
    _refused(project, {"op": "rename_library_tag", "tag": "ta", "name": "tb"}, message="identifiant")
    _ok(project, {"op": "rename_library_folder", "folder": "fa", "name": "fa"}, {"op": "rename_library_tag", "tag": "ta", "name": "ta"})


def test_ambiguous_folder_names_are_an_error_never_a_silent_choice():
    project = _project()
    org = LibraryOrganization(project)
    org.create_folder("Même", folder_id="f1")
    org.create_folder("Même", folder_id="f2")
    _refused(project, {"op": "rename_library_folder", "folder": "Même", "name": "Autre"}, message="ambigu")
    _ok(project, {"op": "rename_library_folder", "folder": "f2", "name": "Autre"})


def _chain(project: Project, depth: int) -> None:
    org = LibraryOrganization(project)
    parent = None
    for level in range(depth):
        parent = org.create_folder(f"n{level}", parent_id=parent, folder_id=f"n{level}").id


def test_the_folder_tree_depth_is_bounded_on_the_resulting_tree():
    project = _project()
    _chain(project, MAX_FOLDER_DEPTH)
    _refused(project, {"op": "create_library_folder", "name": "trop", "parent": f"n{MAX_FOLDER_DEPTH - 1}"}, message="niveaux")
    _ok(project, {"op": "create_library_folder", "name": "ok", "parent": f"n{MAX_FOLDER_DEPTH - 2}"})
    # Déplacer un sous-arbre sous un parent profond : les ancêtres du parent ET la hauteur du sous-arbre comptent.
    project = _project()
    _chain(project, 30)
    org = LibraryOrganization(project)
    org.create_folder("a", folder_id="a")
    org.create_folder("b", parent_id="a", folder_id="b")
    org.create_folder("c", parent_id="b", folder_id="c")                   # a > b > c : deux niveaux sous « a »
    _refused(project, {"op": "move_library_folder", "folder": "a", "parent": "n29"}, message="niveaux")      # 30 + 1 + 2 = 33
    _ok(project, {"op": "move_library_folder", "folder": "b", "parent": "n29"})                              # 30 + 1 + 1 = 32


def test_moving_a_folder_into_itself_or_its_descendant_is_refused_and_leaves_the_tree_alone():
    project = _project()
    org = LibraryOrganization(project)
    org.create_folder("a", folder_id="a")
    org.create_folder("b", parent_id="a", folder_id="b")
    _refused(project, {"op": "move_library_folder", "folder": "a", "parent": "b"}, message="descendant")
    _refused(project, {"op": "move_library_folder", "folder": "a", "parent": "a"})
    session = _ok(project, {"op": "move_library_folder", "folder": "b"})
    assert session.project.library_folders[1].parent_id is None, "sans parent : la racine"


def test_the_number_of_folders_and_tags_is_bounded_before_creating(monkeypatch):
    monkeypatch.setattr("core.scripting.library_rules.MAX_LIBRARY_FOLDERS", 2)
    monkeypatch.setattr("core.scripting.library_rules.MAX_LIBRARY_TAGS", 1)
    _refused(_project(), *[{"op": "create_library_folder", "name": f"d{i}"} for i in range(3)], message="limite")
    _refused(_project(), *[{"op": "create_library_tag", "name": f"t{i}"} for i in range(2)], message="limite")
    _ok(_project(), *[{"op": "create_library_folder", "name": f"d{i}"} for i in range(2)])


def test_deleting_a_folder_follows_the_interface_cascade_and_keeps_every_media():
    project = _project()
    org = LibraryOrganization(project)
    org.create_folder("a", folder_id="a")
    org.create_folder("b", parent_id="a", folder_id="b")
    org.create_folder("c", folder_id="c")
    org.move_asset("m", "b")
    org.move_asset("n", "c")
    session = _ok(project, {"op": "delete_library_folder", "folder": "a"})
    assert [f.id for f in session.project.library_folders] == ["c"]
    assert session.project.library_assignments["m"].folder_id is None and session.project.library_assignments["n"].folder_id == "c"
    assert len(session.project.media_assets) == 4
    assert session.undo() and [f.id for f in session.project.library_folders] == ["a", "b", "c"]


# --- Étiquettes --------------------------------------------------------------------------------------------------------


def test_tags_are_checked_and_deleting_one_unpins_it_everywhere():
    project = _project()
    _refused(project, {"op": "create_library_tag", "name": "T", "color": "#11223344"}, message="Couleur de tag invalide")
    session = _ok(
        project, {"op": "create_library_tag", "name": "T", "id": "t"}, {"op": "tag_media", "asset": "m", "tag": "t"},
        {"op": "tag_media", "asset": "m", "tag": "t"}, {"op": "tag_media", "asset": "n", "tag": "T"},
    )
    assert session.project.library_assignments["m"].tag_ids == ["t"], "idempotent"
    assert session.project.library_tags[0].color == LibraryOrganization(_project()).create_tag("x").color
    session = _ok(session.project, {"op": "untag_media", "asset": "n", "tag": "t"}, {"op": "untag_media", "asset": "n", "tag": "t"},
                  {"op": "delete_library_tag", "tag": "t"})
    assert session.project.library_tags == []
    assert all(a.tag_ids == [] for a in session.project.library_assignments.values())


def test_unknown_folders_tags_and_assets_are_refused():
    _refused(_project(), {"op": "tag_media", "asset": "m", "tag": "inconnu"})
    _refused(_project(), {"op": "move_media_to_folder", "asset": "inconnu"})
    _refused(_project(), {"op": "delete_library_folder", "folder": "inconnu"})


# --- Médias ------------------------------------------------------------------------------------------------------------


def test_technical_media_of_layers_are_not_library_media():
    for op in (
        {"op": "rename_media", "asset": "tech", "name": "X"}, {"op": "move_media_to_folder", "asset": "tech"}, {"op": "remove_media", "asset": "tech"},
        {"op": "relink_media", "asset": "tech", "path": "a.mp4"},
    ):
        _refused(_project(), op, context=ScriptContext(Path.cwd()), message="média technique")


def test_removing_a_media_is_refused_while_any_clip_of_any_sequence_uses_it():
    project = _project()
    other = create_sequence(project, "Autre", sequence_id="autre")
    other.tracks[0].clips.append(Clip("c", "n", other.tracks[0].id, 0.0, 0.0, 2.0, label="c"))
    _refused(project, {"op": "remove_media", "asset": "n"}, message="utilisé par 1 clip")     # séquence NON active
    project.tracks[0].clips.append(Clip("d", "m", "V1", 0.0, 0.0, 2.0, label="d"))
    _refused(project, {"op": "remove_media", "asset": "m"}, message="utilisé par 1 clip")
    org = LibraryOrganization(project)
    org.move_asset("voix", None)
    session = _ok(project, {"op": "remove_media", "asset": "voix"})
    assert [a.id for a in session.project.media_assets] == ["m", "n", "tech"]
    assert "voix" not in session.project.library_assignments
    assert session.undo() and any(a.id == "voix" for a in session.project.media_assets)


# --- relink_media ------------------------------------------------------------------------------------------------------


def _fresh(media_type: str = "video", duration: float = 12.0, width: int = 1280, height: int = 720, fps: float = 30.0) -> MediaAsset:
    if media_type == "audio":
        width = height = 0
        fps = 0.0
    return MediaAsset("probe", "", "", duration, width, height, fps, media_type, has_audio=True, timecode="01:00:00:00", reel="R1")


@pytest.fixture
def probe(monkeypatch):
    """Une sonde sans ffprobe : le test porte sur les règles du verbe, pas sur le média."""
    state = {"asset": _fresh(), "calls": []}

    def fake(path: str) -> MediaAsset:
        state["calls"].append(path)
        if state["asset"] is None:
            raise MediaProbeError("illisible")
        return state["asset"]

    monkeypatch.setattr("core.scripting.library_verbs.probe_media", fake)
    return state


def _folder(tmp_path: Path, *names: str) -> Path:
    folder = tmp_path / "montage"
    folder.mkdir()
    for name in names:
        (folder / name).write_bytes(b"x")
    return folder


def test_relink_media_reads_the_new_file_inside_the_script_folder_and_updates_the_media(tmp_path, probe):
    folder = _folder(tmp_path, "nouveau.mp4")
    project = _project()
    project.tracks[0].clips.append(Clip("c", "m", "V1", 0.0, 0.0, 9.0, label="c"))
    session = _ok(project, {"op": "relink_media", "asset": "m", "path": "nouveau.mp4"}, context=ScriptContext(folder))
    asset = session.project.media_assets[0]
    twin = _project()
    twin.tracks[0].clips.append(Clip("c", "m", "V1", 0.0, 0.0, 9.0, label="c"))
    LibraryOrganization(twin).relink_asset("m", str((folder / "nouveau.mp4").resolve()), probe=lambda _p: probe["asset"])
    assert asset == twin.media_assets[0]
    assert asset.duration == 12.0 and asset.timecode == "01:00:00:00" and asset.reel == "R1" and asset.path.endswith("nouveau.mp4")
    assert session.undo() and session.project.media_assets[0].path == "/x/intro.mp4"


def test_relink_media_follows_the_import_media_file_policy(tmp_path, probe):
    folder = _folder(tmp_path, "a.mp4")
    outside = tmp_path / "dehors.mp4"
    outside.write_bytes(b"x")
    op = {"op": "relink_media", "asset": "m", "path": "../dehors.mp4"}
    _refused(_project(), op, context=ScriptContext(folder), message="--allow-outside")
    _refused(_project(), {"op": "relink_media", "asset": "m", "path": str(outside)}, context=ScriptContext(folder), message="hors du dossier")
    _refused(_project(), {"op": "relink_media", "asset": "m", "path": "absent.mp4"}, context=ScriptContext(folder), message="introuvable")
    _refused(_project(), {"op": "relink_media", "asset": "m", "path": "a.mp4"}, message="contexte de fichiers")
    (folder / "lien.mp4").symlink_to(outside)
    _refused(_project(), {"op": "relink_media", "asset": "m", "path": "lien.mp4"}, context=ScriptContext(folder))
    _ok(_project(), op, context=ScriptContext(folder, allow_outside=True))
    assert probe["calls"], "la sonde ne tourne qu'après la résolution du chemin"
    assert len(probe["calls"]) == 1


def test_relink_media_refusals_leave_the_project_untouched(tmp_path, probe):
    folder = _folder(tmp_path, "a.mp4", "b.mp4")
    context = ScriptContext(folder)
    op = {"op": "relink_media", "asset": "m", "path": "a.mp4"}
    probe["asset"] = None
    _refused(_project(), op, context=context, message="ne peut pas être relié")
    probe["asset"] = _fresh("audio")
    _refused(_project(), op, context=context, message="genre")
    probe["asset"] = _fresh("image")
    _refused(_project(), op, context=context, message="genre")
    # Plus court que ce que les clips lisent (l'interface le signalerait seulement).
    project = _project()
    project.tracks[0].clips.append(Clip("c", "m", "V1", 0.0, 0.0, 9.0, label="c"))
    probe["asset"] = _fresh(duration=8.0)
    _refused(project, op, context=context, message="dure 8 s")
    probe["asset"] = _fresh(duration=9.0)
    _ok(project, op, context=context)
    # Un autre média du projet pointe déjà vers ce fichier.
    project = _project()
    project.media_assets[1].path = str((folder / "b.mp4").resolve())
    _refused(project, {"op": "relink_media", "asset": "m", "path": "b.mp4"}, context=context, message="déjà le média 'n'")


def test_relink_media_refuses_a_locked_track_and_a_tracked_clip_whose_frame_changes(tmp_path, probe):
    folder = _folder(tmp_path, "a.mp4")
    context = ScriptContext(folder)
    op = {"op": "relink_media", "asset": "m", "path": "a.mp4"}
    project = _project()
    other = create_sequence(project, "Autre", sequence_id="autre")           # séquence NON active
    other.tracks[0].clips.append(Clip("e", "m", other.tracks[0].id, 0.0, 0.0, 3.0, label="e"))
    other.tracks[0].locked = True
    _refused(project, op, context=context, message="verrouillée")             # le clip verrouillé est dans une autre séquence
    project.get_sequence("autre").tracks[0].locked = False
    project.tracks[0].clips.append(Clip("c", "m", "V1", 0.0, 0.0, 5.0, label="c"))
    project.tracks[0].locked = True
    _refused(project, op, context=context, message="verrouillée")
    project.tracks[0].locked = False
    project.tracks[0].clips[0].tracking = ClipTracking(stabilization=Stabilization())
    probe["asset"] = _fresh(width=1920, height=1080)
    _refused(project, op, context=context, message="suivi")
    probe["asset"] = _fresh(fps=25.0)
    _refused(project, op, context=context, message="suivi")
    probe["asset"] = _fresh()
    _ok(project, op, context=context)


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg/ffprobe absents")
def test_relink_media_with_the_real_ffprobe(tmp_path):
    folder = _folder(tmp_path)
    target = folder / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=1", "-f", "lavfi", "-i", "sine=duration=1",
         "-shortest", "-pix_fmt", "yuv420p", str(target)], check=True,
    )
    session = _ok(_project(), {"op": "relink_media", "asset": "m", "path": "clip.mp4"}, context=ScriptContext(folder))
    asset = session.project.media_assets[0]
    assert (asset.width, asset.height) == (320, 240) and abs(asset.duration - 1.0) < 0.2 and asset.has_audio
    assert os.path.samefile(asset.path, target)


# --- Entrées hostiles --------------------------------------------------------------------------------------------------


def test_hostile_inputs_are_refused_at_decoding_without_echoing_them():
    huge = "x" * 5_000_000
    for op in (
        {"op": "create_library_folder", "name": huge}, {"op": "rename_media", "asset": "m", "name": huge},
        {"op": "relink_media", "asset": "m", "path": huge}, {"op": "tag_media", "asset": huge, "tag": "t"},
        {"op": "create_library_tag", "name": "T", "color": huge}, {"op": "create_library_folder", "name": "A", "id": huge},
    ):
        with pytest.raises(ScriptInvalid) as caught:
            parse_script(_script(op))
        assert len(str(caught.value)) < 2_000
    for value in (float("nan"), float("inf"), None, 3, [], {}):
        with pytest.raises(ScriptInvalid):
            parse_script(_script({"op": "create_library_folder", "name": value}))


def test_a_thousand_operations_of_the_same_creation_stop_at_the_bound(monkeypatch):
    monkeypatch.setattr("core.scripting.library_rules.MAX_LIBRARY_TAGS", 50)
    report = _refused(_project(), *[{"op": "create_library_tag", "name": f"t{i}"} for i in range(1000)], message="limite")
    assert report.failed_index == 50


def _png(path: Path, width: int = 7, height: int = 5) -> Path:
    from PySide6.QtGui import QImage

    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(0x336699)
    assert image.save(str(path), "PNG")
    return path


def test_relink_media_relinks_a_real_image_and_keeps_images_and_videos_apart(tmp_path, probe):
    folder = _folder(tmp_path, "film.mp4")
    _png(folder / "photo.png", 7, 5)
    project = _project()
    project.media_assets.append(MediaAsset("img", "/x/p.png", "photo", 0.0, 100, 100, 30.0, "image"))
    context = ScriptContext(folder)
    project = deepcopy(project)
    session = _ok(deepcopy(project), {"op": "relink_media", "asset": "img", "path": "photo.png"}, context=context)
    asset = next(a for a in session.project.media_assets if a.id == "img")
    assert (asset.width, asset.height, asset.media_type, asset.duration) == (7, 5, "image", 0.0) and asset.path.endswith("photo.png")
    assert not probe["calls"], "une image ne passe pas par ffprobe (qui la classerait en vidéo)"
    _refused(project, {"op": "relink_media", "asset": "img", "path": "film.mp4"}, context=context, message="pas une image")
    _refused(project, {"op": "relink_media", "asset": "m", "path": "photo.png"}, context=context, message="est une image")
    (folder / "faux.png").write_bytes(b"pas une image")
    _refused(project, {"op": "relink_media", "asset": "img", "path": "faux.png"}, context=context, message="image")
    _refused(project, {"op": "relink_media", "asset": "img", "path": "../photo.png"}, context=context, message="hors du dossier")


def _at_cap(project: Project, monkeypatch) -> None:
    from core.scripting.auto_rules import total_clips

    monkeypatch.setattr("core.scripting.auto_rules.MAX_CLIPS_IN_PROJECT", total_clips(project))


def test_every_sequence_creating_verb_counts_the_clips_it_adds_in_a_project_at_the_clip_cap(monkeypatch):
    from core.multicam_ops import AngleSpec, create_multicam_source

    project = _project()
    project.tracks[0].clips.append(Clip("c1", "m", "V1", 0.0, 0.0, 2.0, label="c1"))
    project.tracks[0].clips.append(Clip("c2", "n", "V1", 3.0, 0.0, 2.0, label="c2"))
    create_sequence(project, "Autre", sequence_id="autre").tracks[0].clips.append(Clip("z", "m", "V1", 0.0, 0.0, 2.0, label="z"))
    _at_cap(project, monkeypatch)
    for op, message in (
        ({"op": "duplicate_sequence", "sequence": "autre"}, "dépasseraient"),
        ({"op": "create_multicam_source", "angles": [{"asset": "m"}, {"asset": "n"}]}, "dépasseraient"),
        ({"op": "create_format_version", "sequence": "autre", "format": "square"}, "dépasseraient"),
        ({"op": "create_multicam_from_clips", "clips": [{"clip": "c1"}, {"clip": "c2"}]}, "dépasseraient"),
    ):
        before = deepcopy(project)
        session, report = _apply(project, op)
        assert not report.ok and message in report.error, (op, report.error)
        assert session.project.sequences == before.sequences, op
    _ok(project, {"op": "create_sequence", "name": "Vide"})                          # une séquence vide n'ajoute aucun clip
    _ok(project, {"op": "nest_clips", "clips": ["c1"], "name": "Un"})               # net : le clip imbriqué remplace le clip déplacé
