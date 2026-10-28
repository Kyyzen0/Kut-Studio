"""Lisibilité d'une page (outil de développement, hors écran) : chevauchements, contenu coupé, libellés tronqués.

``tools.ui_audit`` ne regarde que les widgets frères rangés par une ``QBoxLayout`` ou une ``QFormLayout`` et laisse la
fenêtre s'agrandir : il n'a vu ni les étiquettes superposées du qualifieur (une ``QGridLayout``), ni le mixeur coupé, ni
le bouton « Pi...te ». Ce module ajoute trois contrôles, joués sur les pages Couleur et Audio **avec un clip sélectionné** :

* ``overlaps`` : deux widgets frères rangés par n'importe quelle layout (boîte, grille, formulaire, retour à la ligne)
  se chevauchent ;
* ``cut-off`` : un widget est plus bas que sa hauteur minimale utile **sans** zone défilante pour atteindre le reste ;
* ``elided-button`` : un bouton d'action est plus étroit que son texte (« Affi...tion »).

    QT_QPA_PLATFORM=offscreen python -m tools.ui_legibility --sizes 1440x790,1280x690 --out ~/Movies/Kut-Studio-UX/apres
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

from tools.ui_audit import TOLERANCE, Finding, widget_path

#: Un widget se chevauche lui-même s'il est « décoratif » : la couche de sous-titres du moniteur recouvre la vue exprès.
_OVERLAY_NAMES: tuple[str, ...] = ("_SubtitleLayer", "QGraphicsView")


def _layout_widgets(layout) -> list:
    out = []
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item is None:
            continue
        if item.widget() is not None:
            out.append(item.widget())
        elif item.layout() is not None:
            out += _layout_widgets(item.layout())
    return out


def overlapping_siblings(root, label: str = "") -> list[Finding]:
    """Frères rangés par une même layout (toute sorte, grille et retour à la ligne compris) qui se chevauchent."""
    from PySide6.QtWidgets import QWidget

    findings: list[Finding] = []
    for parent in [root, *root.findChildren(QWidget)]:
        layout = parent.layout()
        if layout is None or not parent.isVisible():
            continue
        widgets = [w for w in _layout_widgets(layout) if w.isVisible() and w.width() > 0 and w.height() > 0
                   and type(w).__name__ not in _OVERLAY_NAMES]
        for i, first in enumerate(widgets):
            for second in widgets[i + 1:]:
                inter = first.geometry().intersected(second.geometry())
                if inter.width() > TOLERANCE + 2 and inter.height() > TOLERANCE + 2:
                    findings.append(Finding(
                        "overlaps", f"{label}{widget_path(parent, 3)}",
                        f"{widget_path(first, 1)} et {widget_path(second, 1)} se chevauchent ({inter.width()}x{inter.height()} px)",
                    ))
    return findings


def _scrolls_its_content(widget) -> bool:
    """``True`` si une zone défilante entre ``widget`` et la fenêtre lui donnera de la place (il peut y être plus grand)."""
    from PySide6.QtWidgets import QAbstractScrollArea

    parent = widget.parentWidget()
    while parent is not None:
        if parent.objectName() == "qt_scrollarea_viewport" and isinstance(parent.parentWidget(), QAbstractScrollArea):
            return True
        parent = parent.parentWidget()
    return False


def cut_off_containers(root, label: str = "", *, threshold: int = 12) -> list[Finding]:
    """Conteneurs (avec layout) plus bas que leur hauteur minimale utile, hors de toute zone défilante.

    Une zone défilante (``QScrollArea``) ou un splitter ne sont jamais jugés eux-mêmes : c'est leur contenu qui défile.
    """
    from PySide6.QtWidgets import QAbstractScrollArea, QSplitter, QWidget

    findings: list[Finding] = []
    for widget in root.findChildren(QWidget):
        if (not widget.isVisible() or widget.layout() is None or widget.isWindow() or isinstance(widget, (QAbstractScrollArea, QSplitter))
                or widget.objectName().startswith("qt_") or _scrolls_its_content(widget)):
            continue
        need = widget.minimumSizeHint().height()
        if need > 0 and widget.height() + threshold < need:
            findings.append(Finding(
                "cut-off", f"{label}{widget_path(widget, 3)}",
                f"{widget.height()} px disponibles pour {need} px minimum (sans zone défilante)",
            ))
    return findings


def elided_buttons(root, label: str = "") -> list[Finding]:
    """Boutons à texte plus étroits que leur texte : le style le tronque avec « ... »."""
    from PySide6.QtWidgets import QAbstractButton

    findings: list[Finding] = []
    for button in root.findChildren(QAbstractButton):
        if not button.isVisible() or not button.text().strip() or button.objectName().startswith("qt_"):
            continue
        if button.minimumWidth() == button.maximumWidth():
            continue                                  # taille imposée (flèches ‹ ›, boutons carrés) : voulue
        if button.width() + 4 <= button.sizeHint().width() and button.width() < button.minimumSizeHint().width() + 4:
            findings.append(Finding(
                "elided-button", f"{label}{widget_path(button, 3)}",
                f"« {button.text()[:30]} » : {button.width()} px pour {button.sizeHint().width()} px",
            ))
    return findings


def legibility_findings(root, label: str = "") -> list[Finding]:
    return overlapping_siblings(root, label) + cut_off_containers(root, label) + elided_buttons(root, label)


# ---------------------------------------------------------------------------
# Scénarios : pages Couleur et Audio, clip sélectionné
# ---------------------------------------------------------------------------


def page_scenarios(window) -> Iterable[tuple[str, Callable[[], None]]]:
    """Un scénario par onglet de la page Couleur et par onglet d'inspecteur de la page Audio (un clip est sélectionné)."""
    from tools.ui_audit import INSPECTOR_TABS, select_first_clip, settle

    select_first_clip(window)
    colour = window.color_panel

    def colour_tab(index: int, timeline: bool) -> None:
        window.switch_page("color")
        window.clip_strip.timeline_button.setChecked(timeline)          # « Afficher la timeline » : état explicite à chaque scénario
        colour.tabs.setCurrentIndex(index)
        settle(window)

    def montage(mixer_open: bool) -> None:
        window.switch_page("edit")
        if window.mixer_panel.collapsed == mixer_open:                 # un clic sur la barre (ou « Replier ») : la voie de l'utilisateur
            window.mixer_panel._toggle_collapsed()
        settle(window)

    yield "montage-mixeur-replie", lambda: montage(False)              # barre de 40 px sous la timeline
    yield "montage-mixeur-ouvert", lambda: montage(True)
    yield "montage-mixeur-replie-2", lambda: montage(False)            # l'état de départ des scénarios suivants
    for index, name in enumerate(("roues", "qualificateur", "fenetres", "flou")):
        yield f"couleur-{name}", lambda i=index: colour_tab(i, False)
    yield "couleur-roues-timeline", lambda: colour_tab(0, True)         # timeline rouverte sous la page, avec la bande des plans
    yield "couleur-qualificateur-timeline", lambda: colour_tab(1, True)
    for index, name in INSPECTOR_TABS:
        yield f"audio-inspecteur-{name}", lambda i=index: (
            colour_tab(0, False), window.switch_page("audio"), window.properties_panel._select_inspector_tab(i), settle(window))


def run(sizes: Iterable[tuple[int, int]], out_dir: Path | None = None, level: str = "advanced") -> list[Finding]:
    from tools.ui_audit import make_main_window, settle

    findings: list[Finding] = []
    for width, height in sizes:
        window = make_main_window(width, height, scopes=False, usage_level=level)
        try:
            for name, action in page_scenarios(window):
                action()
                settle(window)
                label = f"{width}x{height} {name}"
                findings += legibility_findings(window, label + " ")
                if window.height() > height:
                    findings.append(Finding("window-grows", label, f"la fenêtre est passée de {height} à {window.height()} px"))
                if out_dir is not None:
                    out_dir.mkdir(parents=True, exist_ok=True)
                    window.grab().save(str(out_dir / f"{width}x{height}-{name}.png"))
        finally:
            window.close()
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sizes", default="1440x790,1280x690,1222x738", help="tailles de fenêtre (zone utilisable d'un Air 13\")")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tools.ui_audit import ensure_offscreen_fonts, isolate_user_config

    ensure_offscreen_fonts()
    isolate_user_config()
    sizes = [tuple(int(v) for v in part.lower().split("x")) for part in args.sizes.split(",")]
    findings = run(sizes, args.out)  # type: ignore[arg-type]
    for finding in findings:
        print(finding)
    print(f"{len(findings)} constat(s)")
    return 1 if any(f.blocking for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
