"""Groupe Audio : gain, panoramique et fondus du clip."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from core.project_model import MAX_GAIN_DB, MIN_GAIN_DB
from ui.design_system import Sizes, Spacing
from ui.icons import IconButton, IconName
from ui.theme import label_style


class AudioMixin:
    """Mixin de ``PropertiesPanel`` : groupe Audio : gain, panoramique et fondus du clip."""

    def _build_audio_group(self) -> QGroupBox:
        """Groupe de mixage du clip sélectionné.

        N'affiche que des réglages **réels** : gain, panoramique, fondus.
        Le panneau émet des intentions ; ``MainWindow`` les applique au
        modèle, refuse les pistes verrouillées et enregistre l'historique.
        """
        from ui.i18n import translate

        group = QGroupBox(translate("audio.gain"))
        group.setObjectName("audioGroup")
        group.setStyleSheet(self.group_style())
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(
            Spacing.md, Spacing.md, Spacing.md, Spacing.md
        )
        group_layout.setSpacing(Spacing.xs)

        self.audio_gain_slider, self._audio_gain_row, self.audio_gain_value = (
            self.make_slider(int(MIN_GAIN_DB), int(MAX_GAIN_DB), 0, suffix=" dB")
        )
        self.audio_gain_slider.valueChanged.connect(
            lambda value: self.audio_gain_changed.emit(float(value))
        )
        group_layout.addWidget(self._audio_gain_row)

        self.audio_pan_slider, self._audio_pan_row, self.audio_pan_value = (
            self.make_slider(-100, 100, 0)
        )
        self.audio_pan_slider.valueChanged.connect(
            lambda value: self.audio_pan_changed.emit(value / 100.0)
        )
        group_layout.addWidget(self._audio_pan_row)

        # Fondus, en secondes, pas de 0,1 s.
        self._fade_spins: dict[str, QDoubleSpinBox] = {}
        for which in ("in", "out"):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(Spacing.sm)
            label = QLabel(
                translate("audio.fade_in")
                if which == "in"
                else translate("audio.fade_out")
            )
            label.setStyleSheet(label_style(11, "muted", 600))
            label.setMinimumWidth(84)
            row_layout.addWidget(label)
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 600.0)
            spin.setDecimals(2)
            spin.setSingleStep(0.1)
            spin.setSuffix(" s")
            spin.setMinimumWidth(80)
            spin.valueChanged.connect(
                lambda value, w=which: self.audio_fade_changed.emit(w, float(value))
            )
            self._fade_spins[which] = spin
            row_layout.addWidget(spin, 1)
            group_layout.addWidget(row)

        self.reset_fades_button = IconButton(
            icon=IconName.PANEL_RESET,
            tooltip=translate("audio.reset_fades"),
            size=Sizes.icon_button,
        )
        self.reset_fades_button.setText(f"  {translate('audio.reset_fades')}")
        self.reset_fades_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.reset_fades_button.setMinimumHeight(Sizes.button_sm)
        self.reset_fades_button.clicked.connect(
            lambda: self.audio_fades_reset.emit()
        )
        group_layout.addWidget(self.reset_fades_button)
        return group

    def set_audio_clip(self, model, *, locked: bool = False) -> None:
        """Affiche les réglages audio d'un clip, sans émettre de signal.

        Args:
            model: le :class:`~core.project_model.Clip` sélectionné, ou
                ``None`` pour masquer le groupe.
            locked: la piste est verrouillée : tout est désactivé.
        """
        from ui.i18n import translate

        if model is None:
            self.audio_group.setEnabled(False)
            return
        enabled = not locked
        self.audio_group.setEnabled(enabled)
        for widget in (
            self.audio_gain_slider,
            self.audio_pan_slider,
            self.reset_fades_button,
            *self._fade_spins.values(),
        ):
            widget.setEnabled(enabled)
        for widget in (self.audio_gain_slider, self.audio_pan_slider):
            widget.blockSignals(True)
        self.audio_gain_slider.setValue(int(round(float(model.gain_db))))
        self.audio_pan_slider.setValue(int(round(float(model.pan) * 100)))
        for widget in (self.audio_gain_slider, self.audio_pan_slider):
            widget.blockSignals(False)
        self.audio_gain_value.setText(f"{model.gain_db:+.1f} dB")
        self.audio_pan_value.setText(
            translate("mixer.pan_center")
            if abs(model.pan) < 0.02
            else f"{model.pan * 100:+.0f}"
        )
        for which, spin in self._fade_spins.items():
            spin.blockSignals(True)
            spin.setValue(float(getattr(model, f"fade_{which}", 0.0)))
            spin.blockSignals(False)
