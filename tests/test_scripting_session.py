"""La session d'édition d'un script (``core/scripting/session.py``, ADR-0005 étape 2).

Quatre promesses, chacune prouvée par comparaison de l'état complet du projet (``project_payload``) :

* **tout ou rien** : une opération refusée laisse le projet exactement comme avant, historique compris ;
* **une étape d'historique** par transaction réussie, annulable et rétablissable ;
* **un bogue n'est pas un refus** : une erreur de programmation restaure le projet puis se propage ;
* **le même résultat que l'interface** : après la même édition, le projet d'une session est celui de la fenêtre (recadrage des
  séquences imbriquées compris).
"""

from __future__ import annotations

from copy import deepcopy
from functools import partial

import pytest
from main_window_harness import build_window, install_dialogs

from core.audio_buses import add_bus, add_bus_insert, route_track
from core.audio_effects_model import AudioEffectType
from core.object_ids import InvalidObjectId
from core.project_io import project_payload
from core.project_model import MediaAsset, Project, Track
from core.scripting import ApplyReport, Session, SessionBusyError
from core.sequences import create_sequence_from_selection
from core.timeline_operations import add_clip_to_track, delete_clip, find_clip


def _project() -> Project:
    return Project(
        "p", width=64, height=36, fps=30.0,
        media_assets=[MediaAsset("m", "/x/m.mp4", "m", 10.0, 64, 36, 30.0, "video", has_audio=False)],
        tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio")],
    )


def _nested_project() -> Project:
    """Une séquence « scene » (deux clips de 10 s) imbriquée dans la principale ; « scene » est la séquence active."""
    project = _project()
    add_clip_to_track(project, "m", "V1", 0.0, clip_id="a")
    add_clip_to_track(project, "m", "V1", 10.0, clip_id="b")
    create_sequence_from_selection(project, ["a", "b"], "Scène", sequence_id="scene", nested_clip_id="nest")
    project.active_sequence_id = "scene"
    return project


def _snapshot(project: Project) -> dict:
    return project_payload(project)


def _comparable(project: Project) -> dict:
    """L'état du projet **sans** ``library_assignments`` : le panneau de bibliothèque de l'interface y crée à la demande
    l'emplacement d'un média dans le classeur (un effet d'affichage, pas une édition) ; une session n'a pas de panneau."""
    payload = project_payload(project)
    payload["project"] = {key: value for key, value in payload["project"].items() if key != "library_assignments"}
    return payload


def _nested_clip(project: Project):
    """Le clip imbriqué ``nest``, qui vit dans la séquence **principale** (la séquence active est ``scene``)."""
    return find_clip(_in_main(project), "nest")


def _in_main(project: Project) -> Project:
    """Le projet vu depuis la séquence principale (``find_clip`` ne regarde que la séquence active)."""
    view = deepcopy(project)
    view.active_sequence_id = "seq-main"
    return view


def _entries(session: Session) -> int:
    return len(session.history.entries())


# --- Tout ou rien -----------------------------------------------------------------------------------------------------


def test_successful_operations_are_applied_in_one_history_step_and_their_values_are_returned():
    project = _project()
    session = Session(project)
    before = _entries(session)
    report = session.apply(
        [("add intro", partial(add_clip_to_track, asset_id="m", track_id="V1", timeline_start=0.0, clip_id="intro")),
         ("add suite", partial(add_clip_to_track, asset_id="m", track_id="V1", timeline_start=10.0, clip_id="suite"))],
        label="Montage")
    assert isinstance(report, ApplyReport) and report.ok and report.failed_index is None
    assert [clip.id for clip in report.values] == ["intro", "suite"], "ce que chaque opération a créé est rendu"
    assert [result.name for result in report.results] == ["add intro", "add suite"]
    assert [clip.id for clip in project.tracks[0].clips] == ["intro", "suite"]
    assert session.project is project
    assert _entries(session) == before + 1 and session.history.undo_label == "Montage"


def test_a_refused_operation_restores_the_project_and_leaves_no_history_entry():
    project = _project()
    session = Session(project)
    before, entries, revision = _snapshot(project), _entries(session), session.history.revision
    report = session.apply(
        [partial(add_clip_to_track, asset_id="m", track_id="V1", timeline_start=0.0, clip_id="intro"),
         ("ajout refusé", partial(add_clip_to_track, asset_id="absent", track_id="V1", timeline_start=1.0))],
        label="Montage")
    assert not report.ok and report.failed_index == 1 and report.failed_name == "ajout refusé"
    assert report.error_type == "KeyError" and "absent" in report.error
    assert [result.name for result in report.results] == ["add_clip_to_track"], "le nom vient de la fonction enveloppée"
    assert _snapshot(project) == before, "la première opération est annulée aussi"
    assert _entries(session) == entries and session.history.revision == revision, "l'historique n'a pas bougé"
    assert session.project is project


def test_an_operation_that_changes_the_project_then_fails_is_rolled_back_too():
    project = _project()
    session = Session(project)
    before = _snapshot(project)

    def half_done(p: Project):
        add_clip_to_track(p, "m", "V1", 0.0, clip_id="moitie")
        raise ValueError("échec après une modification")

    report = session.apply([half_done], label="x")
    assert not report.ok and report.failed_name == "half_done" and "échec après" in report.error
    assert _snapshot(project) == before


def test_an_invalid_explicit_id_is_a_refusal_not_a_crash():
    project = _project()
    session = Session(project)
    report = session.apply([partial(add_clip_to_track, asset_id="m", track_id="V1", timeline_start=0.0, clip_id="mauvais id")],
                           label="x")
    assert not report.ok and report.error_type == InvalidObjectId.__name__
    assert project.tracks[0].clips == []


def test_a_programming_error_restores_the_project_then_propagates():
    project = _project()
    session = Session(project)
    before, entries = _snapshot(project), _entries(session)

    def buggy(p: Project):
        add_clip_to_track(p, "m", "V1", 0.0, clip_id="x")
        return None + 1                                       # type: ignore[operator]

    with pytest.raises(TypeError):
        session.apply([buggy], label="x")
    assert _snapshot(project) == before and _entries(session) == entries
    assert session.apply([], label="après").ok, "la session reste utilisable"


def test_without_operations_nothing_is_recorded():
    session = Session(_project())
    entries = _entries(session)
    report = session.apply([], label="rien")
    assert report.ok and report.results == () and _entries(session) == entries


# --- Transaction (bloc with) ------------------------------------------------------------------------------------------


def test_a_transaction_block_commits_on_exit_and_reports():
    project = _project()
    session = Session(project)
    entries = _entries(session)
    with session.transaction("Bloc") as tx:
        clip = tx.run(partial(add_clip_to_track, asset_id="m", track_id="V1", timeline_start=0.0, clip_id="c1"))
        tx.run(partial(add_clip_to_track, asset_id="m", track_id="V1", timeline_start=10.0, clip_id="c2"), name="second")
    assert clip.id == "c1" and tx.report is not None and tx.report.ok
    assert [result.name for result in tx.report.results] == ["add_clip_to_track", "second"]
    assert _entries(session) == entries + 1 and len(project.tracks[0].clips) == 2


def test_an_exception_in_a_transaction_block_restores_the_project_and_propagates():
    project = _project()
    session = Session(project)
    before, entries = _snapshot(project), _entries(session)
    with pytest.raises(KeyError), session.transaction("Bloc") as tx:
        tx.run(partial(add_clip_to_track, asset_id="m", track_id="V1", timeline_start=0.0, clip_id="c1"))
        tx.run(partial(add_clip_to_track, asset_id="absent", track_id="V1", timeline_start=1.0))
    assert _snapshot(project) == before and _entries(session) == entries
    with session.transaction("Après") as tx:                  # la session s'est refermée proprement
        tx.run(partial(add_clip_to_track, asset_id="m", track_id="V1", timeline_start=0.0, clip_id="c1"))


def test_transactions_do_not_nest():
    session = Session(_project())
    with session.transaction("Externe"), pytest.raises(SessionBusyError):
        with session.transaction("Interne"):
            pass
    with session.transaction("Externe"), pytest.raises(SessionBusyError):
        session.apply([], label="Interne")
    assert session.apply([], label="libre").ok


# --- Revue de Codex (PR 124) ------------------------------------------------------------------------------------------


def test_undo_and_redo_are_refused_while_a_transaction_is_open_and_nothing_is_corrupted():
    """Annuler dans une transaction ferait avancer l'historique alors que la restauration de la transaction remet le projet en
    place sans le suivre : projet et historique ne se correspondraient plus."""
    project = _project()
    session = Session(project)
    session.apply([partial(add_clip_to_track, asset_id="m", track_id="V1", timeline_start=0.0, clip_id="a")], label="A")
    after_a = _snapshot(project)
    entries, can_undo = _entries(session), session.can_undo

    with pytest.raises(SessionBusyError, match="annuler"), session.transaction("Bloc"):
        session.undo()
    with pytest.raises(SessionBusyError, match="rétablir"), session.transaction("Bloc"):
        session.redo()
    assert _snapshot(project) == after_a and _entries(session) == entries and session.can_undo == can_undo
    assert not session.can_redo, "A n'a pas été annulée par la tentative"

    def undoes(_p: Project):
        session.undo()

    with pytest.raises(SessionBusyError):
        session.apply([undoes], label="x")
    assert _snapshot(project) == after_a and _entries(session) == entries
    assert session.undo() and session.redo(), "libre de nouveau une fois la transaction fermée"


@pytest.mark.parametrize("error", [IndexError("hors liste"), ZeroDivisionError("division"), OverflowError("trop grand"),
                                   LookupError("recherche"), ArithmeticError("calcul")])
def test_accidental_errors_are_programming_errors_not_refusals(error):
    """``LookupError`` et ``ArithmeticError`` regroupent des bogues (``IndexError``, ``ZeroDivisionError``…) : ils ne doivent pas
    passer pour le refus d'une opération. Seuls ``ValueError``, ``KeyError`` et ``OSError`` (ce que ``core/`` lève pour refuser)
    sont rendus dans le rapport."""
    project = _project()
    session = Session(project)
    before = _snapshot(project)

    def buggy(p: Project):
        add_clip_to_track(p, "m", "V1", 0.0, clip_id="x")
        raise error

    with pytest.raises(type(error)):
        session.apply([buggy], label="x")
    assert _snapshot(project) == before, "restauré malgré la propagation"


@pytest.mark.parametrize("error", [ValueError("v"), KeyError("k"), FileNotFoundError("f"), InvalidObjectId("i")])
def test_the_errors_core_raises_to_refuse_are_reported(error):
    def refuses(_p: Project):
        raise error

    report = Session(_project()).apply([refuses], label="x")
    assert not report.ok and report.error_type == type(error).__name__


def test_a_failing_iterable_is_not_mistaken_for_a_failed_operation_and_nothing_is_applied():
    """Un générateur qui lève en route : avant, l'erreur était rendue comme le refus de l'opération précédente (qui avait réussi)."""
    project = _project()
    session = Session(project)
    before, entries = _snapshot(project), _entries(session)

    def late_failure():
        yield partial(add_clip_to_track, asset_id="m", track_id="V1", timeline_start=0.0, clip_id="a")
        raise ValueError("l'itérable échoue à son deuxième élément")

    def early_failure():
        raise ValueError("l'itérable échoue avant son premier élément")
        yield  # pragma: no cover - fait de cette fonction un générateur

    for source in (late_failure(), early_failure()):
        with pytest.raises(ValueError, match="itérable"):
            session.apply(source, label="x")
        assert _snapshot(project) == before and _entries(session) == entries, "aucune opération n'a été appliquée"
    assert session.apply([], label="libre").ok, "la session reste utilisable"


def test_an_item_that_is_not_an_operation_is_refused_before_any_effect():
    project = _project()
    session = Session(project)
    before = _snapshot(project)
    with pytest.raises(TypeError, match="appelable"):
        session.apply([partial(add_clip_to_track, asset_id="m", track_id="V1", timeline_start=0.0, clip_id="a"), "pas une opération"],
                      label="x")
    assert _snapshot(project) == before


# --- Annuler / rétablir -----------------------------------------------------------------------------------------------


def test_undo_and_redo_walk_the_transactions_and_keep_the_same_project_object():
    project = _project()
    session = Session(project)
    base = _snapshot(project)
    session.apply([partial(add_clip_to_track, asset_id="m", track_id="V1", timeline_start=0.0, clip_id="un")], label="Un")
    after_one = _snapshot(project)
    session.apply([partial(add_clip_to_track, asset_id="m", track_id="V1", timeline_start=10.0, clip_id="deux")], label="Deux")
    after_two = _snapshot(project)
    assert session.can_undo and not session.can_redo

    assert session.undo() and _snapshot(project) == after_one and session.project is project
    assert session.undo() and _snapshot(project) == base
    assert not session.can_undo and session.undo() is False, "rien avant l'état initial"
    assert session.redo() and session.redo() and _snapshot(project) == after_two
    assert session.redo() is False
    assert session.project is project, "ceux qui référencent le projet le voient toujours"


def test_a_new_transaction_after_an_undo_drops_the_redo_branch():
    session = Session(_project())
    session.apply([partial(add_clip_to_track, asset_id="m", track_id="V1", timeline_start=0.0, clip_id="un")], label="Un")
    session.undo()
    assert session.can_redo
    session.apply([partial(add_clip_to_track, asset_id="m", track_id="V1", timeline_start=0.0, clip_id="autre")], label="Autre")
    assert not session.can_redo


def test_a_shared_history_is_used_and_not_replaced():
    from core.edit_history import ProjectHistory

    project = _project()
    history = ProjectHistory()
    history.reset(project)
    session = Session(project, history)
    session.apply([partial(add_clip_to_track, asset_id="m", track_id="V1", timeline_start=0.0, clip_id="c")], label="Via la session")
    assert session.history is history and history.undo_label == "Via la session"


# --- Recadrage des séquences imbriquées -------------------------------------------------------------------------------


def test_shortening_a_source_sequence_trims_the_nested_clip_in_the_same_transaction_and_reports_it():
    project = _nested_project()
    session = Session(project)
    assert _nested_clip(project).source_out == pytest.approx(20.0)
    entries = _entries(session)
    report = session.apply([partial(delete_clip, clip_id="b")], label="Raccourcir la scène")
    assert report.ok and len(report.adjustments) == 1
    adjustment = report.adjustments[0]
    assert (adjustment.sequence_id, adjustment.clip_id) == ("seq-main", "nest"), "la séquence qui contient le clip recadré"
    assert adjustment.old_source_out == pytest.approx(20.0) and adjustment.new_source_out == pytest.approx(10.0)
    assert _nested_clip(project).source_out == pytest.approx(10.0)
    assert _entries(session) == entries + 1, "le recadrage n'ajoute pas d'étape : il est dans la même"
    assert session.undo() and _nested_clip(project).source_out == pytest.approx(20.0), "annulé avec l'édition"


def test_a_refused_transaction_does_not_trim_anything():
    project = _nested_project()
    session = Session(project)
    before = _snapshot(project)
    report = session.apply([partial(delete_clip, clip_id="b"), partial(delete_clip, clip_id="inconnu")], label="x")
    assert not report.ok and report.adjustments == ()
    assert _snapshot(project) == before and _nested_clip(project).source_out == pytest.approx(20.0)


# --- Le même résultat que l'interface ---------------------------------------------------------------------------------


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    install_dialogs(monkeypatch)
    window = build_window(qtbot, monkeypatch, tmp_path / "config")
    # Annuler / rétablir resynchronise le moniteur, qui planifierait de vrais rendus des morceaux de composition (FFmpeg et
    # rasterisation Qt sur un média inexistant) pendant que la fenêtre se ferme : un worker de CI macOS en est mort deux fois
    # (segfault natif, sans cadre Python). Ces tests comparent des projets, pas l'aperçu.
    monkeypatch.setattr(type(window), "_schedule_composition_caches", lambda self, center: None)
    return window


def _load(window, project: Project) -> None:
    window.project = project
    window._reload_timeline_preserving_selection()
    window.history.reset(window.project)


def test_the_same_edit_gives_the_same_project_in_the_window_and_in_a_session(window):
    """Parité (ADR-0005) : ce que l'interface fait d'une suppression (l'opération du modèle, puis ``_record_history`` qui recadre
    les séquences imbriquées et enregistre) est ce que fait la session : même projet, à l'octet près."""
    _load(window, _nested_project())
    twin = _nested_project()
    assert _snapshot(window.project) == _snapshot(twin)
    session = Session(twin)

    delete_clip(window.project, "b")
    window._record_history("Supprimer")
    session.apply([partial(delete_clip, clip_id="b")], label="Supprimer")

    assert _snapshot(window.project) == _snapshot(session.project)
    assert _nested_clip(session.project).source_out == pytest.approx(10.0), "le recadrage a eu lieu des deux côtés"
    assert window.history.can_undo and session.can_undo
    assert len(window.history.entries()) == len(session.history.entries()), "une étape chacun"


def test_undo_gives_the_same_project_in_the_window_and_in_a_session(window):
    _load(window, _nested_project())
    twin = _nested_project()
    session = Session(twin)
    delete_clip(window.project, "b")
    window._record_history("Supprimer")
    session.apply([partial(delete_clip, clip_id="b")], label="Supprimer")
    window.undo_last()
    session.undo()
    assert _comparable(window.project) == _comparable(session.project) == _comparable(_nested_project())


def test_an_audio_handler_and_the_same_operation_in_a_session_agree(window):
    """Un gestionnaire de l'interface (sortie d'une piste vers un bus) et l'opération du modèle appliquée par la session."""
    def start() -> Project:
        project = _project()
        add_bus(project, "Voix", bus_id="voix")
        add_bus_insert(project, "voix", AudioEffectType.LIMITER, effect_id="lim")
        return project

    _load(window, start())
    session = Session(start())
    window.on_track_route_changed("A1", "voix")
    session.apply([partial(route_track, track_id="A1", bus_id="voix")], label="Sortie d'une piste")
    assert _snapshot(window.project) == _snapshot(session.project)
    assert window.project.tracks[1].bus_id == "voix"


def test_a_failing_snapshot_does_not_leave_the_session_busy(monkeypatch):
    """Revue Codex : si la copie de l'instantané échoue (mémoire, contenu non copiable), la session se referme avant que l'erreur
    se propage, au lieu de refuser toute transaction suivante."""
    import core.scripting.session as session_module

    session = Session(_project())
    real_deepcopy = session_module.deepcopy
    monkeypatch.setattr(session_module, "deepcopy", lambda _value: (_ for _ in ()).throw(MemoryError("copie impossible")))
    with pytest.raises(MemoryError):
        session.apply([partial(add_clip_to_track, asset_id="m", track_id="V1", timeline_start=0.0, clip_id="x")], label="Ajouter")
    with pytest.raises(MemoryError), session.transaction("Bloc"):
        pass
    monkeypatch.setattr(session_module, "deepcopy", real_deepcopy)

    report = session.apply([partial(add_clip_to_track, asset_id="m", track_id="V1", timeline_start=0.0, clip_id="x")], label="Ajouter")
    assert report.ok and find_clip(session.project, "x") is not None
