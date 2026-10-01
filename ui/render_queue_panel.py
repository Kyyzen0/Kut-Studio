"""Panneau « File de rendu » : vue et commandes de :class:`core.render_queue.RenderQueue`.

Le panneau ne contient aucune règle de rendu : il affiche l'état de la
file (signaux ``jobs_changed`` / ``job_updated``) et traduit chaque
bouton en un appel de la file. La progression d'un job ne reconstruit
jamais le tableau : seule la ligne concernée est mise à jour, ce qui
garde l'interface fluide pendant un rendu.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.render_job import JobStatus, RenderJob
from core.render_presets import get_preset
from core.render_queue import RenderQueue
from ui import i18n
from ui.theme import COLORS, label_style

_JOB_ID_ROLE = Qt.UserRole
_COLUMNS = ("name", "preset", "status", "progress", "output")
_STATUS_COLORS = {
    JobStatus.WAITING: "muted",
    JobStatus.RENDERING: "accent",
    JobStatus.COMPLETED: "success",
    JobStatus.FAILED: "danger",
    JobStatus.CANCELLED: "muted",
}


def format_duration(seconds: float | None) -> str:
    """``73.4`` → ``1 min 13 s`` ; ``None`` → ``—``."""
    if seconds is None:
        return "—"
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} h {minutes:02d} min"
    if minutes:
        return f"{minutes} min {secs:02d} s"
    return f"{secs} s"


def format_size(size: int) -> str:
    """Taille lisible (``1,5 Mo`` en base 1024)."""
    value = float(size)
    for unit in ("o", "Ko", "Mo", "Go"):
        if value < 1024 or unit == "Go":
            return f"{value:.0f} {unit}" if unit == "o" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} o"  # pragma: no cover - inatteignable


def open_path(path: str) -> bool:
    """Ouvre un fichier ou un dossier avec l'application du système."""
    return QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def _button(key: str, *, primary: bool = False) -> QPushButton:
    button = QPushButton()
    button.setCursor(Qt.PointingHandCursor)
    button.setProperty("i18n_key", key)
    if primary:
        button.setStyleSheet(
            f"QPushButton {{ background: {COLORS['accent']}; border: none; font-weight: 700; padding: 6px 12px; }}"
            f"QPushButton:hover {{ background: {COLORS['accent_hover']}; }}"
            f"QPushButton:disabled {{ background: {COLORS['surface']}; color: #626875; }}"
        )
    return button


class RenderQueuePanel(QWidget):
    """Liste des jobs, progression globale, commandes et détail d'erreur."""

    def __init__(self, queue: RenderQueue, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("render_queue_panel")
        self._queue = queue
        self._items: dict[str, QTreeWidgetItem] = {}
        self._bars: dict[str, QProgressBar] = {}
        self._build_ui()
        queue.jobs_changed.connect(self.rebuild)
        queue.job_updated.connect(self._update_row)
        queue.run_state_changed.connect(self._refresh_state)
        queue.overall_progress_changed.connect(self._update_overall)
        callback = self._on_language_changed
        i18n.subscribe(callback)
        self.destroyed.connect(lambda *_: i18n.unsubscribe(callback))
        self.retranslate()
        self.rebuild()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.title_label = QLabel()
        self.title_label.setStyleSheet(label_style(13, "muted", 700))
        layout.addWidget(self.title_label)

        self.ffmpeg_banner = QLabel()
        self.ffmpeg_banner.setWordWrap(True)
        self.ffmpeg_banner.setStyleSheet(label_style(12, "danger", 600))
        layout.addWidget(self.ffmpeg_banner)

        overall_row = QHBoxLayout()
        self.overall_bar = QProgressBar()
        self.overall_bar.setRange(0, 100)
        self.overall_bar.setTextVisible(False)
        self.overall_bar.setFixedHeight(10)
        self.overall_label = QLabel()
        self.overall_label.setStyleSheet(label_style(12, "text", 600))
        overall_row.addWidget(self.overall_bar, 1)
        overall_row.addWidget(self.overall_label)
        layout.addLayout(overall_row)

        controls = QHBoxLayout()
        controls.setSpacing(6)
        self.start_all_button = _button("render.btn.start_all", primary=True)
        self.start_button = _button("render.btn.start")
        self.stop_button = _button("render.btn.stop")
        self.up_button = _button("render.btn.up")
        self.down_button = _button("render.btn.down")
        self.cancel_button = _button("render.btn.cancel")
        self.retry_button = _button("render.btn.retry")
        self.remove_button = _button("render.btn.remove")
        self.clear_button = _button("render.btn.clear")
        for widget in (self.start_all_button, self.start_button, self.stop_button):
            controls.addWidget(widget)
        controls.addSpacing(8)
        for widget in (self.up_button, self.down_button):
            controls.addWidget(widget)
        controls.addSpacing(8)
        for widget in (self.cancel_button, self.retry_button, self.remove_button, self.clear_button):
            controls.addWidget(widget)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.tree = QTreeWidget()
        self.tree.setObjectName("renderQueueTree")
        self.tree.setColumnCount(len(_COLUMNS))
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setMinimumHeight(140)
        layout.addWidget(self.tree, 1)

        self.empty_label = QLabel()
        self.empty_label.setWordWrap(True)
        self.empty_label.setStyleSheet(label_style(12, "muted", 500))
        layout.addWidget(self.empty_label)

        self.detail_label = QLabel()
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.detail_label.setStyleSheet(label_style(12, "text", 500))
        layout.addWidget(self.detail_label)

        detail_buttons = QHBoxLayout()
        self.open_file_button = _button("render.btn.open_file")
        self.open_folder_button = _button("render.btn.open_folder")
        self.copy_error_button = _button("render.btn.copy_error")
        for widget in (self.open_file_button, self.open_folder_button, self.copy_error_button):
            detail_buttons.addWidget(widget)
        detail_buttons.addStretch(1)
        layout.addLayout(detail_buttons)

        self.start_all_button.clicked.connect(self._on_start_all)
        self.start_button.clicked.connect(self._on_start_selected)
        self.stop_button.clicked.connect(self._queue.stop)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))
        self.cancel_button.clicked.connect(self._on_cancel)
        self.retry_button.clicked.connect(self._on_retry)
        self.remove_button.clicked.connect(self._on_remove)
        self.clear_button.clicked.connect(lambda: self._queue.clear_finished())
        self.open_file_button.clicked.connect(self._on_open_file)
        self.open_folder_button.clicked.connect(self._on_open_folder)
        self.copy_error_button.clicked.connect(self._on_copy_error)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.itemDoubleClicked.connect(lambda *_: self._on_open_file())

    # ------------------------------------------------------------------
    # Texte
    # ------------------------------------------------------------------

    def _on_language_changed(self, _code: str) -> None:
        self.retranslate()

    def retranslate(self) -> None:
        self.title_label.setText(i18n.translate("render.title"))
        self.empty_label.setText(i18n.translate("render.empty"))
        self.ffmpeg_banner.setText(i18n.translate("render.ffmpeg_missing"))
        self.tree.setHeaderLabels([i18n.translate(f"render.col.{name}") for name in _COLUMNS])
        for button in self.findChildren(QPushButton):
            key = button.property("i18n_key")
            if key:
                button.setText(i18n.translate(key))
        self.up_button.setText("▲")
        self.down_button.setText("▼")
        self.up_button.setToolTip(i18n.translate("render.btn.up"))
        self.down_button.setToolTip(i18n.translate("render.btn.down"))
        if self._items:
            self.rebuild()
        else:
            self._refresh_state()

    @staticmethod
    def preset_label(job: RenderJob) -> str:
        key = f"render.preset.{job.preset_id}"
        text = i18n.translate(key)
        return job.preset_id if text.startswith("[") else text

    # ------------------------------------------------------------------
    # Lignes
    # ------------------------------------------------------------------

    def selected_job(self) -> RenderJob | None:
        item = self.tree.currentItem()
        return self._queue.job(item.data(0, _JOB_ID_ROLE)) if item is not None else None

    def select_job(self, job_id: str) -> None:
        item = self._items.get(job_id)
        if item is not None:
            self.tree.setCurrentItem(item)

    def row_progress(self, job_id: str) -> int:
        return self._bars[job_id].value()

    def rebuild(self) -> None:
        """Reconstruit le tableau (ajout, suppression, ordre, changement d'état)."""
        selected = self.selected_job()
        self.tree.blockSignals(True)
        self.tree.clear()
        self._items.clear()
        self._bars.clear()
        for job in self._queue.jobs:
            item = QTreeWidgetItem()
            item.setData(0, _JOB_ID_ROLE, job.id)
            self.tree.addTopLevelItem(item)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setFixedHeight(14)
            bar.setTextVisible(True)
            self.tree.setItemWidget(item, _COLUMNS.index("progress"), bar)
            self._items[job.id] = item
            self._bars[job.id] = bar
            self._fill_row(job)
        for column in range(len(_COLUMNS)):
            self.tree.resizeColumnToContents(column)
        self.tree.setColumnWidth(_COLUMNS.index("progress"), max(120, self.tree.columnWidth(_COLUMNS.index("progress"))))
        if selected is not None and selected.id in self._items:
            self.tree.setCurrentItem(self._items[selected.id])
        self.tree.blockSignals(False)
        self._refresh_state()

    def _fill_row(self, job: RenderJob) -> None:
        item, bar = self._items[job.id], self._bars[job.id]
        item.setText(0, job.name)
        item.setText(1, self.preset_label(job))
        item.setText(2, i18n.translate(f"render.status.{job.status.value}"))
        item.setText(4, job.file_name)
        item.setToolTip(4, job.output_path)
        if job.status is JobStatus.FAILED and job.error_message:
            item.setToolTip(2, job.error_message[-600:])
        bar.setValue(job.progress)
        color = COLORS.get(_STATUS_COLORS[job.status], COLORS["text"])
        item.setForeground(2, QBrush(QColor(color)))
        bar.setStyleSheet(
            f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}"
        )

    def _update_row(self, job_id: str) -> None:
        job = self._queue.job(job_id)
        if job is None or job_id not in self._items:
            return
        self._fill_row(job)
        if self.selected_job() is job:
            self._refresh_state()

    def _update_overall(self, value: int) -> None:
        self._refresh_overall(value)

    # ------------------------------------------------------------------
    # État des boutons et des détails
    # ------------------------------------------------------------------

    def _on_selection_changed(self) -> None:
        self._refresh_state()

    def _refresh_overall(self, value: int | None = None) -> None:
        percent = self._queue.overall_progress() if value is None else value
        jobs = self._queue.jobs
        batch = [j for j in self._queue.run_jobs if j.status is not JobStatus.CANCELLED]
        self.overall_bar.setValue(percent)
        if self._queue.is_running and batch:
            done = sum(1 for j in batch if j.status is JobStatus.COMPLETED)
            self.overall_label.setText(
                i18n.translate("render.overall", done=done, total=len(batch), percent=percent)
            )
        else:
            self.overall_label.setText(i18n.translate("render.overall.idle"))

    def _refresh_state(self) -> None:
        queue = self._queue
        jobs = queue.jobs
        job = self.selected_job()
        available = queue.ffmpeg_available()
        self.ffmpeg_banner.setVisible(not available)
        self.empty_label.setVisible(not jobs)
        self.tree.setVisible(bool(jobs))
        has_waiting = any(j.status is JobStatus.WAITING for j in jobs)
        self.start_all_button.setEnabled(available and has_waiting and not queue.is_running)
        self.start_button.setEnabled(
            available and job is not None and job.status is JobStatus.WAITING
        )
        self.stop_button.setEnabled(queue.is_running)
        self.up_button.setEnabled(job is not None and job.status is JobStatus.WAITING)
        self.down_button.setEnabled(job is not None and job.status is JobStatus.WAITING)
        self.cancel_button.setEnabled(job is not None and not job.is_finished)
        self.retry_button.setEnabled(job is not None and job.can_retry)
        self.remove_button.setEnabled(job is not None and job.status is not JobStatus.RENDERING)
        self.clear_button.setEnabled(
            any(j.status in (JobStatus.COMPLETED, JobStatus.CANCELLED) for j in jobs)
        )
        output_exists = job is not None and job.status is JobStatus.COMPLETED and os.path.isfile(job.output_path)
        self.open_file_button.setEnabled(output_exists)
        self.open_folder_button.setEnabled(job is not None and Path(job.output_path).parent.is_dir())
        self.copy_error_button.setVisible(job is not None and bool(job.error_message))
        self.detail_label.setText(self._detail_text(job))
        self._refresh_overall()

    def _detail_text(self, job: RenderJob | None) -> str:
        if job is None:
            return i18n.translate("render.detail.none")
        lines = [f"{job.name}", i18n.translate("render.detail.output", path=job.output_path)]
        spec = get_preset(job.preset_id)
        lines.append(
            spec.summary() if spec is not None
            else f"{job.container.upper()} · {job.width}×{job.height} · {job.fps} fps"
        )
        if job.status is JobStatus.COMPLETED and job.result is not None:
            lines.append(i18n.translate("render.detail.render_time", time=format_duration(job.result.render_seconds)))
            lines.append(i18n.translate("render.detail.size", size=format_size(job.result.output_bytes)))
            if job.result.encoder:
                lines.append(i18n.translate("render.detail.encoder", encoder=job.result.encoder))
            if job.result.fallback_reason:
                lines.append(job.result.fallback_reason)
            if not os.path.isfile(job.output_path):
                lines.append(i18n.translate("render.detail.missing_file"))
        if job.error_message:
            kind = i18n.translate(f"render.kind.{job.error_kind}") if job.error_kind else ""
            heading = kind if kind and not kind.startswith("[") else i18n.translate("render.status.failed")
            lines.append(f"{heading} — {_tail(job.error_message)}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Commandes
    # ------------------------------------------------------------------

    def _on_start_all(self) -> None:
        self._queue.start_all()

    def _on_start_selected(self) -> None:
        job = self.selected_job()
        if job is not None:
            self._queue.start_job(job.id)

    def _move(self, offset: int) -> None:
        job = self.selected_job()
        if job is not None:
            self._queue.move(job.id, offset)
            self.select_job(job.id)

    def _on_cancel(self) -> None:
        job = self.selected_job()
        if job is not None:
            self._queue.cancel(job.id)

    def _on_retry(self) -> None:
        job = self.selected_job()
        if job is None:
            return
        if job.status is JobStatus.COMPLETED and os.path.isfile(job.output_path):
            answer = QMessageBox.question(
                self,
                i18n.translate("render.confirm.title"),
                i18n.translate("render.confirm.rerender"),
            )
            if answer != QMessageBox.Yes:
                return
        self._queue.retry(job.id)

    def _on_remove(self) -> None:
        job = self.selected_job()
        if job is not None:
            self._queue.remove(job.id)

    def _on_open_file(self) -> None:
        job = self.selected_job()
        if job is not None and job.status is JobStatus.COMPLETED and os.path.isfile(job.output_path):
            open_path(job.output_path)

    def _on_open_folder(self) -> None:
        job = self.selected_job()
        if job is not None:
            folder = str(Path(job.output_path).parent)
            if os.path.isdir(folder):
                open_path(folder)

    def _on_copy_error(self) -> None:
        job = self.selected_job()
        if job is not None and job.error_message:
            QGuiApplication.clipboard().setText(job.error_message)


def _tail(message: str, limit: int = 600) -> str:
    """Fin d'un message d'erreur (la partie utile d'une sortie FFmpeg)."""
    text = message.strip()
    return text if len(text) <= limit else "…" + text[-limit:]


__all__ = ["RenderQueuePanel", "format_duration", "format_size", "open_path"]
