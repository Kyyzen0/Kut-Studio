"""Navigation au clavier : politiques de focus et bouton par défaut.

Deux règles, parce que deux contextes s'opposent :

* **Fenêtre principale et inspecteur** : un bouton qui prend le focus au clic *vole* les touches de lecture (Espace
  déclencherait le bouton cliqué au lieu de lire). Les contrôles qui ne servent pas à saisir du texte (boutons, listes
  déroulantes non éditables, curseurs, cases) sont donc en ``Qt.TabFocus`` : on les atteint avec Tab, mais un clic ne
  leur donne jamais le focus et ne change rien aux raccourcis. Seuls les champs de saisie gardent le focus au clic
  (``ShortcutManager`` les épargne déjà : aucune touche de commande ne se déclenche pendant une saisie).
* **Dialogues** : tout est atteignable et chaque dialogue a **un seul** bouton par défaut (celui de validation ou de
  fermeture), sinon Entrée dans un champ déclenche une action sans rapport.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractScrollArea,
    QAbstractSpinBox,
    QComboBox,
    QDialog,
    QKeySequenceEdit,
    QLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QTextEdit,
    QWidget,
)

_LEAF_CONTROLS = (
    QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit, QSlider, QTextEdit, QPlainTextEdit,
    QAbstractItemView, QKeySequenceEdit,
)
"""Contrôles qui sont des feuilles : on ne descend pas dans leurs composants internes."""


def tab_only(*widgets: QWidget) -> None:
    """Rend les widgets atteignables avec Tab, sans qu'un clic leur donne le focus."""
    for widget in widgets:
        widget.setFocusPolicy(Qt.TabFocus)


def tab_only_controls(root: QWidget) -> None:
    """Applique :func:`tab_only` à tous les contrôles sans saisie de texte sous ``root``.

    Boutons (cases et boutons-icônes compris), listes déroulantes non éditables et curseurs. Les champs de saisie
    (texte, nombres, listes déroulantes éditables) gardent leur politique : ils doivent recevoir le focus au clic.
    """
    for widget in root.findChildren(QWidget):
        if isinstance(widget, (QAbstractButton, QSlider)):
            widget.setFocusPolicy(Qt.TabFocus)
        elif isinstance(widget, QComboBox) and not widget.isEditable():
            widget.setFocusPolicy(Qt.TabFocus)


def let_tab_leave(*editors: QTextEdit | QPlainTextEdit) -> None:
    """Fait sortir Tab d'un éditeur multiligne au lieu d'y insérer une tabulation (focus piégé)."""
    for editor in editors:
        editor.setTabChangesFocus(True)


def let_tab_leave_in(root: QWidget) -> None:
    """Applique :func:`let_tab_leave` à tous les éditeurs multilignes sous ``root``."""
    let_tab_leave(*root.findChildren(QTextEdit), *root.findChildren(QPlainTextEdit))


def _focusable_in_layout_order(widget: QWidget, found: list[QWidget]) -> None:
    if isinstance(widget, _LEAF_CONTROLS):
        if widget.focusPolicy() & Qt.TabFocus:
            found.append(widget)
        return
    if isinstance(widget, QScrollArea):
        if widget.widget() is not None:
            _focusable_in_layout_order(widget.widget(), found)
        return
    if isinstance(widget, QAbstractScrollArea):
        return
    # L'en-tête d'une section repliable est un arrêt de Tab qui précède son contenu (un conteneur, pas une feuille).
    if getattr(widget, "is_section_header", False) and widget.focusPolicy() & Qt.TabFocus:
        found.append(widget)
    layout = widget.layout()
    if layout is not None:
        _walk_layout(layout, found)
    else:
        for child in widget.children():
            if isinstance(child, QWidget) and not child.isWindow():
                _focusable_in_layout_order(child, found)


def _walk_layout(layout: QLayout, found: list[QWidget]) -> None:
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item is None:
            continue
        if item.widget() is not None:
            _focusable_in_layout_order(item.widget(), found)
        elif item.layout() is not None:
            _walk_layout(item.layout(), found)


def follow_layout_order(layout: QLayout) -> None:
    """Fait suivre à Tab l'ordre d'apparition des contrôles dans ``layout``.

    Par défaut Tab suit l'ordre de *création* des widgets : un groupe inséré plus haut après coup (calque graphique
    avant « Mouvement »), ou des réglages ajoutés dynamiquement, seraient visités dans le désordre. À rappeler
    quand des widgets sont ajoutés après la construction.
    """
    widgets: list[QWidget] = []
    _walk_layout(layout, widgets)
    for first, second in zip(widgets, widgets[1:]):
        QWidget.setTabOrder(first, second)


def set_single_default(dialog: QDialog, primary: QPushButton | None) -> None:
    """Fait de ``primary`` le seul bouton par défaut du dialogue (Entrée), ``None`` : aucun.

    Dans un ``QDialog`` tout ``QPushButton`` est « auto-défaut » : le premier créé devient le bouton par défaut si
    aucun ne l'est explicitement (« Retirer » un raccourci, « Choisir une couleur… »). À rappeler après avoir ajouté
    des boutons dynamiquement.
    """
    for button in dialog.findChildren(QPushButton):
        is_primary = button is primary
        button.setAutoDefault(is_primary)
        button.setDefault(is_primary)


__all__ = [
    "follow_layout_order",
    "let_tab_leave",
    "let_tab_leave_in",
    "set_single_default",
    "tab_only",
    "tab_only_controls",
]
