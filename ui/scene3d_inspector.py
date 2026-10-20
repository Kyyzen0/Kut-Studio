"""Section « Scène 3D » de l'inspecteur d'un calque (lot B3) : nom du fichier ``.kut3d`` et bouton « Recharger ».

Lecture seule : l'édition de la scène (caméra, lumières, modèles) n'existe pas encore ; le fichier se modifie dehors puis se
recharge ici. Le widget n'écrit rien dans le modèle : il émet ``reload_requested(chemin)`` ; la fenêtre oublie ce que le modèle
a mémorisé du fichier (:func:`core.scene3d.reload_scene`) et redessine le moniteur.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFormLayout, QLabel, QPushButton, QWidget

from ui import i18n
from ui.design_system import Spacing
from ui.theme import label_style

MAX_NAME_CHARS = 120
"""Un nom de fichier démesuré est raccourci à l'affichage (le chemin complet reste dans l'infobulle)."""


class Scene3DSection(QWidget):
    """Nom du fichier de scène (lecture seule) et « Recharger »."""

    reload_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._path = ""
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(Spacing.xs)
        self.file_label = QLabel("--")
        self.file_label.setObjectName("scene3d_file")
        self.file_label.setWordWrap(True)
        self.file_label.setStyleSheet(label_style(11, "text", 600))
        form.addRow(i18n.translate("scene3d.inspector.file"), self.file_label)
        self.reload_button = QPushButton(i18n.translate("scene3d.inspector.reload"))
        self.reload_button.setObjectName("scene3d_reload")
        self.reload_button.setToolTip(i18n.translate("scene3d.inspector.reload.tooltip"))
        self.reload_button.clicked.connect(self._on_reload)
        form.addRow(self.reload_button)

    def set_path(self, path: str) -> None:
        """Affiche le fichier de la scène (son nom ; le chemin entier en infobulle)."""
        self._path = str(path or "")
        name = Path(self._path).name or "--"
        self.file_label.setText(name if len(name) <= MAX_NAME_CHARS else name[: MAX_NAME_CHARS - 1] + "…")
        self.file_label.setToolTip(self._path)

    def _on_reload(self) -> None:
        if self._path and self.isEnabled():                 # éditeur désactivé (calque ou piste verrouillé) : rien à faire
            self.reload_requested.emit(self._path)


SPEED_CONTROLS = (
    "speed_group", "speed_spinbox", "speed_0_25x_button", "speed_0_5x_button", "speed_1x_button", "speed_2x_button",
    "speed_4x_button", "reverse_button", "freeze_frame_button", "freeze_duration_spinbox", "reset_speed_button",
)
"""Contrôles « Vitesse et durée » que le modèle refuse pour une scène 3D (:func:`core.scene3d.ensure_fixed_speed`)."""


def set_fixed_speed_tooltips(panel: object, fixed: bool) -> None:
    """Pose l'infobulle « une scène 3D se joue à sa vitesse » sur les contrôles de vitesse (``fixed``), ou leur rend la leur.

    L'inspecteur grise ces contrôles lui-même (``enabled_tr`` de ``show_clip``) ; ici seulement l'explication. Les infobulles
    d'origine sont mémorisées à la première pose.
    """
    originals: dict[str, str] = panel.__dict__.setdefault("_speed_tooltips_original", {})
    for name in SPEED_CONTROLS:
        widget = getattr(panel, name, None)
        if widget is None:
            continue
        originals.setdefault(name, widget.toolTip())
        widget.setToolTip(i18n.translate("scene3d.speed.tooltip") if fixed else originals[name])


__all__ = ["Scene3DSection", "SPEED_CONTROLS", "set_fixed_speed_tooltips"]
