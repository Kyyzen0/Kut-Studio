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
from core.media_probe import MediaProbeError, probe_media, probe_video
from core.project_factory import create_default_project
from core.project_io import load_project, save_project
from core.project_model import MediaAsset, Project
from core.timeline_operations import (
    add_clip_to_track,
    cut_clip,
    delete_clip,
    find_clip,
    move_clip,
    trim_clip_left,
    trim_clip_right,
)
from core.timeline_evaluator import (
    ActiveClip,
    evaluate_timeline,
    timeline_duration,
)
from core.render_plan import RenderPlan, build_render_plan
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
        # État du document courant pour la persistance ``.kut``.
        self.current_project_path: str | None = None
        self.project_dirty: bool = False
        self._build_menu_bar()

        self.preview_panel = PreviewPanel(
            self.toggle_play,
            self.stop_playback,
            self.seek_relative,
            self.cut_at_playhead,
            self.import_media_via_dialog,
        )
        self.project_panel = ProjectPanel()
        self.project_panel.asset_selected.connect(self.preview_media_asset)
        self.project_panel.add_to_timeline_requested.connect(
            self.add_asset_to_timeline
        )
        self.project_panel.import_requested.connect(self.import_media_via_dialog)
        self.properties_panel = PropertiesPanel(self.update_color_effect, self.update_volume, self.save_subtitles)
        self.timeline_panel = TimelinePanel(self.project)
        self.export_panel = ExportPanel()
        self.properties_panel.timeline_panel = self.timeline_panel
        self.export_panel.export_requested.connect(self.launch_export)
        self.export_panel.close_requested.connect(self.show_editor)
        self.export_panel.cancel_requested.connect(self.cancel_export)
        # Synchroniser la bibliothèque de médias avec le Project initial.
        self._refresh_project_library()

        self.export_engine = ExportEngine(self)
        self.export_engine.progress_changed.connect(self.export_panel.progress_bar.setValue)
        self.export_engine.status_changed.connect(self.export_panel.set_status)
        self.export_engine.finished_ok.connect(self._on_export_finished)
        self.export_engine.failed.connect(self.export_panel.mark_export_error)
        self.export_engine.cancelled.connect(self.export_panel.mark_export_cancelled)

        # La timeline est désormais l'horloge principale : on ne lit plus
        # ``QMediaPlayer.positionChanged`` pour piloter ``TimelinePanel``.
        # Le lecteur ``QMediaPlayer`` n'est qu'un consommateur de la position
        # de la timeline (voir ``_sync_preview_to_timeline``).
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

        # Initialisation de l'horloge de programme (tâche 8).
        self.playhead_seconds: float = 0.0
        self.is_playing: bool = False
        self._last_tick_monotonic: float | None = None
        # Affichage initial de la durée totale (clip de démo = 12 s).
        self._update_timeline_duration()
        self._sync_preview_to_timeline()

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
        self.timeline_timer.timeout.connect(self._tick_playback)
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
        self.project_label = QLabel()
        self.project_label.setStyleSheet(label_style(12, "muted", 500))
        self.saved_indicator = QLabel()
        self.saved_indicator.setStyleSheet(label_style(11, "success", 600))
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
        layout.addWidget(self.project_label)
        layout.addWidget(self.saved_indicator)
        layout.addStretch()
        layout.addWidget(self.export_button)
        self._update_top_bar()
        return bar

    def show_export(self):
        self.pages.setCurrentWidget(self.export_panel)

    def show_editor(self):
        self.pages.setCurrentWidget(self.editor_page)

    # ------------------------------------------------------------------
    # État du document et persistance ``.kut``
    # ------------------------------------------------------------------

    def _update_top_bar(self) -> None:
        """Met à jour le nom du projet et l'indicateur « Enregistré / Non enregistré »."""
        name = self.project.name if self.project is not None else "Projet sans titre"
        self.project_label.setText(f"Mon montage  /  {name}")
        if self.project_dirty:
            self.saved_indicator.setText("●  Non enregistré")
            self.saved_indicator.setStyleSheet(label_style(11, "danger", 600))
        else:
            self.saved_indicator.setText("●  Enregistré")
            self.saved_indicator.setStyleSheet(label_style(11, "success", 600))

    def _mark_dirty(self) -> None:
        self.project_dirty = True
        self._update_top_bar()

    def _mark_clean(self) -> None:
        self.project_dirty = False
        self._update_top_bar()

    def _reset_selection_and_inspector(self) -> None:
        """Réinitialise la sélection de clip et l'inspecteur après un changement de projet."""
        self.active_subtitle_clip = None
        self.timeline_panel.selected_clip_id = None
        self.properties_panel.set_clip(None, "")

    def new_project(self) -> None:
        """Crée un nouveau projet vierge via ``create_default_project()``."""
        self.project = create_default_project()
        self.current_project_path = None
        self.timeline_panel.set_project(self.project)
        self.playhead_seconds = 0.0
        self.is_playing = False
        self._update_timeline_duration()
        self._sync_preview_to_timeline()
        self._reset_selection_and_inspector()
        self._mark_clean()

    def save_project_file(self) -> None:
        """Enregistre le projet courant. Délègue à ``save_project_as`` si aucun chemin."""
        if self.current_project_path is None:
            self.save_project_as()
            return
        try:
            save_project(self.project, self.current_project_path)
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Enregistrement impossible",
                f"Impossible d'enregistrer le projet :\n\n{exc}",
            )
            return
        self._mark_clean()

    def save_project_as(self) -> None:
        """Ouvre un dialogue pour choisir un chemin ``.kut`` et enregistre le projet."""
        if self.current_project_path is not None:
            default_path = self.current_project_path
        else:
            safe_name = (self.project.name or "projet").strip() or "projet"
            default_path = os.path.join(os.path.expanduser("~"), f"{safe_name}.kut")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Enregistrer le projet sous...",
            default_path,
            "Projets Kut-Studio (*.kut)",
        )
        if not path:
            return
        if not path.lower().endswith(".kut"):
            path = path + ".kut"
        try:
            save_project(self.project, path)
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Enregistrement impossible",
                f"Impossible d'enregistrer le projet :\n\n{exc}",
            )
            return
        self.current_project_path = path
        self._mark_clean()

    def open_project_file(self) -> None:
        """Ouvre un dialogue et charge un projet ``.kut`` sélectionné."""
        default_dir = os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Ouvrir un projet Kut-Studio",
            default_dir,
            "Projets Kut-Studio (*.kut)",
        )
        if not path:
            return
        self._load_project_from_path(path)

    def _load_project_from_path(self, path: str) -> None:
        """Charge ``path`` et remplace ``self.project`` uniquement en cas de succès.

        Toutes les erreurs de chargement (fichier absent, JSON invalide,
        format/version non supportés, **ou structure interne incomplète
        qui lèverait un ``TypeError`` lors de la désérialisation des
        dataclasses**) sont traitées de la même façon : un message
        d'erreur est affiché et l'état courant de l'application reste
        intact.
        """
        try:
            loaded = load_project(path)
        except (FileNotFoundError, ValueError, OSError, TypeError) as exc:
            QMessageBox.critical(
                self,
                "Impossible d'ouvrir le projet",
                f"Le fichier {path} n'a pas pu être ouvert :\n\n{exc}",
            )
            return
        self.project = loaded
        self.current_project_path = path
        self.timeline_panel.set_project(self.project)
        self.playhead_seconds = 0.0
        self.is_playing = False
        self._update_timeline_duration()
        self._sync_preview_to_timeline()
        self._refresh_project_library()
        self._reset_selection_and_inspector()
        self._mark_clean()

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
            render_plan = self.get_render_plan()
            request = self.export_panel.build_request(render_plan, path)
        except Exception as exc:
            self.export_panel.mark_export_error(f"Paramètres invalides : {exc}")
            return
        self.export_panel.mark_export_started()
        self.export_engine.start(request)

    def get_render_plan(self) -> RenderPlan:
        """Construit le :class:`RenderPlan` du projet courant.

        Le plan décrit fidèlement la timeline (positions, trims, trous,
        ordre des pistes, clips activés). C'est désormais l'entrée
        unique du moteur d'export.
        """
        return build_render_plan(self.project)

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
        new_action.triggered.connect(self.new_project)
        open_action = QAction("Ouvrir...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_project_file)
        save_action = QAction("Enregistrer", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.save_project_file)
        save_as_action = QAction("Enregistrer sous...", self)
        save_as_action.setShortcut("Ctrl+Shift+S")
        save_as_action.triggered.connect(self.save_project_as)
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
        """Synchronise les widgets de transport avec l'état réel du ``QMediaPlayer``.

        L'état de lecture est désormais piloté par ``MainWindow.is_playing``
        ; ce slot sert à répercuter les transitions natives (fin de
        média, erreur, etc.) sur les widgets de transport.
        """
        is_playing = state == QMediaPlayer.PlayingState
        self.timeline_panel.setPlayState(is_playing)
        self.preview_panel.play_button.setText("❚❚ Pause" if is_playing else "▶ Play")

    def _tick_playback(self) -> None:
        """Avance l'horloge de timeline quand la lecture est active.

        Toute la logique de lecture / pause / seek / stop est désormais
        concentrée ici : ``MainWindow`` possède le playhead, le timer
        Qt l'incrémente, puis on synchronise la timeline et l'aperçu.
        """
        if self.is_playing:
            duration = timeline_duration(self.project)
            # 40 ms = intervalle du timer ; on consomme un delta fixe
            # pour rester stable face aux variations de wall-clock.
            next_playhead = self.playhead_seconds + 0.04
            if duration > 0.0 and next_playhead >= duration:
                self.playhead_seconds = duration
                self._pause_internal()
            else:
                self.playhead_seconds = next_playhead
        self.timeline_panel.set_playhead_seconds(self.playhead_seconds)
        self._sync_preview_to_timeline()
        self.update_subtitle_overlay(self.playhead_seconds)

    def _sync_preview_to_timeline(self) -> None:
        """Évalue la timeline à ``playhead_seconds`` et synchronise l'aperçu.

        - S'il existe au moins un clip vidéo actif, on charge la source
          du dernier clip vidéo retourné par ``evaluate_timeline`` (la
          piste la plus basse dans ``project.tracks`` est considérée
          comme visuellement au-dessus).
        - Sinon, on affiche l'état vide via ``PreviewPanel.show_empty``.
        - Les clips non-vidéo (audio, sous-titres) n'influencent pas
          la fenêtre vidéo, mais leurs effets (overlay, etc.) sont
          appliqués séparément (``update_subtitle_overlay``).
        """
        try:
            active_clips = evaluate_timeline(self.project, self.playhead_seconds)
        except ValueError:
            return
        video_clips = [c for c in active_clips if c.track_type == "video"]
        if not video_clips:
            self.preview_panel.show_empty()
            return
        top_clip = video_clips[-1]
        self.preview_panel.preview_at(top_clip.source_path, top_clip.source_time)
        if self.is_playing:
            # Si une source vient d'être chargée ou remplacée, on relance
            # la lecture native pour qu'elle démarre à ``source_time``.
            if self.preview_panel.player.playbackState() != QMediaPlayer.PlayingState:
                self.preview_panel.player.play()

    def _update_timeline_duration(self) -> None:
        """Met à jour la durée affichée à partir de ``timeline_duration(project)``."""
        self.timeline_panel.set_timeline_duration(timeline_duration(self.project))

    def _pause_internal(self) -> None:
        """Met la lecture en pause sans toucher au playhead."""
        self.is_playing = False
        self.preview_panel.player.pause()
        self.timeline_panel.setPlayState(False)
        self.preview_panel.play_button.setText("▶ Play")

    def on_clip_selected(self, clip_id):
        view = self.timeline_panel.find_view_by_id(clip_id)
        if view is None:
            return
        self.active_subtitle_clip = view if view.track_id == "S1" else None
        self.properties_panel.show_clip(view)
        # Sélection d'un clip = seek vers son début sur la timeline.
        # L'aperçu est resynchronisé par ``seek_to_position``.
        self.seek_to_position(view.start)

    def on_move_clip_requested(self, clip_id: str, new_timeline_start: float) -> None:
        """Applique un déplacement demandé par la timeline."""
        try:
            move_clip(self.project, clip_id, new_timeline_start)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] move refusé : {exc}")
            return
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_trim_left_requested(self, clip_id: str, new_timeline_start: float) -> None:
        try:
            trim_clip_left(self.project, clip_id, new_timeline_start)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] trim gauche refusé : {exc}")
            return
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_trim_right_requested(self, clip_id: str, new_timeline_end: float) -> None:
        try:
            trim_clip_right(self.project, clip_id, new_timeline_end)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] trim droit refusé : {exc}")
            return
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._mark_dirty()

    def cut_at_playhead(self):
        clip_id = self.timeline_panel.selected_clip_id
        if clip_id is None:
            print("[MainWindow] Cut : aucun clip sélectionné")
            return
        self.cut_selected_clip(clip_id, self.timeline_panel.playhead_seconds)

    # ------------------------------------------------------------------
    # Import de médias (vidéo et audio)
    # ------------------------------------------------------------------

    def import_media_via_dialog(self) -> None:
        """Ouvre un dialogue d'import et importe chaque fichier sélectionné.

        Les filtres du dialogue couvrent les formats vidéo et audio
        acceptés par ``probe_media``. Chaque fichier est analysé pour
        déterminer son type (vidéo, audio) avant d'être ajouté au projet.
        """
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Importer des médias",
            os.path.expanduser("~/Movies"),
            "Médias (*.mp4 *.mov *.avi *.mkv *.webm *.mp3 *.wav *.m4a *.aac *.flac *.ogg)",
        )
        if not paths:
            return
        for path in paths:
            self.import_media_to_project(path)

    def import_media_to_project(self, path: str) -> bool:
        """Importe ``path`` comme ``MediaAsset`` réel (vidéo ou audio).

        L'opération est idempotente pour un même chemin normalisé : un
        doublon est ignoré silencieusement. En cas d'échec de la sonde,
        ni le projet ni la bibliothèque ne sont modifiés ; une boîte de
        dialogue claire est affichée à l'utilisateur.
        """
        normalized = os.path.normpath(os.path.abspath(path))
        for asset in self.project.media_assets:
            if os.path.normpath(os.path.abspath(asset.path)) == normalized:
                # Doublon silencieux : on conserve le projet intact et on
                # met le focus sur l'asset existant dans la bibliothèque.
                self.project_panel.select_asset(asset.id)
                if asset.media_type == "video":
                    self.preview_panel.load_video(asset.path)
                return False

        try:
            asset = probe_media(path)
        except MediaProbeError as exc:
            QMessageBox.critical(
                self,
                "Import impossible",
                f"Impossible d'importer le média :\n\n{path}\n\n{exc}",
            )
            return False

        self.project.media_assets.append(asset)
        self._refresh_project_library()
        self.project_panel.select_asset(asset.id)
        if asset.media_type == "video":
            self.preview_panel.load_video(asset.path)
        self._mark_dirty()
        return True

    # Compatibilité ascendante : les anciens tests/appels peuvent continuer
    # d'utiliser ``import_video_to_project``.
    def import_video_to_project(self, path: str) -> bool:
        """Délègue à :meth:`import_media_to_project` (alias historique)."""
        return self.import_media_to_project(path)

    def preview_media_asset(self, asset_id: str) -> None:
        """Prévisualise le ``MediaAsset`` identifié par ``asset_id``.

        Les assets audio ne déclenchent pas de prévisualisation vidéo.
        """
        asset = next(
            (a for a in self.project.media_assets if a.id == asset_id),
            None,
        )
        if asset is None:
            return
        if asset.media_type == "video":
            self.preview_panel.load_video(asset.path)

    def add_asset_to_timeline(self, asset_id: str) -> None:
        """Ajoute le média sélectionné à la piste adaptée à son type.

        Routing :

        - asset ``video`` → piste ``V1`` (créée si absente) ;
        - asset ``audio`` → piste ``A1`` (créée si absente) ;
        - autres types → erreur claire.

        Le clip est créé à la position du playhead ; si le playhead est
        hors limites (typiquement après un reset à zéro sur un projet
        vide), il est ramené à ``0.0``.
        """
        asset = next(
            (a for a in self.project.media_assets if a.id == asset_id),
            None,
        )
        if asset is None:
            QMessageBox.critical(
                self,
                "Ajout impossible",
                f"Média '{asset_id}' introuvable dans le projet.",
            )
            return

        if asset.media_type == "video":
            target_track_id = "V1"
        elif asset.media_type == "audio":
            target_track_id = "A1"
        else:
            QMessageBox.critical(
                self,
                "Ajout impossible",
                f"Le type de média '{asset.media_type}' ne peut pas être "
                "ajouté à la timeline depuis le panneau de bibliothèque.",
            )
            return

        if not any(track.id == target_track_id for track in self.project.tracks):
            QMessageBox.critical(
                self,
                "Ajout impossible",
                f"La piste '{target_track_id}' est absente du projet courant.",
            )
            return

        timeline_start = self.timeline_panel.playhead_seconds
        try:
            new_clip = add_clip_to_track(
                self.project,
                asset_id,
                target_track_id,
                timeline_start,
            )
        except (KeyError, ValueError) as exc:
            QMessageBox.critical(
                self,
                "Ajout impossible",
                f"Impossible d'ajouter le média à la timeline :\n\n{exc}",
            )
            return

        # Rafraîchit la projection (qui inclut le nouveau clip).
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self.timeline_panel.select_clip(new_clip.id)
        self._mark_dirty()

    # Compatibilité ascendante : anciens appels.
    def add_asset_to_v1(self, asset_id: str) -> None:
        """Délègue à :meth:`add_asset_to_timeline` (alias historique)."""
        self.add_asset_to_timeline(asset_id)

    def _refresh_project_library(self) -> None:
        """Synchronise ``ProjectPanel`` avec ``self.project.media_assets``."""
        self.project_panel.set_assets(list(self.project.media_assets))

    def cut_selected_clip(self, clip_id, playhead_pos):
        try:
            cut_clip(self.project, clip_id, playhead_pos)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] cut refusé : {exc}")
            return
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._mark_dirty()
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
        self._update_timeline_duration()
        self._mark_dirty()

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
        self._mark_dirty()

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
        """Seek sur la timeline (et non plus sur le média source).

        Met à jour le playhead, synchronise la timeline puis l'aperçu,
        et rafraîchit l'overlay de sous-titres.
        """
        duration = timeline_duration(self.project)
        if duration > 0.0:
            seconds = max(0.0, min(float(seconds), duration))
        else:
            seconds = max(0.0, float(seconds))
        self.playhead_seconds = seconds
        self.timeline_panel.set_playhead_seconds(self.playhead_seconds)
        self._sync_preview_to_timeline()
        self.update_subtitle_overlay(self.playhead_seconds)

    def load_video(self, asset_id: str) -> None:
        """Compatibilité : délègue à ``preview_media_asset``.

        Conservé pour ne pas casser d'éventuels appels externes ; les
        clics de la bibliothèque passent désormais directement par
        ``preview_media_asset``.
        """
        self.preview_media_asset(asset_id)

    def stop_playback(self):
        """Stop : playhead à 0, aperçu synchronisé, lecture arrêtée."""
        self.is_playing = False
        self.preview_panel.player.stop()
        self.playhead_seconds = 0.0
        self.timeline_panel.set_playhead_seconds(0.0)
        self._sync_preview_to_timeline()
        self.timeline_panel.setPlayState(False)
        self.preview_panel.play_button.setText("▶ Play")

    def toggle_play(self):
        """Bascule lecture / pause en pilotant l'horloge de la timeline."""
        if self.is_playing:
            self._pause_internal()
            return
        # Si la timeline n'a aucun clip activé, rien à lire.
        if timeline_duration(self.project) <= 0.0:
            return
        self.is_playing = True
        self._sync_preview_to_timeline()
        self.preview_panel.player.play()
        self.timeline_panel.setPlayState(True)
        self.preview_panel.play_button.setText("❚❚ Pause")

    def seek_relative(self, delta_seconds):
        """Seek relatif sur la timeline (avance / recule de ``delta_seconds``)."""
        self.seek_to_position(self.playhead_seconds + delta_seconds)

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
            local_path = url.toLocalFile()
            if local_path:
                self.import_video_to_project(local_path)
