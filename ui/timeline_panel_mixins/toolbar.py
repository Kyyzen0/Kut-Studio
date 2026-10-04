"""Barre d'outils de la timeline : construction, thème, bouton lecture."""

from __future__ import annotations


from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ui.design_system import Sizes, Spacing, Typography, Weights
from ui.i18n import translate
from ui.icons import IconButton, IconName
from ui.theme import label_style, monospace_font_family
from ui.timeline_widgets.common import (
    _current_palette,
    _minus_icon,
)
from ui.timeline_widgets.common import _current_palette, _minus_icon

class ToolbarMixin:
    """Mixin de ``TimelinePanel`` : barre d'outils de la timeline : construction, thème, bouton lecture."""

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_toolbar(self) -> QWidget:
        """Construit la barre d'outils supérieure (transport + actions)."""
        from PySide6.QtWidgets import QHBoxLayout
        palette = _current_palette()

        bar = QWidget()
        bar.setFixedHeight(Sizes.timeline_header_height)
        bar.setObjectName("timeline_toolbar")
        bar.setStyleSheet(
            f"QWidget#timeline_toolbar {{ background: {palette.panel}; "
            f"border-bottom: 1px solid {palette.border}; }}"
        )
        # Layout principal : horizontal. Une rangée unique, dense et lisible.
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(Spacing.md, Spacing.xs, Spacing.md, Spacing.xs)
        layout.setSpacing(Spacing.sm)
        layout.setAlignment(Qt.AlignVCenter)

        # --- Bloc gauche : transport + horloge -------------------------
        left_block = QWidget()
        left_layout = QHBoxLayout(left_block)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(Spacing.sm)
        left_layout.setAlignment(Qt.AlignVCenter)

        self.play_button = IconButton(
            icon=IconName.PLAY,
            tooltip=translate("shortcuts.command.play_pause"),
            accent=True,
            size=Sizes.icon_button,
        )
        self.play_button.clicked.connect(self._on_play_clicked)
        left_layout.addWidget(self.play_button)
        self.play_button.hide()

        time_box = QWidget()
        time_layout = QVBoxLayout(time_box)
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.setSpacing(2)
        self.time_label = QLabel("00:00")
        self.time_label.setStyleSheet(
            f"color: {palette.accent}; font-weight: {Weights.bold}; font-size: {Typography.body_lg}px;"
            f" {monospace_font_family()} letter-spacing: 1px;"
        )
        # Rangée combinée : timecode + durée totale séparées par un slash.
        self.timecode_label = QLabel("00:00:00")
        self.timecode_label.setStyleSheet(
            f"color: {palette.muted}; font-size: 11px;"
            f" {monospace_font_family()}"
        )
        self.total_time_label = QLabel("/ 00:00")
        self.total_time_label.setStyleSheet(
            f"color: {palette.muted}; font-size: 11px;"
            f" {monospace_font_family()}"
        )
        time_row = QWidget()
        time_row_layout = QHBoxLayout(time_row)
        time_row_layout.setContentsMargins(0, 0, 0, 0)
        time_row_layout.setSpacing(4)
        time_row_layout.addWidget(self.timecode_label)
        time_row_layout.addWidget(self.total_time_label)
        time_row_layout.addStretch(1)
        time_layout.addWidget(self.time_label)
        time_layout.addWidget(time_row)
        left_layout.addWidget(time_box)
        time_box.hide()

        # Petit séparateur vertical pour aérer visuellement.
        left_layout.addSpacing(Spacing.sm)

        self.snap_button = IconButton(
            icon=IconName.SNAP,
            tooltip=translate("tooltip.snap"),
            checkable=True,
            checked=True,
            size=Sizes.icon_button,
        )
        self.snap_button.toggled.connect(self.set_snap_enabled)
        left_layout.addWidget(self.snap_button)
        self.blade_button = IconButton(
            icon=IconName.SCISSORS,
            tooltip=translate("timeline.tip.blade", hint=" (B)"),
            checkable=True,
            size=Sizes.icon_button,
        )
        self.blade_button.toggled.connect(lambda checked: self.set_tool("blade" if checked else "select"))
        left_layout.addWidget(self.blade_button)
        self.roll_button = IconButton(
            icon=IconName.CUT,
            tooltip=translate("timeline.tip.roll", hint=" (R)"),
            checkable=True,
            size=Sizes.icon_button,
        )
        self.slip_button = IconButton(
            icon=IconName.REWIND,
            tooltip=translate("timeline.tip.slip", hint=" (Y)"),
            checkable=True,
            size=Sizes.icon_button,
        )
        self.slide_button = IconButton(
            icon=IconName.FORWARD,
            tooltip=translate("timeline.tip.slide", hint=" (U)"),
            checkable=True,
            size=Sizes.icon_button,
        )
        self.roll_button.toggled.connect(lambda checked: self.set_tool("roll" if checked else "select"))
        self.slip_button.toggled.connect(lambda checked: self.set_tool("slip" if checked else "select"))
        self.slide_button.toggled.connect(lambda checked: self.set_tool("slide" if checked else "select"))
        left_layout.addWidget(self.roll_button)
        left_layout.addWidget(self.slip_button)
        left_layout.addWidget(self.slide_button)
        self.record_button = IconButton(
            # Icône distincte de ``marker_button`` : les deux boutons sont
            # voisins dans la barre d'outils et partageaient auparavant le
            # même glyphe de marqueur, ce qui les rendait indiscernables.
            icon=IconName.MIC,
            tooltip=translate("timeline.tip.record"),
            checkable=True,
            size=Sizes.icon_button,
        )
        self.record_button.toggled.connect(self.record_requested.emit)
        left_layout.addWidget(self.record_button)
        self.ripple_button = IconButton(
            icon=IconName.FORWARD,
            tooltip=translate("timeline.tip.ripple", hint=" (N)"),
            checkable=True,
            size=Sizes.icon_button,
        )
        self.ripple_button.toggled.connect(self._on_ripple_toggled)
        left_layout.addWidget(self.ripple_button)
        self.marker_button = IconButton(
            icon=IconName.MARKER,
            tooltip=translate("timeline.tip.marker", hint=" (M)"),
            size=Sizes.icon_button,
        )
        self.marker_button.clicked.connect(
            lambda: self.marker_add_requested.emit(self.playhead_seconds)
        )
        left_layout.addWidget(self.marker_button)

        # --- Bloc central : ajout de pistes ------------------------------
        center_block = QWidget()
        center_layout = QHBoxLayout(center_block)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(Spacing.sm)
        center_layout.setAlignment(Qt.AlignVCenter)

        self.add_video_btn = IconButton(
            icon=IconName.PLUS,
            tooltip=translate("tooltip.add_video"),
            size=Sizes.icon_button,
            square=False,
        )
        self.add_video_btn.setText(f"  {translate('tracks.add_video')}")
        self.add_video_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.add_audio_btn = IconButton(
            icon=IconName.PLUS,
            tooltip=translate("tooltip.add_audio"),
            size=Sizes.icon_button,
            square=False,
        )
        self.add_audio_btn.setText(f"  {translate('tracks.add_audio')}")
        self.add_audio_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.add_subtitle_btn = IconButton(
            icon=IconName.PLUS,
            tooltip=translate("tooltip.add_subtitle"),
            size=Sizes.icon_button,
            square=False,
        )
        self.add_subtitle_btn.setText(f"  {translate('tracks.add_subtitle')}")
        self.add_subtitle_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)

        center_layout.addWidget(self.add_video_btn)
        center_layout.addWidget(self.add_audio_btn)
        center_layout.addWidget(self.add_subtitle_btn)
        layout.addWidget(center_block)
        layout.addWidget(left_block)

        # --- Bloc extensible (vide pour l'instant) ----------------------
        layout.addStretch(1)

        # --- Bloc droite : statut + zoom ---------------------------------
        right_block = QWidget()
        right_layout = QHBoxLayout(right_block)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(Spacing.md)
        right_layout.setAlignment(Qt.AlignVCenter)

        status_box = QWidget()
        status_layout = QVBoxLayout(status_box)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(0)
        status_layout.setAlignment(Qt.AlignRight)
        self.clip_count_label = QLabel()
        self.clip_count_label.setStyleSheet(label_style(11, "muted", 500))
        self.clip_count_label.setAlignment(Qt.AlignRight)
        status_layout.addWidget(self.clip_count_label)
        self.version_label = QLabel("KUT-STUDIO")
        self.version_label.setStyleSheet(label_style(10, "muted", 700))
        self.version_label.setAlignment(Qt.AlignRight)
        status_layout.addWidget(self.version_label)
        self.version_label.hide()
        right_layout.addWidget(status_box)

        zoom_box = QWidget()
        zoom_layout = QHBoxLayout(zoom_box)
        zoom_layout.setContentsMargins(0, 0, 0, 0)
        zoom_layout.setSpacing(Spacing.xs)
        zoom_layout.setAlignment(Qt.AlignVCenter)
        self.zoom_out_btn = IconButton(
            icon=IconName.PLUS,
            tooltip=translate("tooltip.zoom_out"),
            size=Sizes.icon_button_sm,
        )
        self.zoom_out_btn.setIcon(_minus_icon())
        self.zoom_label = QLabel("100%")
        self.zoom_label.setStyleSheet(
            f"color: {palette.text}; font-weight: 700; min-width: 48px; "
            f"font-size: 11px;"
        )
        self.zoom_label.setAlignment(Qt.AlignCenter)
        self.zoom_in_btn = IconButton(
            icon=IconName.PLUS,
            tooltip=translate("tooltip.zoom_in"),
            size=Sizes.icon_button_sm,
        )
        self.zoom_fit_btn = IconButton(
            icon=IconName.PANEL_RESTORE,
            tooltip=translate("timeline.tip.fit"),
            size=Sizes.icon_button_sm,
        )
        self.zoom_fit_btn.clicked.connect(self.fit_timeline)
        zoom_layout.addWidget(self.zoom_out_btn)
        zoom_layout.addWidget(self.zoom_label)
        zoom_layout.addWidget(self.zoom_in_btn)
        zoom_layout.addWidget(self.zoom_fit_btn)
        right_layout.addWidget(zoom_box)

        layout.addWidget(right_block)
        return bar

    def _on_play_clicked(self) -> None:
        # Émet le signal handled by MainWindow.
        if hasattr(self, "play_requested"):
            self.play_requested.emit()

    def subscribe_to_theme(self, manager) -> None:
        """Abonne la timeline aux changements de palette de ``manager``.

        Appelé par :class:`~ui.main_window.MainWindow` avec *son*
        gestionnaire de thème. On ne crée volontairement pas de
        ``ThemeManager`` ici : une instance parasite publierait une
        palette par défaut (sombre) et écraserait le thème réel de
        l'application.
        """
        self._theme_manager = manager
        manager.subscribe(self._on_palette_changed)

    def unsubscribe_from_theme(self) -> None:
        """Retire l'abonnement thème. À appeler à la fermeture de la fenêtre."""
        manager = self._theme_manager
        if manager is None:
            return
        manager.unsubscribe(self._on_palette_changed)
        self._theme_manager = None

    def _on_palette_changed(self, manager) -> None:
        # La palette active est déjà publiée par ``ThemeManager`` :
        # on se contente de rafraîchir ce qui est peint à la main.
        try:
            palette = manager.effective_palette
        except Exception:
            palette = _current_palette()
        # Rafraîchit les en-têtes de pistes (pastilles type) et les clips.
        for header in self.track_header_widgets.values():
            header.refresh_state(palette)
        for widget in self.clip_widgets.values():
            widget.refresh_style()
        self._sync_transition_widgets()
        self._restyle_toolbar(palette)
        self.update()

    def _restyle_toolbar(self, palette) -> None:
        bar = self.findChild(QWidget, "timeline_toolbar")
        if bar is not None:
            bar.setStyleSheet(
                f"QWidget#timeline_toolbar {{ background: {palette.panel}; "
                f"border-bottom: 1px solid {palette.border}; }}"
            )

    def _on_ripple_toggled(self, checked: bool) -> None:
        self.ripple_enabled = bool(checked)
