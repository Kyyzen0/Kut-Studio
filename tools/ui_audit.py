"""Audit de l'interface aux petites tailles de fenêtre et au clavier (outil de développement).

Rejouable à la main, sans écran réel (plateforme Qt ``offscreen``) ::

    QT_QPA_PLATFORM=offscreen python -m tools.ui_audit                       # 3 tailles, constats bloquants
    QT_QPA_PLATFORM=offscreen python -m tools.ui_audit --out /tmp/audit      # + captures PNG de chaque scénario
    QT_QPA_PLATFORM=offscreen python -m tools.ui_audit --font-scale 1.2      # simule des polices plus larges
    QT_QPA_PLATFORM=offscreen python -m tools.ui_audit --verbose             # + constats informatifs (faux positifs fréquents)
    QT_QPA_PLATFORM=offscreen python -m tools.ui_audit --dialogs             # parcours clavier des dialogues

Les contrôles sont **structurels** (relatifs à la police de la plateforme, jamais des pixels absolus) :

* ``clipped`` : un contrôle visible dépasse la largeur du viewport d'une zone défilante sans barre horizontale ;
  il est coupé et on ne peut pas l'atteindre ;
* ``overflows-host`` : un panneau, ou un bloc du squelette de l'interface, sort de son conteneur ;
* ``overlaps`` : deux widgets frères rangés par le même layout se chevauchent (l'un est écrasé sous son minimum).

Ces constats sont **bloquants** : les tests de ``tests/test_ui_small_windows.py`` exigent qu'il n'y en ait aucun.
Les contrôles « écrasés sous leur ``minimumSizeHint`` » sont seulement **informatifs** (``--verbose``) : un bouton-icône de
taille fixe est une fausse alerte fréquente, il faut regarder la capture avant de corriger.

``--dialogs`` audite le clavier des dialogues (``tests/test_ui_keyboard.py`` en rejoue les règles) :

* ``keyboard`` : un contrôle que Tab n'atteint pas ;
* ``default`` : un dialogue qui n'a pas exactement un bouton par défaut (Entrée) ;
* ``unnamed`` : un bouton sans texte lisible ni nom accessible.

Les fonctions de ce module sont aussi la boîte à outils des tests (``from tools.ui_audit import ...``).
"""

from __future__ import annotations

import argparse
import atexit
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

SIZES: tuple[tuple[int, int], ...] = ((1440, 900), (1280, 720), (1180, 720))
"""Tailles de fenêtre supportées (le minimum de ``MainWindow`` est 1180 × 720)."""

TOLERANCE = 1
"""Dépassement toléré (px) : arrondis de géométrie, bordures de 1 px."""


@dataclass(frozen=True)
class Finding:
    """Un constat de l'audit."""

    kind: str
    where: str
    detail: str
    blocking: bool = True

    def __str__(self) -> str:
        return f"[{self.kind}] {self.where} : {self.detail}"


# ---------------------------------------------------------------------------
# Contrôles structurels (sans dépendance au projet : ils prennent un widget)
# ---------------------------------------------------------------------------


def widget_path(widget, depth: int = 5) -> str:
    """Chemin lisible d'un widget (noms d'objet, sinon noms de classe), du plus haut au plus bas."""
    names: list[str] = []
    while widget is not None and len(names) < depth:
        names.append(widget.objectName() or type(widget).__name__)
        widget = widget.parentWidget()
    return "/".join(reversed(names))


def _is_scroll_internal(widget) -> bool:
    return widget.objectName().startswith("qt_")


def _inside_inner_scroll_area(widget, boundary) -> bool:
    """``True`` si ``widget`` vit dans un viewport d'une autre zone défilante que celle de ``boundary``.

    Ce qui est dans une liste ou un arbre défile à sa façon : on ne le juge pas par rapport au conteneur extérieur.
    """
    from PySide6.QtWidgets import QAbstractScrollArea

    parent = widget.parentWidget()
    while parent is not None and parent is not boundary:
        if isinstance(parent.parentWidget(), QAbstractScrollArea) and parent.objectName() == "qt_scrollarea_viewport":
            return True
        parent = parent.parentWidget()
    return False


_CONTROL_TYPES: tuple[str, ...] = (
    "QAbstractButton", "QLineEdit", "QComboBox", "QAbstractSpinBox", "QSlider", "QAbstractItemView", "QLabel",
)


def _is_control(widget) -> bool:
    return any(widget.inherits(name) for name in _CONTROL_TYPES)


def scroll_culprits(scroll, limit: int = 6) -> list[str]:
    """Widgets les plus profonds dont la largeur minimale dépasse le bord droit utile d'une zone défilante.

    Un widget placé en ``x`` avec une largeur minimale ``m`` doit tenir : ``x + m ≤ largeur du viewport - marge``.
    """
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QWidget

    content = scroll.widget()
    layout = content.layout()
    margin = layout.contentsMargins().right() if layout is not None else 0
    edge = scroll.viewport().width() - margin
    flagged: dict[QWidget, int] = {}
    for child in content.findChildren(QWidget):
        if not child.isVisibleTo(content) or child.width() <= 0 or _is_scroll_internal(child):
            continue
        if _inside_inner_scroll_area(child, content) or not (_is_control(child) or child.layout() is not None):
            continue
        needed = child.mapTo(content, QPoint(0, 0)).x() + max(child.minimumSizeHint().width(), child.minimumWidth())
        if needed > edge + TOLERANCE:
            flagged[child] = needed - edge
    deepest = [w for w in flagged if not any(other is not w and w.isAncestorOf(other) for other in flagged)]
    deepest.sort(key=lambda w: -flagged[w])
    names = []
    for widget in deepest[:limit]:
        text = widget.text()[:20] if hasattr(widget, "text") and callable(widget.text) else ""
        names.append(f"{widget_path(widget, 2)} {text!r} (+{flagged[widget]} px)".replace(" ''", ""))
    return names


def ensure_offscreen_fonts() -> None:
    """Donne de vraies polices à la plateforme Qt ``offscreen`` de Windows (à appeler avant de créer ``QApplication``).

    Sous Windows, ``offscreen`` ne charge aucune police : ``QFontDatabase.families()`` est vide et Qt dessine chaque
    caractère comme une boîte aussi large que la police est haute. Un texte y mesure alors 2,3 fois sa largeur
    réelle (« État : Aucun clip sélectionné » : 377 px au lieu de 161 px en Segoe UI), et l'audit y signalait des
    dépassements qui n'existent pas. ``QT_QPA_FONTDIR`` désigne le dossier des polices du système.
    """
    if sys.platform == "win32" and os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"))


def _item_text(widget) -> str:
    text = widget.text() if hasattr(widget, "text") and callable(widget.text) else ""
    return f" {text[:24]!r}" if isinstance(text, str) and text else ""


def min_width_path(content, depth: int = 10) -> str:
    """Chaîne de widgets qui impose la largeur minimale de ``content`` (le « chemin critique » de sa largeur).

    ``scroll_culprits`` liste les contrôles trop larges pour le viewport ; celui-ci répond à la question utile :
    *qui* fixe le minimum du contenu. À chaque niveau on suit l'élément de layout le plus large (son
    ``minimumSize`` est celui que Qt utilise réellement : un minimum explicite l'emporte sur le ``minimumSizeHint``) ;
    une rangée de plusieurs éléments est détaillée, car ses largeurs s'additionnent.
    """
    steps: list[str] = []
    widget = content
    while widget is not None and len(steps) < depth:
        layout = widget.layout()
        name = widget.objectName() or type(widget).__name__
        if layout is None:
            steps.append(f"{name}{_item_text(widget)} {widget.minimumSizeHint().width()} px")
            break
        items = [layout.itemAt(index) for index in range(layout.count())]
        items = [item for item in items if item is not None and not item.isEmpty()]
        if not items:
            steps.append(f"{name} {layout.totalMinimumSize().width()} px")
            break
        widest = max(items, key=lambda item: item.minimumSize().width())
        own, inner = widget.minimumSizeHint().width(), layout.totalMinimumSize().width()
        steps.append(f"{name} {own} px")
        if widget.inherits("QGroupBox") and own > inner + 2 * layout.contentsMargins().left() + 4:
            steps.append(f"titre {widget.title()[:28]!r} (plus large que son contenu : {inner} px)")
            break
        if widest.widget() is not None:
            widget = widest.widget()
            continue
        row = widest.layout()
        cells = []
        for index in range(row.count() if row is not None else 0):
            cell = row.itemAt(index)
            if cell is None or cell.isEmpty():
                continue
            child = cell.widget()
            label = (child.objectName() or type(child).__name__) + _item_text(child) if child is not None else "…"
            cells.append(f"{label} {cell.minimumSize().width()}")
        steps.append("rangée [" + " + ".join(cells) + "]")
        break
    return " > ".join(steps)


def environment_summary() -> str:
    """Contexte de plateforme d'une mesure : police effective, DPI, style, plateforme (à joindre aux échecs).

    Les contrôles sont relatifs à la police de la plateforme ; sans ce contexte, un constat qui n'apparaît que sous
    Windows ou Linux ne dit pas pourquoi (famille de police absente, DPI, style…).
    """
    from PySide6.QtGui import QFont, QFontDatabase, QFontInfo, QFontMetrics, QGuiApplication
    from PySide6.QtWidgets import QApplication, QLabel

    from ui.theme import _UI_FONT_FAMILIES

    app = QApplication.instance()
    if app is None:
        return "pas d'application Qt"
    screen = QGuiApplication.primaryScreen()
    installed = set(QFontDatabase.families())
    probe = QLabel("État : Aucun clip sélectionné")
    probe.ensurePolished()
    info = QFontInfo(probe.font())
    sample = "État : Aucun clip sélectionné"
    return (
        f"plateforme={QGuiApplication.platformName()} style={app.style().objectName()} "
        f"dpi={screen.logicalDotsPerInch():.0f} dpr={screen.devicePixelRatio():.2f} "
        f"police_app={QFontInfo(QFont(app.font())).family()!r} "
        f"police_sonde={info.family()!r} {info.pixelSize()}px exacte={info.exactMatch()} "
        f"police_systeme={QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()!r} "
        f"dpi_physique={screen.physicalDotsPerInch():.0f} QT_FONT_DPI={os.environ.get('QT_FONT_DPI', '-')} "
        f"largeur({sample!r})={QFontMetrics(probe.font()).horizontalAdvance(sample)}px "
        f"famille_ok={[family for family in _UI_FONT_FAMILIES if family in installed]} polices={len(installed)}"
    )


def scroll_clipping(scroll, label: str = "") -> list[Finding]:
    """Contenu coupé par le bord droit d'une zone défilante **sans** barre horizontale utilisable.

    Quand la barre horizontale est disponible, rien n'est inaccessible : on ne signale rien. Un seul constat par zone,
    avec les contrôles à revoir et le chemin qui fixe la largeur minimale du contenu.
    """
    from PySide6.QtCore import Qt

    content = scroll.widget()
    if content is None:
        return []
    bar = scroll.horizontalScrollBar()
    if scroll.horizontalScrollBarPolicy() != Qt.ScrollBarAlwaysOff and bar.maximum() > 0:
        return []
    excess = content.width() - scroll.viewport().width()
    if excess <= TOLERANCE:
        return []
    culprits = ", ".join(scroll_culprits(scroll))
    return [Finding(
        "clipped", f"{label}{widget_path(scroll, 2)}",
        f"contenu de {content.width()} px dans un viewport de {scroll.viewport().width()} px (+{excess} px) ; "
        f"à revoir : {culprits} ; minimum fixé par : {min_width_path(content)}",
    )]


def content_min_width_excess(scroll) -> int:
    """Largeur de trop du contenu d'une zone défilante : minimum du contenu moins largeur du viewport (≤ 0 : ça tient)."""
    content = scroll.widget()
    if content is None:
        return 0
    return content.minimumSizeHint().width() - scroll.viewport().width()


def parent_overflow(root, label: str = "") -> list[Finding]:
    """Widgets visibles qui sortent du rectangle de leur parent (hors contenus défilants)."""
    from PySide6.QtWidgets import QMenu, QWidget

    findings: list[Finding] = []
    for widget in root.findChildren(QWidget):
        if not widget.isVisible() or widget.width() <= 0 or widget.isWindow() or isinstance(widget, QMenu):
            continue
        parent = widget.parentWidget()
        if parent is None or _is_scroll_internal(widget) or _is_scroll_internal(parent):
            continue
        if parent.inherits("QAbstractScrollArea") or _inside_inner_scroll_area(widget, root):
            continue
        over = max(
            widget.geometry().right() - parent.rect().right(),
            widget.geometry().bottom() - parent.rect().bottom(),
        )
        if over > TOLERANCE + 2:
            findings.append(Finding(
                "overflows-host", f"{label}{widget_path(widget)}",
                f"dépasse de {over} px son parent {widget_path(parent, 2)}",
            ))
    return findings


def _flat_layout_widgets(layout) -> list:
    widgets = []
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item is None:
            continue
        if item.widget() is not None:
            widgets.append(item.widget())
        elif item.layout() is not None:
            widgets.extend(_flat_layout_widgets(item.layout()))
    return widgets


def sibling_overlaps(root, label: str = "") -> list[Finding]:
    """Paires de widgets frères rangés par le même layout de boîte ou de formulaire qui se chevauchent."""
    from PySide6.QtWidgets import QBoxLayout, QFormLayout, QWidget

    findings: list[Finding] = []
    for parent in [root, *root.findChildren(QWidget)]:
        layout = parent.layout()
        if not parent.isVisible() or not isinstance(layout, (QBoxLayout, QFormLayout)):
            continue
        widgets = [w for w in _flat_layout_widgets(layout) if w.isVisible() and w.width() > 0 and w.height() > 0]
        for i, first in enumerate(widgets):
            for second in widgets[i + 1:]:
                inter = first.geometry().intersected(second.geometry())
                if inter.width() > TOLERANCE + 2 and inter.height() > TOLERANCE + 2:
                    findings.append(Finding(
                        "overlaps", f"{label}{widget_path(parent, 3)}",
                        f"{widget_path(first, 1)} et {widget_path(second, 1)} se chevauchent "
                        f"({inter.width()}×{inter.height()} px)",
                    ))
    return findings


def squeezed_controls(root, label: str = "") -> list[Finding]:
    """Contrôles plus petits que leur ``minimumSizeHint`` (informatif : nombreux faux positifs)."""
    from PySide6.QtWidgets import QWidget

    findings: list[Finding] = []
    for widget in root.findChildren(QWidget):
        if not widget.isVisible() or _is_scroll_internal(widget) or not _is_control(widget):
            continue
        if widget.inherits("QLabel") and (not widget.text() or widget.wordWrap()):
            continue
        hint = widget.minimumSizeHint()
        if widget.width() + 2 < hint.width() or widget.height() + 2 < hint.height():
            findings.append(Finding(
                "squeezed", f"{label}{widget_path(widget)}",
                f"{widget.width()}×{widget.height()} < minimum {hint.width()}×{hint.height()}", blocking=False,
            ))
    return findings


# ---------------------------------------------------------------------------
# Mise en place de la fenêtre principale
# ---------------------------------------------------------------------------


def scale_fonts(root, factor: float) -> None:
    """Agrandit toutes les tailles de police en pixels de la feuille de style (simule des polices plus larges)."""
    from PySide6.QtWidgets import QApplication, QWidget

    pattern = re.compile(r"font-size:\s*(\d+)px")

    def scaled(match: re.Match[str]) -> str:
        return f"font-size: {max(1, round(int(match.group(1)) * factor))}px"

    app = QApplication.instance()
    if app is not None:
        # La feuille de style de l'application survit à la fenêtre : on repart toujours de l'originale,
        # sinon l'agrandissement se cumulerait d'une fenêtre (donc d'une taille) à l'autre.
        base = app.property("_kut_audit_base_stylesheet")
        if base is None:
            base = app.styleSheet()
            app.setProperty("_kut_audit_base_stylesheet", base)
        app.setStyleSheet(pattern.sub(scaled, base))
    for widget in [root, *root.findChildren(QWidget)]:
        sheet = widget.styleSheet()
        if "font-size" in sheet:
            widget.setStyleSheet(pattern.sub(scaled, sheet))


_CONFIG_ROOT: Path | None = None


def isolate_user_config(monkeypatch=None) -> Path:
    """Dirige les **prochaines fenêtres** vers une configuration neuve et vide, jamais celle de l'utilisateur.

    Une ``MainWindow`` lit au démarrage, puis réécrit en vivant, les réglages de l'utilisateur, la disposition des panneaux et la
    file de rendu. Les mesures et les captures de cet outil en changeaient donc les réglages réels (le thème, les scopes) et
    démarraient chacune d'une disposition laissée par la fenêtre précédente : des colonnes dont les largeurs **dérivaient** d'une
    fenêtre à l'autre, ce qui rendait deux captures « identiques » différentes de plusieurs pixels, seulement sur une machine sans
    réglages enregistrés (la CI). Un dossier neuf par appel donne à chaque fenêtre le même point de départ.

    ``monkeypatch`` (pytest) : les variables sont posées par lui et rendues à son ``undo`` ; sans lui elles restent posées jusqu'à la
    fin du processus (un outil en ligne de commande), et le dossier est alors supprimé à la sortie."""
    global _CONFIG_ROOT
    if _CONFIG_ROOT is None:
        _CONFIG_ROOT = Path(tempfile.mkdtemp(prefix="kut-studio-ui-"))
        atexit.register(shutil.rmtree, _CONFIG_ROOT, ignore_errors=True)
    folder = Path(tempfile.mkdtemp(dir=_CONFIG_ROOT))
    values = {
        "KUT_STUDIO_CONFIG_DIR": str(folder / "config"),
        "KUT_STUDIO_CACHE_DIR": str(folder / "cache"),
        "KUT_STUDIO_PROXY_DIR": str(folder / "proxies"),
        "KUT_STUDIO_HARDWARE_ENCODING": "off",              # pas de détection GPU réelle : la même fenêtre sur toute machine
        "KUT_STUDIO_UPDATE_CHECK": "off",                   # aucune recherche de mise à jour vers GitHub
    }
    for name, value in values.items():
        if monkeypatch is not None:
            monkeypatch.setenv(name, value)
        else:
            os.environ[name] = value
    return folder


def make_main_window(width: int, height: int, *, scopes: bool = True, rich: bool = True, font_scale: float = 1.0):
    """Fenêtre principale de la taille demandée, avec le projet de démonstration riche, prête à être mesurée.

    Sans dossier de configuration déjà choisi (``KUT_STUDIO_CONFIG_DIR``), la fenêtre travaille dans une configuration jetable : elle
    ne lit ni n'écrit jamais celle de l'utilisateur (voir :func:`isolate_user_config`)."""
    from PySide6.QtWidgets import QApplication

    from ui.main_window import MainWindow

    if not os.environ.get("KUT_STUDIO_CONFIG_DIR"):
        isolate_user_config()

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.timeline_timer.stop()
    if rich:
        from tests.rich_project import build_rich_project

        window.project = build_rich_project()
        window._timeline_index = None
        window._reload_timeline_preserving_selection()
    window.resize(width, height)
    window.show()
    app.processEvents()
    if window.scopes_panel.isVisible() != scopes:
        window.toggle_scopes_visible()
    if font_scale != 1.0:
        scale_fonts(window, font_scale)
    settle(window)
    return window


def settle(window) -> None:
    """Laisse Qt appliquer les layouts en attente (quelques tours de boucle d'événements)."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    for _ in range(3):
        app.processEvents()


def select_first_clip(window) -> str | None:
    """Sélectionne le premier clip de la timeline (peuple l'inspecteur) ; retourne son identifiant."""
    views = window.timeline_panel.clip_views
    if not views:
        return None
    window._restore_clip_selection(views[0].id)
    settle(window)
    return views[0].id


INSPECTOR_TABS: tuple[tuple[int, str], ...] = (
    (0, "clip"), (1, "couleur"), (2, "effets"), (3, "audio"), (4, "graphiques"), (5, "compositing"), (6, "suivi"),
)


def inspector_scenarios(window) -> Iterable[tuple[str, Callable[[], None]]]:
    """Un scénario par onglet de l'inspecteur (clip sélectionné)."""
    panel = window.properties_panel
    for index, name in INSPECTOR_TABS:
        yield f"inspecteur-{name}", lambda i=index: (panel._select_inspector_tab(i), settle(window))


# ---------------------------------------------------------------------------
# Audit de la fenêtre principale
# ---------------------------------------------------------------------------


def audit_main_window(window, label: str) -> list[Finding]:
    """Tous les constats structurels de la fenêtre dans son état courant."""
    from PySide6.QtWidgets import QScrollArea

    findings: list[Finding] = []
    for scroll in window.findChildren(QScrollArea):
        if scroll.isVisible() and scroll.widget() is not None:
            findings += scroll_clipping(scroll, label + " ")
    findings += parent_overflow(window, label + " ")
    findings += sibling_overlaps(window, label + " ")
    findings += squeezed_controls(window, label + " ")
    return findings


LIBRARY_SECTIONS: tuple[str, ...] = (
    "media", "audio", "text", "effects", "transitions", "graphics", "sequences", "audio-effects",
)
"""Sections de la bibliothèque. « audio-effects » est le sous-mode « Effets audio » de la section Audio."""


def open_library_section(window, section: str) -> None:
    """Ouvre une section de la bibliothèque par l'API réelle de ``ProjectPanel`` (jamais en forçant la pile)."""
    panel = window.project_panel
    if section == "audio-effects":
        panel.set_audio_mode("effects")
        panel.select_section("audio")
    else:
        panel.set_audio_mode("files")
        panel.select_section(section)
    settle(window)


def library_scenarios(window) -> Iterable[tuple[str, Callable[[], None]]]:
    """Un scénario par section de la bibliothèque."""
    for section in LIBRARY_SECTIONS:
        yield f"bibliotheque-{section}", lambda s=section: open_library_section(window, s)
    yield "bibliotheque-medias-fin", lambda: open_library_section(window, "media")


def _clip_ids(window, predicate: Callable[[object, object], bool]) -> list[str]:
    return [clip.id for track in window.project.tracks for clip in track.clips if predicate(track, clip)]


def special_clip_scenarios(window) -> Iterable[tuple[str, Callable[[], None]]]:
    """Clips qui font apparaître d'autres groupes de l'inspecteur : sous-titre et calque graphique."""
    subtitles = _clip_ids(window, lambda track, clip: track.type == "subtitle")
    graphics = _clip_ids(window, lambda track, clip: getattr(clip, "graphic", None) is not None)
    for name, ids in (("sous-titre", subtitles), ("calque-graphique", graphics)):
        if ids:
            yield f"inspecteur-{name}", lambda i=ids[0]: (
                window._restore_clip_selection(i), window.properties_panel._select_inspector_tab(4), settle(window)
            )


def window_scenarios(window) -> Iterable[tuple[str, Callable[[], None]]]:
    """Fenêtres et pages annexes : éditeur de courbes, panneau d'export."""
    yield "editeur-de-courbes", lambda: (window.open_graph_editor(), settle(window))
    yield "export", lambda: (window.graph_editor.hide(), window.show_export(), settle(window))
    yield "retour-editeur", lambda: (window.show_editor(), settle(window))
    segments = _clip_ids(window, lambda track, clip: bool(clip.sequence_id) and window.project.get_sequence(clip.sequence_id) is not None
                         and window.project.get_sequence(clip.sequence_id).multicam is not None)
    if segments:
        yield "moniteur-multicam", lambda i=segments[0]: (window.show_multicam_viewer(i), settle(window))
        yield "retour-moniteur", lambda: (window._monitor_stack.setCurrentWidget(window.preview_panel), settle(window))


def _scenarios(window) -> Iterable[tuple[str, Callable[[], None]]]:
    yield "defaut", lambda: None
    if select_first_clip(window) is not None:
        yield from inspector_scenarios(window)
    yield from special_clip_scenarios(window)
    yield from library_scenarios(window)
    yield from window_scenarios(window)


def run_audit(
    sizes: Iterable[tuple[int, int]] = SIZES,
    *,
    out_dir: Path | None = None,
    font_scale: float = 1.0,
    scopes: bool = True,
) -> list[Finding]:
    """Joue tous les scénarios à chaque taille ; enregistre une capture par scénario si ``out_dir`` est donné."""
    findings: list[Finding] = []
    for width, height in sizes:
        window = make_main_window(width, height, scopes=scopes, font_scale=font_scale)
        try:
            for name, action in _scenarios(window):
                action()
                settle(window)
                label = f"{width}x{height} {name}"
                findings += audit_main_window(window, label)
                if out_dir is not None:
                    out_dir.mkdir(parents=True, exist_ok=True)
                    window.grab().save(str(out_dir / f"{width}x{height}-{name}.png"))
        finally:
            window.close()
    return findings


# ---------------------------------------------------------------------------
# Navigation au clavier
# ---------------------------------------------------------------------------

_INTERACTIVE_TYPES: tuple[str, ...] = (
    "QAbstractButton", "QAbstractSpinBox", "QComboBox", "QLineEdit", "QSlider", "QTextEdit", "QPlainTextEdit",
    "QAbstractItemView", "QKeySequenceEdit",
)


def interactive_controls(root) -> list:
    """Contrôles interactifs visibles et actifs sous ``root`` (sans leurs composants internes)."""
    from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QKeySequenceEdit, QTabBar, QWidget

    controls = []
    for widget in root.findChildren(QWidget):
        if not any(widget.inherits(name) for name in _INTERACTIVE_TYPES):
            continue
        if not widget.isVisibleTo(root) or not widget.isEnabled() or _is_scroll_internal(widget):
            continue
        parent = widget.parentWidget()
        if widget.inherits("QLineEdit") and isinstance(parent, (QAbstractSpinBox, QComboBox, QKeySequenceEdit)):
            continue  # le champ interne d'une boîte numérique, d'une liste éditable, d'un champ de raccourci
        if isinstance(parent, QTabBar) or widget.inherits("QHeaderView"):
            continue
        controls.append(widget)
    return controls


def _is_exclusive_radio(widget) -> bool:
    return widget.inherits("QRadioButton") and widget.autoExclusive()


def keyboard_gaps(root, label: str = "") -> list[Finding]:
    """Contrôles que Tab n'atteint pas : politique de focus sans ``TabFocus``.

    Un bouton radio d'un groupe exclusif n'a ``TabFocus`` que s'il est coché (les flèches changent de choix) : ce
    n'est pas un défaut tant qu'un bouton de son groupe l'a.
    """
    from PySide6.QtCore import Qt

    controls = interactive_controls(root)
    findings: list[Finding] = []
    for widget in controls:
        if widget.focusPolicy() & Qt.TabFocus:
            continue
        if _is_exclusive_radio(widget):
            group = widget.group()
            members = group.buttons() if group is not None else [
                w for w in controls if _is_exclusive_radio(w) and w.parentWidget() is widget.parentWidget()
            ]
            if any(member.focusPolicy() & Qt.TabFocus for member in members):
                continue
        name = widget.accessibleName() or widget.toolTip()[:30]
        findings.append(Finding(
            "keyboard", f"{label}{widget_path(widget, 3)}",
            f"n'accepte pas Tab (politique {int(widget.focusPolicy())}) {name!r}".rstrip(),
        ))
    return findings


def default_buttons(dialog) -> list:
    """Boutons marqués par défaut (Entrée) d'un dialogue affiché."""
    from PySide6.QtWidgets import QPushButton

    return [button for button in dialog.findChildren(QPushButton) if button.isDefault()]


def unnamed_icon_buttons(root, label: str = "") -> list[Finding]:
    """Boutons sans texte lisible (icône ou glyphe) et sans nom accessible : inutilisables avec une aide technique."""
    from PySide6.QtWidgets import QAbstractButton

    findings = []
    for button in root.findChildren(QAbstractButton):
        if not button.isVisibleTo(root):
            continue
        text = button.text().replace("&", "").strip()
        if sum(ch.isalnum() for ch in text) >= 2 or button.accessibleName():
            continue
        findings.append(Finding("unnamed", f"{label}{widget_path(button, 3)}", f"texte {text!r}, sans nom accessible"))
    return findings


def _owner(widget):
    """Le contrôle visible qui porte un composant interne focalisé (champ d'une boîte numérique…)."""
    from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QKeySequenceEdit

    parent = widget.parentWidget()
    if widget.inherits("QLineEdit") and isinstance(parent, (QAbstractSpinBox, QComboBox, QKeySequenceEdit)):
        return parent
    return widget


def tab_sequence(top, *, start=None, within=None, limit: int = 300) -> list:
    """Contrôles successivement focalisés par Tab (``QTest.keyClick``), jusqu'au tour complet ou à la sortie de ``within``."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    top.show()
    top.activateWindow()
    settle(top)
    sequence: list = []
    seen: set = set()
    if start is not None:
        start.setFocus(Qt.TabFocusReason)
        settle(top)
        sequence.append(start)
        seen.add(start)
    for _ in range(limit):
        QTest.keyClick(top, Qt.Key_Tab)
        settle(top)
        focused = QApplication.focusWidget()
        if focused is None:
            break
        focused = _owner(focused)
        if focused in seen:
            break
        if within is not None and not within.isAncestorOf(focused):
            if sequence:
                break
            continue
        seen.add(focused)
        sequence.append(focused)
    return sequence


def tab_order_violations(sequence, content) -> list[str]:
    """Paires de contrôles consécutifs où le second est au-dessus du premier (ou à sa gauche, sur une même ligne).

    ``content`` est le contenu défilant : ses contrôles sont comparés dans ses coordonnées (le défilement ne compte
    pas) ; ceux qui sont hors du contenu (l'en-tête et ses onglets) passent avant.
    """

    def centre(widget):
        """Le point qui représente le widget : le centre de son bandeau pour l'en-tête d'une section, celui du widget sinon."""
        if getattr(widget, "is_section_header", False):
            return widget.header_rect().center()
        return widget.rect().center()

    def height(widget) -> int:
        """La hauteur qui compte pour « sur la même ligne » : celle du bandeau d'une section, pas de la section entière."""
        return widget.header_rect().height() if getattr(widget, "is_section_header", False) else widget.height()

    def position(widget) -> tuple[int, int]:
        if content.isAncestorOf(widget):
            point = widget.mapTo(content, centre(widget))
            return point.x(), point.y()
        top = widget.window()
        point = widget.mapTo(top, centre(widget))
        return point.x(), point.y() - 1_000_000  # hors du contenu : avant lui

    problems = []
    for first, second in zip(sequence, sequence[1:]):
        (ax, ay), (bx, by) = position(first), position(second)
        same_row = abs(ay - by) <= max(height(first), height(second)) // 2
        backwards = bx < ax - 2 if same_row else by < ay
        if backwards:
            problems.append(f"{widget_path(first, 2)}@({ax},{ay}) -> {widget_path(second, 2)}@({bx},{by})")
    return problems


def dialog_factories(window) -> list[tuple[str, Callable[[], object]]]:
    """Les dialogues de l'application, construits comme l'application les construit."""
    from core.library_organization import LibraryOrganization
    from core.multicam_model import AudioMode, MulticamAudio, SyncStatus
    from ui.library_organization_widgets import TagManagerDialog
    from ui.multicam_dialogs import MulticamCreateDialog, SourceRow, SummaryRow, SyncSummaryDialog
    from ui.multicam_settings import MulticamSettingsDialog
    from ui.preferences_dialog import PreferencesDialog
    from ui.project_panel_widgets.effects_library_view import SavePresetDialog
    from ui.project_panel_widgets.transition_library import SaveTransitionPresetDialog

    return [
        ("Préférences", lambda: PreferencesDialog()),
        ("Préférences complètes", lambda: PreferencesDialog(shortcut_manager=window.shortcuts, performance_host=window)),
        ("Gestionnaire de tags", lambda: TagManagerDialog(LibraryOrganization(window.project))),
        ("Enregistrer un preset d'effet", lambda: SavePresetDialog(default_name="x")),
        ("Enregistrer une transition", lambda: SaveTransitionPresetDialog(default_name="x")),
        ("Créer une séquence Multicam", lambda: MulticamCreateDialog(
            [SourceRow("a", "Wide", info="1920×1080 · 25 · 01:00:00:00"), SourceRow("b", "Close-up", info="1920×1080 · 25")],
            default_name="Multicam", from_timeline=False, addable=[SourceRow("rec", "Recorder", kind="audio")],
        )),
        ("Réglages Multicam", lambda: _settings_dialog(window, MulticamSettingsDialog)),
        ("Résultat de la synchronisation", lambda: SyncSummaryDialog(
            [SummaryRow("Wide", SyncStatus.NONE, 0.0, reference=True), SummaryRow("Close-up", SyncStatus.GOOD, 2.48),
             SummaryRow("Drone", SyncStatus.FAILED)],
            audio_choices=[("suit", MulticamAudio(AudioMode.FOLLOW_VIDEO)), ("fixe", MulticamAudio(AudioMode.FIXED, ("angle-1",)))],
        )),
    ]


def _settings_dialog(window, dialog_class):
    """Réglages Multicam construits sur la première source Multicam du projet (le projet riche en contient une)."""
    dialog = dialog_class()
    source = next((sequence for sequence in window.project.sequences if sequence.multicam is not None), None)
    if source is not None:
        dialog.set_state(window.project, source.id)
    return dialog


def audit_dialog(name: str, dialog) -> list[Finding]:
    """Constats clavier d'un dialogue : contrôles non atteints par Tab, boutons par défaut, boutons sans nom."""
    dialog.show()
    dialog.activateWindow()
    settle(dialog)
    pages = [None]
    tabs = getattr(dialog, "tabs", None)
    if tabs is not None:
        pages = list(range(tabs.count()))
    findings: list[Finding] = []
    for page in pages:
        if page is not None:
            tabs.setCurrentIndex(page)
            settle(dialog)
        label = f"{name}{'' if page is None else f' (onglet {page})'} "
        findings += keyboard_gaps(dialog, label)
        reached = set(tab_sequence(dialog))
        for widget in interactive_controls(dialog):
            if widget not in reached and not _is_exclusive_radio(widget) and widget.focusPolicy() & 1:
                findings.append(Finding("keyboard", f"{label}{widget_path(widget, 3)}", "Tab ne l'atteint pas"))
    defaults = default_buttons(dialog)
    if len(defaults) != 1:
        findings.append(Finding("default", name, f"{len(defaults)} bouton(s) par défaut : {[b.text() for b in defaults]}"))
    findings += unnamed_icon_buttons(dialog, f"{name} ")
    dialog.close()
    return findings


def print_dialog_report() -> int:
    """Audite le clavier de chaque dialogue et affiche les constats (code de sortie 1 s'il y en a)."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = make_main_window(1280, 720, scopes=False)
    total = 0
    try:
        for name, factory in dialog_factories(window):
            findings = audit_dialog(name, factory())
            print(f"== {name} : {len(findings)} constat(s)")
            for finding in findings:
                print(f"   {finding}")
            total += len(findings)
    finally:
        window.close()
        app.processEvents()
    return 1 if total else 0


# ---------------------------------------------------------------------------
# Ligne de commande
# ---------------------------------------------------------------------------


def _parse_sizes(text: str) -> list[tuple[int, int]]:
    sizes = []
    for part in text.split(","):
        width, _, height = part.strip().lower().partition("x")
        sizes.append((int(width), int(height)))
    return sizes


def scratch_directory() -> tempfile.TemporaryDirectory[str]:
    """Dossier jetable de l'audit : préférences, caches et proxys y sont isolés, jamais ceux de l'utilisateur.

    Son nettoyage est **au mieux** et ne fait jamais le verdict de l'audit. Sous Windows un fichier écrit à l'instant
    (image de calque de motion graphics, ``.<nom>.<pid>.tmp.png``) peut rester verrouillé quelques instants, par un
    processus fils en train de se fermer ou par l'antivirus qui l'analyse : ``rmtree`` levait alors ``PermissionError
    [WinError 32]`` en sortant du bloc, le résumé n'était jamais affiché et le code de sortie valait 1 sans qu'aucun
    constat n'existe (un run de CI sur deux commits identiques pour ce test : vert, puis rouge). Un reliquat dans le
    dossier temporaire du système est sans importance.
    """
    return tempfile.TemporaryDirectory(prefix="kut-ui-audit-", ignore_cleanup_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sizes", default=",".join(f"{w}x{h}" for w, h in SIZES), help="ex. 1280x720,1180x720")
    parser.add_argument("--out", type=Path, default=None, help="dossier des captures PNG")
    parser.add_argument("--font-scale", type=float, default=1.0, help="agrandit les polices (ex. 1.2)")
    parser.add_argument("--no-scopes", action="store_true", help="masque les scopes (affichés par défaut)")
    parser.add_argument("--verbose", action="store_true", help="affiche aussi les constats informatifs")
    parser.add_argument("--dialogs", action="store_true", help="audite le parcours clavier des dialogues")
    args = parser.parse_args(argv)

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    ensure_offscreen_fonts()
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    with scratch_directory() as scratch:
        # Jamais les préférences ni les caches de l'utilisateur.
        os.environ["KUT_STUDIO_CONFIG_DIR"] = str(Path(scratch) / "config")
        os.environ["KUT_STUDIO_CACHE_DIR"] = str(Path(scratch) / "cache")
        os.environ["KUT_STUDIO_PROXY_DIR"] = str(Path(scratch) / "proxies")
        os.environ["KUT_STUDIO_HARDWARE_ENCODING"] = "off"  # pas de détection matérielle en tâche de fond
        if args.dialogs:
            return print_dialog_report()
        findings = run_audit(
            _parse_sizes(args.sizes), out_dir=args.out, font_scale=args.font_scale, scopes=not args.no_scopes
        )
    shown = [f for f in findings if f.blocking or args.verbose]
    seen: set[tuple[str, str]] = set()
    for finding in shown:
        key = (finding.kind, finding.where.split(" ", 2)[-1] + finding.detail)
        if key in seen:
            continue
        seen.add(key)
        print(finding)
    blocking = [f for f in findings if f.blocking]
    print(f"\nenvironnement : {environment_summary()}")
    print(f"\n{len(blocking)} constat(s) bloquant(s)" + (f", {len(findings) - len(blocking)} informatif(s)" if args.verbose else ""))
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
