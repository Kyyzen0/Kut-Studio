"""Rail vertical d'icônes de Kut‑Studio.

Rail compact inspiré de DaVinci Resolve et Final Cut Pro. Il
donne accès aux sections globales (Médias, Éditer, Effets, Texte,
Transitions, Audio, Graphiques, Modèles) sans alourdir la barre
supérieure. Chaque bouton porte son nom écrit sous l'icône, en permanence
(on ne devine pas une icône) ; l'infobulle et le nom accessible reprennent ce
même nom. L'état actif utilise un fond vert
foncé arrondi et une icône turquoise.

Aucune logique métier ici : le rail expose un signal ``section_changed``
que la fenêtre principale traduit en bascule de panneau / d'onglet.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import QVBoxLayout, QWidget

from core.usage_level import UsageLevel, coerce_usage_level
from ui.design_system import Iconography, Sizes, Spacing
from ui.i18n import translate
from ui.icons import IconButton, IconLabel, IconName, make_icon
from ui.theme import COLORS


@dataclass(frozen=True)
class RailSection:
    """Section représentée par un bouton du rail (``label_key`` : clé i18n du libellé)."""

    id: str
    label_key: str
    icon: IconName

    @property
    def label(self) -> str:
        """Libellé dans la langue courante."""
        return translate(self.label_key)


# Sections exposées par le rail. L'ordre reflète la hiérarchie visuelle
# d'un monteur (médias d'abord, puis édition, puis enrichissement).
DEFAULT_SECTIONS: tuple[RailSection, ...] = (
    RailSection("media", "rail.media", IconName.MEDIA),
    RailSection("sequences", "rail.sequences", IconName.FILM),
    RailSection("edit", "rail.edit", IconName.SCISSORS),
    RailSection("effects", "rail.effects", IconName.EFFECTS),
    RailSection("color", "rail.color", IconName.COLOR),
    RailSection("text", "rail.text", IconName.TEXT),
    RailSection("transitions", "rail.transitions", IconName.TRANSITIONS),
    RailSection("audio", "rail.audio", IconName.AUDIO),
    RailSection("graphics", "rail.graphics", IconName.SUBTITLE),
    RailSection("templates", "rail.templates", IconName.PROJECT),
)


# Niveau d'usage minimal de chaque section (ADR-0006, lot 4) : médias, montage, texte, audio et graphiques sont Simple ;
# effets, transitions, couleur, séquences et modèles sont Studio. Une section cachée reste atteignable (menu Fenêtre,
# raccourcis, niveau supérieur) ; un niveau ne fait que montrer ou cacher son bouton.
SECTION_LEVELS: dict[str, UsageLevel] = {
    "effects": UsageLevel.STUDIO, "transitions": UsageLevel.STUDIO, "color": UsageLevel.STUDIO,
    "sequences": UsageLevel.STUDIO, "templates": UsageLevel.STUDIO,
}


RAIL_BUTTON_WIDTH = Sizes.side_rail_width - 2 * Spacing.sm
RAIL_BUTTON_HEIGHT = 54
RAIL_FONT_PX = 10
RAIL_MAX_WIDTH = 120
RAIL_TEXT_MARGIN = 12      # bord (2 × 1 px), marges internes (2 × 2 px) et reste d'arrondi de la police, des deux côtés du nom réunis
RAIL_MIN_FONT_PX = 7
RAIL_ICON_PX = 20


class SideRail(QWidget):
    """Colonne verticale d'icônes (rail principal)."""

    section_changed = Signal(str)

    def __init__(
        self,
        sections: tuple[RailSection, ...] = DEFAULT_SECTIONS,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._sections = sections
        self._buttons: dict[str, IconButton] = {}
        self._icons = {section.id: section.icon for section in sections}
        self._active: str | None = None
        self._font_px = RAIL_FONT_PX
        self._busy = False
        self._rail_width = Sizes.side_rail_width
        # Les événements de police et de style (des centaines au démarrage) sont regroupés en un seul recalcul.
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(0)
        self._fit_timer.timeout.connect(self.fit_to_fonts)
        self._usage_level = UsageLevel.ADVANCED      # sans fenêtre principale : tout est montré

        self.setObjectName("side_rail")
        self.setFixedWidth(Sizes.side_rail_width)
        self.setStyleSheet(
            f"QWidget#side_rail {{ background: {COLORS['panel']};"
            f" border-right: 1px solid {COLORS['border']}; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.sm, Spacing.sm, Spacing.sm, Spacing.sm)
        layout.setSpacing(2)

        for index, section in enumerate(sections):
            # Petit séparateur invisible entre les groupes de travail
            # (médias/édition et enrichissement) — on s'appuie sur l'ordre
            # pour créer deux blocs visuellement séparés.
            if index == 4:
                layout.addSpacing(Spacing.md)

            button = IconButton(
                icon=section.icon,
                text=section.label,
                tooltip=section.label,
                size=RAIL_ICON_PX + 4,      # ``IconButton`` en tire sa taille d'icône interne (taille - 4) : une seule source de vérité, ``setIcon`` ne la perd plus
            )
            button.setIcon(make_icon(
                section.icon,
                size=RAIL_ICON_PX,
                color=QColor(COLORS["muted_strong"]),
            ))
            button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)      # le nom est TOUJOURS visible
            button.setFixedSize(RAIL_BUTTON_WIDTH, RAIL_BUTTON_HEIGHT)
            button.installEventFilter(self)
            button.setCheckable(True)
            button.setObjectName("railButton")
            button.setToolTip(section.label)
            button.setAccessibleName(section.label)
            button.clicked.connect(
                lambda _checked=False, sid=section.id: self._on_clicked(sid)
            )
            self._buttons[section.id] = button
            layout.addWidget(button, 0, Qt.AlignHCenter)

        layout.addStretch(1)

        # Pied de rail : un pictogramme sobre pour rappeler l'identité.
        footer = IconLabel(IconName.INFO, size=Iconography.sm)
        footer.set_color("muted")
        footer.setToolTip("Kut‑Studio")
        layout.addWidget(footer, 0, Qt.AlignHCenter)

        if sections:
            self.set_active(sections[0].id)
        self.fit_to_fonts()

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def set_active(self, section_id: str) -> None:
        """Marque une section comme active (sans émettre de signal)."""
        if section_id == self._active:
            return
        for sid, button in self._buttons.items():
            button.setChecked(sid == section_id)
        self._active = section_id
        self._refresh_styles()

    @property
    def usage_level(self) -> UsageLevel:
        return self._usage_level

    def set_usage_level(self, level: object) -> None:
        """Montre ou cache les sections selon le niveau d'usage (visibilité seulement : ni ``setEnabled`` ni reparentage)."""
        new = coerce_usage_level(level)
        if new is None:
            return
        self._usage_level = new
        for sid, button in self._buttons.items():
            button.setVisible(new.at_least(SECTION_LEVELS.get(sid, UsageLevel.SIMPLE)))
        self.fit_to_fonts()

    def active(self) -> str | None:
        """Section active courante."""
        return self._active

    def retranslate(self) -> None:
        """Libellés, infobulles et noms accessibles dans la langue courante."""
        for section in self._sections:
            button = self._buttons[section.id]
            button.setText(section.label)
            button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)      # ``IconButton.setText`` le repasse en « texte à côté de l'icône »
            button.setFixedSize(self._rail_width - 2 * Spacing.sm, RAIL_BUTTON_HEIGHT)  # … et libère la taille fixe (largeur minimale de 96 px)
            button.setToolTip(section.label)
            button.setAccessibleName(section.label)
        self._font_px = RAIL_FONT_PX
        self._refresh_styles()
        self.fit_to_fonts()

    def rail_width(self) -> int:
        """Largeur courante du rail (celle du plus long nom de la langue et de la police courantes)."""
        return self._rail_width

    def fit_to_fonts(self) -> None:
        """Largeur du rail = celle du plus long nom, à la police réelle des boutons (plancher ``Sizes.side_rail_width``, plafond
        ``RAIL_MAX_WIDTH``). Si même au plafond un nom ne tient pas, la police du rail baisse d'un cran : un nom n'est jamais élidé.

        Rappelée au changement de langue, de niveau, de police et de feuille de style (les polices de repli de Windows et de Linux sont
        plus larges que celles de macOS)."""
        if self._busy:
            return
        self._busy = True
        try:
            width = self._rail_width
            for _ in range(16):
                for button in self._buttons.values():
                    button.ensurePolished()
                widest = max((QFontMetrics(b.font()).horizontalAdvance(b.text()) for b in self._buttons.values()), default=0)
                width = max(Sizes.side_rail_width, widest + RAIL_TEXT_MARGIN + 2 * Spacing.sm)
                if width <= RAIL_MAX_WIDTH or self._font_px <= RAIL_MIN_FONT_PX:
                    break
                current = next(iter(self._buttons.values())).font().pixelSize()
                self._font_px = max(RAIL_MIN_FONT_PX, (current if current > 0 else self._font_px) - 1)
                self._refresh_styles()
            width = min(width, RAIL_MAX_WIDTH) if self._font_px > RAIL_MIN_FONT_PX else width
            self._rail_width = width
            self.setFixedWidth(width)
            for button in self._buttons.values():
                button.setFixedSize(width - 2 * Spacing.sm, RAIL_BUTTON_HEIGHT)
        finally:
            self._busy = False

    def _schedule_fit(self) -> None:
        if not self._busy and not self._fit_timer.isActive():
            self._fit_timer.start()

    def changeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange, QEvent.ApplicationFontChange):
            self._schedule_fit()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - API Qt
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._schedule_fit()          # une feuille de style de bouton réécrite (taille de police) change la largeur du plus long nom
        return False

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().showEvent(event)
        self.fit_to_fonts()

    def refresh_theme(self) -> None:
        """Après un changement de thème : les icônes du rail portent une couleur imposée, qu'il faut recalculer."""
        self._refresh_styles()

    # ------------------------------------------------------------------
    # Slots internes
    # ------------------------------------------------------------------

    def _on_clicked(self, section_id: str) -> None:
        # Le bouton reste visuellement actif jusqu'à ce que le panneau
        # courant change ; on met simplement à jour la cible émettrice.
        if section_id == self._active:
            # Re-clic sur la section déjà active : on réémet pour rester
            # découvrable, mais sans changer l'état visuel.
            self.section_changed.emit(section_id)
            return
        self._active = section_id
        for sid, button in self._buttons.items():
            button.setChecked(sid == section_id)
        self._refresh_styles()
        self.section_changed.emit(section_id)

    def _refresh_styles(self) -> None:
        """Met à jour les couleurs des boutons selon l'état actif."""
        accent = COLORS["accent"]
        accent_dark = COLORS["accent_dark"]
        text = COLORS["text"]
        surface_hover = COLORS["surface_hover"]
        for sid, button in self._buttons.items():
            if sid == self._active:
                button.setIcon(make_icon(
                    self._icons[sid], size=RAIL_ICON_PX,
                    color=QColor(accent),
                ))
                button.setStyleSheet(
                    f"QToolButton {{ background: {accent_dark};"
                    f" color: {accent}; border: 1px solid {accent};"
                    f" border-radius: 8px; padding: 2px; font-size: {self._font_px}px; font-weight: 600; }}"
                )
            else:
                button.setIcon(make_icon(
                    self._icons[sid], size=RAIL_ICON_PX,
                    color=QColor(COLORS["muted_strong"]),
                ))
                button.setStyleSheet(
                    f"QToolButton {{ background: transparent;"
                    f" color: {COLORS['muted_strong']};"
                    f" border: 1px solid transparent; border-radius: 8px;"
                    f" padding: 2px; font-size: {self._font_px}px; font-weight: 600; }}"
                    f"QToolButton:hover {{ background: {surface_hover};"
                    f" color: {text}; }}"
                )


__all__ = ["SideRail", "RailSection", "DEFAULT_SECTIONS"]
