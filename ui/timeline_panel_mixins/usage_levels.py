"""Niveau d'usage de la timeline (ADR-0006, lot 4) : outils de la barre et entrées des menus contextuels.

``TimelinePanel.usage_level`` vaut Avancé sans fenêtre principale (le comportement d'avant le lot). La fenêtre l'impose à la
création et le suit à chaud par ``set_usage_level``. Un niveau ne fait que **montrer ou cacher** : jamais ``setEnabled`` (les
verrous de piste et de clip s'appliquent à tous les niveaux), jamais de reparentage, et les raccourcis des outils (B, R, Y, U,
N, M, enregistrement) restent actifs, car ils agissent par les boutons, cachés ou non.

Barre : sélection, lame, aimantation, ripple et marqueur sont Simple ; roll, slip, slide et enregistrement sont Studio.
Un outil caché ne reste pas actif (retour à la sélection) ; un enregistrement en cours garde son bouton visible pour pouvoir
être arrêté.
"""

from __future__ import annotations

from core.usage_level import UsageLevel, coerce_usage_level

STUDIO_TOOLS = ("roll", "slip", "slide")


class UsageLevelsTimelineMixin:
    """Mixin de ``TimelinePanel`` : niveau d'usage de la barre d'outils et des menus contextuels."""

    _usage_level: UsageLevel = UsageLevel.ADVANCED

    @property
    def usage_level(self) -> UsageLevel:
        return self._usage_level

    def level_at_least(self, minimum: UsageLevel) -> bool:
        """Vrai si le niveau courant atteint ``minimum`` (le niveau minimal d'une entrée de menu)."""
        return self._usage_level.at_least(minimum)

    def set_usage_level(self, level: object) -> None:
        """Applique le niveau : visibilité des outils Studio seulement (valeur invalide : niveau inchangé)."""
        new = coerce_usage_level(level)
        if new is None:
            return
        self._usage_level = new
        self._apply_toolbar_level()

    def _apply_toolbar_level(self) -> None:
        studio = self._usage_level.at_least(UsageLevel.STUDIO)
        for name in STUDIO_TOOLS:
            button = getattr(self, f"{name}_button", None)
            if button is not None:
                button.setVisible(studio)
        if not studio and getattr(self, "tool", "select") in STUDIO_TOOLS:
            self.set_tool("select")                       # un outil caché ne reste pas actif
        record = getattr(self, "record_button", None)
        if record is not None:
            record.setVisible(studio or record.isChecked())

    def _on_record_toggled_for_level(self, _checked: bool = False) -> None:
        """Un enregistrement qui se termine cache de nouveau son bouton aux niveaux sans enregistrement."""
        self._apply_toolbar_level()


__all__ = ["STUDIO_TOOLS", "UsageLevelsTimelineMixin"]
