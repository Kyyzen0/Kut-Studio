"""Vignettes et formes d'onde des clips : planification et cache."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap

from core.cache_keys import file_exists
from core.media_previews import (
    extract_thumbnail,
    extract_waveform_peaks,
    thumbnail_cache_key,
    thumbnail_slots,
    thumbnail_source_times,
    waveform_bins,
    waveform_cache_key,
)
from core.task_queue import PRIORITY_VISIBLE
from core.timeline_view_model import (
    TimelineClipView,
)


class PreviewsMixin:
    """Mixin de ``TimelinePanel`` : vignettes et formes d'onde des clips : planification et cache."""

    def synthetic_thumb(self, view: TimelineClipView, index: int, slots: int) -> QPixmap:
        """Vignette de secours, peinte sans décoder le média."""
        key = f"synth:{view.id}:{index}:{slots}"
        cached = self._pixmaps.get(key)
        if cached is not None:
            return cached
        image = QImage(160, 90, QImage.Format_RGB32)
        color = QColor(view.color_key)
        image.fill(color.darker(110 + index * 18))
        painter = QPainter(image)
        painter.setPen(QColor("white"))
        painter.drawText(image.rect(), Qt.AlignCenter, view.label or str(index + 1))
        painter.end()
        pixmap = QPixmap.fromImage(image)
        if len(self._pixmaps) > 48:
            self._pixmaps.clear()
        self._pixmaps[key] = pixmap
        return pixmap

    def attach_runtime(self, runtime) -> None:
        self._runtime = runtime

    def attach_preview_panel(self, preview_panel) -> None:
        """Référence faible vers le panneau de prévisualisation.

        Le timecode turquoise de la barre de transport se met à jour
        à chaque changement de tête de lecture. On garde une référence
        faible (champ ``_preview_panel``) pour pouvoir la nettoyer si
        le panneau est détruit avant la timeline.
        """
        self._preview_panel = preview_panel
        # Synchronisation immédiate pour aligner les deux horloges.
        if hasattr(preview_panel, "set_timecode"):
            preview_panel.set_timecode(self.playhead_seconds, self.duration_seconds)

    def pixmap_for(self, key: str, data: bytes):
        pixmap = self._pixmaps.get(key)
        if pixmap is None:
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            if len(self._pixmaps) > 48:
                self._pixmaps.clear()
            self._pixmaps[key] = pixmap
        return pixmap

    def _schedule_previews(self) -> None:
        runtime = self._runtime
        if runtime is None or self.project is None:
            return
        filmstrips = runtime.resolved_profile().filmstrips
        # Uniquement les clips **montés** (visibles) : planifier des
        # vignettes pour des milliers de clips hors écran serait du travail
        # perdu. L'existence du fichier est mémorisée (``file_exists``) :
        # un appel système par clip à chaque défilement était le coût dominant.
        for widget in list(self.clip_widgets.values()):
            view = widget.view
            if not view.source_path or not file_exists(view.source_path):
                continue
            if self.track_is_collapsed(view.track_id):
                continue
            if view.track_type == "audio":
                bins = waveform_bins(max(widget.width(), 16), self.track_height_mode(view.track_id))
                key = waveform_cache_key(view.source_path, bins)
                if runtime.cache.get(key) is None:
                    self._submit_preview(
                        key,
                        lambda token, path=view.source_path, count=bins, cache_key=key: self._waveform_job(
                            token, path, count, cache_key
                        ),
                    )
            elif view.track_type == "video" and filmstrips:
                clip = self.clip_model(view.id)
                if clip is None:
                    continue
                slots = thumbnail_slots(widget.width(), enabled=True)
                for instant in thumbnail_source_times(clip.source_in, clip.source_out, slots):
                    thumb_key = thumbnail_cache_key(view.source_path, instant, 160)
                    if runtime.cache.get(thumb_key) is None:
                        self._submit_preview(
                            thumb_key,
                            lambda token, path=view.source_path, time=instant, cache_key=thumb_key: self._thumb_job(
                                token, path, time, cache_key
                            ),
                        )

    def _submit_preview(self, key: str, fn) -> None:
        runtime = self._runtime
        if runtime is None:
            return
        runtime.schedule(key, fn, priority=PRIORITY_VISIBLE)
        if self._preview_timer is None:
            self._preview_timer = QTimer(self)
            self._preview_timer.setInterval(300)
            self._preview_timer.timeout.connect(self._drain_previews)
        if not self._preview_timer.isActive():
            self._preview_timer.start()

    def _waveform_job(self, token, path: str, bins: int, key: str) -> None:
        if token.cancelled or self._runtime is None:
            return
        peaks = extract_waveform_peaks(path, bins)
        self._runtime.mailbox.push(
            key,
            peaks if peaks else (),
            max(32, bins * 8),
            session_id=self._runtime.session_id,
        )

    def _thumb_job(self, token, path: str, instant: float, key: str) -> None:
        if token.cancelled or self._runtime is None:
            return
        image = extract_thumbnail(path, instant, 160)
        self._runtime.mailbox.push(
            key,
            image if image else b"",
            len(image) if image else 1,
            session_id=self._runtime.session_id,
        )

    def _drain_previews(self) -> None:
        runtime = self._runtime
        if runtime is None:
            return
        # Le filtre de session est appliqué par la mailbox : un résultat
        # produit pour un projet déjà remplacé n'atteint jamais le cache.
        items = runtime.mailbox.drain(runtime.session_id)
        for key, value, size, namespace in items:
            runtime.cache.put(key, value, size_bytes=size, namespace=namespace)
        if items:
            for widget in self.clip_widgets.values():
                widget.update()
        if runtime.tasks.pending == 0 and not items and self._preview_timer is not None:
            self._preview_timer.stop()
