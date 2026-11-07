"""La sonde du spike bac à sable (``packaging/store-spike/probe.py``) ne doit jamais modifier le fichier de l'utilisateur."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

PROBE = Path(__file__).resolve().parent.parent / "packaging" / "store-spike" / "probe.py"


def _load():
    spec = importlib.util.spec_from_file_location("store_spike_probe", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_file_tests_leaves_the_chosen_file_untouched(tmp_path, capsys):
    probe = _load()
    project = tmp_path / "projet.kut"
    project.write_bytes(b'{"format": 17, "contenu": "precieux"}')
    for sibling in ("projet.kut.lock", "projet.kut.autosave"):
        (tmp_path / sibling).write_bytes(b"existant")
    before = hashlib.sha256(project.read_bytes()).hexdigest()
    names_before = sorted(entry.name for entry in tmp_path.iterdir())

    probe.file_tests(project)

    assert hashlib.sha256(project.read_bytes()).hexdigest() == before
    assert sorted(entry.name for entry in tmp_path.iterdir()) == names_before      # copies voisines détruites, rien d'autre touché
    assert (tmp_path / "projet.kut.lock").read_bytes() == b"existant"
    assert (tmp_path / "projet.kut.autosave").read_bytes() == b"existant"
    assert '"ok": true' in capsys.readouterr().out                                  # hors bac à sable, les sondes réussissent
