"""Le format déclaratif (ADR-0005 étape 4) : la table de verbes, le décodeur strict, l'application par la session, le schéma généré et
les commandes ``apply`` / ``validate script.json`` / ``schema``.

Ce que le décodeur promet : un script mal formé est refusé **en entier, avec toutes ses erreurs**, avant d'avoir touché au projet ;
un script valide s'applique en tout ou rien ; rien n'y est évalué ; un fuzz n'en tire jamais autre chose qu'une ``ScriptInvalid``.
"""

from __future__ import annotations

import json
import random
import string
from copy import deepcopy
from pathlib import Path

import pytest

from core import cli
from core.audio_buses import find_bus
from core.project_io import load_project, project_payload, save_project
from core.project_model import MediaAsset, Project, Track
from core.scripting import (
    VERBS, ScriptInvalid, Session, apply_script, created_ids, json_schema, loads_script, parse_script, read_script,
)
from core.scripting.script import MAX_OPS, MAX_SCRIPT_BYTES, SCRIPT_VERSION
from core.scripting.verbs import Kind
from core.timeline_operations import find_clip
from ui import i18n


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _project() -> Project:
    return Project(
        "Démo", width=64, height=36, fps=30.0,
        media_assets=[MediaAsset("m", "/x/intro.mp4", "intro", 10.0, 64, 36, 30.0, "video", has_audio=True)],
        tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio")],
    )


def _script(*ops: dict) -> dict:
    return {"kut_script": SCRIPT_VERSION, "ops": list(ops)}


def _issues(data) -> list[tuple[str, str]]:
    with pytest.raises(ScriptInvalid) as caught:
        parse_script(data)
    return [(issue.code, issue.path) for issue in caught.value.issues]


# --- Le décodeur est strict ---------------------------------------------------------------------------------------------


def test_a_valid_script_decodes_with_its_aliases_resolved():
    script = parse_script(_script(
        {"op": "add_bus", "as": "voix", "name": "Voix"},
        {"op": "route_track", "track": "A1", "bus": {"ref": "voix"}},
        {"op": "add_bus_insert", "bus": {"ref": "voix"}, "effect": "compressor", "params": {"ratio": 4}, "id": "comp"},
    ))
    assert [op.verb.name for op in script.ops] == ["add_bus", "route_track", "add_bus_insert"]
    assert script.aliases == {"voix": "voix"}
    assert script.ops[0].args["id"] == "voix", "sans « id », l'alias sert d'identifiant : le script rejoué donne le même projet"
    assert script.ops[1].args["bus"] == "voix" and script.ops[2].args["id"] == "comp"


def test_every_problem_of_a_script_is_reported_in_one_pass():
    found = _issues({
        "kut_script": 1, "colour": 1,
        "ops": [
            {"op": "add_buss"},
            {"op": "move_clip", "clip": "a", "at": -1, "zz": 1},
            {"op": "set_bus_volume", "bus": "b"},
            "pas un objet",
        ],
    })
    assert ("unknown_field", "colour") in found
    assert ("unknown_verb", "ops[0].op") in found
    assert ("unknown_field", "ops[1].zz") in found and ("out_of_range", "ops[1].at") in found
    assert ("missing_field", "ops[2].volume_db") in found
    assert ("op_not_object", "ops[3]") in found


@pytest.mark.parametrize("data, code", [
    ([], "not_object"),
    ({"ops": []}, "missing_version"),
    ({"kut_script": SCRIPT_VERSION + 1, "ops": []}, "newer_version"),
    ({"kut_script": True, "ops": []}, "wrong_type"),
    ({"kut_script": 1}, "missing_ops"),
    ({"kut_script": 1, "ops": {}}, "wrong_type"),
])
def test_the_envelope_of_a_script_is_checked(data, code):
    assert code in {issue_code for issue_code, _path in _issues(data)}


def test_values_are_typed_and_bounded():
    bad = [
        {"op": "set_bus_volume", "bus": "b", "volume_db": "fort"},
        {"op": "set_bus_volume", "bus": "b", "volume_db": True},
        {"op": "set_bus_volume", "bus": "b", "volume_db": 1000},
        {"op": "set_bus_volume", "bus": "b", "volume_db": float("inf")},
        {"op": "set_bus_muted", "bus": "b", "muted": 1},
        {"op": "move_clip", "clip": "c", "at": 1e12},
        {"op": "add_bus_insert", "bus": "b", "effect": "reverb_inconnue"},
        {"op": "add_bus", "id": "a b"},
        {"op": "add_bus", "id": "x" * 65},
        {"op": "rename_bus", "bus": "b", "name": "a\x00b"},
        {"op": "rename_bus", "bus": "b", "name": ""},
        {"op": "route_track", "track": {"ref": "inconnu"}},
        {"op": "add_bus_insert", "bus": "b", "effect": "limiter", "params": {"a b": 1}},
        {"op": "add_bus_insert", "bus": "b", "effect": "limiter", "params": {"x": [1]}},
        {"op": "add_bus_insert", "bus": "b", "effect": "limiter", "params": {f"p{i}": i for i in range(40)}},
    ]
    for op in bad:
        with pytest.raises(ScriptInvalid):
            parse_script(_script(op))


def test_an_alias_must_exist_before_use_have_the_right_kind_and_be_defined_once():
    assert ("unknown_alias", "ops[0].bus") in _issues(_script({"op": "remove_bus", "bus": {"ref": "voix"}}))
    kind = _issues(_script({"op": "add_bus", "as": "voix"}, {"op": "move_clip", "clip": {"ref": "voix"}, "at": 1}))
    assert ("alias_kind", "ops[1].clip") in kind
    twice = _issues(_script({"op": "add_bus", "as": "voix"}, {"op": "add_bus", "as": "voix"}))
    assert ("duplicate_alias", "ops[1].as") in twice
    same_id = _issues(_script({"op": "add_bus", "id": "v"}, {"op": "add_bus", "id": "v"}))
    assert ("duplicate_id", "ops[1].id") in same_id


def test_a_misspelled_field_or_verb_comes_with_a_suggestion():
    with pytest.raises(ScriptInvalid) as caught:
        parse_script(_script({"op": "set_bus_volume", "bus": "b", "volume_dB": 1, "volume_db": 1}))
    issue = caught.value.issues[0]
    assert issue.code == "unknown_field" and issue.values()["suggestion"] == "volume_db"


def test_limits_on_size_and_number_of_operations():
    assert "too_many_ops" in {code for code, _ in _issues(_script(*[{"op": "add_marker", "at": 1}] * (MAX_OPS + 1)))}


def test_json_with_duplicate_keys_or_nan_is_refused():
    for text in ('{"kut_script": 1, "kut_script": 2, "ops": []}', '{"kut_script": 1, "ops": [{"op": "move_clip", "at": NaN}]}', "pas du json"):
        with pytest.raises(ScriptInvalid) as caught:
            loads_script(text)
        assert caught.value.issues[0].code == "not_json"


def test_a_script_file_is_size_capped_and_must_be_utf8(tmp_path):
    big = tmp_path / "big.json"
    big.write_bytes(b" " * (MAX_SCRIPT_BYTES + 1))
    with pytest.raises(ScriptInvalid) as caught:
        read_script(big)
    assert caught.value.issues[0].code == "too_large"
    broken = tmp_path / "latin.json"
    broken.write_bytes(b'{"kut_script": 1, "ops": [], "description": "\xe9"}')
    with pytest.raises(ScriptInvalid):
        read_script(broken)
    with pytest.raises(FileNotFoundError):
        read_script(tmp_path / "absent.json")


# --- Application --------------------------------------------------------------------------------------------------------


def test_a_script_builds_the_same_project_as_the_calls_it_names():
    script = parse_script(_script(
        {"op": "add_clip", "track": "V1", "asset": "intro", "at": 0.0, "as": "intro"},
        {"op": "add_bus", "as": "voix", "name": "Voix"},
        {"op": "route_track", "track": "A1", "bus": {"ref": "voix"}},
        {"op": "add_bus_insert", "bus": "Voix", "effect": "limiter", "id": "lim", "params": {"limit_db": -1.5}},
        {"op": "set_bus_volume", "bus": "voix", "volume_db": -6},
        {"op": "move_clip", "clip": {"ref": "intro"}, "at": 2.5},
        {"op": "add_marker", "at": 1.0, "name": "Début", "id": "m1"},
    ))
    session = Session(_project())
    report = apply_script(session, script, label="essai")
    assert report.ok, report.error
    project = session.project
    assert find_clip(project, "intro").timeline_start == 2.5
    bus = find_bus(project, "voix")
    assert bus.volume_db == -6 and [insert.id for insert in bus.inserts] == ["lim"]
    assert next(track for track in project.tracks if track.id == "A1").bus_id == "voix"
    assert [marker.id for marker in project.markers] == ["m1"]
    assert created_ids(script, report) == [
        {"op": "0:add_clip", "id": "intro"}, {"op": "1:add_bus", "id": "voix"}, {"op": "3:add_bus_insert", "id": "lim"},
        {"op": "6:add_marker", "id": "m1"},
    ]
    assert len(session.history.entries()) == 2, "une seule étape d'historique pour tout le script"


def test_replaying_a_script_gives_the_identical_project():
    text = _script(
        {"op": "add_clip", "track": "V1", "asset": "m", "at": 0, "as": "a"},
        {"op": "add_bus", "as": "voix"},
        {"op": "add_bus_insert", "bus": {"ref": "voix"}, "effect": "compressor", "as": "comp"},
    )
    first, second = Session(_project()), Session(_project())
    apply_script(first, parse_script(text))
    apply_script(second, parse_script(text))
    assert project_payload(first.project) == project_payload(second.project)


def test_a_refused_operation_leaves_the_project_untouched_and_names_the_operation():
    session = Session(_project())
    before = project_payload(session.project)
    script = parse_script(_script(
        {"op": "add_bus", "as": "voix"},
        {"op": "move_clip", "clip": "inexistant", "at": 1},
        {"op": "add_bus", "as": "autre"},
    ))
    report = apply_script(session, script)
    assert not report.ok and report.failed_index == 1 and report.failed_name == "1:move_clip"
    assert project_payload(session.project) == before and not session.can_undo


def test_selectors_choose_by_id_first_then_by_unique_name():
    project = _project()
    project.tracks.append(Track("A2", "A1", "audio"))                       # deux pistes « A1 » : l'identifiant de l'une, le nom de l'autre
    session = Session(project)
    assert apply_script(session, parse_script(_script({"op": "add_bus", "id": "b", "name": "Voix"}))).ok
    by_name = apply_script(session, parse_script(_script({"op": "route_track", "track": "A2", "bus": "Voix"})))
    assert by_name.ok and next(track for track in session.project.tracks if track.id == "A2").bus_id == "b"
    by_id = apply_script(session, parse_script(_script({"op": "route_track", "track": "A1", "bus": "b"})))
    assert by_id.ok, "« A1 » est l'identifiant de la première piste : l'identifiant l'emporte sur le nom de la seconde"
    assert next(track for track in session.project.tracks if track.id == "A1").bus_id == "b"


def test_an_ambiguous_name_is_an_error_not_a_choice():
    project = _project()
    project.tracks.append(Track("A2", "Voix", "audio"))
    project.tracks.append(Track("A3", "Voix", "audio"))
    report = apply_script(Session(project), parse_script(_script({"op": "add_bus", "id": "b"}, {"op": "route_track", "track": "Voix", "bus": "b"})))
    assert not report.ok and "ambigu" in report.error


# --- Le schéma ---------------------------------------------------------------------------------------------------------


def test_the_schema_is_generated_from_the_verb_table():
    schema = json_schema()
    variants = {item["properties"]["op"]["const"]: item for item in schema["properties"]["ops"]["items"]["oneOf"]}
    assert set(variants) == set(VERBS), "un verbe de la table = une variante du schéma, et réciproquement"
    for name, verb in VERBS.items():
        variant = variants[name]
        assert set(variant["properties"]) == {"op", *verb.accepts}
        assert set(variant["required"]) == {"op", *[param.name for param in verb.params if param.required]}
        assert variant["additionalProperties"] is False
    assert schema["additionalProperties"] is False and schema["required"] == ["kut_script", "ops"]
    json.dumps(schema)                                              # sérialisable tel quel


def test_every_verb_is_documented_and_creating_verbs_accept_id_and_alias():
    for name, verb in VERBS.items():
        assert verb.doc and all(param.doc for param in verb.params), name
        assert ("id" in verb.accepts) == bool(verb.creates) == ("as" in verb.accepts), name
        for param in verb.params:
            assert param.kind is not Kind.SELECTOR or param.of, f"{name}.{param.name} : genre de sélecteur absent"


# --- Fuzz --------------------------------------------------------------------------------------------------------------


def _random_value(rng: random.Random, depth: int = 0):
    choices = [
        lambda: rng.choice([0, 1, -1, 2.5, 1e308, -1e308, float("nan"), float("inf"), 10**30, True, False, None]),
        lambda: "".join(rng.choice(string.printable) for _ in range(rng.randint(0, 20))),
        lambda: rng.choice(["A1", "V1", "m", "b", "", "\x00", "é" * 300, "../../etc/passwd", "{ref}"]),
    ]
    if depth < 3:
        choices.append(lambda: [_random_value(rng, depth + 1) for _ in range(rng.randint(0, 3))])
        choices.append(lambda: {rng.choice(["ref", "op", "x", "volume_db"]): _random_value(rng, depth + 1) for _ in range(rng.randint(0, 3))})
    return rng.choice(choices)()


def _random_op(rng: random.Random) -> object:
    if rng.random() < 0.1:
        return _random_value(rng)
    verb = rng.choice(list(VERBS.values()))
    op: dict = {"op": verb.name if rng.random() < 0.9 else _random_value(rng)}
    for key in verb.accepts:
        if rng.random() < 0.8:
            op[key] = _random_value(rng)
    if rng.random() < 0.2:
        op["".join(rng.choice(string.printable) for _ in range(3)) if rng.random() < 0.5 else "extra"] = _random_value(rng)
    return op


def test_fuzz_the_decoder_only_ever_raises_script_invalid():
    rng = random.Random(20261013)
    decoded = 0
    for _ in range(1500):
        data = {"kut_script": 1, "ops": [_random_op(rng) for _ in range(rng.randint(0, 6))]}
        if rng.random() < 0.1:
            data[rng.choice(["ops", "kut_script", "x"])] = _random_value(rng)
        try:
            script = parse_script(data)
        except ScriptInvalid as error:
            assert error.issues and len(error.issues) <= 50
            continue
        decoded += 1
        report = apply_script(Session(_project()), script)             # un script décodé s'applique ou est refusé, jamais n'explose
        assert report.ok or report.failed_index is not None
    assert decoded > 0, "le fuzz doit aussi produire des scripts valides, sinon il ne teste que le refus"


def test_fuzz_random_json_text_never_escapes():
    rng = random.Random(7)
    pieces = ['{', '}', '[', ']', '"op"', '"ops"', ':', ',', '"kut_script"', '1', '"a"', 'null', 'true', '-0', '1e999', '\\u0000']
    for _ in range(800):
        text = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 25)))
        try:
            parse_script(loads_script(text))
        except ScriptInvalid:
            pass


# --- Les commandes -----------------------------------------------------------------------------------------------------


def _run(*argv: str, capsys) -> tuple[int, str, str]:
    code = cli.run(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def files(tmp_path) -> tuple[Path, Path]:
    project = tmp_path / "p.kut"
    save_project(_project(), str(project))
    script = tmp_path / "s.json"
    script.write_text(json.dumps(_script(
        {"op": "add_bus", "as": "voix", "name": "Voix"}, {"op": "route_track", "track": "A1", "bus": {"ref": "voix"}},
    )), encoding="utf-8")
    return script, project


def test_apply_writes_another_file_and_leaves_the_input_alone(files, tmp_path, capsys):
    script, project = files
    before = project.read_bytes()
    out = tmp_path / "out.kut"
    code, stdout, _err = _run("apply", str(script), str(project), "-o", str(out), "--json", capsys=capsys)
    data = json.loads(stdout)
    assert code == 0 and data["ok"] and data["written"] == str(out) and data["operations"] == 2
    assert data["created"] == [{"op": "0:add_bus", "id": "voix"}]
    assert project.read_bytes() == before, "l'entrée n'est jamais écrasée sans --in-place"
    assert [bus.id for bus in load_project(str(out)).buses] == ["voix"]


def test_apply_dry_run_reports_and_writes_nothing(files, tmp_path, capsys):
    script, project = files
    before = project.read_bytes()
    code, stdout, _err = _run("apply", str(script), str(project), "--dry-run", "--json", capsys=capsys)
    data = json.loads(stdout)
    assert code == 0 and data["dry_run"] is True and data["written"] is None
    assert project.read_bytes() == before and sorted(path.name for path in tmp_path.iterdir()) == ["p.kut", "s.json"]


def test_apply_refuses_to_overwrite_its_input_without_in_place(files, capsys):
    script, project = files
    before = project.read_bytes()
    code, stdout, err = _run("apply", str(script), str(project), "-o", str(project), "--json", capsys=capsys)
    assert code == cli.EXIT_USAGE and json.loads(stdout)["error"]["kind"] == "output_is_input" and "--in-place" in err
    code, _out, err = _run("apply", str(script), str(project), capsys=capsys)
    assert code == cli.EXIT_USAGE and "-o" in err
    assert project.read_bytes() == before


def test_apply_in_place_modifies_the_project(files, capsys):
    script, project = files
    code, _out, _err = _run("apply", str(script), str(project), "--in-place", capsys=capsys)
    assert code == 0 and [bus.id for bus in load_project(str(project)).buses] == ["voix"]
    code, _out, err = _run("apply", str(script), str(project), "--in-place", "-o", "x.kut", capsys=capsys)
    assert code == cli.EXIT_USAGE and "--in-place" in err


def test_apply_symlink_to_the_input_counts_as_the_input(files, tmp_path, capsys):
    script, project = files
    link = tmp_path / "lien.kut"
    link.symlink_to(project)
    before = project.read_bytes()
    code, _out, _err = _run("apply", str(script), str(project), "-o", str(link), "--overwrite", capsys=capsys)
    assert code == cli.EXIT_USAGE and project.read_bytes() == before


def test_apply_a_refused_operation_writes_nothing_and_says_which(files, tmp_path, capsys):
    script, project = files
    script.write_text(json.dumps(_script({"op": "add_bus", "id": "b"}, {"op": "remove_bus", "bus": "absent"})), encoding="utf-8")
    out = tmp_path / "out.kut"
    code, stdout, err = _run("apply", str(script), str(project), "-o", str(out), "--json", capsys=capsys)
    data = json.loads(stdout)
    assert code == cli.EXIT_FAILED and data["error"]["kind"] == "refused" and data["error"]["index"] == 1
    assert "1:remove_bus" in err and "Rien n'a été modifié" in err and not out.exists()


def test_apply_an_invalid_script_lists_every_error_and_never_touches_the_project(files, tmp_path, capsys):
    script, project = files
    script.write_text(json.dumps(_script({"op": "add_buss"}, {"op": "move_clip", "clip": "c", "at": -1})), encoding="utf-8")
    code, stdout, err = _run("apply", str(script), str(project), "-o", str(tmp_path / "o.kut"), "--json", capsys=capsys)
    data = json.loads(stdout)
    assert code == cli.EXIT_FAILED and data["error"]["kind"] == "script_invalid"
    assert {issue["code"] for issue in data["error"]["issues"]} == {"unknown_verb", "out_of_range"}
    assert "vouliez-vous dire « add_bus »" in err and not (tmp_path / "o.kut").exists()


def test_validate_checks_a_script_without_a_project(files, capsys):
    script, _project_path = files
    code, stdout, _err = _run("validate", str(script), "--json", capsys=capsys)
    assert code == 0 and json.loads(stdout) == {"kut_cli": 1, "ok": True, "file": str(script), "kind": "script", "operations": 2}
    script.write_text("{", encoding="utf-8")
    code, _out, err = _run("validate", str(script), capsys=capsys)
    assert code == cli.EXIT_FAILED and "JSON illisible" in err


def test_schema_prints_the_generated_schema(capsys):
    code, stdout, _err = _run("schema", capsys=capsys)
    assert code == 0 and json.loads(stdout) == json_schema()


def test_script_errors_are_translated(files, capsys):
    script, project = files
    script.write_text(json.dumps(_script({"op": "add_buss"})), encoding="utf-8")
    _code, _out, err = _run("apply", str(script), str(project), "--dry-run", "--lang", "en", capsys=capsys)
    assert "unknown verb" in err and "Invalid script" in err
    _code, _out, err = _run("apply", str(script), str(project), "--dry-run", "--lang", "es", capsys=capsys)
    assert "verbo desconocido" in err and "Script no válido" in err


def test_script_messages_exist_in_every_language_for_every_issue_code():
    import re
    from pathlib import Path as P

    source = (P(__file__).resolve().parent.parent / "core" / "scripting" / "script.py").read_text(encoding="utf-8")
    codes = set(re.findall(r'issue\(\s*"([a-z_]+)"', source)) | set(re.findall(r'ScriptIssue\("([a-z_]+)"', source))
    assert codes
    for language in ("fr", "en", "es"):
        i18n.set_language(language)
        for code in sorted(codes):
            key = f"cli.script.issue.{code}"
            assert i18n.translate(key) != key, f"{key} manque en {language}"


def test_a_script_is_never_executed_it_only_names_verbs():
    """R1 : le décodeur ne connaît que la table ; un texte qui ressemble à du code reste un texte."""
    sneaky = _script({"op": "__import__('os').system('x')"}, {"op": "rename_bus", "bus": "b", "name": "${HOME}; rm -rf /"})
    assert ("unknown_verb", "ops[0].op") in _issues(sneaky)
    session = Session(_project())
    assert apply_script(session, parse_script(_script({"op": "add_bus", "id": "b"}, {"op": "rename_bus", "bus": "b", "name": "${HOME}; rm -rf /"}))).ok
    assert find_bus(session.project, "b").name == "${HOME}; rm -rf /"
    deepcopy(session.project)


def test_the_documentation_lists_exactly_the_verbs_of_the_table():
    """docs/scripting.md ne dérive pas de la table : chaque verbe y a sa ligne, et rien d'autre n'y est présenté comme un verbe."""
    import re

    text = (Path(__file__).resolve().parent.parent / "docs" / "scripting.md").read_text(encoding="utf-8")
    table = text[text.index("## Les verbes"):text.index("## Pas encore là")]
    documented = set(re.findall(r"^\| `([a-z_]+)` \|", table, flags=re.M))
    assert documented == set(VERBS)
