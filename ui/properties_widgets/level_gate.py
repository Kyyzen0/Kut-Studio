"""Briques du niveau d'usage dans l'inspecteur (ADR-0006, étape 4, lot 3).

Un niveau ne fait **que** montrer ou cacher : il n'appelle jamais ``setEnabled`` (les verrous de piste et de calque restent
ceux de ``show_clip``) et ne touche ni aux valeurs ni aux signaux des contrôles.

* :class:`LevelGate` : conteneur créé **à la construction** autour d'un widget (jamais posé après coup à la place d'un widget
  déjà dans un layout : Qt ne le supporte pas sans fuite d'éléments de layout). Le niveau n'écrit que la visibilité du
  conteneur ; la logique propre du widget (clip vidéo ou calque, piste audio…) continue d'écrire la sienne, et le widget
  n'est visible que si les deux le sont. C'est la règle P3 : jamais un ``setVisible`` du niveau sur un widget que d'autres
  écrivains pilotent.
* :class:`AdvancedFold` : l'en-tête repliable « Avancé » d'un groupe, qui rouvre ce que le niveau courant ne montre pas.
* :func:`limit_choices` : cache des choix d'une liste déroulante sans la reconstruire (la valeur choisie reste affichée).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFormLayout, QListView, QToolButton, QVBoxLayout, QWidget

from core.usage_level import UsageLevel
from ui.adaptive_layout import allow_shrinking


class LevelGate(QWidget):
    """Conteneur transparent d'un seul widget, dont le niveau pilote la visibilité."""

    def __init__(self, form: QFormLayout | None = None) -> None:
        super().__init__()
        self.setObjectName("level_gate")
        self.form = form
        self.child: QWidget | None = None
        self.minimum: UsageLevel | None = None
        self.inverse_of: LevelGate | None = None    # affiché tant que cet autre conteneur ne l'est pas
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)

    def hold(self, widget: QWidget, minimum: UsageLevel | None = None) -> "LevelGate":
        """Contient ``widget`` (neuf : il n'est encore dans aucun layout) ; ``minimum`` : niveau minimal où il est affiché directement."""
        self.layout().addWidget(widget)
        self.child = widget
        self.minimum = minimum
        return self

    def set_shown(self, shown: bool) -> None:
        """Montre ou cache le conteneur ; dans un ``QFormLayout``, la ligne entière (libellé compris)."""
        if self.form is not None:
            self.form.setRowVisible(self, bool(shown))
        else:
            self.setVisible(bool(shown))

    def is_shown(self) -> bool:
        return not self.isHidden()


def gated(widget: QWidget, minimum: UsageLevel, form: QFormLayout | None = None) -> LevelGate:
    """Un :class:`LevelGate` neuf qui contient ``widget`` ; à poser dans le layout à la place du widget."""
    return LevelGate(form).hold(widget, minimum)


class AdvancedFold(QToolButton):
    """En-tête « Avancé » d'un groupe : replié, il laisse cachés les réglages du niveau supérieur ; ouvert, il les montre."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("advanced_fold")
        self.setCheckable(True)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setArrowType(Qt.RightArrow)
        self.setStyleSheet("QToolButton { border: none; font-weight: 700; }")
        self.toggled.connect(lambda checked: self.setArrowType(Qt.DownArrow if checked else Qt.RightArrow))
        allow_shrinking(self, 120)

    def is_open(self) -> bool:
        return self.isChecked()


def limit_choices(combo: QComboBox, hidden_data: set[object], *, move_to_visible: bool = False) -> None:
    """Cache dans la liste les choix dont la donnée est dans ``hidden_data`` ; ceux-ci restent dans le modèle.

    Le choix courant reste toujours listé (un mode déjà choisi dans un projet ne disparaît pas). ``move_to_visible``
    ramène sans signal le choix courant sur le premier choix visible s'il est caché (liste « ajouter » sans valeur
    portée par le modèle). Sans liste de type ``QListView`` (style natif), la liste est laissée telle quelle.
    """
    view = combo.view()
    if not isinstance(view, QListView):
        return
    if move_to_visible and combo.itemData(combo.currentIndex()) in hidden_data:
        for index in range(combo.count()):
            if combo.itemData(index) not in hidden_data:
                blocked = combo.blockSignals(True)
                combo.setCurrentIndex(index)
                combo.blockSignals(blocked)
                break
    current = combo.currentIndex()
    for index in range(combo.count()):
        view.setRowHidden(index, combo.itemData(index) in hidden_data and index != current)


__all__ = ["AdvancedFold", "LevelGate", "gated", "limit_choices"]
