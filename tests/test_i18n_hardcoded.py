"""Garde-fou i18n « cliquet » : plus de nouveau texte d'interface écrit en dur, et une dette qui ne fait que baisser.

Deux parties :

* le **détecteur** (``tools/i18n_audit.py``, AST) est testé sur de petits exemples : vrais positifs, et faux
  positifs évités (clés i18n, chemins, CSS, noms d'objets Qt, journal, formats, docstrings…) ;
* la **baseline** (``tests/i18n_hardcoded_baseline.json``) fixe la dette connue par fichier + texte normalisé,
  jamais par numéro de ligne. Un texte neuf fait échouer le test (avec la marche à suivre) ; un texte migré
  qui reste dans la baseline le fait échouer aussi, pour que la dette ne puisse que décroître.
"""

from __future__ import annotations

import json
import textwrap
from collections import Counter
from pathlib import Path

import pytest

from tools import i18n_audit as audit

ROOT = Path(__file__).resolve().parent.parent


def _scan(source: str, filename: str = "ui/example.py") -> list[audit.Finding]:
    return audit.scan_source(textwrap.dedent(source), filename)


def _texts(source: str) -> list[str]:
    return [finding.text for finding in _scan(source)]


# ---------------------------------------------------------------------------
# Détecteur : vrais positifs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected", "category"),
    [
        ('label = QLabel("Durée")', "Durée", "widget"),
        ('button = QPushButton("Annuler")', "Annuler", "widget"),  # sans accent : mot du lexique
        ('self.setToolTip("Réinitialiser le mouvement")', "Réinitialiser le mouvement", "tooltip"),
        ('self.setStatusTip("Ouvre le projet")', "Ouvre le projet", "tooltip"),
        ('menu.addAction("Dupliquer")', "Dupliquer", "menu"),
        ('submenu = menu.addMenu("Déplacer vers")', "Déplacer vers", "menu"),
        ('action = QAction("Copier", self)', "Copier", "menu"),
        ('self.statusBar().showMessage("Transition supprimée.", 3000)', "Transition supprimée.", "status"),
        ('box.setPlaceholderText("Rechercher un effet…")', "Rechercher un effet…", "widget"),
        ('self.setWindowTitle("Préférences")', "Préférences", "dialog"),
        ('tabs.addTab(panel, "Effets")', "Effets", "widget"),
        ('form.addRow("Hauteur", spin)', "Hauteur", "widget"),
        ('self._record_history("Dupliquer le clip")', "Dupliquer le clip", "history"),
        ('raise ValueError("Piste verrouillée")', "Piste verrouillée", "error"),
    ],
)
def test_a_french_literal_in_a_display_sink_is_found(source, expected, category):
    findings = _scan(source)
    assert [finding.text for finding in findings] == [expected]
    assert findings[0].category == category


def test_message_boxes_and_file_dialogs_report_every_text_argument():
    findings = _scan(
        """
        QMessageBox.warning(self, "Erreur", "Impossible d'ouvrir le fichier.")
        QInputDialog.getText(self, "Renommer", "Nouveau nom :")
        QFileDialog.getOpenFileName(self, "Importer des médias", "", "Vidéos (*.mp4 *.mov)")
        """
    )
    assert [finding.text for finding in findings] == [
        "Erreur", "Impossible d'ouvrir le fichier.", "Renommer", "Nouveau nom :",
        "Importer des médias", "Vidéos (*.mp4 *.mov)",
    ]
    assert {finding.category for finding in findings} == {"dialog"}


def test_f_strings_concatenations_and_format_are_normalised_to_a_stable_template():
    assert _texts('self.setText(f"Piste {name} verrouillée")') == ["Piste {} verrouillée"]
    assert _texts('self.setText("Durée : " + str(seconds) + " s")') == ["Durée :"]  # le morceau humain seul
    assert _texts('self.setText("Durée : {} s".format(seconds))') == ["Durée : {} s"]
    assert _texts('self.setText("Durée : %d s" % seconds)') == ["Durée : %d s"]
    assert _texts('self.setText(("Impossible de couper "\n "le clip"))') == ["Impossible de couper le clip"]
    # espaces et retours à la ligne repliés : reformater le code ne change pas la clé
    assert _texts('self.setText("Aucun clip\\n   sous la tête de lecture")') == ["Aucun clip sous la tête de lecture"]


def test_both_branches_of_a_conditional_and_list_items_are_found():
    assert _texts('button.setText("Activer" if on else "Désactiver")') == ["Activer", "Désactiver"]
    assert _texts('combo.addItems(["Faible", "Moyen", "Élevé"])') == ["Faible", "Moyen", "Élevé"]
    assert _texts('tree.setHeaderLabels(["Calque", "Durée"])') == ["Calque", "Durée"]


def test_a_french_literal_is_found_outside_a_sink_when_it_has_an_accent_or_a_french_word():
    # tables de libellés et valeurs de retour : le texte passe par une variable avant d'être affiché
    assert _texts('LABELS = {"fade": "Fondu enchaîné"}') == ["Fondu enchaîné"]
    assert _texts("def name(self):\n    return 'Zone de recherche'") == ["Zone de recherche"]


def test_any_language_is_found_when_passed_straight_to_a_display_sink():
    # « Position » s'écrit pareil en français et en anglais : dans un puits d'affichage, c'est un texte à traduire
    assert _texts('QLabel("Position")') == ["Position"]
    assert _texts('QPushButton("Apply to clip")') == ["Apply to clip"]
    assert _texts('self.setToolTip("Reset")') == ["Reset"]


# ---------------------------------------------------------------------------
# Détecteur : faux positifs évités
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        'label = QLabel(translate("tracking.title"))',
        'self.setText(i18n.translate("proxy.state.ready"))',
        'self.setToolTip(_tr("tracking.add.tip", count=2))',
        'icon = QIcon("assets/icônes/lecture.svg")',  # chemin
        'path = Path("projet.kut")',  # nom de fichier
        'url = "https://exemple.org/aide"',
        'color = "#ff00cc"',
        'self.setStyleSheet("QLabel { color: red; font-weight: 600; }")',
        'self.setStyleSheet("/* Séparateur : fin */ QFrame { border: 0; }")',
        'self.setObjectName("panneauÉditeur")',  # nom d'objet Qt, même accentué
        'self.setProperty("variant", "primary")',
        'self.setProperty("état", "actif")',
        'label.setText("{} px".format(width))',  # unité seule
        'label.setText("{:.1f} s".format(seconds))',  # format
        'label.setText("%d fps" % rate)',
        'label.setText("dB")',
        'label.setText("×")',
        'label.setText("0")',
        'label.setText(f"{value:.2f}")',
        'button = QPushButton("OK")',
        'self.setWindowTitle("Kut-Studio")',  # nom de l'application
        'combo.addItem("Français")',  # nom de langue dans sa propre langue
        'combo.addItem(translate("audio.mono"), "mono")',  # la donnée (minuscule, sans espace) n'est pas un texte
        'combo.addItem("GPU")',  # sigle
        'self.addAction(IconName.PLAY)',
        'LOGGER.info("Opération refusée : %s", reason)',  # journal technique
        'logging.getLogger(__name__).warning("Échec de lecture")',
        'x = "fr"',
        'mode = "auto"',
        'key = "track-volume:{}"',
        'shortcut = "Ctrl+Shift+S"',
    ],
)
def test_technical_and_already_translated_strings_are_not_reported(source):
    assert _scan(source) == []


def test_docstrings_and_floating_strings_are_not_reported():
    assert _scan(
        '''
        """Module : réglages de l'aperçu, en français."""

        class Panneau:
            """Panneau des réglages : ce texte n'est pas affiché."""

            def methode(self):
                "Décrit la méthode."
                return 1
        '''
    ) == []


def test_english_text_outside_a_display_sink_is_a_documented_blind_spot():
    # Seul le français est repéré hors puits d'affichage (voir la docstring de tools/i18n_audit.py).
    assert _scan('TITLES = {"fade": "Cross dissolve"}') == []


def test_an_ignore_marker_on_the_line_or_on_the_function_header_silences_the_report():
    assert _scan('label = QLabel("Durée")  # i18n-ignore: nom propre d\'un format') == []
    assert _scan(
        """
        def steps(self):  # i18n-ignore: noms d'étapes du journal d'arrêt
            return [("pompe d'aperçu", self.stop), ("sessions d'édition", self.finalize)]
        """
    ) == []
    # le marqueur d'une fonction ne couvre pas les autres
    assert _texts(
        """
        def a(self):  # i18n-ignore: raison
            return "pompe d'aperçu"

        def b(self):
            return "sessions d'édition"
        """
    ) == ["sessions d'édition"]


def test_repr_and_str_are_for_developers_and_are_not_reported():
    assert _scan('class A:\n    def __repr__(self):\n        return f"A({self.n} jetons, thème actif)"') == []


def test_technical_reason_names_why_a_string_is_skipped():
    assert audit.technical_reason("proxy.state.ready") == "clé i18n"
    assert audit.technical_reason("assets/icônes/lecture.svg") == "chemin ou nom de fichier"
    assert audit.technical_reason("https://exemple.org") == "adresse"
    assert audit.technical_reason("QPushButton { color: red; }") == "CSS"
    assert audit.technical_reason("Français") == "nom de langue"
    assert audit.technical_reason("Ouvrir le projet") is None


def test_findings_carry_a_stable_key_independent_of_the_line_number():
    first = _scan('QLabel("Durée")')[0]
    shifted = _scan('\n\n\n# commentaire\nQLabel("Durée")')[0]
    assert first.line != shifted.line
    assert first.key == shifted.key == ("ui/example.py", "Durée")


# ---------------------------------------------------------------------------
# Cliquet : baseline sur un petit dépôt de test
# ---------------------------------------------------------------------------


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source), encoding="utf-8")


@pytest.fixture
def mini_repo(tmp_path):
    _write(tmp_path, "ui/panel.py", 'from x import QLabel\n\nlabel = QLabel("Durée")\nother = QLabel("Durée")\n')
    _write(tmp_path, "ui/menu.py", 'menu.addAction("Dupliquer")\n')
    baseline = tmp_path / "tests" / "i18n_hardcoded_baseline.json"
    assert audit.main(["--root", str(tmp_path), "--update-baseline", "--accept-new"]) == 0
    assert baseline.exists()
    return tmp_path


def _compare(root: Path):
    findings = audit.scan_tree(root)
    return audit.compare(findings, audit.load_baseline(root / "tests" / "i18n_hardcoded_baseline.json"))


def test_a_fresh_baseline_is_clean_and_counts_occurrences(mini_repo):
    assert _compare(mini_repo).clean
    baseline = audit.load_baseline(mini_repo / "tests" / "i18n_hardcoded_baseline.json")
    assert baseline[("ui/panel.py", "Durée")] == 2
    assert baseline[("ui/menu.py", "Dupliquer")] == 1


def test_shifting_lines_does_not_fail(mini_repo):
    source = (mini_repo / "ui" / "panel.py").read_text(encoding="utf-8")
    (mini_repo / "ui" / "panel.py").write_text("# un commentaire\n\n\n" + source, encoding="utf-8")
    assert _compare(mini_repo).clean


def test_a_new_hardcoded_string_fails_with_instructions(mini_repo):
    _write(mini_repo, "ui/panel.py", 'label = QLabel("Durée")\nother = QLabel("Durée")\nnew = QLabel("Nouveau texte")\n')
    comparison = _compare(mini_repo)
    assert list(comparison.new) == [("ui/panel.py", "Nouveau texte")]
    assert not comparison.stale
    message = audit.format_new(comparison)
    assert "ui/panel.py" in message and "Nouveau texte" in message
    assert 'translate("' in message and "fr, en ET es" in message  # dit quoi faire


def test_a_third_occurrence_of_a_known_string_is_new(mini_repo):
    _write(mini_repo, "ui/panel.py", 'a = QLabel("Durée")\nb = QLabel("Durée")\nc = QLabel("Durée")\n')
    comparison = _compare(mini_repo)
    assert comparison.new_excess == {("ui/panel.py", "Durée"): 1}


def test_the_same_text_in_another_file_is_new(mini_repo):
    _write(mini_repo, "ui/autre.py", 'menu.addAction("Dupliquer")\n')
    assert list(_compare(mini_repo).new) == [("ui/autre.py", "Dupliquer")]


def test_a_migrated_string_must_leave_the_baseline(mini_repo):
    _write(mini_repo, "ui/menu.py", 'menu.addAction(translate("menu.item.duplicate"))\n')
    comparison = _compare(mini_repo)
    assert not comparison.new
    assert comparison.stale == {("ui/menu.py", "Dupliquer"): (1, 0)}
    assert not comparison.clean
    assert "--update-baseline" in audit.format_stale(comparison)


def test_a_partly_migrated_string_is_stale_until_the_count_is_lowered(mini_repo):
    _write(mini_repo, "ui/panel.py", 'a = QLabel(translate("x.y"))\nb = QLabel("Durée")\n')
    assert _compare(mini_repo).stale == {("ui/panel.py", "Durée"): (2, 1)}


def test_a_deleted_file_makes_its_entries_stale(mini_repo):
    (mini_repo / "ui" / "menu.py").unlink()
    assert ("ui/menu.py", "Dupliquer") in _compare(mini_repo).stale


def test_update_baseline_only_removes_debt(mini_repo, capsys):
    baseline_path = mini_repo / "tests" / "i18n_hardcoded_baseline.json"
    _write(mini_repo, "ui/menu.py", 'menu.addAction(translate("menu.item.duplicate"))\n')
    assert audit.main(["--root", str(mini_repo), "--check"]) == 1
    assert audit.main(["--root", str(mini_repo), "--update-baseline"]) == 0
    assert audit.main(["--root", str(mini_repo), "--check"]) == 0
    assert ("ui/menu.py", "Dupliquer") not in audit.load_baseline(baseline_path)
    # un texte neuf n'entre jamais en silence dans la baseline
    before = baseline_path.read_text(encoding="utf-8")
    _write(mini_repo, "ui/menu.py", 'menu.addAction("Supprimer")\n')
    assert audit.main(["--root", str(mini_repo), "--update-baseline"]) == 1
    assert baseline_path.read_text(encoding="utf-8") == before
    assert "Supprimer" in capsys.readouterr().err


def test_list_prints_the_debt_by_zone(mini_repo, capsys):
    _write(mini_repo, "ui/tracking_panel.py", 'x = QLabel("Suivi du point")\n')
    assert audit.main(["--root", str(mini_repo), "--list"]) == 0
    output = capsys.readouterr().out
    assert "== tracking : 1 chaîne(s), 1 occurrence(s) ==" in output
    assert "ui/tracking_panel.py:1" in output and "« Suivi du point »" in output
    assert audit.main(["--root", str(mini_repo), "--list", "--zone", "autres"]) == 0
    other_zone = capsys.readouterr().out
    assert "== autres : 2 chaîne(s), 3 occurrence(s) ==" in other_zone and "== tracking" not in other_zone
    assert audit.main(["--root", str(mini_repo), "--summary"]) == 0
    assert "TOTAL" in capsys.readouterr().out


def test_i18n_tables_are_never_scanned(tmp_path):
    _write(tmp_path, "ui/i18n_extra.py", 'T = {"a.b": {"fr": "Fichier ouvert", "en": "Open file"}}\n')
    _write(tmp_path, "ui/i18n.py", 'X = "Ouvrir le projet"\n')
    assert audit.scan_tree(tmp_path) == []


# ---------------------------------------------------------------------------
# Dette réelle du dépôt
# ---------------------------------------------------------------------------


def test_no_new_hardcoded_ui_strings():
    """Un nouvel appel de widget / menu / dialogue avec un littéral français fait échouer ce test."""
    comparison = audit.compare(audit.scan_tree(ROOT), audit.load_baseline())
    assert not comparison.new, "\n" + audit.format_new(comparison)


def test_the_baseline_only_lists_strings_that_are_still_hardcoded():
    """Cliquet : un texte migré doit sortir de la baseline (``python -m tools.i18n_audit --update-baseline``)."""
    comparison = audit.compare(audit.scan_tree(ROOT), audit.load_baseline())
    assert not comparison.stale, "\n" + audit.format_stale(comparison)


def test_the_baseline_file_is_well_formed_sorted_and_assigned_to_a_zone():
    data = json.loads(audit.BASELINE_PATH.read_text(encoding="utf-8"))
    assert data["version"] == audit.BASELINE_VERSION
    entries = data["entries"]
    keys = [(entry["file"], entry["text"]) for entry in entries]
    assert keys == sorted(keys), "baseline non triée : régénérez-la avec --update-baseline"
    assert len(keys) == len(set(keys)), "entrée en double"
    for entry in entries:
        assert set(entry) == {"file", "text", "count"}
        assert entry["count"] >= 1
        assert (ROOT / entry["file"]).is_file(), f"fichier disparu : {entry['file']}"
        assert entry["text"] == audit.normalize_text(entry["text"])
        assert audit.zone_of(entry["file"]) != audit.OTHER_ZONE, (
            f"{entry['file']} n'appartient à aucune zone : complétez ZONES dans tools/i18n_audit.py"
        )


def test_the_audit_command_line_agrees_with_the_test():
    assert audit.main(["--check"]) == 0


# ---------------------------------------------------------------------------
# Erreurs du cœur (inventaire, hors baseline)
# ---------------------------------------------------------------------------


def test_core_error_messages_are_inventoried_separately(tmp_path, capsys):
    _write(
        tmp_path,
        "core/ops.py",
        '''
        class SequenceError(ValueError):
            """Erreur : la docstring n'est pas un message."""

        def a(x):
            raise SequenceError("Impossible de supprimer la séquence")

        def b(x):
            raise ValueError(f"Piste {x} verrouillée")

        def c(x):
            raise KeyError("missing")  # technique : pas du français

        def d(x):
            raise ValueError("Unknown clip")
        ''',
    )
    messages = audit.scan_core_messages(tmp_path)
    assert [(message.exception, message.text) for message in messages] == [
        ("SequenceError", "Impossible de supprimer la séquence"),
        ("ValueError", "Piste {} verrouillée"),
    ]
    assert audit.main(["--root", str(tmp_path), "--core"]) == 0
    output = capsys.readouterr().out
    assert "2" in output and "SequenceError" in output and "core/ops.py" in output


def test_the_baseline_does_not_include_core_messages():
    data = json.loads(audit.BASELINE_PATH.read_text(encoding="utf-8"))
    assert not [entry for entry in data["entries"] if entry["file"].startswith("core/")]


def test_counter_equality_is_by_occurrence_not_by_line():
    first = Counter(finding.key for finding in _scan('QLabel("Durée")\nQLabel("Durée")'))
    second = Counter(finding.key for finding in _scan('\n\nQLabel("Durée")\n\nQLabel("Durée")'))
    assert first == second == Counter({("ui/example.py", "Durée"): 2})
