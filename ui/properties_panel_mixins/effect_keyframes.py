"""Images clés des paramètres d'effets : losanges ‹ ◆ › sous les réglages animables (ADR-0008, étape 4).

Même modèle que la transformation (:mod:`ui.properties_panel_mixins.keyframes`) : sans image clé sur le paramètre, le curseur
écrit la valeur de **base** ; animé, il écrit (ou met à jour) l'image clé à la tête de lecture et affiche la valeur évaluée à
cet instant. Les paramètres animables sont ceux de :data:`core.effect_targets.ANIMATABLE_EFFECT_PARAMS`, jamais d'autres.

Niveau d'usage (``docs/usage-levels.md``) : les losanges sont **Studio**. Chaque rangée est créée par le builder des réglages
(nouveaux widgets à chaque construction, jamais déplacés) avec son conteneur :class:`~ui.properties_widgets.level_gate.LevelGate` ;
le niveau n'écrit que la visibilité de ce conteneur. Le menu du clic droit suit ``_advanced_animation_menu_shown`` (Avancé).
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QFormLayout, QHBoxLayout, QToolButton, QWidget

from core.effect_targets import ANIMATABLE_EFFECT_PARAMS, evaluated_effects
from core.effects_model import ClipEffect, fx_property_id
from core.usage_level import UsageLevel
from ui import i18n
from ui.properties_widgets.diamond_button import KeyframeDiamondButton
from ui.properties_widgets.level_gate import LevelGate, gated


@dataclass
class EffectKeyframeRow:
    """Les trois boutons d'un paramètre animable et leur conteneur de niveau."""

    gate: LevelGate
    previous: QToolButton
    diamond: KeyframeDiamondButton
    next: QToolButton


class EffectKeyframesMixin:
    """Mixin de ``PropertiesPanel`` : losanges d'images clés du paramètre d'effet affiché."""

    # ------------------------------------------------------------------
    # Construction (appelée par ``_build_effect_parameters``)
    # ------------------------------------------------------------------

    def _is_animatable_effect_param(self, effect: ClipEffect, name: str) -> bool:
        return name in ANIMATABLE_EFFECT_PARAMS.get(effect.type, ())

    def _add_effect_keyframe_row(self, form: QFormLayout, effect: ClipEffect, name: str, label_text: str) -> None:
        """Rangée ‹ ◆ › sous le curseur de ``name`` : nouveaux widgets, conteneur Studio créé avec eux."""
        property_id = fx_property_id(effect.id, name)
        inner = QWidget()
        layout = QHBoxLayout(inner)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        previous = QToolButton()
        previous.setObjectName("iconOnly")      # padding nul : sinon le thème rogne la flèche
        previous.setText("‹")
        previous.setFixedSize(18, 22)
        previous.clicked.connect(lambda _checked=False, pid=property_id: self._on_keyframe_navigation(pid, -1))
        diamond = KeyframeDiamondButton(property_id)
        diamond.clicked.connect(lambda _checked=False, eid=effect.id, n=name: self._on_effect_diamond_clicked(eid, n))
        diamond.setContextMenuPolicy(Qt.CustomContextMenu)
        diamond.customContextMenuRequested.connect(
            lambda position, eid=effect.id, n=name: self._open_effect_animation_menu(eid, n, position)
        )
        next_button = QToolButton()
        next_button.setObjectName("iconOnly")
        next_button.setText("›")
        next_button.setFixedSize(18, 22)
        next_button.clicked.connect(lambda _checked=False, pid=property_id: self._on_keyframe_navigation(pid, 1))
        previous.setAccessibleName(i18n.translate("a11y.keyframe.previous", property=label_text))
        diamond.setAccessibleName(i18n.translate("a11y.keyframe.toggle", property=label_text))
        next_button.setAccessibleName(i18n.translate("a11y.keyframe.next", property=label_text))
        for widget in (previous, diamond, next_button):
            layout.addWidget(widget)
        layout.addStretch(1)
        gate = gated(inner, UsageLevel.STUDIO, form)
        form.addRow(gate)
        self._effect_keyframe_rows[name] = EffectKeyframeRow(gate, previous, diamond, next_button)

    def _apply_effect_keyframe_levels(self) -> None:
        """Visibilité des rangées selon le niveau (Studio) : le niveau ne fait que montrer ou cacher."""
        shown = self._level_allows(UsageLevel.STUDIO, self.effects_group)
        for row in self._effect_keyframe_rows.values():
            row.gate.set_shown(shown)

    # ------------------------------------------------------------------
    # Lecture de l'état
    # ------------------------------------------------------------------

    def effect_keyframe_widgets(self) -> dict[str, EffectKeyframeRow]:
        """Rangées ‹ ◆ › du réglage affiché, par nom de paramètre (vide pour un effet sans paramètre animable)."""
        return dict(self._effect_keyframe_rows)

    def _sync_effect_keyframes(self) -> None:
        """Aligne curseurs animés et losanges sur la tête de lecture, sans émettre de signal ni recréer de widget."""
        effect = self._selected_effect()
        if effect is None or self.selected_clip is None or not self._effect_keyframe_rows:
            return
        local = self._display_local_time()
        shown = evaluated_effects([effect], self._current_effect_keyframes, local)[0].params
        enabled = self.effects_group.isEnabled()
        for name, row in self._effect_keyframe_rows.items():
            property_id = fx_property_id(effect.id, name)
            spin = self._effect_param_widgets.get(name)
            if spin is not None and self._is_animated(property_id):
                self._set_spin_quietly(spin, float(shown[name]))
            times = [k.time_seconds for k in self._current_effect_keyframes if k.property_name == property_id]
            self._set_effect_diamond(row, self._matching_keyframe(property_id, local) is not None, bool(times))
            row.previous.setEnabled(enabled and any(t < local - 1e-3 for t in times))
            row.next.setEnabled(enabled and any(t > local + 1e-3 for t in times))

    @staticmethod
    def _set_spin_quietly(spin: QDoubleSpinBox, value: float) -> None:
        if abs(spin.value() - value) < 5e-4:
            return
        blocked = spin.blockSignals(True)
        try:
            spin.setValue(value)
        finally:
            spin.blockSignals(blocked)

    @staticmethod
    def _set_effect_diamond(row: EffectKeyframeRow, checked: bool, animated: bool) -> None:
        blocked = row.diamond.blockSignals(True)
        row.diamond.setChecked(checked)
        row.diamond.blockSignals(blocked)
        row.diamond.set_animated(animated)

    def refresh_effect_keyframes(self, keyframes: list, playhead_seconds: float) -> None:
        """Nouvelles images clés des effets ou nouvelle tête de lecture : curseurs et losanges suivent."""
        self._current_effect_keyframes = list(keyframes)
        self._current_playhead_seconds = float(playhead_seconds)
        self._sync_effect_keyframes()

    # ------------------------------------------------------------------
    # Gestes
    # ------------------------------------------------------------------

    def _on_effect_diamond_clicked(self, effect_id: str, name: str) -> None:
        shift = bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)
        self._apply_effect_diamond_action(effect_id, name, shift=shift)

    def _apply_effect_diamond_action(self, effect_id: str, name: str, *, shift: bool) -> None:
        """Clic sur le losange : retire l'image clé sous la tête, sinon en ajoute une (le premier clic active l'animation)."""
        row = self._effect_keyframe_rows.get(name)
        clip = self.selected_clip
        if row is None or clip is None:
            return
        property_id = fx_property_id(effect_id, name)
        local = self._display_local_time()
        matched = self._matching_keyframe(property_id, local)
        if not self._playhead_inside_clip() or (shift and matched is None):
            self._set_effect_diamond(row, matched is not None, self._is_animated(property_id))
            return
        self.active_property_changed.emit(property_id)
        if matched is not None:
            self._set_effect_diamond(row, False, self._is_animated(property_id))
            self.effect_keyframe_removed.emit(clip.id, property_id, float(matched.time_seconds))
            return
        if not self._is_animated(property_id):
            self._set_effect_diamond(row, True, True)
            self.animation_toggled.emit(clip.id, property_id, True)
            return
        spin = self._effect_param_widgets.get(name)
        if spin is None:
            return
        self._set_effect_diamond(row, True, True)
        self.effect_keyframe_set.emit(clip.id, property_id, float(local), float(spin.value()))

    def _effect_edit_target(self, effect_id: str, name: str) -> tuple[str, float] | None:
        """Où va une valeur saisie : ``None`` (valeur de base) ou ``(identifiant, temps local)`` de l'image clé à écrire."""
        effect = self._selected_effect()
        if effect is None or effect.id != effect_id or not self._is_animatable_effect_param(effect, name):
            return None
        property_id = fx_property_id(effect_id, name)
        if not self._is_animated(property_id):
            return None
        local = self._display_local_time()
        matched = self._matching_keyframe(property_id, local)
        time_seconds = float(matched.time_seconds) if matched is not None else min(local, self._clip_duration())
        return property_id, time_seconds

    def effect_animation_menu(self, effect_id: str, name: str):
        """Menu du losange d'un paramètre d'effet : celui de la transformation, plus « Supprimer l'animation de l'effet »."""
        property_id = fx_property_id(effect_id, name)
        menu = self.animation_menu(property_id)
        clip = self.selected_clip
        prefix = f"fx.{effect_id}."
        if clip is not None and any(k.property_name.startswith(prefix) for k in self._current_effect_keyframes):
            action = QAction(i18n.translate("animation.menu.clear_effect"), menu)
            action.triggered.connect(lambda: self.effect_animation_cleared.emit(clip.id, effect_id))
            actions = menu.actions()
            menu.insertAction(actions[1] if len(actions) > 1 else None, action)
        return menu

    def _open_effect_animation_menu(self, effect_id: str, name: str, position) -> None:
        row = self._effect_keyframe_rows.get(name)
        if row is None or self.selected_clip is None:
            return
        self.active_property_changed.emit(fx_property_id(effect_id, name))
        self.effect_animation_menu(effect_id, name).exec(row.diamond.mapToGlobal(position))


__all__ = ["EffectKeyframeRow", "EffectKeyframesMixin"]
