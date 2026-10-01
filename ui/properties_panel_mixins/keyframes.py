"""Mouvement : curseurs, images-clés et losanges d'animation."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
)

from core.animation import InterpolationType
from ui import i18n

from core.visual_effects import (
    ANIMATABLE_PROPERTIES,
    ClipTransform,
    TransformKeyframe,
    evaluate_transform,
)
from ui.properties_widgets.common import _KEYFRAME_MATCH_TOLERANCE, _PROPERTY_RANGES

class KeyframesMixin:
    """Mixin de ``PropertiesPanel`` : mouvement : curseurs, images-clés et losanges d'animation."""

    # ------------------------------------------------------------------
    # Helpers privés : MOUVEMENT
    # ------------------------------------------------------------------

    @staticmethod
    def _human_label(property_name: str) -> str:
        return {
            "position_x": "X",
            "position_y": "Y",
            "scale": "Échelle",
            "rotation": "Rotation",
            "opacity": "Opacité",
        }[property_name]

    @staticmethod
    def _default_value_for(property_name: str) -> float:
        return {
            "position_x": 0.0,
            "position_y": 0.0,
            "scale": 1.0,
            "rotation": 0.0,
            "opacity": 1.0,
        }[property_name]

    def _make_value_changed_handler(self, property_name: str):
        def _handler(value: float) -> None:
            self._on_property_changed(property_name, float(value))
        return _handler

    def _push_signal_block(self) -> None:
        self._signal_block_depth += 1
        self._allow_property_signals = False

    def _pop_signal_block(self) -> None:
        self._signal_block_depth = max(0, self._signal_block_depth - 1)
        self._allow_property_signals = self._signal_block_depth == 0

    @staticmethod
    def _slider_range(property_name: str) -> tuple[int, int]:
        low, high, _step = _PROPERTY_RANGES[property_name]
        if property_name == "rotation":
            return int(low), int(high)
        return int(round(low * 100)), int(round(high * 100))

    @staticmethod
    def _slider_position(property_name: str, value: float) -> int:
        low, high = KeyframesMixin._slider_range(property_name)
        if property_name == "rotation":
            position = int(round(value))
        else:
            position = int(round(value * 100))
        return max(low, min(high, position))

    def _make_slider_handler(self, property_name: str):
        def _handler(value: int) -> None:
            if not self._allow_property_signals:
                return
            if property_name == "rotation":
                float_value = float(value)
            else:
                float_value = float(value) / 100.0
            spin = self._spin_boxes.get(property_name)
            if spin is not None:
                self._push_signal_block()
                try:
                    spin.setValue(float_value)
                finally:
                    self._pop_signal_block()
            self._on_property_changed(property_name, float_value)
        return _handler

    def _on_property_changed(self, property_name: str, value: float) -> None:
        if not self._allow_property_signals or self.selected_clip is None:
            return
        value = float(value)
        self._sync_sibling_widgets(property_name, value)
        local = self._display_local_time()
        mode, time_seconds = self._edit_target(property_name, local)
        clip_id = self.selected_clip.id
        self.active_property_changed.emit(property_name)
        if mode == "base":
            self.transform_changed.emit(clip_id, property_name, value)
            return
        self.keyframe_added.emit(clip_id, property_name, time_seconds, value)

    def _sync_sibling_widgets(self, property_name: str, value: float) -> None:
        slider = self._slider_widgets.get(property_name)
        if slider is None:
            return
        position = self._slider_position(property_name, value)
        if slider.value() == position:
            return
        self._push_signal_block()
        try:
            slider.setValue(position)
        finally:
            self._pop_signal_block()

    def _on_diamond_clicked(self, property_name: str) -> None:
        shift = bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)
        self._apply_diamond_action(property_name, shift=shift)

    def _apply_diamond_action(self, property_name: str, *, shift: bool) -> None:
        """Clic sur le losange : retire l'image-clé sous la tête, sinon en ajoute une.

        Le premier clic d'une propriété non animée **active l'animation**
        (image-clé à la valeur actuelle). Maj+clic retire seulement.
        """
        if self.selected_clip is None:
            return
        if not self._playhead_inside_clip():
            self._restore_diamond(property_name)
            return
        self.active_property_changed.emit(property_name)
        local = self._display_local_time()
        matched = self._matching_keyframe(property_name, local)
        if shift or matched is not None:
            if matched is None:
                self._restore_diamond(property_name)
                return
            self._set_diamond_checked(property_name, False)
            self.keyframe_removed.emit(
                self.selected_clip.id,
                property_name,
                float(matched.time_seconds),
            )
            return
        if not self._is_animated(property_name):
            self._set_diamond_checked(property_name, True)
            self.animation_toggled.emit(self.selected_clip.id, property_name, True)
            return
        spin = self._spin_boxes.get(property_name)
        if spin is None:
            return
        time_seconds = float(matched.time_seconds) if matched is not None else float(local)
        self._set_diamond_checked(property_name, True)
        self.keyframe_added.emit(
            self.selected_clip.id,
            property_name,
            time_seconds,
            float(spin.value()),
        )

    def _set_diamond_checked(self, property_name: str, checked: bool) -> None:
        diamond = self._diamonds[property_name]
        diamond.blockSignals(True)
        diamond.setChecked(checked)
        diamond.blockSignals(False)
        self._diamond_was_checked[property_name] = checked

    def _restore_diamond(self, property_name: str) -> None:
        matched = self._matching_keyframe(property_name, self._display_local_time())
        self._set_diamond_checked(property_name, matched is not None)

    def _sync_diamond_state_memory(self) -> None:
        self._diamond_was_checked = {
            name: diamond.isChecked() for name, diamond in self._diamonds.items()
        }

    def _clip_bounds(self) -> tuple[float, float] | None:
        clip = self.selected_clip
        if clip is None:
            return None
        start = float(clip.start)
        end = float(clip.end)
        if end < start:
            end = start
        return start, end

    def _clip_duration(self) -> float:
        bounds = self._clip_bounds()
        if bounds is None:
            return 0.0
        return bounds[1] - bounds[0]

    def _display_local_time(self) -> float:
        bounds = self._clip_bounds()
        if bounds is None:
            return 0.0
        start, end = bounds
        playhead = float(self._current_playhead_seconds)
        if playhead < start:
            return 0.0
        if playhead > end:
            return end - start
        return playhead - start

    def _playhead_inside_clip(self) -> bool:
        bounds = self._clip_bounds()
        if bounds is None:
            return False
        start, end = bounds
        playhead = float(self._current_playhead_seconds)
        return start - 1e-6 <= playhead <= end + 1e-6

    def _matching_keyframe(
        self, property_name: str, local_time: float
    ) -> TransformKeyframe | None:
        best: TransformKeyframe | None = None
        best_distance = _KEYFRAME_MATCH_TOLERANCE
        for keyframe in self._current_keyframes:
            if keyframe.property_name != property_name:
                continue
            distance = abs(keyframe.time_seconds - local_time)
            if distance <= best_distance:
                best = keyframe
                best_distance = distance
        return best

    def _is_animated(self, property_name: str) -> bool:
        return any(k.property_name == property_name for k in self._current_keyframes)

    def _edit_target(self, property_name: str, local_time: float) -> tuple[str, float]:
        """Où va une valeur saisie : base (non animée) ou image-clé au temps courant.

        Propriété animée : la saisie crée (ou met à jour) une image-clé à la tête
        de lecture — « activer, déplacer la tête, modifier » suffit à animer.
        """
        matched = self._matching_keyframe(property_name, local_time)
        if matched is not None:
            return "keyframe", float(matched.time_seconds)
        if not self._is_animated(property_name):
            return "base", local_time
        duration = self._clip_duration()
        if local_time > duration:
            local_time = duration
        return "keyframe", local_time

    def _emit_reset(self) -> None:
        if self.selected_clip is None:
            return
        self.transform_reset.emit(self.selected_clip.id)

    # ------------------------------------------------------------------
    # Helpers de mise à jour depuis l'extérieur
    # ------------------------------------------------------------------

    def update_transform_from_clip(
        self,
        transform: ClipTransform,
        keyframes: list[TransformKeyframe],
        playhead_seconds: float = 0.0,
    ) -> None:
        self._current_playhead_seconds = float(playhead_seconds)
        self._current_transform = transform
        self._current_keyframes = list(keyframes)
        if self.selected_clip is None:
            return
        self._apply_motion_fields(transform, self._current_keyframes)

    def _apply_motion_fields(
        self,
        transform: ClipTransform | None,
        keyframes: list[TransformKeyframe],
    ) -> None:
        if self.selected_clip is None:
            return
        self._push_signal_block()
        try:
            if transform is not None:
                local_time = self._display_local_time()
                evaluated = evaluate_transform(
                    transform,
                    keyframes,
                    local_time,
                    self._clip_duration(),
                )
                for property_name in ANIMATABLE_PROPERTIES:
                    matched = self._matching_keyframe(property_name, local_time)
                    if matched is not None:
                        value = float(matched.value)
                    else:
                        value = float(getattr(evaluated, property_name))
                    spin = self._spin_boxes.get(property_name)
                    if spin is not None:
                        spin.setValue(value)
                    slider = self._slider_widgets.get(property_name)
                    if slider is not None:
                        slider.setValue(self._slider_position(property_name, value))
            self._sync_diamonds()
        finally:
            self._pop_signal_block()

    def _sync_diamonds(self) -> None:
        local = self._display_local_time()
        enabled = self.selected_clip is not None and self.movement_group.isEnabled()
        for property_name, diamond in self._diamonds.items():
            diamond.blockSignals(True)
            diamond.setChecked(
                self._matching_keyframe(property_name, local) is not None
            )
            diamond.blockSignals(False)
            times = [k.time_seconds for k in self._current_keyframes if k.property_name == property_name]
            diamond.set_animated(bool(times))
            buttons = getattr(self, "_keyframe_nav_buttons", {}).get(property_name)
            if buttons is not None:
                previous_button, next_button = buttons
                previous_button.setEnabled(enabled and any(t < local - 1e-3 for t in times))
                next_button.setEnabled(enabled and any(t > local + 1e-3 for t in times))
        self._sync_diamond_state_memory()

    # ------------------------------------------------------------------
    # Navigation et menu d'animation
    # ------------------------------------------------------------------

    def _on_keyframe_navigation(self, property_name: str, direction: int) -> None:
        """Flèches ‹ › d'une ligne : image-clé précédente / suivante de la propriété."""
        if self.selected_clip is not None:
            self.keyframe_navigation_requested.emit(property_name, int(direction))

    def animation_menu(self, property_name: str) -> QMenu:
        """Menu du losange : activer / désactiver, interpolation, copier / coller, courbes."""
        menu = QMenu(self)
        clip = self.selected_clip
        if clip is None:
            return menu
        animated = self._is_animated(property_name)
        toggle = QAction(i18n.translate("animation.menu.disable" if animated else "animation.menu.enable"), menu)
        toggle.triggered.connect(
            lambda: self.animation_toggled.emit(clip.id, property_name, not animated)
        )
        menu.addAction(toggle)
        matched = self._matching_keyframe(property_name, self._display_local_time())
        interpolation_menu = menu.addMenu(i18n.translate("animation.menu.interpolation"))
        interpolation_menu.setEnabled(matched is not None)
        for kind in InterpolationType:
            action = QAction(i18n.translate(f"animation.interpolation.{kind.value}"), interpolation_menu)
            action.setCheckable(True)
            action.setChecked(matched is not None and matched.interpolation is kind)
            action.triggered.connect(
                lambda _checked=False, value=kind.value: self.interpolation_requested.emit(property_name, value)
            )
            interpolation_menu.addAction(action)
        menu.addSeparator()
        copy = QAction(i18n.translate("animation.menu.copy"), menu)
        copy.setEnabled(animated)
        copy.triggered.connect(lambda: self.animation_copy_requested.emit(property_name))
        menu.addAction(copy)
        paste = QAction(i18n.translate("animation.menu.paste"), menu)
        paste.triggered.connect(lambda: self.animation_paste_requested.emit(property_name))
        menu.addAction(paste)
        menu.addSeparator()
        graph = QAction(i18n.translate("animation.menu.graph"), menu)
        graph.triggered.connect(lambda: self.graph_editor_requested.emit(property_name))
        menu.addAction(graph)
        return menu

    def _open_animation_menu(self, property_name: str, position) -> None:
        diamond = self._diamonds.get(property_name)
        if diamond is None or self.selected_clip is None:
            return
        self.active_property_changed.emit(property_name)
        self.animation_menu(property_name).exec(diamond.mapToGlobal(position))

    def refresh_keyframe_diamonds(
        self,
        keyframes: list[TransformKeyframe],
        playhead_seconds: float,
        transform: ClipTransform | None = None,
    ) -> None:
        self._current_playhead_seconds = float(playhead_seconds)
        self._current_keyframes = list(keyframes)
        if transform is not None:
            self._current_transform = transform
        if self.selected_clip is None:
            return
        self._apply_motion_fields(self._current_transform, self._current_keyframes)
