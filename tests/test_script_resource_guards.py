"""Garde-fou des plafonds de ressources des verbes de script (ADR-0005) : un verbe qui CRÉE ne peut plus oublier sa garde.

Trois contrôles : (1) tout verbe créateur de la table ``VERBS`` (découverte automatique) est inscrit — plafond prouvé, borné par
construction, ou dette nommée ; (2) pour chaque plafond inscrit, un projet déjà au plafond refuse le verbe, sans changer le projet, que les
objets soient dans la séquence active ou dans une autre ; (3) la dette ne peut que diminuer. Voir ``tests/script_resource_guards.py``.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator

import pytest

from core.scripting.verbs import VERBS
from tests import script_resource_guards as guards
from tests.script_resource_guards import (
    BOUNDED, CAP, CEILINGS, DEBT_SPECS, EXCLUDED_RESOURCES, RESOURCES, Spec, attempt, case, discovered_creators, load_debt, make_context,
)


@pytest.fixture(autouse=True)
def _fake_probe(monkeypatch):
    """``import_media`` lit un fichier : une sonde sans ffprobe (le test porte sur le plafond, pas sur le média)."""
    from core.project_model import MediaAsset

    monkeypatch.setattr("core.scripting.verbs.probe_media", lambda path: MediaAsset("probe", path, "p", 1.0, 8, 8, 30.0, "video"))


def _ceiling_cases() -> Iterator[object]:
    for verb, specs in sorted(CEILINGS.items()):
        for spec in specs:
            for placement in spec.placements:
                if placement == "other" and not RESOURCES[spec.resource].cross_sequence:
                    continue
                yield pytest.param(verb, spec, placement, id=f"{verb}-{spec.resource}-{placement}")


@pytest.fixture
def lowered(monkeypatch):
    """Abaisse les constantes de plafond d'une ressource à ``cap`` (les modules lisent leurs constantes à l'appel)."""

    def lower(spec: Spec) -> None:
        for module, name in RESOURCES[spec.resource].caps:
            monkeypatch.setattr(importlib.import_module(module), name, spec.cap)

    return lower


def _decided_verbs(debt: dict[str, str]) -> set[str]:
    return set(CEILINGS) | set(BOUNDED) | {key.split(":", 1)[0] for key in debt}


# --- (1) Tout verbe créateur est inscrit ---------------------------------------------------------------------------------


def test_every_creating_verb_is_decided():
    creators = discovered_creators()
    assert creators, "la découverte ne trouve plus aucun verbe créateur : elle est cassée"
    undecided = sorted(set(creators) - _decided_verbs(load_debt()))
    assert not undecided, (
        "Verbe(s) qui créent des objets sans décision de plafond : " + ", ".join(f"{name} ({creators[name]})" for name in undecided)
        + ". Inscrivez-les dans tests/script_resource_guards.py : CEILINGS (garde prouvée), BOUNDED (borné, avec la raison) ou, "
        "à défaut de plafond, dans tests/data/script_resource_debt.json (dette nommée, visible en revue)."
    )


def test_the_inventory_names_real_creating_verbs_and_each_only_once():
    creators = discovered_creators()
    debt = load_debt()
    stale = sorted(name for name in _decided_verbs(debt) if name not in VERBS)
    assert not stale, f"Verbe(s) inscrits qui n'existent plus : {stale}"
    not_creators = sorted(name for name in _decided_verbs(debt) if name not in creators)
    assert not not_creators, f"Verbe(s) inscrits qui ne sont pas (ou plus) découverts comme créateurs : {not_creators}"
    assert not set(CEILINGS) & set(BOUNDED), "un verbe est à la fois en CEILINGS et en BOUNDED"
    assert all(reason.strip() for reason in (*BOUNDED.values(), *debt.values())), "une raison est vide"
    for key in debt:
        assert ":" in key and key.split(":", 1)[0] in VERBS, key
    for verb in BOUNDED:
        assert verb not in {key.split(":", 1)[0] for key in debt}, f"{verb} : borné ET en dette"


def test_the_debt_only_shrinks():
    """Une dette sondée par exécution échoue dès que le verbe refuse : il faut alors payer la dette (retirer l'entrée, inscrire le verbe)."""
    debt = load_debt()
    assert not set(DEBT_SPECS) - set(debt), f"sondes sans entrée de dette : {sorted(set(DEBT_SPECS) - set(debt))}"
    for key in debt:
        verb, resource = key.split(":", 1)
        assert not any(spec.resource == resource for spec in CEILINGS.get(verb, ())), f"{key} : déjà prouvé dans CEILINGS, retirez la dette"


@pytest.mark.parametrize("key", sorted(DEBT_SPECS))
def test_a_probed_debt_is_still_a_debt(key, tmp_path):
    spec = DEBT_SPECS[key]
    verb = key.split(":", 1)[0]
    resource = RESOURCES[spec.resource]
    project = case(spec, spec.placements[0], total=resource.big)
    error, _same = attempt(verb, spec, project, make_context(tmp_path) if spec.context else None)
    assert error is None, (
        f"{verb} refuse maintenant au-delà de {resource.big} {spec.resource} ({error}) : la dette {key} est payée. Retirez-la de "
        "tests/data/script_resource_debt.json et inscrivez le verbe dans CEILINGS (tests/script_resource_guards.py)."
    )


# --- (2) Un projet au plafond refuse, sans changement ---------------------------------------------------------------------


@pytest.mark.parametrize(("verb", "spec", "placement"), list(_ceiling_cases()))
def test_a_project_at_the_ceiling_refuses_the_verb_and_stays_unchanged(verb, spec, placement, lowered, tmp_path):
    lowered(spec)
    context = make_context(tmp_path) if spec.context else None
    # La place d'exactement l'opération : elle passe (les arguments sont bons, et la garde ne refuse pas trop tôt) ...
    error, _same = attempt(verb, spec, case(spec, placement, total=spec.cap - spec.adds), context)
    assert error is None, f"{verb} : refusé alors qu'il restait la place d'exactement {spec.adds} {spec.resource} : {error}"
    # ... un objet de moins de place : refusé, AVANT de créer, projet strictement inchangé.
    error, unchanged = attempt(verb, spec, case(spec, placement, total=spec.cap - spec.adds + 1), context)
    assert error is not None, f"{verb} a créé des {spec.resource} au-delà du plafond ({spec.cap}) : sa garde manque ou ne compte pas tout le projet"
    assert unchanged, f"{verb} : refusé mais le projet a changé (la garde doit précéder toute création)"


def test_set_automation_curve_is_capped(monkeypatch):
    monkeypatch.setattr(importlib.import_module("core.scripting.audio_rules"), "MAX_AUTOMATION_POINTS", CAP)
    monkeypatch.setattr(importlib.import_module("core.scripting.audio_verbs"), "MAX_AUTOMATION_POINTS", CAP)
    def curve(count: int) -> Spec:
        return Spec("points", {"track": "A1", "points": [{"time_seconds": 0.1 * (i + 1), "gain_db": -3.0} for i in range(count)]})
    ok = curve(CAP)
    assert attempt("set_automation_curve", ok, case(ok, "active", total=0))[0] is None
    over = curve(CAP + 1)
    error, unchanged = attempt("set_automation_curve", over, case(over, "active", total=0))
    assert error is not None and unchanged


# --- Le garde-fou prouve qu'il échoue -------------------------------------------------------------------------------------


def test_an_unguarded_creator_is_caught_by_the_behavioural_check(monkeypatch, lowered):
    """Sans sa garde, ``add_graphic`` créerait au-delà du plafond : le même contrôle le voit (la preuve que le test n'est pas vide)."""
    verbs_module = importlib.import_module("core.scripting.verbs")
    monkeypatch.setattr(verbs_module, "require_layers_room", lambda project, adding, **kw: None)
    spec = CEILINGS["add_graphic"][0]
    lowered(spec)
    error, _unchanged = attempt("add_graphic", spec, case(spec, "other", total=spec.cap - spec.adds + 1))
    assert error is None


def test_a_guard_that_counts_only_the_active_sequence_is_caught(monkeypatch, lowered):
    """Le piège connu : ``Project.tracks`` ne montre que la séquence active. Une garde qui ne compte que celle-ci laisse passer l'autre."""
    layer_rules = importlib.import_module("core.scripting.layer_rules")
    monkeypatch.setattr(layer_rules, "total_layers", lambda project: sum(
        1 for track in project.tracks for clip in track.clips if clip.graphic is not None))
    spec = CEILINGS["add_graphic"][0]
    lowered(spec)
    assert attempt("add_graphic", spec, case(spec, "active", total=spec.cap - spec.adds + 1))[0] is not None
    assert attempt("add_graphic", spec, case(spec, "other", total=spec.cap - spec.adds + 1))[0] is None


def test_a_new_creating_verb_is_discovered(monkeypatch):
    """Un verbe neuf, qui crée par ``creates=`` ou en appelant une fonction de création, est vu sans rien écrire dans la liste."""
    from dataclasses import replace

    verb = VERBS["add_marker"]
    fake = {**VERBS, "brand_new": replace(verb, name="brand_new")}
    monkeypatch.setattr(guards, "VERBS", fake)
    assert "brand_new" in discovered_creators()
    assert "brand_new" not in _decided_verbs(load_debt())
    fake_plain = {**VERBS, "plain": replace(VERBS["set_clip_gain"], name="plain")}
    monkeypatch.setattr(guards, "VERBS", fake_plain)
    assert "plain" not in discovered_creators()


def test_the_discovery_sees_creation_through_a_helper(monkeypatch):
    from dataclasses import replace

    verb = replace(VERBS["add_flash"], name="no_creates_attribute", creates=None)
    monkeypatch.setattr(guards, "VERBS", {"no_creates_attribute": verb})
    assert "no_creates_attribute" in discovered_creators()


# --- Une spec par RESSOURCE consommée, pas une par verbe -----------------------------------------------------------------


def _counts(project) -> dict[str, int]:
    counts = {}
    for name, resource in RESOURCES.items():
        try:
            counts[name] = resource.count(project)
        except (AttributeError, KeyError, StopIteration):          # ressource absente de ce projet minimal (angles sans source Multicam…)
            counts[name] = 0
    return counts


def test_every_resource_a_verb_consumes_has_a_spec(tmp_path):
    """Mesure, pour chaque verbe prouvé, les comptes avant / après l'opération : toute ressource qui augmente a sa spec de plafond, sa
    dette (``verbe:ressource``) ou une exclusion écrite. Retirer une des gardes d'un verbe à deux ressources ne peut plus passer inaperçu."""
    debt = load_debt()
    context = make_context(tmp_path)
    for verb, specs in sorted(CEILINGS.items()):
        for spec in specs:
            project = guards.base_project()
            if spec.prepare is not None:
                spec.prepare(project)
            before = _counts(project)
            build = VERBS[verb].build
            (build(dict(spec.args), context) if spec.context else build(dict(spec.args)))(project)
            after = _counts(project)
            grown = {name for name in after if after[name] > before[name]}
            covered = {s.resource for s in specs} | {key.split(":", 1)[1] for key in debt if key.split(":", 1)[0] == verb}
            covered |= {resource for (name, resource) in EXCLUDED_RESOURCES if name == verb}
            assert grown <= covered, (
                f"{verb} fait croître {sorted(grown - covered)} sans spec de plafond : ajoutez une Spec à CEILINGS, une dette "
                "« verbe:ressource », ou une exclusion écrite (EXCLUDED_RESOURCES) dans tests/script_resource_guards.py."
            )
    for (verb, resource), reason in EXCLUDED_RESOURCES.items():
        assert verb in CEILINGS and resource in RESOURCES and reason.strip(), (verb, resource)
        assert resource not in {s.resource for s in CEILINGS[verb]}, f"{verb}/{resource} : exclu mais aussi prouvé"


def test_a_missing_second_resource_is_caught(monkeypatch):
    """Un verbe à deux ressources dont on retire la spec de clips est vu par la mesure (preuve que le test n'est pas vide)."""
    monkeypatch.setitem(CEILINGS, "create_format_version", tuple(s for s in CEILINGS["create_format_version"] if s.resource != "clips"))
    with pytest.raises(AssertionError, match="create_format_version fait croître"):
        test_every_resource_a_verb_consumes_has_a_spec(__import__("pathlib").Path(__import__("tempfile").mkdtemp()))
