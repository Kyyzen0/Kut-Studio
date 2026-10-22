"""Images clés de l'étalonnage dans l'inspecteur : losanges ‹ ◆ › sous les sept réglages (ADR-0008, étape 8).

Même modèle que les effets (:mod:`ui.properties_panel_mixins.effect_keyframes`) : sans image clé sur le réglage, le curseur écrit
la valeur de **base** ; animé, il affiche la valeur évaluée à la tête de lecture et sa saisie écrit (ou met à jour) l'image clé à
cet instant. Le curseur émet toujours ``color_grade_field_changed`` : c'est la **fenêtre** qui décide de la destination (base ou
image clé, ``ui.main_window_mixins.color_animation``), pour qu'un signal qui atteint le modèle suive la même règle.

Les réglages animables sont ceux de :data:`core.grade_targets.SCALAR_NAMES`. **Un étalonnage découpé en nœuds ne s'anime pas** (le
rendu ignore les images clés ``color.*``) : les losanges sont désactivés et un avis le dit, avec un bouton pour retirer les images
clés que le clip garde. Niveau d'usage : les rangées sont **Studio**, créées avec leur conteneur par le builder du groupe.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout

from core.grade_keyframing import KeyState, key_state
from core.grade_targets import GradeCurves, is_animatable_grade, scalar_property_id
from core.usage_level import UsageLevel
from ui import i18n
from ui.icons import IconName
from ui.properties_widgets.keyframe_row import KeyframeRow, build_keyframe_row, set_diamond_quietly, set_spin_quietly
from ui.theme import label_style


class ColorKeyframesMixin:
    """Mixin de ``PropertiesPanel`` : losanges d'images clés des réglages de couleur affichés."""

    # ------------------------------------------------------------------
    # Construction (appelée par ``_build_color_group``)
    # ------------------------------------------------------------------

    def _add_color_keyframe_row(self, line: QHBoxLayout, name: str, label_text: str) -> None:
        """Boutons ‹ ◆ › à droite du champ de ``name`` (même ligne que son libellé) : nouveaux widgets, conteneur Studio créé avec eux."""
        property_id = scalar_property_id(name)
        self._color_keyframe_rows[name] = build_keyframe_row(
            property_id, label_text,
            on_previous=lambda: self._on_keyframe_navigation(property_id, -1),
            on_toggle=lambda: self._on_color_diamond_clicked(name),
            on_next=lambda: self._on_keyframe_navigation(property_id, 1),
            on_menu=lambda position: self._open_color_animation_menu(name, position),
            beside=line, minimum=UsageLevel.STUDIO,
        )

    def _equalize_color_captions(self) -> None:
        """Tous les libellés de réglages ont la largeur du plus long : le formulaire passe à la ligne (libellé au-dessus de son champ et
        de ses losanges) pour **toutes** les rangées à la fois, jamais seulement pour « Température »."""
        for caption in self._color_captions.values():
            caption.setMinimumWidth(0)
        widest = max((caption.sizeHint().width() for caption in self._color_captions.values()), default=0)
        for caption in self._color_captions.values():
            caption.setMinimumWidth(widest)

    def _build_color_animation_notice(self, root: QVBoxLayout) -> None:
        """Avis « étalonnage découpé en nœuds » et bouton qui retire les images clés ignorées (cachés tant qu'il ne sert pas)."""
        self.color_animation_label = QLabel()
        self.color_animation_label.setWordWrap(True)
        self.color_animation_label.setStyleSheet(label_style(11, "muted", 600))
        self.color_animation_label.hide()
        root.addWidget(self.color_animation_label)
        self.color_animation_clear_button = self._make_action_button(
            IconName.REMOVE, i18n.translate("color.animation.clear"), i18n.translate("color.animation.clear.tip"),
        )
        self.color_animation_clear_button.clicked.connect(self._request_color_animation_clear)
        self.color_animation_clear_button.hide()
        root.addWidget(self.color_animation_clear_button)

    def _apply_color_keyframe_levels(self) -> None:
        """Visibilité des rangées selon le niveau (Studio) ; « Afficher plus d'options » les montre aussi depuis Simple."""
        shown = self._level_allows(UsageLevel.STUDIO, self.color_group) or self._more_groups_shown
        for row in self._color_keyframe_rows.values():
            if row.gate is not None:
                row.gate.set_shown(shown)

    # ------------------------------------------------------------------
    # Lecture de l'état
    # ------------------------------------------------------------------

    def color_keyframe_widgets(self) -> dict[str, KeyframeRow]:
        """Rangées ‹ ◆ › des sept réglages, par nom de champ (``exposure``…)."""
        return dict(self._color_keyframe_rows)

    def _color_animatable(self) -> bool:
        """Le réglage affiché est celui que les images clés animent (un réglage simple, pas un graphe de nœuds)."""
        return is_animatable_grade(self._color_value)

    def color_key_state(self, name: str) -> KeyState:
        return key_state(self._current_grade_keyframes, [scalar_property_id(name)], self._display_local_time())

    def _sync_color_keyframes(self) -> None:
        """Aligne curseurs animés, losanges et flèches sur la tête de lecture, sans signal ni widget recréé."""
        animatable = self._color_animatable()
        keys = self._current_grade_keyframes
        enabled = self.selected_clip is not None and self.color_group.isEnabled() and animatable
        curves = GradeCurves(self._color_value, keys) if keys and animatable else None
        local = self._display_local_time()
        tip = i18n.translate("color.animation.disabled_tip")
        for name, row in self._color_keyframe_rows.items():
            property_id = scalar_property_id(name)
            state = key_state(keys, [property_id], local)
            set_diamond_quietly(row.diamond, state.keyed_all, state.animated and animatable)
            row.diamond.setEnabled(animatable)
            row.previous.setEnabled(enabled and state.previous)
            row.next.setEnabled(enabled and state.next)
            row.diamond.setToolTip(
                i18n.translate("animation.diamond.tooltip", name=self._color_field_label(name))
                if animatable else tip
            )
            spin = self.color_field_spins.get(name)
            if spin is not None and curves is not None and property_id in curves.curves:
                set_spin_quietly(spin, curves.value(property_id, local))
        self._refresh_color_animation_notice()

    def _refresh_color_animation_notice(self) -> None:
        keys = len(self._current_grade_keyframes)
        show = self.selected_clip is not None and not self._color_animatable()
        text = i18n.translate("color.animation.nodes")
        if keys:
            text = f"{text}\n{i18n.translate('color.animation.ignored', count=keys)}"
        self.color_animation_label.setText(text if show else "")
        self.color_animation_label.setVisible(show)
        self.color_animation_clear_button.setVisible(show and keys > 0)

    def refresh_color_keyframes(self, keyframes: list, playhead_seconds: float) -> None:
        """Nouvelles images clés d'étalonnage ou nouvelle tête de lecture : curseurs et losanges suivent."""
        self._current_grade_keyframes = list(keyframes)
        self._current_playhead_seconds = float(playhead_seconds)
        self._sync_color_keyframes()

    # ------------------------------------------------------------------
    # Gestes
    # ------------------------------------------------------------------

    def _on_color_diamond_clicked(self, name: str) -> None:
        shift = bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)
        self._apply_color_diamond_action(name, shift=shift)

    def _apply_color_diamond_action(self, name: str, *, shift: bool) -> None:
        """Clic sur le losange : retire l'image clé sous la tête, sinon en pose une (le premier clic active l'animation).

        Maj + clic retire seulement. Tête hors du clip ou étalonnage non animable : rien, le losange reprend son état."""
        row = self._color_keyframe_rows.get(name)
        clip = self.selected_clip
        if row is None or clip is None:
            return
        property_id = scalar_property_id(name)
        state = self.color_key_state(name)
        if not self._color_animatable() or not self._playhead_inside_clip() or (shift and not state.keyed_any):
            set_diamond_quietly(row.diamond, state.keyed_all, state.animated)
            return
        self.active_property_changed.emit(property_id)
        set_diamond_quietly(row.diamond, not state.keyed_any, True)
        self.color_keys_toggled.emit(clip.id, [property_id])

    def color_animation_menu(self, property_id: str):
        """Menu du losange d'un réglage de couleur : celui de la transformation, plus « Supprimer l'animation de l'étalonnage »."""
        menu = self.animation_menu(property_id)
        clip = self.selected_clip
        if clip is not None and self._current_grade_keyframes:
            action = QAction(i18n.translate("animation.menu.clear_grade"), menu)
            action.triggered.connect(lambda: self.color_animation_cleared.emit(clip.id))
            actions = menu.actions()
            menu.insertAction(actions[1] if len(actions) > 1 else None, action)
        return menu

    def _open_color_animation_menu(self, name: str, position) -> None:
        row = self._color_keyframe_rows.get(name)
        if row is None or self.selected_clip is None:
            return
        property_id = scalar_property_id(name)
        self.active_property_changed.emit(property_id)
        self.color_animation_menu(property_id).exec(row.diamond.mapToGlobal(position))

    def _request_color_animation_clear(self) -> None:
        if self.selected_clip is not None:
            self.color_animation_cleared.emit(self.selected_clip.id)

    def _color_field_label(self, name: str) -> str:
        """Libellé du réglage dans la langue courante (jamais mis en cache : la langue change en cours d'exécution)."""
        key = self._color_field_keys.get(name)
        return i18n.translate(key) if key else name

    def _retranslate_color_keyframes(self) -> None:
        for name, row in self._color_keyframe_rows.items():
            label = self._color_field_label(name)
            row.previous.setAccessibleName(i18n.translate("a11y.keyframe.previous", property=label))
            row.diamond.setAccessibleName(i18n.translate("a11y.keyframe.toggle", property=label))
            row.next.setAccessibleName(i18n.translate("a11y.keyframe.next", property=label))
            caption = self._color_captions.get(name)
            if caption is not None:
                caption.setText(label)
        self._equalize_color_captions()
        self.color_animation_clear_button.setText(i18n.translate("color.animation.clear"))
        self.color_animation_clear_button.setToolTip(i18n.translate("color.animation.clear.tip"))
        self._sync_color_keyframes()


__all__ = ["ColorKeyframesMixin"]
