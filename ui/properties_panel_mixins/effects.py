"""Rack d'effets vidéo du clip sélectionné."""

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

from core.effects_model import (
    ClipEffect,
    EffectType,
    is_single_instance,
    parameter_specs,
)
from ui.adaptive_layout import FlowLayout, make_shrinkable
from ui.design_system import Sizes, Spacing
from ui.i18n import translate
from ui.theme import COLORS, label_style


class EffectsMixin:
    """Mixin de ``PropertiesPanel`` : rack d'effets vidéo du clip sélectionné."""

    # ------------------------------------------------------------------
    # Effets du clip (tâche 21)
    # ------------------------------------------------------------------

    def _build_effects_group(self) -> QGroupBox:
        """Groupe « Effets » : liste, actions et réglages du clip vidéo.

        L'inspecteur n'écrit jamais dans le modèle : il publie des
        intentions via ses signaux, que ``MainWindow`` traduit en
        opérations métier puis en entrée d'historique.
        """
        group = QGroupBox(translate("effects.section"))
        group.setObjectName("effectsGroup")
        group.setStyleSheet(self.group_style())
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(
            Spacing.md, Spacing.md, Spacing.md, Spacing.md
        )
        group_layout.setSpacing(Spacing.xs)

        self.effects_hint = QLabel(translate("effects.no_clip"))
        self.effects_hint.setWordWrap(True)
        self.effects_hint.setStyleSheet(label_style(11, "muted", 500))
        group_layout.addWidget(self.effects_hint)

        self.effect_add_row = QWidget()
        add_layout = QHBoxLayout(self.effect_add_row)
        add_layout.setContentsMargins(0, 0, 0, 0)
        add_layout.setSpacing(Spacing.xs)
        self.effect_type_combo = make_shrinkable(QComboBox())
        self.effect_type_combo.setObjectName("effectType")
        self.effect_type_combo.setFocusPolicy(Qt.NoFocus)
        self.effect_type_combo.setStyleSheet(
            f"QComboBox#effectType {{ background: {COLORS['surface']};"
            f" color: {COLORS['text']}; border: 1px solid {COLORS['border']};"
            f" border-radius: 6px; padding: 4px 6px; font-size: 11px; }}"
            f"QComboBox#effectType:focus {{ border-color: {COLORS['accent']}; }}"
        )
        self.effect_add_button = self._make_effect_button(
            translate("effects.add"), self._on_effect_add
        )
        add_layout.addWidget(self.effect_type_combo, 1)
        add_layout.addWidget(self.effect_add_button)
        group_layout.addWidget(self.effect_add_row)

        self.effects_list = QListWidget()
        self.effects_list.setObjectName("effectsList")
        self.effects_list.setFixedHeight(104)
        self.effects_list.setStyleSheet(
            f"QListWidget {{ background: {COLORS['panel_alt']};"
            f" color: {COLORS['text']};"
            f" border: 1px solid {COLORS['border']}; border-radius: 6px; }}"
            f"QListWidget::item {{ padding: 4px 6px; }}"
            f"QListWidget::item:selected {{ background: {COLORS['accent_dark']};"
            f" color: {COLORS['accent']}; }}"
        )
        self.effects_list.currentRowChanged.connect(
            self._on_effect_selection_changed
        )
        group_layout.addWidget(self.effects_list)

        self.effects_empty_label = QLabel(translate("effects.empty"))
        self.effects_empty_label.setStyleSheet(label_style(11, "muted", 500))
        group_layout.addWidget(self.effects_empty_label)

        # --- Bandeau d'actions ---------------------------------------
        self.effect_buttons_row = QWidget()
        # Quatre boutons : ils passent à la ligne à 280 px d'inspecteur au lieu d'imposer ~270 px.
        buttons_layout = FlowLayout(self.effect_buttons_row, spacing=Spacing.xs)
        self.effect_toggle_button = self._make_effect_button(
            translate("effects.disable"), self._on_effect_toggle
        )
        self.effect_remove_button = self._make_effect_button(
            translate("effects.remove"), self._on_effect_remove
        )
        self.effect_up_button = self._make_effect_button(
            translate("effects.move_up"), lambda: self._emit_effect_move(-1)
        )
        self.effect_down_button = self._make_effect_button(
            translate("effects.move_down"), lambda: self._emit_effect_move(1)
        )
        for button in (
            self.effect_toggle_button,
            self.effect_remove_button,
            self.effect_up_button,
            self.effect_down_button,
        ):
            buttons_layout.addWidget(button)
        group_layout.addWidget(self.effect_buttons_row)

        # --- Réglages de l'effet sélectionné --------------------------
        self.effect_parameters_container = QWidget()
        self.effect_parameters_layout = QVBoxLayout(
            self.effect_parameters_container
        )
        self.effect_parameters_layout.setContentsMargins(0, 0, 0, 0)
        self.effect_parameters_layout.setSpacing(Spacing.xs)
        group_layout.addWidget(self.effect_parameters_container)

        group.setEnabled(False)
        return group

    def _make_effect_button(self, text: str, handler) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("effectAction")
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.NoFocus)
        button.setMinimumHeight(Sizes.button_sm)
        button.setStyleSheet(
            f"QPushButton#effectAction {{ background: {COLORS['surface']};"
            f" color: {COLORS['text']}; border: 1px solid {COLORS['border']};"
            f" border-radius: 6px; padding: 4px 6px; font-size: 11px; }}"
            f"QPushButton#effectAction:hover {{ background: {COLORS['surface_hover']}; }}"
            f"QPushButton#effectAction:focus {{ border: 1px solid {COLORS['accent']}; }}"
            f"QPushButton#effectAction:disabled {{ color: {COLORS['muted']}; }}"
        )
        button.clicked.connect(lambda _checked=False: handler())
        return button

    def update_effects_from_clip(self, effects, track_type=None) -> None:
        """Reconstruit la section Effets depuis l'état du ``Project``.

        Args:
            effects: liste des :class:`ClipEffect` du clip sélectionné.
            track_type: type de piste (``"video"``, ``"audio"``...). Toute
                valeur autre que ``"video"`` affiche un état non éditable.
        """
        self._current_effects = list(effects or [])
        if track_type != "video":
            self._selected_effect_id = None
            self._populate_effects_list()
            self._clear_effect_parameters()
            self.effects_hint.setText(
                translate("effects.no_clip")
                if track_type is None
                else translate("effects.video_only")
            )
            self.effects_hint.setVisible(True)
            self.effects_list.setVisible(False)
            self.effect_add_row.setVisible(False)
            self.effect_buttons_row.setVisible(False)
            self.effect_parameters_container.setVisible(False)
            self.effects_empty_label.setVisible(False)
            self.effects_group.setEnabled(False)
            return

        self.effects_hint.setVisible(False)
        self.effect_add_row.setVisible(True)
        self.effects_list.setVisible(True)
        self.effect_buttons_row.setVisible(True)
        self.effect_parameters_container.setVisible(True)
        self.effects_group.setEnabled(True)
        self._populate_effects_list()
        self._refresh_effect_catalog()

    def _refresh_effect_catalog(self) -> None:
        """Propose les effets compatibles qui ne sont pas déjà uniques."""
        current = self.effect_type_combo.currentData()
        existing = {effect.type for effect in self._current_effects}
        self.effect_type_combo.blockSignals(True)
        self.effect_type_combo.clear()
        for effect_type in EffectType:
            if is_single_instance(effect_type) and effect_type in existing:
                continue
            self.effect_type_combo.addItem(
                translate(f"effects.name.{effect_type.value}"), effect_type.value
            )
        if current:
            index = self.effect_type_combo.findData(current)
            if index >= 0:
                self.effect_type_combo.setCurrentIndex(index)
        self.effect_type_combo.blockSignals(False)
        self.effect_add_button.setEnabled(self.effect_type_combo.count() > 0)

    def _populate_effects_list(self) -> None:
        """Remplit la liste des effets et restaure la sélection."""
        self.effects_list.blockSignals(True)
        self.effects_list.clear()
        for effect in self._current_effects:
            name = translate(f"effects.name.{effect.type.value}")
            if not effect.enabled:
                name = f"{name} · {translate('effects.disabled_suffix')}"
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, effect.id)
            self.effects_list.addItem(item)
        self.effects_list.blockSignals(False)

        ids = [effect.id for effect in self._current_effects]
        if self._selected_effect_id not in ids:
            self._selected_effect_id = ids[0] if ids else None
        self.effects_empty_label.setVisible(not ids)
        row = ids.index(self._selected_effect_id) if self._selected_effect_id else -1
        self.effects_list.blockSignals(True)
        self.effects_list.setCurrentRow(row)
        self.effects_list.blockSignals(False)
        self._refresh_effect_controls()

    def _selected_effect(self) -> ClipEffect | None:
        for effect in self._current_effects:
            if effect.id == self._selected_effect_id:
                return effect
        return None

    def _on_effect_selection_changed(self, row: int) -> None:
        item = self.effects_list.item(row)
        self._selected_effect_id = (
            item.data(Qt.UserRole) if item is not None else None
        )
        self._refresh_effect_controls()

    def _refresh_effect_controls(self) -> None:
        """Synchronise actions et paramètres avec l'effet sélectionné."""
        effect = self._selected_effect()
        has_effect = effect is not None
        for button in (
            self.effect_toggle_button,
            self.effect_remove_button,
            self.effect_up_button,
            self.effect_down_button,
        ):
            button.setEnabled(has_effect)
        if effect is None:
            self._clear_effect_parameters()
            return
        self.effect_toggle_button.setText(
            translate("effects.disable")
            if effect.enabled
            else translate("effects.enable")
        )
        index = next(
            i for i, e in enumerate(self._current_effects) if e.id == effect.id
        )
        self.effect_up_button.setEnabled(index > 0)
        self.effect_down_button.setEnabled(index < len(self._current_effects) - 1)
        self._build_effect_parameters(effect)

    def _clear_effect_parameters(self) -> None:
        """Vide la zone de réglages de l'effet sélectionné."""
        self._effect_param_widgets = {}
        while self.effect_parameters_layout.count():
            item = self.effect_parameters_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _build_effect_parameters(self, effect: ClipEffect) -> None:
        """Construit un contrôle par paramètre déclaré pour ``effect``."""
        self._clear_effect_parameters()
        specs = parameter_specs(effect.type)
        if not specs:
            label = QLabel(translate("effects.no_parameters"))
            label.setStyleSheet(label_style(11, "muted", 500))
            self.effect_parameters_layout.addWidget(label)
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
                    self._on_effect_parameter_changed(fx_id, name, float(value))
                )
            )
            self._effect_param_widgets[spec.name] = spin
            label = QLabel(translate(f"effects.param.{spec.name}"))
            label.setStyleSheet(label_style(11, "muted", 600))
            form.addRow(label, spin)
        wrapper = QWidget()
        wrapper.setLayout(form)
        self.effect_parameters_layout.addWidget(wrapper)
        self._sync_inspector_tab_order()  # réglages ajoutés après coup : Tab les visite à leur place

    def _on_effect_parameter_changed(
        self, effect_id: str, name: str, value: float
    ) -> None:
        if not self._allow_effect_signals or self.selected_clip is None:
            return
        self.effect_parameter_changed.emit(
            self.selected_clip.id, effect_id, name, float(value)
        )

    def _on_effect_toggle(self) -> None:
        effect = self._selected_effect()
        if effect is None or self.selected_clip is None:
            return
        self.effect_enabled_changed.emit(
            self.selected_clip.id, effect.id, not effect.enabled
        )

    def _on_effect_add(self) -> None:
        if self.selected_clip is None:
            return
        effect_type = self.effect_type_combo.currentData()
        if isinstance(effect_type, str):
            self.effect_add_requested.emit(self.selected_clip.id, effect_type)

    def _on_effect_remove(self) -> None:
        effect = self._selected_effect()
        if effect is None or self.selected_clip is None:
            return
        self.effect_removed.emit(self.selected_clip.id, effect.id)

    def _emit_effect_move(self, delta: int) -> None:
        effect = self._selected_effect()
        if effect is None or self.selected_clip is None:
            return
        self.effect_moved.emit(self.selected_clip.id, effect.id, int(delta))
