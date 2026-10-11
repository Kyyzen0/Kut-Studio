"""Boîte « Inserts de la piste » : la chaîne d'effets qui traite la somme des clips d'une piste audio.

Comme le rack d'effets de clip de l'inspecteur, elle n'écrit jamais dans le projet : chaque action émet une intention
(avec l'identifiant de la piste), le gestionnaire de la fenêtre principale applique le changement, enregistre
l'historique et rappelle :meth:`TrackInsertsDialog.set_track`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.audio_effects_model import AudioEffect, AudioEffectType, parameter_specs
from ui.design_system import Spacing
from ui.i18n import translate
from ui.theme import label_style


class TrackInsertsDialog(QDialog):
    """Chaîne d'inserts d'une piste : liste ordonnée, contournement, ajout, suppression, réglages."""

    add_requested = Signal(str, str)  # piste, type d'effet
    remove_requested = Signal(str, str)  # piste, insert
    move_requested = Signal(str, str, int)  # piste, insert, décalage (−1 : monter)
    enabled_changed = Signal(str, str, bool)  # piste, insert, actif
    parameter_changed = Signal(str, str, str, float)  # piste, insert, paramètre, valeur

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("trackInsertsDialog")
        self.setModal(False)
        self.setMinimumWidth(320)
        self.track_id = ""
        self._track_name = ""
        self._inserts: list[AudioEffect] = []
        self._selected_id: str | None = None
        self._building = False

        layout = QVBoxLayout(self)
        layout.setSpacing(Spacing.sm)

        self.hint_label = QLabel(translate("inserts.hint"))
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet(label_style(11, "muted", 500))
        layout.addWidget(self.hint_label)

        add_row = QHBoxLayout()
        self.type_combo = QComboBox()
        self.type_combo.setObjectName("insertType")
        for effect_type in AudioEffectType:
            self.type_combo.addItem(translate(f"audio_effects.preset.{effect_type.value}.name"), effect_type.value)
        self.add_button = QPushButton(translate("audio_effects.add"))
        self.add_button.setObjectName("insertAdd")
        self.add_button.clicked.connect(self._on_add)
        add_row.addWidget(self.type_combo, 1)
        add_row.addWidget(self.add_button)
        layout.addLayout(add_row)

        self.list = QListWidget()
        self.list.setObjectName("insertsList")
        self.list.setMinimumHeight(110)
        self.list.currentRowChanged.connect(self._on_row_changed)
        layout.addWidget(self.list)

        self.empty_label = QLabel(translate("inserts.empty"))
        self.empty_label.setWordWrap(True)
        self.empty_label.setStyleSheet(label_style(11, "muted", 500))
        layout.addWidget(self.empty_label)

        buttons = QHBoxLayout()
        self.toggle_button = QPushButton(translate("audio_effects.disable"))
        self.toggle_button.setObjectName("insertToggle")
        self.toggle_button.clicked.connect(self._on_toggle)
        self.up_button = QPushButton(translate("audio_effects.move_up"))
        self.up_button.setObjectName("insertUp")
        self.up_button.clicked.connect(lambda: self._on_move(-1))
        self.down_button = QPushButton(translate("audio_effects.move_down"))
        self.down_button.setObjectName("insertDown")
        self.down_button.clicked.connect(lambda: self._on_move(1))
        self.remove_button = QPushButton(translate("audio_effects.remove"))
        self.remove_button.setObjectName("insertRemove")
        self.remove_button.clicked.connect(self._on_remove)
        for button in (self.toggle_button, self.up_button, self.down_button, self.remove_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)

        self.parameters = QWidget()
        self.parameters_layout = QVBoxLayout(self.parameters)
        self.parameters_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.parameters)
        self.spin_boxes: dict[str, QDoubleSpinBox] = {}

        self._refresh()

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def set_track(self, track_id: str, name: str, inserts, *, locked: bool = False) -> None:
        """Reflète la chaîne de la piste (appelé après chaque changement du modèle)."""
        inserts = list(inserts)
        locked = bool(locked)
        if (
            track_id == self.track_id and name == self._track_name and inserts == self._inserts
            and locked == getattr(self, "_locked", False)
        ):
            return  # rien n'a changé pour cette boîte : ne pas reconstruire les réglages pendant qu'on les tourne
        self.track_id = track_id
        self._track_name = name
        self._inserts = inserts
        ids = [effect.id for effect in self._inserts]
        if self._selected_id not in ids:
            self._selected_id = ids[0] if ids else None
        self.setWindowTitle(translate("inserts.title", name=name))
        self._locked = locked
        self._refresh()

    # ------------------------------------------------------------------
    # Interne
    # ------------------------------------------------------------------

    def _selected(self) -> AudioEffect | None:
        return next((effect for effect in self._inserts if effect.id == self._selected_id), None)

    def _refresh(self) -> None:
        locked = getattr(self, "_locked", False)
        self._building = True
        self.list.clear()
        for effect in self._inserts:
            name = translate(f"audio_effects.preset.{effect.type.value}.name")
            if not effect.enabled:
                name = f"{name} · {translate('effects.disabled_suffix')}"
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, effect.id)
            self.list.addItem(item)
        ids = [effect.id for effect in self._inserts]
        self.list.setCurrentRow(ids.index(self._selected_id) if self._selected_id in ids else -1)
        self._building = False
        self.empty_label.setVisible(not self._inserts)

        effect = self._selected()
        index = ids.index(effect.id) if effect is not None else -1
        self.toggle_button.setText(
            translate("audio_effects.disable") if effect is None or effect.enabled else translate("audio_effects.enable")
        )
        self.toggle_button.setEnabled(effect is not None and not locked)
        self.remove_button.setEnabled(effect is not None and not locked)
        self.up_button.setEnabled(effect is not None and index > 0 and not locked)
        self.down_button.setEnabled(effect is not None and index < len(ids) - 1 and not locked)
        self.add_button.setEnabled(not locked)
        self.type_combo.setEnabled(not locked)
        self._build_parameters(effect, locked)

    def _build_parameters(self, effect: AudioEffect | None, locked: bool) -> None:
        self.spin_boxes = {}
        while self.parameters_layout.count():
            item = self.parameters_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        if effect is None:
            return
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(Spacing.xs)
        for spec in parameter_specs(effect.type):
            spin = QDoubleSpinBox()
            spin.setObjectName(f"insertParam_{spec.name}")
            spin.setRange(spec.minimum, spec.maximum)
            spin.setDecimals(3)
            spin.setSingleStep(0.1)
            spin.setValue(float(effect.params.get(spec.name, spec.default)))
            spin.setEnabled(not locked)
            spin.valueChanged.connect(
                lambda value, name=spec.name, insert_id=effect.id: self._on_parameter(insert_id, name, float(value))
            )
            self.spin_boxes[spec.name] = spin
            label = QLabel(translate(f"audio_effects.param.{spec.name}"))
            label.setStyleSheet(label_style(11, "muted", 600))
            form.addRow(label, spin)
        wrapper = QWidget()
        wrapper.setLayout(form)
        self.parameters_layout.addWidget(wrapper)

    def _on_parameter(self, insert_id: str, name: str, value: float) -> None:
        # La copie locale suit la boîte : le rappel de ``set_track`` qui suit l'enregistrement la trouve identique au
        # modèle et ne reconstruit pas les réglages sous la main de l'utilisateur.
        for position, effect in enumerate(self._inserts):
            if effect.id == insert_id:
                try:
                    self._inserts[position] = effect.with_parameters({name: value})
                except ValueError:
                    pass
        self.parameter_changed.emit(self.track_id, insert_id, name, value)

    def _on_row_changed(self, row: int) -> None:
        if self._building:
            return
        item = self.list.item(row)
        self._selected_id = item.data(Qt.UserRole) if item is not None else None
        self._refresh()

    def _on_add(self) -> None:
        effect_type = self.type_combo.currentData()
        if isinstance(effect_type, str) and self.track_id:
            self.add_requested.emit(self.track_id, effect_type)

    def _on_remove(self) -> None:
        effect = self._selected()
        if effect is not None:
            self.remove_requested.emit(self.track_id, effect.id)

    def _on_move(self, delta: int) -> None:
        effect = self._selected()
        if effect is not None:
            self.move_requested.emit(self.track_id, effect.id, delta)

    def _on_toggle(self) -> None:
        effect = self._selected()
        if effect is not None:
            self.enabled_changed.emit(self.track_id, effect.id, not effect.enabled)

    def select_insert(self, insert_id: str) -> None:
        """Sélectionne un insert (la boîte vient d'en créer un)."""
        self._selected_id = insert_id
        self._refresh()
