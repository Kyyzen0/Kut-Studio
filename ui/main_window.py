import os
import tempfile
import time
from dataclasses import replace

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QAction, QCursor
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
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

from core.autosave import (
    AutosaveCoordinator,
    autosave_is_newer,
    discard_autosave,
    sidecar_path,
)
from core.effects import apply_color_effect, play_crossfade_preview, set_volume
from core.effects_library import (
    EffectCategory,
    EffectPreset,
    UserPresetStore,
    apply_preset_to_clip,
    builtin_presets,
    snapshot_clip_preset,
)
from core.edit_history import ProjectHistory
from core.color_grading import (
    ColorCurve,
    ColorGrade,
    ColorGradingError,
    ColorGradingService,
    LUTResource,
    make_user_color_preset,
)
from core.transition_presets import (
    TransitionPreset,
    TransitionPresetStore,
    builtin_transition_presets,
    make_user_transition_preset,
)
from core.text_presets import (
    TextPreset,
    TextPresetStore,
    builtin_text_presets,
    make_user_text_preset,
)
from core.text_style import (
    TextStyle,
    default_text_style,
)
from core.export_engine import ExportEngine
from core.media_cache import cached_probe
from core.media_probe import MediaProbeError, probe_media, probe_video
from core.project_factory import create_default_project
from core.project_io import load_project, save_project
from core.project_model import Clip, MediaAsset, Project
from core.subtitle_io import load_srt, parse_srt, save_srt
from core.shortcuts import resolve_shortcut
from core.scopes import ColorSpace, ScopeResult, VideoLevels
from core.scopes_analyzer import (
    ScopeAnalysis,
    ScopeAnalyzer,
    ScopeExtractionError,
    cleanup_temporary_paths,
    png_to_scope_frame,
)
from core.studio_runtime import StudioRuntime, peak_rss_bytes
from core.audio_recorder import AudioRecorder, AudioRecorderError, pcm_duration, write_wav
from core.timeline_editing import (
    add_marker,
    apply_solo,
    delete_clips,
    move_clips,
    neighbor_marker,
    remove_marker,
    ripple_trim_left,
    roll_edit,
    shift_track_after,
    slide_clip,
    slip_clip,
)
from core.timeline_index import build_timeline_index
from core.timeline_navigation import step_frames
from core.workspace_state import PanelId, workspace_display_name
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
    timeline_duration,
)
from core.render_plan import RenderPlan, build_render_plan
from core.timeline_view_model import build_export_clips
from core.user_settings import (
    DEFAULT_LANGUAGE,
    DEFAULT_THEME,
    VALID_LANGUAGES,
    VALID_THEME_MODES,
    UserSettings,
    load_user_settings,
    save_user_settings,
)
from core.visual_effects import evaluate_transform
from ui import i18n
from ui.debug_overlay import DebugOverlay
from ui.mixer_panel import MixerPanel
from ui.preferences_dialog import PreferencesDialog
from ui.preview_panel import PreviewPanel
from ui.project_panel import ProjectPanel, SavePresetDialog, SaveTransitionPresetDialog
from ui.scopes_panel import ScopeLayout, ScopeView, ScopesPanel
from ui.properties_panel import PropertiesPanel
from ui.timeline_panel import TimelinePanel
from ui.export_panel import ExportPanel
from ui.side_rail import SideRail, DEFAULT_SECTIONS
from ui.workspace import WorkspaceManager
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


# --- Scopes vidéo / monitoring couleur (tâche 31) -------------------------
#
# Ces trois constantes bornent le coût CPU de l'analyse : pendant la
# lecture, on ne veut pas analyser plus de 10 images / s (au‑delà, la
# scope n'est pas lisible à l'œil et on sature le CPU au détriment de
# la lecture).


SCOPES_MIN_INTERVAL: float = 0.1
"""Intervalle minimal (secondes) entre deux analyses pendant la lecture."""

SCOPES_COLUMNS: int = 320
"""Nombre de colonnes des waveform / parade."""

SCOPES_VECTORSCOPE_BINS: int = 128
"""Résolution du vectorscope (côté en bins)."""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Kut-Studio")
        self.setMinimumSize(1180, 720)
        self.resize(1480, 920)
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
        # État Master : préférence de session, jamais du projet.
        self._master_gain_db = float(loaded_settings.master_gain_db)
        self._master_muted = bool(loaded_settings.master_muted)
        self._render_quality = loaded_settings.render_quality
        self.theme_manager = ThemeManager(requested_mode=loaded_settings.theme_mode)
        i18n.set_language(loaded_settings.language)
        self.runtime = StudioRuntime(
            profile=loaded_settings.performance_profile,
            preview_quality=loaded_settings.preview_quality,
        )
        self._timeline_index = None
        self._timeline_index_project_id: int | None = None
        self._autosave = AutosaveCoordinator()
        # S'abonne aux changements de langue pour recharger les libellés.
        # Conserver la référence permet de se désabonner à la fermeture.
        # Une lambda anonyme conserverait les anciennes fenêtres en mémoire.
        self._i18n_callback = self.on_language_changed
        i18n.subscribe(self._i18n_callback)

        self._build_menu_bar()

        self.preview_panel = PreviewPanel(
            self.toggle_play,
            self.stop_playback,
            self.seek_relative,
            self.cut_at_playhead,
            self.import_media_via_dialog,
        )
        # --- Scopes vidéo / monitoring couleur (tâche 31) ---------------
        # La visibilité par défaut vient des préférences utilisateur
        # déjà chargées ; sinon les scopes restent repliés : on ne
        # rogne pas le viewer sans que l'utilisateur l'ait demandé.
        self._scopes_visible: bool = bool(loaded_settings.scopes_visible)
        self._scopes_layout_pref: str = loaded_settings.scopes_layout
        self._scopes_view_pref: str = loaded_settings.scopes_view
        self._scopes_levels_pref: str = loaded_settings.scopes_levels
        self._scopes_alerts_pref: bool = bool(
            loaded_settings.scopes_alerts_enabled
        )
        self._last_scopes_playhead = -1.0
        # Les SRT/ASS créés pour une commande de scopes n'appartiennent pas
        # à l'export en cours : l'analyseur les reçoit puis les supprime.
        self._scope_temporary_paths: tuple[str, ...] = ()
        # L'analyseur s'exécute hors du thread Qt : le panneau reçoit
        # les résultats via un signal Qt émis depuis le thread de
        # travail (voir ``_on_scopes_analysis_ready``).
        self.scopes_analyzer = ScopeAnalyzer(
            on_result=self._on_scopes_analysis_ready,
            on_error=self._on_scopes_analysis_failed,
            # 10 analyses / s maximum pendant la lecture : au‑delà,
            # on saturerait le CPU sans gain de lisibilité.
            min_interval=SCOPES_MIN_INTERVAL,
        )
        self.scopes_panel = ScopesPanel()
        self.scopes_panel.refresh_requested.connect(
            self._request_scopes_analysis
        )
        self.scopes_panel.layout_changed.connect(
            self._persist_scopes_preferences
        )
        self.scopes_panel.levels_changed.connect(
            self._persist_scopes_preferences
        )
        # Application des préférences (sans émettre de signal : on ne
        # veut pas réécrire le fichier qu'on vient de lire).
        self.scopes_panel.set_layout_mode(self._scopes_layout_pref)
        self.scopes_panel.set_single_view(self._scopes_view_pref)
        self.scopes_panel.set_levels(self._scopes_levels_pref)
        self.scopes_panel.set_alerts_enabled(self._scopes_alerts_pref)
        self.scopes_panel.setVisible(self._scopes_visible)
        # Le viewer et les scopes partagent un splitter vertical : les
        # scopes sont redimensionnables et escamotables sans toucher
        # au dock de la zone centrale.
        self._viewer_host = QSplitter(Qt.Vertical)
        self._viewer_host.setObjectName("viewer_with_scopes")
        self._viewer_host.setChildrenCollapsible(False)
        self._viewer_host.setHandleWidth(6)
        self._viewer_host.addWidget(self.preview_panel)
        self._viewer_host.addWidget(self.scopes_panel)
        self._viewer_host.setStretchFactor(0, 3)
        self._viewer_host.setStretchFactor(1, 2)
        # Par défaut, les scopes restent repliés pour ne pas rogner le
        # viewer ; l'utilisateur les ouvre via le menu Affichage.
        self._viewer_host.setSizes([520, 0])

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
        self.project_panel.add_transition_requested.connect(self.add_transition_from_library)
        self.project_panel.graphic_create_requested.connect(
            self.add_graphic_at_playhead
        )
        self.project_panel.graphic_import_requested.connect(
            self.import_graphic_image
        )
        # --- Organisation avancée de la bibliothèque (tâche 25) ---
        # Dossiers
        self.project_panel.folder_create_requested.connect(
            self._on_folder_create_requested
        )
        self.project_panel.folder_rename_requested.connect(
            self._on_folder_rename_requested
        )
        self.project_panel.folder_delete_requested.connect(
            self._on_folder_delete_requested
        )
        # Tags
        self.project_panel.tag_manager_requested.connect(
            self._on_tag_manager_requested
        )
        # Affectations
        self.project_panel.asset_move_to_folder_requested.connect(
            self._on_asset_move_to_folder
        )
        self.project_panel.asset_tag_toggled.connect(
            self._on_asset_tag_toggled
        )
        self.project_panel.asset_relink_requested.connect(
            self._on_asset_relink_requested
        )
        self.project_panel.asset_rename_requested.connect(
            self._on_asset_rename_requested
        )
        self.project_panel.asset_remove_requested.connect(
            self._on_asset_remove_requested
        )
        self.project_panel.asset_occurrences_requested.connect(
            self._on_asset_occurrences_requested
        )
        self.properties_panel = PropertiesPanel(self.update_color_effect, self.update_volume)
        self.properties_panel.set_project_color_presets(
            getattr(self.project, "color_presets", [])
        )
        self.timeline_panel = TimelinePanel(self.project)
        # La timeline peint ses fonds et ses clips à la main : elle doit
        # suivre les changements de palette du gestionnaire de thème.
        self.timeline_panel.subscribe_to_theme(self.theme_manager)
        self.timeline_panel.on_structure_changed = self._on_timeline_structure_changed
        self.timeline_panel.attach_runtime(self.runtime)
        self.timeline_panel.attach_preview_panel(self.preview_panel)
        self.timeline_panel.clips_move_requested.connect(self.on_clips_move_requested)
        self.timeline_panel.blade_cut_requested.connect(self.on_blade_cut_requested)
        self.timeline_panel.selection_cleared.connect(self._reset_selection_and_inspector)
        self.timeline_panel.transition_selected.connect(self.on_transition_selected)
        self.properties_panel.transition_type_changed.connect(self.on_transition_type_changed)
        self.properties_panel.transition_duration_changed.connect(self.on_transition_duration_changed)
        self.properties_panel.transition_remove_requested.connect(self.remove_selected_transition)
        self.timeline_panel.duplicate_requested.connect(self.duplicate_selected_clip)
        self.timeline_panel.ripple_delete_requested.connect(self.ripple_delete_selected_clip)
        self.timeline_panel.toggle_enabled_requested.connect(self.toggle_selected_clip_enabled)
        self.timeline_panel.marker_add_requested.connect(self.add_marker_at)
        self.timeline_panel.marker_rename_requested.connect(self.rename_marker)
        self.timeline_panel.solo_toggled.connect(self.on_track_solo)
        self.timeline_panel.arm_toggled.connect(self.on_track_armed)
        self.timeline_panel.height_cycle_requested.connect(self.on_track_height_cycle)
        self.timeline_panel.collapse_toggled.connect(self.on_track_collapsed)
        self.timeline_panel.slip_requested.connect(self.on_slip_requested)
        self.timeline_panel.slide_requested.connect(self.on_slide_requested)
        self.timeline_panel.roll_requested.connect(self.on_roll_requested)
        self.timeline_panel.record_requested.connect(self.on_record_toggled)
        self._audio_recorder = AudioRecorder()
        self._record_origin = 0.0
        self._record_tracks: list[str] = []
        self._apply_runtime_hints()
        self._init_faithful_preview()
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
        self.properties_panel.subtitle_editor.content_changed.connect(
            self.update_subtitle_from_editor
        )
        # Tâche 13 : opérations visuelles.
        self.properties_panel.transform_changed.connect(self.on_transform_property_changed)
        self.properties_panel.keyframe_added.connect(self.on_transform_keyframe_added)
        self.properties_panel.keyframe_removed.connect(self.on_transform_keyframe_removed)
        self.properties_panel.transform_reset.connect(self.on_transform_reset)
        # Tâche 18 : time remapping
        self.properties_panel.speed_changed.connect(self.on_speed_changed)
        self.properties_panel.reverse_toggled.connect(self.on_reverse_toggled)
        self.properties_panel.freeze_frame_created.connect(self.on_freeze_frame_created)
        self.properties_panel.freeze_frame_removed.connect(self.on_freeze_frame_removed)
        self.properties_panel.freeze_duration_changed.connect(self.on_freeze_duration_changed)
        self.properties_panel.time_remapping_reset.connect(self.on_time_remapping_reset)
        # Tâche 21 : effets visuels du clip.
        self.properties_panel.effect_enabled_changed.connect(
            self.on_clip_effect_enabled_changed
        )
        self.properties_panel.effect_add_requested.connect(self.on_clip_effect_added)
        self.properties_panel.effect_removed.connect(self.on_clip_effect_removed)
        self.properties_panel.effect_moved.connect(self.on_clip_effect_moved)
        self.properties_panel.effect_parameter_changed.connect(
            self.on_clip_effect_parameter_changed
        )
        # --- Effets audio non destructifs (tâche 27) ---
        self.properties_panel.audio_effect_add_requested.connect(
            self.on_clip_audio_effect_added
        )
        self.properties_panel.audio_effect_removed.connect(
            self.on_clip_audio_effect_removed
        )
        self.properties_panel.audio_effect_moved.connect(
            self.on_clip_audio_effect_moved
        )
        self.properties_panel.audio_effect_enabled_changed.connect(
            self.on_clip_audio_effect_enabled_changed
        )
        self.properties_panel.audio_effect_parameter_changed.connect(
            self.on_clip_audio_effect_parameter_changed
        )
        # Tâche 29 : étalonnage couleur, courbes, presets et LUTs.
        self.properties_panel.color_grade_field_changed.connect(
            self.on_color_grade_field_changed
        )
        self.properties_panel.color_grade_enabled_changed.connect(
            self.on_color_grade_enabled_changed
        )
        self.properties_panel.color_curve_changed.connect(
            self.on_color_curve_changed
        )
        self.properties_panel.color_preset_applied.connect(
            self.on_color_preset_applied
        )
        self.properties_panel.color_preset_save_requested.connect(
            self.on_color_preset_save_requested
        )
        self.properties_panel.color_lut_import_requested.connect(
            self.on_lut_loaded
        )
        self.properties_panel.color_lut_remove_requested.connect(
            self.on_color_lut_removed
        )
        self.properties_panel.color_grade_reset_requested.connect(
            self.on_color_grade_reset
        )
        self.properties_panel.graphic_property_changed.connect(
            self.on_graphic_property_changed
        )
        # Tâche 22 : bibliothèque d'effets et presets.
        # ``UserPresetStore`` conserve la liste des presets utilisateur
        # en mémoire et persiste à chaque mutation.
        self.user_preset_store = UserPresetStore()
        self.project_panel.set_user_effect_presets(self.user_preset_store.all())
        self.user_preset_store.subscribe(self._on_user_presets_changed)
        self.project_panel.effect_apply_requested.connect(
            self.on_effect_preset_apply_requested
        )
        self.project_panel.effect_preset_save_requested.connect(
            self.on_effect_preset_save_requested
        )
        self.project_panel.effect_preset_delete_requested.connect(
            self.on_effect_preset_delete_requested
        )
        # Tâche 23 : bibliothèque de transitions et presets.
        # ``TransitionPresetStore`` conserve favoris + presets utilisateur
        # en mémoire et persiste à chaque mutation.
        self.transition_preset_store = TransitionPresetStore()
        self.project_panel.set_transition_presets(
            self.transition_preset_store.all_presets(),
            favorites=self.transition_preset_store.favorites(),
        )
        self.transition_preset_store.subscribe(self._on_transition_presets_changed)
        self.project_panel.transition_apply_requested.connect(
            self.on_transition_preset_apply_requested
        )
        self.project_panel.transition_preset_save_requested.connect(
            self.on_transition_preset_save_requested
        )
        self.project_panel.transition_preset_delete_requested.connect(
            self.on_transition_preset_delete_requested
        )
        self.project_panel.transition_favorite_toggled.connect(
            self.on_transition_favorite_toggled
        )
        # Tâche 24 : modèles de texte et édition du style.
        self.text_preset_store = TextPresetStore()
        self.project_panel.subtitle_view.set_presets(
            self.text_preset_store.all_presets()
        )
        self.text_preset_store.subscribe(self._on_text_presets_changed)
        self.project_panel.preset_apply_requested.connect(
            self.on_text_preset_apply_requested
        )
        self.project_panel.preset_new_clip_requested.connect(
            self.on_text_preset_new_clip_requested
        )
        self.project_panel.preset_save_requested.connect(
            self.on_text_preset_save_requested
        )
        self.project_panel.preset_delete_requested.connect(
            self.on_text_preset_delete_requested
        )
        self.properties_panel.subtitle_content_changed.connect(
            self.on_subtitle_content_changed
        )
        self.properties_panel.subtitle_style_changed.connect(
            self.on_subtitle_style_changed
        )
        self.properties_panel.subtitle_style_reset.connect(
            self.on_subtitle_style_reset
        )

        # Initialisation de l'horloge de programme (tâche 8).
        # ``playhead_seconds`` est une propriété qui délègue au
        # panneau de timeline (voir plus bas) : le repli sert uniquement
        # avant la construction du panneau.
        self._playhead_fallback: float = 0.0
        self.is_playing: bool = False
        self._last_tick_monotonic: float | None = None
        # Affichage initial de la durée totale (clip de démo = 12 s).
        self._update_timeline_duration()
        self._sync_preview_to_timeline()

        # Espace de travail : le gestionnaire est l'unique autorité sur
        # la disposition. Il enregistre les quatre panneaux existants
        # (mêmes objets, mêmes signaux) puis compose les zones dock.
        self.mixer_panel = MixerPanel()
        self.workspace = WorkspaceManager(self)
        self.workspace.register(PanelId.MEDIA, self.project_panel)
        # Le dock « viewer » contient le viewer **et** le panneau de
        # scopes dans un splitter vertical (tâche 31) : c'est ce
        # host qu'on enregistre, pas le seul ``preview_panel``.
        self.workspace.register(PanelId.VIEWER, self._viewer_host)
        self.workspace.register(PanelId.INSPECTOR, self.properties_panel)
        self.workspace.register(PanelId.TIMELINE, self.timeline_panel)
        self.workspace.register(PanelId.MIXER, self.mixer_panel)
        self._connect_audio_controls()
        self.mixer_panel.set_project(self.project)
        workspace_root = self.workspace.build()
        # Le menu « Fenêtre » ne peut être rempli qu'une fois le
        # gestionnaire d'espace de travail construit.
        self._build_workspace_menu(self.window_menu)

        self.editor_page = QWidget()
        # Le rail vertical est global et reste visible même si tous les
        # panneaux dockés sont repliés / cachés. On l'intègre donc comme
        # un widget frère du workspace_root, dans une ligne horizontale.
        editor_layout = QVBoxLayout(self.editor_page)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(0)

        # Barre latérale d'icônes (Médias / Éditer / Effets…).
        self.side_rail = SideRail(DEFAULT_SECTIONS)
        self.side_rail.set_active("media")
        self.side_rail.section_changed.connect(self._on_side_rail_changed)

        body_row = QWidget()
        body_layout = QHBoxLayout(body_row)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self.side_rail)
        body_layout.addWidget(workspace_root, 1)
        editor_layout.addWidget(body_row, 1)
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

        self._debug_overlay = DebugOverlay(shell)
        self._debug_timer = QTimer(self)
        self._debug_timer.setInterval(500)
        self._debug_timer.timeout.connect(self._refresh_debug_overlay)

        # L'horloge ne tourne que pendant la lecture. La laisser active
        # en pause réévaluait toute la timeline et repeignait l'interface
        # 25 fois par seconde sans que rien ne change.
        self.timeline_timer = QTimer(self)
        self.timeline_timer.setInterval(40)
        self.timeline_timer.timeout.connect(self._tick_playback)

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(2000)
        self._autosave_timer.timeout.connect(self._write_autosave)
        self.theme_manager.apply_to(QApplication.instance())
        # Synchronise l'état initial des actions undo/redo. Cette
        # opération doit suivre ``_build_top_bar`` qui crée
        # ``project_label``.
        self._refresh_undo_redo_state()
        if os.environ.get("KUT_STUDIO_DEBUG") == "1":
            self.diagnostics_action.setChecked(True)

    def on_workspace_changed(self) -> None:
        """Notifié par le gestionnaire d'espace de travail.

        Volontairement léger : on ne fait que resynchroniser l'état de
        lecture et les menus. Appeler ``_retranslate_ui`` ici
        reconstruirait la timeline à chaque changement de disposition,
        ce qui serait inutile et coûteux.
        """
        self._sync_playhead_labels()
        self._sync_workspace_menu()

    # ------------------------------------------------------------------
    # Rail latéral : navigation principale par section
    # ------------------------------------------------------------------

    def _on_side_rail_changed(self, section_id: str) -> None:
        """Réagit à un clic sur le rail vertical.

        La cible dépend de la section :

        - ``media`` : on affiche le panneau Médias ;
        - ``audio`` / ``text`` / ``effects`` / ``transitions`` : on rend
          le panneau Médias visible et on y affiche le contenu demandé ;
        - ``color`` / ``graphics`` : on ouvre directement l'onglet
          correspondant de l'inspecteur ;
        - ``edit`` / ``templates`` :
          aucune action concrète disponible aujourd'hui — on laisse
          l'état actif visuellement et on affiche un message discret
          pour rester honnête vis-à-vis de l'utilisateur.
        """
        # Le rail et la navigation supérieure représentent les mêmes
        # espaces de travail. La synchronisation est bidirectionnelle :
        # cliquer à gauche doit donc aussi déplacer l'état actif en haut.
        top_nav_index = {
            "edit": 0,
            "media": 1,
            "effects": 2,
            "color": 3,
            "audio": 4,
            "graphics": 5,
        }.get(section_id)
        if top_nav_index is not None and hasattr(self, "top_nav_buttons"):
            for index, button in enumerate(self.top_nav_buttons):
                button.setChecked(index == top_nav_index)

        inspector_tab = {"color": 1, "audio": 3, "graphics": 4}.get(section_id)
        if inspector_tab is not None:
            if not self.workspace.is_visible(PanelId.INSPECTOR):
                self.workspace.set_panel_visible(PanelId.INSPECTOR, True)
            self.properties_panel._select_inspector_tab(inspector_tab)
            if section_id == "color":
                return

        if section_id in (
            "media", "audio", "text", "effects", "transitions", "graphics"
        ):
            # S'assurer que le panneau Médias est visible.
            if not self.workspace.is_visible(PanelId.MEDIA):
                self.workspace.set_panel_visible(PanelId.MEDIA, True)
            self.project_panel.select_section(section_id)
            return

        # Sections sans panneau dédié pour l'instant : on confirme
        # visuellement la sélection mais on prévient l'utilisateur que
        # la zone est inactive. C'est le comportement « honnête »
        # demandé par le cahier des charges.
        self._notify_placeholder(section_id)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        workspace = getattr(self, "workspace", None)
        if workspace is not None:
            QTimer.singleShot(0, workspace.balance_vertical_split)

    def _connect_audio_controls(self) -> None:
        """Branche le mixeur et l'inspecteur sur les handlers audio.

        Aucun de ces widgets n'écrit dans le projet : ils émettent une
        intention, et les handlers ci-dessous appliquent le changement,
        enregistrent l'historique et marquent le projet modifié. C'est
        le même chemin que pour le déplacement d'un clip, ce qui évite
        deux conventions d'annulation.
        """
        mixer = self.mixer_panel
        mixer.volume_changed.connect(self.on_track_volume_changed)
        mixer.pan_changed.connect(self.on_track_pan_changed)
        mixer.mute_toggled.connect(self.on_track_mute_toggled)
        mixer.solo_toggled.connect(self.on_track_solo_toggled)
        mixer.arm_toggled.connect(self.on_track_arm_toggled)
        mixer.reset_requested.connect(self.on_track_audio_reset)
        mixer.master_volume_changed.connect(self.on_master_volume_changed)
        mixer.master_mute_toggled.connect(self.on_master_mute_toggled)
        mixer.master_reset_requested.connect(self.on_master_reset)

        properties = self.properties_panel
        properties.audio_gain_changed.connect(self.on_clip_gain_changed)
        properties.audio_pan_changed.connect(self.on_clip_pan_changed)
        properties.audio_fade_changed.connect(self.on_clip_fade_changed)
        properties.audio_fades_reset.connect(self.on_clip_fades_reset)

        timeline = self.timeline_panel
        timeline.fade_changed_requested.connect(self.on_clip_fade_from_timeline)
        timeline.reset_clip_fades_requested.connect(self.on_clip_fades_reset)

        # Le volume Master est une préférence d'interface, pas du projet :
        # il vit dans les réglages utilisateur, jamais dans le ``.kut``.
        # Les valeurs initiales viennent des préférences chargées.
        mixer.set_master(self._master_gain_db, self._master_muted)
        return

    # -- Mixeur : pistes ------------------------------------------------

    def _find_audio_track(self, track_id: str):
        if self.project is None or not track_id:
            return None
        for track in self.project.tracks:
            if track.id == track_id and track.type == "audio":
                return track
        return None

    def _find_clip_anywhere(self, clip_id: str):
        """Recherche un clip dans toutes les pistes du projet courant.

        Retourne le clip ou ``None``. Utilisé par les handlers
        d'étalonnage / LUT qui n'ont pas besoin de connaître la
        piste parente (l'étalonnage est strictement lié au clip).
        """
        if self.project is None:
            return None
        for track in self.project.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    return clip
        return None

    def project_io_root(self):
        """Répertoire de sauvegarde du projet courant (pour les LUTs).

        L'inspecteur Couleur a besoin du ``project_root`` pour
        résoudre les chemins relatifs des LUTs ``.cube``. On
        retourne le dossier du fichier de projet courant s'il est
        connu, sinon ``None`` (les chemins absolus restent valides).
        """
        path = getattr(self, "current_project_path", None)
        if not path:
            return None
        from pathlib import Path

        return Path(path).parent

    def on_track_volume_changed(self, track_id: str, value: float) -> None:
        track = self._find_audio_track(track_id)
        if track is None or track.locked:
            self.mixer_panel.refresh_track(track) if track else None
            return
        track.set_volume_db(value)
        self._record_audio_change(i18n.translate("mixer.volume"))

    # ------------------------------------------------------------------
    # Étalonnage couleur (tâche 29)
    # ------------------------------------------------------------------

    def _commit_color_grade(
        self, clip_id: str, grade: ColorGrade, label: str, *, coalesce: bool = False
    ) -> bool:
        clip, track = self._find_clip_and_track(clip_id)
        if clip is None or track is None or track.type != "video" or track.locked:
            return False
        if not coalesce:
            # Une rafale de curseur précédente devient sa propre étape,
            # distincte de cette action ponctuelle.
            self._finalize_color_history()
        try:
            ColorGradingService().set_grade(self.project, clip_id, grade)
        except ColorGradingError as exc:
            print(f"[MainWindow] Étalonnage refusé : {exc}")
            return False
        if coalesce:
            self._schedule_color_history(label, clip_id)
        else:
            self._record_history(label)
            self._reload_timeline_preserving_selection(clip_id)
        self._refresh_color_monitor(clip_id)
        return True

    def _refresh_color_monitor(self, clip_id: str) -> None:
        """Aligne le moniteur sur l'étalonnage courant du projet."""
        self.update_color_effect()
        try:
            self._invalidate_preview_for_clip(clip_id)
            self._sync_preview_to_timeline()
        except Exception:
            pass
        # Scopes (tâche 31) : un changement d'exposition, de contraste,
        # de saturation, de courbe ou de LUT doit être visible
        # immédiatement. En pause on force l'analyse (pas de
        # limitation de fréquence) car l'utilisateur juge son réglage
        # en direct ; pendant la lecture on laisse l'analyseur
        # borner la fréquence.
        self._request_scopes_analysis(force=not self.is_playing)

    def _schedule_color_history(self, label: str, clip_id: str) -> None:
        """Regroupe une rafale de curseur couleur dans une seule étape."""
        first = not getattr(self, "_color_session_active", False)
        self._color_session_active = True
        self._color_session_clip_id = clip_id
        if first:
            self._color_session_label = label
        if not hasattr(self, "_color_session_timer"):
            self._color_session_timer = QTimer(self)
            self._color_session_timer.setSingleShot(True)
            self._color_session_timer.timeout.connect(self._finalize_color_history)
        self._color_session_timer.start(400)
        self._mark_dirty()

    def _finalize_color_history(self) -> None:
        """Enregistre l'état final d'une rafale d'étalonnage."""
        if not getattr(self, "_color_session_active", False):
            return
        timer = getattr(self, "_color_session_timer", None)
        if timer is not None:
            timer.stop()
        self._color_session_active = False
        label = getattr(self, "_color_session_label", "Étalonnage")
        clip_id = getattr(self, "_color_session_clip_id", None)
        self._record_history(label)
        if clip_id:
            self._reload_timeline_preserving_selection(clip_id)

    def on_color_grade_field_changed(
        self, clip_id: str, field: str, value: float
    ) -> None:
        try:
            current = ColorGradingService().get_grade(self.project, clip_id)
            updated = current.with_field(field, float(value))
        except ColorGradingError as exc:
            print(f"[MainWindow] Paramètre couleur invalide : {exc}")
            return
        self._commit_color_grade(clip_id, updated, f"Couleur : {field}", coalesce=True)

    def on_color_grade_enabled_changed(self, clip_id: str, enabled: bool) -> None:
        try:
            current = ColorGradingService().get_grade(self.project, clip_id)
        except ColorGradingError:
            return
        self._commit_color_grade(
            clip_id, current.with_enabled(enabled), "Activer l’étalonnage"
        )

    def on_color_curve_changed(
        self, clip_id: str, channel: str, points: object
    ) -> None:
        try:
            current = ColorGradingService().get_grade(self.project, clip_id)
            curve = ColorCurve(points=tuple(tuple(point) for point in points))
            curves = current.curves._replace(channel, curve)
            updated = current.with_curves(curves)
        except (ColorGradingError, TypeError, ValueError) as exc:
            print(f"[MainWindow] Courbe couleur invalide : {exc}")
            return
        self._commit_color_grade(clip_id, updated, f"Courbe {channel}", coalesce=True)

    def on_color_grade_replaced(
        self, clip_id: str, grade_dict: dict | None
    ) -> None:
        """Remplace l'étalonnage d'un clip par un nouveau :class:`ColorGrade`.

        ``grade_dict`` est la représentation JSON-ready du grade ;
        on reconstruit un :class:`ColorGrade` côté métier pour
        garantir la validation des bornes et la cohérence des
        champs.
        """
        from core.project_io import _deserialize_color_grade

        grade = (
            ColorGrade.identity()
            if grade_dict is None
            else _deserialize_color_grade(
                grade_dict, project_root=self.project_io_root()
            )
        )
        if grade is None:
            print("[MainWindow] Étalonnage invalide")
            return
        self._commit_color_grade(clip_id, grade, "Étalonner le clip")

    def on_color_grade_reset(self, clip_id: str) -> None:
        """Réinitialise l'étalonnage d'un clip."""
        from core.color_grading import (
            ColorGradingError,
            ColorGradingService,
        )

        clip, track = self._find_clip_and_track(clip_id)
        if clip is None or track is None or track.locked:
            return
        self._finalize_color_history()
        service = ColorGradingService()
        try:
            service.reset_grade(self.project, clip_id)
        except ColorGradingError as exc:
            print(f"[MainWindow] Reset étalonnage refusé : {exc}")
            return
        self._record_history("Réinitialiser l'étalonnage")
        self._reload_timeline_preserving_selection(clip_id)
        self._refresh_color_monitor(clip_id)

    def on_color_preset_applied(
        self, clip_id: str, preset_id: str
    ) -> None:
        """Applique un preset d'étalonnage au clip."""
        from core.color_grading import (
            ColorGradingError,
            ColorGradingService,
            ColorPresetStore,
        )

        clip, track = self._find_clip_and_track(clip_id)
        if clip is None or track is None or track.locked:
            return
        self._finalize_color_history()
        store = self.properties_panel.color_preset_store
        preset = store.get_preset(preset_id)
        if preset is None:
            return
        service = ColorGradingService()
        try:
            service.apply_preset(self.project, clip_id, preset)
        except ColorGradingError as exc:
            print(f"[MainWindow] Preset étalonnage refusé : {exc}")
            return
        self._record_history("Appliquer un preset d'étalonnage")
        self._reload_timeline_preserving_selection(clip_id)
        self._refresh_color_monitor(clip_id)

    def on_color_preset_save_requested(self, clip_id: str, name: str) -> None:
        self._finalize_color_history()
        try:
            grade = ColorGradingService().get_grade(self.project, clip_id)
            preset = make_user_color_preset(name=name, description="", grade=grade)
            self.properties_panel.color_preset_store.add_user_preset(preset)
        except (ColorGradingError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Preset couleur", str(exc))
            return
        self.project.color_presets.append(preset)
        self._record_history("Enregistrer un preset couleur")
        self._mark_dirty()
        self.properties_panel._refresh_color_presets()

    def on_lut_loaded(self, clip_id: str, lut_path: str) -> None:
        """Importe un LUT et l'attache au clip sélectionné."""
        from core.color_grading import (
            ColorGrade,
            ColorGradingError,
            ColorGradingService,
        )
        from core.lut_importer import (
            LUTImportError,
            parse_cube_lut,
        )

        clip, track = self._find_clip_and_track(clip_id)
        if clip is None or track is None or track.locked:
            return
        self._finalize_color_history()
        try:
            parsed = parse_cube_lut(lut_path)
        except LUTImportError as exc:
            QMessageBox.warning(self, "LUT", f"LUT invalide : {exc}")
            return
        try:
            resource = LUTResource.from_path(
                lut_path,
                title=parsed.title,
                project_root=self.project_io_root()
                if hasattr(self, "project_io_root")
                else None,
            )
        except (ColorGradingError, OSError) as exc:
            QMessageBox.warning(self, "LUT", f"LUT invalide : {exc}")
            return
        # On préserve les réglages existants du clip ; seul le LUT
        # est remplacé.
        service = ColorGradingService()
        current = service.get_grade(self.project, clip_id)
        # On reconstruit en s'assurant que le hash / la taille
        # reflètent le contenu lu (la valeur ``_from_path`` a déjà
        # re-calculé ces champs).
        try:
            updated = current.with_lut(resource)
            service.set_grade(self.project, clip_id, updated)
        except ColorGradingError as exc:
            QMessageBox.warning(self, "LUT", str(exc))
            return
        self._record_history("Importer un LUT")
        self._reload_timeline_preserving_selection(clip_id)
        self._refresh_color_monitor(clip_id)

    def on_color_lut_removed(self, clip_id: str) -> None:
        try:
            current = ColorGradingService().get_grade(self.project, clip_id)
        except ColorGradingError:
            return
        self._commit_color_grade(clip_id, current.with_lut(None), "Retirer le LUT")

    # ------------------------------------------------------------------
    # Automation audio et ducking (tâche 28)
    # ------------------------------------------------------------------

    def on_track_role_changed(
        self, track_id: str, role: str
    ) -> None:
        """Définit le rôle sémantique d'une piste audio."""
        from core.audio_automation import (
            AudioAutomationError,
            AudioAutomationService,
            TrackRole,
        )
        track = self._find_audio_track(track_id)
        if track is None:
            return
        if track.locked:
            self.mixer_panel.refresh_track(track)
            return
        service = AudioAutomationService()
        try:
            service.set_track_role(self.project, track.id, TrackRole(role))
        except AudioAutomationError as exc:
            QMessageBox.warning(self, "Mixage", str(exc))
            self.mixer_panel.refresh_track(track)
            return
        self._record_audio_change("Modifier le rôle de la piste")

    def on_track_automation_point_added(
        self, track_id: str, time_seconds: float,
        gain_db: float, fade_seconds: float,
    ) -> None:
        """Ajoute un point-clé d'automation sur une piste."""
        from core.audio_automation import (
            AudioAutomationError,
            AudioAutomationService,
        )
        track = self._find_audio_track(track_id)
        if track is None or track.locked:
            return
        service = AudioAutomationService()
        try:
            service.add_automation_point(
                self.project, track.id, time_seconds, gain_db, fade_seconds
            )
        except AudioAutomationError as exc:
            QMessageBox.warning(self, "Mixage", str(exc))
            return
        self._record_audio_change("Ajouter un point d'automation")

    def on_track_automation_point_removed(
        self, track_id: str, time_seconds: float
    ) -> None:
        track = self._find_audio_track(track_id)
        if track is None or track.locked:
            return
        from core.audio_automation import (
            AudioAutomationError,
            AudioAutomationService,
        )
        service = AudioAutomationService()
        try:
            service.remove_automation_point(
                self.project, track.id, time_seconds
            )
        except AudioAutomationError as exc:
            QMessageBox.warning(self, "Mixage", str(exc))
            return
        self._record_audio_change("Supprimer un point d'automation")

    def on_track_automation_point_updated(
        self, track_id: str, time_seconds: float,
        gain_db: float, fade_seconds: float,
    ) -> None:
        track = self._find_audio_track(track_id)
        if track is None or track.locked:
            return
        from core.audio_automation import (
            AudioAutomationError,
            AudioAutomationService,
        )
        service = AudioAutomationService()
        try:
            service.update_automation_point(
                self.project, track.id, time_seconds,
                gain_db=gain_db, fade_seconds=fade_seconds,
            )
        except AudioAutomationError as exc:
            QMessageBox.warning(self, "Mixage", str(exc))
            return
        self._record_audio_change("Modifier un point d'automation")

    def on_ducking_sidechain_added(
        self, music_track_id: str, voice_track_id: str,
    ) -> None:
        """Crée une association de ducking musique ← voix."""
        from core.audio_automation import (
            AudioAutomationError,
            AudioAutomationService,
            DuckingConfig,
        )
        music = self._find_audio_track(music_track_id)
        voice = self._find_audio_track(voice_track_id)
        if music is None or voice is None:
            return
        if music.locked or voice.locked:
            return
        service = AudioAutomationService()
        try:
            service.add_ducking_sidechain(
                self.project, music.id, voice.id,
                config=DuckingConfig(),
            )
        except AudioAutomationError as exc:
            QMessageBox.warning(self, "Mixage", str(exc))
            return
        self._record_audio_change("Ajouter un ducking")

    def on_ducking_sidechain_removed(self, sidechain_id: str) -> None:
        from core.audio_automation import (
            AudioAutomationError,
            AudioAutomationService,
        )
        service = AudioAutomationService()
        try:
            service.remove_ducking_sidechain(self.project, sidechain_id)
        except AudioAutomationError as exc:
            QMessageBox.warning(self, "Mixage", str(exc))
            return
        self._record_audio_change("Supprimer un ducking")

    def on_ducking_config_changed(
        self, sidechain_id: str,
        threshold_db: float, reduction_db: float,
        attack_seconds: float, release_seconds: float,
    ) -> None:
        from core.audio_automation import (
            AudioAutomationError,
            AudioAutomationService,
            DuckingConfig,
        )
        service = AudioAutomationService()
        try:
            config = DuckingConfig(
                threshold_db=threshold_db,
                reduction_db=reduction_db,
                attack_seconds=attack_seconds,
                release_seconds=release_seconds,
            )
            service.update_ducking_config(self.project, sidechain_id, config)
        except AudioAutomationError as exc:
            QMessageBox.warning(self, "Mixage", str(exc))
            return
        self._record_audio_change("Modifier le ducking")

    def on_track_pan_changed(self, track_id: str, value: float) -> None:
        track = self._find_audio_track(track_id)
        if track is None or track.locked:
            if track is not None:
                self.mixer_panel.refresh_track(track)
            return
        track.set_pan(value)
        self._record_audio_change(i18n.translate("mixer.pan"))

    def on_track_mute_toggled(self, track_id: str, muted: bool) -> None:
        track = self._find_audio_track(track_id)
        if track is None:
            return
        if track.locked:
            self.mixer_panel.refresh_track(track)
            self.timeline_panel.refresh_headers()
            return
        track.muted = bool(muted)
        self.timeline_panel.refresh_headers()
        self._record_audio_change(i18n.translate("mixer.mute"))

    def on_track_solo_toggled(self, track_id: str, solo: bool) -> None:
        track = self._find_audio_track(track_id)
        if track is None:
            return
        if track.locked:
            self.mixer_panel.refresh_track(track)
            self.timeline_panel.refresh_headers()
            return
        track.solo = bool(solo)
        # Le solo est un état global : toutes les tranches doivent refléter.
        self.timeline_panel.refresh_headers()
        self._sync_preview_to_timeline()
        self._record_audio_change(i18n.translate("mixer.solo"))

    def on_track_arm_toggled(self, track_id: str, armed: bool) -> None:
        track = self._find_audio_track(track_id)
        if track is None:
            return
        if track.locked:
            self.mixer_panel.refresh_track(track)
            self.timeline_panel.refresh_headers()
            return
        track.armed = bool(armed)
        self.timeline_panel.refresh_headers()
        self._record_audio_change(i18n.translate("mixer.arm"))

    def on_track_audio_reset(self, track_id: str) -> None:
        track = self._find_audio_track(track_id)
        if track is None or track.locked:
            if track is not None:
                self.mixer_panel.refresh_track(track)
            return
        track.reset_audio()
        self.mixer_panel.refresh_track(track)
        self._record_audio_change(i18n.translate("mixer.reset"))

    def on_master_volume_changed(self, value: float) -> None:
        self._master_gain_db = float(value)
        self._save_master_state()

    def on_master_mute_toggled(self, muted: bool) -> None:
        self._master_muted = bool(muted)
        self._save_master_state()

    def on_master_reset(self) -> None:
        self._master_gain_db = 0.0
        self._master_muted = False
        self.mixer_panel.set_master(0.0, False)
        self._save_master_state()

    def _save_master_state(self) -> None:
        """Persiste l'état Master dans les préférences utilisateur.

        Le Master n'appartient pas au projet : il décrit la session de
        mixage de l'utilisateur, pas le montage livré.
        """
        try:
            save_user_settings(
                replace(
                    self._settings_snapshot(),
                    master_gain_db=self._master_gain_db,
                    master_muted=self._master_muted,
                )
            )
        except OSError:
            pass

    # -- Inspecteur : clip ----------------------------------------------

    def _selected_audio_clip(self):
        """Clip audio sélectionné et sa piste, ou ``(None, None)``."""
        clip_id = self.timeline_panel.selected_clip_id
        if not clip_id or self.project is None:
            return None, None
        for track in self.project.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    return clip, track
        return None, None

    def on_clip_gain_changed(self, value: float) -> None:
        clip, track = self._selected_audio_clip()
        if clip is None or track is None or track.locked:
            self._sync_audio_inspector()
            return
        clip.gain_db = float(value)
        self._record_audio_change(i18n.translate("audio.gain"))

    def on_clip_pan_changed(self, value: float) -> None:
        clip, track = self._selected_audio_clip()
        if clip is None or track is None or track.locked:
            self._sync_audio_inspector()
            return
        clip.pan = float(value)
        self._record_audio_change(i18n.translate("mixer.pan"))

    def on_clip_fade_changed(self, which: str, value: float) -> None:
        clip, track = self._selected_audio_clip()
        if clip is None or track is None or track.locked:
            self._sync_audio_inspector()
            return
        if which == "in":
            clip.set_fade_in(float(value))
        else:
            clip.set_fade_out(float(value))
        self.timeline_panel.refresh_clip_widgets()
        self._record_audio_change(
            i18n.translate("audio.fade_in" if which == "in" else "audio.fade_out")
        )

    def on_clip_fade_from_timeline(self, clip_id: str, which: str, value: float) -> None:
        """Applique un fondu glisse dans la timeline."""
        clip, track = self._find_clip_and_track(clip_id)
        if clip is None or track is None or track.locked:
            return
        if which == "in":
            clip.set_fade_in(float(value))
        else:
            clip.set_fade_out(float(value))
        self._sync_audio_inspector()
        self._record_audio_change(
            i18n.translate("audio.fade_in" if which == "in" else "audio.fade_out")
        )

    def on_clip_fades_reset(self, *args) -> None:
        """Double-clic ou bouton Réinitialiser : fondus à zéro."""
        clip_id = args[0] if args else self.timeline_panel.selected_clip_id
        clip, track = self._find_clip_and_track(clip_id) if clip_id else (None, None)
        if clip is None or track is None or track.locked:
            return
        clip.reset_fades()
        self.timeline_panel.refresh_clip_widgets()
        self._sync_audio_inspector()
        self._record_audio_change(i18n.translate("audio.reset_fades"))

    def _find_clip_and_track(self, clip_id: str):
        if self.project is None or not clip_id:
            return None, None
        for track in self.project.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    return clip, track
        return None, None

    def _record_audio_change(self, label: str) -> None:
        """Enregistre une modification audio dans l'historique."""
        if self.project is None:
            return
        self._record_history(label)
        self.mixer_panel.set_project(self.project)
        self.mixer_panel.set_master(self._master_gain_db, self._master_muted)

    def _sync_audio_inspector(self) -> None:
        """Rafraîchit le groupe audio de l'inspecteur depuis le modèle."""
        clip, track = self._selected_audio_clip()
        if clip is None or getattr(track, "type", None) != "audio":
            self.properties_panel.set_audio_clip(None)
            return
        self.properties_panel.set_audio_clip(clip, locked=bool(track.locked))

    def closeEvent(self, event) -> None:
        """Libère les abonnements globaux avant de fermer la fenêtre."""
        workspace = getattr(self, "workspace", None)
        if workspace is not None:
            workspace.shutdown()
        if hasattr(self, "timeline_timer") and self.timeline_timer is not None:
            self.timeline_timer.stop()
        recorder = getattr(self, "_audio_recorder", None)
        if recorder is not None and recorder.is_recording:
            pcm, rate, channels = recorder.stop()
            self._place_recording(pcm, rate, channels, quiet=True)
        self._finalize_pending_edit_sessions()
        self._write_autosave()
        if hasattr(self, "_autosave_timer") and self._autosave_timer is not None:
            self._autosave_timer.stop()
        if hasattr(self, "_debug_timer") and self._debug_timer is not None:
            self._debug_timer.stop()
        preview_pump = getattr(self, "_preview_pump_timer", None)
        if preview_pump is not None:
            preview_pump.stop()
        preview_engine = getattr(self, "preview_engine", None)
        if preview_engine is not None:
            try:
                preview_engine.cancel_all()
            except Exception:
                pass
        timeline = getattr(self, "timeline_panel", None)
        if timeline is not None:
            timeline.unsubscribe_from_theme()
        preview = getattr(self, "preview_panel", None)
        if preview is not None:
            preview.release_media()
        # Le thread de travail des scopes est un daemon, mais on le
        # ferme proprement : un FFmpeg en cours ne doit pas survivre à
        # la fenêtre.
        scopes_analyzer = getattr(self, "scopes_analyzer", None)
        if scopes_analyzer is not None:
            scopes_analyzer.close()
        cleanup_temporary_paths(getattr(self, "_scope_temporary_paths", ()))
        self._scope_temporary_paths = ()
        autosave = getattr(self, "_autosave", None)
        if autosave is not None:
            autosave.close()
        runtime = getattr(self, "runtime", None)
        if runtime is not None:
            runtime.shutdown()
        subtitle_timer = getattr(self, "_subtitle_edit_timer", None)
        if subtitle_timer is not None:
            subtitle_timer.stop()

        callback = getattr(self, "_i18n_callback", None)
        if callback is not None:
            i18n.unsubscribe(callback)
            self._i18n_callback = None

        super().closeEvent(event)

    def _workspace_icon(self, name: str):
        """Icône du registre d'icônes pour les menus de workspace."""
        from ui.design_system import Iconography
        from ui.icons import IconName, make_icon

        return make_icon(getattr(IconName, name), size=Iconography.md)

    @property
    def playhead_seconds(self) -> float:
        """Position de la tête de lecture, en secondes.

        **Source de vérité unique** : le panneau de timeline. La
        fenêtre lit et écrit par cette propriété, ce qui évite deux
        états qui divergeraient — notamment quand la timeline est
        détachée dans une fenêtre séparée, où seule une des deux
        copies serait mise à jour.
        """
        timeline = getattr(self, "timeline_panel", None)
        if timeline is None:
            return self._playhead_fallback
        return timeline.playhead_seconds

    @playhead_seconds.setter
    def playhead_seconds(self, value: float) -> None:
        timeline = getattr(self, "timeline_panel", None)
        if timeline is None:
            self._playhead_fallback = float(value)
            return
        # On passe par la méthode du panneau : elle applique le
        # bornage et rafraîchit le compteur horaire.
        timeline.set_playhead_seconds(float(value))

    def _sync_playhead_labels(self) -> None:
        """Réaffiche l'heure courante sur les panneaux de transport.

        Inutile en temps normal (l'écriture passe déjà par le panneau),
        mais utile après un changement de disposition : la tête de
        lecture peut migrer vers une autre fenêtre.
        """
        timeline = getattr(self, "timeline_panel", None)
        if timeline is None:
            return
        position = timeline.playhead_seconds
        timeline.set_playhead_seconds(position)
        # Met aussi à jour le timecode turquoise de la barre de transport.
        preview = getattr(self, "preview_panel", None)
        if preview is not None:
            preview.set_timecode(position, timeline.duration_seconds)

    def _build_workspace_menu(self, menu: QMenu) -> None:
        """Remplit le menu « Fenêtre » avec les actions de panneaux.

        Ce menu est la voie *principale* et découvrable : la barre
        d'options au survol reste un raccourci discret. Les deux
        partagent la même source d'actions
        (:meth:`WorkspaceManager.build_actions`), donc aucune logique
        n'est dupliquée.
        """
        panels_menu = menu.addMenu("Panneaux")
        for panel in PanelId:
            action = QAction(panel.label(), self)
            action.setCheckable(True)
            action.setChecked(self.workspace.is_visible(panel))
            action.setIcon(self._workspace_icon("PANEL_MAXIMIZE"))
            action.triggered.connect(
                lambda _checked, p=panel: self.toggle_panel(p)
            )
            panels_menu.addAction(action)

        menu.addSeparator()
        for panel in PanelId:
            # Un sous-menu par panneau : sans étiquette, quatre groupes
            # d'actions identiques seraient ambiguës.
            panel_menu = menu.addMenu(panel.label())
            for action in self.workspace.build_actions(panel):
                if isinstance(action, QMenu):
                    # ``addMenu`` gère l'action associée au sous-menu.
                    # Reparenté ici, le QMenu devient un enfant visuel du
                    # parent et Qt calcule son popup en coordonnées locales,
                    # ce qui le faisait se chevaucher au lieu de s'ouvrir
                    # à droite.
                    panel_menu.addMenu(action)
                else:
                    panel_menu.addAction(action)
        restore_action = QAction("Restaurer la disposition", self)
        restore_action.setIcon(self._workspace_icon("PANEL_RESTORE"))
        restore_action.triggered.connect(self.restore_workspace_layout)
        menu.addSeparator()
        menu.addAction(restore_action)

        # Espaces de travail nommés (§8) : appliqués ou enregistrés.
        spaces = menu.addMenu("Espaces de travail")
        for name in self.workspace.list_workspaces():
            action = QAction(workspace_display_name(name), self)
            action.triggered.connect(
                lambda _c=False, n=name: self.apply_workspace(n)
            )
            spaces.addAction(action)
        spaces.addSeparator()
        save_space = QAction("Enregistrer la disposition sous…", spaces)
        save_space.triggered.connect(self.save_workspace_as)
        spaces.addAction(save_space)

    # -- Espaces de travail --------------------------------------------

    def apply_workspace(self, name: str) -> None:
        """Applique un espace de travail nommé."""
        if not self.workspace.apply_workspace(name):
            return
        self._sync_workspace_menu()

    def save_workspace_as(self) -> None:
        """Enregistre la disposition courante sous un nom choisi."""
        name, accepted = QInputDialog.getText(
            self, "Espace de travail", "Nom de l'espace de travail :"
        )
        if not accepted or not name.strip():
            return
        if self.workspace.save_workspace_as(name):
            QMessageBox.information(
                self,
                "Espace de travail",
                f"Disposition enregistrée sous « {name.strip()} ».",
            )
        else:
            QMessageBox.warning(
                self,
                "Espace de travail",
                "Impossible d'enregistrer cet espace de travail.",
            )

    # -- Actions de workspace ------------------------------------------

    def toggle_panel(self, panel: PanelId) -> None:
        """Ouvre ou ferme un panneau depuis le menu."""
        if self.workspace.state.is_visible(panel) and self.workspace.is_visible(
            panel
        ):
            self.workspace.set_panel_visible(panel, False)
        else:
            self.workspace.set_panel_visible(panel, True)
        self._sync_workspace_menu()

    def maximize_panel(self, panel: PanelId) -> None:
        """Maximise temporairement un panneau."""
        self.workspace.maximize_panel(panel)
        self._sync_workspace_menu()

    def restore_workspace_layout(self) -> None:
        """Restaure la disposition précédant une maximisation."""
        self.workspace.restore_layout()
        self._sync_workspace_menu()

    def reset_workspace_layout(self) -> None:
        """Rétablit la disposition et la taille par défaut."""
        self.workspace.reset_layout()
        self._sync_workspace_menu()

    def _sync_workspace_menu(self) -> None:
        """Recalcule l'état coché du menu « Panneaux »."""
        menu = self.menuBar()
        window_menu = None
        for candidate in menu.actions():
            if candidate.menu() is not None and candidate.text() == "Fenêtre":
                window_menu = candidate.menu()
                break
        if window_menu is None:
            return
        panels_menu = None
        for action in window_menu.actions():
            sub = action.menu()
            if sub is not None and sub.title() == "Panneaux":
                panels_menu = sub
                break
        if panels_menu is None:
            return
        for action, panel in zip(panels_menu.actions(), PanelId):
            action.setChecked(self.workspace.is_visible(panel))

    def _build_top_bar(self):
        """Barre supérieure fine façon DaVinci / Final Cut.

        Composition :

        - pastilles macOS + logo + marque à gauche ;
        - navigation centrale (Éditer, Médias, Effets, Couleur, Audio,
          Graphiques) avec un état actif turquoise ;
        - nom de séquence et indicateur d'enregistrement au centre ;
        - à droite, qualité de prévisualisation, résolution, zoom et
          bouton Exporter turquoise très lisible.

        Toutes les chaînes sont ``setStyleSheet`` au niveau du widget
        conteneur : la feuille de style globale reste l'autorité pour
        les widgets enfants.
        """
        from ui.design_system import Iconography, Sizes, Spacing
        from ui.icons import IconButton, IconLabel, IconName, make_icon

        bar = QWidget()
        bar.setFixedHeight(Sizes.top_bar)
        bar.setObjectName("main_topbar")
        bar.setStyleSheet(
            f"QWidget#main_topbar {{ background: {COLORS['panel']};"
            f" border-bottom: 1px solid {COLORS['border']}; }}"
        )
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(Spacing.md, 0, Spacing.md, 0)
        layout.setSpacing(Spacing.md)

        # --- Logo + marque + pastilles macOS ---------------------------
        logo_box = QWidget()
        logo_layout = QHBoxLayout(logo_box)
        logo_layout.setContentsMargins(0, 0, 0, 0)
        logo_layout.setSpacing(Spacing.sm)

        # Trois petites pastilles façon macOS — purement décoratif mais
        # elles ancrent visuellement la barre dans le registre
        # « application macOS haut de gamme » du cahier des charges.
        macos_box = QWidget()
        macos_layout = QHBoxLayout(macos_box)
        macos_layout.setContentsMargins(0, 0, 0, 0)
        macos_layout.setSpacing(6)
        for hex_color in ("#FF5F57", "#FEBC2E", "#28C840"):
            dot = QLabel()
            dot.setFixedSize(11, 11)
            dot.setStyleSheet(
                f"background: {hex_color}; border-radius: 5px;"
                f" border: 1px solid rgba(0, 0, 0, 0.25);"
            )
            macos_layout.addWidget(dot)
        logo_layout.addWidget(macos_box)

        # Logo carré turquoise + nom de la marque.
        logo = QLabel("K")
        logo.setAlignment(Qt.AlignCenter)
        logo.setFixedSize(26, 26)
        logo.setStyleSheet(
            f"background: {COLORS['accent']}; color: #061514;"
            f" border-radius: 7px; font-size: 14px; font-weight: 800;"
        )
        brand = QLabel("KUT‑STUDIO")
        brand.setStyleSheet(label_style(11, "text", 800))
        logo_layout.addSpacing(Spacing.sm)
        logo_layout.addWidget(logo)
        logo_layout.addWidget(brand)
        layout.addWidget(logo_box)

        # Séparateur vertical fin.
        layout.addWidget(self._vseparator())

        # --- Navigation centrale --------------------------------------
        # Éditer / Médias / Effets / Couleur / Audio / Graphiques.
        # Boutons ``checkable`` alignés horizontalement pour un look
        # segmented compact.
        top_nav_row = QWidget()
        top_nav_layout = QHBoxLayout(top_nav_row)
        top_nav_layout.setContentsMargins(0, 0, 0, 0)
        top_nav_layout.setSpacing(2)
        self.top_nav_buttons: list[QPushButton] = []
        for index, label in enumerate(("Éditer", "Médias", "Effets", "Couleur", "Audio", "Graphiques")):
            button = QPushButton(label)
            button.setObjectName("topNavTab")
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.setStyleSheet(
                f"QPushButton#topNavTab {{ background: transparent;"
                f" color: {COLORS['muted']}; border: 1px solid transparent;"
                f" border-radius: 6px; padding: 4px 10px;"
                f" font-weight: 600; font-size: 12px; }}"
                f"QPushButton#topNavTab:hover {{ color: {COLORS['text']};"
                f" background: {COLORS['surface_hover']}; }}"
                f"QPushButton#topNavTab:checked {{ color: {COLORS['accent']};"
                f" background: {COLORS['accent_dark']};"
                f" border: 1px solid {COLORS['accent']}; }}"
            )
            button.clicked.connect(
                lambda _checked=False, idx=index: self._select_top_nav(idx)
            )
            self.top_nav_buttons.append(button)
            top_nav_layout.addWidget(button)
        self.top_nav = top_nav_row
        layout.addWidget(self.top_nav)

        # Étire le nav pour absorber l'espace libre, en gardant les
        # boutons à gauche (sinon ils se répartissent sur la ligne).
        top_nav_layout.addStretch(1)

        # Séparateur vertical fin.
        layout.addWidget(self._vseparator())

        # --- Bloc central : nom de séquence + enregistrement ----------
        sequence_box = QWidget()
        sequence_layout = QVBoxLayout(sequence_box)
        sequence_layout.setContentsMargins(0, 0, 0, 0)
        sequence_layout.setSpacing(0)
        sequence_layout.addStretch()
        seq_title = QLabel("Séquence")
        seq_title.setStyleSheet(label_style(9, "muted", 700))
        seq_title.setAlignment(Qt.AlignCenter)
        self.project_label = QLabel("Projet sans titre")
        self.project_label.setStyleSheet(label_style(12, "text", 700))
        self.project_label.setAlignment(Qt.AlignCenter)
        sequence_layout.addWidget(seq_title)
        sequence_layout.addWidget(self.project_label)
        sequence_layout.addStretch()

        saved_box = QWidget()
        saved_layout = QHBoxLayout(saved_box)
        saved_layout.setContentsMargins(0, 0, 0, 0)
        saved_layout.setSpacing(6)
        self.saved_indicator = QLabel("● Enregistré")
        self.saved_indicator.setStyleSheet(label_style(10, "success", 700))
        self.saved_indicator.setAlignment(Qt.AlignCenter)
        saved_layout.addWidget(self.saved_indicator)
        layout.addWidget(sequence_box, 1)
        layout.addWidget(saved_box)

        layout.addWidget(self._vseparator())

        # --- Chip preview settings -------------------------------------
        # On reste compact : icônes seules pour Aperçu et Qualité, le
        # texte est dans l'infobulle.
        preview_btn = IconButton(
            icon=IconName.MENU,
            tooltip="Réglages de prévisualisation",
            size=Sizes.icon_button,
        )
        preview_btn.setObjectName("chipButton")
        layout.addWidget(preview_btn)

        quality_btn = IconButton(
            icon=IconName.INFO,
            tooltip="Qualité de prévisualisation : Plein",
            size=Sizes.icon_button,
        )
        quality_btn.setObjectName("chipButton")
        layout.addWidget(quality_btn)

        resolution_btn = IconButton(
            icon=IconName.FILM,
            tooltip="Résolution de la séquence : 1920 × 1080",
            size=Sizes.icon_button,
        )
        resolution_btn.setObjectName("chipButton")
        layout.addWidget(resolution_btn)

        zoom_out = IconButton(
            icon=IconName.REMOVE,
            tooltip="Dézoomer la timeline",
            size=Sizes.icon_button,
        )
        zoom_in = IconButton(
            icon=IconName.PLUS,
            tooltip="Zoomer la timeline",
            size=Sizes.icon_button,
        )
        zoom_label = QLabel("100 %")
        zoom_label.setStyleSheet(label_style(11, "muted", 700))
        zoom_label.setMinimumWidth(42)
        zoom_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(zoom_out)
        layout.addWidget(zoom_label)
        layout.addWidget(zoom_in)

        layout.addSpacing(Spacing.xs)
        self.export_button = IconButton(
            icon=IconName.EXPORT,
            tooltip="Exporter le montage (⌘E)",
            size=Sizes.button_md,
            square=False,
            accent=True,
        )
        self.export_button.setText(" Exporter")
        self.export_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.export_button.setMinimumWidth(108)
        self.export_button.setMinimumHeight(32)
        self.export_button.clicked.connect(self.show_export)
        layout.addWidget(self.export_button)

        # Undo / Redo conservés mais en mode compact à droite de l'export.
        self.undo_button = IconButton(
            icon=IconName.RESET,
            tooltip="Annuler (Ctrl+Z)",
            size=Sizes.icon_button_sm,
        )
        self.redo_button = IconButton(
            icon=IconName.RESET,
            tooltip="Rétablir (Ctrl+Y)",
            size=Sizes.icon_button_sm,
        )
        from PySide6.QtGui import QTransform, QIcon
        from PySide6.QtCore import Qt as QtCore
        base_pixmap = make_icon(IconName.RESET, size=Sizes.icon_button_sm - 4).pixmap(64, 64)
        self.redo_button.setIcon(
            QIcon(base_pixmap.transformed(
                QTransform().rotate(180), QtCore.SmoothTransformation
            ))
        )
        self.undo_button.clicked.connect(self.undo_last)
        self.redo_button.clicked.connect(self.redo_last)
        layout.addWidget(self.undo_button)
        layout.addWidget(self.redo_button)

        self._update_top_bar()
        return bar

    def _vseparator(self) -> "QWidget":
        """Séparateur vertical fin, réutilisé dans la barre supérieure."""
        sep = QWidget()
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"background: {COLORS['border']};")
        return sep

    def _select_top_nav(self, index: int) -> None:
        """Bascule l'onglet actif de la barre supérieure.

        Synchronise le rail latéral : un clic sur ``Médias`` active
        la section Médias du rail, etc.
        """
        for i, button in enumerate(self.top_nav_buttons):
            button.setChecked(i == index)
        # Synchronise le rail latéral.
        section_map = {0: "edit", 1: "media", 2: "effects",
                       3: "color", 4: "audio", 5: "graphics"}
        target = section_map.get(index, "edit")
        if hasattr(self, "side_rail") and self.side_rail.active() != target:
            self.side_rail.set_active(target)
        self._on_side_rail_changed(target)

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
        self._schedule_autosave()

    def _mark_clean(self) -> None:
        self._finalize_pending_edit_sessions()
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
        # Une rafale d'étalonnage encore ouverte est déjà dans le projet :
        # elle est incluse dans ce snapshot, sans étape supplémentaire.
        if getattr(self, "_color_session_active", False):
            timer = getattr(self, "_color_session_timer", None)
            if timer is not None:
                timer.stop()
            self._color_session_active = False
        # Même principe pour une saisie graphique encore ouverte : l'action
        # courante capture son état final sans laisser un timer enregistrer un
        # snapshot obsolète plus tard.
        if getattr(self, "_graphic_session_active", False):
            timer = getattr(self, "_graphic_session_timer", None)
            if timer is not None:
                timer.stop()
            self._graphic_session_active = False
        self.history.record(self.project, label)
        self._refresh_undo_redo_state()

    def _finalize_pending_edit_sessions(self) -> None:
        """Ferme les éditions différées avant sauvegarde ou navigation."""
        self._flush_subtitle_history_record()
        self._finalize_transform_session()
        self._finalize_color_history()
        self._finalize_graphic_history()

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
        # Boutons rapides de la top-bar.
        if hasattr(self, "undo_button"):
            self.undo_button.setEnabled(self.history.can_undo)
            self.undo_button.setToolTip(
                f"Annuler : {self.history.undo_label}" if self.history.undo_label else "Annuler"
            )
        if hasattr(self, "redo_button"):
            self.redo_button.setEnabled(self.history.can_redo)
            self.redo_button.setToolTip(
                f"Rétablir : {self.history.redo_label}" if self.history.redo_label else "Rétablir"
            )
        # Synchronise le flag ``project_dirty`` avec l'historique.
        self.project_dirty = self.history.is_dirty
        self._update_top_bar()
        if self.project_dirty:
            self._schedule_autosave()
        else:
            timer = getattr(self, "_autosave_timer", None)
            if timer is not None:
                timer.stop()

    # ------------------------------------------------------------------
    # Undo / Redo / Duplicate / Ripple / Enable
    # ------------------------------------------------------------------

    def _apply_history_snapshot(self, snapshot_project, label: str) -> None:
        """Ré-installe ``snapshot_project`` partout dans l'interface."""
        self.project = snapshot_project
        # La durée affichée vaut au moins une seconde : on borne la tête
        # avec la durée réelle du projet, puis on la repousse au widget.
        duration = timeline_duration(self.project)
        if duration > 0.0:
            self.playhead_seconds = min(max(self.playhead_seconds, 0.0), duration)
        else:
            self.playhead_seconds = max(0.0, self.playhead_seconds)
        self._reload_timeline_preserving_selection()
        self._update_timeline_duration()
        self.timeline_panel.set_playhead_seconds(self.playhead_seconds)
        self.playhead_seconds = self.timeline_panel.playhead_seconds
        self._sync_preview_to_timeline()
        self._refresh_project_library()
        self.properties_panel.set_project_color_presets(
            getattr(self.project, "color_presets", [])
        )
        mixer = getattr(self, "mixer_panel", None)
        if mixer is not None:
            mixer.set_project(self.project)
        self._sync_audio_inspector()
        self._refresh_undo_redo_state()

    def undo_last(self) -> None:
        """Annule la dernière opération enregistrée."""
        self._finalize_pending_edit_sessions()
        snapshot = self.history.undo()
        if snapshot is None:
            return
        self._apply_history_snapshot(snapshot, self.history.undo_label or "")

    def redo_last(self) -> None:
        """Rétablit la dernière opération annulée."""
        self._finalize_pending_edit_sessions()
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
        """Supprime la sélection. En mode ripple, les clips suivants se rapprochent."""
        ids = list(self.timeline_panel.selected_clip_ids)
        if not ids and self.timeline_panel.selected_clip_id:
            ids = [self.timeline_panel.selected_clip_id]
        if not ids:
            return
        if len(ids) == 1 and not self.timeline_panel.ripple_enabled:
            self.delete_selected_clip(ids[0])
            return
        try:
            delete_clips(self.project, ids, ripple=self.timeline_panel.ripple_enabled)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] suppression groupée refusée : {exc}")
            return
        self._record_history("Supprimer la sélection")
        self.timeline_panel.selected_clip_id = None
        self.timeline_panel.selected_clip_ids = set()
        self.active_subtitle_clip = None
        self.properties_panel.set_clip(None, "")
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._mark_dirty()

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
        was_enabled = clip.enabled
        try:
            set_clip_enabled(self.project, clip_id, not was_enabled)
        except KeyError:
            return
        self._record_history(
            "Désactiver le clip" if was_enabled else "Activer le clip"
        )
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    # Tâche 18 : remappage temporel
    def on_speed_changed(self, clip_id: str, speed: float) -> None:
        """Modifie la vitesse d'un clip depuis l'inspecteur."""
        from core.timeline_operations import set_clip_speed
        try:
            clip = set_clip_speed(self.project, clip_id, speed)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] vitesse refusée : {exc}")
            return
        self._record_history("Modifier la vitesse")
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_reverse_toggled(self, clip_id: str, reverse: bool) -> None:
        """Modifie le mode reverse d'un clip depuis l'inspecteur."""
        from core.timeline_operations import set_clip_reverse
        try:
            clip = set_clip_reverse(self.project, clip_id, reverse)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] reverse refusé : {exc}")
            return
        self._record_history("Inverser le clip" if reverse else "Désinverser le clip")
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_freeze_frame_created(self, clip_id: str, freeze_source_time: float, freeze_duration: float) -> None:
        """Crée un arrêt sur image pour un clip depuis l'inspecteur."""
        from core.timeline_operations import set_clip_freeze_frame
        try:
            clip = set_clip_freeze_frame(self.project, clip_id, freeze_source_time, freeze_duration)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] arrêt sur image refusé : {exc}")
            return
        self._record_history("Créer un arrêt sur image")
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_freeze_frame_removed(self, clip_id: str) -> None:
        """Supprime un arrêt sur image pour un clip depuis l'inspecteur."""
        from core.timeline_operations import remove_clip_freeze_frame
        try:
            clip = remove_clip_freeze_frame(self.project, clip_id)
        except KeyError as exc:
            print(f"[MainWindow] suppression arrêt sur image refusée : {exc}")
            return
        self._record_history("Supprimer l'arrêt sur image")
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_freeze_duration_changed(self, clip_id: str, freeze_duration: float) -> None:
        """Modifie la durée d'un arrêt sur image depuis l'inspecteur."""
        from core.timeline_operations import set_clip_freeze_duration
        try:
            clip = set_clip_freeze_duration(self.project, clip_id, freeze_duration)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] durée arrêt sur image refusée : {exc}")
            return
        self._record_history("Modifier la durée de l'arrêt sur image")
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_time_remapping_reset(self, clip_id: str) -> None:
        """Réinitialise le remappage temporel d'un clip depuis l'inspecteur."""
        from core.timeline_operations import reset_clip_time_remapping
        try:
            clip = reset_clip_time_remapping(self.project, clip_id)
        except KeyError as exc:
            print(f"[MainWindow] réinitialisation remappage temporel refusée : {exc}")
            return
        self._record_history("Réinitialiser la vitesse et le remappage temporel")
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    # ------------------------------------------------------------------
    # Effets visuels du clip (tâche 21)
    # ------------------------------------------------------------------

    def _restore_clip_selection(self, clip_id: str) -> None:
        """Resélectionne ``clip_id`` et rafraîchit l'inspecteur sans seek.

        ``select_clip`` annonce la sélection, et ``on_clip_selected``
        place alors la tête de lecture au début du clip. C'est le bon
        comportement pour un clic. Après une coupe, un changement de
        vitesse ou un mute, la tête de lecture doit rester où elle est.
        """
        if self.timeline_panel.find_view_by_id(clip_id) is None:
            self._reset_selection_and_inspector()
            return
        self.timeline_panel._set_selection([clip_id], clip_id, announce=False)
        view = self.timeline_panel.find_view_by_id(clip_id)
        self.active_subtitle_clip = (
            view if getattr(view, "track_type", None) == "subtitle" else None
        )
        self.properties_panel.show_clip(view)
        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            clip = None
        if clip is not None:
            self.properties_panel.update_transform_from_clip(
                clip.transform,
                clip.transform_keyframes,
                playhead_seconds=self.playhead_seconds,
            )
        self._sync_audio_inspector()

    def _reload_timeline_preserving_selection(
        self, preferred_id: str | None = None
    ) -> None:
        """Reconstruit la timeline sans bouger la tête de lecture.

        ``set_project`` efface la sélection. Si on la relisait ensuite,
        elle serait vide et l'inspecteur resterait sur l'ancien clip.
        Un clic doit toujours chercher le début du clip ; un
        rafraîchissement après édition, non.

        ``preferred_id`` devient la sélection principale quand l'édition
        porte sur un clip précis (vitesse, effet) qui n'était pas
        forcément surligné dans la timeline.
        """
        panel = self.timeline_panel
        selected = set(panel.selected_clip_ids)
        primary = panel.selected_clip_id
        if primary:
            selected.add(primary)
        inspector = getattr(self.properties_panel, "selected_clip", None)
        inspector_id = getattr(inspector, "id", None)
        playhead = self.playhead_seconds
        panel.set_project(self.project)
        alive = {
            clip_id
            for clip_id in selected
            if panel.find_view_by_id(clip_id) is not None
        }
        if preferred_id and panel.find_view_by_id(preferred_id) is not None:
            alive.add(preferred_id)
            primary = preferred_id
        elif (
            not alive
            and inspector_id
            and panel.find_view_by_id(inspector_id) is not None
        ):
            alive = {inspector_id}
            primary = inspector_id
        if alive:
            chosen = primary if primary in alive else next(iter(alive))
            self._restore_clip_selection(chosen)
            if len(alive) > 1:
                panel._set_selection(alive, chosen, announce=False)
        else:
            self._reset_selection_and_inspector()
        if self.playhead_seconds != playhead:
            self.playhead_seconds = playhead
            panel.set_playhead_seconds(playhead)

    def _selected_video_clip_id(self) -> str | None:
        """Identifiant du clip vidéo actuellement sélectionné, ou ``None``."""
        view = getattr(self.properties_panel, "selected_clip", None)
        clip_id = getattr(view, "id", None)
        if clip_id is None:
            return None
        if getattr(view, "track_type", None) != "video":
            return None
        return clip_id

    def _refresh_effects_after_change(self, clip_id: str) -> None:
        """Reconstruit timeline et inspecteur depuis le ``Project``."""
        # Les badges font partie des projections ``TimelineClipView`` :
        # les reconstruire est plus sûr qu'une mutation visuelle locale.
        self._reload_timeline_preserving_selection(clip_id)
        # Tâche 30 : effet/transform/grade/LUT modifié → le résultat doit
        # être visible dans le moniteur sans export manuel. On synchronise
        # l'aperçu (repli source immédiat) et on invalide uniquement les
        # segments du clip affecté.
        try:
            self._invalidate_preview_for_clip(clip_id)
            self._sync_preview_to_timeline()
        except Exception:
            pass
        self._update_timeline_duration()
        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            self.properties_panel.update_effects_from_clip([], None)
            self.properties_panel.update_audio_effects_from_clip([], None)
            track_type_for_audio = None
        else:
            self.properties_panel.update_effects_from_clip(
                list(clip.effects), "video"
            )
            # Le rack d'effets audio accepte les pistes ``video`` (si le
            # média porte de l'audio) et ``audio``. On détermine le type
            # de piste à partir du clip, qui le connaît via ``track_id``.
            track_type_for_audio = self._track_type_for_clip(clip_id)
            self.properties_panel.update_audio_effects_from_clip(
                list(clip.audio_effects), track_type_for_audio
            )
        # La bibliothèque d'effets se contente d'être notifiée de
        # l'état du clip sélectionné ; l'inspecteur, lui, garde la
        # responsabilité de l'édition fine.
        self._sync_effects_library_context()
        self._mark_dirty()

    def _track_type_for_clip(self, clip_id: str) -> str | None:
        """Retourne le type de piste (``"video"``, ``"audio"``...) du clip."""
        for track in self.project.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    return track.type
        return None

    def _sync_effects_library_context(self) -> None:
        """Synchronise la bibliothèque d'effets avec le clip sélectionné."""
        clip_id = self._selected_video_clip_id()
        clip_has_effects = False
        if clip_id is not None:
            try:
                clip = find_clip(self.project, clip_id)
            except KeyError:
                clip = None
            clip_has_effects = bool(clip and clip.effects)
        self.project_panel.update_effects_clip_context(
            has_video_clip=clip_id is not None,
            clip_has_effects=clip_has_effects,
        )
        # La bibliothèque de transitions partage la même notion de
        # sélection : on l'aligne dans la foulée.
        self._sync_transitions_library_context()

    def _on_user_presets_changed(self) -> None:
        """Répercute les mutations du store vers la bibliothèque."""
        self.project_panel.set_user_effect_presets(
            self.user_preset_store.all()
        )

    def _sync_transitions_library_context(self) -> None:
        """Synchronise la bibliothèque de transitions avec la sélection."""
        selected = [
            view for view in self.timeline_panel.clip_views
            if view.id in self.timeline_panel.selected_clip_ids
            and view.track_type == "video"
        ]
        has_two_video_clips = (
            len(selected) == 2 and selected[0].track_id == selected[1].track_id
        )
        self.project_panel.update_transitions_clip_context(
            has_two_video_clips=has_two_video_clips,
        )

    # ------------------------------------------------------------------
    # Tâche 22 : gestion des presets d'effets
    # ------------------------------------------------------------------

    def on_effect_preset_apply_requested(self, preset_id: str) -> None:
        """Applique un preset (intégré ou utilisateur) au clip vidéo courant."""
        clip_id = self._selected_video_clip_id()
        if clip_id is None:
            return
        # On cherche d'abord chez les intégrés, puis chez l'utilisateur.
        preset = next(
            (p for p in builtin_presets() if p.id == preset_id),
            None,
        )
        if preset is None:
            preset = self.user_preset_store.get(preset_id)
        if preset is None:
            print(f"[MainWindow] preset inconnu : {preset_id!r}")
            return
        try:
            apply_preset_to_clip(self.project, clip_id, preset)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] application du preset refusée : {exc}")
            return
        self._record_history("Appliquer un preset d'effets")
        self._refresh_effects_after_change(clip_id)

    def on_effect_preset_save_requested(self) -> None:
        """Ouvre le dialogue d'enregistrement d'un preset utilisateur."""
        clip_id = self._selected_video_clip_id()
        if clip_id is None:
            return
        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return
        if not clip.effects:
            QMessageBox.information(
                self,
                i18n.translate("effects.library.dialog.title"),
                i18n.translate("effects.library.no_effects_to_save"),
            )
            return
        default_name = ""
        if clip.label:
            default_name = f"Preset « {clip.label} »"
        dialog = SavePresetDialog(
            self,
            default_name=default_name,
            default_category=EffectCategory.LOOK,
        )
        if dialog.exec() != dialog.Accepted:
            return
        name, description, category = dialog.result_data()
        if not name:
            return
        try:
            preset = snapshot_clip_preset(
                clip,
                name=name,
                description=description,
                category=category,
            )
        except ValueError as exc:
            print(f"[MainWindow] capture de preset refusée : {exc}")
            return
        try:
            self.user_preset_store.add(preset)
        except ValueError as exc:
            print(f"[MainWindow] ajout du preset refusé : {exc}")
            return
        self._record_history("Enregistrer un preset utilisateur")

    def on_effect_preset_delete_requested(self, preset_id: str) -> None:
        """Supprime un preset utilisateur après confirmation."""
        preset = self.user_preset_store.get(preset_id)
        if preset is None:
            return
        if preset.builtin:
            QMessageBox.information(
                self,
                i18n.translate("effects.library.delete"),
                i18n.translate("effects.library.user_builtin_lock"),
            )
            return
        confirm = QMessageBox.question(
            self,
            i18n.translate("effects.library.delete"),
            i18n.translate("effects.library.delete_confirm").format(name=preset.name),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            self.user_preset_store.remove(preset_id)
        except KeyError as exc:
            print(f"[MainWindow] suppression du preset refusée : {exc}")
            return
        self._record_history("Supprimer un preset utilisateur")

    def on_clip_effect_enabled_changed(
        self, clip_id: str, effect_id: str, enabled: bool
    ) -> None:
        """Active ou désactive un effet depuis l'inspecteur."""
        from core.effects_model import set_clip_effect_enabled
        try:
            set_clip_effect_enabled(self.project, clip_id, effect_id, enabled)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] effet refusé : {exc}")
            return
        self._record_history(
            "Activer un effet" if enabled else "Désactiver un effet"
        )
        self._refresh_effects_after_change(clip_id)

    def on_clip_effect_added(self, clip_id: str, effect_type: str) -> None:
        """Ajoute un effet choisi dans l'inspecteur au clip vidéo courant."""
        from core.effects_model import add_effect_to_clip
        try:
            add_effect_to_clip(self.project, clip_id, effect_type)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] ajout d'effet refusé : {exc}")
            return
        self._record_history("Ajouter un effet")
        self._refresh_effects_after_change(clip_id)

    def on_clip_effect_removed(self, clip_id: str, effect_id: str) -> None:
        """Supprime un effet depuis l'inspecteur."""
        from core.effects_model import remove_effect_from_clip
        try:
            remove_effect_from_clip(self.project, clip_id, effect_id)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] suppression d'effet refusée : {exc}")
            return
        self._record_history("Supprimer un effet")
        self._refresh_effects_after_change(clip_id)

    def on_clip_effect_moved(
        self, clip_id: str, effect_id: str, delta: int
    ) -> None:
        """Réordonne un effet depuis l'inspecteur."""
        from core.effects_model import move_clip_effect
        try:
            move_clip_effect(self.project, clip_id, effect_id, delta)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] réordonnancement d'effet refusé : {exc}")
            return
        self._record_history("Réordonner un effet")
        self._refresh_effects_after_change(clip_id)

    def on_clip_effect_parameter_changed(
        self, clip_id: str, effect_id: str, name: str, value: float
    ) -> None:
        """Met à jour un paramètre d'effet depuis l'inspecteur."""
        from core.effects_model import update_clip_effect_parameters
        try:
            update_clip_effect_parameters(
                self.project, clip_id, effect_id, {name: value}
            )
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] paramètre d'effet refusé : {exc}")
            return
        self._record_history("Modifier un effet")
        self._refresh_effects_after_change(clip_id)

    # ------------------------------------------------------------------
    # Effets audio non destructifs (tâche 27)
    # ------------------------------------------------------------------

    def on_clip_audio_effect_added(
        self, clip_id: str, effect_type: str
    ) -> None:
        """Ajoute un effet audio choisi dans l'inspecteur au clip."""
        from core.audio_effects_model import (
            AudioEffectType,
            add_audio_effect_to_clip,
        )

        try:
            add_audio_effect_to_clip(
                self.project, clip_id, AudioEffectType(effect_type)
            )
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] ajout d'effet audio refusé : {exc}")
            return
        self._record_history("Ajouter un effet audio")
        self._refresh_effects_after_change(clip_id)

    def on_clip_audio_effect_removed(
        self, clip_id: str, effect_id: str
    ) -> None:
        """Supprime un effet audio depuis l'inspecteur."""
        from core.audio_effects_model import remove_audio_effect_from_clip
        try:
            remove_audio_effect_from_clip(
                self.project, clip_id, effect_id
            )
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] suppression d'effet audio refusée : {exc}")
            return
        self._record_history("Supprimer un effet audio")
        self._refresh_effects_after_change(clip_id)

    def on_clip_audio_effect_moved(
        self, clip_id: str, effect_id: str, delta: int
    ) -> None:
        """Réordonne un effet audio depuis l'inspecteur."""
        from core.audio_effects_model import move_clip_audio_effect
        try:
            move_clip_audio_effect(
                self.project, clip_id, effect_id, int(delta)
            )
        except (KeyError, ValueError) as exc:
            print(
                f"[MainWindow] réordonnancement d'effet audio refusé : {exc}"
            )
            return
        self._record_history("Réordonner un effet audio")
        self._refresh_effects_after_change(clip_id)

    def on_clip_audio_effect_enabled_changed(
        self, clip_id: str, effect_id: str, enabled: bool
    ) -> None:
        """Active ou désactive un effet audio."""
        from core.audio_effects_model import set_clip_audio_effect_enabled
        try:
            set_clip_audio_effect_enabled(
                self.project, clip_id, effect_id, bool(enabled)
            )
        except (KeyError, ValueError) as exc:
            print(
                f"[MainWindow] activation d'effet audio refusée : {exc}"
            )
            return
        self._record_history("Modifier un effet audio")
        self._refresh_effects_after_change(clip_id)

    def on_clip_audio_effect_parameter_changed(
        self, clip_id: str, effect_id: str, name: str, value: float
    ) -> None:
        """Met à jour un paramètre d'effet audio depuis l'inspecteur."""
        from core.audio_effects_model import update_clip_audio_effect_parameters
        try:
            update_clip_audio_effect_parameters(
                self.project, clip_id, effect_id, {name: value}
            )
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] paramètre d'effet audio refusé : {exc}")
            return
        self._record_history("Modifier un effet audio")
        self._refresh_effects_after_change(clip_id)

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

    def _schedule_subtitle_history_record(
        self, clip_id: str, new_text: str | None = None
    ) -> None:
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
        self.timeline_panel._set_selection([], None, announce=False)
        self.timeline_panel.clear_transition_selection()
        self.properties_panel.set_clip(None, "")
        # La bibliothèque d'effets perd son contexte de clip.
        self._sync_effects_library_context()

    def new_project(self) -> None:
        """Crée un nouveau projet vierge via ``create_default_project()``."""
        self._finalize_pending_edit_sessions()
        self._release_open_project()
        self.project = create_default_project()
        self.current_project_path = None
        self.history.reset(self.project)
        self._refresh_undo_redo_state()
        self.timeline_panel.set_project(self.project)
        self.playhead_seconds = 0.0
        self.is_playing = False
        self._update_timeline_duration()
        self._sync_preview_to_timeline()
        self._refresh_project_library()
        self.properties_panel.set_project_color_presets(
            getattr(self.project, "color_presets", [])
        )
        self._reset_selection_and_inspector()
        self.mixer_panel.set_project(self.project)
        self.mixer_panel.set_master(self._master_gain_db, self._master_muted)
        self._mark_clean()

    def save_project_file(self) -> None:
        """Enregistre le projet courant. Délègue à ``save_project_as`` si aucun chemin."""
        self._flush_subtitle_history_record()
        if self.current_project_path is None:
            self.save_project_as()
            return
        try:
            save_project(self.project, self.current_project_path)
            discard_autosave(self.current_project_path)
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
            discard_autosave(path)
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
        self._finalize_pending_edit_sessions()
        try:
            loaded = load_project(path)
        except (FileNotFoundError, ValueError, OSError, TypeError) as exc:
            QMessageBox.critical(
                self,
                "Impossible d'ouvrir le projet",
                f"Le fichier {path} n'a pas pu être ouvert :\n\n{exc}",
            )
            return
        restored_autosave = False
        if autosave_is_newer(path):
            answer = QMessageBox.question(
                self,
                "Récupération",
                "Une sauvegarde automatique plus récente que ce projet "
                "a été trouvée.\n\nVoulez-vous la restaurer ?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Yes:
                try:
                    loaded = load_project(str(sidecar_path(path)))
                    restored_autosave = True
                except (FileNotFoundError, ValueError, OSError, TypeError) as exc:
                    QMessageBox.critical(
                        self,
                        "Récupération impossible",
                        f"La sauvegarde automatique n'a pas pu être lue :\n\n{exc}",
                    )
        self._release_open_project()
        self.project = loaded
        self.current_project_path = path
        self.history.reset(self.project)
        if restored_autosave:
            # Le fichier .kut est plus ancien que ce qui est à l'écran.
            self.history.mark_unsaved()
        self._refresh_undo_redo_state()
        self.timeline_panel.set_project(self.project)
        self.playhead_seconds = 0.0
        self.is_playing = False
        self._update_timeline_duration()
        self._sync_preview_to_timeline()
        self._refresh_project_library()
        self.properties_panel.set_project_color_presets(
            getattr(self.project, "color_presets", [])
        )
        self._reset_selection_and_inspector()
        self.mixer_panel.set_project(self.project)
        self.mixer_panel.set_master(self._master_gain_db, self._master_muted)
        if restored_autosave:
            self._refresh_undo_redo_state()
        else:
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
        return build_render_plan(
            self.project,
            master_gain_db=self._master_gain_db,
            master_muted=self._master_muted,
        )

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
        file_menu = QMenu(i18n.translate("menu.file"), self)
        file_menu.setObjectName("file_menu")
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
        edit_menu = QMenu(i18n.translate("menu.edit"), self)
        edit_menu.setObjectName("edit_menu")
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
        sequence_menu = QMenu(i18n.translate("menu.timeline"), self)
        sequence_menu.setObjectName("timeline_menu")
        for label in ("Ajouter un clip", "Couper / Réduire", "Marqueur"):
            action = QAction(label, self)
            action.triggered.connect(lambda checked=False, l=label: self._notify_placeholder(l))
            sequence_menu.addAction(action)

        # Fenêtre — le contenu dépend du gestionnaire d'espace de
        # travail, créé plus bas ; on ne garde que la partie fixe ici.
        window_menu = QMenu("Fenêtre", self)
        window_menu.setObjectName("window_menu")
        self.window_menu = window_menu
        reset_action = QAction("Réinitialiser la disposition", self)
        reset_action.triggered.connect(self.reset_workspace_layout)
        window_menu.addAction(reset_action)
        window_menu.addSeparator()
        # Scopes de monitoring couleur (tâche 31). L'action reste
        # checkable pour refléter l'état du splitter sans qu'on ait à
        # le relire à chaque ouverture de menu.
        self.scopes_action = QAction("Afficher les scopes", self)
        self.scopes_action.setCheckable(True)
        self.scopes_action.setChecked(True)
        self.scopes_action.setShortcut("Ctrl+Shift+S")
        self.scopes_action.setShortcutContext(Qt.ApplicationShortcut)
        self.scopes_action.triggered.connect(self.toggle_scopes_visible)
        window_menu.addAction(self.scopes_action)

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
        self.diagnostics_action = QAction(i18n.translate("debug.toggle"), self)
        self.diagnostics_action.setCheckable(True)
        self.diagnostics_action.toggled.connect(self.set_diagnostics_visible)
        window_menu.addAction(self.diagnostics_action)

    def _notify_placeholder(self, feature_name):
        """Affiche un message discret pour les features à venir."""
        print(f"[MainWindow] {feature_name} : à implémenter")

    @staticmethod
    def global_style():
        return global_stylesheet()

    def on_playback_state_changed(self, state):
        """Met à jour l'icône du visualiseur quand il lit un média de bibliothèque.

        L'horloge de la timeline reste l'autorité du transport. Le
        ``QMediaPlayer`` s'arrête aussi dans un trou, au changement de
        source ou à la fin d'un fichier : recopier cet état sur le
        bouton Lecture ferait clignoter Pause alors que la timeline
        continue, et un clic suivant lancerait la timeline au lieu de
        mettre en pause l'aperçu de bibliothèque.
        """
        if self.is_playing:
            return
        self._set_preview_play_icon(state == QMediaPlayer.PlayingState)

    # ------------------------------------------------------------------
    # Scopes vidéo / monitoring couleur (tâche 31)
    # ------------------------------------------------------------------

    def _on_scopes_analysis_ready(self, analysis: ScopeAnalysis) -> None:
        """Reçoit un résultat d'analyse **depuis le thread de travail**.

        On ne fait que programmer la mise à jour du panneau Qt : le
        repaint et le calcul des courbes doivent rester dans le thread
        GUI. Un résultat marqué ``stale`` (la tête de lecture a bougé
        pendant l'analyse) est ignoré pour ne pas faire clignoter les
        scopes avec une image obsolète.
        """
        if getattr(analysis, "stale", False):
            return
        result = analysis.result
        # ``QTimer.singleShot(0, ...)``rebascule dans le thread GUI
        # sans bloquer le thread de travail.
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, lambda: self.scopes_panel.set_result(result))

    def _on_scopes_analysis_failed(
        self, request, error: BaseException,
    ) -> None:
        """Trace une erreur d'analyse sans interrompre la lecture."""
        # Une erreur d'extraction est banale quand aucune image n'est
        # compositionnée (tête de lecture hors media) : on reste
        # silencieux, sinon on sature la console pendant la lecture.
        if isinstance(error, ScopeExtractionError):
            return
        print(f"[MainWindow] analyse de scopes échouée : {error!r}")

    def _request_scopes_analysis(self, force: bool = False) -> None:
        """Déclenche une analyse de l'image composée à la tête de lecture.

        Pendant la lecture, la fréquence est bornée par
        :data:`SCOPES_MIN_INTERVAL` ; en pause (ou avec
        ``force=True``), l'analyse est immédiate pour que les scopes
        réagissent tout de suite à un changement d'exposition, de
        contraste, de courbes ou de LUT.
        """
        if not self._scopes_visible:
            return
        if self.project is None:
            return
        playhead = float(getattr(self, "playhead_seconds", 0.0))
        # On ignore les requêtes quasi identiques : inutile de
        # réanalyser la même image 10 fois par seconde.
        if not force and abs(playhead - self._last_scopes_playhead) < 1e-3:
            return
        self._last_scopes_playhead = playhead
        self._scope_temporary_paths = ()
        command = self._build_scopes_ffmpeg_command(playhead)
        if command is None:
            return
        temporary_paths = self._scope_temporary_paths
        self._scope_temporary_paths = ()
        accepted = self.scopes_analyzer.submit(
            playhead=playhead,
            ffmpeg_command=command,
            color_space=self.scopes_panel._color_space,
            levels=self.scopes_panel.levels(),
            columns=SCOPES_COLUMNS,
            vectorscope_bins=SCOPES_VECTORSCOPE_BINS,
            source="timeline",
            temporary_paths=temporary_paths,
            force=force,
        )
        if accepted is None:
            cleanup_temporary_paths(temporary_paths)

    def _build_scopes_ffmpeg_command(
        self, playhead: float,
    ) -> list[str] | None:
        """Construit la commande ``ffmpeg`` qui rend **une** frame composée.

        On réutilise le graphe de filtres de l'export (effets,
        étalonnage, LUT, courbes) via
        :meth:`core.export_engine.ExportEngine.build_frame_command`, afin
        que les scopes reflètent exactement ce que le moniteur affiche.
        La commande est volontairement limitée à une seule image PNG
        sur stdout : l'analyse doit rester peu coûteuse, y compris
        pendant la lecture.
        """
        try:
            render_plan = self.get_render_plan()
        except Exception:
            # Projet sans média, plan incomplet : rien à analyser.
            return None
        if not getattr(render_plan, "video_layers", ()):
            return None
        frame_engine: ExportEngine | None = None
        try:
            # Le chemin de sortie n'est jamais écrit (la sortie est un
            # PNG sur stdout) mais ``ExportRequest`` en exige un : on
            # pointe donc vers un dossier toujours présent.
            request = self.export_panel.build_request(
                render_plan, os.path.join(tempfile.gettempdir(), "kut-frame.png")
            )
            # Une instance dédiée évite qu'une analyse de scopes ne remplace
            # le SRT temporaire d'un export déjà en cours.
            frame_engine = ExportEngine()
            command = frame_engine.build_frame_command(request, playhead)
            self._scope_temporary_paths = frame_engine.take_temporary_files()
            return command
        except Exception:
            if frame_engine is not None:
                cleanup_temporary_paths(frame_engine.take_temporary_files())
            cleanup_temporary_paths(self._scope_temporary_paths)
            self._scope_temporary_paths = ()
            return None

    def _persist_scopes_preferences(self) -> None:
        """Enregistre la disposition / les niveaux des scopes.

        ``UserSettings`` est immuable : on reconstruit un instantané
        complet via :meth:`_settings_snapshot` plutôt que de muter
        l'instance chargée (ce qui lèverait une ``FrozenInstanceError``).
        """
        try:
            save_user_settings(self._settings_snapshot())
        except OSError:
            # Un échec d'écriture des préférences ne doit pas
            # interrompre l'édition.
            pass

    def toggle_scopes_visible(self) -> None:
        """Affiche / masque le panneau de scopes."""
        self._scopes_visible = not self._scopes_visible
        self.scopes_panel.setVisible(self._scopes_visible)
        # L'action de menu reflète toujours l'état réel du splitter.
        action = getattr(self, "scopes_action", None)
        if action is not None:
            action.blockSignals(True)
            action.setChecked(self._scopes_visible)
            action.blockSignals(False)
        # Le redimensionnement suit la visibilité : replié quand masqué,
        # il reprend sa hauteur quand affiché.
        host = getattr(self, "_viewer_host", None)
        if host is not None:
            if self._scopes_visible:
                host.setSizes([420, 260])
            else:
                host.setSizes([680, 0])
        self._persist_scopes_preferences()
        if self._scopes_visible:
            # On analyse immédiatement : l'utilisateur veut voir les
            # scopes tout de suite.
            self._request_scopes_analysis(force=True)


    def _set_preview_play_icon(self, playing: bool) -> None:
        from ui.design_system import Iconography
        from ui.icons import IconName, make_icon

        if playing:
            self.preview_panel.play_button.setIcon(
                make_icon(IconName.PAUSE, size=Iconography.lg)
            )
            self.preview_panel.play_button.setToolTip("Pause")
        else:
            self.preview_panel.play_button.setIcon(
                make_icon(IconName.PLAY, size=Iconography.lg)
            )
            self.preview_panel.play_button.setToolTip("Lecture")

    def _tick_playback(self) -> None:
        """Avance l'horloge de timeline quand la lecture est active.

        Toute la logique de lecture / pause / seek / stop est désormais
        concentrée ici : ``MainWindow`` possède le playhead, le timer
        Qt l'incrémente, puis on synchronise la timeline et l'aperçu.
        Le timer n'est armé que pendant la lecture. Cet appel reste
        utilisable à l'arrêt : les tests s'en servent pour forcer une
        synchronisation.
        """
        if self.is_playing:
            self.runtime.diagnostics.note_playback_tick(time.perf_counter())
            duration = self._ensure_timeline_index().duration
            # 40 ms = intervalle du timer ; on consomme un delta fixe
            # pour rester stable face aux variations de wall-clock.
            next_playhead = self.playhead_seconds + 0.04
            if duration > 0.0 and next_playhead >= duration:
                self.playhead_seconds = duration
                self._pause_internal()
            else:
                self.playhead_seconds = next_playhead
        self.timeline_panel.set_playhead_seconds(self.playhead_seconds)
        active = self._sync_preview_to_timeline()
        self.update_subtitle_overlay(self.playhead_seconds, active)
        # Scopes (tâche 31) : pendant la lecture, on demande une
        # analyse à chaque tick, mais l'analyseur borne lui‑même la
        # fréquence à ``SCOPES_MIN_INTERVAL`` (10 img/s) : le tick
        # est à 25 Hz, on jette donc ~60 % des requêtes sans rien
        # calculer. En pause, on ne demande rien ici : le refresh
        # immédiat passe par ``_refresh_color_monitor``.
        if self.is_playing:
            self._request_scopes_analysis()

    def _sync_preview_to_timeline(self) -> list:
        """Évalue la timeline à ``playhead_seconds`` et synchronise l'aperçu.

        - S'il existe au moins un clip vidéo actif, on charge la source
          du dernier clip vidéo (la piste la plus basse dans
          ``project.tracks`` est considérée comme visuellement au-dessus).
        - Sinon, on affiche l'état vide via ``PreviewPanel.show_empty``.
        - Les clips non-vidéo (audio, sous-titres) n'influencent pas
          la fenêtre vidéo. La liste retournée sert à l'overlay, pour
          ne pas évaluer la timeline une seconde fois dans le même tick.
        """
        self._refresh_motion_inspector()
        started = time.perf_counter()
        try:
            active_clips = self._ensure_timeline_index().active_at(
                self.project, self.playhead_seconds
            )
        except ValueError:
            return []
        self.runtime.diagnostics.note_preview_sync(
            (time.perf_counter() - started) * 1000.0,
            len(active_clips),
        )
        active_clips = apply_solo(self.project, active_clips)

        # Les segments produits par le moteur fidèle contiennent déjà la
        # composition complète (effets, couleur, graphiques, sous-titres et
        # transforms). En pause, ils deviennent donc la source prioritaire du
        # moniteur dès qu'ils sont disponibles.
        if not self.is_playing and self._present_cached_preview_at(
            float(self.playhead_seconds)
        ):
            return active_clips

        video_clips = [c for c in active_clips if c.track_type == "video"]
        if not video_clips:
            self.preview_panel.show_empty()
            if any(c.track_type == "graphics" for c in active_clips):
                try:
                    self._schedule_preview_around(float(self.playhead_seconds))
                except Exception:
                    pass
            return active_clips
        top_clip = video_clips[-1]
        self.preview_panel.preview_at(top_clip.source_path, top_clip.source_time)
        # Tâche 13 : applique le transform animé du clip supérieur si
        # la timeline contient au moins un clip vidéo. On évalue le
        # ``ClipTransform`` à ``playhead_seconds`` ; on garde l'opacité
        # au sommet pour être conforme à la convention de la tâches 6
        # (clip actif supérieur = superposition).
        clip_obj = self._ensure_timeline_index().clip(top_clip.clip_id)
        if clip_obj is None:
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
            self.preview_panel.set_effects(clip_obj.effects)
        if self.is_playing:
            # Si une source vient d'être chargée ou remplacée, on relance
            # la lecture native pour qu'elle démarre à ``source_time``.
            if self.preview_panel.player.playbackState() != QMediaPlayer.PlayingState:
                self.preview_panel.player.play()
        # Tâche 30 : le média source reste le repli affiché pendant que
        # les segments fidèles (même graphe que l'export) se rendent en
        # arrière-plan sans bloquer l'interface.
        try:
            self._schedule_preview_around(float(self.playhead_seconds))
        except Exception:
            pass
        return active_clips

    def _update_timeline_duration(self) -> None:
        """Met à jour la durée affichée à partir de ``timeline_duration(project)``."""
        self.timeline_panel.set_timeline_duration(timeline_duration(self.project))

    def _ensure_timeline_index(self):
        """Reconstruit l'index seulement quand le projet a été remplacé.

        Les éditions passent par ``TimelinePanel.set_project``, qui
        oublie l'index. Entre deux éditions, la lecture réutilise la
        même structure.
        """
        project_id = id(self.project)
        if (
            self._timeline_index is None
            or self._timeline_index_project_id != project_id
        ):
            self._timeline_index = build_timeline_index(self.project)
            self._timeline_index_project_id = project_id
            self.runtime.note_project_size(
                media_count=len(self.project.media_assets),
                clip_count=self._timeline_index.clip_count,
                track_count=len(self.project.tracks),
            )
        return self._timeline_index

    def _on_timeline_structure_changed(self) -> None:
        self._timeline_index = None
        self._timeline_index_project_id = None
        self._ensure_timeline_index()
        self._apply_runtime_hints()

    def _apply_runtime_hints(self) -> None:
        """Pousse les budgets du profil vers les panneaux."""
        profile = self.runtime.resolved_profile()
        timeline = getattr(self, "timeline_panel", None)
        if timeline is not None:
            timeline.set_culling_overscan(profile.timeline_overscan_px)
        preview = getattr(self, "preview_panel", None)
        if preview is not None:
            preview.set_preview_divisor(self.runtime.preview_divisor())

    def _release_open_project(self) -> None:
        """Coupe la lecture, le décodeur et le travail du projet quitté."""
        recorder = getattr(self, "_audio_recorder", None)
        if recorder is not None and recorder.is_recording:
            recorder.stop()
        timeline = getattr(self, "timeline_panel", None)
        if timeline is not None and hasattr(timeline, "record_button"):
            self._set_record_button(False)
        self.is_playing = False
        timer = getattr(self, "timeline_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()
        if timeline is not None:
            timeline.setPlayState(False)
        preview = getattr(self, "preview_panel", None)
        if preview is not None:
            self._set_preview_play_icon(False)
            preview.release_media()
        self._timeline_index = None
        self._timeline_index_project_id = None
        runtime = getattr(self, "runtime", None)
        if runtime is not None:
            runtime.begin_project()

    def _schedule_autosave(self) -> None:
        timer = getattr(self, "_autosave_timer", None)
        if timer is None or not self.current_project_path or not self.project_dirty:
            return
        timer.start()

    def _write_autosave(self) -> None:
        if not self.project_dirty or not self.current_project_path:
            return
        try:
            self._autosave.submit(self.project, self.current_project_path)
        except (OSError, TypeError, ValueError):
            return

    def set_diagnostics_visible(self, visible: bool) -> None:
        """Affiche ou cache l'overlay de performance."""
        if visible:
            self._debug_timer.start()
            self._refresh_debug_overlay()
        else:
            self._debug_timer.stop()
            self._debug_overlay.hide()

    def _refresh_debug_overlay(self) -> None:
        profile = self.runtime.resolved_profile()
        cache = self.runtime.cache.stats()
        fps = self.runtime.diagnostics.viewer_fps()
        rss = peak_rss_bytes()
        output = self.runtime.output_size(self.project.width, self.project.height)
        lines = [
            f"Profil {profile.name} ({self.runtime.requested_profile})"
            f" · aperçu {self.runtime.preview_label()}",
            (
                f"Clips {self.timeline_panel.mounted_clip_count} montés"
                f" / {len(self.timeline_panel.clip_views)}"
            ),
            (
                f"Cache {cache.entries} · {cache.bytes / (1024 * 1024):.1f} Mo"
                f" / {cache.budget_bytes / (1024 * 1024):.0f} Mo"
            ),
            f"Tâches {self.runtime.tasks.pending} · session {self.runtime.session_id}",
            (
                f"Sync {self.runtime.diagnostics.preview_sync_ms:.2f} ms"
                f" · actifs {self.runtime.diagnostics.active_clips}"
            ),
            f"Lecture {fps:.1f} ticks/s" if fps is not None else "Lecture en pause",
            f"Cible aperçu {output[0]}×{output[1]}",
        ]
        if rss is not None:
            lines.append(f"Mémoire max {rss / (1024 * 1024):.0f} Mo")
        self._debug_overlay.present(lines)

    def _settings_snapshot(self) -> UserSettings:
        # L'état des scopes est lu depuis le panneau : c'est lui qui
        # fait foi, comme le thème ou la qualité d'aperçu pour le
        # reste. Les attributs sont lus de façon défensive car
        # l'instantané est aussi pris avant la construction du
        # panneau (au démarrage de la fenêtre).
        scopes_panel = getattr(self, "scopes_panel", None)
        if scopes_panel is None:
            scopes_layout = "quad"
            scopes_view = "waveform"
            scopes_levels = "video"
            scopes_alerts = False
        else:
            scopes_layout = scopes_panel.layout_mode().value
            scopes_view = scopes_panel.single_view().value
            scopes_levels = scopes_panel.levels().value
            scopes_alerts = scopes_panel.alerts_enabled()
        return UserSettings(
            theme_mode=self.theme_manager.requested_mode,
            language=i18n.current_language(),
            performance_profile=self.runtime.requested_profile,
            preview_quality=self.runtime.requested_quality,
            render_quality=self._render_quality,
            master_gain_db=self._master_gain_db,
            master_muted=self._master_muted,
            scopes_visible=bool(getattr(self, "_scopes_visible", False)),
            scopes_layout=scopes_layout,
            scopes_view=scopes_view,
            scopes_levels=scopes_levels,
            scopes_alerts_enabled=scopes_alerts,
        )

    def _pause_internal(self) -> None:
        """Met la lecture en pause sans toucher au playhead."""
        self.is_playing = False
        if self.timeline_timer.isActive():
            self.timeline_timer.stop()
        self.preview_panel.player.pause()
        self.timeline_panel.setPlayState(False)
        self._set_preview_play_icon(False)

    def on_clip_selected(self, clip_id):
        self._finalize_pending_edit_sessions()
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
        # Réglages audio : visibles uniquement pour un clip sur piste audio.
        self._sync_audio_inspector()
        # La bibliothèque d'effets doit suivre la sélection courante.
        self._sync_effects_library_context()
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
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_trim_left_requested(self, clip_id: str, new_timeline_start: float) -> None:
        try:
            if self.timeline_panel.ripple_enabled:
                ripple_trim_left(self.project, clip_id, new_timeline_start)
            else:
                trim_clip_left(self.project, clip_id, new_timeline_start)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] trim gauche refusé : {exc}")
            return
        self._record_history("Trim gauche")
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_trim_right_requested(self, clip_id: str, new_timeline_end: float) -> None:
        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            clip = None
        old_end = None if clip is None else clip.timeline_start + clip.duration
        track_id = None if clip is None else clip.track_id
        try:
            trim_clip_right(self.project, clip_id, new_timeline_end)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] trim droit refusé : {exc}")
            return
        if self.timeline_panel.ripple_enabled and old_end is not None and track_id:
            shift_track_after(
                self.project,
                track_id,
                old_end,
                new_timeline_end - old_end,
                exclude_ids={clip_id},
            )
        self._record_history("Trim droit")
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def cut_at_playhead(self):
        clip_id = self.timeline_panel.selected_clip_id
        if clip_id is None:
            print("[MainWindow] Cut : aucun clip sélectionné")
            return
        self.cut_selected_clip(clip_id, self.timeline_panel.playhead_seconds)

    # ------------------------------------------------------------------
    # Organisation avancée de la bibliothèque (tâche 25)
    # ------------------------------------------------------------------

    def _on_folder_create_requested(
        self,
        name: str,
        parent_id: object,
        color: str,
    ) -> None:
        """Crée un dossier (à la racine ou sous un parent)."""
        from core.library_organization import (
            LibraryError,
            LibraryNameError,
            LibraryOrganization,
        )

        org = LibraryOrganization(self.project)
        try:
            parent_id_str = (
                str(parent_id) if parent_id not in (None, "") else None
            )
            folder = org.create_folder(
                name,
                parent_id=parent_id_str,
                color=color or "",
            )
        except (LibraryError, LibraryNameError) as exc:
            QMessageBox.warning(self, "Dossier", str(exc))
            return
        self._record_history(
            f"Créer le dossier « {folder.name} »"
        )
        self._refresh_project_library()
        self._mark_dirty()

    def _on_folder_rename_requested(
        self,
        folder_id: str,
        new_name: str,
    ) -> None:
        """Renomme un dossier (déclenché via menu contextuel)."""
        from core.library_organization import (
            LibraryError,
            LibraryNameError,
            LibraryOrganization,
        )

        org = LibraryOrganization(self.project)
        try:
            folder = org.rename_folder(folder_id, new_name)
        except (LibraryError, LibraryNameError) as exc:
            QMessageBox.warning(self, "Dossier", str(exc))
            return
        self._record_history(
            f"Renommer le dossier en « {folder.name} »"
        )
        self._refresh_project_library()
        self._mark_dirty()

    def _on_folder_delete_requested(self, folder_id: str) -> None:
        """Supprime un dossier et rattache ses médias à la racine."""
        from core.library_organization import (
            LibraryError,
            LibraryOrganization,
        )

        org = LibraryOrganization(self.project)
        folder = org.get_folder(folder_id)
        if folder is None:
            return
        confirm = QMessageBox.question(
            self,
            "Supprimer le dossier",
            f"Supprimer le dossier « {folder.name} » et ramener ses "
            "médias à la racine ?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            org.delete_folder(folder_id, cascade=True)
        except LibraryError as exc:
            QMessageBox.warning(self, "Dossier", str(exc))
            return
        self._record_history(
            f"Supprimer le dossier « {folder.name} »"
        )
        self._refresh_project_library()
        self._mark_dirty()

    def _on_tag_manager_requested(self) -> None:
        """Ouvre le dialogue de gestion des tags."""
        from core.library_organization import LibraryOrganization
        from ui.library_organization_widgets import TagManagerDialog

        org = LibraryOrganization(self.project)
        dialog = TagManagerDialog(org, parent=self)
        result = dialog.exec()
        if result == QDialog.Accepted:
            # Le dialogue n'utilise pas Accept ; tout est appliqué en
            # place. On enregistre l'historique dès qu'il y a au moins
            # une modification.
            self._record_history("Modifier les tags de la bibliothèque")
            self._refresh_project_library()
            self._mark_dirty()
        # Même sur rejet, le dialogue peut avoir été modifié : on
        # rafraîchit pour rester cohérent avec le projet.
        self._refresh_project_library()

    def _on_asset_move_to_folder(
        self,
        asset_id: str,
        folder_id: object,
    ) -> None:
        """Range un média dans un dossier (ou remet à la racine)."""
        from core.library_organization import (
            LibraryError,
            LibraryOrganization,
        )

        org = LibraryOrganization(self.project)
        folder_id_str = str(folder_id) if folder_id not in (None, "") else None
        try:
            org.move_asset(asset_id, folder_id_str)
        except LibraryError as exc:
            QMessageBox.warning(self, "Bibliothèque", str(exc))
            return
        # On n'enregistre l'historique que si le déplacement est
        # effectif (le service est idempotent).
        self._record_history(
            "Déplacer le média dans un dossier"
        )
        self._refresh_project_library()
        self._mark_dirty()

    def _on_asset_tag_toggled(
        self,
        asset_id: str,
        tag_id: str,
        assign: bool,
    ) -> None:
        """Ajoute ou retire un tag sur un média."""
        from core.library_organization import (
            LibraryError,
            LibraryOrganization,
        )

        org = LibraryOrganization(self.project)
        try:
            if assign:
                org.add_tag_to_asset(asset_id, tag_id)
                self._record_history("Tagger un média")
            else:
                org.remove_tag_from_asset(asset_id, tag_id)
                self._record_history("Retirer un tag d'un média")
        except LibraryError as exc:
            QMessageBox.warning(self, "Bibliothèque", str(exc))
            return
        self._refresh_project_library()
        self._mark_dirty()

    def _on_asset_relink_requested(self, asset_id: str) -> None:
        """Ouvre un dialogue de sélection de fichier pour relier."""
        from core.library_organization import LibraryError

        asset = next(
            (a for a in self.project.media_assets if a.id == asset_id),
            None,
        )
        if asset is None:
            return
        start_path = (
            os.path.dirname(asset.path) if asset.path
            else os.path.expanduser("~")
        )
        if not os.path.isdir(start_path):
            start_path = os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Relier le média…",
            start_path,
            "Médias (*.mp4 *.mov *.avi *.mkv *.webm *.mp3 *.wav *.m4a *.aac *.flac *.ogg *.png *.jpg *.jpeg)",
        )
        if not path:
            return
        from core.library_organization import LibraryOrganization
        org = LibraryOrganization(self.project)
        try:
            org.relink_asset(asset_id, path)
        except LibraryError as exc:
            QMessageBox.warning(self, "Bibliothèque", str(exc))
            return
        self._record_history("Relier le fichier d'un média")
        self._refresh_project_library()
        self._mark_dirty()

    def _on_asset_rename_requested(
        self,
        asset_id: str,
        new_name: str,
    ) -> None:
        """Renomme un média (déclenché via menu contextuel)."""
        from core.library_organization import (
            LibraryError,
            LibraryNameError,
            LibraryOrganization,
        )

        org = LibraryOrganization(self.project)
        try:
            org.rename_asset(asset_id, new_name)
        except (LibraryError, LibraryNameError) as exc:
            QMessageBox.warning(self, "Bibliothèque", str(exc))
            return
        self._record_history("Renommer un média")
        self._refresh_project_library()
        self._mark_dirty()

    def _on_asset_remove_requested(self, asset_id: str) -> None:
        """Supprime un média du projet (les clips restent par défaut)."""
        from core.library_organization import (
            LibraryError,
            LibraryOrganization,
        )

        org = LibraryOrganization(self.project)
        asset = next(
            (a for a in self.project.media_assets if a.id == asset_id),
            None,
        )
        if asset is None:
            return
        confirm = QMessageBox.question(
            self,
            "Supprimer le média",
            f"Supprimer « {asset.name} » de la bibliothèque ?\n"
            "Les clips qui l'utilisent resteront sur la timeline "
            "(ils pointeront vers un média absent).",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            org.remove_asset(asset_id, keep_orphan_clips=True)
        except LibraryError as exc:
            QMessageBox.warning(self, "Bibliothèque", str(exc))
            return
        self._record_history("Supprimer un média")
        self._refresh_project_library()
        self._mark_dirty()

    def _on_asset_occurrences_requested(self, asset_id: str) -> None:
        """Sélectionne la première occurrence du média dans la timeline."""
        from core.library_organization import compute_usage
        usage = compute_usage(self.project, asset_id)
        if not usage.clip_ids:
            return
        clip_id = usage.clip_ids[0]
        # On cherche la piste du clip.
        for track in self.project.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    self.playhead_seconds = clip.timeline_start
                    self.timeline_panel.set_playhead_seconds(self.playhead_seconds)
                    self.timeline_panel.select_clip(clip_id)
                    self._sync_preview_to_timeline()
                    return

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
            asset = cached_probe(self.runtime.cache, path, probe_media)
        except MediaProbeError as exc:
            QMessageBox.critical(
                self,
                "Import impossible",
                f"Impossible d'importer le média :\n\n{path}\n\n{exc}",
            )
            return False

        # La sonde réelle fournit déjà le nom de fichier, mais les
        # intégrations externes peuvent retourner un ``MediaAsset`` avec
        # le chemin complet comme libellé. Le nom affiché ne dépend jamais
        # du séparateur POSIX : sous Windows, ``split('/')`` laisserait le
        # chemin complet dans la bibliothèque et dans les .kut.
        display_name = os.path.basename(str(asset.path).replace("\\", "/"))
        if not display_name:
            display_name = os.path.basename(str(path).replace("\\", "/"))
        if display_name:
            asset.name = display_name
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
        Texte avec les clips activés des pistes ``subtitle``, l'organisation
        de la bibliothèque (dossiers / tags / affectations) et les badges
        d'utilisation des médias (tâche 25).
        """
        # Les médias techniques des calques G vivent dans le projet pour
        # garantir l'intégrité des clips, mais leurs modèles se choisissent
        # dans la bibliothèque Graphiques : ne pas les dupliquer dans Médias.
        self.project_panel.set_assets(
            [
                asset for asset in self.project.media_assets
                if asset.media_type != "graphic"
            ]
        )
        subtitle_clips = [
            clip
            for track in self.project.tracks
            if track.type == "subtitle"
            for clip in track.clips
            if clip.enabled and (clip.text or "").strip()
        ]
        self.project_panel.set_subtitle_clips(subtitle_clips)
        # Organisation de la bibliothèque (tâche 25) : on attache le
        # service au panneau et on rafraîchit les badges d'usage.
        if not hasattr(self, "_library_organization") or (
            self._library_organization is not None
            and self._library_organization.project is not self.project
        ):
            from core.library_organization import LibraryOrganization
            self._library_organization = LibraryOrganization(self.project)
        else:
            from core.library_organization import LibraryOrganization
            self._library_organization = LibraryOrganization(self.project)
        self.project_panel.set_library(self._library_organization)
        self.project_panel.set_usage_for_assets()
        # La bibliothèque d'effets suit la sélection courante.
        self._sync_effects_library_context()

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
            self._restore_clip_selection(new_id)
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

    def update_subtitle_from_editor(self, new_text: str | None = None):
        """Applique le texte saisi dans l'éditeur de l'inspecteur.

        Delegue à :meth:`on_subtitle_content_changed` qui porte la
        logique d'historique (debounce) — ce handler reste pour la
        compatibilité avec les appels historiques.
        """
        if self.active_subtitle_clip is None:
            return
        if new_text is None:
            new_text = self.properties_panel.subtitle_editor.content()
        self.on_subtitle_content_changed(self.active_subtitle_clip.id, new_text)


    def update_subtitle_overlay(self, seconds, active_clips=None):
        """Affiche le sous-titre actif (borne demi-ouverte ``start <= t < end``).

        Plusieurs sous-titres superposés sont départagés par leur
        ordre dans la timeline : le dernier gagne (comportement
        déterministe). ``active_clips`` évite une seconde évaluation
        quand l'appelant vient déjà de synchroniser l'aperçu.
        """
        if active_clips is None:
            try:
                active_clips = self._ensure_timeline_index().active_at(
                    self.project, seconds
                )
            except (ValueError, KeyError):
                active_clips = []
        active_clips = apply_solo(self.project, active_clips)
        subtitle_clips = [c for c in active_clips if c.track_type == "subtitle"]
        if not subtitle_clips:
            self.preview_panel.clear_subtitle()
            return
        view = subtitle_clips[-1]
        text = view.text.strip() if view.text else ""
        if not text:
            self.preview_panel.clear_subtitle()
            return
        # La vue de timeline porte le style du clip : on l'applique tel
        # quel pour que le preview reflète l'inspecteur sans relance.
        style = getattr(view, "text_style", None) or default_text_style()
        self.preview_panel.set_subtitle(text, style)

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
        try:
            for cue in cues:
                clip = add_subtitle_clip(
                    self.project,
                    text=cue.text,
                    timeline_start=cue.start,
                    duration=cue.end - cue.start,
                )
                created_clips.append(clip)
        except (KeyError, ValueError):
            if created_clips:
                self._record_history(
                    f"Importer le SRT ({len(created_clips)} sous-titres)"
                )
                self._reload_timeline_preserving_selection()
                self._update_timeline_duration()
                self._refresh_project_library()
                self._mark_dirty()
            raise
        self._record_history(f"Importer le SRT ({len(cues)} sous-titres)")
        self._reload_timeline_preserving_selection()
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

    # ------------------------------------------------------------------
    # Tâche 32 : calques graphiques
    # ------------------------------------------------------------------

    def add_graphic_at_playhead(self, graphic_type: str) -> None:
        """Ajoute un calque généré sur G1 et ouvre son inspecteur."""
        from core.graphics import add_graphic_clip

        try:
            clip = add_graphic_clip(
                self.project,
                graphic_type,
                timeline_start=self.playhead_seconds,
                duration=5.0,
            )
        except (FileNotFoundError, ValueError) as exc:
            QMessageBox.warning(self, "Graphique", str(exc))
            return
        self._record_history("Ajouter un calque graphique")
        self._reload_timeline_preserving_selection(clip.id)
        self._update_timeline_duration()
        self._refresh_project_library()
        self.timeline_panel.select_clip(clip.id)
        self.on_clip_selected(clip.id)
        self.properties_panel._select_inspector_tab(4)
        self._invalidate_preview_for_clip(clip.id)
        self._mark_dirty()

    def import_graphic_image(self) -> None:
        """Importe une image comme calque graphique animable."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importer une image graphique",
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)",
        )
        if not path:
            return
        from core.graphics import add_graphic_clip

        try:
            clip = add_graphic_clip(
                self.project,
                "image",
                timeline_start=self.playhead_seconds,
                duration=5.0,
                source_path=path,
            )
            # Conserve le ratio et la taille intrinsèque de l'image, sans
            # dépasser le cadre du projet. Le modèle reste indépendant de Qt ;
            # cette lecture de métadonnées appartient donc à la couche UI.
            from PySide6.QtGui import QImageReader

            image_size = QImageReader(path).size()
            if image_size.isValid():
                source_width = max(1, image_size.width())
                source_height = max(1, image_size.height())
                ratio = min(
                    1.0,
                    self.project.width / source_width,
                    self.project.height / source_height,
                )
                from core.graphics import update_graphic

                update_graphic(clip, "width", round(source_width * ratio))
                update_graphic(clip, "height", round(source_height * ratio))
                asset = next(
                    item for item in self.project.media_assets
                    if item.id == clip.asset_id
                )
                asset.width = clip.graphic.width
                asset.height = clip.graphic.height
        except (FileNotFoundError, ValueError) as exc:
            QMessageBox.warning(self, "Graphique", str(exc))
            return
        self._record_history("Importer une image graphique")
        self._reload_timeline_preserving_selection(clip.id)
        self._update_timeline_duration()
        self._refresh_project_library()
        self.timeline_panel.select_clip(clip.id)
        self.on_clip_selected(clip.id)
        self.properties_panel._select_inspector_tab(4)
        self._invalidate_preview_for_clip(clip.id)
        self._mark_dirty()

    def on_graphic_property_changed(
        self, clip_id: str, field_name: str, value: object
    ) -> None:
        """Applique une propriété intrinsèque du calque avec Undo/Redo."""
        from core.graphics import update_graphic

        if not bool(getattr(self, "_graphic_session_active", False)):
            self._finalize_transform_session()
        clip, track = self._find_clip_and_track(clip_id)
        if (
            clip is None
            or track is None
            or track.type != "graphics"
            or bool(track.locked)
        ):
            return
        try:
            before = clip.graphic
            updated = update_graphic(clip, field_name, value)
        except (TypeError, ValueError) as exc:
            self.statusBar().showMessage(f"Modification graphique refusée : {exc}", 5000)
            return
        if updated is before:
            return
        self._schedule_graphic_history(clip_id)
        self._invalidate_preview_for_clip(clip_id)
        self._schedule_graphic_preview_refresh()

    def _schedule_graphic_history(self, clip_id: str) -> None:
        """Regroupe une saisie ou une rafale de spinbox en un seul Undo."""
        active = bool(getattr(self, "_graphic_session_active", False))
        previous_clip = getattr(self, "_graphic_session_clip_id", None)
        if active and previous_clip != clip_id:
            self._finalize_graphic_history()
            active = False
        self._graphic_session_active = True
        self._graphic_session_clip_id = clip_id
        if not active:
            self._graphic_session_label = "Modifier un calque graphique"
        timer = getattr(self, "_graphic_session_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._finalize_graphic_history)
            self._graphic_session_timer = timer
        timer.start(400)
        self._mark_dirty()

    def _finalize_graphic_history(self) -> None:
        if not bool(getattr(self, "_graphic_session_active", False)):
            return
        timer = getattr(self, "_graphic_session_timer", None)
        if timer is not None:
            timer.stop()
        self._graphic_session_active = False
        clip_id = getattr(self, "_graphic_session_clip_id", None)
        label = getattr(
            self, "_graphic_session_label", "Modifier un calque graphique"
        )
        self._record_history(label)
        if clip_id:
            self._reload_timeline_preserving_selection(clip_id)

    def _schedule_graphic_preview_refresh(self) -> None:
        """Évite de relancer FFmpeg à chaque caractère saisi."""
        timer = getattr(self, "_graphic_preview_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._sync_preview_to_timeline)
            self._graphic_preview_timer = timer
        timer.start(120)

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
        except (OSError, ValueError, KeyError) as exc:
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
        # Synchronise l'overlay du preview avec le style du clip.
        self._refresh_subtitle_overlay()

    def update_color_effect(self, *_):
        panel = self.properties_panel
        grade = None
        selected = getattr(panel, "selected_clip", None)
        clip_id = getattr(selected, "id", None)
        if clip_id:
            clip, _track = self._find_clip_and_track(clip_id)
            grade = getattr(clip, "color_grade", None) if clip is not None else None
        if grade is None:
            grade = getattr(panel, "_current_color_grade", ColorGrade.identity())
        color_effect = getattr(self.preview_panel, "color_effect", None)
        if color_effect is None:
            return
        apply_color_effect(
            color_effect,
            int(max(-100, min(100, grade.exposure * 50.0))),
            int(max(-100, min(100, grade.contrast * 100.0))),
            int(max(-100, min(100, (grade.saturation - 1.0) * 100.0))),
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

    def add_transition_from_library(self, preset_id: str, duration: float) -> None:
        """Pose un preset de transition entre les deux clips sélectionnés.

        Conservé comme façade de compatibilité : la nouvelle
        bibliothèque publie ``transition_apply_requested(preset_id, duration)``
        qui aboutit ici. On garde aussi l'ancien nom ``add_transition_from_library``
        pour ne pas casser d'éventuels appels externes (tests).
        """
        self.on_transition_preset_apply_requested(preset_id, duration)

    # ------------------------------------------------------------------
    # Tâche 23 : gestion des presets de transitions
    # ------------------------------------------------------------------

    def _on_transition_presets_changed(self) -> None:
        """Répercute les mutations du store vers la bibliothèque."""
        self.project_panel.set_transition_presets(
            self.transition_preset_store.all_presets(),
            favorites=self.transition_preset_store.favorites(),
        )

    def on_transition_preset_apply_requested(
        self, preset_id: str, duration: float
    ) -> None:
        """Applique un preset (intégré ou utilisateur) entre les deux clips sélectionnés."""
        preset = self.transition_preset_store.get_preset(preset_id)
        if preset is None:
            self.statusBar().showMessage(
                i18n.translate("transitions.library.no_results"), 5000
            )
            return
        selected = [
            view for view in self.timeline_panel.clip_views
            if view.id in self.timeline_panel.selected_clip_ids
            and view.track_type == "video"
        ]
        if len(selected) != 2:
            self.statusBar().showMessage(
                i18n.translate("transitions.library.two_clips_required"),
                5000,
            )
            return
        selected.sort(key=lambda view: view.start)
        if selected[0].track_id != selected[1].track_id:
            self.statusBar().showMessage(
                "Les clips doivent être placés sur la même piste.", 5000
            )
            return
        from core.transitions import add_transition
        try:
            transition = add_transition(
                self.project,
                selected[0].id,
                selected[1].id,
                preset.transition_type,
                float(duration),
            )
        except (KeyError, ValueError) as error:
            self.statusBar().showMessage(
                f"Transition refusée : {error}", 6000
            )
            return
        self._record_history("Ajouter une transition")
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._mark_dirty()
        self.timeline_panel.select_transition(transition.id)
        self.statusBar().showMessage(
            f"Transition « {preset.name} » ajoutée.", 3000
        )

    def on_transition_preset_save_requested(self) -> None:
        """Ouvre le dialogue d'enregistrement d'un preset utilisateur."""
        selected = [
            view for view in self.timeline_panel.clip_views
            if view.id in self.timeline_panel.selected_clip_ids
            and view.track_type == "video"
        ]
        if len(selected) != 2:
            self.statusBar().showMessage(
                i18n.translate("transitions.library.two_clips_required"),
                5000,
            )
            return
        selected.sort(key=lambda view: view.start)
        if selected[0].track_id != selected[1].track_id:
            self.statusBar().showMessage(
                "Les clips doivent être placés sur la même piste.", 5000
            )
            return
        from core.timeline_operations import find_clip

        # Si une transition existe déjà entre ces deux clips, on en
        # capture le type et la durée pour pré-remplir le dialogue.
        existing_type = "crossfade"
        existing_duration = 0.5
        for transition in self.project.transitions:
            if (
                transition.from_clip_id == selected[0].id
                and transition.to_clip_id == selected[1].id
            ):
                existing_type = transition.type.value
                existing_duration = float(transition.duration)
                break
        try:
            from_clip = find_clip(self.project, selected[0].id)
            to_clip = find_clip(self.project, selected[1].id)
        except (KeyError, ValueError) as exc:
            self.statusBar().showMessage(str(exc), 5000)
            return
        default_name = (
            f"{from_clip.label or 'Plan'} → {to_clip.label or 'Plan'}"
        )
        dialog = SaveTransitionPresetDialog(
            self,
            default_name=default_name,
            default_type=existing_type,
            default_duration=existing_duration,
        )
        if dialog.exec() != dialog.Accepted:
            return
        name, description, transition_type, duration = dialog.result_data()
        if not name:
            return
        try:
            preset = make_user_transition_preset(
                name=name,
                description=description,
                transition_type=transition_type,
                default_duration=duration,
            )
        except ValueError as exc:
            self.statusBar().showMessage(str(exc), 5000)
            return
        try:
            self.transition_preset_store.add_user_preset(preset)
        except ValueError as exc:
            self.statusBar().showMessage(str(exc), 5000)
            return
        self._record_history("Enregistrer une transition personnalisée")

    def on_transition_preset_delete_requested(self, preset_id: str) -> None:
        """Supprime un preset utilisateur après confirmation."""
        preset = self.transition_preset_store.get_preset(preset_id)
        if preset is None:
            return
        if preset.builtin:
            QMessageBox.information(
                self,
                i18n.translate("transitions.library.delete"),
                i18n.translate("transitions.library.user_builtin_lock"),
            )
            return
        confirm = QMessageBox.question(
            self,
            i18n.translate("transitions.library.delete"),
            i18n.translate(
                "transitions.library.delete_confirm"
            ).format(name=preset.name),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            self.transition_preset_store.remove_user_preset(preset_id)
        except KeyError as exc:
            self.statusBar().showMessage(str(exc), 5000)
            return
        self._record_history("Supprimer une transition personnalisée")

    def on_transition_favorite_toggled(self, preset_id: str) -> None:
        """Bascule l'état favori d'un preset (intégré ou utilisateur)."""
        try:
            self.transition_preset_store.toggle_favorite(preset_id)
        except KeyError as exc:
            self.statusBar().showMessage(str(exc), 5000)
            return
        self.statusBar().showMessage("Transition ajoutée.", 3000)

    # ------------------------------------------------------------------
    # Tâche 24 : gestion des modèles de texte + édition du style
    # ------------------------------------------------------------------

    def _on_text_presets_changed(self) -> None:
        """Répercute les mutations du store vers la bibliothèque."""
        self.project_panel.subtitle_view.set_presets(
            self.text_preset_store.all_presets()
        )

    def _resolve_text_preset(self, preset_id: str) -> TextPreset | None:
        """Cherche un modèle dans la bibliothèque complète."""
        for preset in builtin_text_presets():
            if preset.id == preset_id:
                return preset
        return self.text_preset_store.get(preset_id)

    def _apply_text_preset_to_clip(
        self, clip_id: str, preset: TextPreset
    ) -> bool:
        """Applique ``preset.style`` (et son texte par défaut si vide) au clip."""
        from core.timeline_operations import find_clip

        try:
            clip = find_clip(self.project, clip_id)
        except KeyError as exc:
            self.statusBar().showMessage(str(exc), 5000)
            return False
        clip.text_style = preset.style
        if not (clip.text or "").strip() and preset.default_text:
            clip.text = preset.default_text
        self._record_history("Appliquer un modèle de sous-titre")
        self._reload_timeline_preserving_selection(clip_id)
        self._refresh_subtitle_overlay()
        return True

    def _restore_clip_style(self, clip_id: str, style: TextStyle) -> None:
        """Restaure le style ``style`` sur ``clip_id`` (Undo / Redo)."""
        from core.timeline_operations import find_clip

        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return
        clip.text_style = style
        self._reload_timeline_preserving_selection(clip_id)
        self._refresh_subtitle_overlay()

    def on_text_preset_apply_requested(self, preset_id: str) -> None:
        """Applique un modèle au clip sélectionné (sélection timeline)."""
        preset = self._resolve_text_preset(preset_id)
        if preset is None:
            return
        # Cible = clip sélectionné, ou clip actif si le sous-titre
        # actif est connu.
        target_clip_id = self._resolve_subtitle_target_clip_id()
        if target_clip_id is None:
            self.statusBar().showMessage(
                i18n.translate("effects.library.apply_hint"), 5000
            )
            return
        self._apply_text_preset_to_clip(target_clip_id, preset)

    def on_text_preset_new_clip_requested(self, preset_id: str) -> None:
        """Crée un nouveau clip de sous-titre au playhead avec le modèle."""
        preset = self._resolve_text_preset(preset_id)
        if preset is None:
            return
        text = preset.default_text or preset.name
        self.add_subtitle_at_playhead(text, 3.0)
        # Si un clip vient d'être créé, on lui applique le style du modèle.
        from core.timeline_operations import find_clip

        subtitle_track = next(
            (t for t in self.project.tracks if t.type == "subtitle"), None
        )
        if subtitle_track is None:
            return
        for clip in reversed(subtitle_track.clips):
            if (clip.text or "").strip() == text:
                self._apply_text_preset_to_clip(clip.id, preset)
                break

    def on_text_preset_save_requested(
        self,
        name: str,
        description: str,
        style: TextStyle,
        default_text: str,
    ) -> None:
        """Ouvre un dialogue d'enregistrement d'un modèle utilisateur."""
        from PySide6.QtWidgets import QInputDialog

        new_name, accepted = QInputDialog.getText(
            self,
            i18n.translate("text.library.save_dialog.title"),
            i18n.translate("text.library.save_dialog.name"),
            text=name,
        )
        if not accepted or not new_name.strip():
            return
        try:
            preset = make_user_text_preset(
                name=new_name,
                description=description,
                style=style,
                default_text=default_text,
            )
        except ValueError as exc:
            self.statusBar().showMessage(str(exc), 5000)
            return
        try:
            self.text_preset_store.add(preset)
        except ValueError as exc:
            self.statusBar().showMessage(str(exc), 5000)
            return
        self.statusBar().showMessage(
            f"Modèle « {preset.name} » enregistré.", 3000
        )

    def on_text_preset_delete_requested(self, preset_id: str) -> None:
        """Supprime un modèle utilisateur après confirmation."""
        from PySide6.QtWidgets import QMessageBox

        preset = self.text_preset_store.get(preset_id)
        if preset is None:
            return
        if preset.builtin:
            QMessageBox.information(
                self,
                i18n.translate("text.library.delete"),
                i18n.translate("text.library.user_builtin_lock"),
            )
            return
        confirm = QMessageBox.question(
            self,
            i18n.translate("text.library.delete"),
            i18n.translate("text.library.delete_confirm").format(
                name=preset.name
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            self.text_preset_store.remove(preset_id)
        except KeyError as exc:
            self.statusBar().showMessage(str(exc), 5000)

    # ----- Édition du style via l'inspecteur --------------------------

    def on_subtitle_content_changed(self, clip_id: str, content: str) -> None:
        """Capture le contenu du sous-titre dans l'historique.

        Le regroupement des frappes est assuré par le debounce existant
        (``_schedule_subtitle_history_record``) : une session de saisie
        ne produit qu'une entrée d'historique.
        """
        from core.timeline_operations import find_clip

        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return
        if clip.text == content:
            return
        clip.text = content
        self._schedule_subtitle_history_record(clip_id)
        self._mark_dirty()
        # ``set_project`` efface la sélection. On la repose sans
        # ``show_clip`` : recharger l'inspecteur pendant la frappe
        # remettrait le curseur de l'éditeur au début.
        playhead = self.playhead_seconds
        self.timeline_panel.set_project(self.project)
        if self.timeline_panel.find_view_by_id(clip_id) is not None:
            self.timeline_panel._set_selection([clip_id], clip_id, announce=False)
            view = self.timeline_panel.find_view_by_id(clip_id)
            if getattr(view, "track_type", None) == "subtitle":
                self.active_subtitle_clip = view
        if self.playhead_seconds != playhead:
            self.playhead_seconds = playhead
        self.update_subtitle_overlay(self.playhead_seconds)

    def on_subtitle_style_changed(self, clip_id: str, style: TextStyle) -> None:
        """Applique immédiatement le style (Undo/Redo friendly)."""
        from core.timeline_operations import find_clip

        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return
        if clip.text_style == style:
            return
        clip.text_style = style
        self._record_history("Modifier le style du sous-titre")
        self._reload_timeline_preserving_selection(clip_id)
        self._refresh_subtitle_overlay()

    def on_subtitle_style_reset(self, clip_id: str) -> None:
        """Réinitialise le style au standard (Undo/Redo friendly)."""
        from core.timeline_operations import find_clip

        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return
        if clip.text_style == default_text_style():
            return
        clip.text_style = default_text_style()
        self._record_history("Réinitialiser le style du sous-titre")
        self._reload_timeline_preserving_selection(clip_id)
        self._refresh_subtitle_overlay()
        self.statusBar().showMessage(
            i18n.translate("text.library.reset_style"), 3000
        )

    def _refresh_subtitle_overlay(self) -> None:
        """Synchronise l'overlay preview avec le sous-titre courant."""
        preview = getattr(self, "preview_panel", None)
        if preview is None:
            return
        clip = self.active_subtitle_clip
        if clip is None:
            preview.clear_subtitle()
            return
        from core.timeline_operations import find_clip

        try:
            model_clip = find_clip(self.project, clip.id)
        except KeyError:
            preview.clear_subtitle()
            return
        text = (model_clip.text or "").strip()
        if not text:
            preview.clear_subtitle()
            return
        preview.set_subtitle(text, model_clip.text_style)

    def _resolve_subtitle_target_clip_id(self) -> str | None:
        """Identifiant du clip cible pour appliquer un modèle."""
        if self.active_subtitle_clip is not None:
            return self.active_subtitle_clip.id
        # Sinon, premier clip de sous-titre visible.
        for track in self.project.tracks:
            if track.type != "subtitle" or not track.visible:
                continue
            for clip in track.clips:
                if clip.enabled:
                    return clip.id
        return None

    def on_transition_selected(self, transition_id: str) -> None:
        """Affiche les réglages de la transition choisie sur la timeline."""
        transition = next(
            (item for item in self.project.transitions if item.id == transition_id), None
        )
        if transition is None:
            self.properties_panel.clear_transition()
            return
        outgoing = self.timeline_panel.find_view_by_id(transition.from_clip_id)
        incoming = self.timeline_panel.find_view_by_id(transition.to_clip_id)
        if outgoing is None or incoming is None:
            self.properties_panel.clear_transition()
            return
        track = next((item for item in self.project.tracks if item.id == outgoing.track_id), None)
        self.properties_panel.show_transition(
            transition, outgoing, incoming, track.name if track is not None else outgoing.track_id
        )

    def on_transition_type_changed(self, transition_id: str, transition_type: str) -> None:
        self._update_transition(transition_id, transition_type=transition_type)

    def on_transition_duration_changed(self, transition_id: str, duration: float) -> None:
        self._update_transition(transition_id, duration=duration)

    def _update_transition(
        self, transition_id: str, *, transition_type: str | None = None,
        duration: float | None = None,
    ) -> None:
        from core.transitions import TransitionType, update_transition

        try:
            update_transition(
                self.project,
                transition_id,
                transition_type=TransitionType(transition_type) if transition_type else None,
                duration=duration,
            )
        except (KeyError, ValueError) as error:
            self.statusBar().showMessage(f"Modification refusée : {error}", 6000)
            self.on_transition_selected(transition_id)
            return
        self._record_history("Modifier une transition")
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._mark_dirty()
        self.timeline_panel.select_transition(transition_id)

    def remove_selected_transition(self, transition_id: str) -> None:
        from core.transitions import remove_transition

        try:
            remove_transition(self.project, transition_id)
        except KeyError:
            return
        self._record_history("Supprimer une transition")
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._mark_dirty()
        self.properties_panel.clear_transition()
        self.statusBar().showMessage("Transition supprimée.", 3000)

    def on_transform_property_changed(
        self, clip_id: str, property_name: str, value: float
    ):
        """Applique une modification de transform depuis l'inspecteur.

        Les modifications fréquentes (slider de l'inspecteur) sont
        coalescées : on ne crée une entrée d'historique qu'après la
        fin d'une rafale (debounce ~400 ms), pour ne pas polluer
        l'historique avec des dizaines d'entrées par seconde.
        """
        if not bool(getattr(self, "_transform_session_active", False)):
            self._finalize_graphic_history()
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
        try:
            self._invalidate_preview_for_clip(clip_id)
        except Exception:
            pass
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
        self._reload_timeline_preserving_selection()

    def _refresh_motion_inspector(self) -> None:
        """Aligne l'inspecteur Mouvement sur le clip sélectionné et la tête de lecture."""
        panel = self.properties_panel
        selected = panel.selected_clip
        if (
            selected is None
            or getattr(selected, "track_type", None) not in {"video", "graphics"}
        ):
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
        if not bool(getattr(self, "_transform_session_active", False)):
            self._finalize_graphic_history()
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
        self._finalize_graphic_history()
        self._finalize_transform_session()
        try:
            remove_transform_keyframe(
                self.project, clip_id, property_name, clip_local_time
            )
        except ValueError as exc:
            print(f"[MainWindow] suppression keyframe refusée : {exc}")
            return
        clip = find_clip(self.project, clip_id)
        self._record_history("Supprimer une image-clé")
        self._reload_timeline_preserving_selection(clip_id)
        if clip is not None:
            self.properties_panel.update_transform_from_clip(
                clip.transform,
                clip.transform_keyframes,
                playhead_seconds=self.playhead_seconds,
            )
        self._sync_preview_to_timeline()
        self._mark_dirty()

    def on_transform_reset(self, clip_id: str) -> None:
        self._finalize_graphic_history()
        self._finalize_transform_session()
        try:
            reset_clip_transform(self.project, clip_id)
        except ValueError as exc:
            print(f"[MainWindow] reset transform refusé : {exc}")
            return
        clip = find_clip(self.project, clip_id)
        self._record_history("Réinitialiser le mouvement")
        self._reload_timeline_preserving_selection(clip_id)
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
        active = self._sync_preview_to_timeline()
        self.update_subtitle_overlay(self.playhead_seconds, active)

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
        if self.timeline_timer.isActive():
            self.timeline_timer.stop()
        self.preview_panel.player.stop()
        self.playhead_seconds = 0.0
        self.timeline_panel.set_playhead_seconds(0.0)
        self._sync_preview_to_timeline()
        self.timeline_panel.setPlayState(False)
        self._set_preview_play_icon(False)

    def toggle_play(self):
        """Bascule lecture / pause en pilotant l'horloge de la timeline."""
        if self.is_playing:
            self._pause_internal()
            return
        preview = self.preview_panel
        if (
            self.sender() is preview.play_button
            and preview.is_library_preview()
            and preview.player.playbackState() == QMediaPlayer.PlayingState
        ):
            preview.player.pause()
            return
        # Si la timeline n'a aucun clip activé, rien à lire.
        if timeline_duration(self.project) <= 0.0:
            return
        self.is_playing = True
        self.timeline_timer.start()
        self._sync_preview_to_timeline()
        self.preview_panel.player.play()
        self.timeline_panel.setPlayState(True)
        self._set_preview_play_icon(True)

    def seek_relative(self, delta_seconds):
        """Seek relatif sur la timeline (avance / recule de ``delta_seconds``)."""
        self.seek_to_position(self.playhead_seconds + delta_seconds)

    def keyPressEvent(self, event):
        focus = QApplication.focusWidget()
        if focus is not None and (
            focus.inherits("QLineEdit")
            or focus.inherits("QTextEdit")
            or focus.inherits("QPlainTextEdit")
        ):
            super().keyPressEvent(event)
            return
        modifiers = set()
        if event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            modifiers.add("ctrl")
        if event.modifiers() & Qt.ShiftModifier:
            modifiers.add("shift")
        if event.modifiers() & Qt.AltModifier:
            modifiers.add("alt")
        key_name = self._key_name(event.key())
        action = resolve_shortcut(key_name, modifiers) if key_name else None
        if action and self._run_editor_shortcut(action):
            event.accept()
            return
        super().keyPressEvent(event)

    @staticmethod
    def _key_name(key: int) -> str | None:
        names = {
            Qt.Key_Space: "space",
            Qt.Key_Left: "left",
            Qt.Key_Right: "right",
            Qt.Key_J: "j",
            Qt.Key_K: "k",
            Qt.Key_L: "l",
            Qt.Key_B: "b",
            Qt.Key_R: "r",
            Qt.Key_Y: "y",
            Qt.Key_U: "u",
            Qt.Key_V: "v",
            Qt.Key_S: "s",
            Qt.Key_N: "n",
            Qt.Key_M: "m",
            Qt.Key_Equal: "equal",
            Qt.Key_Plus: "plus",
            Qt.Key_Minus: "minus",
            Qt.Key_0: "0",
            Qt.Key_Z: "z",
            Qt.Key_A: "a",
            Qt.Key_BracketLeft: "bracketleft",
            Qt.Key_BracketRight: "bracketright",
        }
        return names.get(key)

    def _run_editor_shortcut(self, action: str) -> bool:
        timeline = self.timeline_panel
        if action == "play_pause":
            self.toggle_play()
        elif action == "frame_back":
            self.seek_to_position(step_frames(self.playhead_seconds, -1, self.project.fps))
        elif action == "frame_forward":
            self.seek_to_position(step_frames(self.playhead_seconds, 1, self.project.fps))
        elif action == "second_back":
            self.seek_relative(-1)
        elif action == "second_forward":
            self.seek_relative(1)
        elif action == "shuttle_back":
            self.seek_relative(-2)
        elif action == "shuttle_forward":
            self.seek_relative(2)
        elif action == "zoom_in":
            timeline.zoom_in()
        elif action == "zoom_out":
            timeline.zoom_out()
        elif action == "zoom_fit":
            timeline.fit_timeline()
        elif action == "tool_blade":
            timeline.set_tool("select" if timeline.tool == "blade" else "blade")
        elif action == "tool_roll":
            timeline.set_tool("select" if timeline.tool == "roll" else "roll")
        elif action == "tool_slip":
            timeline.set_tool("select" if timeline.tool == "slip" else "slip")
        elif action == "tool_slide":
            timeline.set_tool("select" if timeline.tool == "slide" else "slide")
        elif action == "tool_select":
            timeline.set_tool("select")
        elif action == "toggle_snap":
            timeline.snap_button.setChecked(not timeline.snap_button.isChecked())
        elif action == "toggle_ripple":
            timeline.ripple_button.setChecked(not timeline.ripple_button.isChecked())
        elif action == "marker_add":
            self.add_marker_at(self.playhead_seconds)
        elif action == "marker_previous":
            self.goto_marker(-1)
        elif action == "marker_next":
            self.goto_marker(1)
        elif action == "cut_at_playhead":
            self.cut_at_playhead()
        elif action == "select_all":
            ids = [view.id for view in timeline.clip_views]
            if ids:
                timeline._set_selection(ids, ids[0], announce=True)
        else:
            return False
        return True

    def on_slip_requested(self, clip_id: str, delta: float) -> None:
        try:
            slip_clip(self.project, clip_id, delta)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] slip refusé : {exc}")
            self._reload_timeline_preserving_selection()
            return
        self._record_history("Slip")
        self._reload_timeline_preserving_selection(clip_id)
        self._mark_dirty()

    def on_slide_requested(self, clip_id: str, new_start: float) -> None:
        try:
            slide_clip(self.project, clip_id, new_start)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] slide refusé : {exc}")
            self._reload_timeline_preserving_selection()
            return
        self._record_history("Slide")
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_roll_requested(self, clip_id: str, edge: str, new_time: float) -> None:
        try:
            roll_edit(self.project, clip_id, edge, new_time)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] roll refusé : {exc}")
            self._reload_timeline_preserving_selection()
            return
        self._record_history("Roll")
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_record_toggled(self, checked: bool) -> None:
        """Démarre ou arrête le microphone sur les pistes audio armées."""
        if checked:
            armed = [
                track.id
                for track in self.project.tracks
                if track.type == "audio" and track.armed and not track.locked
            ]
            if not armed:
                self._set_record_button(False)
                QMessageBox.information(
                    self,
                    "Enregistrement",
                    "Armez une piste audio avant d'enregistrer.",
                )
                return
            try:
                self._audio_recorder.start()
            except AudioRecorderError as exc:
                self._set_record_button(False)
                QMessageBox.critical(self, "Enregistrement", str(exc))
                return
            self._record_origin = float(self.playhead_seconds)
            self._record_tracks = armed
            return
        if not self._audio_recorder.is_recording:
            return
        pcm, rate, channels = self._audio_recorder.stop()
        self._place_recording(pcm, rate, channels)

    def _set_record_button(self, checked: bool) -> None:
        button = self.timeline_panel.record_button
        button.blockSignals(True)
        button.setChecked(checked)
        button.blockSignals(False)

    def _place_recording(
        self, pcm: bytes, sample_rate: int, channels: int, *, quiet: bool = False
    ) -> None:
        import uuid
        from pathlib import Path

        duration = pcm_duration(pcm, sample_rate, channels)
        if duration < 0.05:
            if not quiet:
                QMessageBox.information(
                    self,
                    "Enregistrement",
                    "L'enregistrement est trop court pour devenir un clip.",
                )
            return
        if self.current_project_path:
            folder = Path(self.current_project_path).parent / "enregistrements"
        else:
            folder = Path.home() / "Movies" / "Kut-Studio"
        target = folder / f"prise-{uuid.uuid4().hex[:8]}.wav"
        try:
            write_wav(str(target), pcm, sample_rate, channels)
        except OSError as exc:
            if not quiet:
                QMessageBox.critical(self, "Enregistrement", str(exc))
            return
        asset = MediaAsset(
            id=f"rec-{uuid.uuid4().hex[:8]}",
            path=str(target),
            name=target.stem,
            duration=duration,
            width=0,
            height=0,
            fps=0.0,
            media_type="audio",
            has_audio=True,
        )
        self.project.media_assets.append(asset)
        for track_id in self._record_tracks:
            try:
                add_clip_to_track(self.project, asset.id, track_id, self._record_origin)
            except (KeyError, ValueError) as exc:
                print(f"[MainWindow] prise non placée sur {track_id} : {exc}")
        self._record_history("Enregistrer une prise")
        self._refresh_project_library()
        self._reload_timeline_preserving_selection()
        self._update_timeline_duration()
        self._mark_dirty()

    def on_clips_move_requested(self, placements) -> None:
        try:
            move_clips(self.project, list(placements))
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] déplacement refusé : {exc}")
            self._reload_timeline_preserving_selection()
            return
        self._record_history("Déplacer les clips")
        self._reload_timeline_preserving_selection()
        self._update_timeline_duration()
        self._mark_dirty()

    def on_blade_cut_requested(self, clip_id: str, instant: float) -> None:
        self.cut_selected_clip(clip_id, instant)

    def add_marker_at(self, seconds: float) -> None:
        marker = add_marker(self.project, seconds)
        self._record_history("Ajouter un marqueur")
        self._reload_timeline_preserving_selection()
        self._mark_dirty()
        del marker

    def rename_marker(self, marker_id: str) -> None:
        marker = next((item for item in self.project.markers if item.id == marker_id), None)
        if marker is None:
            return
        name, accepted = QInputDialog.getText(
            self,
            "Marqueur",
            "Nom du marqueur",
            text=marker.name,
        )
        if not accepted:
            return
        marker.name = name.strip()
        self._record_history("Renommer un marqueur")
        self._reload_timeline_preserving_selection()
        self._mark_dirty()

    def goto_marker(self, direction: int) -> None:
        marker = neighbor_marker(self.project, self.playhead_seconds, direction)
        if marker is not None:
            self.seek_to_position(marker.time_seconds)

    def on_track_solo(self, track_id: str, enabled: bool) -> None:
        track = next((item for item in self.project.tracks if item.id == track_id), None)
        if track is None:
            return
        if track.locked:
            self.timeline_panel.refresh_headers()
            return
        track.solo = bool(enabled)
        self._record_history("Solo de piste")
        self._refresh_after_track_change()
        self._mark_dirty()

    def on_track_armed(self, track_id: str, enabled: bool) -> None:
        track = next((item for item in self.project.tracks if item.id == track_id), None)
        if track is None or track.type != "audio":
            return
        if track.locked:
            self.timeline_panel.refresh_headers()
            return
        track.armed = bool(enabled)
        self._record_history("Armer la piste")
        self._refresh_after_track_change()
        self._mark_dirty()

    def on_track_height_cycle(self, track_id: str) -> None:
        track = next((item for item in self.project.tracks if item.id == track_id), None)
        if track is None:
            return
        order = ("compact", "normal", "large")
        current = track.height_mode if track.height_mode in order else "normal"
        track.height_mode = order[(order.index(current) + 1) % len(order)]
        self._record_history("Hauteur de piste")
        self._refresh_after_track_change()
        self._mark_dirty()

    def on_track_collapsed(self, track_id: str, collapsed: bool) -> None:
        track = next((item for item in self.project.tracks if item.id == track_id), None)
        if track is None:
            return
        track.collapsed = bool(collapsed)
        self._record_history("Réduire la piste")
        self._refresh_after_track_change()
        self._mark_dirty()

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
        label_keys = {
            "video": "tracks.add_video_long",
            "audio": "tracks.add_audio_long",
            "subtitle": "tracks.add_subtitle_long",
        }
        self.history.record(self.project, i18n.translate(label_keys[track.type]))
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
        track = next((item for item in self.project.tracks if item.id == track_id), None)
        if track is not None and track.locked:
            self.timeline_panel.refresh_headers()
            return
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
        """Reconstruit la timeline, l'aperçu, le mixeur et la bibliothèque.

        ``set_project`` efface la sélection. On la restaure sans passer
        par ``on_clip_selected``, qui déplacerait la tête de lecture au
        début du clip. L'appel suivant lisait donc toujours une
        sélection vide, et l'inspecteur restait sur l'ancien clip.
        """
        self._reload_timeline_preserving_selection()
        self._update_timeline_duration()
        self._sync_preview_to_timeline()
        self._refresh_project_library()
        mixer = getattr(self, "mixer_panel", None)
        if mixer is not None:
            mixer.set_project(self.project)
            mixer.set_master(self._master_gain_db, self._master_muted)
        self._refresh_undo_redo_state()

    # ------------------------------------------------------------------
    # Tâche 14 : préférences utilisateur (thème + langue)
    # ------------------------------------------------------------------

    def show_preferences(self) -> None:
        """Ouvre la fenêtre ``Préférences``."""
        dialog = PreferencesDialog(
            current_theme=self.theme_manager.requested_mode,
            current_language_code=i18n.current_language(),
            current_performance=self.runtime.requested_profile,
            current_preview_quality=self.runtime.requested_quality,
            current_render_quality=self._render_quality,
            parent=self,
        )
        dialog.theme_changed.connect(self.on_user_setting_changed)
        dialog.language_changed.connect(self.on_user_setting_changed)
        dialog.performance_changed.connect(self.on_performance_setting_changed)
        dialog.preview_quality_changed.connect(self.on_preview_quality_changed)
        dialog.render_quality_changed.connect(self.on_render_quality_changed)
        dialog.restore_defaults_requested.connect(self._restore_default_preferences)
        dialog.exec()

    def _restore_default_preferences(self) -> None:
        """Restaure les réglages affichés par la boîte Préférences.

        Seuls les réglages visibles par l'utilisateur sont
        réinitialisés : le Master (gain/muet) est un réglage de session
        qui n'apparaît pas dans le dialogue — le reconstruire depuis
        ``UserSettings()`` ferait sauter un gain que l'utilisateur
        n'avait pas demandé de restaurer.
        """
        snapshot = self._settings_snapshot()
        self._apply_settings(
            replace(
                UserSettings(),
                master_gain_db=snapshot.master_gain_db,
                master_muted=snapshot.master_muted,
            )
        )

    def on_user_setting_changed(self, value: str) -> None:
        """Applique un thème ou une langue sans oublier les autres préférences."""
        settings = self._settings_snapshot()
        if value in VALID_THEME_MODES:
            settings = replace(settings, theme_mode=value)
        elif value in VALID_LANGUAGES:
            settings = replace(settings, language=value)
        self._apply_settings(settings)

    def on_performance_setting_changed(self, value: str) -> None:
        self._apply_settings(replace(self._settings_snapshot(), performance_profile=value))

    def on_preview_quality_changed(self, value: str) -> None:
        self._apply_settings(replace(self._settings_snapshot(), preview_quality=value))

    def on_render_quality_changed(self, value: str) -> None:
        """Qualité de rendu d'aperçu (tache 30) : persiste + invalide."""
        from core.preview_render import coerce_render_quality

        quality = coerce_render_quality(value)
        self._apply_settings(replace(self._settings_snapshot(), render_quality=quality))
        engine = getattr(self, "preview_engine", None)
        if engine is not None:
            try:
                engine.cancel_all()
            except Exception:
                pass
        self._refresh_preview_cache_state()

    def _init_faithful_preview(self) -> None:
        """Moteur d'aperçu fidèle : cache disque hors .kut + rendus bg."""
        import os as _os

        try:
            from core.preview_cache import DiskPreviewCache
            from core.preview_engine import PreviewEngine
            from core.task_queue import TaskQueue

            cache_dir = _os.environ.get("KUT_STUDIO_CACHE_DIR")
            self.preview_engine = PreviewEngine(
                task_queue=TaskQueue(), cache=DiskPreviewCache(directory=cache_dir)
            )
            try:
                self.preview_engine.cache.evict_if_needed()
            except Exception:
                pass
            self.preview_engine.subscribe(self._on_preview_engine_state)
            self._preview_pump_timer = QTimer(self)
            self._preview_pump_timer.setInterval(150)
            self._preview_pump_timer.timeout.connect(self._pump_preview_queue)
            self._preview_pump_timer.start()
        except Exception:
            self.preview_engine = None

    def _pump_preview_queue(self) -> None:
        """Vide la file d'aperçu sans bloquer l'interface (1 tâche/tick)."""
        engine = getattr(self, "preview_engine", None)
        if engine is None:
            return
        try:
            engine.pump(1)
        except Exception:
            pass

    def _on_preview_engine_state(self, state) -> None:
        """Indicateur 'Calcul de l'aperçu' + état du cache sur le moniteur."""
        panel = getattr(self, "preview_panel", None)
        if panel is None:
            return
        computing = bool(getattr(state, "pending", 0) or getattr(state, "running", 0))
        was_computing = bool(getattr(self, "_preview_was_computing", False))
        self._preview_was_computing = computing
        try:
            if computing:
                panel.set_render_state(True, i18n.translate("preview.computing"))
            else:
                panel.set_render_state(False)
                cached = int(getattr(state, "cached_segments", 0) or 0)
                if cached > 0:
                    panel.set_cache_state(True, i18n.translate("preview.cached"))
                    # Le rendu vient de se terminer : remplace immédiatement
                    # le média source par le segment composé, sans attendre un
                    # mouvement de la tête de lecture.
                    if was_computing and not bool(getattr(self, "is_playing", False)):
                        self._present_cached_preview_at(
                            float(getattr(self, "playhead_seconds", 0.0))
                        )
        except Exception:
            pass

    def _preview_segment_jobs(self, center: float) -> list:
        """Segments proches de la tête de lecture (préchargement)."""
        try:
            from core.filter_graph import fingerprint_plan
            from core.preview_cache import PreviewSegmentKey, SEGMENT_SECONDS
            from core.preview_engine import PreviewJob
            from core.render_plan import build_render_plan
        except Exception:
            return []
        try:
            plan = build_render_plan(self.project)
        except Exception:
            return []
        if not (plan.video_layers or getattr(plan, "graphics_layers", ())):
            return []
        quality = self._render_quality
        params = fingerprint_plan(
            plan, width=self.project.width, height=self.project.height,
            fps=self.project.fps, quality=quality,
        )
        jobs = []
        cursor = max(0.0, float(center) - SEGMENT_SECONDS)
        end = float(center) + 2 * SEGMENT_SECONDS
        while cursor < end:
            seg_end = min(end, cursor + SEGMENT_SECONDS)
            clip_id = "timeline"
            for layer in plan.video_layers:
                start = float(layer.timeline_start)
                stop = float(layer.timeline_end)
                if start <= cursor < stop:
                    clip_id = layer.clip_id
                    break
            if clip_id == "timeline":
                for layer in getattr(plan, "graphics_layers", ()):
                    if layer.timeline_start <= cursor < layer.timeline_end:
                        clip_id = layer.clip_id
                        break
            key = PreviewSegmentKey(
                clip_id=clip_id, start=cursor, end=seg_end,
                quality=quality, params_hash=params,
            )
            jobs.append(
                PreviewJob(
                    key=key, plan=plan, width=self.project.width,
                    height=self.project.height, fps=int(self.project.fps),
                    quality=quality, start=cursor, duration=seg_end - cursor,
                )
            )
            cursor = seg_end
        return jobs

    def _schedule_preview_around(self, center: float) -> None:
        """Précharge les segments proches de la tête de lecture."""
        engine = getattr(self, "preview_engine", None)
        if engine is None:
            return
        try:
            engine.set_playing(bool(self.is_playing))
        except Exception:
            pass
        if bool(self.is_playing):
            return  # lecture : on limite le travail CPU/GPU
        jobs = self._preview_segment_jobs(center)
        self._last_preview_jobs = list(jobs)
        if jobs:
            try:
                engine.prefetch_around(float(center), jobs)
            except Exception:
                pass

    def _cached_preview_at(self, timeline_time: float):
        """Retourne ``(chemin, début)`` pour le segment fidèle actif."""
        engine = getattr(self, "preview_engine", None)
        if engine is None:
            return None
        current_params = self._preview_params_hash()
        if current_params is None:
            return None
        jobs = getattr(self, "_last_preview_jobs", ())
        for job in jobs:
            if getattr(getattr(job, "key", None), "params_hash", None) != current_params:
                # Les média, trims ou effets ont changé depuis la
                # planification. Un segment de cache antérieur ne doit
                # jamais remplacer la source courante.
                continue
            start = float(getattr(job, "start", 0.0))
            duration = float(getattr(job, "duration", 0.0))
            if start <= timeline_time < start + duration:
                try:
                    path = engine.cache.lookup(job.key)
                except Exception:
                    path = None
                if path is not None:
                    return str(path), start
        return None

    def _preview_params_hash(self) -> str | None:
        """Empreinte du plan courant, pour refuser un cache devenu obsolète."""
        try:
            from core.filter_graph import fingerprint_plan
            from core.render_plan import build_render_plan

            plan = build_render_plan(self.project)
            return fingerprint_plan(
                plan,
                width=self.project.width,
                height=self.project.height,
                fps=self.project.fps,
                quality=self._render_quality,
            )
        except Exception:
            return None

    def _present_cached_preview_at(self, timeline_time: float) -> bool:
        """Affiche directement un segment composé, sans réévaluer la timeline."""
        cached_preview = self._cached_preview_at(timeline_time)
        if cached_preview is None:
            return False
        cached_path, segment_start = cached_preview
        self.preview_panel.preview_at(
            cached_path, max(0.0, timeline_time - segment_start)
        )
        # Le segment inclut déjà transform et effets : les réappliquer dans
        # QGraphicsVideoItem doublerait le traitement.
        self.preview_panel.apply_transform(
            position_x=0.0,
            position_y=0.0,
            scale=1.0,
            rotation=0.0,
            opacity=1.0,
        )
        self.preview_panel.set_effects(())
        return True

    def _refresh_preview_cache_state(self) -> None:
        """Invalide/actualise l'état du cache après une modification."""
        self._schedule_preview_around(float(getattr(self, "playhead_seconds", 0.0)))

    def _invalidate_preview_for_clip(self, clip_id: str) -> None:
        """Invalide uniquement les segments affectés + annule l'obsolète."""
        engine = getattr(self, "preview_engine", None)
        if engine is None:
            return
        try:
            engine.invalidate_clip(str(clip_id))
        except Exception:
            pass

    def _apply_settings(self, settings: UserSettings) -> None:
        # Application du thème dans Qt.
        self.theme_manager.set_mode(settings.theme_mode)
        self.theme_manager.apply_to(QApplication.instance())
        self.runtime.set_requested_profile(settings.performance_profile)
        self.runtime.set_preview_quality(settings.preview_quality)
        self._render_quality = settings.render_quality
        self._apply_runtime_hints()
        # État Master : preference de session, jamais du projet.
        self._master_gain_db = float(settings.master_gain_db)
        self._master_muted = bool(settings.master_muted)
        mixer = getattr(self, "mixer_panel", None)
        if mixer is not None:
            mixer.set_master(self._master_gain_db, self._master_muted)
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
        # Persistance immédiate : la langue doit suivre les changements
        # sans effacer le profil de performance ni la qualité d'aperçu.
        save_user_settings(replace(self._settings_snapshot(), language=code))

    def _retranslate_ui(self) -> None:
        """Force la mise à jour des textes dépendant de la langue."""
        self.setWindowTitle(i18n.translate("app.title"))
        # Les menus racine qui possèdent une clé de traduction sont
        # mis à jour ; les sous-menus sans clé conservent leur titre.
        for action in self.menuBar().actions():
            menu = action.menu()
            if menu is None:
                continue
            translated_title = self._translate_menu_title(menu.objectName())
            if translated_title:
                menu.setTitle(translated_title)
        # Mise à jour des widgets traduisibles les plus visibles.
        if hasattr(self.preview_panel, "update_translations"):
            self.preview_panel.update_translations()
        mixer = getattr(self, "mixer_panel", None)
        if mixer is not None:
            mixer.update_translations()
        self._sync_workspace_menu()
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
