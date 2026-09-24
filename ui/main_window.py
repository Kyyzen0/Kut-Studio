import os

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QAction, QCursor
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.effects import apply_color_effect, play_crossfade_preview, save_subtitles, set_volume
from core.export_engine import ExportEngine
from core.project_factory import create_default_project
from core.project_model import Project
from core.timeline_operations import cut_clip, delete_clip, find_clip, move_clip, trim_clip_left, trim_clip_right
from core.timeline_view_model import build_export_clips
from ui.preview_panel import PreviewPanel
from ui.project_panel import ProjectPanel
from ui.properties_panel import PropertiesPanel
from ui.timeline_panel import TimelinePanel
from ui.export_panel import ExportPanel
from ui.theme import COLORS, global_stylesheet, label_style


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Kut-Studio")
        self.setMinimumSize(1080, 680)
        self.resize(1440, 900)
        self.subtitle_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "subtitles.srt")
        self.active_subtitle_clip = None
        self.transition_seconds = None
        # ``Project`` est désormais l'unique source de vérité de la timeline.
        self.project: Project = create_default_project()
        self._build_menu_bar()

        self.preview_panel = PreviewPanel(
            self.toggle_play,
            self.stop_playback,
            self.seek_relative,
            self.cut_at_playhead,
            self.open_video_file,
        )
        self.project_panel = ProjectPanel(self.load_video)
        self.properties_panel = PropertiesPanel(self.update_color_effect, self.update_volume, self.save_subtitles)
        self.timeline_panel = TimelinePanel(self.project)
        self.export_panel = ExportPanel()
        self.properties_panel.timeline_panel = self.timeline_panel
        self.export_panel.export_requested.connect(self.launch_export)
        self.export_panel.close_requested.connect(self.show_editor)
        self.export_panel.cancel_requested.connect(self.cancel_export)

        self.export_engine = ExportEngine(self)
        self.export_engine.progress_changed.connect(self.export_panel.progress_bar.setValue)
        self.export_engine.status_changed.connect(self.export_panel.set_status)
        self.export_engine.finished_ok.connect(self._on_export_finished)
        self.export_engine.failed.connect(self.export_panel.mark_export_error)
        self.export_engine.cancelled.connect(self.export_panel.mark_export_cancelled)

        self.preview_panel.player.durationChanged.connect(self.timeline_panel.setDuration)
        self.preview_panel.player.positionChanged.connect(self.timeline_panel.setPlaybackPosition)
        self.preview_panel.player.playbackStateChanged.connect(self.on_playback_state_changed)
        self.timeline_panel.play_button.clicked.connect(self.toggle_play)
        self.timeline_panel.seek_requested.connect(self.seek_to_position)
        self.timeline_panel.clip_selected.connect(self.on_clip_selected)
        self.timeline_panel.transition_clicked.connect(self.offer_transition)
        self.timeline_panel.move_clip_requested.connect(self.on_move_clip_requested)
        self.timeline_panel.trim_clip_left_requested.connect(self.on_trim_left_requested)
        self.timeline_panel.trim_clip_right_requested.connect(self.on_trim_right_requested)
        self.properties_panel.cut_requested.connect(self.cut_selected_clip)
        self.properties_panel.delete_requested.connect(self.delete_selected_clip)
        self.properties_panel.subtitle_editor.textChanged.connect(self.update_subtitle_from_editor)

        top_split = QSplitter(Qt.Horizontal)
        top_split.setObjectName("workspace_splitter")
        top_split.addWidget(self.project_panel)
        top_split.addWidget(self.preview_panel)
        top_split.addWidget(self.properties_panel)
        top_split.setSizes([220, 650, 260])
        top_split.setStretchFactor(0, 0)
        top_split.setStretchFactor(1, 1)
        top_split.setStretchFactor(2, 0)
        main_split = QSplitter(Qt.Vertical)
        main_split.addWidget(top_split)
        main_split.addWidget(self.timeline_panel)
        main_split.setSizes([500, 270])
        main_split.setStretchFactor(0, 1)
        main_split.setStretchFactor(1, 0)

        self.editor_page = QWidget()
        editor_layout = QVBoxLayout(self.editor_page)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.addWidget(main_split)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.editor_page)
        self.pages.addWidget(self.export_panel)

        shell = QWidget()
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        shell_layout.addWidget(self._build_top_bar())
        shell_layout.addWidget(self.pages)
        self.setCentralWidget(shell)

        self.timeline_timer = QTimer(self)
        self.timeline_timer.setInterval(40)
        self.timeline_timer.timeout.connect(self.update_timeline)
        self.timeline_timer.start()
        self.setStyleSheet(global_stylesheet())

    def _build_top_bar(self):
        bar = QWidget()
        bar.setFixedHeight(56)
        bar.setStyleSheet(
            f"background: {COLORS['panel']}; border-bottom: 1px solid {COLORS['border']};"
        )
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(18, 0, 18, 0)
        layout.setSpacing(14)

        logo = QLabel("K")
        logo.setAlignment(Qt.AlignCenter)
        logo.setFixedSize(28, 28)
        logo.setStyleSheet(
            f"background: {COLORS['accent']}; color: white; border-radius: 7px; font-size: 15px; font-weight: 800;"
        )
        brand = QLabel("KUT-STUDIO")
        brand.setStyleSheet(label_style(13, "text", 800))
        project = QLabel("Mon montage  /  Projet sans titre")
        project.setStyleSheet(label_style(12, "muted", 500))
        saved = QLabel("●  Enregistré")
        saved.setStyleSheet(label_style(11, "success", 600))
        self.export_button = QPushButton("Exporter")
        self.export_button.setCursor(Qt.PointingHandCursor)
        self.export_button.setStyleSheet(
            f"QPushButton {{ background: {COLORS['accent']}; border: none; padding: 8px 17px; font-weight: 700; }}"
            f"QPushButton:hover {{ background: {COLORS['accent_hover']}; }}"
        )
        self.export_button.clicked.connect(self.show_export)
        layout.addWidget(logo)
        layout.addWidget(brand)
        layout.addSpacing(18)
        layout.addWidget(project)
        layout.addWidget(saved)
        layout.addStretch()
        layout.addWidget(self.export_button)
        return bar

    def show_export(self):
        self.pages.setCurrentWidget(self.export_panel)

    def show_editor(self):
        self.pages.setCurrentWidget(self.editor_page)

    def launch_export(self):
        default_dir = os.path.expanduser("~/Movies")
        os.makedirs(default_dir, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Enregistrer l'export",
            os.path.join(default_dir, "kut-studio-export.mp4"),
            "Vidéos (*.mp4 *.mov)",
        )
        if not path:
            return
        try:
            request = self.export_panel.build_request(self.get_export_clips(), path)
        except Exception as exc:
            self.export_panel.mark_export_error(f"Paramètres invalides : {exc}")
            return
        self.export_panel.mark_export_started()
        self.export_engine.start(request)

    def get_export_clips(self) -> list[dict]:
        """Retourne les dictionnaires attendus par ExportEngine (adaptateur)."""
        return build_export_clips(self.project)

    def cancel_export(self):
        self.export_engine.cancel()

    def _on_export_finished(self, output_path):
        self.export_panel.mark_export_finished()
        QMessageBox.information(
            self,
            "Export terminé",
            f"L'export est terminé avec succès.\n\nFichier : {output_path}",
        )

    def _build_menu_bar(self):
        menu_bar = self.menuBar()
        menu_bar.setNativeMenuBar(False)

        # Fichier
        file_menu = QMenu("Fichier", self)
        new_action = QAction("Nouveau", self)
        new_action.setShortcut("Ctrl+N")
        new_action.triggered.connect(lambda: self._notify_placeholder("Nouveau projet"))
        open_action = QAction("Ouvrir...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_video_file)
        save_action = QAction("Enregistrer", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(lambda: self._notify_placeholder("Enregistrement projet"))
        save_as_action = QAction("Enregistrer sous...", self)
        save_as_action.setShortcut("Ctrl+Shift+S")
        save_as_action.triggered.connect(lambda: self._notify_placeholder("Enregistrement projet"))
        file_menu.addAction(new_action)
        file_menu.addAction(open_action)
        file_menu.addSeparator()
        file_menu.addAction(save_action)
        file_menu.addAction(save_as_action)
        file_menu.addSeparator()
        exit_action = QAction("Quitter", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Édition
        edit_menu = QMenu("Édition", self)
        for label in ("Annuler", "Rétablir"):
            action = QAction(label, self)
            action.triggered.connect(lambda checked=False, l=label: self._notify_placeholder(l))
            edit_menu.addAction(action)
        edit_menu.addSeparator()
        for label in ("Couper", "Copier", "Coller"):
            action = QAction(label, self)
            action.triggered.connect(lambda checked=False, l=label: self._notify_placeholder(l))
            edit_menu.addAction(action)

        # Séquence
        sequence_menu = QMenu("Séquence", self)
        for label in ("Ajouter un clip", "Couper / Réduire", "Marqueur"):
            action = QAction(label, self)
            action.triggered.connect(lambda checked=False, l=label: self._notify_placeholder(l))
            sequence_menu.addAction(action)

        # Fenêtre
        window_menu = QMenu("Fenêtre", self)
        reset_action = QAction("Réinitialiser la disposition", self)
        reset_action.triggered.connect(lambda: self._notify_placeholder("Reset disposition"))
        window_menu.addAction(reset_action)

        for menu in (file_menu, edit_menu, sequence_menu, window_menu):
            menu_bar.addMenu(menu)

    def _notify_placeholder(self, feature_name):
        """Affiche un message discret pour les features à venir."""
        print(f"[MainWindow] {feature_name} : à implémenter")

    @staticmethod
    def global_style():
        return global_stylesheet()

    def on_playback_state_changed(self, state):
        is_playing = state == QMediaPlayer.PlayingState
        self.timeline_panel.setPlayState(is_playing)
        self.preview_panel.play_button.setText("❚❚ Pause" if is_playing else "▶ Play")

    def update_timeline(self):
        if self.preview_panel.player.playbackState() == QMediaPlayer.PlayingState:
            self.timeline_panel.setPlaybackPosition(self.preview_panel.player.position())
        self.update_subtitle_overlay(self.timeline_panel.playhead_seconds)

    def on_clip_selected(self, clip_id):
        view = self.timeline_panel.find_view_by_id(clip_id)
        if view is None:
            return
        self.active_subtitle_clip = view if view.track_id == "S1" else None
        self.properties_panel.show_clip(view)
        self.timeline_panel.playhead_seconds = view.start
        self.timeline_panel.time_label.setText(
            self.timeline_panel.format_time(view.start)
        )
        self.preview_panel.player.setPosition(int(view.start * 1000))
        self.update_subtitle_overlay(view.start)

    def on_move_clip_requested(self, clip_id: str, new_timeline_start: float) -> None:
        """Applique un déplacement demandé par la timeline."""
        try:
            move_clip(self.project, clip_id, new_timeline_start)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] move refusé : {exc}")
        self.timeline_panel.set_project(self.project)

    def on_trim_left_requested(self, clip_id: str, new_timeline_start: float) -> None:
        try:
            trim_clip_left(self.project, clip_id, new_timeline_start)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] trim gauche refusé : {exc}")
        self.timeline_panel.set_project(self.project)

    def on_trim_right_requested(self, clip_id: str, new_timeline_end: float) -> None:
        try:
            trim_clip_right(self.project, clip_id, new_timeline_end)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] trim droit refusé : {exc}")
        self.timeline_panel.set_project(self.project)

    def cut_at_playhead(self):
        clip_id = self.timeline_panel.selected_clip_id
        if clip_id is None:
            print("[MainWindow] Cut : aucun clip sélectionné")
            return
        self.cut_selected_clip(clip_id, self.timeline_panel.playhead_seconds)

    def open_video_file(self):
        """Ouvre un dialogue pour charger une vidéo et l'ajoute au projet."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Ouvrir une vidéo",
            os.path.expanduser("~/Movies"),
            "Vidéos (*.mp4 *.mov *.avi *.mkv *.webm)",
        )
        if not path:
            return
        self.project_panel.add_file(path)
        # Sélectionner le nouveau fichier dans le bin
        last_index = self.project_panel.bin.count() - 1
        if last_index >= 0:
            self.project_panel.bin.setCurrentRow(last_index)
        # Charger dans le preview
        self.preview_panel.load_video(path)

    def cut_selected_clip(self, clip_id, playhead_pos):
        try:
            cut_clip(self.project, clip_id, playhead_pos)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] cut refusé : {exc}")
            return
        self.timeline_panel.set_project(self.project)
        # Tenter de conserver la sélection : si l'ancien id existe encore
        # (clip gauche de la coupe), on le re-sélectionne, sinon on prend
        # le clip V1 actif autour du playhead.
        new_id = clip_id
        if self.timeline_panel.find_view_by_id(new_id) is None:
            view_at_playhead = next(
                (
                    v
                    for v in self.timeline_panel.clip_views
                    if v.track_id == "V1" and v.start <= playhead_pos <= v.end
                ),
                None,
            )
            new_id = view_at_playhead.id if view_at_playhead is not None else None
        if new_id is not None:
            self.timeline_panel.select_clip(new_id)
            self.on_clip_selected(new_id)
        else:
            self.properties_panel.set_clip(None, "")
            self.timeline_panel.selected_clip_id = None

    def delete_selected_clip(self, clip_id):
        try:
            delete_clip(self.project, clip_id)
        except KeyError as exc:
            print(f"[MainWindow] delete refusé : {exc}")
            return
        self.timeline_panel.selected_clip_id = None
        self.active_subtitle_clip = None
        self.properties_panel.set_clip(None, "")
        self.timeline_panel.set_project(self.project)

    def update_subtitle_from_editor(self):
        if self.active_subtitle_clip is None:
            return
        new_text = self.properties_panel.subtitle_editor.toPlainText()
        try:
            clip = find_clip(self.project, self.active_subtitle_clip.id)
        except KeyError:
            return

        # 1. Mettre à jour le modèle métier.
        clip.text = new_text

        # 2. Rafraîchir immédiatement la projection de timeline.
        #    ``set_project`` réinitialise ``selected_clip_id`` ; on le
        #    restaure juste après et on repeint le widget pour le border.
        self.timeline_panel.set_project(self.project)
        self.timeline_panel.selected_clip_id = self.active_subtitle_clip.id
        self.timeline_panel.refresh_clip_widgets()

        # 3. Mettre à jour l'overlay de preview.
        self.update_subtitle_overlay(self.timeline_panel.playhead_seconds)

        # 4. Sauvegarde ``.srt`` en meilleure effort : un échec I/O ne
        #    doit jamais bloquer la mise à jour du modèle ni lever dans
        #    la boucle Qt.
        try:
            self.save_subtitles()
        except OSError as exc:
            print(f"[MainWindow] sauvegarde .srt impossible : {exc}")

    def update_subtitle_overlay(self, seconds):
        subtitle_clip = self._subtitle_clip_at(seconds)
        if subtitle_clip is not None and subtitle_clip.text.strip():
            self.preview_panel.preview_subtitle_overlay.setText(subtitle_clip.text.strip())
            self.preview_panel.preview_subtitle_overlay.show()
        else:
            self.preview_panel.preview_subtitle_overlay.hide()

    def _subtitle_clip_at(self, seconds):
        """Retourne le clip de sous-titre actif à ``seconds`` ou ``None``."""
        for track in self.project.tracks:
            if track.id != "S1":
                continue
            for clip in track.clips:
                if clip.timeline_start <= seconds <= clip.timeline_start + clip.duration:
                    return clip
        return None

    def save_subtitles(self):
        export_clips = build_export_clips(self.project)
        save_subtitles(export_clips, self.subtitle_file)

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
