"""Boîtes de dialogue de la vidéo sociale : nouveau projet (format, cadence, zones de plateforme), réglages de séquence,
export en plusieurs formats.

Le nouveau projet ne demande que l'essentiel : un nom, un **format** (le cadre de la séquence), une cadence, et la
plateforme dont le viewer montrera les zones masquées. Les templates s'y ajoutent quand il y en a (``templates``).
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.canvas_guides import PLATFORMS
from core.sequences import MAX_SEQUENCE_SIDE
from core.social_formats import DEFAULT_FORMAT_ID, SOCIAL_FORMATS, SOCIAL_FRAME_RATES, social_format
from ui.design_system import DIALOG_MARGINS, Sizes, Spacing
from ui.i18n import translate
from ui.keyboard_navigation import set_single_default
from ui.theme import COLORS, label_style

SEQUENCE_FRAME_RATES: tuple[float, ...] = (23.976, 24.0, 25.0, 29.97, 30.0, 50.0, 59.94, 60.0)


def format_choice_text(format_id: str) -> str:
    """« Vertical · 9:16 · 1080 × 1920 » : libellé d'un format."""
    entry = social_format(format_id)
    return translate("social.format.choice", name=translate(f"social.format.{entry.id}"), ratio=entry.ratio,
                     width=entry.width, height=entry.height)


def fps_text(fps: float) -> str:
    return translate("social.fps", fps=f"{fps:g}")


def platform_items() -> list[tuple[str, str]]:
    """``(identifiant, libellé)`` des choix de zones, « aucune » en premier."""
    return [("", translate("social.platform.none"))] + [(p, translate(f"social.platform.{p}")) for p in PLATFORMS]


@dataclass(frozen=True)
class SocialProjectChoice:
    """Réponse de la boîte « Nouveau projet réseaux sociaux »."""

    name: str
    format_id: str
    fps: float
    platform: str
    template_id: str = ""


class SocialProjectDialog(QDialog):
    """« Nouveau projet réseaux sociaux… »."""

    def __init__(self, parent: QWidget | None = None, *,
                 templates: list[tuple[str, str, str, QImage]] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(translate("social.dialog.new_title"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*DIALOG_MARGINS)
        layout.setSpacing(Spacing.md)
        intro = QLabel(translate("social.dialog.new_intro"))
        intro.setWordWrap(True)
        intro.setStyleSheet(label_style(12, "muted", 500))
        layout.addWidget(intro)
        form = QFormLayout()
        form.setSpacing(Spacing.sm)
        self.name_edit = QLineEdit(translate("social.dialog.default_name"))
        self.name_edit.setMaxLength(80)
        form.addRow(translate("social.dialog.name"), self.name_edit)
        formats = QWidget()
        formats_layout = QVBoxLayout(formats)
        formats_layout.setContentsMargins(0, 0, 0, 0)
        formats_layout.setSpacing(Spacing.xs)
        self.format_group = QButtonGroup(self)
        self.format_buttons: dict[str, QRadioButton] = {}
        for entry in SOCIAL_FORMATS:
            button = QRadioButton(format_choice_text(entry.id))
            button.setObjectName(f"social_format_{entry.id}")
            self.format_group.addButton(button)
            self.format_buttons[entry.id] = button
            formats_layout.addWidget(button)
            button.toggled.connect(lambda checked, fid=entry.id: checked and self._on_format(fid))
        form.addRow(translate("social.dialog.format"), formats)
        self.fps_combo = QComboBox()
        for rate in SOCIAL_FRAME_RATES:
            self.fps_combo.addItem(fps_text(rate), float(rate))
        form.addRow(translate("social.dialog.fps"), self.fps_combo)
        self.platform_combo = QComboBox()
        for platform, label in platform_items():
            self.platform_combo.addItem(label, platform)
        form.addRow(translate("social.dialog.zones"), self.platform_combo)
        layout.addLayout(form)
        # Galerie des templates : « Projet vide » d'abord, puis chaque template avec sa vignette (9:16).
        self.template_list = QListWidget(objectName="socialTemplateGallery")
        self.template_list.setViewMode(QListWidget.IconMode)
        self.template_list.setFlow(QListWidget.LeftToRight)
        self.template_list.setWrapping(False)
        self.template_list.setMovement(QListWidget.Static)
        thumb = QSize(round(Sizes.template_thumb * 9 / 16), Sizes.template_thumb)
        self.template_list.setIconSize(thumb)
        self.template_list.setFixedHeight(thumb.height() + 4 * Spacing.lg)
        empty = QPixmap(thumb)
        empty.fill(QColor(COLORS["surface"]))                      # « Projet vide » : une toile nue, alignée sur les autres
        blank = QListWidgetItem(QIcon(empty), translate("social.dialog.no_template"))
        blank.setData(Qt.UserRole, "")
        self.template_list.addItem(blank)
        for template_id, label, description, image in templates or ():
            item = QListWidgetItem(QPixmap.fromImage(image), label)
            item.setData(Qt.UserRole, template_id)
            item.setToolTip(description)
            self.template_list.addItem(item)
        self.template_list.setCurrentRow(0)
        self.template_list.setVisible(bool(templates))
        if templates:
            title = QLabel(translate("social.dialog.template"))
            title.setStyleSheet(label_style(12, "text", 600))
            layout.addWidget(title)
            layout.addWidget(self.template_list)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(translate("social.dialog.create"))
        buttons.button(QDialogButtonBox.Cancel).setText(translate("social.dialog.cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        set_single_default(self, buttons.button(QDialogButtonBox.Ok))
        self.format_buttons[DEFAULT_FORMAT_ID].setChecked(True)

    def _on_format(self, format_id: str) -> None:
        """Un format propose les zones de sa plateforme native (TikTok pour le vertical)."""
        platforms = social_format(format_id).platforms
        index = self.platform_combo.findData(platforms[0] if platforms else "")
        self.platform_combo.setCurrentIndex(max(0, index))

    def _current_template(self) -> str:
        item = self.template_list.currentItem()
        return str(item.data(Qt.UserRole)) if item is not None else ""

    def choice(self) -> SocialProjectChoice:
        format_id = next((fid for fid, button in self.format_buttons.items() if button.isChecked()), DEFAULT_FORMAT_ID)
        return SocialProjectChoice(
            name=self.name_edit.text().strip() or translate("social.dialog.default_name"),
            format_id=format_id,
            fps=float(self.fps_combo.currentData()),
            platform=str(self.platform_combo.currentData() or ""),
            template_id=str(self._current_template() or ""),
        )


class SequenceSettingsDialog(QDialog):
    """« Réglages de la séquence… » : taille du cadre et cadence."""

    def __init__(self, width: int, height: int, fps: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(translate("social.sequence.title"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*DIALOG_MARGINS)
        layout.setSpacing(Spacing.md)
        form = QFormLayout()
        form.setSpacing(Spacing.sm)
        self.preset_combo = QComboBox()
        self.preset_combo.addItem(translate("social.sequence.custom"), "")
        for entry in SOCIAL_FORMATS:
            self.preset_combo.addItem(format_choice_text(entry.id), entry.id)
        self.preset_combo.currentIndexChanged.connect(self._on_preset)
        form.addRow(translate("social.dialog.format"), self.preset_combo)
        self.width_spin = self._side(width)
        self.height_spin = self._side(height)
        form.addRow(translate("social.sequence.width"), self.width_spin)
        form.addRow(translate("social.sequence.height"), self.height_spin)
        self.fps_combo = QComboBox()
        for rate in SEQUENCE_FRAME_RATES:
            self.fps_combo.addItem(fps_text(rate), rate)
        index = min(range(len(SEQUENCE_FRAME_RATES)), key=lambda i: abs(SEQUENCE_FRAME_RATES[i] - float(fps)))
        self.fps_combo.setCurrentIndex(index)
        form.addRow(translate("social.dialog.fps"), self.fps_combo)
        layout.addLayout(form)
        note = QLabel(translate("social.sequence.note"))
        note.setWordWrap(True)
        note.setStyleSheet(label_style(11, "muted", 500))
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(translate("social.sequence.apply"))
        buttons.button(QDialogButtonBox.Cancel).setText(translate("social.dialog.cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        set_single_default(self, buttons.button(QDialogButtonBox.Ok))
        self._sync_preset()
        self.width_spin.valueChanged.connect(self._sync_preset)
        self.height_spin.valueChanged.connect(self._sync_preset)

    @staticmethod
    def _side(value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(16, MAX_SEQUENCE_SIDE)
        spin.setSingleStep(2)
        spin.setValue(int(value))
        spin.setSuffix(translate("social.sequence.pixels"))
        return spin

    def _on_preset(self, _index: int) -> None:
        format_id = self.preset_combo.currentData()
        if format_id:
            entry = social_format(format_id)
            self.width_spin.setValue(entry.width)
            self.height_spin.setValue(entry.height)

    def _sync_preset(self) -> None:
        size = (self.width_spin.value(), self.height_spin.value())
        match = next((entry.id for entry in SOCIAL_FORMATS if (entry.width, entry.height) == size), "")
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentIndex(max(0, self.preset_combo.findData(match)))
        self.preset_combo.blockSignals(False)

    def values(self) -> tuple[int, int, float]:
        width, height = self.width_spin.value(), self.height_spin.value()
        return width - width % 2, height - height % 2, float(self.fps_combo.currentData())


@dataclass(frozen=True)
class FormatVersionsChoice:
    """Réponse de « Exporter en plusieurs formats… »."""

    formats: tuple[str, ...]
    relayout: bool
    enqueue: bool


class FormatVersionsDialog(QDialog):
    """Formats à livrer : une version par format (séquence mise en page pour son cadre), puis un export chacun.

    ``source_format`` : format du montage d'origine (``""`` si son cadre n'est pas un format social) ; ``existing`` :
    formats dont une version existe déjà (réutilisée telle quelle, retouches comprises, sauf « refaire »)."""

    def __init__(self, source_format: str, existing: set[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(translate("social.formats.title"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*DIALOG_MARGINS)
        layout.setSpacing(Spacing.md)
        intro = QLabel(translate("social.formats.intro"))
        intro.setWordWrap(True)
        intro.setStyleSheet(label_style(11, "muted", 500))
        layout.addWidget(intro)
        self.format_boxes: dict[str, QCheckBox] = {}
        for entry in SOCIAL_FORMATS:
            text = format_choice_text(entry.id)
            if entry.id == source_format:
                text = translate("social.formats.this_one", format=text)
            elif entry.id in existing:
                text = translate("social.formats.existing", format=text)
            box = QCheckBox(text)
            box.setObjectName(f"format_version_{entry.id}")
            box.setChecked(True)
            box.toggled.connect(self._sync_buttons)
            self.format_boxes[entry.id] = box
            layout.addWidget(box)
        self.relayout_box = QCheckBox(translate("social.formats.relayout"))
        self.relayout_box.setVisible(bool(existing - {source_format}))
        layout.addWidget(self.relayout_box)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Cancel).setText(translate("social.dialog.cancel"))
        self.create_button = self.buttons.addButton(translate("social.formats.create"), QDialogButtonBox.ActionRole)
        self.export_button = self.buttons.addButton(translate("social.formats.export"), QDialogButtonBox.AcceptRole)
        self.create_button.clicked.connect(lambda: self._finish(enqueue=False))
        self.export_button.clicked.connect(lambda: self._finish(enqueue=True))
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        set_single_default(self, self.export_button)
        self._enqueue = True
        self._source_format = source_format
        self._sync_buttons()

    def _sync_buttons(self, *_args) -> None:
        chosen = self._chosen()
        self.export_button.setEnabled(bool(chosen))
        # Créer des versions n'a de sens que pour un autre format que celui du montage.
        self.create_button.setEnabled(any(format_id != self._source_format for format_id in chosen))

    def _chosen(self) -> tuple[str, ...]:
        return tuple(format_id for format_id, box in self.format_boxes.items() if box.isChecked())

    def _finish(self, *, enqueue: bool) -> None:
        self._enqueue = enqueue
        self.accept()

    def choice(self) -> FormatVersionsChoice:
        return FormatVersionsChoice(self._chosen(), self.relayout_box.isChecked(), self._enqueue)


__all__ = [
    "SEQUENCE_FRAME_RATES", "FormatVersionsChoice", "FormatVersionsDialog", "SequenceSettingsDialog",
    "SocialProjectChoice", "SocialProjectDialog",
    "format_choice_text", "fps_text", "platform_items",
]
