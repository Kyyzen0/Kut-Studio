"""Panneau Mixeur audio de Kut-Studio.

Le mixeur est un panneau dockable de l'espace de travail : il suit les
mêmes règles que les autres (masquable, détachable, restorable). Il
n'affiche que des **contrôles réels** adossés au modèle : volume de
piste, panoramique, mute, solo, arm, plus une tranche Master.

Les vumètres montrent des niveaux **réels** : ceux du mixage en direct
(:mod:`core.live_mix`), mesurés sur le son qui part vers la sortie, via
:attr:`MixerPanel.level_provider`. Sans mesure (lecture arrêtée), ils
retombent : jamais de niveau estimé.

Le panneau ne modifie jamais le projet par lui-même : il émet des
signaux, et c'est :class:`~ui.main_window.MainWindow` qui applique le
changement, enregistre l'historique et marque le projet modifié. Les
pistes verrouillées sont refusées ici **et** refusées à nouveau côté
fenêtre : la règle est appliquée au plus près de la donnée.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.project_model import MAX_GAIN_DB, MIN_GAIN_DB, Project, Track
from ui.design_system import Iconography, Radius, Spacing
from ui.i18n import translate
from ui.icons import IconButton, IconLabel, IconName
from ui.level_meter import LevelMeter
from ui.panel_header import PanelHeader
from ui.theme import COLORS, label_style


#: Position du panoramique au curseur (0 = gauche, 100 = droite).
_PAN_MIN = -100
_PAN_MAX = 100

#: Ligne compacte d'une piste (panneau bas) : hauteur, largeur maximale, largeurs minimales du fader et du vumètre.
COMPACT_ROW_HEIGHT = 30
COMPACT_ROW_MAX_WIDTH = 760
COMPACT_FADER_MIN_WIDTH = 90
COMPACT_METER_MIN_WIDTH = 56
COMPACT_ROUTE_WIDTH = 84
COMPACT_NAME_MIN_WIDTH = 28
COMPACT_NAME_MAX_WIDTH = 72

#: Hauteur de la zone des tranches (viewport) à partir de laquelle la tranche verticale (nom, fader, panoramique, boutons)
#: tient entière : en dessous, le panneau montre des lignes compactes qui défilent.
FULL_STRIP_VIEWPORT_HEIGHT = 316
STRIP_HYSTERESIS = 16

#: Hauteur minimale du panneau : une tranche complète (nom, fader,
#: panoramique, boutons) doit tenir sans être rognée.
MIN_PANEL_HEIGHT = 140      # les tranches défilent verticalement (voir ``core.workspace_state.MIN_HEIGHT``)


def _panel_stylesheet() -> str:
    """Feuille de style du panneau, couleurs résolues à la construction.

    Elle est déclarée localement (et non seulement dans la feuille
    globale) car une feuille posée sur un widget fait autorité sur tout
    son sous-arbre : sans cela les tranches perdraient leur fond et
    leurs boutons perdraient leurs états.
    """
    return (
        f"QWidget#mixer_panel {{ background: {COLORS['panel']};"
        f" color: {COLORS['text']}; }}"
        f"QFrame#mixerStrip {{ background: {COLORS['panel']};"
        f" border: 1px solid {COLORS['border']}; border-radius: 6px; }}"
        f"QFrame#mixerMasterBar {{ background: {COLORS['panel_alt']};"
        f" border-top: 1px solid {COLORS['border']}; }}"
        f"QScrollArea {{ border: none; background: transparent; }}"
    )


class MixerStrip(QFrame):
    """Une tranche de mixage : une piste audio ou la sortie Master.

    Attributes:
        track_id: Identifiant de la piste, ``None`` pour le Master.
    """

    volume_changed = Signal(str, float)
    pan_changed = Signal(str, float)
    mute_toggled = Signal(str, bool)
    solo_toggled = Signal(str, bool)
    arm_toggled = Signal(str, bool)
    reset_requested = Signal(str)
    inserts_requested = Signal(str)
    route_changed = Signal(str, str)  # piste, bus ("" : Master)
    remove_requested = Signal(str)  # bus
    rename_requested = Signal(str)  # bus

    def __init__(
        self,
        name: str,
        track_id: str | None = None,
        parent: QWidget | None = None,
        *,
        is_bus: bool = False,
        compact: bool = False,
    ) -> None:
        super().__init__(parent)
        self.track_id = track_id
        self.is_bus = is_bus
        self.compact = compact
        self._name = name
        self._locked = False
        self.setObjectName("mixerStrip")
        # Pas de feuille de style locale : le fond de la tranche est
        # déclaré dans la feuille globale, sinon les boutons enfants
        # perdraient leurs règles (hover, checked, icônes).
        # ``compact`` : une ligne (nom, FX, M/S/A, fader horizontal, vumètre...) pour un panneau trop bas pour la tranche
        # verticale ; le panneau reconstruit ses tranches quand sa hauteur change de côté du seuil.
        if compact:
            self.setFixedHeight(COMPACT_ROW_HEIGHT)
            self.setMaximumWidth(COMPACT_ROW_MAX_WIDTH)
            layout: QBoxLayout = QHBoxLayout(self)
            layout.setContentsMargins(Spacing.sm, 1, Spacing.sm, 1)
            layout.setSpacing(Spacing.sm)
        else:
            self.setFixedWidth(84)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(Spacing.sm, Spacing.sm, Spacing.sm, Spacing.sm)
            layout.setSpacing(Spacing.xs)
            layout.setAlignment(Qt.AlignTop)

        # Nom de piste (élidé si besoin) + cadenas si verrouillée.
        self.name_label = QLabel(name)
        self.name_label.setStyleSheet(label_style(11, "text", 700))
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setToolTip(name)
        if compact:
            self.name_label.setMinimumWidth(COMPACT_NAME_MIN_WIDTH)
            self.name_label.setMaximumWidth(COMPACT_NAME_MAX_WIDTH)
        layout.addWidget(self.name_label)

        self.lock_badge = IconLabel(IconName.LOCK, size=Iconography.sm)
        self.lock_badge.set_color("warning")
        self.lock_badge.hide()
        self.lock_badge.setToolTip(translate("mixer.locked"))
        layout.addWidget(self.lock_badge, 0, Qt.AlignHCenter)

        # Inserts de la piste : un bouton qui ouvre la chaîne d'effets (le nombre dit combien en porte la piste).
        self.inserts_button = QPushButton(translate("inserts.button"))
        self.inserts_button.setObjectName("mixerInsertsButton")
        self.inserts_button.setFixedHeight(22)
        self.inserts_button.setToolTip(translate("inserts.tip", name=name))
        self.inserts_button.clicked.connect(lambda: self.inserts_requested.emit(self.track_id or ""))
        self.inserts_button.setVisible(track_id is not None)
        layout.addWidget(self.inserts_button)

        # Fader de volume, en dB : vertical, ou horizontal dans la ligne compacte.
        self.volume_slider = QSlider(Qt.Horizontal) if compact else _VerticalFader()
        if compact:
            self.volume_slider.setMinimumWidth(COMPACT_FADER_MIN_WIDTH)
        self.volume_slider.setRange(int(MIN_GAIN_DB), int(MAX_GAIN_DB))
        self.volume_slider.setValue(0)
        self.volume_slider.valueChanged.connect(
            lambda value: self.volume_changed.emit(
                self.track_id or "", float(value)
            )
        )
        # Le vumètre de la piste (après fader et ducking) à côté de son fader.
        self.meter = LevelMeter(Qt.Horizontal if compact else Qt.Vertical)
        fader_row = QHBoxLayout()
        fader_row.setSpacing(Spacing.xs)
        if compact:
            self.meter.setMinimumWidth(COMPACT_METER_MIN_WIDTH)
            fader_row.addWidget(self.volume_slider, 3)
            fader_row.addWidget(self.meter, 1)
        else:
            fader_row.addStretch(1)
            fader_row.addWidget(self.volume_slider)
            fader_row.addWidget(self.meter)
            fader_row.addStretch(1)
        layout.addLayout(fader_row, 1)

        self.volume_value = QLabel("0.0 dB")
        self.volume_value.setStyleSheet(label_style(10, "muted", 600))
        self.volume_value.setAlignment(Qt.AlignCenter)
        self.volume_value.setMinimumWidth(64)
        layout.addWidget(self.volume_value)

        # Panoramique horizontal, avec repos centré.
        self.pan_slider = QSlider(Qt.Horizontal)
        self.pan_slider.setRange(_PAN_MIN, _PAN_MAX)
        self.pan_slider.setValue(0)
        self.pan_slider.setMaximumWidth(64)
        self.pan_slider.setToolTip(translate("mixer.pan"))
        self.pan_slider.valueChanged.connect(
            lambda value: self.pan_changed.emit(
                self.track_id or "", value / 100.0
            )
        )
        layout.addWidget(self.pan_slider, 0, Qt.AlignHCenter)

        # Boutons d'état : M / S / A, puis réinitialisation.
        buttons = QHBoxLayout()
        buttons.setSpacing(Spacing.xs)
        buttons.setContentsMargins(0, 0, 0, 0)
        self.mute_button = IconButton(
            icon=IconName.AUDIO_MUTE,
            tooltip=translate("mixer.track_mute", name=name)
            if track_id
            else translate("mixer.master_mute"),
            checkable=True,
            size=22,
        )
        self.mute_button.toggled.connect(
            lambda checked: self.mute_toggled.emit(
                self.track_id or "", checked
            )
        )
        buttons.addWidget(self.mute_button)
        layout.addLayout(buttons)

        master_buttons = QHBoxLayout()
        master_buttons.setSpacing(Spacing.xs)
        master_buttons.setContentsMargins(0, 0, 0, 0)
        self.solo_button = IconButton(
            icon=IconName.AUDIO_SOLO,
            tooltip=translate("mixer.track_solo", name=name),
            checkable=True,
            size=22,
        )
        self.solo_button.toggled.connect(
            lambda checked: self.solo_toggled.emit(
                self.track_id or "", checked
            )
        )
        self.arm_button = IconButton(
            icon=IconName.AUDIO_ARM,
            tooltip=translate("mixer.track_arm", name=name),
            checkable=True,
            size=22,
        )
        self.arm_button.toggled.connect(
            lambda checked: self.arm_toggled.emit(
                self.track_id or "", checked
            )
        )
        master_buttons.addWidget(self.solo_button)
        master_buttons.addWidget(self.arm_button)
        layout.addLayout(master_buttons)

        self.reset_button = IconButton(
            icon=IconName.PANEL_RESET,
            tooltip=translate("mixer.reset"),
            size=22,
        )
        self.reset_button.clicked.connect(
            lambda: self.reset_requested.emit(self.track_id or "")
        )
        layout.addWidget(self.reset_button, 0, Qt.AlignHCenter)

        # Sortie d'une piste : le Master ou un bus. Un bus n'a ni panoramique, ni solo, ni armement : il porte un
        # bouton pour se supprimer (ses pistes retournent au Master).
        self.route_combo = QComboBox()
        self.route_combo.setObjectName("mixerRouteCombo")
        self.route_combo.setMaximumWidth(COMPACT_ROUTE_WIDTH if compact else 68)
        self.route_combo.setToolTip(translate("bus.route.tip", name=name))
        self.route_combo.activated.connect(
            lambda _index: self.route_changed.emit(self.track_id or "", str(self.route_combo.currentData() or ""))
        )
        self.route_combo.setVisible(track_id is not None and not is_bus)
        layout.addWidget(self.route_combo, 0, Qt.AlignHCenter)
        self.remove_button = QPushButton("×")
        self.remove_button.setObjectName("mixerBusRemove")
        self.remove_button.setFixedHeight(22)
        self.remove_button.setToolTip(translate("bus.remove.tip", name=name))
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(self.track_id or ""))
        self.remove_button.setVisible(is_bus)
        layout.addWidget(self.remove_button)
        if is_bus:
            for widget in (self.pan_slider, self.solo_button, self.arm_button, self.reset_button):
                widget.setVisible(False)
            self.name_label.setToolTip(translate("bus.rename.tip"))

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt
        if self.is_bus:
            self.rename_requested.emit(self.track_id or "")
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def set_routes(self, buses, current: str) -> None:
        """Remplit le choix de sortie : le Master puis les bus de la séquence (``buses`` : ``(id, nom)``)."""
        self.route_combo.blockSignals(True)
        self.route_combo.clear()
        self.route_combo.addItem(translate("bus.route.master"), "")
        for bus_id, bus_name in buses:
            self.route_combo.addItem(bus_name, bus_id)
        index = self.route_combo.findData(current)
        self.route_combo.setCurrentIndex(index if index >= 0 else 0)
        self.route_combo.blockSignals(False)

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def set_inserts(self, count: int, active: int) -> None:
        """Dit combien d'inserts porte la piste (``active`` : ceux qui ne sont pas contournés)."""
        self.inserts_button.setText(
            translate("inserts.button.count", count=count) if count else translate("inserts.button")
        )
        self.inserts_button.setProperty("active", active > 0)
        self.inserts_button.setStyleSheet(
            f"QPushButton#mixerInsertsButton {{ background: {COLORS['accent_dark'] if active else COLORS['surface']};"
            f" color: {COLORS['accent'] if active else COLORS['muted']}; border: 1px solid {COLORS['border']};"
            f" border-radius: 6px; font-size: 10px; font-weight: 700; padding: 0 4px; }}"
        )

    def set_track(self, *, name: str, volume_db: float, pan: float, locked: bool) -> None:
        """Met à jour la tranche depuis le modèle, sans émettre de signal."""
        self._name = name
        self._locked = locked
        self.name_label.setText(name)
        self.name_label.setToolTip(name)
        self.lock_badge.setVisible(locked)
        self.name_label.setStyleSheet(
            label_style(11, "disabled_text" if locked else "text", 700)
        )
        self._apply_volume(volume_db)
        self._apply_pan(pan)
        for widget in (
            self.volume_slider,
            self.pan_slider,
            self.mute_button,
            self.solo_button,
            self.arm_button,
            self.reset_button,
            self.route_combo,
        ):
            widget.setEnabled(not locked)

    def set_states(self, *, muted: bool, solo: bool, armed: bool) -> None:
        """Synchronise les boutons d'état sans réémettre de changement."""
        for button, value in (
            (self.mute_button, muted),
            (self.solo_button, solo),
            (self.arm_button, armed),
        ):
            button.blockSignals(True)
            button.setChecked(value)
            button.blockSignals(False)

    def is_locked(self) -> bool:
        return self._locked

    # ------------------------------------------------------------------
    # Interne
    # ------------------------------------------------------------------

    def _apply_volume(self, value: float) -> None:
        bounded = int(round(max(MIN_GAIN_DB, min(MAX_GAIN_DB, float(value)))))
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(bounded)
        self.volume_slider.blockSignals(False)
        self._update_volume_label(bounded)

    def _update_volume_label(self, value: int) -> None:
        if value <= int(MIN_GAIN_DB):
            self.volume_value.setText(translate("mixer.mute"))
        else:
            self.volume_value.setText(f"{value:+.1f} dB".replace("+", "+"))

    def _apply_pan(self, value: float) -> None:
        position = int(round(max(_PAN_MIN, min(_PAN_MAX, float(value) * 100))))
        self.pan_slider.blockSignals(True)
        self.pan_slider.setValue(position)
        self.pan_slider.blockSignals(False)
        self.pan_slider.setToolTip(_pan_label(value))


def _pan_label(value: float) -> str:
    """Infobulle lisible du panoramique."""
    if abs(value) < 0.02:
        return translate("mixer.pan_center")
    if value < 0:
        return f"{translate('mixer.pan_left')} {abs(value) * 100:.0f}%"
    return f"{translate('mixer.pan_right')} {value * 100:.0f}%"


class _VerticalFader(QSlider):
    """Fader vertical de volume, en décibels.

    Qt ne sait dessiner un fader vertical qu'avec des feuilles de style ;
    on fournit donc une règle explicite pour que le poignée reste lisible
    dans les deux thèmes.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Vertical, parent)
        self.setObjectName("verticalFader")
        # Assez long pour être précis au manche, assez court pour que la
        # tranche entière tienne dans le panneau sans défilement.
        self.setMinimumHeight(72)
        self.setMaximumHeight(200)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setPageStep(6)
        self.setTickInterval(6)
        self.setTickPosition(QSlider.TicksLeft)
        self.setStyleSheet(self._style())

    @staticmethod
    def _style() -> str:
        return (
            f"QSlider#verticalFader {{ background: transparent; }}"
            f"QSlider::groove:vertical {{"
            f" background: {COLORS['border']}; width: 4px; border-radius: {Radius.pill}px; }}"
            f"QSlider::sub-page:vertical {{"
            f" background: {COLORS['accent']}; border-radius: {Radius.pill}px; }}"
            f"QSlider::handle:vertical {{"
            f" background: {COLORS['text']}; width: 12px; height: 12px;"
            f" margin: 0 -4px; border-radius: 6px; }}"
            f"QSlider::handle:vertical:hover {{"
            f" background: {COLORS['accent_hover']}; }}"
            f"QSlider::handle:vertical:disabled {{"
            f" background: {COLORS['disabled_text']}; }}"
            f"QSlider::add-page:vertical {{ background: {COLORS['border']}; }}"
        )


def _color(value: str):
    from PySide6.QtGui import QColor

    return QColor(value)


class _MasterBar(QFrame):
    """Barre de sortie Master, compacte et horizontale.

    Une tranche verticale prendrait une place excessive pour trois
    contrôles ; la barre rend le Master lisible sans voler de hauteur
    aux tranches de pistes.
    """

    volume_changed = Signal(float)
    mute_toggled = Signal(bool)
    reset_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("mixerMasterBar")
        self.setFixedHeight(48)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            Spacing.md, Spacing.sm, Spacing.md, Spacing.sm
        )
        layout.setSpacing(Spacing.md)

        icon = IconLabel(IconName.AUDIO_VOLUME, size=Iconography.md)
        icon.set_color("muted_strong")
        layout.addWidget(icon)

        self.name_label = QLabel(translate("mixer.master"))
        self.name_label.setStyleSheet(label_style(11, "text", 700))
        layout.addWidget(self.name_label)

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(int(MIN_GAIN_DB), int(MAX_GAIN_DB))
        self.volume_slider.setValue(0)
        self.volume_slider.setMinimumWidth(140)
        self.volume_slider.valueChanged.connect(
            lambda value: self.volume_changed.emit(float(value))
        )
        layout.addWidget(self.volume_slider, 1)

        self.volume_value = QLabel("0.0 dB")
        self.volume_value.setStyleSheet(label_style(11, "muted", 600))
        self.volume_value.setAlignment(Qt.AlignRight)
        self.volume_value.setFixedWidth(64)
        layout.addWidget(self.volume_value)

        # Ce qui sort : crête et RMS, puis la sonie (BS.1770) momentanée et à court terme.
        self.meter = LevelMeter(Qt.Horizontal)
        layout.addWidget(self.meter, 1)
        self.loudness_label = QLabel("")
        self.loudness_label.setStyleSheet(label_style(10, "muted", 600))
        self.loudness_label.setMinimumWidth(150)
        self.loudness_label.setToolTip(translate("mixer.loudness_tip"))
        layout.addWidget(self.loudness_label)

        self.mute_button = IconButton(
            icon=IconName.AUDIO_MUTE,
            tooltip=translate("mixer.master_mute"),
            checkable=True,
            size=26,
        )
        self.mute_button.toggled.connect(self.mute_toggled)
        layout.addWidget(self.mute_button)

        self.reset_button = IconButton(
            icon=IconName.PANEL_RESET,
            tooltip=translate("mixer.master_reset"),
            size=26,
        )
        self.reset_button.clicked.connect(self.reset_requested)
        layout.addWidget(self.reset_button)

    def set_master(self, gain_db: float, muted: bool) -> None:
        """Met à jour gain et coupure globale, sans réémettre de signal."""
        bounded = int(round(max(MIN_GAIN_DB, min(MAX_GAIN_DB, float(gain_db)))))
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(bounded)
        self.volume_slider.blockSignals(False)
        if bounded <= int(MIN_GAIN_DB):
            self.volume_value.setText(translate("mixer.mute"))
        else:
            self.volume_value.setText(f"{bounded:+.1f} dB")
        self.mute_button.blockSignals(True)
        self.mute_button.setChecked(bool(muted))
        self.mute_button.blockSignals(False)

    def set_levels(self, reading) -> None:
        """Mesures du Master (:class:`core.live_mix.MeterFrame`) ; ``None`` : aucune, tout retombe."""
        if reading is None:
            self.meter.set_levels(None)
            self.loudness_label.setText("")
            return
        self.meter.set_levels(reading.peak_db, reading.rms_db)
        self.loudness_label.setText(translate(
            "mixer.loudness", momentary=_lufs_text(reading.momentary_lufs),
            short_term=_lufs_text(reading.short_term_lufs)))

    def retranslate(self) -> None:
        self.loudness_label.setToolTip(translate("mixer.loudness_tip"))
        self.name_label.setText(translate("mixer.master"))
        self.mute_button.setToolTip(translate("mixer.master_mute"))
        self.reset_button.setToolTip(translate("mixer.master_reset"))


class MixerPanel(QWidget):
    """Panneau de mixage dockable."""

    volume_changed = Signal(str, float)
    pan_changed = Signal(str, float)
    mute_toggled = Signal(str, bool)
    solo_toggled = Signal(str, bool)
    arm_toggled = Signal(str, bool)
    reset_requested = Signal(str)
    inserts_requested = Signal(str)
    #: Émis quand les tranches viennent d'être reconstruites depuis le projet (annulation, ouverture, modification).
    project_refreshed = Signal()
    track_route_changed = Signal(str, str)
    bus_add_requested = Signal()
    bus_volume_changed = Signal(str, float)
    bus_mute_toggled = Signal(str, bool)
    bus_inserts_requested = Signal(str)
    bus_remove_requested = Signal(str)
    bus_rename_requested = Signal(str)
    master_volume_changed = Signal(float)
    master_mute_toggled = Signal(bool)
    master_reset_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("mixer_panel")
        self.setMinimumHeight(MIN_PANEL_HEIGHT)
        # Les fonds des enfants sont déclarés **ici** et pas seulement
        # dans la feuille globale : une feuille locale fait autorité sur
        # son sous-arbre, les tranches resteraient sinon sans fond.
        self.setStyleSheet(_panel_stylesheet())
        self._strips: dict[str, MixerStrip] = {}
        self._bus_strips: dict[str, MixerStrip] = {}
        self._master_bar: MixerPanel._MasterBar | None = None
        self._project: Project | None = None
        self._compact = False                       # lignes compactes (panneau bas) plutôt que tranches verticales
        self._built_compact = False                 # la forme des tranches actuellement construites
        self._rebuild_pending = False
        #: Fournisseur des niveaux réels : ``() -> (relevé, pistes) | None``
        #: (:class:`core.live_mix.MeterFrame` et l'ordre de ses pistes).
        #: ``None`` ou aucun relevé : les vumètres retombent.
        self.level_provider = None
        self._meter_timer = QTimer(self)
        self._meter_timer.setInterval(33)
        self._meter_timer.timeout.connect(self.refresh_levels)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # En-tête.
        header = PanelHeader(translate("mixer.title"), icon=IconName.AUDIO_MIXER)
        self.title_label = header.title_label
        self.solo_hint = QLabel("")
        self.solo_hint.setStyleSheet(label_style(10, "warning", 600))
        header.add_trailing(self.solo_hint)
        self.add_bus_button = QPushButton(translate("bus.add"))
        self.add_bus_button.setObjectName("mixerAddBus")
        self.add_bus_button.setToolTip(translate("bus.add.tip"))
        self.add_bus_button.setStyleSheet(
            f"QPushButton#mixerAddBus {{ background: {COLORS['surface']}; color: {COLORS['text']};"
            f" border: 1px solid {COLORS['border']}; border-radius: 6px; padding: 2px 10px; font-size: 11px; }}"
            f"QPushButton#mixerAddBus:hover {{ background: {COLORS['surface_hover']}; }}"
        )
        self.add_bus_button.clicked.connect(self.bus_add_requested)
        header.add_trailing(self.add_bus_button)
        layout.addWidget(header)

        # Tranches, horizontalement défilantes.
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # Le défilement vertical reste possible : sur une fenêtre basse
        # une tranche peut dépasser la zone, et son contenu doit rester
        # accessible plutôt que d'être coupé.
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setStyleSheet("QScrollArea { border: none; }")

        self._body = QWidget()
        self._body.setStyleSheet("background: transparent;")
        self._body_layout = QHBoxLayout(self._body)
        self._body_layout.setContentsMargins(
            Spacing.md, Spacing.md, Spacing.md, Spacing.md
        )
        self._body_layout.setSpacing(Spacing.md)
        self._body_layout.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        self.empty_label = QLabel(translate("mixer.no_tracks"))
        self.empty_label.setStyleSheet(label_style(12, "muted", 500))
        self.empty_label.setAlignment(Qt.AlignCenter)
        self._body_layout.addWidget(self.empty_label)
        self._body_layout.addStretch(1)
        self.scroll.setWidget(self._body)
        self.scroll.viewport().installEventFilter(self)
        layout.addWidget(self.scroll, 1)

        self._master_bar = _MasterBar(self)
        self._master_bar.volume_changed.connect(self.master_volume_changed)
        self._master_bar.mute_toggled.connect(self.master_mute_toggled)
        self._master_bar.reset_requested.connect(self.master_reset_requested)
        layout.addWidget(self._master_bar)

        self._retranslate()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def set_project(self, project: Project) -> None:
        """Reconstruit les tranches depuis le projet.

        L'état du panneau est un reflet du modèle : le panneau ne
        conserve aucun état de mixage propre.
        """
        self._project = project
        self._built_compact = self._compact
        self._body_layout.setDirection(QBoxLayout.TopToBottom if self._compact else QBoxLayout.LeftToRight)
        margin = 2 if self._compact else Spacing.md
        self._body_layout.setContentsMargins(margin, margin, margin, margin)
        self._body_layout.setSpacing(2 if self._compact else Spacing.md)
        for strip in [*self._strips.values(), *self._bus_strips.values()]:
            strip.setParent(None)
            strip.deleteLater()
        self._strips.clear()
        self._bus_strips.clear()
        buses = [(bus.id, bus.name) for bus in project.buses]

        audio_tracks = [t for t in project.tracks if t.type == "audio"]
        self.empty_label.setVisible(not audio_tracks)
        for track in audio_tracks:
            strip = MixerStrip(track.name, track_id=track.id, compact=self._compact)
            strip.set_track(
                name=track.name,
                volume_db=track.volume_db,
                pan=track.pan,
                locked=track.locked,
            )
            strip.set_states(
                muted=track.muted, solo=track.solo, armed=track.armed
            )
            strip.set_inserts(len(track.inserts), sum(1 for e in track.inserts if e.enabled))
            strip.set_routes(buses, track.bus_id)
            strip.route_changed.connect(self.track_route_changed)
            strip.inserts_requested.connect(self.inserts_requested)
            strip.volume_changed.connect(self.volume_changed)
            strip.pan_changed.connect(self.pan_changed)
            strip.mute_toggled.connect(self.mute_toggled)
            strip.solo_toggled.connect(self.solo_toggled)
            strip.arm_toggled.connect(self.arm_toggled)
            strip.reset_requested.connect(self.reset_requested)
            # Inséré avant l'étirement final pour rester à gauche.
            self._body_layout.insertWidget(
                self._body_layout.count() - 1, strip
            )
            self._strips[track.id] = strip

        for bus in project.buses:
            strip = MixerStrip(bus.name, track_id=bus.id, is_bus=True, compact=self._compact)
            strip.set_track(name=bus.name, volume_db=bus.volume_db, pan=0.0, locked=False)
            strip.set_states(muted=bus.muted, solo=False, armed=False)
            strip.set_inserts(len(bus.inserts), sum(1 for e in bus.inserts if e.enabled))
            strip.volume_changed.connect(self.bus_volume_changed)
            strip.mute_toggled.connect(self.bus_mute_toggled)
            strip.inserts_requested.connect(self.bus_inserts_requested)
            strip.remove_requested.connect(self.bus_remove_requested)
            strip.rename_requested.connect(self.bus_rename_requested)
            self._body_layout.insertWidget(self._body_layout.count() - 1, strip)
            self._bus_strips[bus.id] = strip

        self._refresh_solo_hint(project)
        self.project_refreshed.emit()

    def is_compact(self) -> bool:
        """``True`` : une ligne par piste (zone des tranches trop basse pour la tranche verticale)."""
        return self._built_compact

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - API Qt
        if watched is self.scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._sync_compact()               # la taille du viewport est celle d'après la mise en page
        return super().eventFilter(watched, event)

    def _sync_compact(self) -> None:
        # Hystérésis : la barre de défilement horizontale (tranches nombreuses) mange ~12 px du viewport.
        threshold = FULL_STRIP_VIEWPORT_HEIGHT + (STRIP_HYSTERESIS if self._compact else 0)
        compact = self.scroll.viewport().height() < threshold
        if compact != self._compact and not self._rebuild_pending:
            self._compact = compact
            self._rebuild_pending = True
            QTimer.singleShot(0, self, self._rebuild_for_height)      # jamais pendant le redimensionnement

    def _rebuild_for_height(self) -> None:
        self._rebuild_pending = False
        if self._project is not None:
            self.set_project(self._project)

    def set_master(self, gain_db: float, muted: bool) -> None:
        """Met à jour la sortie Master (gain, coupure globale)."""
        if self._master_bar is not None:
            self._master_bar.set_master(gain_db, muted)

    def refresh_track(self, track: Track) -> None:
        """Rafraîchit une tranche après une modification externe."""
        strip = self._strips.get(track.id)
        if strip is None:
            return
        strip.set_track(
            name=track.name,
            volume_db=track.volume_db,
            pan=track.pan,
            locked=track.locked,
        )
        strip.set_states(
            muted=track.muted, solo=track.solo, armed=track.armed
        )
        strip.set_inserts(len(track.inserts), sum(1 for e in track.inserts if e.enabled))

    def refresh_levels(self) -> None:
        """Relève les niveaux auprès de :attr:`level_provider` et les montre (minuteur, panneau visible)."""
        result = self.level_provider() if self.level_provider is not None else None
        reading, tracks = result if result else (None, ())
        if self._master_bar is not None:
            self._master_bar.set_levels(reading)
        peaks = dict(zip(tracks, reading.track_peaks_db)) if reading is not None else {}
        for track_id, strip in self._strips.items():
            strip.meter.set_levels(peaks.get(track_id))      # piste muette ou sans clip ici : elle retombe
        for bus_id, strip in self._bus_strips.items():
            strip.meter.set_levels(peaks.get(f"bus:{bus_id}"))  # bus sans piste : il retombe

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._meter_timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._meter_timer.stop()

    def strip_count(self) -> int:
        return len(self._strips)

    def strip_for(self, track_id: str) -> MixerStrip | None:
        return self._strips.get(track_id)

    def bus_strip_for(self, bus_id: str) -> MixerStrip | None:
        return self._bus_strips.get(bus_id)

    def master_bar(self) -> "_MasterBar | None":
        return self._master_bar

    # ------------------------------------------------------------------
    # Internationalisation
    # ------------------------------------------------------------------

    def _retranslate(self) -> None:
        self.title_label.setText(translate("mixer.title"))
        self.empty_label.setText(translate("mixer.no_tracks"))
        self.add_bus_button.setText(translate("bus.add"))
        self.add_bus_button.setToolTip(translate("bus.add.tip"))
        if self._master_bar is not None:
            self._master_bar.retranslate()
        for strip in self._strips.values():
            name = strip.name_label.text()
            strip.mute_button.setToolTip(translate("mixer.track_mute", name=name))
            strip.solo_button.setToolTip(translate("mixer.track_solo", name=name))
            strip.arm_button.setToolTip(translate("mixer.track_arm", name=name))
            strip.reset_button.setToolTip(translate("mixer.reset"))
            strip.lock_badge.setToolTip(translate("mixer.locked"))
            strip.inserts_button.setToolTip(translate("inserts.tip", name=name))

    def update_translations(self) -> None:
        """Point d'entrée utilisé par ``MainWindow._retranslate_ui``."""
        self._retranslate()

    def _refresh_solo_hint(self, project: Project) -> None:
        solo = [t for t in project.tracks if t.type == "audio" and t.solo]
        self.solo_hint.setText(translate("mixer.solo_active") if solo else "")


def _lufs_text(value: float) -> str:
    from core.live_mix import SILENCE_DB

    return "−∞" if value <= SILENCE_DB + 1e-6 else f"{value:.1f}".replace("-", "−")


__all__ = ["MixerPanel", "MixerStrip"]
