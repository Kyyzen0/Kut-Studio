"""Niveau d'usage de l'inspecteur (ADR-0006, étape 4, lot 3) : ce que chaque niveau montre directement.

Mécanisme (voir ``docs/usage-levels.md``) :

* les **groupes** entiers d'un niveau supérieur au niveau courant sont cachés, par la troisième condition de
  ``_apply_group_visibility`` (le point unique d'écriture de leur visibilité) ; le lien « Afficher plus d'options », en bas
  de l'inspecteur, les rend visibles d'un geste ;
* les **réglages** d'un groupe visible qui relèvent d'un niveau supérieur sont posés dans un :class:`LevelGate` (le conteneur
  remplace le widget dans son layout) et réunis sous un sous-bloc « Avancé » replié ; au niveau Avancé, aucun conteneur n'est
  caché ni aucun sous-bloc montré : l'inspecteur est celui d'avant les niveaux ;
* des **choix** de listes déroulantes (modes de fusion, formes de masque, types d'effets audio) sont cachés de la liste, jamais
  retirés : la valeur déjà choisie reste affichée.

Un niveau n'écrit que la visibilité de ces conteneurs, de ces groupes et de ces sous-blocs : jamais ``setEnabled`` (les
verrous de piste et de calque de ``show_clip`` restent prioritaires à tous les niveaux), jamais une valeur, jamais un signal.
Le niveau vient de la fenêtre principale (``MainWindow.usage_level`` / ``usage_level_changed``) par ``set_usage_level`` ;
sans fenêtre (tests, panneau isolé) c'est le niveau Avancé.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QPushButton, QWidget

from core.blend_modes import BlendMode
from core.compositing import MaskMode, MaskShape
from core.audio_effects_model import AudioEffectType
from core.usage_level import UsageLevel, coerce_usage_level
from ui.adaptive_layout import allow_shrinking
from ui.i18n import translate
from ui.properties_widgets.level_gate import AdvancedFold, LevelGate, limit_choices
from ui.theme import COLORS

SIMPLE, STUDIO, ADVANCED = UsageLevel.SIMPLE, UsageLevel.STUDIO, UsageLevel.ADVANCED

AUDIO_EFFECTS_STUDIO = frozenset({AudioEffectType.NORMALIZE.value, AudioEffectType.BASS_BOOST.value, AudioEffectType.TREBLE_BOOST.value})
"""Normaliser, basses et aigus : les effets audio du niveau Studio ; les autres sont avancés."""


@dataclass
class _Limit:
    group: QWidget
    combo: QComboBox
    levels: dict[object, UsageLevel]       # donnée du choix -> niveau minimal où il est listé
    move_to_visible: bool                  # liste « ajouter » : le choix courant ne doit pas être un choix caché


@dataclass
class _Gate:
    gate: LevelGate
    group: QWidget

    @property
    def minimum(self) -> UsageLevel:
        return self.gate.minimum or SIMPLE


class UsageLevelsMixin:
    """Mixin de ``PropertiesPanel`` : visibilité de l'inspecteur selon le niveau d'usage."""

    _usage_level: UsageLevel = ADVANCED
    _more_groups_shown: bool = False
    _group_levels: dict[QWidget, UsageLevel] = {}
    _level_gate_specs: list[_Gate] = []
    _level_limits: list[_Limit] = []
    _level_folds: dict[QWidget, list[AdvancedFold]] = {}

    # ------------------------------------------------------------------
    # Installation
    # ------------------------------------------------------------------

    def _install_usage_levels(self, layout) -> None:
        """Rassemble les conteneurs créés avec les contrôles, pose sous-blocs « Avancé » et lien du bas ; une fois les groupes construits."""
        self._group_levels = {
            self.transition_group: STUDIO, self.color_group: STUDIO, self.speed_group: STUDIO,
            self.effects_group: STUDIO, self.audio_effects_group: STUDIO, self.tracking_group: ADVANCED,
        }
        self._level_gate_specs = [_Gate(gate, self.movement_group) for gate in self._level_gates]
        for group, gates in (
            (self.speed_group, [self.time_section_gate]), (self.color_group, [self.color_advanced_gate]),
            (self.effects_group, self.effect_order_gates), (self.audio_group, [self.audio_pan_gate]),
            (self.compositing_group, self.compositing_group.level_gates), (self.graphics_group, self.graphics_group.level_gates),
            (self.subtitle_group, self.subtitle_editor.level_gates),
        ):
            self._level_gate_specs.extend(_Gate(gate, group) for gate in gates)
        self._level_limits = []
        for group, owner in ((self.compositing_group, self.compositing_group), (self.graphics_group, self.graphics_group),
                             (self.subtitle_group, self.subtitle_editor)):
            self._level_folds.setdefault(group, []).extend(owner.level_folds)
        for limit in self._limit_plan():
            self._level_limits.append(limit)
            limit.combo.currentIndexChanged.connect(self._apply_usage_level_choices)
        for fold in self._all_folds():
            fold.toggled.connect(self._refresh_usage_level)
        self.more_options_button = QPushButton()
        self.more_options_button.setObjectName("more_options_button")
        self.more_options_button.setCheckable(True)
        self.more_options_button.setFlat(True)
        self.more_options_button.setCursor(Qt.PointingHandCursor)
        self.more_options_button.setStyleSheet(
            f"QPushButton#more_options_button {{ border: none; background: transparent; color: {COLORS['accent']};"
            f" font-weight: 600; text-align: left; padding: 4px 0; }}"
            f"QPushButton#more_options_button:hover {{ text-decoration: underline; }}"
        )
        allow_shrinking(self.more_options_button, 120)
        self.more_options_button.toggled.connect(self._set_more_groups_shown)
        layout.addWidget(self.more_options_button)
        self._retranslate_usage_levels()
        self._refresh_usage_level()

    def _limit_plan(self) -> list[_Limit]:
        compositing = self.compositing_group
        return [
            _Limit(compositing, compositing.blend_mode, {BlendMode.ADD.value: ADVANCED, BlendMode.DIFFERENCE.value: ADVANCED}, False),
            _Limit(compositing, compositing.mask_shape, {MaskShape.POLYGON.value: ADVANCED, MaskShape.PATH.value: ADVANCED}, True),
            _Limit(compositing, compositing.mask_mode, {MaskMode.SUBTRACT.value: ADVANCED, MaskMode.INTERSECT.value: ADVANCED}, False),
            _Limit(self.audio_effects_group, self.audio_effect_type_combo,
                   {t.value: ADVANCED for t in AudioEffectType if t.value not in AUDIO_EFFECTS_STUDIO}, True),
        ]

    def _new_fold(self, group: QWidget, boundary: UsageLevel) -> AdvancedFold:
        """Sous-bloc « Avancé » d'un groupe, créé par son builder à la place voulue (juste devant ce qu'il révèle)."""
        fold = AdvancedFold(boundary)
        self._level_folds.setdefault(group, []).append(fold)
        return fold

    def _all_folds(self) -> list[AdvancedFold]:
        return [fold for folds in self._level_folds.values() for fold in folds]

    # ------------------------------------------------------------------
    # Niveau courant
    # ------------------------------------------------------------------

    @property
    def usage_level(self) -> UsageLevel:
        return self._usage_level

    def set_usage_level(self, level: object) -> None:
        """Niveau d'usage (``UsageLevel`` ou sa chaîne) ; une valeur inconnue est ignorée. Aucun widget n'est recréé."""
        chosen = coerce_usage_level(level)
        if chosen is None or chosen == self._usage_level:
            return
        self._usage_level = chosen
        self._more_groups_shown = False
        blocked = self.more_options_button.blockSignals(True)
        self.more_options_button.setChecked(False)
        self.more_options_button.blockSignals(blocked)
        self._refresh_usage_level()

    def more_groups_shown(self) -> bool:
        """Vrai si le lien « Afficher plus d'options » a révélé les groupes d'un niveau supérieur."""
        return self._more_groups_shown

    def _set_more_groups_shown(self, shown: bool) -> None:
        self._more_groups_shown = bool(shown)
        self._apply_group_visibility()
        self._apply_effect_keyframe_levels()
        self._apply_color_keyframe_levels()
        self._retranslate_usage_levels()

    def _level_allows_group(self, group: QWidget) -> bool:
        """3e condition de ``_apply_group_visibility`` : le groupe est du niveau courant, ou le lien l'a révélé."""
        return self._more_groups_shown or self._usage_level.at_least(self._group_levels.get(group, SIMPLE))

    def _level_allows(self, minimum: UsageLevel, group: QWidget) -> bool:
        """Un réglage de niveau ``minimum`` est affiché directement, ou un sous-bloc « Avancé » ouvert du groupe le révèle.

        Un pliant de palier ``boundary`` n'est montré que sous ce niveau et révèle les réglages de ce palier et au-delà.
        """
        level = self._usage_level
        return level.at_least(minimum) or any(
            fold.is_open() and not level.at_least(fold.boundary) and minimum.at_least(fold.boundary)
            for fold in self._level_folds.get(group, ())
        )

    def _advanced_animation_menu_shown(self, property_name: str = "") -> bool:
        """Interpolation, copier / coller et courbes du menu des losanges (avancés) ; le groupe est celui du paramètre."""
        if property_name.startswith("fx."):
            group = self.effects_group
        elif property_name.startswith("color."):
            group = self.color_group
        else:
            group = self.movement_group
        return self._level_allows(ADVANCED, group)

    # ------------------------------------------------------------------
    # Rafraîchissement (sans recréer de widget)
    # ------------------------------------------------------------------

    def _gate_shown(self, spec: _Gate) -> bool:
        if spec.gate.inverse_of is not None:
            return not self._level_allows(spec.gate.inverse_of.minimum or SIMPLE, spec.group)
        return self._level_allows(spec.minimum, spec.group)

    def _refresh_usage_level(self, *_ignored) -> None:
        for spec in self._level_gate_specs:
            spec.gate.set_shown(self._gate_shown(spec))
        self._apply_usage_level_choices()
        for fold in self._all_folds():
            fold.setVisible(not self._usage_level.at_least(fold.boundary))
        self._apply_effect_keyframe_levels()       # rangées ‹ ◆ › du réglage d'effet affiché (créées après coup, avec leur conteneur)
        self._apply_color_keyframe_levels()        # rangées ‹ ◆ › des réglages de couleur (créées avec leur conteneur)
        self._apply_group_visibility()
        self.more_options_button.setVisible(self._usage_level is not ADVANCED)

    def _apply_usage_level_choices(self, *_ignored) -> None:
        """Cache des listes les choix d'un niveau supérieur (sans les retirer : une valeur déjà choisie reste affichée)."""
        for limit in getattr(self, "_level_limits", ()):
            hidden = {data for data, level in limit.levels.items() if not self._level_allows(level, limit.group)}
            limit_choices(limit.combo, hidden, move_to_visible=limit.move_to_visible)

    # ------------------------------------------------------------------
    # Langue
    # ------------------------------------------------------------------

    def _retranslate_usage_levels(self) -> None:
        shown = self._more_groups_shown
        self.more_options_button.setText(translate("usage.inspector.fewer" if shown else "usage.inspector.more"))
        self.more_options_button.setAccessibleName(self.more_options_button.text())
        self.more_options_button.setToolTip(translate("usage.inspector.more.tip"))
        for group, folds in self._level_folds.items():
            for fold in folds:
                fold.setText(translate("usage.inspector.fold"))
                fold.setAccessibleName(translate("usage.inspector.fold.name", group=group.title()))
                fold.setToolTip(translate("usage.inspector.fold.tip"))


__all__ = ["AUDIO_EFFECTS_STUDIO", "UsageLevelsMixin"]
