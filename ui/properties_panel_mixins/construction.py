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
    QMenu,
    QPushButton,
    QScrollArea,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.time_remapping import MIN_SPEED, MAX_SPEED
from core.visual_effects import (
    ANIMATABLE_PROPERTIES,
)
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
from ui.properties_widgets.common import _PROPERTY_RANGES
from ui.properties_widgets.diamond_button import _DiamondButton

class ConstructionMixin:
    """Mixin de ``PropertiesPanel`` : construction des groupes de l'inspecteur."""

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
        title = QLabel("INSPECTEUR")
        title.setStyleSheet(label_style(10, "muted", 800))
        title.hide()

        # Onglets principaux : Clip / Couleur / Audio / Effets. Les outils
        # spécialisés restent disponibles dans un menu compact afin que
        # l'inspecteur conserve une vraie largeur de travail.
        # Utilisation de QPushButton ``checkable`` plutôt que
        # ``QListWidget`` pour garantir un affichage horizontal compact.
        self.inspector_tabs_row = QWidget()
        self.inspector_tabs_layout = QHBoxLayout(self.inspector_tabs_row)
        self.inspector_tabs_layout.setContentsMargins(0, 0, 0, 0)
        self.inspector_tabs_layout.setSpacing(Spacing.xs)
        self.inspector_tab_buttons: list[QPushButton] = []
        labels = ("Clip", "Couleur", "Effets", "Audio", "Graphiques", "Compositing")
        for index, label in enumerate(labels):
            button = QPushButton(label)
            button.setObjectName("inspectorTab")
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
            )
            button.clicked.connect(
                lambda _checked=False, idx=index: self._select_inspector_tab(idx)
            )
            self.inspector_tab_buttons.append(button)

        # L'ordre visuel suit le geste attendu dans la maquette tout en
        # conservant les indices historiques utilisés par le contrôleur.
        for index in (0, 1, 3, 2):
            self.inspector_tabs_layout.addWidget(self.inspector_tab_buttons[index])
        # Graphiques et Compositing passent par le menu « ••• » : leurs
        # boutons gardent l'état coché mais restent rattachés au panneau
        # pour ne pas devenir des fenêtres orphelines.
        for index in (4, 5):
            self.inspector_tab_buttons[index].setParent(self.inspector_tabs_row)
            self.inspector_tab_buttons[index].hide()

        self.inspector_more_button = QToolButton()
        self.inspector_more_button.setObjectName("inspectorMore")
        self.inspector_more_button.setText("•••")
        self.inspector_more_button.setToolTip("Outils spécialisés")
        self.inspector_more_button.setAccessibleName("Outils spécialisés")
        self.inspector_more_button.setStyleSheet(
            f"QToolButton {{ color: {COLORS['muted']}; background: transparent;"
            f" border: none; border-radius: 4px; padding: 5px 6px; }}"
            f"QToolButton:hover {{ color: {COLORS['text']};"
            f" background: {COLORS['surface_hover']}; }}"
            f"QToolButton[active='true'] {{ color: {COLORS['accent']}; }}"
        )
        self.inspector_more_button.setPopupMode(QToolButton.InstantPopup)
        more_menu = QMenu(self.inspector_more_button)
        graphics_action = more_menu.addAction("Graphiques")
        graphics_action.triggered.connect(
            lambda _checked=False: self._select_inspector_tab(4)
        )
        compositing_action = more_menu.addAction("Compositing")
        compositing_action.triggered.connect(
            lambda _checked=False: self._select_inspector_tab(5)
        )
        self.inspector_more_button.setMenu(more_menu)
        self.inspector_tabs_layout.addWidget(self.inspector_more_button)
        self.inspector_tabs_layout.addStretch(1)
        header_layout.addWidget(self.inspector_tabs_row)
        outer_layout.addWidget(header)

    def _build_scroll_area(self, outer_layout):
        """Zone défilante portant les groupes ; retourne son layout."""
        # Pile de contenu (les widgets existants sont ajoutés plus bas
        # par appel direct ; ici on prépare la coquille).
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("properties_scroll_area")
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
        project_group = QGroupBox("Paramètres du projet")
        project_group.setStyleSheet(self.group_style())
        project_layout = QVBoxLayout(project_group)
        project_layout.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.sm)
        project_layout.setSpacing(Spacing.xs)

        project_fields = [
            ("État", "Aucun clip sélectionné"),
            ("Résolution", "1920 × 1080"),
            ("Format", "16:9"),
            ("Fréquence", "30 fps"),
            ("Fond", "#000000"),
        ]
        for field, value in project_fields:
            lbl = QLabel(f"{field} : {value}")
            lbl.setMinimumHeight(20)
            lbl.setStyleSheet(label_style(12, "text", 500))
            project_layout.addWidget(lbl)
        layout.addWidget(project_group)
        return project_group

    def _build_clip_group(self, layout):
        """Groupe « Clip sélectionné »."""
        # ----- Clip sélectionné ---------------------------------------
        clip_group = QGroupBox("Clip sélectionné")
        clip_group.setStyleSheet(self.group_style())
        clip_form = QFormLayout(clip_group)
        clip_form.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.sm)
        clip_form.setSpacing(Spacing.xs)
        clip_form.setLabelAlignment(Qt.AlignLeft)
        self.clip_name = QLabel("Aucun clip sélectionné")
        self.clip_duration = QLabel("--")
        self.clip_position = QLabel("--")
        for label in (self.clip_name, self.clip_duration, self.clip_position):
            label.setStyleSheet(label_style(12, "muted", 500))
        self.clip_name.setStyleSheet(label_style(13, "text", 700))
        clip_form.addRow("Nom", self.clip_name)
        clip_form.addRow("Durée", self.clip_duration)
        clip_form.addRow("Position", self.clip_position)
        layout.addWidget(clip_group)
        return clip_group

    def _build_transition_group(self, layout):
        """Groupe « Transition » (visible sur sélection de transition)."""
        # ----- Transition sélectionnée --------------------------------
        self.transition_group = QGroupBox("Transition")
        self.transition_group.setStyleSheet(self.group_style())
        transition_form = QFormLayout(self.transition_group)
        transition_form.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.sm)
        transition_form.setSpacing(Spacing.xs)
        self.transition_type_combo = QComboBox()
        self.transition_type_combo.addItem("Fondu enchaîné", "crossfade")
        self.transition_type_combo.addItem("Fondu au noir", "fade_black")
        self.transition_type_combo.addItem("Balayage gauche", "wipe_left")
        self.transition_type_combo.addItem("Balayage droite", "wipe_right")
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
            IconName.REMOVE, "Supprimer la transition", "Supprimer uniquement la transition"
        )
        transition_form.addRow("Type", self.transition_type_combo)
        transition_form.addRow("Durée", self.transition_duration_spin)
        transition_form.addRow("Clip sortant", self.transition_from_label)
        transition_form.addRow("Clip entrant", self.transition_to_label)
        transition_form.addRow("Piste", self.transition_track_label)
        transition_form.addRow("", self.remove_transition_button)
        self.transition_type_combo.currentIndexChanged.connect(self._on_transition_type_changed)
        self.transition_duration_spin.valueChanged.connect(self._on_transition_duration_changed)
        self.remove_transition_button.clicked.connect(self._on_transition_remove)
        layout.addWidget(self.transition_group)

    def _build_volume_group(self, layout, update_volume):
        """Groupe « Audio » (curseur de volume)."""
        # ----- Audio ---------------------------------------------------
        audio_group = QGroupBox("Audio")
        audio_group.setStyleSheet(self.group_style())
        audio_form = QFormLayout(audio_group)
        audio_form.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.sm)
        self.volume_slider, volume_row, self.volume_value = self.make_slider(
            0, 200, 100, suffix=" %"
        )
        audio_form.addRow("Volume", volume_row)
        layout.addWidget(audio_group)
        self.volume_slider.valueChanged.connect(update_volume)
        return audio_group

    def _build_speed_group(self, layout):
        """Groupe « Vitesse et durée » (remappage temporel)."""
        # ----- Vitesse et durée (tâche 18) -----------------------------
        self.speed_group = QGroupBox(translate("group.speed_and_duration"))
        self.speed_group.setStyleSheet(self.group_style())
        speed_form = QFormLayout(self.speed_group)
        speed_form.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.sm)
        speed_form.setSpacing(Spacing.xs)
        speed_form.setLabelAlignment(Qt.AlignLeft)

        # Vitesse numérique
        self.speed_spinbox = QDoubleSpinBox()
        self.speed_spinbox.setDecimals(2)
        self.speed_spinbox.setRange(MIN_SPEED, MAX_SPEED)
        self.speed_spinbox.setSingleStep(0.1)
        self.speed_spinbox.setValue(1.0)
        self.speed_spinbox.setMinimumWidth(70)
        self.speed_spinbox.setEnabled(False)
        self.speed_spinbox.valueChanged.connect(self._on_speed_changed)
        speed_form.addRow(translate("field.speed"), self.speed_spinbox)

        # Boutons de preset de vitesse
        speed_presets = QWidget()
        speed_presets_layout = QHBoxLayout(speed_presets)
        speed_presets_layout.setContentsMargins(0, 0, 0, 0)
        speed_presets_layout.setSpacing(Spacing.xs)

        self.speed_0_25x_button = self._make_action_button(
            None, translate("action.speed_0.25x"), translate("tooltip.speed_0.25x")
        )
        self.speed_0_5x_button = self._make_action_button(
            None, translate("action.speed_0.5x"), translate("tooltip.speed_0.5x")
        )
        self.speed_1x_button = self._make_action_button(
            None, translate("action.speed_1x"), translate("tooltip.speed_1x")
        )
        self.speed_2x_button = self._make_action_button(
            None, translate("action.speed_2x"), translate("tooltip.speed_2x")
        )
        self.speed_4x_button = self._make_action_button(
            None, translate("action.speed_4x"), translate("tooltip.speed_4x")
        )

        # Connecter les boutons de preset
        self.speed_0_25x_button.clicked.connect(lambda: self.speed_spinbox.setValue(0.25))
        self.speed_0_5x_button.clicked.connect(lambda: self.speed_spinbox.setValue(0.5))
        self.speed_1x_button.clicked.connect(lambda: self.speed_spinbox.setValue(1.0))
        self.speed_2x_button.clicked.connect(lambda: self.speed_spinbox.setValue(2.0))
        self.speed_4x_button.clicked.connect(lambda: self.speed_spinbox.setValue(4.0))
        
        for btn in [
            self.speed_0_25x_button,
            self.speed_0_5x_button,
            self.speed_1x_button,
            self.speed_2x_button,
            self.speed_4x_button,
        ]:
            btn.setMinimumWidth(40)
            btn.setEnabled(False)
            speed_presets_layout.addWidget(btn)

        speed_form.addRow("Presets", speed_presets)

        # Bouton Reverse
        self.reverse_button = IconButton(
            icon=None, tooltip=translate("tooltip.reverse"), size=Sizes.icon_button
        )
        self.reverse_button.setText("  " + translate("field.reverse"))
        self.reverse_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.reverse_button.setCheckable(True)
        self.reverse_button.setMinimumHeight(Sizes.button_md)
        self.reverse_button.setEnabled(False)
        self.reverse_button.toggled.connect(self._on_reverse_toggled)
        speed_form.addRow(translate("field.reverse"), self.reverse_button)

        # Freeze frame
        self.freeze_frame_button = self._make_action_button(
            None, translate("field.freeze_frame"), translate("tooltip.freeze_frame")
        )
        self.freeze_frame_button.setEnabled(False)
        self.freeze_frame_button.clicked.connect(self._on_freeze_frame_clicked)
        speed_form.addRow(translate("field.freeze_frame"), self.freeze_frame_button)

        # Durée freeze frame (visible uniquement en mode freeze)
        self.freeze_duration_spinbox = QDoubleSpinBox()
        self.freeze_duration_spinbox.setDecimals(2)
        self.freeze_duration_spinbox.setRange(0.01, 3600.0)  # 0.01s à 1h
        self.freeze_duration_spinbox.setSingleStep(0.1)
        self.freeze_duration_spinbox.setValue(1.0)
        self.freeze_duration_spinbox.setMinimumWidth(70)
        self.freeze_duration_spinbox.setEnabled(False)
        self.freeze_duration_spinbox.valueChanged.connect(self._on_freeze_duration_changed)
        self.freeze_duration_label = QLabel("1,00 s")
        self.freeze_duration_label.setStyleSheet(label_style(11, "muted", 500))
        self.freeze_duration_label.setVisible(False)

        freeze_row = QWidget()
        freeze_row_layout = QHBoxLayout(freeze_row)
        freeze_row_layout.setContentsMargins(0, 0, 0, 0)
        freeze_row_layout.setSpacing(Spacing.sm)
        freeze_row_layout.addWidget(self.freeze_duration_spinbox)
        freeze_row_layout.addWidget(self.freeze_duration_label)
        freeze_row.setVisible(False)
        speed_form.addRow(translate("field.freeze_duration"), freeze_row)
        self.freeze_duration_row = freeze_row

        # Bouton de réinitialisation
        self.reset_speed_button = self._make_action_button(
            None, translate("action.reset_speed"), translate("tooltip.reset_speed")
        )
        self.reset_speed_button.setEnabled(False)
        self.reset_speed_button.clicked.connect(self._on_time_remapping_reset)
        speed_form.addRow("", self.reset_speed_button)

        # Affichage des durées source et timeline
        self.source_duration_label = QLabel("Source: --")
        self.source_duration_label.setStyleSheet(label_style(11, "muted", 500))
        self.timeline_duration_label = QLabel("Timeline: --")
        self.timeline_duration_label.setStyleSheet(label_style(11, "muted", 500))
        duration_info = QWidget()
        duration_layout = QHBoxLayout(duration_info)
        duration_layout.setContentsMargins(0, 0, 0, 0)
        duration_layout.setSpacing(Spacing.md)
        duration_layout.addWidget(self.source_duration_label)
        duration_layout.addWidget(self.timeline_duration_label)
        duration_layout.addStretch()
        speed_form.addRow("", duration_info)

        layout.addWidget(self.speed_group)

    def _build_movement_group(self, layout):
        """Groupe « Mouvement » (position, échelle, rotation, opacité)."""
        # ----- Mouvement (tâche 13) ------------------------------------
        self.movement_group = QGroupBox("Mouvement")
        self.movement_group.setStyleSheet(self.group_style())
        movement_layout = QVBoxLayout(self.movement_group)
        movement_layout.setContentsMargins(
            Spacing.md, Spacing.md, Spacing.md, Spacing.md
        )
        movement_layout.setSpacing(Spacing.xs)
        self._spin_boxes: dict[str, QDoubleSpinBox] = {}
        self._diamonds: dict[str, _DiamondButton] = {}
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

            spin = QDoubleSpinBox()
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

            diamond = _DiamondButton(property_name)
            diamond.clicked.connect(
                lambda _checked=False, name=property_name: self._on_diamond_clicked(name)
            )
            diamond.setEnabled(False)
            self._diamonds[property_name] = diamond
            row_layout.addWidget(diamond)
            movement_layout.addWidget(row)

        reset_button = IconButton(
            icon=IconName.RESET,
            tooltip="Réinitialiser le mouvement",
            size=Sizes.icon_button,
        )
        reset_button.setText("  Réinitialiser le mouvement")
        reset_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        reset_button.clicked.connect(self._emit_reset)
        reset_button.setEnabled(False)
        reset_button.setMinimumWidth(0)
        reset_button.setSizePolicy(reset_button.sizePolicy().horizontalPolicy(),
                                   reset_button.sizePolicy().verticalPolicy())
        self.reset_movement_button = reset_button
        movement_layout.addWidget(reset_button)
        self._diamond_was_checked = {name: False for name in self._diamonds}
        self._allow_property_signals = True

        layout.addWidget(self.movement_group)
        self.movement_group.setEnabled(False)

    def _build_subtitle_group(self, layout):
        """Groupe « Sous-titre » (style et contenu)."""
        # ----- Sous-titre (style + contenu, tâche 24) ------------------
        self.subtitle_group = QGroupBox("Sous-titre")
        self.subtitle_group.setStyleSheet(self.group_style())
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
        }
        all_groups = [project_group, clip_group, self.transition_group,
                      self.color_group, self.movement_group, self.speed_group,
                      self.graphics_group, audio_group, self.audio_group,
                      self.subtitle_group,
                      self.effects_group, self.audio_effects_group,
                      self.compositing_group]
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
