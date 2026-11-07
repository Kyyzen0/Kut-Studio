"""Audit de la fenêtre et des panneaux sur écran de portable (outil de développement, hors écran).

    QT_QPA_PLATFORM=offscreen python -m tools.ui_layout_audit --out ~/Movies/Kut-Studio-UX/apres
    QT_QPA_PLATFORM=offscreen python -m tools.ui_layout_audit --sizes 1440x900 --levels simple --pages edit

Pour chaque écran logique simulé (1280x800, 1440x900, 1680x1050 : MacBook Air 13 pouces selon le réglage de la
résolution), on ouvre la fenêtre à la taille **utilisable** (écran moins barre de menus et Dock), puis, pour chaque page
(Montage, Composition, Couleur, Audio) et chaque niveau d'usage, on mesure :

* la taille minimale que la fenêtre impose (``minimumSize`` et ``minimumSizeHint``) face à la zone utilisable ;
* la taille réelle de la fenêtre après affichage (Qt l'agrandit au-delà de la zone si son minimum est trop grand : c'est
  ce qui faisait passer le Dock macOS par-dessus la timeline) ;
* la hauteur de la timeline et celle de chaque liste principale ;
* les constats structurels de ``tools.ui_audit`` (widgets frères qui se chevauchent...).

Une capture PNG par combinaison est déposée dans ``--out`` (jamais dans le dépôt) ; ``--sheet`` assemble une planche contact.
La configuration, les caches et les proxys sont isolés : la vraie configuration de l'utilisateur n'est jamais touchée.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

#: Écrans logiques simulés (largeur, hauteur) : tailles de bureau d'un MacBook Air 13" et d'un écran plus grand.
SCREENS: tuple[tuple[int, int], ...] = ((1280, 800), (1440, 900), (1680, 1050))

#: Barre de menus macOS + Dock (réglage courant) retirés de la hauteur de l'écran pour obtenir la zone utilisable.
CHROME_HEIGHT: int = 110

PAGE_NAMES: tuple[str, ...] = ("edit", "composition", "color", "audio")
LEVELS: tuple[str, ...] = ("simple", "studio", "advanced")
LIBRARY_SECTIONS: tuple[str, ...] = ("media", "effects", "text", "audio", "transitions", "graphics", "sequences")


def usable_area(screen: tuple[int, int]) -> tuple[int, int]:
    """Zone utilisable d'un écran logique : largeur entière, hauteur moins barre de menus et Dock."""
    return screen[0], screen[1] - CHROME_HEIGHT


@dataclass(frozen=True)
class Measure:
    """Mesures d'une fenêtre à une taille d'écran, une page et un niveau."""

    screen: tuple[int, int]
    page: str
    level: str
    minimum: tuple[int, int]       # minimumSize() effectif
    hint: tuple[int, int]          # minimumSizeHint()
    actual: tuple[int, int]        # taille réelle après affichage
    timeline_height: int
    culprits: tuple[str, ...]      # widgets qui imposent le minimum

    def fits(self) -> bool:
        width, height = usable_area(self.screen)
        return self.actual[0] <= width and self.actual[1] <= height and self.hint[0] <= width and self.hint[1] <= height

    def line(self) -> str:
        width, height = usable_area(self.screen)
        verdict = "ok" if self.fits() else "TROP GRANDE"
        return (
            f"{self.screen[0]}x{self.screen[1]} (utilisable {width}x{height}) {self.page:<11} {self.level:<8} "
            f"min={self.minimum[0]}x{self.minimum[1]} hint={self.hint[0]}x{self.hint[1]} "
            f"reelle={self.actual[0]}x{self.actual[1]} timeline_h={self.timeline_height}  {verdict}"
        )


def height_culprits(window, limit: int = 6) -> tuple[str, ...]:
    """Les widgets visibles dont la hauteur minimale pèse le plus : pistes pour trouver qui impose la taille minimale."""
    from PySide6.QtWidgets import QWidget

    rows = []
    for widget in window.findChildren(QWidget):
        if not widget.isVisible() or widget.window() is not window:
            continue
        hint = widget.minimumSizeHint().height()
        fixed = widget.minimumHeight()
        rows.append((max(hint, fixed), widget))
    rows.sort(key=lambda row: -row[0])
    out = []
    for height, widget in rows[:limit]:
        out.append(f"{height}px {type(widget).__name__}#{widget.objectName() or '-'}")
    return tuple(out)


def measure(window, screen: tuple[int, int], page: str, level: str) -> Measure:
    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()
    minimum = window.minimumSize()
    hint = window.minimumSizeHint()
    timeline = window.timeline_panel
    return Measure(
        screen, page, level, (minimum.width(), minimum.height()), (hint.width(), hint.height()),
        (window.width(), window.height()), timeline.height(), height_culprits(window),
    )


def open_window(screen: tuple[int, int], level: str, *, rich: bool = True):
    """Fenêtre ouverte comme sur l'écran simulé : taille utilisable demandée, Qt la relève si le minimum est plus grand."""
    from tools.ui_audit import make_main_window

    width, height = usable_area(screen)
    return make_main_window(width, height, rich=rich, usage_level=level)


def audit(screens, levels, pages, out_dir: Path | None) -> list[Measure]:
    from tools.ui_audit import settle

    results: list[Measure] = []
    for screen in screens:
        for level in levels:
            window = open_window(screen, level)
            try:
                if "edit" in pages and out_dir is not None:
                    out_dir.mkdir(parents=True, exist_ok=True)
                    for section in LIBRARY_SECTIONS:
                        window.project_panel.select_section(section)
                        settle(window)
                        window.grab().save(str(out_dir / f"{screen[0]}x{screen[1]}-{level}-edit-{section}.png"))
                    window.project_panel.select_section("media")
                for page in pages:
                    window.switch_page(page)
                    settle(window)
                    results.append(measure(window, screen, page, level))
                    if out_dir is not None:
                        out_dir.mkdir(parents=True, exist_ok=True)
                        window.grab().save(str(out_dir / f"{screen[0]}x{screen[1]}-{level}-{page}.png"))
            finally:
                window.close()
    return results


def contact_sheet(folder: Path, target: Path, columns: int = 3, thumb_width: int = 640) -> Path:
    """Planche contact des PNG d'un dossier (nom sous chaque vignette)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter

    files = sorted(folder.glob("*.png"))
    thumbs = []
    for file in files:
        image = QImage(str(file))
        if not image.isNull():
            thumbs.append((file.stem, image.scaledToWidth(thumb_width, Qt.TransformationMode.SmoothTransformation)))
    if not thumbs:
        raise SystemExit(f"aucune capture dans {folder}")
    cell_h = max(image.height() for _, image in thumbs) + 24
    rows = (len(thumbs) + columns - 1) // columns
    sheet = QImage(columns * (thumb_width + 8) + 8, rows * (cell_h + 8) + 8, QImage.Format.Format_RGB32)
    sheet.fill(QColor("#202020"))
    painter = QPainter(sheet)
    painter.setPen(QColor("#dddddd"))
    painter.setFont(QFont("Inter", 9))
    for index, (name, image) in enumerate(thumbs):
        x = 8 + (index % columns) * (thumb_width + 8)
        y = 8 + (index // columns) * (cell_h + 8)
        painter.drawText(x, y + 14, name)
        painter.drawImage(x, y + 20, image)
    painter.end()
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(str(target))
    return target


def _parse_screens(text: str) -> list[tuple[int, int]]:
    out = []
    for part in text.split(","):
        width, _, height = part.strip().lower().partition("x")
        out.append((int(width), int(height)))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sizes", default=",".join(f"{w}x{h}" for w, h in SCREENS), help="écrans logiques simulés")
    parser.add_argument("--levels", default=",".join(LEVELS))
    parser.add_argument("--pages", default=",".join(PAGE_NAMES))
    parser.add_argument("--out", type=Path, default=None, help="dossier des captures (hors dépôt)")
    parser.add_argument("--sheet", type=Path, default=None, help="planche contact à écrire depuis --out")
    parser.add_argument("--culprits", action="store_true", help="liste les widgets qui imposent la hauteur minimale")
    args = parser.parse_args(argv)

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from tools.ui_audit import ensure_offscreen_fonts, isolate_user_config

    ensure_offscreen_fonts()
    isolate_user_config()          # jamais la configuration de l'utilisateur
    results = audit(
        _parse_screens(args.sizes), [x for x in args.levels.split(",") if x], [x for x in args.pages.split(",") if x],
        args.out,
    )
    for result in results:
        print(result.line())
        if args.culprits:
            for item in result.culprits:
                print("    ", item)
    if args.sheet is not None and args.out is not None:
        print("planche :", contact_sheet(args.out, args.sheet))
    return 0 if all(result.fits() for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
