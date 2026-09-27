import os

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QAction, QCursor
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
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

from core.effects import apply_color_effect, play_crossfade_preview, set_volume
from core.edit_history import ProjectHistory
from core.export_engine import ExportEngine
from core.media_probe import MediaProbeError, probe_media, probe_video
from core.project_factory import create_default_project
from core.project_io import load_project, save_project
from core.project_model import MediaAsset, Project
from core.subtitle_io import load_srt, parse_srt, save_srt
from core.timeline_evaluator import evaluate_timeline
from core.timeline_operations import (
    add_clip_to_track,
    add_subtitle_clip,
    cut_clip,
    delete_clip,
    duplicate_clip,
    find_clip,
    move_clip,
    remove_transform_keyframe,
    reset_clip_transform,
    ripple_delete_clip,
    set_clip_enabled,
    set_clip_transform,
    set_transform_keyframe,
    snap_timeline_position,
    subtitle_cues_from_project,
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
from core.user_settings import (
    DEFAULT_LANGUAGE,
    DEFAULT_THEME,
    UserSettings,
    load_user_settings,
    save_user_settings,
)
from core.visual_effects import evaluate_transform
from ui import i18n
from ui.preferences_dialog import PreferencesDialog
from ui.preview_panel import PreviewPanel
from ui.project_panel import ProjectPanel
from ui.properties_panel import PropertiesPanel
from ui.timeline_panel import TimelinePanel
from ui.export_panel import ExportPanel
from core.track_operations import (
    add_track as track_operations_add_track,
    move_track as track_operations_move_track,
    remove_track as track_operations_remove_track,
    rename_track as track_operations_rename_track,
    set_track_locked,
    set_track_muted,
    set_track_visible,
)
from ui.theme import COLORS, ThemeManager, global_stylesheet, label_style


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Kut-Studio")
        self.setMinimumSize(1080, 680)
        self.resize(1440, 900)
        self.subtitle_file: str | None = None
        self.active_subtitle_clip = None
        self.transition_seconds = None
        # ``Project`` est désormais l'unique source de vérité de la timeline.
        self.project: Project = create_default_project()
        # Historique undo/redo non destructif.
        self.history = ProjectHistory()
        self.history.reset(self.project)
        # État du document courant pour la persistance ``.kut``.
        self.current_project_path: str | None = None
        self.project_dirty: bool = False

        # Gestionnaire de thème (sombre / clair / système).
        loaded_settings: UserSettings = load_user_settings()
        self.theme_manager = ThemeManager(requested_mode=loaded_settings.theme_mode)
        self.theme_manager.apply_to(QApplication.instance())
        i18n.set_language(loaded_settings.language)
        # S'abonne aux changements de langue pour recharger les libellés.
        i18n.subscribe(lambda code: self._on_language_changed(code))

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
        self.project_panel.add_subtitle_requested.connect(self.add_subtitle_at_playhead)
        self.project_panel.import_subtitles_requested.connect(self.import_subtitles_via_dialog)
        self.project_panel.export_subtitles_requested.connect(self.export_subtitles_via_dialog)
        self.project_panel.subtitle_selected.connect(self.on_subtitle_clip_selected)
        self.properties_panel = PropertiesPanel(self.update_color_effect, self.update_volume)
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
        self.timeline_panel.asset_dropped.connect(self.on_asset_dropped)
        # Tâche 14 : opérations de pistes (ajout, suppression,
        # verrouillage, visibilité, mute, déplacement, renommage).
        self.timeline_panel.add_track_requested.connect(self.on_add_track_requested)
        self.timeline_panel.remove_track_requested.connect(self.on_remove_track_requested)
        self.timeline_panel.rename_track_requested.connect(self.on_rename_track_requested)
        self.timeline_panel.toggle_track_lock_requested.connect(self.on_toggle_track_lock)
        self.timeline_panel.toggle_track_visible_requested.connect(self.on_toggle_track_visible)
        self.timeline_panel.toggle_track_muted_requested.connect(self.on_toggle_track_muted)
        self.timeline_panel.move_track_up_requested.connect(self.on_move_track_up)
        self.timeline_panel.move_track_down_requested.connect(self.on_move_track_down)
        self.properties_panel.cut_requested.connect(self.cut_selected_clip)
        self.properties_panel.delete_requested.connect(self.delete_selected_clip)
        self.properties_panel.duplicate_requested.connect(self.duplicate_clip_from_panel)
        self.properties_panel.ripple_delete_requested.connect(self.ripple_delete_clip_from_panel)
        self.properties_panel.enabled_changed.connect(self.set_clip_enabled_from_panel)
        self.properties_panel.subtitle_editor.textChanged.connect(self.update_subtitle_from_editor)
        # Tâche 13 : opérations visuelles.
        self.properties_panel.transform_changed.connect(self.on_transform_property_changed)
        self.properties_panel.keyframe_added.connect(self.on_transform_keyframe_added)
        self.properties_panel.keyframe_removed.connect(self.on_transform_keyframe_removed)
        self.properties_panel.transform_reset.connect(self.on_transform_reset)

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
        self.theme_manager.apply_to(QApplication.instance())
        # Synchronise l'état initial des actions undo/redo. Cette
        # opération doit suivre ``_build_top_bar`` qui crée
        # ``project_label``.
        self._refresh_undo_redo_state()

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
        self._flush_subtitle_history_record()
        self._finalize_transform_session()
        self.project_dirty = False
        self.history.mark_saved()
        self._refresh_undo_redo_state()
        self._update_top_bar()

    def _record_history(self, label: str) -> None:
        """Enregistre l'état courant du projet dans l'historique."""
        # Une autre action clôt la saisie de sous-titre en cours et son
        # état final est inclus dans ce snapshot d'action.
        if getattr(self, "_subtitle_edit_pending", None) is not None:
            timer = getattr(self, "_subtitle_edit_timer", None)
            if timer is not None:
                timer.stop()
            self._subtitle_edit_pending = None
            self._subtitle_edit_history_before = None
        self.history.record(self.project, label)
        self._refresh_undo_redo_state()

    def _refresh_undo_redo_state(self) -> None:
        """Synchronise les actions et indicateurs undo/redo."""
        if hasattr(self, "undo_action"):
            self.undo_action.setEnabled(self.history.can_undo)
            label = self.history.undo_label
            self.undo_action.setText(
                f"Annuler : {label}" if label else "Annuler"
            )
        if hasattr(self, "redo_action"):
            self.redo_action.setEnabled(self.history.can_redo)
            label = self.history.redo_label
            self.redo_action.setText(
                f"Rétablir : {label}" if label else "Rétablir"
            )
        # Synchronise le flag ``project_dirty`` avec l'historique.
        self.project_dirty = self.history.is_dirty
        self._update_top_bar()

    # ------------------------------------------------------------------
    # Undo / Redo / Duplicate / Ripple / Enable
    # ------------------------------------------------------------------

    def _apply_history_snapshot(self, snapshot_project, label: str) -> None:
        """Ré-installe ``snapshot_project`` partout dans l'interface."""
        self.project = snapshot_project
        self.timeline_panel.set_project(self.project)
        # Conserver un playhead valide.
        if self.playhead_seconds > self.timeline_panel.duration_seconds:
            self.playhead_seconds = self.timeline_panel.duration_seconds
        if self.playhead_seconds < 0.0:
            self.playhead_seconds = 0.0
        # Si la sélection courante n'existe plus, on la réinitialise.
        if (
            self.timeline_panel.selected_clip_id is not None
            and self.timeline_panel.find_view_by_id(
                self.timeline_panel.selected_clip_id
            ) is None
        ):
            self.timeline_panel.selected_clip_id = None
            self.active_subtitle_clip = None
            self.properties_panel.set_clip(None, "")
        self._update_timeline_duration()
        self._sync_preview_to_timeline()
        self._refresh_project_library()
        self._refresh_undo_redo_state()

    def undo_last(self) -> None:
        """Annule la dernière opération enregistrée."""
        self._flush_subtitle_history_record()
        self._finalize_transform_session()
        snapshot = self.history.undo()
        if snapshot is None:
            return
        self._apply_history_snapshot(snapshot, self.history.undo_label or "")

    def redo_last(self) -> None:
        """Rétablit la dernière opération annulée."""
        self._flush_subtitle_history_record()
        self._finalize_transform_session()
        snapshot = self.history.redo()
        if snapshot is None:
            return
        self._apply_history_snapshot(snapshot, self.history.redo_label or "")

    def duplicate_selected_clip(self) -> None:
        """Duplique le clip sélectionné juste après sa fin."""
        clip_id = self.timeline_panel.selected_clip_id
        if clip_id is None:
            return
        try:
            new_clip = duplicate_clip(self.project, clip_id)
        except (KeyError, ValueError) as exc:
            QMessageBox.critical(
                self,
                "Duplication impossible",
                f"Impossible de dupliquer le clip :\n\n{exc}",
            )
            return
        self._record_history("Dupliquer le clip")
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self.timeline_panel.select_clip(new_clip.id)
        self._mark_dirty()

    def ripple_delete_selected_clip(self) -> None:
        """Supprime le clip sélectionné et ramène à gauche les clips suivants."""
        clip_id = self.timeline_panel.selected_clip_id
        if clip_id is None:
            return
        try:
            moved_ids = ripple_delete_clip(self.project, clip_id)
        except KeyError as exc:
            QMessageBox.critical(
                self,
                "Suppression impossible",
                f"Impossible de supprimer le clip :\n\n{exc}",
            )
            return
        self._record_history("Supprimer avec ripple")
        self.timeline_panel.selected_clip_id = None
        self.active_subtitle_clip = None
        self.properties_panel.set_clip(None, "")
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        # Sélectionner le clip précédent sur V1 s'il existe.
        previous_clip = self._find_previous_v1_clip()
        if previous_clip is not None:
            self.timeline_panel.select_clip(previous_clip.id)
            self.on_clip_selected(previous_clip.id)
        self._mark_dirty()

    def delete_selected_clip_with_check(self) -> None:
        """Variante appelée par raccourci : no-op si aucun clip sélectionné."""
        if self.timeline_panel.selected_clip_id is None:
            return
        self.delete_selected_clip(self.timeline_panel.selected_clip_id)

    def _find_previous_v1_clip(self):
        """Retourne le dernier clip V1 (par timeline_start) ou ``None``."""
        views = sorted(self.timeline_panel.clip_views, key=lambda v: v.start)
        for view in reversed(views):
            if view.track_id == "V1":
                return view
        return None

    def toggle_selected_clip_enabled(self) -> None:
        """Bascule l'état ``enabled`` du clip sélectionné."""
        clip_id = self.timeline_panel.selected_clip_id
        if clip_id is None:
            return
        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return
        try:
            set_clip_enabled(self.project, clip_id, not clip.enabled)
        except KeyError:
            return
        self._record_history(
            "Activer le clip" if not clip.enabled else "Désactiver le clip"
        )
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        # Conserver la sélection.
        self.timeline_panel.select_clip(clip_id)
        self._mark_dirty()

    # ------------------------------------------------------------------
    # Wrappers pour les actions du panneau Propriétés
    # ------------------------------------------------------------------

    def duplicate_clip_from_panel(self, clip_id: str) -> None:
        if self.timeline_panel.selected_clip_id != clip_id:
            self.timeline_panel.select_clip(clip_id)
        self.duplicate_selected_clip()

    def ripple_delete_clip_from_panel(self, clip_id: str) -> None:
        if self.timeline_panel.selected_clip_id != clip_id:
            self.timeline_panel.select_clip(clip_id)
        self.ripple_delete_selected_clip()

    def set_clip_enabled_from_panel(self, clip_id: str, enabled: bool) -> None:
        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return
        if clip.enabled == enabled:
            return
        try:
            set_clip_enabled(self.project, clip_id, enabled)
        except KeyError:
            return
        self._record_history(
            "Activer le clip" if enabled else "Désactiver le clip"
        )
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self.timeline_panel.select_clip(clip_id)
        self._mark_dirty()

    # ------------------------------------------------------------------
    # Drag & drop
    # ------------------------------------------------------------------

    def on_asset_dropped(self, asset_id: str, track_id: str, timeline_start: float) -> None:
        """Ajoute le média glissé sur la piste ciblée à la position donnée.

        La compatibilité asset / piste est vérifiée ; un dépôt invalide
        n'est pas appliqué au projet.
        """
        asset = next(
            (a for a in self.project.media_assets if a.id == asset_id),
            None,
        )
        if asset is None:
            return
        try:
            clip = add_clip_to_track(self.project, asset_id, track_id, timeline_start)
        except (KeyError, ValueError):
            return
        self._record_history(
            f"Déposer « {asset.name} » sur {track_id}"
        )
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._refresh_project_library()
        self.timeline_panel.select_clip(clip.id)
        self._mark_dirty()

    # ------------------------------------------------------------------
    # Debounce pour la modification de texte des sous-titres
    # ------------------------------------------------------------------

    def _schedule_subtitle_history_record(self, clip_id: str, new_text: str) -> None:
        """Programme l'enregistrement d'un snapshot après 500 ms d'inactivité.

        ``history_before_text`` capture l'état du projet juste avant la
        saisie courante : si plusieurs frappes surviennent avant le
        déclenchement du timer, on ne crée qu'un seul snapshot couvrant
        toute la saisie.
        """
        if not hasattr(self, "_subtitle_edit_timer"):
            from PySide6.QtCore import QTimer

            self._subtitle_edit_timer = QTimer(self)
            self._subtitle_edit_timer.setSingleShot(True)
            self._subtitle_edit_timer.setInterval(500)
            self._subtitle_edit_timer.timeout.connect(
                self._flush_subtitle_history_record
            )
            self._subtitle_edit_pending = None
            self._subtitle_edit_history_before = None
        if self._subtitle_edit_pending not in (None, clip_id):
            self._flush_subtitle_history_record()
        if self._subtitle_edit_pending != clip_id:
            # Le snapshot final sera capturé au flush, après la saisie.
            self._subtitle_edit_history_before = None
            self._subtitle_edit_pending = clip_id
        self._subtitle_edit_timer.start()

    def _flush_subtitle_history_record(self) -> None:
        """Enregistre le texte final après le debounce."""
        if getattr(self, "_subtitle_edit_pending", None) is None:
            return
        timer = getattr(self, "_subtitle_edit_timer", None)
        if timer is not None:
            timer.stop()
        self._subtitle_edit_history_before = None
        self._subtitle_edit_pending = None
        self.history.record(self.project, "Modifier un sous-titre")
        self._refresh_undo_redo_state()

    def _reset_selection_and_inspector(self) -> None:
        """Réinitialise la sélection de clip et l'inspecteur après un changement de projet."""
        self.active_subtitle_clip = None
        self.timeline_panel.selected_clip_id = None
        self.properties_panel.set_clip(None, "")

    def new_project(self) -> None:
        """Crée un nouveau projet vierge via ``create_default_project()``."""
        self._flush_subtitle_history_record()
        self._finalize_transform_session()
        self.project = create_default_project()
        self.current_project_path = None
        self.history.reset(self.project)
        self._refresh_undo_redo_state()
        self.timeline_panel.set_project(self.project)
        self.playhead_seconds = 0.0
        self.is_playing = False
        self._update_timeline_duration()
        self._sync_preview_to_timeline()
        self._reset_selection_and_inspector()
        self._mark_clean()

    def save_project_file(self) -> None:
        """Enregistre le projet courant. Délègue à ``save_project_as`` si aucun chemin."""
        self._flush_subtitle_history_record()
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
        self._flush_subtitle_history_record()
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
        self._flush_subtitle_history_record()
        self._finalize_transform_session()
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
        self.history.reset(self.project)
        self._refresh_undo_redo_state()
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
        import_subs_action = QAction("Importer des sous-titres SRT…", self)
        import_subs_action.triggered.connect(self.import_subtitles_via_dialog)
        file_menu.addAction(import_subs_action)
        export_subs_action = QAction("Exporter les sous-titres SRT…", self)
        export_subs_action.triggered.connect(self.export_subtitles_via_dialog)
        file_menu.addAction(export_subs_action)
        file_menu.addSeparator()
        exit_action = QAction("Quitter", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Édition
        edit_menu = QMenu("Édition", self)
        self.undo_action = QAction("Annuler", self)
        self.undo_action.setShortcut("Ctrl+Z")
        self.undo_action.setShortcutContext(Qt.ApplicationShortcut)
        self.undo_action.triggered.connect(self.undo_last)
        edit_menu.addAction(self.undo_action)

        self.redo_action = QAction("Rétablir", self)
        self.redo_action.setShortcuts(["Ctrl+Shift+Z", "Ctrl+Y"])
        self.redo_action.setShortcutContext(Qt.ApplicationShortcut)
        self.redo_action.triggered.connect(self.redo_last)
        edit_menu.addAction(self.redo_action)

        edit_menu.addSeparator()

        duplicate_action = QAction("Dupliquer le clip", self)
        duplicate_action.setShortcut("Ctrl+D")
        duplicate_action.setShortcutContext(Qt.ApplicationShortcut)
        duplicate_action.triggered.connect(self.duplicate_selected_clip)
        edit_menu.addAction(duplicate_action)

        self.delete_action = QAction("Supprimer le clip", self)
        self.delete_action.setShortcuts(["Delete", "Backspace"])
        self.delete_action.setShortcutContext(Qt.ApplicationShortcut)
        self.delete_action.triggered.connect(self.delete_selected_clip_with_check)
        edit_menu.addAction(self.delete_action)

        ripple_action = QAction("Supprimer avec ripple", self)
        ripple_action.setShortcut("Ctrl+Backspace")
        ripple_action.setShortcutContext(Qt.ApplicationShortcut)
        ripple_action.triggered.connect(self.ripple_delete_selected_clip)
        edit_menu.addAction(ripple_action)

        enable_action = QAction("Activer / Désactiver le clip", self)
        enable_action.setShortcut("Ctrl+E")
        enable_action.setShortcutContext(Qt.ApplicationShortcut)
        enable_action.triggered.connect(self.toggle_selected_clip_enabled)
        edit_menu.addAction(enable_action)

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

        # Menu Séquence : opérations de piste.
        track_add_video_action = QAction(i18n.translate("tracks.add_video_long"), self)
        track_add_video_action.triggered.connect(
            lambda: self.on_add_track_requested("video")
        )
        sequence_menu.addAction(track_add_video_action)
        track_add_audio_action = QAction(i18n.translate("tracks.add_audio_long"), self)
        track_add_audio_action.triggered.connect(
            lambda: self.on_add_track_requested("audio")
        )
        sequence_menu.addAction(track_add_audio_action)
        track_add_subtitle_action = QAction(
            i18n.translate("tracks.add_subtitle_long"), self
        )
        track_add_subtitle_action.triggered.connect(
            lambda: self.on_add_track_requested("subtitle")
        )
        sequence_menu.addAction(track_add_subtitle_action)
        sequence_menu.addSeparator()
        track_remove_action = QAction(i18n.translate("tracks.remove"), self)
        track_remove_action.triggered.connect(self.remove_selected_track)
        sequence_menu.addAction(track_remove_action)
        track_move_up_action = QAction(i18n.translate("tracks.move_up"), self)
        track_move_up_action.triggered.connect(
            lambda: self._move_selected_track(direction=-1)
        )
        sequence_menu.addAction(track_move_up_action)
        track_move_down_action = QAction(i18n.translate("tracks.move_down"), self)
        track_move_down_action.triggered.connect(
            lambda: self._move_selected_track(direction=1)
        )
        sequence_menu.addAction(track_move_down_action)
        track_rename_action = QAction(i18n.translate("tracks.rename"), self)
        track_rename_action.triggered.connect(self.rename_selected_track)
        sequence_menu.addAction(track_rename_action)
        track_lock_action = QAction(i18n.translate("tracks.toggle_lock"), self)
        track_lock_action.triggered.connect(self.toggle_selected_track_lock)
        sequence_menu.addAction(track_lock_action)
        track_visible_action = QAction(i18n.translate("tracks.toggle_visible"), self)
        track_visible_action.triggered.connect(self.toggle_selected_track_visible)
        sequence_menu.addAction(track_visible_action)
        track_mute_action = QAction(i18n.translate("tracks.toggle_mute"), self)
        track_mute_action.triggered.connect(self.toggle_selected_track_muted)
        sequence_menu.addAction(track_mute_action)

        for menu in (file_menu, edit_menu, sequence_menu, window_menu):
            menu_bar.addMenu(menu)

        # Menu Édition : entrée Préférences (à la fin de la barre).
        preferences_action = QAction(i18n.translate("action.preferences"), self)
        preferences_action.setShortcut("Ctrl+,")
        preferences_action.setShortcutContext(Qt.ApplicationShortcut)
        preferences_action.triggered.connect(self.show_preferences)
        # On insère l'entrée dans la barre ``Fenêtre`` pour rester
        # accessible sans modifier l'ordre établi.
        window_menu.addAction(preferences_action)

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
        self._refresh_motion_inspector()
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
        # Tâche 13 : applique le transform animé du clip supérieur si
        # la timeline contient au moins un clip vidéo. On évalue le
        # ``ClipTransform`` à ``playhead_seconds`` ; on garde l'opacité
        # au sommet pour être conforme à la convention de la tâches 6
        # (clip actif supérieur = superposition).
        clip_obj = find_clip(self.project, top_clip.clip_id)
        if clip_obj is not None:
            evaluated = evaluate_transform(
                clip_obj.transform,
                clip_obj.transform_keyframes,
                clip_local_time=top_clip.source_time,
                clip_duration=clip_obj.duration,
            )
            self.preview_panel.apply_transform(
                position_x=evaluated.position_x,
                position_y=evaluated.position_y,
                scale=evaluated.scale,
                rotation=evaluated.rotation,
                opacity=evaluated.opacity,
            )
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
        self._flush_subtitle_history_record()
        self._finalize_transform_session()
        view = self.timeline_panel.find_view_by_id(clip_id)
        if view is None:
            return
        self.active_subtitle_clip = (
            view if getattr(view, "track_type", None) == "subtitle" else None
        )
        self.properties_panel.show_clip(view)
        # Synchronise la section MOUVEMENT avec le clip réel.
        clip = find_clip(self.project, clip_id)
        if clip is not None:
            self.properties_panel.update_transform_from_clip(
                clip.transform,
                clip.transform_keyframes,
                playhead_seconds=self.timeline_panel.playhead_seconds,
            )
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
        self._record_history("Déplacer le clip")
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_trim_left_requested(self, clip_id: str, new_timeline_start: float) -> None:
        try:
            trim_clip_left(self.project, clip_id, new_timeline_start)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] trim gauche refusé : {exc}")
            return
        self._record_history("Trim gauche")
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_trim_right_requested(self, clip_id: str, new_timeline_end: float) -> None:
        try:
            trim_clip_right(self.project, clip_id, new_timeline_end)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] trim droit refusé : {exc}")
            return
        self._record_history("Trim droit")
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
        self._record_history(f"Importer le média « {asset.name} »")
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
        self._record_history(f"Ajouter le clip « {new_clip.label or new_clip.id} »")
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self.timeline_panel.select_clip(new_clip.id)
        self._mark_dirty()

    # Compatibilité ascendante : anciens appels.
    def add_asset_to_v1(self, asset_id: str) -> None:
        """Délègue à :meth:`add_asset_to_timeline` (alias historique)."""
        self.add_asset_to_timeline(asset_id)

    def _refresh_project_library(self) -> None:
        """Synchronise ``ProjectPanel`` avec ``self.project.media_assets``.

        Met également à jour la bibliothèque de sous-titres de l'onglet
        Texte avec les clips activés des pistes ``subtitle``.
        """
        self.project_panel.set_assets(list(self.project.media_assets))
        subtitle_clips = [
            clip
            for track in self.project.tracks
            if track.type == "subtitle"
            for clip in track.clips
            if clip.enabled and (clip.text or "").strip()
        ]
        self.project_panel.set_subtitle_clips(subtitle_clips)

    def cut_selected_clip(self, clip_id, playhead_pos):
        try:
            cut_clip(self.project, clip_id, playhead_pos)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] cut refusé : {exc}")
            return
        self._record_history("Couper le clip")
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
        self._record_history("Supprimer le clip")
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

        if clip.text == new_text:
            return

        # Démarrer le groupe avant de modifier le modèle. Le snapshot
        # final contiendra ainsi le texte complet, pas la première frappe.
        self._schedule_subtitle_history_record(clip.id, new_text)

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


    def update_subtitle_overlay(self, seconds):
        """Affiche le sous-titre actif (borne demi-ouverte ``start <= t < end``).

        Plusieurs sous-titres superposés sont départagés par leur
        ordre dans la timeline : le dernier gagne (comportement
        déterministe).
        """
        try:
            active_clips = evaluate_timeline(self.project, seconds)
        except ValueError:
            active_clips = []
        subtitle_clips = [c for c in active_clips if c.track_type == "subtitle"]
        text = subtitle_clips[-1].text.strip() if subtitle_clips else ""
        if text:
            self.preview_panel.preview_subtitle_overlay.setText(text)
            self.preview_panel.preview_subtitle_overlay.show()
        else:
            self.preview_panel.preview_subtitle_overlay.hide()

    def export_subtitles_to_path(self, file_path: str) -> None:
        """Exporte tous les sous-titres actifs vers ``file_path`` (.srt)."""
        cues = subtitle_cues_from_project(self.project)
        save_srt(cues, file_path)
        self.subtitle_file = file_path

    def import_subtitles_from_path(self, file_path: str) -> list[Clip]:
        """Importe les sous-titres d'un .srt et crée les clips dans S1.

        Les cues non vides sont ajoutés après les sous-titres déjà
        présents ; la liste des clips créés est retournée.
        """
        cues = [cue for cue in load_srt(file_path) if cue.text.strip()]
        if not cues:
            return []
        created_clips: list[Clip] = []
        for cue in cues:
            clip = add_subtitle_clip(
                self.project,
                text=cue.text,
                timeline_start=cue.start,
                duration=cue.end - cue.start,
            )
            created_clips.append(clip)
        self._record_history(f"Importer le SRT ({len(cues)} sous-titres)")
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._refresh_project_library()
        self._mark_dirty()
        return created_clips

    def add_subtitle_at_playhead(self, text: str, duration: float) -> None:
        """Ajoute un sous-titre au playhead courant et le sélectionne."""
        try:
            clip = add_subtitle_clip(
                self.project,
                text=text,
                timeline_start=self.playhead_seconds,
                duration=duration,
            )
        except (KeyError, ValueError) as exc:
            QMessageBox.critical(
                self,
                "Sous-titre impossible",
                f"Impossible d'ajouter le sous-titre :\n\n{exc}",
            )
            return
        self._record_history("Ajouter un sous-titre")
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._refresh_project_library()
        self.timeline_panel.select_clip(clip.id)
        self._mark_dirty()

    def import_subtitles_via_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importer des sous-titres",
            os.path.expanduser("~"),
            "Sous-titres (*.srt)",
        )
        if not path:
            return
        try:
            self.import_subtitles_from_path(path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self,
                "Import SRT impossible",
                f"Impossible d'importer les sous-titres :\n\n{exc}",
            )

    def export_subtitles_via_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Enregistrer les sous-titres",
            os.path.expanduser("~/subtitles.srt"),
            "Sous-titres (*.srt)",
        )
        if not path:
            return
        try:
            self.export_subtitles_to_path(path)
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Export SRT impossible",
                f"Impossible d'enregistrer les sous-titres :\n\n{exc}",
            )

    def on_subtitle_clip_selected(self, clip_id: str) -> None:
        """Sélectionne un sous-titre depuis la bibliothèque et synchronise le playhead."""
        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return
        # Le playhead se positionne au début du sous-titre sélectionné.
        self.seek_to_position(clip.timeline_start)
        # On rafraîchit la sélection dans le panneau Propriétés.
        self.timeline_panel.select_clip(clip_id)
        self.on_clip_selected(clip_id)

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

    def on_transform_property_changed(
        self, clip_id: str, property_name: str, value: float
    ):
        """Applique une modification de transform depuis l'inspecteur.

        Les modifications fréquentes (slider de l'inspecteur) sont
        coalescées : on ne crée une entrée d'historique qu'après la
        fin d'une rafale (debounce ~400 ms), pour ne pas polluer
        l'historique avec des dizaines d'entrées par seconde.
        """
        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return
        if clip is None:
            return
        from core.visual_effects import ANIMATABLE_PROPERTIES

        if property_name not in ANIMATABLE_PROPERTIES:
            return
        try:
            new_transform = clip.transform.__class__(
                **{
                    **{
                        field: getattr(clip.transform, field)
                        for field in (
                            "position_x",
                            "position_y",
                            "scale",
                            "rotation",
                            "opacity",
                        )
                    },
                    property_name: float(value),
                }
            )
        except ValueError as exc:
            print(f"[MainWindow] valeur transform refusée : {exc}")
            return
        try:
            set_clip_transform(self.project, clip_id, new_transform)
        except ValueError as exc:
            print(f"[MainWindow] set_clip_transform refusé : {exc}")
            return

        # Le snapshot courant de l'historique est l'état avant la rafale.
        # Il suffit d'enregistrer l'état modifié à la fin du debounce.
        self._schedule_transform_history("Modifier le mouvement")

        # Rafraîchit l'aperçu immédiatement pour le retour visuel.
        self.properties_panel.update_transform_from_clip(
            clip.transform,
            clip.transform_keyframes,
            playhead_seconds=self.playhead_seconds,
        )
        self._sync_preview_to_timeline()
        self._mark_dirty()

    def _ensure_transform_session_capture(self) -> None:
        """Marque le début d'une rafale d'édition à regrouper dans l'historique."""
        if getattr(self, "_transform_session_active", False):
            return
        self._transform_session_active = True

    def _schedule_transform_history(self, label: str) -> None:
        """Regroupe une rafale d'éditions dans une seule entrée d'historique."""
        first = not getattr(self, "_transform_session_active", False)
        self._ensure_transform_session_capture()
        if first:
            self._transform_session_label = label
        if not hasattr(self, "_transform_session_timer"):
            from PySide6.QtCore import QTimer

            self._transform_session_timer = QTimer(self)
            self._transform_session_timer.setSingleShot(True)
            self._transform_session_timer.timeout.connect(
                self._finalize_transform_session
            )
        self._transform_session_timer.start(400)

    def _finalize_transform_session(self) -> None:
        """Enregistre l'état final d'une rafale d'édition de transform."""
        if not getattr(self, "_transform_session_active", False):
            return
        timer = getattr(self, "_transform_session_timer", None)
        if timer is not None:
            timer.stop()
        self._transform_session_active = False
        label = getattr(self, "_transform_session_label", "Modifier le mouvement")
        self.history.record(self.project, label)
        self._refresh_undo_redo_state()
        self.timeline_panel.set_project(self.project)

    def _refresh_motion_inspector(self) -> None:
        """Aligne l'inspecteur Mouvement sur le clip sélectionné et la tête de lecture."""
        panel = self.properties_panel
        selected = panel.selected_clip
        if selected is None or getattr(selected, "track_type", None) != "video":
            return
        try:
            clip = find_clip(self.project, selected.id)
        except KeyError:
            return
        panel.refresh_keyframe_diamonds(
            clip.transform_keyframes,
            self.playhead_seconds,
            transform=clip.transform,
        )

    def on_transform_keyframe_added(
        self,
        clip_id: str,
        property_name: str,
        clip_local_time: float,
        value: float,
    ):
        try:
            set_transform_keyframe(
                self.project, clip_id, property_name, clip_local_time, value
            )
        except ValueError as exc:
            print(f"[MainWindow] keyframe refusée : {exc}")
            self._refresh_motion_inspector()
            return
        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return
        # Même debounce que le slider de la base : un glisser qui réécrit
        # l'image-clé sous la tête ne doit pas empiler une entrée par cran.
        self._schedule_transform_history("Ajouter une image-clé")
        self.properties_panel.update_transform_from_clip(
            clip.transform,
            clip.transform_keyframes,
            playhead_seconds=self.playhead_seconds,
        )
        self._sync_preview_to_timeline()
        self._mark_dirty()

    def on_transform_keyframe_removed(
        self, clip_id: str, property_name: str, clip_local_time: float
    ):
        self._finalize_transform_session()
        try:
            remove_transform_keyframe(
                self.project, clip_id, property_name, clip_local_time
            )
        except ValueError as exc:
            print(f"[MainWindow] suppression keyframe refusée : {exc}")
            return
        clip = find_clip(self.project, clip_id)
        self.history.record(self.project, "Supprimer une image-clé")
        self.timeline_panel.set_project(self.project)
        if clip is not None:
            self.properties_panel.update_transform_from_clip(
                clip.transform,
                clip.transform_keyframes,
                playhead_seconds=self.playhead_seconds,
            )
        self._sync_preview_to_timeline()
        self._mark_dirty()

    def on_transform_reset(self, clip_id: str) -> None:
        self._finalize_transform_session()
        try:
            reset_clip_transform(self.project, clip_id)
        except ValueError as exc:
            print(f"[MainWindow] reset transform refusé : {exc}")
            return
        clip = find_clip(self.project, clip_id)
        self.history.record(self.project, "Réinitialiser le mouvement")
        self.timeline_panel.set_project(self.project)
        if clip is not None:
            self.properties_panel.update_transform_from_clip(
                clip.transform,
                clip.transform_keyframes,
                playhead_seconds=self.playhead_seconds,
            )
        self._sync_preview_to_timeline()
        self._mark_dirty()

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

    # ------------------------------------------------------------------
    # Tâche 14 : opérations sur les pistes
    # ------------------------------------------------------------------

    def on_add_track_requested(self, track_type: str) -> None:
        """Ajoute une piste du type demandé, enregistre l'opération."""
        try:
            track = track_operations_add_track(self.project, track_type)
        except ValueError as exc:
            QMessageBox.warning(self, i18n.translate("prefs.title"), str(exc))
            return
        self.history.record(self.project, i18n.translate("tracks.add_video_long"))
        self._refresh_after_track_change()
        self._mark_dirty()
        # Sélectionne la nouvelle piste via ses boutons (lock/visible/mute).
        # L'UI se met à jour avec refresh_clip_widgets dans
        # ``_refresh_after_track_change``.

    def on_remove_track_requested(self, track_id: str) -> None:
        try:
            track_operations_remove_track(self.project, track_id)
        except (KeyError, ValueError) as exc:
            QMessageBox.warning(self, i18n.translate("prefs.title"), str(exc))
            return
        self.history.record(self.project, i18n.translate("tracks.remove"))
        self._refresh_after_track_change()
        self._mark_dirty()

    def on_rename_track_requested(self, track_id: str, new_name: str) -> None:
        if not new_name.strip():
            return
        try:
            track_operations_rename_track(self.project, track_id, new_name.strip())
        except (KeyError, ValueError) as exc:
            QMessageBox.warning(self, i18n.translate("prefs.title"), str(exc))
            return
        self.history.record(self.project, i18n.translate("tracks.rename"))
        self._refresh_after_track_change()
        self._mark_dirty()

    def on_toggle_track_lock(self, track_id: str, locked: bool) -> None:
        try:
            set_track_locked(self.project, track_id, locked)
        except KeyError as exc:
            print(f"[MainWindow] lock : {exc}")
            return
        self.history.record(self.project, i18n.translate("tracks.toggle_lock"))
        self._refresh_after_track_change()
        self._mark_dirty()

    def on_toggle_track_visible(self, track_id: str, visible: bool) -> None:
        try:
            set_track_visible(self.project, track_id, visible)
        except KeyError as exc:
            print(f"[MainWindow] visible : {exc}")
            return
        self.history.record(self.project, i18n.translate("tracks.toggle_visible"))
        self._refresh_after_track_change()
        self._mark_dirty()

    def on_toggle_track_muted(self, track_id: str, muted: bool) -> None:
        try:
            set_track_muted(self.project, track_id, muted)
        except KeyError as exc:
            print(f"[MainWindow] muted : {exc}")
            return
        self.history.record(self.project, i18n.translate("tracks.toggle_mute"))
        self._refresh_after_track_change()
        self._mark_dirty()

    def on_move_track_up(self, track_id: str) -> None:
        self._move_track_relative(track_id, delta=-1)

    def on_move_track_down(self, track_id: str) -> None:
        self._move_track_relative(track_id, delta=1)

    def _move_track_relative(self, track_id: str, *, delta: int) -> None:
        try:
            current_index = next(
                i for i, track in enumerate(self.project.tracks) if track.id == track_id
            )
        except StopIteration:
            return
        target = max(0, min(len(self.project.tracks) - 1, current_index + delta))
        if target == current_index:
            return
        try:
            track_operations_move_track(self.project, track_id, target)
        except (KeyError, ValueError) as exc:
            QMessageBox.warning(self, i18n.translate("prefs.title"), str(exc))
            return
        label_key = (
            "tracks.move_up" if delta < 0 else "tracks.move_down"
        )
        self.history.record(self.project, i18n.translate(label_key))
        self._refresh_after_track_change()
        self._mark_dirty()

    def remove_selected_track(self) -> None:
        sel = self._selected_track_id()
        if sel is None:
            QMessageBox.information(
                self,
                i18n.translate("prefs.title"),
                i18n.translate("tracks.remove"),
            )
            return
        self.on_remove_track_requested(sel)

    def rename_selected_track(self) -> None:
        sel = self._selected_track_id()
        if sel is None:
            return
        track = next((t for t in self.project.tracks if t.id == sel), None)
        if track is None:
            return
        new_name, ok = QInputDialog.getText(
            self,
            i18n.translate("tracks.rename"),
            i18n.translate("tracks.rename"),
            text=track.name,
        )
        if ok and new_name and new_name != track.name:
            self.on_rename_track_requested(sel, new_name.strip())

    def _move_selected_track(self, *, direction: int) -> None:
        sel = self._selected_track_id()
        if sel is None:
            return
        self._move_track_relative(sel, delta=direction)

    def toggle_selected_track_lock(self) -> None:
        sel = self._selected_track_id()
        if sel is None:
            return
        track = next((t for t in self.project.tracks if t.id == sel), None)
        if track is None:
            return
        self.on_toggle_track_lock(sel, not track.locked)

    def toggle_selected_track_visible(self) -> None:
        sel = self._selected_track_id()
        if sel is None:
            return
        track = next((t for t in self.project.tracks if t.id == sel), None)
        if track is None:
            return
        self.on_toggle_track_visible(sel, not track.visible)

    def toggle_selected_track_muted(self) -> None:
        sel = self._selected_track_id()
        if sel is None:
            return
        track = next((t for t in self.project.tracks if t.id == sel), None)
        if track is None:
            return
        self.on_toggle_track_muted(sel, not track.muted)

    def _selected_track_id(self) -> str | None:
        if self.properties_panel.selected_clip is not None:
            return self.properties_panel.selected_clip.track_id
        if self.timeline_panel.selected_clip_id is not None:
            clip = next(
                (
                    clip
                    for track in self.project.tracks
                    for clip in track.clips
                    if clip.id == self.timeline_panel.selected_clip_id
                ),
                None,
            )
            if clip is not None:
                return clip.track_id
        return None

    def _refresh_after_track_change(self) -> None:
        """Reconstruit la timeline, l'aperçu, l'inspecteur et la durée."""
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._sync_preview_to_timeline()
        # L'inspecteur (propriétés) doit être régénéré pour le clip
        # sélectionné car l'état de visibilité / mute du track peut
        # avoir changé la disponibilité des boutons.
        if self.timeline_panel.selected_clip_id is not None:
            self.on_clip_selected(self.timeline_panel.selected_clip_id)
        self._refresh_undo_redo_state()

    # ------------------------------------------------------------------
    # Tâche 14 : préférences utilisateur (thème + langue)
    # ------------------------------------------------------------------

    def show_preferences(self) -> None:
        """Ouvre la fenêtre ``Préférences``."""
        dialog = PreferencesDialog(
            current_theme=self.theme_manager.requested_mode,
            current_language_code=i18n.current_language(),
            parent=self,
        )
        dialog.theme_changed.connect(self.on_user_setting_changed)
        dialog.language_changed.connect(self.on_user_setting_changed)
        dialog.restore_defaults_requested.connect(self._restore_default_preferences)
        dialog.exec()

    def _restore_default_preferences(self) -> None:
        self._apply_settings(UserSettings())

    def on_user_setting_changed(self, _value: str) -> None:
        """Recueil les préférences courantes du manager / i18n."""
        theme_mode = self.theme_manager.requested_mode
        language_code = i18n.current_language()
        self._apply_settings(
            UserSettings(theme_mode=theme_mode, language=language_code)
        )

    def _apply_settings(self, settings: UserSettings) -> None:
        # Application du thème dans Qt.
        self.theme_manager.set_mode(settings.theme_mode)
        self.theme_manager.apply_to(QApplication.instance())
        # Application de la langue.
        if i18n.current_language() != settings.language:
            i18n.set_language(settings.language)
        # Persistance (écriture atomique dans le répertoire de
        # configuration, jamais dans le dépôt du projet).
        save_user_settings(settings)
        # Mise à jour des libellés dépendant de la langue.
        self._retranslate_ui()

    def on_language_changed(self, code: str) -> None:
        """Callback i18n : retraduit l'interface à chaud."""
        self._retranslate_ui()
        # Persistance immédiate : la langue doit suivre les changements.
        save_user_settings(
            UserSettings(
                theme_mode=self.theme_manager.requested_mode,
                language=code,
            )
        )

    def _retranslate_ui(self) -> None:
        """Force la mise à jour des textes dépendant de la langue."""
        self.setWindowTitle(i18n.translate("app.title"))
        # On reconstruit la barre de menus (chemin simple) : chaque
        # label n'est pas réécrit mais les changements de langue se
        # font à la réouverture de la fenêtre Préférences au minimum.
        for menu in self.menuBar().findChildren(QMenu):
            menu.setTitle(self._translate_menu_title(menu.objectName()))
        # Mise à jour des widgets traduisibles les plus visibles.
        if hasattr(self.preview_panel, "update_translations"):
            self.preview_panel.update_translations()
        self.timeline_panel.refresh_clip_widgets()
        self._refresh_undo_redo_state()

    @staticmethod
    def _translate_menu_title(object_name: str | None) -> str:
        mapping = {
            "file_menu": "menu.file",
            "edit_menu": "menu.edit",
            "view_menu": "menu.view",
            "timeline_menu": "menu.timeline",
            "help_menu": "menu.help",
        }
        key = mapping.get(object_name or "")
        return i18n.translate(key) if key else ""
