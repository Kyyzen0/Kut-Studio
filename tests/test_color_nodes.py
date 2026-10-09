"""Étalonnage par nœuds : le socle de graphe, la chaîne de nœuds en série, l'export, le moniteur et le fichier."""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import pytest
from render_probe import lavfi_video, needs_ffmpeg, render_frame

from core.color_grading import ColorGrade, ColorGradingError, ColorGradingService, LUTResource, Wheel
from core.color_nodes import ColorNode, ColorNodeGraph, as_graph, graph_from_dict, luts_of, simplify
from core.export_engine import _build_color_grade_filters
from core.gpu_grade import grade_is_active, lut_key
from core.node_graph import NodeGraph, NodeGraphError, NodeLink
from core.project_io import _deserialize_color_grade, load_project, save_project
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan

W, H = 64, 36
# Réchauffé par le palet du gain (rouge plus, bleu moins) : la température passe par ``colorbalance``, dont les tons
# moyens n'agissent plus quand max + min dépasse ≈ 202 (un gris de 118 ne se réchauffe pas).
WARM = ColorGrade(gain=Wheel(0.25, 0.0, -0.25, 0.0), gamma=Wheel(y=0.2))
DESATURATED = ColorGrade(saturation=0.0)


@dataclass(frozen=True)
class _Node:
    id: str


# --- socle générique ------------------------------------------------------------------------------------------------


def test_the_generic_graph_orders_a_node_after_everything_feeding_it():
    """Une fusion à deux entrées (la composition nodale de demain) : ``m`` vient après ``a`` et ``b``, l'ordre de
    création départage ``a`` et ``b``."""
    graph = NodeGraph(nodes=(_Node("m"), _Node("b"), _Node("a"), _Node("out")),
                      links=(NodeLink("a", "m", 0), NodeLink("b", "m", 1), NodeLink("m", "out")))
    assert [node.id for node in graph.order()] == ["b", "a", "m", "out"]
    assert [link.source for link in graph.inputs("m")] == ["a", "b"]


@pytest.mark.parametrize("links", [
    (NodeLink("a", "b"), NodeLink("b", "a")),                     # cycle
    (NodeLink("a", "a"),),                                        # sur lui-même
    (NodeLink("a", "c"), NodeLink("b", "c")),                     # deux sources sur la même entrée
    (NodeLink("a", "absent"),),                                   # nœud inconnu
], ids=["cycle", "boucle", "entree-doublee", "absent"])
def test_an_invalid_graph_cannot_exist(links):
    with pytest.raises(NodeGraphError):
        NodeGraph(nodes=(_Node("a"), _Node("b"), _Node("c")), links=links)


def test_a_new_node_takes_the_next_number():
    graph = NodeGraph(nodes=(_Node("n1"), _Node("n4"), _Node("x9")))
    assert graph.next_id() == "n5"
    with pytest.raises(NodeGraphError):
        NodeGraph(nodes=(_Node("n1"), _Node("n1")))


# --- chaîne d'étalonnage --------------------------------------------------------------------------------------------


def test_a_grade_is_a_one_node_graph_and_a_lone_unnamed_node_is_a_grade_again():
    graph = as_graph(WARM)
    assert [node.id for node in graph.nodes] == ["n1"] and graph.nodes[0].grade == WARM
    assert simplify(graph) == WARM
    assert simplify(graph.with_label("n1", "Peau")) == graph.with_label("n1", "Peau"), "un nom se garde"
    assert as_graph(None).nodes[0].grade == ColorGrade()


def test_serial_editing_keeps_a_single_chain():
    graph, second = as_graph(WARM).with_node_after("n1", DESATURATED)
    graph, third = graph.with_node_after("n1")                        # inséré entre n1 et n2
    assert [node.id for node in graph.order()] == ["n1", third, second]
    assert set(graph.links) == {NodeLink("n1", third), NodeLink(third, second)}
    moved = graph.moved(second, 0)
    assert [node.id for node in moved.order()] == [second, "n1", third]
    closed = graph.without(third)
    assert set(closed.links) == {NodeLink("n1", second)}, "la chaîne se referme"
    with pytest.raises(NodeGraphError):
        as_graph(WARM).without("n1")


def test_branches_that_never_meet_again_or_a_node_with_two_inputs_are_refused():
    """Deux sorties (branches sans mélangeur), un correcteur à deux entrées, un mélangeur à une seule branche."""
    from core.color_nodes import ColorMixer

    nodes = (ColorNode("n1"), ColorNode("n2"), ColorNode("n3"))
    for links in ((NodeLink("n1", "n2"), NodeLink("n1", "n3")),
                  (NodeLink("n1", "n3", 0), NodeLink("n2", "n3", 1))):
        with pytest.raises(NodeGraphError):
            ColorNodeGraph(nodes=nodes, links=links)
    with pytest.raises(NodeGraphError):
        ColorNodeGraph(nodes=(*nodes[:2], ColorMixer("m1")), links=(NodeLink("n1", "n2"), NodeLink("n2", "m1")))


def test_a_branch_gets_a_mixer_and_more_branches_join_it():
    from core.color_nodes import MixerKind

    graph, second = as_graph(WARM).with_node_after("n1")
    graph, third = graph.with_branch(second, MixerKind.PARALLEL)
    mixer = graph.mixers[0]
    assert graph.split_point(mixer.id) == "n1" and graph.sink.id == mixer.id
    assert [link.source for link in graph.inputs(mixer.id)] == [second, third]
    graph, fourth = graph.with_branch(third, MixerKind.PARALLEL)
    assert len(graph.mixers) == 1 and [link.source for link in graph.inputs(mixer.id)] == [second, third, fourth]
    layered, top = graph.with_branch(fourth, MixerKind.LAYER)
    assert {item.kind for item in layered.mixers} == {MixerKind.PARALLEL, MixerKind.LAYER}, "un autre type s'imbrique"
    assert layered.split_point(next(item.id for item in layered.mixers if item.kind is MixerKind.LAYER)) == "n1"
    after, fifth = graph.with_node_after(None)
    assert after.sink.id == fifth and after.input_of(fifth) == mixer.id, "en fin de chaîne : après le mélangeur"
    assert [node.id for node in graph.correctors] and graph.node_or_first(mixer.id).id == "n1", "un mélangeur ne se règle pas"


def test_deleting_a_branch_node_closes_the_mixer_when_one_branch_is_left():
    from core.color_nodes import MixerKind

    graph, second = as_graph(WARM).with_branch("n1", MixerKind.PARALLEL)
    graph, third = graph.with_branch(second, MixerKind.PARALLEL)
    fewer = graph.without(third)
    assert [link.port for link in fewer.inputs(fewer.mixers[0].id)] == [0, 1], "les entrées restent 0, 1"
    single = fewer.without(second)
    assert not single.mixers and [node.id for node in single.nodes] == ["n1"]
    deep, inner = graph.with_node_after(second)
    assert deep.input_of(inner) == second and deep.without(second).input_of(inner) is None, "la branche continue"


def test_a_node_moves_along_its_own_serial_run_only():
    from core.color_nodes import MixerKind

    graph, second = as_graph(WARM).with_node_after("n1")
    graph, third = graph.with_node_after(second)
    graph, branch = graph.with_branch(third, MixerKind.PARALLEL)
    # ``branch`` a la même entrée que ``third`` : la série s'arrête là où ``second`` alimente deux branches.
    assert graph.serial_run(second) == ["n1", second] and graph.serial_run(third) == [third]
    moved = graph.moved(second, 0)
    assert [node.id for node in moved.order()][:2] == [second, "n1"]
    assert moved.split_point(moved.mixers[0].id) == "n1", "les branches partent du nœud qui est maintenant devant elles"
    assert graph.moved(third, 5) is graph, "seul dans sa suite : rien ne bouge"


def test_a_qualifier_or_a_mixer_keeps_the_graph_form_and_luts_are_mapped_through_mixers(tmp_path):
    from core.color_nodes import MixerKind, map_grades
    from core.color_qualifier import Qualifier

    qualified = as_graph(WARM).with_qualifier("n1", Qualifier(lum_low=0.5))
    assert simplify(qualified) is qualified, "un qualifieur se garde"
    graph, other = as_graph(WARM).with_branch("n1", MixerKind.LAYER)
    mapped = map_grades(graph, lambda grade: grade.with_field("contrast", 0.1))
    assert mapped.mixers == graph.mixers and all(node.grade.contrast == 0.1 for node in mapped.correctors)


def test_the_export_chains_the_active_nodes_in_order():
    graph, _ = as_graph(WARM).with_node_after("n1", DESATURATED)
    assert _build_color_grade_filters(graph) == ",".join((_build_color_grade_filters(WARM),
                                                          _build_color_grade_filters(DESATURATED)))
    bypassed = graph.with_grade("n2", DESATURATED.with_enabled(False))
    assert _build_color_grade_filters(bypassed) == _build_color_grade_filters(WARM)
    neutral, _ = as_graph(None).with_node_after("n1")
    assert _build_color_grade_filters(neutral) == "" and not grade_is_active(neutral)
    assert grade_is_active(graph) and not grade_is_active(graph.with_grade("n1", WARM.with_enabled(False))
                                                          .with_grade("n2", DESATURATED.with_enabled(False)))


def _rendered(tmp_path, grade) -> np.ndarray:
    media = tmp_path / "plan.mp4"
    if not media.exists():
        lavfi_video(media, "color=c=0xB4643C", size=(W, H), seconds=1.0)
    project = Project("p", width=W, height=H, fps=25.0,
                      media_assets=[MediaAsset("v", str(media), "v", 1.0, W, H, 25.0, "video")],
                      tracks=[Track("V1", "V1", "video", clips=[Clip("c", "v", "V1", 0.0, 0.0, 1.0)])])
    project.tracks[0].clips[0].color_grade = grade
    frame = render_frame(build_render_plan(project), W, H, 0.3).astype(float)
    return frame[H // 2 - 4:H // 2 + 4, W // 2 - 4:W // 2 + 4].reshape(-1, 3).mean(axis=0)


@needs_ffmpeg
def test_exported_nodes_apply_one_after_the_other(tmp_path):
    """Un orangé : un nœud seul rend les pixels de son réglage ; « réchauffer puis désaturer » donne un gris, l'ordre
    inverse un gris réchauffé ; un nœud contourné ne compte plus."""
    plain = _rendered(tmp_path, WARM)
    assert np.abs(_rendered(tmp_path, as_graph(WARM).with_label("n1", "Look")) - plain).max() < 0.5
    warm_then_grey, grey_id = as_graph(WARM).with_node_after("n1", DESATURATED)
    grey = _rendered(tmp_path, warm_then_grey)
    assert np.ptp(grey) <= 4, grey                      # un gris sort à (118, 121, 118) même sans nœuds (4:2:0)
    grey_then_warm = _rendered(tmp_path, warm_then_grey.moved(grey_id, 0))
    assert grey_then_warm[0] > grey_then_warm[2] + 10, grey_then_warm
    bypassed = _rendered(tmp_path, warm_then_grey.with_grade(grey_id, DESATURATED.with_enabled(False)))
    assert np.abs(bypassed - plain).max() < 0.5


def test_the_monitor_lut_key_follows_every_node_lut(tmp_path):
    first, second = tmp_path / "a.cube", tmp_path / "b.cube"
    for path in (first, second):
        path.write_text("LUT_3D_SIZE 2\n" + "0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n", encoding="utf-8")
    graph, _ = as_graph(ColorGrade(lut=LUTResource.from_path(first))).with_node_after(
        "n1", ColorGrade(lut=LUTResource.from_path(second)))
    assert len(luts_of(graph)) == 2
    before = lut_key(graph)
    second.write_text(second.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert lut_key(graph) != before, "le .cube du deuxième nœud a changé : la LUT du moniteur se recuit"


# --- service --------------------------------------------------------------------------------------------------------


def _project(tmp_path) -> Project:
    return Project("p", width=W, height=H, fps=25.0,
                   media_assets=[MediaAsset("v", str(tmp_path / "v.mp4"), "v", 1.0, W, H, 25.0, "video")],
                   tracks=[Track("V1", "V1", "video", clips=[Clip("c", "v", "V1", 0.0, 0.0, 1.0)])])


def test_the_service_edits_the_chosen_node_and_falls_back_to_the_first(tmp_path):
    project = _project(tmp_path)
    service = ColorGradingService()
    service.set_grade(project, "c", WARM)
    assert project.tracks[0].clips[0].color_grade == WARM, "un seul nœud : un ColorGrade, comme avant"
    graph = service.edit_nodes(project, "c", lambda g: g.with_node_after("n1")[0])
    assert isinstance(project.tracks[0].clips[0].color_grade, ColorNodeGraph) and len(graph.nodes) == 2
    service.set_grade(project, "c", DESATURATED, "n2")
    assert service.get_grade(project, "c", "n2") == DESATURATED and service.get_grade(project, "c", "n1") == WARM
    assert service.get_grade(project, "c", "n9") == WARM, "nœud inconnu ici : le premier"
    service.reset_grade(project, "c", "n1")
    assert service.get_grade(project, "c", "n1") == ColorGrade()
    service.edit_nodes(project, "c", lambda g: g.without("n1"))
    assert project.tracks[0].clips[0].color_grade == DESATURATED, "retour à un seul nœud sans nom"
    project.tracks[0].clips[0].locked = True
    with pytest.raises(ColorGradingError):
        service.edit_nodes(project, "c", lambda g: g.with_node_after(None)[0])


# --- fichier --------------------------------------------------------------------------------------------------------


def test_nodes_survive_a_save_with_their_names_order_and_portable_luts(tmp_path):
    lut = tmp_path / "outside" / "look.cube"
    lut.parent.mkdir()
    lut.write_text("LUT_3D_SIZE 2\n" + "0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n", encoding="utf-8")
    project = _project(tmp_path)
    graph, second = as_graph(WARM).with_label("n1", "Balance").with_node_after(
        "n1", ColorGrade(lut=LUTResource.from_path(lut)))
    project.tracks[0].clips[0].color_grade = graph.with_grade(second, graph.node(second).grade.with_enabled(False))
    target = tmp_path / "projet" / "p.kut"
    save_project(project, str(target))
    raw = json.loads(target.read_text(encoding="utf-8"))["project"]["sequences"][0]["tracks"][0]["clips"][0]
    assert [node["id"] for node in raw["color_grade"]["nodes"]] == ["n1", second]
    assert raw["color_grade"]["links"] == [["n1", second, 0]]
    loaded = load_project(str(target)).tracks[0].clips[0].color_grade
    assert isinstance(loaded, ColorNodeGraph) and loaded.node("n1").label == "Balance"
    assert loaded.node("n1").grade == WARM and not loaded.node(second).enabled
    copied = loaded.node(second).grade.lut
    assert copied is not None and not copied.missing and "outside" not in copied.path, "LUT copiée dans le projet"


def test_a_project_saved_before_the_nodes_opens_as_a_plain_grade():
    old = {"exposure": 0.25, "contrast": 0.0, "saturation": 1.0, "temperature": 0.0, "hue": 0.0, "shadows": 0.0,
           "highlights": 0.0, "curves": {}, "lut": None, "enabled": True}
    assert _deserialize_color_grade(old) == ColorGrade(exposure=0.25)


@pytest.mark.parametrize("broken", [3, "n1", {"id": "n1"}, None, True], ids=["nombre", "texte", "objet", "nul", "bool"])
def test_a_project_with_malformed_nodes_or_links_still_opens(tmp_path, broken):
    """Fichier abîmé ou modifié à la main : ``nodes`` / ``links`` qui ne sont pas des listes ne font pas échouer
    l'ouverture du projet (avant : ``TypeError`` en les parcourant)."""
    project = _project(tmp_path)
    project.tracks[0].clips[0].color_grade = as_graph(WARM).with_node_after("n1")[0]
    target = tmp_path / "p.kut"
    save_project(project, str(target))
    payload = json.loads(target.read_text(encoding="utf-8"))
    grade = payload["project"]["sequences"][0]["tracks"][0]["clips"][0]["color_grade"]
    grade["links"] = broken
    target.write_text(json.dumps(payload), encoding="utf-8")
    assert [node.id for node in load_project(str(target)).tracks[0].clips[0].color_grade.order()] == ["n1", "n2"]
    grade["nodes"] = broken
    target.write_text(json.dumps(payload), encoding="utf-8")
    assert load_project(str(target)).tracks[0].clips[0].color_grade is None


def test_links_from_a_future_version_fall_back_to_the_file_order():
    """Des nœuds parallèles (étape 2) relus par cette version : remis en série plutôt que perdus."""
    raw = {"nodes": [{"id": "n1", "grade": {"exposure": 0.5}}, {"id": "n2", "grade": {"saturation": 0.5}},
                     {"id": "n3", "grade": None}, "abîmé"],
           "links": [["n1", "n2", 0], ["n1", "n3", 0]]}
    graph = graph_from_dict(raw, _deserialize_color_grade)
    assert graph is not None and [node.id for node in graph.order()] == ["n1", "n2", "n3"]
    assert graph.node("n1").grade.exposure == 0.5 and graph.node("n3").grade == ColorGrade()
    assert graph_from_dict({"nodes": []}, _deserialize_color_grade) is None
