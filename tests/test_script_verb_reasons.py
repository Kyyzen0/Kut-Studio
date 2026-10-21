"""Les RAISONS de la dette des verbes (ADR-0005) : un reclassement durable ne se perd pas dans un merge.

``tests/data/script_verb_debt.json`` dit pourquoi une fonction du modèle n'a pas de verbe. Les raisons durables (« interne », « interface
seulement… », « lecture seule », « remplacé »…) sont posées à la main ; la version de ``main`` les écraserait au merge sans que le cliquet
« la dette ne grossit pas » ne s'en plaigne (il vérifie qu'une entrée existe, pas qu'elle porte la bonne raison). Elles sont donc
recopiées dans ``tests/data/script_verb_reclassified.json`` (``python -m tools.scripting_debt verbs --sync-reclassified``) et comparées ici.
"""

from __future__ import annotations

import json

from tools import scripting_debt as tool


def test_every_durable_reason_of_the_debt_is_the_written_one():
    result = tool.compare_reclassified(tool.load_verb_debt(), tool.load_reclassified())
    assert not result.wrong, (
        f"Reclassement(s) perdu(s) (un merge a écrasé la raison ?) : {result.wrong}. Rétablissez la raison dans "
        "tests/data/script_verb_debt.json ; si le changement est voulu : python -m tools.scripting_debt verbs --sync-reclassified."
    )
    assert not result.unregistered, (
        f"Raison(s) durable(s) non inscrite(s) dans tests/data/script_verb_reclassified.json : {result.unregistered}. "
        "Relisez-les puis : python -m tools.scripting_debt verbs --sync-reclassified."
    )
    assert not result.stale, (
        f"Entrée(s) du fichier des reclassements sans entrée de dette : {result.stale} (--sync-reclassified)."
    )
    assert not result.bad_reason, (
        f"Raison(s) inconnue(s) ou transitoire(s) dans les reclassements : {result.bad_reason}"
    )


def test_every_reason_in_use_is_described_and_transient_ones_say_so():
    debt = tool.load_verb_debt()
    assert set(debt.values()) <= set(tool.REASONS), sorted(
        set(debt.values()) - set(tool.REASONS)
    )
    for reason, description in tool.REASONS.items():
        assert description.strip(), reason
        if tool.is_transient(reason):
            assert "verbe" in description and (
                "à venir" in description or "aura un verbe" in description
            ), (
                f"« {reason} » est une promesse : sa description doit dire qu'un verbe est à venir."
            )
        else:
            assert not reason.startswith("à faire"), reason


def test_the_transient_categories_may_become_empty(capsys):
    """Les catégories « à faire » se vident verbe après verbe : rien ne casse quand il n'en reste plus."""
    debt = {"core.x.a": "interne", "core.x.b": "lecture seule"}
    assert tool.transient_entries(debt) == {}
    assert tool.compare_reclassified(debt, tool.durable_entries(debt)).clean
    with_todo = debt | {"core.x.c": "à faire : audio"}
    assert tool.transient_entries(with_todo) == {"à faire : audio": ["core.x.c"]}
    assert tool.compare_reclassified(
        with_todo, tool.durable_entries(debt)
    ).clean  # une promesse n'a pas à être inscrite
    assert tool.durable_entries(with_todo) == debt


def test_what_is_still_to_do_is_listed():
    """La liste de ce qu'il reste à faire se lit dans la commande (le test ne fige aucun compte : il se vide au fil des lots)."""
    todo = tool.transient_entries(tool.load_verb_debt())
    assert all(tool.is_transient(reason) and names for reason, names in todo.items())


# --- Le garde-fou prouve qu'il échoue ---------------------------------------------------------------------------------------


def test_a_changed_reason_is_caught():
    debt = tool.load_verb_debt()
    reclassified = tool.load_reclassified()
    victim = next(
        name
        for name, reason in sorted(reclassified.items())
        if reason != "lecture seule"
    )
    broken = debt | {
        victim: "lecture seule"
    }  # ce que ferait un merge qui reprend la version de main
    result = tool.compare_reclassified(broken, reclassified)
    assert [item.split(" : ")[0] for item in result.wrong] == [victim]
    assert not result.clean


def test_a_durable_reason_downgraded_to_a_promise_or_dropped_is_caught():
    debt = tool.load_verb_debt()
    reclassified = tool.load_reclassified()
    victim = sorted(reclassified)[0]
    assert tool.compare_reclassified(
        debt | {victim: "à faire : audio"}, reclassified
    ).wrong
    assert tool.compare_reclassified(
        {k: v for k, v in debt.items() if k != victim}, reclassified
    ).stale == [victim]
    assert tool.compare_reclassified(
        debt, {k: v for k, v in reclassified.items() if k != victim}
    ).unregistered == [victim]
    assert tool.compare_reclassified(
        debt, reclassified | {victim: "à faire : audio"}
    ).bad_reason == [victim]
    assert tool.compare_reclassified(
        debt, reclassified | {victim: "parce que"}
    ).bad_reason == [victim]


def test_the_command_checks_and_regenerates_the_file(tmp_path, monkeypatch, capsys):
    debt_path = tmp_path / "debt.json"
    reclass_path = tmp_path / "reclassified.json"
    tool.write_verb_debt(
        {"core.x.a": "interne", "core.x.b": "à faire : audio"}, debt_path
    )
    read = lambda path: dict(json.loads(path.read_text(encoding="utf-8"))["verbs"])  # noqa: E731 - les chemins par défaut sont liés à la définition
    monkeypatch.setattr(tool, "RECLASSIFIED_PATH", reclass_path)
    monkeypatch.setattr(tool, "load_verb_debt", lambda: read(debt_path))
    monkeypatch.setattr(tool, "load_reclassified", lambda: read(reclass_path))
    tool.write_reclassified({"core.x.a": "lecture seule"}, reclass_path)
    assert (
        tool.main(["verbs", "--check-reclassified"]) == 1
    )  # le reclassement attendu n'est plus celui de la dette
    assert tool.main(["verbs", "--sync-reclassified"]) == 0
    assert json.loads(reclass_path.read_text(encoding="utf-8"))["verbs"] == {
        "core.x.a": "interne"
    }
    assert tool.main(["verbs", "--check-reclassified"]) == 0
    assert "encore à faire" in capsys.readouterr().out
    assert tool.main(["fields", "--check-reclassified"]) == 2
