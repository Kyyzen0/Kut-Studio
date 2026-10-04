"""Rail vertical d'icônes de Kut‑Studio.

Rail compact inspiré de DaVinci Resolve et Final Cut Pro. Il
donne accès aux sections globales (Médias, Éditer, Effets, Texte,
Transitions, Audio, Graphiques, Modèles) sans alourdir la barre
supérieure. Les boutons sont icon-only ; leur nom reste disponible dans
une infobulle et comme nom accessible. L'état actif utilise un fond vert
foncé arrondi et une icône turquoise.

Aucune logique métier ici : le rail expose un signal ``section_changed``
que la fenêtre principale traduit en bascule de panneau / d'onglet.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QVBoxLayout, QWidget

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
                text=section.label,
                tooltip=section.label,
                size=Sizes.icon_button_lg,
            )
            button.setIcon(make_icon(
                section.icon,
                size=Iconography.lg,
                color=QColor(COLORS["muted_strong"]),
            ))
            button.setToolButtonStyle(Qt.ToolButtonIconOnly)
            button.setFixedSize(Sizes.icon_button_lg, Sizes.icon_button_lg)
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

    def retranslate(self) -> None:
        """Libellés, infobulles et noms accessibles dans la langue courante."""
        for section in self._sections:
            button = self._buttons[section.id]
            button.setText(section.label)
            button.setToolButtonStyle(Qt.ToolButtonIconOnly)      # ``IconButton.setText`` le repasse en « texte à côté de l'icône »
            button.setToolTip(section.label)
            button.setAccessibleName(section.label)

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
                    self._icons[sid], size=Iconography.lg,
                    color=QColor(accent),
                ))
                button.setStyleSheet(
                    f"QToolButton {{ background: {accent_dark};"
                    f" color: {accent}; border: 1px solid {accent};"
                    f" border-radius: 8px; padding: 0; }}"
                )
            else:
                button.setIcon(make_icon(
                    self._icons[sid], size=Iconography.lg,
                    color=QColor(COLORS["muted_strong"]),
                ))
                button.setStyleSheet(
                    f"QToolButton {{ background: transparent;"
                    f" color: {COLORS['muted_strong']};"
                    f" border: 1px solid transparent; border-radius: 8px;"
                    f" padding: 0; }}"
                    f"QToolButton:hover {{ background: {surface_hover};"
                    f" color: {text}; }}"
                )


__all__ = ["SideRail", "RailSection", "DEFAULT_SECTIONS"]
