"""Rail vertical d'icônes de Kut‑Studio.

Rail fin (~64 px) inspiré de DaVinci Resolve et Final Cut Pro. Il
donne accès aux sections globales (Médias, Éditer, Effets, Texte,
Transitions, Audio, Graphiques, Modèles) sans alourdir la barre
supérieure. Chaque bouton est une simple icône, avec une infobulle au
survol et un état actif matérialisé par un fond vert foncé arrondi et
une icône turquoise.

Aucune logique métier ici : le rail expose un signal ``section_changed``
que la fenêtre principale traduit en bascule de panneau / d'onglet.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ui.design_system import Iconography, Sizes, Spacing
from ui.icons import IconButton, IconLabel, IconName, make_icon
from ui.theme import COLORS


@dataclass(frozen=True)
class RailSection:
    """Section représentée par un bouton du rail."""

    id: str
    label: str
    icon: IconName


# Sections exposées par le rail. L'ordre reflète la hiérarchie visuelle
# d'un monteur (médias d'abord, puis édition, puis enrichissement).
DEFAULT_SECTIONS: tuple[RailSection, ...] = (
    RailSection("media", "Médias", IconName.MEDIA),
    RailSection("edit", "Éditer", IconName.SCISSORS),
    RailSection("effects", "Effets", IconName.EFFECTS),
    RailSection("text", "Texte", IconName.TEXT),
    RailSection("transitions", "Transitions", IconName.TRANSITIONS),
    RailSection("audio", "Audio", IconName.AUDIO),
    RailSection("graphics", "Graphiques", IconName.SUBTITLE),
    RailSection("templates", "Modèles", IconName.PROJECT),
)


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
        self._active: str | None = None

        self.setObjectName("side_rail")
        self.setFixedWidth(Sizes.side_rail_width)
        self.setStyleSheet(
            f"QWidget#side_rail {{ background: {COLORS['panel']};"
            f" border-right: 1px solid {COLORS['border']}; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.sm, Spacing.md, Spacing.sm, Spacing.md)
        layout.setSpacing(Spacing.xs)

        for index, section in enumerate(sections):
            # Petit séparateur invisible entre les groupes de travail
            # (médias/édition et enrichissement) — on s'appuie sur l'ordre
            # pour créer deux blocs visuellement séparés.
            if index == 4:
                layout.addSpacing(Spacing.md)

            button = IconButton(
                icon=section.icon,
                tooltip=section.label,
                size=Sizes.icon_button + 2,
                square=True,
            )
            button.setIcon(make_icon(section.icon, size=Iconography.lg))
            button.setCheckable(True)
            button.setObjectName("railButton")
            button.setToolTip(section.label)
            button.clicked.connect(
                lambda _checked=False, sid=section.id: self._on_clicked(sid)
            )
            self._buttons[section.id] = button
            layout.addWidget(button, 0, Qt.AlignHCenter)

        layout.addStretch(1)

        # Pied de rail : un pictogramme sobre pour rappeler l'identité.
        footer = IconLabel(IconName.INFO, size=Iconography.sm)
        footer.set_color(QColor(COLORS["muted"]))
        footer.setToolTip("Kut‑Studio")
        layout.addWidget(footer, 0, Qt.AlignHCenter)

        if sections:
            self.set_active(sections[0].id)

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

    def active(self) -> str | None:
        """Section active courante."""
        return self._active

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
        muted = COLORS["muted"]
        surface_hover = COLORS["surface_hover"]
        for sid, button in self._buttons.items():
            if sid == self._active:
                button.setStyleSheet(
                    f"QToolButton {{ background: {accent_dark};"
                    f" color: {accent}; border: 1px solid {accent};"
                    f" border-radius: 8px; }}"
                )
            else:
                button.setStyleSheet(
                    f"QToolButton {{ background: transparent;"
                    f" color: {muted}; border: 1px solid transparent;"
                    f" border-radius: 8px; }}"
                    f"QToolButton:hover {{ background: {surface_hover};"
                    f" color: {text}; }}"
                )


__all__ = ["SideRail", "RailSection", "DEFAULT_SECTIONS"]
