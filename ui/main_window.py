import os

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QAction, QCursor
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QMainWindow, QMenu, QSplitter, QVBoxLayout, QWidget

from core.effects import apply_color_effect, play_crossfade_preview, save_subtitles, set_volume
from ui.preview_panel import PreviewPanel
from ui.project_panel import ProjectPanel
from ui.properties_panel import PropertiesPanel
from ui.timeline_panel import TimelinePanel


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Premiere Simple - v0.4")
        self.setGeometry(100, 100, 1200, 700)
        self.subtitle_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "subtitles.srt")
        self.active_subtitle_clip = None
        self.transition_seconds = None
        self._build_menu_bar()

        self.preview_panel = PreviewPanel(self.toggle_play, self.stop_playback, self.seek_relative)
        self.project_panel = ProjectPanel(self.load_video)
        self.properties_panel = PropertiesPanel(self.update_color_effect, self.update_volume, self.save_subtitles)
        self.timeline_panel = TimelinePanel()

        self.preview_panel.player.durationChanged.connect(self.timeline_panel.setDuration)
        self.preview_panel.player.positionChanged.connect(self.timeline_panel.setPlaybackPosition)
        self.preview_panel.player.playbackStateChanged.connect(self.on_playback_state_changed)
        self.timeline_panel.play_button.clicked.connect(self.toggle_play)
        self.timeline_panel.seek_requested.connect(self.seek_to_position)
        self.timeline_panel.clip_clicked.connect(self.on_clip_selected)
        self.timeline_panel.transition_clicked.connect(self.offer_transition)
        self.properties_panel.subtitle_editor.textChanged.connect(self.update_subtitle_from_editor)

        top_split = QSplitter(Qt.Horizontal)
        top_split.addWidget(self.project_panel)
        top_split.addWidget(self.preview_panel)
        top_split.addWidget(self.properties_panel)
        top_split.setSizes([220, 650, 260])
        main_split = QSplitter(Qt.Vertical)
        main_split.addWidget(top_split)
        main_split.addWidget(self.timeline_panel)
        main_split.setSizes([500, 270])
        self.setCentralWidget(main_split)

        self.timeline_timer = QTimer(self)
        self.timeline_timer.setInterval(40)
        self.timeline_timer.timeout.connect(self.update_timeline)
        self.timeline_timer.start()
        self.setStyleSheet(self.global_style())

    def _build_menu_bar(self):
        menu_bar = self.menuBar()
        menu_bar.setNativeMenuBar(False)
        file_menu = QMenu("Fichier", self)
        for label in ("Nouveau", "Ouvrir"):
            file_menu.addAction(QAction(label, self))
        file_menu.addSeparator()
        for label in ("Enregistrer", "Enregistrer sous..."):
            file_menu.addAction(QAction(label, self))
        file_menu.addSeparator()
        exit_action = QAction("Quitter", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        edit_menu = QMenu("Édition", self)
        for label in ("Annuler", "Rétablir"):
            edit_menu.addAction(QAction(label, self))
        edit_menu.addSeparator()
        for label in ("Couper", "Copier", "Coller"):
            edit_menu.addAction(QAction(label, self))
        sequence_menu = QMenu("Séquence", self)
        for label in ("Ajouter un clip", "Couper / Réduire", "Marqueur"):
            sequence_menu.addAction(QAction(label, self))
        window_menu = QMenu("Fenêtre", self)
        window_menu.addAction(QAction("Réinitialiser la disposition", self))
        for menu in (file_menu, edit_menu, sequence_menu, window_menu):
            menu_bar.addMenu(menu)

    @staticmethod
    def global_style():
        return (
            "QMainWindow { background: #121212; color: white; }"
            "QWidget { color: white; }"
            "QSplitter::handle { background: #2a2a2a; }"
            "QSplitter::handle:vertical { width: 4px; }"
            "QSplitter::handle:horizontal { height: 4px; }"
            "QLabel { color: white; }"
            "QPushButton { background: #2d2d2d; color: white; border: 1px solid #3a3a3a; border-radius: 8px; padding: 7px 12px; }"
            "QPushButton:hover { background: #3a3a3a; }"
            "QListWidget::item { background: transparent; }"
            "QMenuBar { background: #1b1b1b; color: #f3f3f3; border-bottom: 1px solid #2d2d2d; padding: 4px; }"
            "QMenuBar::item { background: transparent; padding: 6px 10px; border-radius: 6px; }"
            "QMenuBar::item:selected { background: #2f5d9a; }"
            "QMenu { background: #1d1d1d; border: 1px solid #2d2d2d; color: #f3f3f3; }"
            "QMenu::item { padding: 7px 20px; }"
            "QMenu::item:selected { background: #2f5d9a; }"
        )

    def on_playback_state_changed(self, state):
        is_playing = state == QMediaPlayer.PlayingState
        self.timeline_panel.setPlayState(is_playing)
        self.preview_panel.play_button.setText("❚❚ Pause" if is_playing else "▶ Play")

    def update_timeline(self):
        if self.preview_panel.player.playbackState() == QMediaPlayer.PlayingState:
            self.timeline_panel.setPlaybackPosition(self.preview_panel.player.position())
        self.update_subtitle_overlay(self.timeline_panel.playhead_seconds)

    def on_clip_selected(self, clip):
        self.active_subtitle_clip = clip if clip["track"] == 2 else None
        self.properties_panel.set_clip(clip, self.timeline_panel.track_names[clip["track"]])

    def update_subtitle_from_editor(self):
        if self.active_subtitle_clip is None:
            return
        self.active_subtitle_clip["text"] = self.properties_panel.subtitle_editor.toPlainText()
        self.save_subtitles()
        self.update_subtitle_overlay(self.timeline_panel.playhead_seconds)

    def update_subtitle_overlay(self, seconds):
        subtitle = next(
            (clip for clip in self.timeline_panel.clips if clip["track"] == 2 and clip["start"] <= seconds <= clip["end"]),
            None,
        )
        if subtitle is not None and subtitle.get("text", "").strip():
            self.preview_panel.preview_subtitle_overlay.setText(subtitle["text"].strip())
            self.preview_panel.preview_subtitle_overlay.show()
        else:
            self.preview_panel.preview_subtitle_overlay.hide()

    def save_subtitles(self):
        save_subtitles(self.timeline_panel.clips, self.subtitle_file)

    def update_color_effect(self, *_):
        panel = self.properties_panel
        apply_color_effect(
            self.preview_panel.color_effect,
            panel.brightness_slider.value(),
            panel.contrast_slider.value(),
            panel.saturation_slider.value(),
        )

    def update_volume(self, value):
        self.properties_panel.volume_value.setText(f"{value} %")
        set_volume(self.preview_panel.audio_output, value)

    def offer_transition(self, transition_time):
        menu = QMenu(self)
        menu.addAction(f"Jonction à {transition_time:.2f}s")
        menu.addSeparator()
        crossfade = menu.addAction("Fondu enchaîné · 0.5 s")
        if menu.exec(QCursor.pos()) is crossfade:
            self.transition_seconds = transition_time
            self.transition_animation = play_crossfade_preview(self.preview_panel.preview_transition_overlay, self)

    def seek_to_position(self, seconds):
        self.preview_panel.player.setPosition(int(seconds * 1000))
        self.update_subtitle_overlay(seconds)

    def load_video(self, item):
        path = item.text()
        if path.endswith((".mp4", ".mov", ".avi")):
            self.preview_panel.load_video(path)

    def stop_playback(self):
        self.preview_panel.player.stop()
        self.timeline_panel.setPlaybackPosition(0)
        self.preview_panel.player.setPosition(0)

    def toggle_play(self):
        if self.preview_panel.player.playbackState() == QMediaPlayer.PlayingState:
            self.preview_panel.player.pause()
        else:
            self.preview_panel.player.play()

    def seek_relative(self, delta_seconds):
        current_ms = self.preview_panel.player.position()
        new_pos = max(0, min(self.preview_panel.player.duration(), current_ms + int(delta_seconds * 1000)))
        self.preview_panel.player.setPosition(new_pos)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space:
            self.toggle_play()
            event.accept()
            return
        if event.key() in (Qt.Key_Left, Qt.Key_J):
            self.seek_relative(-2)
            event.accept()
            return
        if event.key() in (Qt.Key_Right, Qt.Key_L):
            self.seek_relative(2)
            event.accept()
            return
        if event.key() == Qt.Key_K:
            self.toggle_play()
            event.accept()
            return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            self.project_panel.add_file(url.toLocalFile())
