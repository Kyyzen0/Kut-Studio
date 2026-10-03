"""Rack d'effets audio du clip sélectionné."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.audio_effects_model import (
    AudioEffect,
    AudioEffectType,
    parameter_specs as audio_parameter_specs,
)
from ui.adaptive_layout import FlowLayout, make_shrinkable
from ui.design_system import Sizes, Spacing
from ui.theme import COLORS, label_style


class AudioEffectsMixin:
    """Mixin de ``PropertiesPanel`` : rack d'effets audio du clip sélectionné."""

    # ------------------------------------------------------------------
    # Effets audio non destructifs (tâche 27)
    # ------------------------------------------------------------------

    def _build_audio_effects_group(self) -> QGroupBox:
        """Groupe « Effets audio » : rack avec liste et paramètres.

        Le rack est strictement aligné sur la structure du rack visuel :
        même nomenclature de contrôles, mêmes signaux ``*_requested``
        (les noms sont préfixés ``audio_effect_*`` pour rester
        distincts en log et permettre un routage simple côté MainWindow).
        """
        from ui.i18n import translate as _tr

        group = QGroupBox(_tr("audio_effects.section"))
        group.setObjectName("audioEffectsGroup")
        group.setStyleSheet(self.group_style())
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(
            Spacing.md, Spacing.md, Spacing.md, Spacing.md
        )
        group_layout.setSpacing(Spacing.xs)

        # Astuce si aucun média sélectionné.
        self.audio_effects_hint = QLabel(_tr("audio_effects.no_clip"))
        self.audio_effects_hint.setWordWrap(True)
        self.audio_effects_hint.setStyleSheet(label_style(11, "muted", 500))
        group_layout.addWidget(self.audio_effects_hint)

        # Ligne d'ajout : combo + bouton.
        self.audio_effect_add_row = QWidget()
        add_layout = QHBoxLayout(self.audio_effect_add_row)
        add_layout.setContentsMargins(0, 0, 0, 0)
        add_layout.setSpacing(Spacing.xs)
        self.audio_effect_type_combo = make_shrinkable(QComboBox())
        self.audio_effect_type_combo.setObjectName("audioEffectType")
        self.audio_effect_type_combo.setFocusPolicy(Qt.NoFocus)
        self.audio_effect_type_combo.setStyleSheet(
            f"QComboBox#audioEffectType {{ background: {COLORS['surface']};"
            f" color: {COLORS['text']}; border: 1px solid {COLORS['border']};"
            f" border-radius: 6px; padding: 4px 6px; font-size: 11px; }}"
            f"QComboBox#audioEffectType:focus {{ border-color: {COLORS['accent']}; }}"
        )
        self.audio_effect_add_button = self._make_audio_effect_button(
            _tr("audio_effects.add"), self._on_audio_effect_add
        )
        add_layout.addWidget(self.audio_effect_type_combo, 1)
        add_layout.addWidget(self.audio_effect_add_button)
        group_layout.addWidget(self.audio_effect_add_row)

        # Liste des effets.
        self.audio_effects_list = QListWidget()
        self.audio_effects_list.setObjectName("audioEffectsList")
        self.audio_effects_list.setFixedHeight(104)
        self.audio_effects_list.setStyleSheet(
            f"QListWidget {{ background: {COLORS['panel_alt']};"
            f" color: {COLORS['text']};"
            f" border: 1px solid {COLORS['border']}; border-radius: 6px; }}"
            f"QListWidget::item {{ padding: 4px 6px; }}"
            f"QListWidget::item:selected {{ background: {COLORS['accent_dark']};"
            f" color: {COLORS['accent']}; }}"
        )
        self.audio_effects_list.currentRowChanged.connect(
            self._on_audio_effect_selection_changed
        )
        group_layout.addWidget(self.audio_effects_list)

        # Placeholder "aucun effet audio".
        self.audio_effects_empty_label = QLabel(_tr("audio_effects.empty"))
        self.audio_effects_empty_label.setWordWrap(True)  # une phrase : elle ne doit pas fixer la largeur de l'inspecteur
        self.audio_effects_empty_label.setStyleSheet(
            label_style(11, "muted", 500)
        )
        group_layout.addWidget(self.audio_effects_empty_label)

        # Boutons d'action.
        self.audio_effect_buttons_row = QWidget()
        buttons_layout = FlowLayout(self.audio_effect_buttons_row, spacing=Spacing.xs)
        self.audio_effect_toggle_button = self._make_audio_effect_button(
            _tr("audio_effects.disable"), self._on_audio_effect_toggle
        )
        self.audio_effect_remove_button = self._make_audio_effect_button(
            _tr("audio_effects.remove"), self._on_audio_effect_remove
        )
        self.audio_effect_up_button = self._make_audio_effect_button(
            _tr("audio_effects.move_up"), lambda: self._emit_audio_effect_move(-1)
        )
        self.audio_effect_down_button = self._make_audio_effect_button(
            _tr("audio_effects.move_down"), lambda: self._emit_audio_effect_move(1)
        )
        for button in (
            self.audio_effect_toggle_button,
            self.audio_effect_remove_button,
            self.audio_effect_up_button,
            self.audio_effect_down_button,
        ):
            buttons_layout.addWidget(button)
        group_layout.addWidget(self.audio_effect_buttons_row)

        # Zone de paramètres.
        self.audio_effect_parameters_container = QWidget()
        self.audio_effect_parameters_layout = QVBoxLayout(
            self.audio_effect_parameters_container
        )
        self.audio_effect_parameters_layout.setContentsMargins(0, 0, 0, 0)
        self.audio_effect_parameters_layout.setSpacing(Spacing.xs)
        group_layout.addWidget(self.audio_effect_parameters_container)

        group.setEnabled(False)
        return group

    def _make_audio_effect_button(self, text: str, handler) -> QPushButton:
        """Bouton d'action réutilisé dans le rack d'effets audio."""
        button = QPushButton(text)
        button.setObjectName("audioEffectAction")
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.NoFocus)
        button.setMinimumHeight(Sizes.button_sm)
        button.setStyleSheet(
            f"QPushButton#audioEffectAction {{ background: {COLORS['surface']};"
            f" color: {COLORS['text']}; border: 1px solid {COLORS['border']};"
            f" border-radius: 6px; padding: 4px 6px; font-size: 11px; }}"
            f"QPushButton#audioEffectAction:hover {{ background: {COLORS['surface_hover']}; }}"
            f"QPushButton#audioEffectAction:focus {{ border: 1px solid {COLORS['accent']}; }}"
            f"QPushButton#audioEffectAction:disabled {{ color: {COLORS['muted']}; }}"
        )
        button.clicked.connect(lambda _checked=False: handler())
        return button

    def update_audio_effects_from_clip(self, effects, track_type=None) -> None:
        """Synchronise le rack d'effets audio avec l'état du clip.

        Args:
            effects: liste des :class:`AudioEffect` du clip sélectionné.
            track_type: type de piste. Le rack reste éditable pour les
                pistes ``video`` (avec ``has_audio=True``) et ``audio`` ;
                toute autre valeur (notamment ``subtitle``) désactive
                l'édition.
        """
        from ui.i18n import translate as _tr

        editable = track_type in ("video", "audio")
        self._current_audio_effects = list(effects or [])
        if not editable:
            self._selected_audio_effect_id = None
            self._populate_audio_effects_list()
            self._clear_audio_effect_parameters()
            self.audio_effects_hint.setText(_tr("audio_effects.no_clip"))
            self.audio_effects_hint.setVisible(True)
            self.audio_effect_add_row.setVisible(False)
            self.audio_effects_list.setVisible(False)
            self.audio_effect_buttons_row.setVisible(False)
            self.audio_effect_parameters_container.setVisible(False)
            self.audio_effects_empty_label.setVisible(False)
            self.audio_effects_group.setEnabled(False)
            return

        self.audio_effects_hint.setVisible(False)
        self.audio_effect_add_row.setVisible(True)
        self.audio_effects_list.setVisible(True)
        self.audio_effect_buttons_row.setVisible(True)
        self.audio_effect_parameters_container.setVisible(True)
        self.audio_effects_group.setEnabled(True)
        self._populate_audio_effects_list()
        self._refresh_audio_effect_catalog()

    def _refresh_audio_effect_catalog(self) -> None:
        """Propose tous les types d'effets audio dans le combo d'ajout."""
        from ui.i18n import translate as _tr

        current = self.audio_effect_type_combo.currentData()
        self.audio_effect_type_combo.blockSignals(True)
        self.audio_effect_type_combo.clear()
        for audio_type in AudioEffectType:
            self.audio_effect_type_combo.addItem(
                _tr(f"audio_effects.preset.{audio_type.value}.name"),
                audio_type.value,
            )
        if current:
            index = self.audio_effect_type_combo.findData(current)
            if index >= 0:
                self.audio_effect_type_combo.setCurrentIndex(index)
        self.audio_effect_type_combo.blockSignals(False)
        self.audio_effect_add_button.setEnabled(
            self.audio_effect_type_combo.count() > 0
        )

    def _populate_audio_effects_list(self) -> None:
        """Remplit la liste des effets audio et restaure la sélection."""
        from ui.i18n import translate as _tr

        self.audio_effects_list.blockSignals(True)
        self.audio_effects_list.clear()
        for effect in self._current_audio_effects:
            name = _tr(f"audio_effects.preset.{effect.type.value}.name")
            if not effect.enabled:
                name = f"{name} · {_tr('effects.disabled_suffix')}"
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, effect.id)
            self.audio_effects_list.addItem(item)
        self.audio_effects_list.blockSignals(False)

        ids = [effect.id for effect in self._current_audio_effects]
        if self._selected_audio_effect_id not in ids:
            self._selected_audio_effect_id = ids[0] if ids else None
        self.audio_effects_empty_label.setVisible(not ids)
        row = (
            ids.index(self._selected_audio_effect_id)
            if self._selected_audio_effect_id else -1
        )
        self.audio_effects_list.blockSignals(True)
        self.audio_effects_list.setCurrentRow(row)
        self.audio_effects_list.blockSignals(False)
        self._refresh_audio_effect_controls()

    def _selected_audio_effect(self) -> AudioEffect | None:
        for effect in self._current_audio_effects:
            if effect.id == self._selected_audio_effect_id:
                return effect
        return None

    def _on_audio_effect_selection_changed(self, row: int) -> None:
        item = self.audio_effects_list.item(row)
        self._selected_audio_effect_id = (
            item.data(Qt.UserRole) if item is not None else None
        )
        self._refresh_audio_effect_controls()

    def _refresh_audio_effect_controls(self) -> None:
        """Synchronise les actions et paramètres avec l'effet audio choisi."""
        from ui.i18n import translate as _tr

        effect = self._selected_audio_effect()
        has_effect = effect is not None
        for button in (
            self.audio_effect_toggle_button,
            self.audio_effect_remove_button,
            self.audio_effect_up_button,
            self.audio_effect_down_button,
        ):
            button.setEnabled(has_effect)
        if effect is None:
            self._clear_audio_effect_parameters()
            return
        self.audio_effect_toggle_button.setText(
            _tr("audio_effects.disable")
            if effect.enabled else _tr("audio_effects.enable")
        )
        index = next(
            i for i, e in enumerate(self._current_audio_effects)
            if e.id == effect.id
        )
        self.audio_effect_up_button.setEnabled(index > 0)
        self.audio_effect_down_button.setEnabled(
            index < len(self._current_audio_effects) - 1
        )
        self._build_audio_effect_parameters(effect)

    def _clear_audio_effect_parameters(self) -> None:
        """Vide la zone de réglages de l'effet audio sélectionné."""
        self._audio_effect_param_widgets = {}
        while self.audio_effect_parameters_layout.count():
            item = self.audio_effect_parameters_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _build_audio_effect_parameters(self, effect: AudioEffect) -> None:
        """Construit un contrôle par paramètre déclaré pour ``effect``."""
        from ui.i18n import translate as _tr

        self._clear_audio_effect_parameters()
        specs = audio_parameter_specs(effect.type)
        if not specs:
            label = QLabel(_tr("effects.no_parameters"))
            label.setStyleSheet(label_style(11, "muted", 500))
            self.audio_effect_parameters_layout.addWidget(label)
            return
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(Spacing.xs)
        form.setLabelAlignment(Qt.AlignLeft)
        for spec in specs:
            spin = QDoubleSpinBox()
            spin.setRange(spec.minimum, spec.maximum)
            spin.setDecimals(3)
            spin.setSingleStep(0.1)
            spin.setMinimumWidth(80)
            spin.setValue(float(effect.params.get(spec.name, spec.default)))
            spin.valueChanged.connect(
                lambda value, name=spec.name, fx_id=effect.id: (
                    self._on_audio_effect_parameter_changed(fx_id, name, float(value))
                )
            )
            self._audio_effect_param_widgets[spec.name] = spin
            label = QLabel(_tr(f"audio_effects.param.{spec.name}"))
            label.setStyleSheet(label_style(11, "muted", 600))
            form.addRow(label, spin)
        wrapper = QWidget()
        wrapper.setLayout(form)
        self.audio_effect_parameters_layout.addWidget(wrapper)
        self._sync_inspector_tab_order()

    def _on_audio_effect_parameter_changed(
        self, effect_id: str, name: str, value: float
    ) -> None:
        if (
            not self._allow_audio_effect_signals
            or self.selected_clip is None
        ):
            return
        self.audio_effect_parameter_changed.emit(
            self.selected_clip.id, effect_id, name, float(value)
        )

    def _on_audio_effect_toggle(self) -> None:
        effect = self._selected_audio_effect()
        if effect is None or self.selected_clip is None:
            return
        self.audio_effect_enabled_changed.emit(
            self.selected_clip.id, effect.id, not effect.enabled
        )

    def _on_audio_effect_add(self) -> None:
        if self.selected_clip is None:
            return
        effect_type = self.audio_effect_type_combo.currentData()
        if isinstance(effect_type, str):
            self.audio_effect_add_requested.emit(
                self.selected_clip.id, effect_type
            )

    def _on_audio_effect_remove(self) -> None:
        effect = self._selected_audio_effect()
        if effect is None or self.selected_clip is None:
            return
        self.audio_effect_removed.emit(self.selected_clip.id, effect.id)

    def _emit_audio_effect_move(self, delta: int) -> None:
        effect = self._selected_audio_effect()
        if effect is None or self.selected_clip is None:
            return
        self.audio_effect_moved.emit(
            self.selected_clip.id, effect.id, int(delta)
        )
