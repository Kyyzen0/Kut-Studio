"""Construction des groupes de l'inspecteur (extraits de ``PropertiesPanel.__init__``)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.transitions import transition_type_values
from core.visual_effects import (
    ANIMATABLE_PROPERTIES,
)
from ui.properties_widgets.section_box import SectionBox
from ui.adaptive_layout import FlowLayout, allow_shrinking
from ui.design_system import Sizes, Spacing
from ui.i18n import translate
from ui.icons import IconButton, IconName
from ui.text_style_editor import TextStyleEditor
from ui.theme import COLORS, label_style
from ui.properties_widgets.common import (  # noqa: F401 - réexports de compatibilité
    _KEYFRAME_MATCH_TOLERANCE,
    _PROPERTY_RANGES,
)
from ui.properties_widgets.diamond_button import _DiamondButton
from ui.properties_widgets.level_gate import LevelGate, gated
from core.usage_level import UsageLevel
from ui.properties_widgets.tolerant_spin_box import TolerantDoubleSpinBox

TRACKING_TAB = 6
"""Onglet « Suivi »."""


class ConstructionMixin:
    """Mixin de ``PropertiesPanel`` : construction des groupes de l'inspecteur."""

    # Clés i18n des onglets, dans l'ordre historique des indices (Clip, Couleur, Effets, Audio, Graphiques,
    # Compositing, Suivi).
    _TAB_KEYS = (
        "inspector.tab.clip", "group.color", "rail.effects", "rail.audio", "rail.graphics",
        "inspector.tab.compositing", "tracking.title",
    )

    # ------------------------------------------------------------------
    # Langue
    # ------------------------------------------------------------------

    def _titled_group(self, key: str) -> QGroupBox:
        """``QGroupBox`` dont le titre suit la langue (réécrit par :meth:`retranslate`)."""
        # Les paramètres du projet ne servent qu'à l'occasion : repliés par défaut (la section se rouvre d'un clic, et le reste).
        group = SectionBox(translate(key), key=key, open_by_default=key != "group.project")
        self._group_titles.append((group, key))
        return group

    def _add_row(self, form: QFormLayout, key: str, field: QWidget) -> None:
        """Ligne de formulaire dont le libellé suit la langue (réécrit par :meth:`retranslate`)."""
        form.addRow(translate(key), field)
        self._row_labels.append((form, field, key))

    def _add_stacked_row(self, form: QFormLayout, key: str, field: QWidget) -> QLabel:
        """Libellé **au-dessus** de son champ, l'un et l'autre sur toute la largeur du formulaire.

        Pour un champ qui retourne à la ligne (un ``FlowLayout`` : rangée de boutons). À côté de son libellé, ``QFormLayout`` lui
        donnait une colonne étroite (la largeur « idéale » d'un ``FlowLayout`` est celle de son plus large élément) et calculait la
        hauteur de la ligne pour une autre largeur que celle où il le plaçait : après un changement d'onglet, la ligne suivante
        démarrait sous le milieu de celle-ci et la recouvrait (un défaut déjà présent, que des boutons plus larges ou une police plus
        large rendent visible). Sur toute la largeur, la largeur du champ est sans ambiguïté."""
        label = QLabel(translate(key))
        form.addRow(label)
        form.addRow(field)
        self._stacked_labels.append((label, key))
        return label

    def retranslate(self) -> None:
        """Onglets, titres de groupes et libellés de formulaire dans la langue courante."""
        for button, key in zip(self.inspector_tab_buttons, self._TAB_KEYS):
            button.setText(translate(key))
            button.setAccessibleName(translate(key))
        for group, key in self._group_titles:
            group.setTitle(translate(key))
        self.time_section.retranslate()
        self.frame_align.retranslate()
        for form, field, key in self._row_labels:
            label = form.labelForField(field)
            if label is not None:
                label.setText(translate(key))
        for label, key in self._stacked_labels:
            label.setText(translate(key))
        for widget in self._monitor_volume_tooltip_targets:
            widget.setToolTip(translate("tooltip.monitor_volume"))
        self._retranslate_color_keyframes()

    def _build_header(self, outer_layout):
        """En-tête : titre et barre d'onglets de l'inspecteur."""
        # Titre du panneau (header) + barre d'onglets.
        header = QWidget()
        header.setStyleSheet(
            f"background: {COLORS['panel']}; border-bottom: 1px solid {COLORS['border']};"
        )
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(Spacing.lg, Spacing.sm, Spacing.lg, 0)
        header_layout.setSpacing(Spacing.sm)
        title = QLabel(translate("inspector.title"))
        title.setStyleSheet(label_style(10, "muted", 800))
        title.hide()

        # Sept onglets, tous visibles (un menu « ••• » cachait Graphiques, Compositing et Suivi).
        # Utilisation de QPushButton ``checkable`` plutôt que
        # ``QListWidget`` pour garantir un affichage horizontal compact.
        self.inspector_tabs_row = QWidget()
        self.inspector_tabs_layout = FlowLayout(self.inspector_tabs_row, spacing=Spacing.xs)
        self.inspector_tabs_layout.setContentsMargins(0, 0, 0, Spacing.xs)
        self.inspector_tab_buttons: list[QPushButton] = []
        for index, key in enumerate(self._TAB_KEYS):
            button = QPushButton(translate(key))
            button.setObjectName("inspectorTab")
            button.setAccessibleName(translate(key))
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.setStyleSheet(
                f"QPushButton#inspectorTab {{ background: transparent;"
                f" color: {COLORS['muted']}; border: 1px solid transparent;"
                f" border-radius: 0; padding: 7px 7px;"
                f" font-weight: 600; font-size: 11px; }}"
                f"QPushButton#inspectorTab:hover {{ color: {COLORS['text']};"
                f" background: transparent; }}"
                f"QPushButton#inspectorTab:checked {{ color: {COLORS['accent']};"
                f" background: transparent; border: none;"
                f" border-bottom: 2px solid {COLORS['accent']}; }}"
                f"QPushButton#inspectorTab:focus {{ border: 1px solid {COLORS['accent']}; }}"
            )
            button.clicked.connect(
                lambda _checked=False, idx=index: self._select_inspector_tab(idx)
            )
            self.inspector_tab_buttons.append(button)

        # L'ordre visuel suit le geste attendu dans la maquette tout en conservant les indices historiques utilisés par le
        # contrôleur. Les sept onglets sont toujours des boutons visibles (plus de menu « ••• » : le Suivi, le Compositing et les
        # Graphiques se trouvent d'un clic) ; ils passent à la ligne quand l'inspecteur est étroit au lieu de se cacher.
        for index in (0, 1, 3, 2, 4, 5, 6):
            self.inspector_tabs_layout.addWidget(self.inspector_tab_buttons[index])
        header_layout.addWidget(self.inspector_tabs_row)
        outer_layout.addWidget(header)

    def _build_scroll_area(self, outer_layout):
        """Zone défilante portant les groupes ; retourne son layout."""
        # Pile de contenu (les widgets existants sont ajoutés plus bas
        # par appel direct ; ici on prépare la coquille).
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("properties_scroll_area")
        self.scroll_area.setFocusPolicy(Qt.NoFocus)  # simple conteneur : un arrêt de Tab invisible, sans rien à faire
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
        )
        # Le viewport est un widget distinct : sans fond explicite il
        # peint la couleur claire par défaut de la plateforme, ce qui
        # rendait les libellés de l'inspecteur illisibles sur fond clair.
        self.scroll_area.viewport().setStyleSheet(
            f"QWidget {{ background: {COLORS['panel']}; }}"
        )

        content = QWidget()
        content.setObjectName("properties_content")
        content.setStyleSheet(
            f"QWidget#properties_content {{ background: {COLORS['panel']}; "
            f"color: {COLORS['text']}; }}"
        )
        self.scroll_area.setWidget(content)
        outer_layout.addWidget(self.scroll_area)

        layout = QVBoxLayout(content)
        layout.setContentsMargins(Spacing.lg, Spacing.md, Spacing.lg, Spacing.md)
        layout.setSpacing(Spacing.md)
        return layout

    def _build_project_group(self, layout):
        """Groupe « Paramètres du projet »."""
        # ----- Paramètres du projet ------------------------------------
        project_group = self._titled_group("group.project")
        project_layout = QVBoxLayout(project_group)
        project_layout.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.sm)
        project_layout.setSpacing(Spacing.xs)

        # Valeurs de la séquence active : remplies par ``set_sequence_info`` (jamais des valeurs en dur).
        self._project_info_labels: dict[str, QLabel] = {}
        for name in ("state", "resolution", "format", "fps", "background"):
            lbl = QLabel()
            lbl.setMinimumHeight(20)
            lbl.setWordWrap(True)  # « État : Aucun clip sélectionné » fixait la largeur minimale de tout l'inspecteur
            lbl.setStyleSheet(label_style(12, "text", 500))
            project_layout.addWidget(lbl)
            self._project_info_labels[name] = lbl
        self.set_sequence_info(1920, 1080, 30.0)
        layout.addWidget(project_group)
        return project_group

    def set_sequence_info(self, width: int, height: int, fps: float) -> None:
        """Cadre et cadence de la séquence active, dans le groupe « Projet »."""
        from math import gcd

        from core.social_formats import format_for_frame

        entry = format_for_frame(width, height)
        divisor = gcd(int(width), int(height)) or 1
        ratio = entry.ratio if entry is not None else f"{int(width) // divisor}:{int(height) // divisor}"
        values = {
            "state": (translate("inspector.project.state"), translate("no_clip_selected")),
            "resolution": (translate("field.resolution"), translate("inspector.project.size", width=width, height=height)),
            "format": (translate("inspector.project.format"), ratio),
            "fps": (translate("field.fps"), translate("social.fps", fps=f"{float(fps):g}")),
            "background": (translate("field.background"), translate("inspector.project.black")),
        }
        for name, (field, value) in values.items():
            label = self._project_info_labels.get(name)
            if label is not None:
                label.setText(translate("inspector.project.field", field=field, value=value))

    def _build_clip_group(self, layout):
        """Groupe « Clip sélectionné »."""
        # ----- Clip sélectionné ---------------------------------------
        clip_group = self._titled_group("inspector.clip.title")
        clip_form = QFormLayout(clip_group)
        clip_form.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.sm)
        clip_form.setSpacing(Spacing.xs)
        clip_form.setLabelAlignment(Qt.AlignLeft)
        clip_form.setRowWrapPolicy(QFormLayout.WrapLongRows)  # « Aucun clip sélectionné » passe sous « Nom »
        self.clip_name = QLabel(translate("no_clip_selected"))
        self.clip_name.setWordWrap(True)  # un nom de fichier long ne doit pas fixer la largeur de l'inspecteur
        self.clip_duration = QLabel("--")
        self.clip_position = QLabel("--")
        for label in (self.clip_name, self.clip_duration, self.clip_position):
            label.setStyleSheet(label_style(12, "muted", 500))
        self.clip_name.setStyleSheet(label_style(13, "text", 700))
        self._add_row(clip_form, "field.name", self.clip_name)
        self._add_row(clip_form, "field.duration", self.clip_duration)
        self._add_row(clip_form, "field.position", self.clip_position)
        layout.addWidget(clip_group)
        return clip_group

    def _build_transition_group(self, layout):
        """Groupe « Transition » (visible sur sélection de transition)."""
        # ----- Transition sélectionnée --------------------------------
        self.transition_group = self._titled_group("inspector.transition.title")
        transition_form = QFormLayout(self.transition_group)
        transition_form.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.sm)
        transition_form.setSpacing(Spacing.xs)
        self.transition_type_combo = QComboBox()
        for value in transition_type_values():
            self.transition_type_combo.addItem(translate(f"transitions.preset.{value}.name"), value)
        self.transition_duration_spin = QDoubleSpinBox()
        self.transition_duration_spin.setRange(0.1, 5.0)
        self.transition_duration_spin.setDecimals(2)
        self.transition_duration_spin.setSingleStep(0.1)
        self.transition_duration_spin.setSuffix(" s")
        self.transition_from_label = QLabel("--")
        self.transition_to_label = QLabel("--")
        self.transition_track_label = QLabel("--")
        for label in (
            self.transition_from_label,
            self.transition_to_label,
            self.transition_track_label,
        ):
            label.setStyleSheet(label_style(11, "muted", 500))
        self.remove_transition_button = self._make_action_button(
            IconName.REMOVE, translate("inspector.transition.remove"), translate("inspector.transition.remove_tip")
        )
        self._add_row(transition_form, "common.type", self.transition_type_combo)
        self._add_row(transition_form, "field.duration", self.transition_duration_spin)
        self._add_row(transition_form, "inspector.transition.outgoing", self.transition_from_label)
        self._add_row(transition_form, "inspector.transition.incoming", self.transition_to_label)
        self._add_row(transition_form, "inspector.transition.track", self.transition_track_label)
        transition_form.addRow("", self.remove_transition_button)
        self.transition_type_combo.currentIndexChanged.connect(self._on_transition_type_changed)
        self.transition_duration_spin.valueChanged.connect(self._on_transition_duration_changed)
        self.remove_transition_button.clicked.connect(self._on_transition_remove)
        layout.addWidget(self.transition_group)

    def _build_volume_group(self, layout, update_volume):
        """Groupe « Audio » (curseur de volume du moniteur).

        Le curseur ne règle que la lecture de l'aperçu (``QAudioOutput.setVolume``) : rien n'est enregistré dans le
        projet ni appliqué à l'export, d'où son libellé et son infobulle. ``QAudioOutput`` plafonne à 1.0 : au-delà de
        100 %, rien ne changerait à l'oreille.
        """
        # ----- Audio ---------------------------------------------------
        audio_group = self._titled_group("group.audio")
        audio_form = QFormLayout(audio_group)
        audio_form.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.sm)
        self.volume_slider, volume_row, self.volume_value = self.make_slider(
            0, 100, 100, suffix=" %"
        )
        # Libellé au-dessus du curseur : à côté, « Volume du moniteur » élargissait la colonne des libellés et donc la
        # largeur minimale de tout l'inspecteur (coupé dans les petites fenêtres, voir test_ui_small_windows).
        monitor_label = self._add_stacked_row(audio_form, "field.monitor_volume", volume_row)
        # Le curseur et sa valeur n'ont pas d'infobulle propre : Qt affiche celle de la ligne qui les contient.
        self._monitor_volume_tooltip_targets = (volume_row, monitor_label)
        for widget in self._monitor_volume_tooltip_targets:
            widget.setToolTip(translate("tooltip.monitor_volume"))
        layout.addWidget(audio_group)
        self.volume_slider.valueChanged.connect(update_volume)
        return audio_group

    def _build_movement_group(self, layout):
        """Groupe « Mouvement » (position, échelle, rotation, opacité)."""
        # ----- Mouvement (tâche 13) ------------------------------------
        self.movement_group = self._titled_group("group.movement")
        movement_layout = QVBoxLayout(self.movement_group)
        movement_layout.setContentsMargins(
            Spacing.md, Spacing.md, Spacing.md, Spacing.md
        )
        movement_layout.setSpacing(Spacing.xs)
        self._spin_boxes: dict[str, QDoubleSpinBox] = {}
        self.keyframe_block = LevelGate()       # niveau d'usage (Studio) : losanges ‹◆› de toutes les propriétés, en fin de groupe
        self.keyframe_block.minimum = UsageLevel.STUDIO
        self._level_gates.append(self.keyframe_block)
        keyframe_layout = self.keyframe_block.layout()
        keyframe_layout.setSpacing(Spacing.xs)
        self._diamonds: dict[str, _DiamondButton] = {}
        self._keyframe_nav_buttons: dict[str, tuple[QToolButton, QToolButton]] = {}
        self._slider_widgets: dict[str, QSlider] = {}
        for property_name in ANIMATABLE_PROPERTIES:
            low, high, step = _PROPERTY_RANGES[property_name]
            default = self._default_value_for(property_name)
            row = QWidget()
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(Spacing.xs)
            label = QLabel(self._human_label(property_name))
            label.setStyleSheet(label_style(11, "muted", 600))
            label.setFixedWidth(76)
            row_layout.addWidget(label)

            spin = TolerantDoubleSpinBox()     # « 0.5 » et « 0,5 » valent la même chose
            spin.setDecimals(2 if property_name != "rotation" else 1)
            spin.setRange(low, high)
            spin.setSingleStep(step)
            spin.setValue(default)
            spin.setMinimumWidth(82)
            spin.setEnabled(False)
            spin.valueChanged.connect(self._make_value_changed_handler(property_name))
            self._spin_boxes[property_name] = spin
            row_layout.addWidget(spin)

            if property_name in {"opacity", "scale", "rotation"}:
                slider = QSlider(Qt.Horizontal)
                slider_min, slider_max = self._slider_range(property_name)
                slider.setRange(slider_min, slider_max)
                slider.setValue(self._slider_position(property_name, default))
                slider.setMinimumWidth(82)
                slider.setEnabled(False)
                slider.valueChanged.connect(self._make_slider_handler(property_name))
                self._slider_widgets[property_name] = slider
                row_layout.addWidget(slider, 1)

            # Animation : ‹ ◆ › — précédent, ajouter / retirer, suivant. Le
            # reste (interpolation, copier/coller, courbes) est au clic droit.
            # Niveau d'usage : les losanges sont Studio ; ils sont créés ici mais rangés dans le bloc « Images-clés » en fin de
            # groupe (un réglage de base n'est jamais sous le pliant « Avancé »).
            animation_row = QHBoxLayout()
            animation_row.setContentsMargins(0, 0, 0, 0)
            animation_row.setSpacing(2)
            animation_row.addWidget(QLabel(label.text()))
            previous_button = QToolButton()
            previous_button.setObjectName("iconOnly")  # padding nul : sinon le thème rogne la flèche
            previous_button.setText("‹")
            previous_button.setFixedSize(18, 22)
            previous_button.setEnabled(False)
            previous_button.clicked.connect(
                lambda _checked=False, name=property_name: self._on_keyframe_navigation(name, -1)
            )
            diamond = _DiamondButton(property_name)
            diamond.clicked.connect(
                lambda _checked=False, name=property_name: self._on_diamond_clicked(name)
            )
            diamond.setContextMenuPolicy(Qt.CustomContextMenu)
            diamond.customContextMenuRequested.connect(
                lambda position, name=property_name: self._open_animation_menu(name, position)
            )
            diamond.setEnabled(False)
            next_button = QToolButton()
            next_button.setObjectName("iconOnly")
            next_button.setText("›")
            next_button.setFixedSize(18, 22)
            next_button.setEnabled(False)
            next_button.clicked.connect(
                lambda _checked=False, name=property_name: self._on_keyframe_navigation(name, 1)
            )
            # Noms accessibles : ces boutons n'ont que « ‹ », « › » et un losange.
            previous_button.setAccessibleName(translate("a11y.keyframe.previous", property=label.text()))
            diamond.setAccessibleName(translate("a11y.keyframe.toggle", property=label.text()))
            next_button.setAccessibleName(translate("a11y.keyframe.next", property=label.text()))
            self._diamonds[property_name] = diamond
            self._keyframe_nav_buttons[property_name] = (previous_button, next_button)
            for widget in (previous_button, diamond, next_button):
                animation_row.addWidget(widget)
            animation_row.addStretch(1)
            keyframe_layout.addLayout(animation_row)
            movement_layout.addWidget(row)

        reset_button = IconButton(
            icon=IconName.RESET,
            tooltip=translate("action.reset_movement"),
            size=Sizes.icon_button,
        )
        reset_button.setText("  " + translate("action.reset_movement"))
        reset_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        reset_button.clicked.connect(self._emit_reset)
        reset_button.setEnabled(False)
        allow_shrinking(reset_button, 120)  # le libellé est le plus long de l'onglet : dernier recours si la place manque
        reset_button.setSizePolicy(reset_button.sizePolicy().horizontalPolicy(),
                                   reset_button.sizePolicy().verticalPolicy())
        self.reset_movement_button = reset_button
        # Transformation avancée (motion graphics) : repliée par défaut.
        from ui.properties_widgets.advanced_transform import AdvancedTransformEditor

        self.advanced_transform = AdvancedTransformEditor()
        self.advanced_transform.value_changed.connect(self._emit_advanced_transform)
        self.advanced_transform.keyframe_toggled.connect(self._emit_advanced_keyframe)
        # Niveau d'usage : « Remplir le cadre » (Simple) est un relais de la case de la transformation avancée (Avancé).
        self.fill_frame_gate = LevelGate()
        self.fill_frame_gate.minimum = UsageLevel.SIMPLE
        self.advanced_transform_gate = gated(self.advanced_transform, UsageLevel.ADVANCED)
        self._level_gates.append(self.advanced_transform_gate)
        self.fill_frame_gate.inverse_of = self.advanced_transform_gate
        self._level_gates.append(self.fill_frame_gate)
        self.fill_frame_quick = self.advanced_transform.make_fill_relay()
        self.fill_frame_gate.hold(self.fill_frame_quick, UsageLevel.SIMPLE)
        from ui.properties_widgets.frame_align_row import FrameAlignRow

        self.frame_align = FrameAlignRow()      # ADR-0006 lot 1c : six boutons ; la fenêtre principale les relie
        self.frame_align_gate = gated(self.frame_align, UsageLevel.STUDIO)
        self._level_gates.append(self.frame_align_gate)
        # Ordre : réglages de base (valeurs, « Remplir le cadre », « Réinitialiser »), puis ce qui relève des niveaux supérieurs,
        # chacun derrière son pliant « Avancé » : images-clés et alignement (Studio), transformation avancée (Avancé).
        movement_layout.addWidget(self.fill_frame_gate)
        movement_layout.addWidget(reset_button)
        movement_layout.addWidget(self._new_fold(self.movement_group, UsageLevel.STUDIO))
        movement_layout.addWidget(self.keyframe_block)
        movement_layout.addWidget(self.frame_align_gate)
        movement_layout.addWidget(self._new_fold(self.movement_group, UsageLevel.ADVANCED))
        movement_layout.addWidget(self.advanced_transform_gate)
        self._diamond_was_checked = {name: False for name in self._diamonds}
        self._allow_property_signals = True

        layout.addWidget(self.movement_group)
        self.movement_group.setEnabled(False)

    def _build_subtitle_group(self, layout):
        """Groupe « Sous-titre » (style et contenu)."""
        # ----- Sous-titre (style + contenu, tâche 24) ------------------
        self.subtitle_group = self._titled_group("inspector.subtitle.title")
        subtitle_layout = QVBoxLayout(self.subtitle_group)
        subtitle_layout.setContentsMargins(
            Spacing.md, Spacing.md, Spacing.md, Spacing.md
        )
        self.subtitle_editor = TextStyleEditor(self)
        subtitle_layout.addWidget(self.subtitle_editor)
        self.subtitle_editor.content_changed.connect(
            self._on_subtitle_content_changed
        )
        self.subtitle_editor.style_changed.connect(
            self._on_subtitle_style_changed
        )
        self.subtitle_editor.reset_requested.connect(
            self._on_subtitle_style_reset
        )
        # La visibilité initiale est pilotée par la sélection de clip :
        # voir ``_set_group_condition`` / ``_apply_group_visibility``.
        layout.addWidget(self.subtitle_group)

    def _register_inspector_groups(self, project_group, clip_group, audio_group):
        """Associe les groupes aux onglets et à leurs conditions de visibilité."""
        # ----- Onglets : filtrage par catégorie -----------------------
        # Le panneau gagne une barre d'onglets : Inspecteur (par
        # défaut, tout visible), Couleur (color_group + project),
        # Effets (transition + mouvement), Audio (audio_group +
        # volume), Graphiques (géométrie + sous-titres).
        self._tab_groups: dict[int, list[QWidget]] = {
            0: [
                project_group,
                clip_group,
                self.transition_group,
                self.color_group,
                self.movement_group,
                self.speed_group,
                self.graphics_group,
                audio_group,
                self.audio_group,
                self.subtitle_group,
                self.effects_group,
                self.audio_effects_group,
                self.compositing_group,
            ],
            1: [project_group, clip_group, self.color_group],  # Couleur
            2: [project_group, self.movement_group,
                self.transition_group, self.speed_group,
                self.effects_group],  # Effets
            3: [project_group, audio_group, self.audio_group,
                self.audio_effects_group],  # Audio
            4: [project_group, clip_group, self.movement_group,
                self.graphics_group, self.subtitle_group],  # Graphiques
            5: [project_group, clip_group, self.compositing_group],
            TRACKING_TAB: [clip_group, self.tracking_group],
        }
        all_groups = [project_group, clip_group, self.transition_group,
                      self.color_group, self.movement_group, self.speed_group,
                      self.graphics_group, audio_group, self.audio_group,
                      self.subtitle_group,
                      self.effects_group, self.audio_effects_group,
                      self.compositing_group, self.tracking_group]
        self._all_inspector_groups = all_groups
        # Certains groupes ont en plus une visibilité *conditionnelle*
        # pilotée par la sélection (``show_clip`` / ``show_transition``) :
        # le groupe Transition n'a de sens qu'une transition sélectionnée,
        # le groupe Sous-titre qu'un clip de sous-titres. Cette
        # condition est indépendante de l'onglet ; on la stocke à part
        # pour que le filtre d'onglets ne l'écrase pas. Un groupe n'est
        # visible que si l'onglet le contient *et* que sa propre
        # condition est remplie.
        self._group_conditional: dict[QWidget, bool] = {
            self.transition_group: False,
            self.subtitle_group: False,
            self.graphics_group: False,
        }
        self._active_inspector_tab: int = 0
        self._on_inspector_tab_changed(0)
