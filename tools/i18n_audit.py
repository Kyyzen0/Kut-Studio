"""Audit des textes d'interface écrits en dur : garde-fou « cliquet » de l'i18n.

Usage ::

    python -m tools.i18n_audit                       # = --check : échoue s'il y a du nouveau ou du périmé
    python -m tools.i18n_audit --list                # la dette connue, par zone, lisible
    python -m tools.i18n_audit --list --zone tracking
    python -m tools.i18n_audit --summary             # chiffres par zone et par catégorie
    python -m tools.i18n_audit --update-baseline     # retire de la baseline ce qui a été migré
    python -m tools.i18n_audit --update-baseline --accept-new   # (re)génère : autorise aussi du nouveau
    python -m tools.i18n_audit --core                # inventaire des messages d'erreur du cœur

Principe
--------

Le détecteur lit l'AST des modules de ``ui/`` (jamais une regex sur le texte source) et relève chaque littéral de
chaîne — f-strings et morceaux de concaténation compris — de deux sortes :

* partout dans ``ui/`` : un **texte humain français**, c'est-à-dire des lettres accentuées ou un mot du lexique
  français figé (:data:`FRENCH_WORDS`) ;
* passé **directement à un puits d'affichage** (constructeurs de widgets, ``setText`` / ``setToolTip`` /
  ``addAction`` / ``addRow``…, ``QMessageBox`` / ``QInputDialog`` / ``QFileDialog``, ``showMessage``, aides
  d'historique du dépôt) : tout texte, **quelle que soit sa langue** (« Position » s'écrit pareil en français et en
  anglais, mais c'est du texte à traduire).

Sont écartés : docstrings, appels de journal, tables de traduction (``ui/i18n*.py``), clés i18n, chemins et noms de
fichiers, formats, CSS, noms d'objets Qt et propriétés, sigles et unités, noms de langues dans leur propre langue,
``__repr__`` / ``__str__``.

Chaque littéral est rapporté avec son **contexte** (la catégorie : dialogue, menu, infobulle, barre d'état, libellé
d'historique, table de libellés…) ; la catégorie ne sert qu'à l'affichage, jamais à la clé.

La **baseline** (``tests/i18n_hardcoded_baseline.json``) liste la dette connue : une entrée est identifiée par
**fichier + texte normalisé** (espaces repliés, champs d'une f-string remplacés par ``{}``) et un nombre
d'occurrences, **jamais par un numéro de ligne** : décaler du code ne fait pas échouer le test. Le test
(``tests/test_i18n_hardcoded.py``) impose un cliquet :

* une occurrence **de plus** que la baseline : texte neuf, non traduit -> à passer par ``translate("clé")`` ;
* une occurrence **de moins** (texte migré) : l'entrée doit être retirée de la baseline, pour que la dette ne
  puisse que décroître.

Limites assumées : un texte construit à l'exécution (``"".join`` de variables, lu dans un fichier…) n'est pas
vu ; **hors puits d'affichage** (tables de libellés, valeurs de retour), un mot français identique à son équivalent
anglais sans accent (« Rotation », « Standard »…) et un texte anglais ne sont pas détectables. Une exception
légitime (donnée technique qui ressemble à du français, message de développeur) se marque par
``# i18n-ignore: raison`` sur la ligne du littéral, ou sur la ligne ``def`` d'une fonction pour l'exempter en entier.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = ROOT / "tests" / "i18n_hardcoded_baseline.json"
SCANNED_PACKAGES = ("ui",)
CORE_PACKAGE = "core"
BASELINE_VERSION = 1

# Les tables de traduction elles-mêmes (le français y est à sa place).
EXCLUDED_FILES = ("ui/i18n.py", "ui/i18n_*.py")

IGNORE_MARKER = "i18n-ignore"

# ---------------------------------------------------------------------------
# Texte humain français
# ---------------------------------------------------------------------------

ACCENTED_LETTERS = frozenset("àâäçéèêëîïôöùûüÿœæÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸŒÆ")

# Noms de langue écrits dans leur propre langue (sélecteurs de langue) : jamais traduits.
NATIVE_LANGUAGE_NAMES = frozenset({"Français", "Español", "English", "Deutsch", "Italiano", "Português"})

# Mots français sans équivalent anglais identique. Lexique **figé** (il ne dépend pas des traductions : ajouter une
# clé i18n ne doit jamais faire apparaître de la dette). Les mots accentués sont déjà couverts par ``ACCENTED_LETTERS``.
FRENCH_WORDS = frozenset(
    """
    absent absente absents absentes invalide accentue actif actifs activer actuelle adaptatif adouci adoucit
    affichage afficher agrandit aide aigu aigus
    aimant aimantation ajoute ajouter ajoutez ajuster alignement analyser analysez ancien ancrage anglais
    annulation annuler annulera aplat apparence applique appliquent appliquer appliquez approche appuyez armer
    armez assombrit attaque atteinte attendez attente attributs aucun aucune augmentez automatique automatiques
    autre aux avancer avant avec avoir baisse balaie balayage balayages bande bas bascule basses blanc bleue
    bloc booste bords brouillon bruit cadence cadre cadrer calcul calque calques capturer cercle ces cet cette
    chaleureux changer chaud choisir choisissez cible clair clavier clic cliquez cochez coller combinaison
    commande commence compatibles complets compresseur confiance contenue continuer contraste convertir
    convertit copier corps correspond corriger corrigez couleur couleurs couper courbes cours courte dans
    demande depuis des descendre deux deviendront dilatation disponible disque dissolution dissolutions dossier
    dossiers douce douceur doux droit droite dupliquer dupliquez effacer effet effets elle enchaine encodage
    encodeur encore enregistrement enregistrer ensuite entendues entrant entre entree entrez erreur espaces
    espagnol essayez estompe existant existe exporter exposition faible favori favoris fermer fermeture fichier
    fichiers figer fixe flou fluide fluides fonction fond fondu fondus forme formes froid garder gauche gaussien
    gestionnaire glisse glissement glissements glisser glissez globale graphique gras graves grille gris grouper
    haut haute hautes hauteur histogramme horizontale hors illisibles ils imbrication imbriquer importer
    importez impossible impossibles incertaines inclinaison incrustation indisponible indisponibles inspecteur
    installez interligne interrompu introuvable invalides inverser italique lame lancer langue largeur lecture
    les liaison liaisons libres lier ligne limiteur lisible lissage logiciel losange maintien manquante
    manquants maquillage marges marqueur marqueurs masque masquer masques maximale mes mettez milieu minimale
    miroir mixe mixeur modification modifications modifier molette moniteur montage monter mouvement moyen muet
    muette nettoyer niveau niveaux noir noirs nom normalisation nouveau nouvelle obscurcir obturation ombre
    ombres onglet optionnelle originaux outil outils ouverture ouvre ouvrez ouvrir panneaux panoramique par
    parente particules pas patientez perdu petit peut peuvent piste pistes pixellisation pixellise placer
    plafond plage plancher plein police pour pousse principale prise prochain prochaine produit profil
    progressivement projet projets puis puissant purger quand qui quitter raccourci raccourcis racine raison
    rapide recadrage recherche rechercher recompose reculer referme regain relancer relancez relier reliez
    remappage remonte rendu rendus renforcement renommer repli reportages ressortir restaurer retards retirer
    retour retrouver revenir rouge saisissez seconde selon sera seront seuil seulement seules sombre sont
    sortant sortie sous soustraire stabiliser statut suit suivant suivante suivi suivies suivre supprimer
    supprimez sur survolez taille tangentes teinte temporairement temporel texte tient tiers titre titres
    toujours tous tout toutes trajectoires translucide travail trop typique typographie une utilisation utilise
    utilisent utiliser utilisez valeur valide verrouiller vers verte verticale vide vides virage visionneuse
    vitesse vitesses volumineux vos votre voulez vous vue vues
    """.split()
)

# Petits mots grammaticaux : trop courts pour compter seuls (« de-esser », « un-link »), deux suffisent.
FRENCH_FUNCTION_WORDS = frozenset({"de", "du", "la", "le", "un", "et", "ou", "au", "en", "se", "si", "ne", "ce"})

_TOKEN = re.compile(r"[^\W\d_]+", re.UNICODE)
_PLACEHOLDER = re.compile(r"\{\}|\{[^{}]*\}|%[-+ 0#]*\d*(?:\.\d+)?[sdifrxXeEgGc%]")
_I18N_KEY = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")
_PATHLIKE = re.compile(r"^[\w\-./\\:~*%{}]+$")
_URL = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{3,8}$")
_SHORTCUT = re.compile(r"^(?:(?:Ctrl|Cmd|Alt|Shift|Meta|Maj|Option)\+)+\S+$")
_BRAND = re.compile(r"^kut[\-\u2010\u2011 ]?studio$", re.IGNORECASE)
# Donnée plutôt que texte : minuscules, chiffres et ponctuation de clé, sans espace (``"track-volume:{}"``).
_IDENTIFIER = re.compile(r"^[a-z0-9_\-.:{}/#@]+$")

# Sigles, formats et unités qui s'écrivent pareil dans toutes les langues : jamais à traduire.
TECHNICAL_TOKENS = frozenset(
    """
    gpu cpu rgb rgba hsl hsv lut cube srt mov mp mkv avi webm png jpg jpeg bmp tif tiff webp wav flac aac ogg m
    ffmpeg hz khz db fps px ms mo go ko mb gb kb kbps mbps lufs rms prores hevc av vp pcm nv rhi qrhi metal vulkan
    opengl direct kut studio ui id hdr sdr yuv ycbcr srgb tc
    """.split()
)


def normalize_text(text: str) -> str:
    """Forme stable d'un texte : espaces repliés, bords retirés."""
    return re.sub(r"\s+", " ", text).strip()


def technical_reason(text: str) -> str | None:
    """Pourquoi ``text`` est technique (pas du texte d'interface), ou ``None``.

    Appelée **avant** la détection du français : un chemin avec une lettre accentuée reste un chemin.
    """
    if not text.strip():
        return "vide"
    if text in NATIVE_LANGUAGE_NAMES:
        return "nom de langue"
    if _I18N_KEY.match(text):
        return "clé i18n"
    if _URL.match(text):
        return "adresse"
    if _HEX_COLOR.match(text):
        return "couleur"
    if _SHORTCUT.match(text):
        return "raccourci clavier"
    if _BRAND.match(text):
        return "nom de l'application"
    if text.lstrip().startswith("/*") or ("{" in text and "}" in text and ";" in text and ":" in text):
        return "CSS"
    if " " not in text.strip() and _PATHLIKE.match(text) and (
        "/" in text or "\\" in text or re.search(r"\.\w{1,6}$", text)
    ):
        return "chemin ou nom de fichier"
    return None


def has_french_text(text: str) -> bool:
    """``True`` si ``text`` contient du français humain (accent, ou mot du lexique)."""
    stripped = _PLACEHOLDER.sub(" ", text)
    if any(char in ACCENTED_LETTERS for char in stripped):
        return True
    tokens = [token.lower() for token in _TOKEN.findall(stripped)]
    if any(token in FRENCH_WORDS for token in tokens):
        return True
    return len({token for token in tokens if token in FRENCH_FUNCTION_WORDS}) >= 2


def is_hardcoded_french(text: str) -> bool:
    """Un littéral qui doit passer par l'i18n : du français humain, pas une donnée technique."""
    return technical_reason(text) is None and has_french_text(text)


def has_human_words(text: str) -> bool:
    """``True`` si ``text`` contient un vrai mot (≥ 3 lettres) qui n'est ni un sigle ni une unité.

    Sert aux littéraux passés **directement** à un puits d'affichage (``QLabel("Position")``) : tout texte
    qui s'y trouve est un texte d'interface, quelle que soit sa langue, et doit passer par l'i18n.
    """
    stripped = _PLACEHOLDER.sub(" ", text)
    if _IDENTIFIER.match(text.strip()):
        return False
    return any(len(token) >= 3 and token.lower() not in TECHNICAL_TOKENS for token in _TOKEN.findall(stripped))


def is_hardcoded_display_text(text: str) -> bool:
    """Un littéral passé à un puits d'affichage : du texte humain, dans n'importe quelle langue."""
    return technical_reason(text) is None and (has_french_text(text) or has_human_words(text))


# ---------------------------------------------------------------------------
# Contexte d'un littéral
# ---------------------------------------------------------------------------

# Appels dont les chaînes sont des données (noms d'objets Qt, propriétés, feuilles de style, comparaisons).
NON_DISPLAY_CALLS = frozenset(
    {"setObjectName", "setProperty", "property", "setStyleSheet", "startswith", "endswith", "connect", "disconnect"}
)
_LOG_METHODS = frozenset({"debug", "info", "warning", "error", "exception", "critical", "log", "warn"})
# Méthodes de ``str`` : un littéral qui en est le récepteur reste « dans » l'appel qui l'englobe.
_STR_METHODS = frozenset({"format", "join", "strip", "lstrip", "rstrip", "replace", "lower", "upper", "title",
                          "capitalize", "casefold", "encode", "ljust", "rjust", "center", "format_map"})

CATEGORIES = {
    "dialog": "dialogue",
    "menu": "menu",
    "widget": "libellé de widget",
    "tooltip": "infobulle / nom accessible",
    "status": "barre d'état",
    "history": "libellé d'historique (annuler/rétablir)",
    "error": "exception levée",
    "table": "table de libellés / valeur",
    "other": "autre appel",
}

_CALL_CATEGORY: dict[str, str] = {}
for _category, _names in {
    "dialog": (
        "QMessageBox QInputDialog QFileDialog QProgressDialog information warning critical question about aboutQt "
        "getText getInt getDouble getItem getMultiLineText getOpenFileName getOpenFileNames getSaveFileName "
        "getExistingDirectory setInformativeText setDetailedText setLabelText setOkButtonText setCancelButtonText "
        "setButtonText addButton setWindowTitle"
    ),
    "menu": "QMenu QAction addAction addMenu addSection insertMenu insertAction setTitle",
    "widget": (
        "QLabel QPushButton QToolButton QCheckBox QRadioButton QGroupBox QDockWidget QLineEdit QTabWidget QTreeWidgetItem "
        "QListWidgetItem QTableWidgetItem QStandardItem QCommandLinkButton QTextEdit QPlainTextEdit setText addItem "
        "addItems insertItem addTab insertTab setTabText setItemText setHeaderLabels setHorizontalHeaderLabels "
        "setVerticalHeaderLabels setHeaderLabel setPlaceholderText setSuffix setPrefix setSpecialValueText setFormat "
        "setPlainText setHtml addRow"
    ),
    "tooltip": "setToolTip setStatusTip setWhatsThis setAccessibleName setAccessibleDescription setTabToolTip showText",
    "status": "showMessage setStatusMessage",
}.items():
    for _name in _names.split():
        _CALL_CATEGORY.setdefault(_name, _category)


@dataclass(frozen=True)
class Finding:
    """Un littéral français relevé dans le code."""

    file: str
    line: int
    text: str
    category: str
    call: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.file, self.text)


def _callee_name(func: ast.expr) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _is_log_call(call: ast.Call) -> bool:
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr not in _LOG_METHODS:
        return False
    try:
        receiver = ast.unparse(func.value).lower()
    except Exception:  # pragma: no cover - ast.unparse ne devrait pas échouer sur un AST valide
        return False
    return "log" in receiver


def _context(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> tuple[str, str] | None:
    """``(catégorie, appelant)`` d'un littéral, ou ``None`` s'il faut l'ignorer (journal)."""
    child: ast.AST = node
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.Call):
            if child is current.func or (
                isinstance(current.func, ast.Attribute) and child is current.func.value
            ):
                # Littéral récepteur d'une méthode de ``str`` (``"…".format(x)``) : on remonte à l'appelant.
                if _callee_name(current.func) in _STR_METHODS:
                    child, current = current, parents.get(current)
                    continue
            if _is_log_call(current):
                return None
            name = _callee_name(current.func)
            if name in NON_DISPLAY_CALLS:
                return None
            category = _CALL_CATEGORY.get(name)
            if category is None:
                category = _project_category(name)
            return category, name
        if isinstance(current, ast.Raise):
            return "error", "raise"
        if isinstance(current, ast.stmt):
            return "table", type(current).__name__.lower()
        child, current = current, parents.get(current)
    return "table", "module"


# Aides d'affichage propres au dépôt : leur argument texte est du texte d'interface.
PROJECT_HELPER_CATEGORIES: dict[str, str] = {
    "IconButton": "tooltip",
    "_make_action_button": "widget",
    "_make_wide_button": "widget",
    "_row_label": "widget",
    "_btn": "widget",
    "_Section": "widget",
    "_add_category_button": "widget",
    "_render_section": "widget",
    "_label_with_count": "widget",
    "RailSection": "widget",
    "_show_warning": "dialog",
    "prompt_for_folder_name": "dialog",
    # Libellés de l'historique (menu Annuler / Rétablir).
    "_record_history": "history",
    "_record_audio_change": "history",
    "_after_animation_edit": "history",
    "_commit_layer_edit": "history",
    "_layer_operation": "history",
    "_schedule_transform_history": "history",
    "_commit_color_grade": "history",
    "finish_gesture": "history",
    "_edited": "history",
}

# Catégories dont le littéral est du texte affiché : il est relevé quelle que soit sa langue.
DISPLAY_CATEGORIES = frozenset({"dialog", "menu", "widget", "tooltip", "status", "history"})
# Fonctions dont les chaînes sont destinées aux développeurs, pas à l'utilisateur.
DEVELOPER_ONLY_FUNCTIONS = frozenset({"__repr__", "__str__"})


def _project_category(name: str) -> str:
    if name in PROJECT_HELPER_CATEGORIES:
        return PROJECT_HELPER_CATEGORIES[name]
    if name.endswith("Error") or name.endswith("Unavailable") or name.endswith("Exception"):
        return "error"
    return "other"


def _joined_template(node: ast.JoinedStr) -> str:
    """Gabarit d'une f-string : les parties constantes, ``{}`` à la place des expressions."""
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        else:
            parts.append("{}")
    return "".join(parts)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Identifiants des littéraux qui sont des instructions seules (docstrings, textes flottants)."""
    return {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


def _enclosing_functions(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    functions: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(current)
        current = parents.get(current)
    return functions


def _is_ignored(node: ast.expr, parents: dict[ast.AST, ast.AST], lines: list[str]) -> bool:
    """Marqueur ``# i18n-ignore`` sur le littéral, ou sur la ligne ``def`` d'une fonction qui l'englobe."""
    end = node.end_lineno or node.lineno
    if any(IGNORE_MARKER in lines[index] for index in range(node.lineno - 1, min(end, len(lines)))):
        return True
    for function in _enclosing_functions(node, parents):
        if function.name in DEVELOPER_ONLY_FUNCTIONS:
            return True
        header_end = function.body[0].lineno - 1
        if any(IGNORE_MARKER in lines[index] for index in range(function.lineno - 1, min(header_end, len(lines)))):
            return True
    return False


def scan_source(source: str, filename: str) -> list[Finding]:
    """Littéraux d'interface écrits en dur dans un module (``filename`` : chemin relatif en ``/``).

    Un littéral est relevé s'il contient du français humain (accent ou mot du lexique), où qu'il soit ; ou s'il est
    passé directement à un puits d'affichage (:data:`DISPLAY_CATEGORIES`) et contient un vrai mot, dans n'importe
    quelle langue.
    """
    tree = ast.parse(source, filename=filename)
    lines = source.splitlines()
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    floating = _docstring_nodes(tree)
    found: list[tuple[int, int, Finding]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            raw = _joined_template(node)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            raw = node.value
        else:
            continue
        if id(node) in floating or isinstance(parents.get(node), ast.JoinedStr):
            continue  # docstring, ou morceau d'une f-string (vue au niveau de la f-string)
        text = normalize_text(raw)
        if not text:
            continue
        context = _context(node, parents)
        if context is None:
            continue
        category, call = context
        if category in DISPLAY_CATEGORIES:
            relevant = is_hardcoded_display_text(text)
        else:
            relevant = is_hardcoded_french(text)
        if not relevant or _is_ignored(node, parents, lines):
            continue
        found.append((node.lineno, node.col_offset, Finding(filename, node.lineno, text, category, call)))
    found.sort(key=lambda item: (item[0], item[1]))
    return [finding for _, _, finding in found]


def _is_excluded(relative: str) -> bool:
    return any(fnmatch.fnmatch(relative, pattern) for pattern in EXCLUDED_FILES)


def python_files(root: Path, packages: tuple[str, ...] = SCANNED_PACKAGES) -> list[Path]:
    files: list[Path] = []
    for package in packages:
        base = root / package
        if base.is_dir():
            files.extend(sorted(base.rglob("*.py")))
    return files


def scan_tree(root: Path = ROOT, packages: tuple[str, ...] = SCANNED_PACKAGES) -> list[Finding]:
    """Tous les littéraux français de ``packages`` sous ``root``."""
    findings: list[Finding] = []
    for path in python_files(root, packages):
        relative = path.relative_to(root).as_posix()
        if _is_excluded(relative):
            continue
        findings.extend(scan_source(path.read_text(encoding="utf-8"), relative))
    return findings


# ---------------------------------------------------------------------------
# Zones
# ---------------------------------------------------------------------------

# Premier motif qui correspond gagne. L'ordre suit les priorités de migration.
ZONES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tracking", ("ui/tracking_*.py", "ui/main_window_mixins/tracking.py")),
    ("gpu-materiel", (
        "ui/gpu_preview.py", "ui/performance_settings.py", "ui/main_window_mixins/hardware_preview.py",
        "ui/main_window_mixins/performance.py", "ui/main_window_mixins/encoding.py",
        "ui/main_window_mixins/faithful_preview.py", "ui/debug_overlay.py",
    )),
    ("sequences", (
        "ui/main_window_mixins/sequences.py", "ui/project_panel_widgets/sequence_library.py",
        "ui/timeline_widgets/sequence_bar.py", "ui/timeline_widgets/nested_clip.py",
    )),
    ("motion-graphics", (
        "ui/main_window_mixins/motion_graphics.py", "ui/graphics_*.py", "ui/layers_panel.py", "ui/viewer_overlay.py",
        "ui/graph_editor.py", "ui/main_window_mixins/animation.py", "ui/properties_widgets/advanced_transform.py",
        "ui/main_window_mixins/subtitles_graphics.py", "ui/text_style_editor.py", "ui/compositing_editor.py",
        "ui/main_window_mixins/presets.py", "ui/project_panel_widgets/text_presets_view.py",
        "ui/project_panel_widgets/transition_library.py",
    )),
    ("preferences", (
        "ui/preferences_dialog.py", "ui/shortcuts_editor.py", "ui/shortcut_manager.py",
        "ui/main_window_mixins/preferences.py", "ui/workspace/*.py", "ui/main_window_mixins/workspace_actions.py",
    )),
    ("fenetre-principale", (
        "ui/main_window.py", "ui/main_window_mixins/*.py", "ui/side_rail.py",
    )),
    ("timeline", ("ui/timeline_*.py", "ui/timeline_*/*.py")),
    ("inspecteur", ("ui/properties_*.py", "ui/properties_*/*.py", "ui/mixer_panel.py", "ui/scopes_panel.py")),
    ("bibliotheque-projet", ("ui/project_panel.py", "ui/project_panel_widgets/*.py", "ui/library_organization_widgets.py",
                             "ui/audio_effects_library.py")),
    ("apercu-export", ("ui/preview_panel.py", "ui/export_panel.py", "ui/render_queue_panel.py")),
)
OTHER_ZONE = "autres"


def zone_of(file: str) -> str:
    for zone, patterns in ZONES:
        if any(fnmatch.fnmatch(file, pattern) for pattern in patterns):
            return zone
    return OTHER_ZONE


def zone_names() -> list[str]:
    return [zone for zone, _ in ZONES] + [OTHER_ZONE]


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------


def counts_of(findings: list[Finding]) -> Counter[tuple[str, str]]:
    return Counter(finding.key for finding in findings)


def load_baseline(path: Path = BASELINE_PATH) -> Counter[tuple[str, str]]:
    """Dette autorisée : ``{(fichier, texte): occurrences}``. Un fichier absent vaut une baseline vide."""
    if not path.exists():
        return Counter()
    data = json.loads(path.read_text(encoding="utf-8"))
    entries: Counter[tuple[str, str]] = Counter()
    for entry in data.get("entries", []):
        entries[(entry["file"], entry["text"])] += int(entry.get("count", 1))
    return entries


_BASELINE_HEADER = (
    "Dette i18n connue : textes d'interface français écrits en dur, par fichier et texte normalisé "
    "(jamais par numéro de ligne). Cliquet : ne peut que décroître. Voir tools/i18n_audit.py."
)


def write_baseline(counts: Counter[tuple[str, str]], path: Path = BASELINE_PATH) -> None:
    """Écrit la baseline : une entrée par ligne, triée, pour des diffs lisibles."""
    rows = [
        json.dumps({"file": file, "text": text, "count": count}, ensure_ascii=False)
        for (file, text), count in sorted(counts.items())
        if count > 0
    ]
    body = ",\n".join(f"  {row}" for row in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{\n "version": %d,\n "comment": %s,\n "entries": [\n%s\n ]\n}\n'
        % (BASELINE_VERSION, json.dumps(_BASELINE_HEADER, ensure_ascii=False), body),
        encoding="utf-8",
    )


@dataclass(frozen=True)
class Comparison:
    """Écart entre le code et la baseline."""

    new: dict[tuple[str, str], list[Finding]]  # texte neuf (ou occurrence en trop) -> occurrences dans le code
    new_excess: dict[tuple[str, str], int]  # nombre d'occurrences au-delà de la baseline
    stale: dict[tuple[str, str], tuple[int, int]]  # (baseline, code) quand le code en a moins

    @property
    def clean(self) -> bool:
        return not self.new and not self.stale


def compare(findings: list[Finding], baseline: Counter[tuple[str, str]]) -> Comparison:
    current = counts_of(findings)
    by_key: dict[tuple[str, str], list[Finding]] = defaultdict(list)
    for finding in findings:
        by_key[finding.key].append(finding)
    new: dict[tuple[str, str], list[Finding]] = {}
    new_excess: dict[tuple[str, str], int] = {}
    stale: dict[tuple[str, str], tuple[int, int]] = {}
    for key, count in current.items():
        allowed = baseline.get(key, 0)
        if count > allowed:
            new[key] = by_key[key]
            new_excess[key] = count - allowed
    for key, allowed in baseline.items():
        count = current.get(key, 0)
        if count < allowed:
            stale[key] = (allowed, count)
    return Comparison(new=new, new_excess=new_excess, stale=stale)


NEW_ADVICE = (
    "Ne l'ajoutez PAS à la baseline : passez par le système i18n existant. Remplacez le littéral par "
    'translate("domaine.clé") (voir ui/i18n.py, ui/i18n_hardware.py, ui/i18n_mograph.py, ui/i18n_tracking.py) '
    "et ajoutez la clé en fr, en ET es (mêmes champs {nom} dans les trois langues). Le texte français doit rester "
    "identique si un test le vérifie. Donnée technique qui ressemble à du français : `# i18n-ignore: raison` "
    "sur la ligne."
)
STALE_ADVICE = (
    "Ces textes ne sont plus (ou moins souvent) écrits en dur : bravo, c'est de la dette en moins. "
    "Retirez-les de tests/i18n_hardcoded_baseline.json avec `python -m tools.i18n_audit --update-baseline`."
)


def format_new(comparison: Comparison) -> str:
    lines = ["Nouveaux textes d'interface écrits en dur (non traduits) :"]
    for key in sorted(comparison.new):
        file, text = key
        excess = comparison.new_excess[key]
        where = ", ".join(f"ligne {finding.line} ({finding.call or finding.category})" for finding in comparison.new[key])
        lines.append(f"  - {file} : « {text} » ({excess} occurrence(s) de plus que la baseline ; {where})")
    lines.append(NEW_ADVICE)
    return "\n".join(lines)


def format_stale(comparison: Comparison) -> str:
    lines = ["Entrées de la baseline périmées (texte migré ou supprimé) :"]
    for (file, text), (allowed, count) in sorted(comparison.stale.items()):
        lines.append(f"  - {file} : « {text} » (baseline : {allowed}, code : {count})")
    lines.append(STALE_ADVICE)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Affichage
# ---------------------------------------------------------------------------


def format_summary(findings: list[Finding]) -> str:
    by_zone: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        by_zone[zone_of(finding.file)].append(finding)
    lines = [f"{'zone':<22}{'chaînes':>9}{'occurrences':>13}"]
    total_distinct = 0
    for zone in zone_names():
        zone_findings = by_zone.get(zone, [])
        distinct = len({finding.key for finding in zone_findings})
        total_distinct += distinct
        lines.append(f"{zone:<22}{distinct:>9}{len(zone_findings):>13}")
    lines.append(f"{'TOTAL':<22}{total_distinct:>9}{len(findings):>13}")
    by_category = Counter(finding.category for finding in findings)
    lines.append("")
    lines.append("Par catégorie (occurrences) :")
    for category, count in by_category.most_common():
        lines.append(f"  {CATEGORIES.get(category, category):<42}{count:>5}")
    return "\n".join(lines)


def format_list(findings: list[Finding], zone: str | None = None) -> str:
    by_zone: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        by_zone[zone_of(finding.file)].append(finding)
    blocks: list[str] = []
    for name in zone_names():
        if zone is not None and name != zone:
            continue
        zone_findings = by_zone.get(name, [])
        if not zone_findings:
            continue
        distinct = len({finding.key for finding in zone_findings})
        blocks.append(f"== {name} : {distinct} chaîne(s), {len(zone_findings)} occurrence(s) ==")
        for finding in zone_findings:
            blocks.append(f"  {finding.file}:{finding.line}  [{finding.category}:{finding.call}]  « {finding.text} »")
        blocks.append("")
    return "\n".join(blocks).rstrip()


# ---------------------------------------------------------------------------
# Messages d'erreur du cœur (dette « erreurs du cœur », hors baseline)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoreMessage:
    file: str
    line: int
    exception: str
    text: str


def _message_templates(node: ast.expr) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return [_joined_template(node)]
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return _message_templates(node.left) + (
            _message_templates(node.right) if isinstance(node.op, ast.Add) else []
        )
    if isinstance(node, ast.IfExp):
        return _message_templates(node.body) + _message_templates(node.orelse)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return _message_templates(node.func.value)
    return []


def scan_core_messages(root: Path = ROOT) -> list[CoreMessage]:
    """``raise X("français…")`` du cœur : messages qui peuvent être affichés tels quels à l'utilisateur."""
    messages: list[CoreMessage] = []
    for path in python_files(root, (CORE_PACKAGE,)):
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call) or not node.exc.args:
                continue
            exception = _callee_name(node.exc.func)
            for template in _message_templates(node.exc.args[0]):
                text = normalize_text(template)
                if text and is_hardcoded_french(text):
                    messages.append(CoreMessage(relative, node.lineno, exception, text))
    return messages


def format_core(messages: list[CoreMessage]) -> str:
    by_exception: Counter[str] = Counter(message.exception for message in messages)
    by_module: Counter[str] = Counter(message.file for message in messages)
    lines = [f"Messages d'erreur français du cœur : {len(messages)} (hors baseline, non migrés)", "", "Par exception :"]
    for name, count in by_exception.most_common():
        lines.append(f"  {name:<34}{count:>5}")
    lines += ["", "Par module :"]
    for name, count in by_module.most_common():
        lines.append(f"  {name:<34}{count:>5}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Ligne de commande
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.i18n_audit", description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="échoue (code 1) s'il y a du neuf ou du périmé (défaut)")
    mode.add_argument("--list", action="store_true", help="liste la dette courante, par zone")
    mode.add_argument("--summary", action="store_true", help="chiffres par zone et par catégorie")
    mode.add_argument("--update-baseline", action="store_true", help="retire de la baseline ce qui a été migré")
    mode.add_argument("--core", action="store_true", help="inventaire des messages d'erreur du cœur")
    parser.add_argument("--zone", choices=zone_names(), help="avec --list : une seule zone")
    parser.add_argument("--accept-new", action="store_true", help="avec --update-baseline : accepte aussi du texte neuf")
    parser.add_argument("--root", type=Path, default=ROOT, help="racine du dépôt (tests)")
    parser.add_argument("--baseline", type=Path, default=None, help="fichier de baseline (tests)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root: Path = args.root
    baseline_path: Path = args.baseline or (root / BASELINE_PATH.relative_to(ROOT))
    if args.core:
        print(format_core(scan_core_messages(root)))
        return 0
    findings = scan_tree(root)
    if args.list:
        print(format_list(findings, args.zone))
        return 0
    if args.summary:
        print(format_summary(findings))
        return 0
    baseline = load_baseline(baseline_path)
    comparison = compare(findings, baseline)
    if args.update_baseline:
        if comparison.new and not args.accept_new:
            print(format_new(comparison), file=sys.stderr)
            print(
                "\nBaseline NON modifiée : une mise à jour ne peut que retirer de la dette. "
                "Pour (ré)générer la baseline, ajoutez --accept-new (à éviter hors initialisation).",
                file=sys.stderr,
            )
            return 1
        current = counts_of(findings)
        updated = current if args.accept_new else Counter({key: min(count, baseline[key]) for key, count in current.items()
                                                           if key in baseline})
        write_baseline(updated, baseline_path)
        print(f"Baseline mise à jour : {sum(updated.values())} occurrence(s), {len(updated)} entrée(s).")
        return 0
    if comparison.clean:
        print(f"i18n : aucune dette nouvelle ni périmée ({sum(baseline.values())} occurrence(s) connue(s)).")
        return 0
    if comparison.new:
        print(format_new(comparison), file=sys.stderr)
    if comparison.stale:
        print(format_stale(comparison), file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
