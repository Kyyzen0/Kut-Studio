import logging
import os
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.autosave import AutosaveCoordinator
from core.audio_effects_library import AudioEffectPresetStore
from core.effects_library import UserPresetStore
from core.edit_history import ProjectHistory
from core.transition_presets import (
    TransitionPresetStore,
)
from core.text_presets import TextPresetStore
from core.export_engine import ExportEngine
from core.render_queue import RenderQueue
from core.process_supervisor import shutdown_children
from core.project_factory import create_default_project
from core.project_model import Project
from core.shortcuts import ShortcutMap
from core.scopes_analyzer import (
    ScopeAnalyzer,
    cleanup_temporary_paths,
)
from core.studio_runtime import StudioRuntime, peak_rss_bytes
from core.audio_recorder import AudioRecorder
from core.timeline_editing import apply_solo
from core.timeline_index import build_timeline_index
from core.timeline_navigation import step_frames
from core.workspace_state import PAGE_COLOR, PAGE_EDIT, PanelId
from core.timeline_operations import find_clip
from core.timeline_evaluator import (
    timeline_duration,
)
from core.user_settings import (
    UserSettings,
    load_user_settings,
)
from core.visual_effects import evaluate_transform
from ui import i18n
from ui.design_system import Radius, Typography, Weights
from ui.debug_overlay import DebugOverlay
from ui.mixer_panel import MixerPanel
from ui.preview_panel import PreviewPanel
from ui.project_panel import ProjectPanel
from ui.shortcut_manager import ShortcutManager
from ui.scopes_panel import ScopesPanel
from ui.properties_panel import PropertiesPanel
from ui.timeline_panel import TimelinePanel
from ui.viewer_host import ViewerHostSplitter
from ui.export_panel import ExportPanel
from ui.side_rail import SideRail, DEFAULT_SECTIONS
from ui.workspace import WorkspaceManager
from ui.main_window_mixins.history import HistoryMixin
from ui.main_window_mixins.preferences import PreferencesMixin
from ui.main_window_mixins.recording import RecordingMixin
from ui.main_window_mixins.timeline_editing import TimelineEditingMixin
from ui.main_window_mixins.media_import import MediaImportMixin
from ui.main_window_mixins.project_files import ProjectFilesMixin
from ui.main_window_mixins.workspace_actions import WorkspaceActionsMixin
from ui.main_window_mixins.scopes import ScopesMixin, _ScopeEvents
from ui.main_window_mixins.transform_and_clip_effects import TransformEffectsMixin
from ui.main_window_mixins.subtitles_graphics import SubtitlesGraphicsMixin
from ui.main_window_mixins.library_organization import LibraryOrganizationMixin
from ui.main_window_mixins.faithful_preview import FaithfulPreviewMixin
from ui.main_window_mixins.animation import AnimationMixin
from ui.main_window_mixins.encoding import EncodingMixin
from ui.main_window_mixins.performance import PerformanceMixin
from ui.main_window_mixins.presets import PresetsMixin
from ui.main_window_mixins.track_management import TrackManagementMixin
from ui.main_window_mixins.audio import AudioMixin
from ui.main_window_mixins.color_grading import ColorGradingMixin
from ui.main_window_mixins.color_page import ColorPageMixin
from ui.main_window_mixins.multicam import MulticamMixin
from ui.main_window_mixins.multicam_creation import MulticamCreationMixin
from ui.main_window_mixins.multicam_settings import MulticamSettingsMixin
from ui.multicam_viewer import MulticamViewer
from ui.main_window_mixins.sequences import SequencesMixin
from ui.main_window_mixins.motion_graphics import MotionGraphicsMixin
from ui.main_window_mixins.tracking import TrackingMixin
from ui.main_window_mixins.time_editing import TimeEditingMixin
from ui.main_window_mixins.hardware_preview import HardwarePreviewMixin
from ui.main_window_mixins.updates import UpdatesMixin
from ui.main_window_mixins.social import SocialMixin
from ui.main_window_mixins.social_audio import SocialAudioMixin
from ui.main_window_mixins.templates import TemplatesMixin
from ui.main_window_mixins.beat_grid import BeatGridMixin
from ui.main_window_mixins.auto_captions import AutoCaptionsMixin
from core.decode_policy import DecodePurpose

# Noms lus à l'appel par les mixins via ``_main_window()`` : des tests les
# remplacent sur ce module (``ui.main_window.QMessageBox``, etc.).
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox  # noqa: E402,F401
from core.media_probe import probe_media  # noqa: E402,F401
from core.user_settings import save_user_settings  # noqa: E402,F401
from ui.theme import COLORS, ThemeManager, global_stylesheet, label_style

LOGGER = logging.getLogger(__name__)


# --- Scopes vidéo / monitoring couleur (tâche 31) -------------------------
#
# Ces trois constantes bornent le coût CPU de l'analyse : pendant la
# lecture, on ne veut pas analyser plus de 10 images / s (au‑delà, la
# scope n'est pas lisible à l'œil et on sature le CPU au détriment de
# la lecture).


SCOPES_MIN_INTERVAL: float = 0.1
"""Intervalle minimal (secondes) entre deux analyses pendant la lecture."""

SCOPES_COLUMNS: int = 320
PLAYBACK_TICK_SECONDS = 0.04
"""Intervalle du minuteur de lecture, et pas d'un tic sans horloge de référence (tic forcé hors lecture, tests)."""
"""Nombre de colonnes des waveform / parade."""

SCOPES_VECTORSCOPE_BINS: int = 128
"""Résolution du vectorscope (côté en bins)."""


class MainWindow(
    BeatGridMixin,
    AutoCaptionsMixin,
    SocialMixin,
    SocialAudioMixin,
    TemplatesMixin,
    UpdatesMixin,
    HardwarePreviewMixin,
    TrackingMixin,
    TimeEditingMixin,
    MotionGraphicsMixin,
    SequencesMixin,
    MulticamMixin,
    MulticamCreationMixin,
    MulticamSettingsMixin,
    ColorPageMixin,
    ColorGradingMixin,
    AudioMixin,
    TrackManagementMixin,
    PresetsMixin,
    PerformanceMixin,
    EncodingMixin,
    AnimationMixin,
    FaithfulPreviewMixin,
    LibraryOrganizationMixin,
    SubtitlesGraphicsMixin,
    TransformEffectsMixin,
    ScopesMixin,
    WorkspaceActionsMixin,
    ProjectFilesMixin,
    MediaImportMixin,
    TimelineEditingMixin,
    RecordingMixin,
    PreferencesMixin,
    HistoryMixin,
    QMainWindow,
):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Kut-Studio")
        self.setMinimumSize(1180, 720)
        self.resize(1480, 920)
        self.subtitle_file: str | None = None
        self.active_subtitle_clip = None
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
        # Proxies média : aperçu seulement, jamais l'export (voir core.proxy_manager).
        self._init_proxies(loaded_settings)
        self._init_encoding(loaded_settings)
        self._flow_backend_request = loaded_settings.flow_backend      # avant le moteur d'aperçu et le moteur d'export
        self._time_ripple_timeline = loaded_settings.time_ripple_timeline
        self._init_social(loaded_settings)
        self._init_auto_captions(loaded_settings)
        # Décodage matériel : réglé avant le premier QMediaPlayer (variable lue une fois par Qt).
        self._init_hardware_preview(loaded_settings)
        self._init_animation()
        # Mises à jour : préférences lues tôt (l'instantané des préférences les réécrit) ; aucun réseau ici.
        self._init_updates(loaded_settings)
        self._timeline_index = None
        self._timeline_index_project_id: int | None = None
        self._autosave = AutosaveCoordinator()
        # S'abonne aux changements de langue pour recharger les libellés.
        # Conserver la référence permet de se désabonner à la fermeture.
        # Une lambda anonyme conserverait les anciennes fenêtres en mémoire.
        self._i18n_callback = self.on_language_changed
        i18n.subscribe(self._i18n_callback)

        # Raccourcis clavier : une seule table (core.shortcuts), appliquée
        # par le gestionnaire, qui se persiste à chaque modification.
        self.shortcuts = ShortcutManager(
            ShortcutMap.from_overrides(loaded_settings.shortcuts), parent=self
        )
        self._translated_actions: list[tuple[QAction, str]] = []
        self._translated_menus: list[tuple[QMenu, str]] = []
        self.shortcuts.register_handlers(self._shortcut_handlers())
        self.shortcuts.overrides_changed.connect(self._on_shortcuts_changed)
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
        # travail (voir ``_on_scopes_analysis_ready``). Le QObject pont vit
        # dans le thread Qt : il doit exister avant le premier résultat.
        self._scope_events = _ScopeEvents(self)
        self.scopes_analyzer = ScopeAnalyzer(
            on_result=self._on_scopes_analysis_ready,
            on_error=self._on_scopes_analysis_failed,
            # 10 analyses / s maximum pendant la lecture : au‑delà,
            # on saturerait le CPU sans gain de lisibilité.
            min_interval=SCOPES_MIN_INTERVAL,
        )
        self.scopes_panel = ScopesPanel()
        self._scope_events.ready.connect(self.scopes_panel.set_result)
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
        # Moniteur : la visionneuse ordinaire, ou le moniteur Multicam (tous les angles) sur la même zone.
        self.multicam_viewer = MulticamViewer()
        self._monitor_stack = QStackedWidget()
        self._monitor_stack.addWidget(self.preview_panel)
        self._monitor_stack.addWidget(self.multicam_viewer)
        self._viewer_host = ViewerHostSplitter(self._monitor_stack, self.scopes_panel)
        # Par défaut, les scopes restent repliés pour ne pas rogner le
        # viewer ; l'utilisateur les ouvre via le menu Affichage.
        self._viewer_host.setSizes([520, 0])

        self.project_panel = ProjectPanel()
        self.project_panel.proxy_state_provider = self._proxy_state_for_asset
        self.project_panel.proxy_action_requested.connect(self._on_proxy_action_requested)
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
        self.properties_panel = PropertiesPanel(self.update_volume)
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
        self._start_hardware_preview()
        self._init_cache_manager(loaded_settings)
        self.export_panel = ExportPanel()
        self.properties_panel.timeline_panel = self.timeline_panel
        self.export_panel.export_requested.connect(self.launch_export)
        self.export_panel.close_requested.connect(self.show_editor)
        self.export_panel.cancel_requested.connect(self.cancel_export)
        # Synchroniser la bibliothèque de médias avec le Project initial.
        self._refresh_project_library()

        # Moteur d'export unique, partagé avec la file de rendu : un seul
        # FFmpeg à la fois. La file persiste ses jobs et reprend au démarrage
        # (un rendu interrompu par un arrêt brutal est marqué « échoué »).
        self.export_engine = ExportEngine(self)
        self.export_engine.flow_preference = self._flow_preference()
        self.export_engine.preparation_reported.connect(self._on_preparation_reported)
        self.render_queue = RenderQueue(self.export_engine, parent=self)
        self.render_queue.restore()
        self.export_panel.set_queue(self.render_queue)
        self.export_panel.add_to_queue_requested.connect(self.enqueue_export)
        self.render_queue.run_finished.connect(self._on_render_run_finished)
        self.render_queue.encoder_fallback.connect(self._on_encoder_fallback)
        # Encodeurs matériels : options du panneau Export, détection en tâche de fond.
        self.export_panel.set_default_encoder(self._export_encoder)
        self.export_panel.encoder_changed.connect(self.set_export_encoder)
        self._start_hardware_detection()
        # Dernier filet : aucun FFmpeg ne survit à la fin de l'application.
        QApplication.instance().aboutToQuit.connect(self.render_queue.shutdown)

        # La timeline est désormais l'horloge principale : on ne lit plus
        # ``QMediaPlayer.positionChanged`` pour piloter ``TimelinePanel``.
        # Le lecteur ``QMediaPlayer`` n'est qu'un consommateur de la position
        # de la timeline (voir ``_sync_preview_to_timeline``).
        self.preview_panel.player.playbackStateChanged.connect(self.on_playback_state_changed)
        self.timeline_panel.play_button.clicked.connect(self.toggle_play)
        self.timeline_panel.seek_requested.connect(self.seek_to_position)
        self.timeline_panel.clip_selected.connect(self.on_clip_selected)
        self.timeline_panel.move_clip_requested.connect(self.on_move_clip_requested)
        self.timeline_panel.trim_clip_left_requested.connect(self.on_trim_left_requested)
        self.timeline_panel.trim_clip_right_requested.connect(self.on_trim_right_requested)
        self.timeline_panel.asset_dropped.connect(self.on_asset_dropped)
        self.timeline_panel.photos_dropped.connect(self.fill_slots_from_photo_drop)
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
        # Animation : losanges, navigation, interpolation, copier/coller, courbes.
        self.properties_panel.animation_toggled.connect(self.on_animation_toggled)
        self.properties_panel.keyframe_navigation_requested.connect(self.on_keyframe_navigation)
        self.properties_panel.interpolation_requested.connect(self.on_keyframe_interpolation_requested)
        self.properties_panel.animation_copy_requested.connect(self.copy_animation)
        self.properties_panel.animation_paste_requested.connect(self.paste_animation)
        self.properties_panel.graph_editor_requested.connect(self.open_graph_editor)
        self.properties_panel.active_property_changed.connect(self.set_active_animation_property)
        self.timeline_panel.keyframes_selected.connect(self.on_timeline_keyframes_selected)
        self.timeline_panel.keyframes_move_requested.connect(self.on_keyframes_move_requested)
        # Tâche 18 : time remapping
        self.properties_panel.speed_changed.connect(self.on_speed_changed)
        self.properties_panel.reverse_toggled.connect(self.on_reverse_toggled)
        self.properties_panel.freeze_frame_created.connect(self.on_freeze_frame_created)
        self.properties_panel.freeze_frame_removed.connect(self.on_freeze_frame_removed)
        self.properties_panel.freeze_duration_changed.connect(self.on_freeze_duration_changed)
        self.properties_panel.time_remapping_reset.connect(self.on_time_remapping_reset)
        # Temps du clip : menu « Vitesse », section « Temps » de l'inspecteur, raccourcis (core.time_commands).
        self.properties_panel.time_command_requested.connect(self.on_time_command)
        self.timeline_panel.time_command_requested.connect(self.on_time_command)
        self.timeline_panel.time_ripple_timeline = self._time_ripple_timeline
        self.timeline_panel.seek_requested.connect(self._schedule_preview_notice)
        self.timeline_panel.clip_selected.connect(self._schedule_preview_notice)
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
        self.properties_panel.text_animation_requested.connect(self.apply_text_animation_to_clip)
        self.properties_panel.voice_sync_requested.connect(self.sync_text_on_voice)
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
        # Bibliothèque d'effets audio (section Audio du panneau).
        self.audio_effect_preset_store = AudioEffectPresetStore()
        self.project_panel.set_user_audio_effect_presets(
            self.audio_effect_preset_store.all_user_presets()
        )
        self.project_panel.set_audio_effect_favorites(
            self.audio_effect_preset_store.favorites()
        )
        self.audio_effect_preset_store.subscribe(
            self._on_audio_effect_presets_changed
        )
        self.project_panel.audio_effect_apply_requested.connect(
            self.on_audio_effect_preset_apply_requested
        )
        self._connect_sfx_library()
        self.project_panel.audio_effect_preset_save_requested.connect(
            self.on_audio_effect_preset_save_requested
        )
        self.project_panel.audio_effect_preset_delete_requested.connect(
            self.on_audio_effect_preset_delete_requested
        )
        self.project_panel.audio_effect_favorite_toggled.connect(
            self.audio_effect_preset_store.toggle_favorite
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
        # Séquences : navigation (fil d'Ariane), bibliothèque, imbrication.
        self._init_sequences()
        # Multicam : bascule d'angle, remplacement, aplatir, création depuis la timeline.
        self._init_multicam()
        self._init_multicam_creation()
        self._init_multicam_settings()
        # Motion graphics : panneau Calques, viewer interactif, presets.
        self._init_motion_graphics()
        self.set_platform_zones(self._platform_zones)               # zones de plateforme mémorisées (préférences)
        # Tracking 2D : panneau Suivi, trackers dans le viewer, analyses.
        self._init_tracking()

        # Espace de travail : le gestionnaire est l'unique autorité sur
        # la disposition. Il enregistre les quatre panneaux existants
        # (mêmes objets, mêmes signaux) puis compose les zones dock.
        self.mixer_panel = MixerPanel()
        from ui.history_panel import HistoryPanel

        self.history_panel = HistoryPanel()
        self.history_panel.entries_provider = lambda: self.history.entries()
        self.history_panel.state_requested.connect(self.go_to_history_state)
        # Page Couleur : nœuds d'étalonnage et roues du clip affiché par l'inspecteur.
        self._init_color_page()
        self.workspace = WorkspaceManager(self)
        self.workspace.register(PanelId.MEDIA, self.project_panel)
        # Le dock « viewer » contient le viewer **et** le panneau de
        # scopes dans un splitter vertical (tâche 31) : c'est ce
        # host qu'on enregistre, pas le seul ``preview_panel``.
        self.workspace.register(PanelId.VIEWER, self._viewer_host)
        self.workspace.register(PanelId.INSPECTOR, self.properties_panel)
        self.workspace.register(PanelId.TIMELINE, self.timeline_panel)
        self.workspace.register(PanelId.MIXER, self.mixer_panel)
        self.workspace.register(PanelId.HISTORY, self.history_panel)
        self.workspace.register(PanelId.COLOR, self.color_panel)
        self.workspace.register(PanelId.CLIPS, self.clip_strip)
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
        self.timeline_timer.setInterval(round(PLAYBACK_TICK_SECONDS * 1000))
        self._playback_clock: float | None = None
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
        self.shortcuts.changed.connect(self._refresh_shortcut_tooltips)
        self._refresh_shortcut_tooltips()
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
        - ``color`` : la page Couleur (nœuds, roues, scopes, onglet
          Couleur de l'inspecteur) ; ``edit`` : la page Montage ; toute
          autre section ramène au Montage, où vivent ses outils ;
        - ``graphics`` : on ouvre directement l'onglet correspondant de
          l'inspecteur ;
        - ``templates`` : aucune action concrète disponible aujourd'hui —
          on laisse l'état actif visuellement et on affiche un message
          discret pour rester honnête vis-à-vis de l'utilisateur.
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

        self.switch_page(PAGE_COLOR if section_id == "color" else PAGE_EDIT)
        if section_id in ("color", "edit"):
            return

        inspector_tab = {"audio": 3, "graphics": 4}.get(section_id)
        if inspector_tab is not None:
            if not self.workspace.is_visible(PanelId.INSPECTOR):
                self.workspace.set_panel_visible(PanelId.INSPECTOR, True)
            self.properties_panel._select_inspector_tab(inspector_tab)

        if section_id in (
            "media", "audio", "text", "effects", "transitions", "graphics", "sequences"
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

    # ------------------------------------------------------------------
    # Étalonnage couleur (tâche 29)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Automation audio et ducking (tâche 28)
    # ------------------------------------------------------------------

    # -- Inspecteur : clip ----------------------------------------------

    def _find_clip_and_track(self, clip_id: str):
        if self.project is None or not clip_id:
            return None, None
        for track in self.project.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    return clip, track
        return None, None

    def closeEvent(self, event) -> None:
        """Libère les abonnements globaux avant de fermer la fenêtre."""
        # Rien ne se ferme sans que l'utilisateur ait pu sauver son travail.
        if not self._confirm_discard_changes():
            event.ignore()
            return
        # Un rendu en cours demande confirmation : refuser garde tout ouvert.
        if not self._confirm_close_during_render():
            event.ignore()
            return
        # Chaque étape est protégée : si l'une échoue (file de rendu, proxy...), les suivantes
        # doivent quand même s'exécuter, sinon FFmpeg, l'autosave ou les threads survivent à la fenêtre.
        for name, step in self._shutdown_steps():
            try:
                step()
            except Exception:  # noqa: BLE001 - l'arrêt ne s'interrompt jamais
                logging.getLogger(__name__).exception("Arrêt : l'étape « %s » a échoué", name)
        super().closeEvent(event)

    def _shutdown_steps(self):  # i18n-ignore: noms d'étapes du journal d'arrêt, jamais affichés
        """Étapes de fermeture dans l'ordre : ``(nom, fonction)``."""
        def attr(name):
            return getattr(self, name, None)

        def stop_timer(name):
            timer = attr(name)
            if timer is not None:
                timer.stop()

        def stop_recording():
            recorder = attr("_audio_recorder")
            if recorder is not None and recorder.is_recording:
                pcm, rate, channels = recorder.stop()
                self._place_recording(pcm, rate, channels, quiet=True)

        def shutdown_proxies():
            # Aucune génération de proxy ne survit à la fenêtre : FFmpeg est tué.
            if attr("proxies") is not None:
                self._shutdown_proxies()

        def call(owner_name, method):
            owner = attr(owner_name)
            if owner is not None:
                getattr(owner, method)()

        def clean_scope_files():
            cleanup_temporary_paths(attr("_scope_temporary_paths") or ())
            self._scope_temporary_paths = ()

        def release_i18n():
            callback = attr("_i18n_callback")
            if callback is not None:
                i18n.unsubscribe(callback)
                self._i18n_callback = None

        return [
            ("file de rendu", self.render_queue.shutdown),
            ("encodage", self._shutdown_encoding),
            ("aperçu matériel", self._shutdown_hardware_preview),
            # Désabonne la fenêtre, annule les rendus d'aperçu puis attend leur thread (dans cet ordre, voir
            # _stop_preview_pump) : un segment en cours ne notifie jamais une fenêtre en cours de destruction.
            ("pompe et rendus d'aperçu", self._stop_preview_pump),
            ("proxies", shutdown_proxies),
            ("mises à jour", self._shutdown_updates),
            ("espaces de travail", lambda: call("workspace", "shutdown")),
            ("minuteur timeline", lambda: stop_timer("timeline_timer")),
            ("enregistrement audio", stop_recording),
            ("sessions d'édition", self._finalize_pending_edit_sessions),
            ("autosave final", self._write_autosave),
            ("minuteur autosave", lambda: stop_timer("_autosave_timer")),
            ("minuteur debug", lambda: stop_timer("_debug_timer")),
            ("minuteur aperçu", lambda: stop_timer("_preview_pump_timer")),
            ("pistage", self._cancel_tracking_jobs),
            ("analyse du flux optique", self._cancel_flow_analysis),
            ("thème de la timeline", lambda: call("timeline_panel", "unsubscribe_from_theme")),
            ("synchronisation Multicam", self._cancel_multicam_syncs),
            ("réglages Multicam", self._close_multicam_settings),
            ("audio Multicam", self._release_multicam_audio),
            ("moniteur Multicam", lambda: call("multicam_viewer", "shutdown")),
            ("média du viewer", lambda: call("preview_panel", "release_media")),
            ("scopes", lambda: call("scopes_analyzer", "close")),
            ("fichiers de scopes", clean_scope_files),
            ("autosave", lambda: call("_autosave", "close")),
            ("runtime", lambda: call("runtime", "shutdown")),
            # Filet final, après tous les arrêts ciblés : tout FFmpeg encore enregistré (miniature, sonde lancée
            # par un thread qui finit) est tué, identité vérifiée, puis attendu (core/process_supervisor.py).
            ("processus enfants", shutdown_children),
            ("minuteur sous-titres", lambda: stop_timer("_subtitle_edit_timer")),
            ("traductions", release_i18n),
        ]

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

    # -- Espaces de travail --------------------------------------------

    # -- Actions de workspace ------------------------------------------

    def _build_top_bar(self):
        """Barre d'application compacte centrée sur le montage courant.

        La navigation détaillée vit dans le rail gauche. La barre haute
        garde seulement l'identité, la séquence, l'historique, les
        réglages et l'action primaire d'export.
        """
        from ui.design_system import Sizes, Spacing
        from ui.icons import IconButton, IconName, make_icon

        bar = QWidget()
        bar.setFixedHeight(Sizes.top_bar)
        bar.setObjectName("main_topbar")
        bar.setStyleSheet(
            f"QWidget#main_topbar {{ background: {COLORS['panel']};"
            f" border-bottom: 1px solid {COLORS['border']}; }}"
        )
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(Spacing.md, 0, Spacing.md, 0)
        layout.setSpacing(Spacing.sm)

        # --- Identité ---------------------------------------------------
        logo_box = QWidget()
        logo_layout = QHBoxLayout(logo_box)
        logo_layout.setContentsMargins(0, 0, 0, 0)
        logo_layout.setSpacing(Spacing.sm)
        logo = QLabel("K")
        logo.setAlignment(Qt.AlignCenter)
        logo.setFixedSize(26, 26)
        logo.setStyleSheet(
            f"background: {COLORS['accent']}; color: {COLORS['on_accent']};"
            f" border-radius: {Radius.pill}px; font-size: {Typography.body_lg}px; font-weight: {Weights.bold};"
        )
        brand = QLabel("KUT‑STUDIO")
        brand.setStyleSheet(label_style(10, "muted_strong", 800))
        logo_layout.addWidget(logo)
        logo_layout.addWidget(brand)
        layout.addWidget(logo_box)

        # Les objets de navigation restent disponibles pour les raccourcis,
        # la synchronisation du rail et la compatibilité de l'API, sans
        # dupliquer la navigation à l'écran.
        top_nav_row = QWidget(bar)
        top_nav_layout = QHBoxLayout(top_nav_row)
        top_nav_layout.setContentsMargins(0, 0, 0, 0)
        top_nav_layout.setSpacing(2)
        self.top_nav_buttons: list[QPushButton] = []
        for index, key in enumerate(self._TOP_NAV_KEYS):
            button = QPushButton(i18n.translate(key))
            button.setObjectName("topNavTab")
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(
                lambda _checked=False, idx=index: self._select_top_nav(idx)
            )
            self.top_nav_buttons.append(button)
            top_nav_layout.addWidget(button)
        self.top_nav = top_nav_row
        self.top_nav.hide()

        layout.addWidget(self._vseparator())

        # --- Séquence courante -----------------------------------------
        sequence_box = QWidget()
        sequence_layout = QVBoxLayout(sequence_box)
        sequence_layout.setContentsMargins(0, 0, 0, 0)
        sequence_layout.setSpacing(0)
        seq_title = QLabel(i18n.translate("sequence.nav.caption"))
        self._sequence_caption = seq_title
        seq_title.setStyleSheet(label_style(9, "muted", 800))
        self.project_label = QLabel(i18n.translate("topbar.default_name"))
        self.project_label.setStyleSheet(label_style(12, "text", 700))
        sequence_layout.addWidget(seq_title)
        sequence_layout.addWidget(self.project_label)

        saved_box = QWidget()
        saved_layout = QHBoxLayout(saved_box)
        saved_layout.setContentsMargins(0, 0, 0, 0)
        saved_layout.setSpacing(6)
        self.saved_indicator = QLabel(i18n.translate("topbar.saved"))
        self.saved_indicator.setToolTip(i18n.translate("topbar.saved_tooltip"))
        self.saved_indicator.setStyleSheet(label_style(10, "success", 700))
        self.saved_indicator.setAlignment(Qt.AlignCenter)
        saved_layout.addWidget(self.saved_indicator)
        layout.addWidget(sequence_box)
        layout.addWidget(saved_box)
        layout.addStretch(1)
        # --- Pages (Montage | Couleur), au centre ---------------------
        layout.addWidget(self._build_page_switcher())
        layout.addStretch(1)

        # --- Historique, disposition, réglages et export ---------------
        self.undo_button = IconButton(
            icon=IconName.RESET,
            tooltip=i18n.translate("action.undo"),
            size=Sizes.icon_button_sm,
        )
        self.redo_button = IconButton(
            icon=IconName.RESET,
            tooltip=i18n.translate("action.redo"),
            size=Sizes.icon_button_sm,
        )
        from PySide6.QtGui import QTransform, QIcon
        from PySide6.QtCore import Qt as QtCore
        base_pixmap = make_icon(IconName.RESET, size=Sizes.icon_button_sm - 4).pixmap(64, 64)
        self.redo_button.setIcon(
            QIcon(base_pixmap.transformed(
                # Miroir horizontal : une rotation de 180° garderait le
                # sens antihoraire de la flèche « Annuler ».
                QTransform().scale(-1, 1), QtCore.SmoothTransformation
            ))
        )
        self.undo_button.clicked.connect(self.undo_last)
        self.redo_button.clicked.connect(self.redo_last)
        layout.addWidget(self.undo_button)
        layout.addWidget(self.redo_button)

        layout.addWidget(self._vseparator())
        layout_btn = IconButton(
            icon=IconName.PANEL_RESTORE,
            tooltip=i18n.translate("topbar.reset_layout"),
            size=Sizes.icon_button,
        )
        self.reset_layout_button = layout_btn
        layout_btn.clicked.connect(self.workspace.reset_layout)
        layout.addWidget(layout_btn)

        layout.addWidget(self._build_update_notice())

        settings_btn = IconButton(
            icon=IconName.MENU,
            tooltip=i18n.translate("topbar.settings"),
            size=Sizes.icon_button,
            square=False,
        )
        settings_btn.setText(" " + i18n.translate("topbar.settings"))
        settings_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.settings_button = settings_btn
        settings_btn.clicked.connect(self.show_preferences)
        layout.addWidget(settings_btn)

        self.export_button = IconButton(
            icon=IconName.EXPORT,
            tooltip=i18n.translate("tooltip.export"),       # aucun raccourci : Ctrl+E est « activer/désactiver le clip »
            size=Sizes.button_md,
            square=False,
            accent=True,
        )
        self.export_button.setText(" " + i18n.translate("topbar.export"))
        self.export_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.export_button.setMinimumWidth(108)
        self.export_button.setMinimumHeight(32)
        self.export_button.clicked.connect(self.show_export)
        layout.addWidget(self.export_button)

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
        name = self.project.name if self.project is not None else "Projet sans titre"  # i18n-ignore: nom par défaut du projet (donnée du cœur)
        display_name = (
            i18n.translate("topbar.default_name") if name == "Projet sans titre" else name  # i18n-ignore: idem
        )
        display_name = display_name or i18n.translate("topbar.default_name")
        # La séquence éditée est toujours visible : « Projet › Séquence ».
        if self.project is not None and len(self.project.sequences) > 1:
            self.project_label.setText(f"{display_name} › {self.project.active_sequence.name}")
        else:
            self.project_label.setText(display_name)
        if self.project is not None:
            self.project_label.setToolTip(self.project.active_sequence.name)
            sequence = self.project.active_sequence
            self.properties_panel.set_sequence_info(sequence.width, sequence.height, sequence.fps)
        if self.project_dirty:
            self.saved_indicator.setText(i18n.translate("topbar.dirty"))
            self.saved_indicator.setToolTip(i18n.translate("topbar.dirty_tooltip"))
            self.saved_indicator.setStyleSheet(label_style(11, "warning", 700))
        else:
            self.saved_indicator.setText(i18n.translate("topbar.saved"))
            self.saved_indicator.setToolTip(i18n.translate("topbar.saved_tooltip"))
            self.saved_indicator.setStyleSheet(label_style(11, "success", 700))

    # ------------------------------------------------------------------
    # Undo / Redo / Duplicate / Ripple / Enable
    # ------------------------------------------------------------------

    def _find_previous_v1_clip(self):
        """Retourne le dernier clip V1 (par timeline_start) ou ``None``."""
        views = sorted(self.timeline_panel.clip_views, key=lambda v: v.start)
        for view in reversed(views):
            if view.track_id == "V1":
                return view
        return None

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
            LOGGER.warning(
                "Rafraîchissement de l'aperçu en échec après la modification du clip %s : le moniteur peut rester sur l'ancienne image",
                clip_id, exc_info=True,
            )
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

    # ------------------------------------------------------------------
    # Tâche 22 : gestion des presets d'effets
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Effets audio non destructifs (tâche 27)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Drag & drop
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Debounce pour la modification de texte des sous-titres
    # ------------------------------------------------------------------

    def _reset_selection_and_inspector(self) -> None:
        """Réinitialise la sélection de clip et l'inspecteur après un changement de projet."""
        self.active_subtitle_clip = None
        self.timeline_panel._set_selection([], None, announce=False)
        self.timeline_panel.clear_transition_selection()
        self.properties_panel.set_clip(None, "")
        # La bibliothèque d'effets perd son contexte de clip.
        self._sync_effects_library_context()

    def _build_menu_bar(self):
        menu_bar = self.menuBar()
        menu_bar.setNativeMenuBar(False)

        # Fichier
        file_menu = QMenu(i18n.translate("menu.file"), self)
        file_menu.setObjectName("file_menu")
        new_action = self._command_action("project_new", "menu.item.new")
        open_action = self._command_action("project_open", "menu.item.open")
        save_action = self._command_action("project_save", "menu.item.save")
        save_as_action = self._command_action("project_save_as", "menu.item.save_as")
        file_menu.addAction(new_action)
        file_menu.addAction(open_action)
        file_menu.addSeparator()
        file_menu.addAction(save_action)
        file_menu.addAction(save_as_action)
        file_menu.addSeparator()
        import_subs_action = self._labelled_action("menu.item.import_srt")
        import_subs_action.triggered.connect(self.import_subtitles_via_dialog)
        file_menu.addAction(import_subs_action)
        export_subs_action = self._labelled_action("menu.item.export_srt")
        export_subs_action.triggered.connect(self.export_subtitles_via_dialog)
        file_menu.addAction(export_subs_action)
        file_menu.addSeparator()
        exit_action = self._command_action("quit", "action.quit")
        file_menu.addAction(exit_action)

        # Édition
        edit_menu = QMenu(i18n.translate("menu.edit"), self)
        edit_menu.setObjectName("edit_menu")
        self.undo_action = self._command_action("undo", "action.undo")
        edit_menu.addAction(self.undo_action)

        self.redo_action = self._command_action("redo", "action.redo")
        edit_menu.addAction(self.redo_action)

        edit_menu.addSeparator()

        duplicate_action = self._command_action("duplicate_clip", "menu.item.duplicate_clip")
        edit_menu.addAction(duplicate_action)

        self.delete_action = self._command_action("delete_clip", "menu.item.delete_clip")
        edit_menu.addAction(self.delete_action)

        ripple_action = self._command_action("ripple_delete", "action.ripple_delete")
        edit_menu.addAction(ripple_action)

        enable_action = self._command_action("toggle_clip_enabled", "action.toggle_clip")
        edit_menu.addAction(enable_action)

        edit_menu.addSeparator()

        for key in ("action.cut", "menu.item.copy", "menu.item.paste"):
            # Pas de presse-papiers de clips pour l'instant : l'entrée est désactivée plutôt que de
            # rester active et muette (un print invisible dans l'application empaquetée).
            action = self._labelled_action(key)
            self._disable_unavailable(action)
            edit_menu.addAction(action)

        # Séquence
        sequence_menu = QMenu(i18n.translate("menu.timeline"), self)
        sequence_menu.setObjectName("timeline_menu")
        # Séquences : création, imbrication, navigation, gestion.
        sequence_menu.addAction(self._command_action("sequence_new", "sequence.action.new"))
        self.nest_selection_action = self._command_action(
            "sequence_nest_selection", "sequence.action.nest_selection"
        )
        sequence_menu.addAction(self.nest_selection_action)
        sequence_menu.addAction(
            self._command_action("sequence_open_nested", "sequence.action.open_nested")
        )
        sequence_menu.addAction(self._command_action("sequence_parent", "sequence.action.parent"))
        sequence_menu.addAction(self._command_action("sequence_back", "sequence.action.back"))
        sequence_menu.addAction(self._command_action("sequence_forward", "sequence.action.forward"))
        rename_sequence_action = self._labelled_action("sequence.action.rename")
        rename_sequence_action.triggered.connect(lambda: self.rename_sequence_command())
        sequence_menu.addAction(rename_sequence_action)
        duplicate_sequence_action = self._labelled_action("sequence.action.duplicate")
        duplicate_sequence_action.triggered.connect(lambda: self.duplicate_sequence_command())
        sequence_menu.addAction(duplicate_sequence_action)
        delete_sequence_action = self._labelled_action("sequence.action.delete")
        delete_sequence_action.triggered.connect(lambda: self.delete_sequence_command())
        sequence_menu.addAction(delete_sequence_action)
        sequence_menu.addSeparator()
        # Multicam : création depuis la sélection, source, aplatir, moniteur.
        self.multicam_create_action = self._command_action("multicam_create", "multicam.menu.create")
        sequence_menu.addAction(self.multicam_create_action)
        sequence_menu.addAction(self._command_action("multicam_open_source", "multicam.menu.open_source"))
        sequence_menu.addAction(self._command_action("multicam_flatten", "multicam.menu.flatten"))
        sequence_menu.addAction(self._command_action("multicam_viewer", "multicam.menu.viewer"))
        sequence_menu.addAction(self._command_action("multicam_settings", "multicam.menu.settings"))
        sequence_menu.addSeparator()
        for key, handler in (
            ("menu.item.add_clip", None),
            ("menu.item.scene_cut", lambda: self.cut_at_scene_changes()),
            ("menu.item.trim", lambda: self.cut_at_playhead()),                         # Ctrl+K
            ("menu.item.marker", lambda: self.add_marker_at(self.playhead_seconds)),    # M
        ):
            action = self._labelled_action(key)
            if handler is None:
                self._disable_unavailable(action)
            else:
                action.triggered.connect(lambda checked=False, run=handler: run())
            sequence_menu.addAction(action)

        # Fenêtre — le contenu dépend du gestionnaire d'espace de
        # travail, créé plus bas ; on ne garde que la partie fixe ici.
        window_menu = QMenu(i18n.translate("menu.window"), self)
        window_menu.setObjectName("window_menu")
        self.window_menu = window_menu
        reset_action = self._labelled_action("menu.item.reset_layout")
        reset_action.triggered.connect(self.reset_workspace_layout)
        window_menu.addAction(reset_action)
        window_menu.addSeparator()
        # Pages (Montage, Couleur) : aussi au centre de la barre supérieure.
        for page in ("edit", "color"):
            window_menu.addAction(self._command_action(f"page_{page}", f"shortcuts.command.page_{page}"))
        window_menu.addSeparator()
        # Scopes de monitoring couleur (tâche 31). L'action reste
        # checkable pour refléter l'état du splitter sans qu'on ait à
        # le relire à chaque ouverture de menu.
        self.scopes_action = self._command_action(
            "toggle_scopes", "menu.item.show_scopes", checkable=True
        )
        self.scopes_action.setChecked(True)
        window_menu.addAction(self.scopes_action)
        # Proxies : bascule rapide (l'export lit toujours les originaux).
        self.proxies_action = self._labelled_action("menu.item.use_proxies")
        self.proxies_action.setCheckable(True)
        self.proxies_action.setChecked(self.proxies.enabled)
        self.proxies_action.toggled.connect(self.set_proxies_enabled)
        window_menu.addAction(self.proxies_action)
        # Animation : éditeur de courbes (outil avancé, jamais obligatoire).
        self.graph_editor_action = self._command_action("graph_editor", "menu.item.graph_editor")
        window_menu.addAction(self.graph_editor_action)

        # Menu Séquence : opérations de piste.
        track_add_video_action = self._labelled_action("tracks.add_video_long")
        track_add_video_action.triggered.connect(
            lambda: self.on_add_track_requested("video")
        )
        sequence_menu.addAction(track_add_video_action)
        track_add_audio_action = self._labelled_action("tracks.add_audio_long")
        track_add_audio_action.triggered.connect(
            lambda: self.on_add_track_requested("audio")
        )
        sequence_menu.addAction(track_add_audio_action)
        track_add_subtitle_action = self._labelled_action("tracks.add_subtitle_long")
        track_add_subtitle_action.triggered.connect(
            lambda: self.on_add_track_requested("subtitle")
        )
        sequence_menu.addAction(track_add_subtitle_action)
        sequence_menu.addSeparator()
        track_remove_action = self._labelled_action("tracks.remove")
        track_remove_action.triggered.connect(self.remove_selected_track)
        sequence_menu.addAction(track_remove_action)
        track_move_up_action = self._labelled_action("tracks.move_up")
        track_move_up_action.triggered.connect(
            lambda: self._move_selected_track(direction=-1)
        )
        sequence_menu.addAction(track_move_up_action)
        track_move_down_action = self._labelled_action("tracks.move_down")
        track_move_down_action.triggered.connect(
            lambda: self._move_selected_track(direction=1)
        )
        sequence_menu.addAction(track_move_down_action)
        track_rename_action = self._labelled_action("tracks.rename")
        track_rename_action.triggered.connect(self.rename_selected_track)
        sequence_menu.addAction(track_rename_action)
        track_lock_action = self._labelled_action("tracks.toggle_lock")
        track_lock_action.triggered.connect(self.toggle_selected_track_lock)
        sequence_menu.addAction(track_lock_action)
        track_visible_action = self._labelled_action("tracks.toggle_visible")
        track_visible_action.triggered.connect(self.toggle_selected_track_visible)
        sequence_menu.addAction(track_visible_action)
        track_mute_action = self._labelled_action("tracks.toggle_mute")
        track_mute_action.triggered.connect(self.toggle_selected_track_muted)
        sequence_menu.addAction(track_mute_action)
        sequence_menu.addSeparator()
        proxies_selection_action = self._labelled_action("proxy.action.generate_selection")
        proxies_selection_action.triggered.connect(self.generate_proxies_for_selection)
        sequence_menu.addAction(proxies_selection_action)

        for menu in (file_menu, edit_menu, sequence_menu):
            menu_bar.addMenu(menu)
        self._build_layers_menu(menu_bar)
        self._build_social_menu(menu_bar)
        menu_bar.addMenu(window_menu)

        # Menu Édition : entrée Préférences (à la fin de la barre).
        preferences_action = self._command_action("preferences", "action.preferences")
        # On insère l'entrée dans la barre ``Fenêtre`` pour rester
        # accessible sans modifier l'ordre établi.
        window_menu.addAction(preferences_action)
        self.diagnostics_action = self._labelled_action("debug.toggle")
        self.diagnostics_action.setCheckable(True)
        self.diagnostics_action.toggled.connect(self.set_diagnostics_visible)
        window_menu.addAction(self.diagnostics_action)

        # Aide : mises à jour et version installée.
        help_menu = QMenu(i18n.translate("menu.help"), self)
        help_menu.setObjectName("help_menu")
        self.check_updates_action = self._labelled_action("update.menu.check")
        self.check_updates_action.triggered.connect(lambda _checked=False: self.check_for_updates())
        help_menu.addAction(self.check_updates_action)
        help_menu.addSeparator()
        self.about_action = self._labelled_action("update.menu.about")
        self.about_action.triggered.connect(lambda _checked=False: self.show_about())
        help_menu.addAction(self.about_action)
        menu_bar.addMenu(help_menu)

    def _labelled_action(self, text_key: str) -> QAction:
        """``QAction`` dont le libellé suit la langue (retraduite à chaud)."""
        action = QAction(i18n.translate(text_key), self)
        self._translated_actions.append((action, text_key))
        return action

    def _command_action(
        self, command_id: str, text_key: str, *, checkable: bool = False
    ) -> QAction:
        """Action d'une commande clavier, avec un libellé traduit à chaud."""
        action = self.shortcuts.create_action(
            command_id, i18n.translate(text_key), self, checkable=checkable
        )
        self._translated_actions.append((action, text_key))
        return action

    def _notify_placeholder(self, feature_name):
        """Zone du rail sans panneau : le dit dans la barre d'état (un print ne se voit pas une fois empaqueté)."""
        bar = self.statusBar() if hasattr(self, "statusBar") else None
        if bar is not None:
            bar.showMessage(i18n.translate("status.unavailable"), 4000)

    @staticmethod
    def _disable_unavailable(action) -> None:
        """Entrée de menu sans fonction pour l'instant : grisée, avec l'explication en infobulle."""
        action.setEnabled(False)
        action.setToolTip(i18n.translate("status.unavailable"))

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


    def _set_preview_play_icon(self, playing: bool) -> None:
        from ui.design_system import Iconography
        from ui.icons import IconName, make_icon

        if playing:
            self.preview_panel.play_button.setIcon(
                make_icon(IconName.PAUSE, size=Iconography.lg)
            )
            self.preview_panel.play_button.setToolTip(i18n.translate("topbar.pause"))
        else:
            self.preview_panel.play_button.setIcon(
                make_icon(IconName.PLAY, size=Iconography.lg)
            )
            self.preview_panel.play_button.setToolTip(i18n.translate("topbar.play"))

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
            tick_time = time.perf_counter()
            self.runtime.diagnostics.note_playback_tick(tick_time)
            self._observe_playback_quality(tick_time)
            duration = self._ensure_timeline_index().duration
            # La tête de lecture avance du temps **réellement** écoulé depuis le tic précédent. Avec un pas fixe de
            # 40 ms, un tic en retard ralentissait la lecture ; le lecteur vidéo, lui, avance en temps réel, et
            # l'écart (> 200 ms) se soldait par un recalage forcé du lecteur : une saccade visible. Pas de plafond :
            # après un gel de l'interface, le lecteur a avancé de tout ce temps, et une tête de lecture bornée le
            # ferait reculer. La veille de la machine n'entre pas dans ``perf_counter`` (horloge monotone).
            last = self._playback_clock
            step = PLAYBACK_TICK_SECONDS if last is None else max(0.0, tick_time - last)
            self._playback_clock = tick_time
            next_playhead = self.playhead_seconds + step
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
        # Sous charge (aperçu réduit par le mode Auto), les scopes — un
        # FFmpeg par analyse — ne sont pas recalculés pendant la lecture :
        # ils se rafraîchissent à l'arrêt.
        if self.is_playing and not self.runtime.preview.degraded:
            self._request_scopes_analysis()

    def _sync_preview_to_timeline(self) -> list:
        """Synchronise le moniteur puis l'aperçu des calques et la surcouche."""
        self._viewer_composited = False
        sequence = self.project.active_sequence
        self.preview_panel.set_frame_format(sequence.width, sequence.height, sequence.fps)
        self.export_panel.set_project_fps(sequence.fps)
        result = self._sync_preview_core()
        self._after_preview_sync()
        return result

    def _sync_preview_core(self) -> list:
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
            self._sync_multicam_audio(None, active_clips)
            return active_clips

        video_clips = [c for c in active_clips if c.track_type == "video"]
        if not video_clips:
            self._sync_multicam_audio(None, active_clips)
            self.preview_panel.show_no_active_clip()
            if any(c.track_type == "graphics" for c in active_clips):
                try:
                    self._schedule_preview_around(float(self.playhead_seconds))
                except Exception:
                    LOGGER.debug(
                        "Planification des segments d'aperçu en échec (graphiques seuls) : le moniteur reste sur son repli",
                        exc_info=True,
                    )
            return active_clips
        top_clip = video_clips[-1]
        if not top_clip.source_path:
            clip_obj = self._ensure_timeline_index().clip(top_clip.owner_clip_id)
            clip_name = getattr(clip_obj, "label", "") if clip_obj is not None else ""
            self.preview_panel.show_missing_media(clip_name)
            return active_clips
        # Aperçu : proxy prêt ou original, le moins coûteux à décoder qui reste
        # assez net (voir ``preview_source_for`` ; repli silencieux sur
        # l'original si le proxy est absent, supprimé ou obsolète).
        self.preview_panel.preview_at(
            self.preview_source_for(
                top_clip.source_path, divisor=self.runtime.preview_divisor(),
                purpose=DecodePurpose.REALTIME,
            ),
            top_clip.source_time,
            playing=bool(self.is_playing),
        )
        # Multicam : si le son vient d'une autre source que l'image, le lecteur du moniteur est coupé et ces sources jouent.
        self._sync_multicam_audio(top_clip, active_clips)
        # Tâche 13 : applique le transform animé du clip supérieur si
        # la timeline contient au moins un clip vidéo. On évalue le
        # ``ClipTransform`` à ``playhead_seconds`` ; on garde l'opacité
        # au sommet pour être conforme à la convention de la tâches 6
        # (clip actif supérieur = superposition).
        # Pour un clip vu à travers une séquence imbriquée, le moniteur temps
        # réel applique le transform du clip imbriqué (le niveau visible
        # ici) ; la composition exacte des niveaux vient des segments fidèles.
        owner_id = top_clip.owner_clip_id
        clip_obj = self._ensure_timeline_index().clip(owner_id)
        if clip_obj is None:
            try:
                clip_obj = find_clip(self.project, owner_id)
            except KeyError:
                clip_obj = None
        if clip_obj is not None:
            # Les images-clés sont locales au clip (0 = début du clip sur la
            # timeline), comme dans l'export : jamais le temps du média source,
            # qui diffère dès que le clip est rogné ou accéléré.
            # Tracking : liaisons et stabilisation sont des keyframes dérivées
            # (mêmes valeurs que l'export) ; sans tracking, les keyframes du clip.
            from core.tracking_bindings import effective_transform_keyframes

            evaluated = evaluate_transform(
                clip_obj.transform,
                effective_transform_keyframes(self.project, clip_obj),
                clip_local_time=float(self.playhead_seconds) - float(clip_obj.timeline_start),
                clip_duration=clip_obj.duration,
            )
            self.preview_panel.set_effect_time(float(self.playhead_seconds) - float(clip_obj.timeline_start))
            self.preview_panel.apply_transform(
                position_x=evaluated.position_x,
                position_y=evaluated.position_y,
                scale=evaluated.scale,
                rotation=evaluated.rotation,
                opacity=evaluated.opacity,
                anchor_x=evaluated.anchor_x,
                anchor_y=evaluated.anchor_y,
                scale_x=evaluated.scale_x,
                scale_y=evaluated.scale_y,
                flip_h=evaluated.flip_h,
                flip_v=evaluated.flip_v,
                fill=evaluated.fill,
                pan_x=evaluated.pan_x,
                pan_y=evaluated.pan_y,
            )
            self.preview_panel.set_effects(clip_obj.effects)
            self.preview_panel.set_color_grade(self._monitor_color_grade(clip_obj))
            if self.preview_panel.gpu_active:
                self._sync_gpu_compositing(clip_obj, float(self.playhead_seconds))
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
            LOGGER.debug(
                "Planification des segments d'aperçu fidèles en échec : le moniteur reste sur le média source",
                exc_info=True,
            )
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
        # L'index décrit la séquence active : changer de séquence le reconstruit.
        project_id = (id(self.project), self.project.active_sequence_id)
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

    def set_diagnostics_visible(self, visible: bool) -> None:
        """Affiche ou cache l'overlay de performance."""
        if visible:
            self._debug_timer.start()
            self._refresh_debug_overlay()
        else:
            self._debug_timer.stop()
            self._debug_overlay.hide()

    def _refresh_debug_overlay(self) -> None:  # i18n-ignore: incrustation de débogage (KUT_STUDIO_DEBUG)
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

    def _pause_internal(self) -> None:
        """Met la lecture en pause sans toucher au playhead."""
        self.is_playing = False
        self._playback_clock = None
        if self.timeline_timer.isActive():
            self.timeline_timer.stop()
        self.preview_panel.player.pause()
        self.timeline_panel.setPlayState(False)
        self._set_preview_play_icon(False)
        self._reset_adaptive_quality()

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

    # ------------------------------------------------------------------
    # Organisation avancée de la bibliothèque (tâche 25)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Import de médias (vidéo et audio)
    # ------------------------------------------------------------------

    # Compatibilité ascendante : les anciens tests/appels peuvent continuer
    # d'utiliser ``import_video_to_project``.
    # Compatibilité ascendante : anciens appels.

    # ------------------------------------------------------------------
    # Tâche 32 : calques graphiques
    # ------------------------------------------------------------------

    def update_volume(self, value):
        self.properties_panel.volume_value.setText(f"{value} %")
        self.preview_panel.audio_output.setVolume(value / 100.0)

    # ------------------------------------------------------------------
    # Tâche 23 : gestion des presets de transitions
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Tâche 24 : gestion des modèles de texte + édition du style
    # ------------------------------------------------------------------

    def seek_to_position(self, seconds):
        """Seek sur la timeline (et non plus sur le média source).

        Met à jour le playhead, synchronise la timeline puis l'aperçu,
        et rafraîchit l'overlay de sous-titres.
        """
        # La durée vient de l'index (reconstruit à chaque modification
        # structurelle) : plus de parcours de tous les clips à chaque seek.
        duration = self._ensure_timeline_index().duration
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
        self._playback_clock = None
        if self.timeline_timer.isActive():
            self.timeline_timer.stop()
        self.preview_panel.player.stop()
        self._reset_adaptive_quality()
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
        self._playback_clock = time.perf_counter()
        self.timeline_timer.start()
        self._sync_preview_to_timeline()
        self.preview_panel.player.play()
        self.timeline_panel.setPlayState(True)
        self._set_preview_play_icon(True)

    def seek_relative(self, delta_seconds):
        """Seek relatif sur la timeline (avance / recule de ``delta_seconds``)."""
        self.seek_to_position(self.playhead_seconds + delta_seconds)

    def keyPressEvent(self, event):
        # Un champ texte garde ses frappes : la garde vit dans le gestionnaire.
        if self.shortcuts.handle_key_event(event, QApplication.focusWidget()):
            event.accept()
            return
        super().keyPressEvent(event)

    def _shortcut_handlers(self) -> dict:
        """Fonction associée à chaque commande de ``core.shortcuts.COMMANDS``.

        Seul endroit où une commande clavier est reliée à son effet. Les
        lambdas lisent ``timeline_panel`` à l'appel : le gestionnaire est
        créé avant les panneaux.
        """

        def timeline():
            return self.timeline_panel

        def toggle_tool(name):
            def run():
                tl = timeline()
                tl.set_tool("select" if tl.tool == name else name)

            return run

        def toggle_button(attribute):
            def run():
                button = getattr(timeline(), attribute)
                button.setChecked(not button.isChecked())

            return run

        def step(frames):
            return lambda: self.seek_to_position(
                step_frames(self.playhead_seconds, frames, self.project.fps)
            )

        return {
            # Projet
            "project_new": self.new_project,
            "project_open": self.open_project_file,
            "project_save": self.save_project_file,
            "project_save_as": self.save_project_as,
            "quit": self.close,
            # Édition
            "undo": self.undo_last,
            "redo": self.redo_last,
            "duplicate_clip": self.duplicate_selected_clip,
            "delete_clip": self.delete_selection_or_clip,
            "ripple_delete": self.ripple_delete_selected_clip,
            "toggle_clip_enabled": self.toggle_selected_clip_enabled,
            "cut_at_playhead": self.cut_at_playhead,
            "select_all": self._select_all_clips,
            # Lecture
            "play_pause": self.toggle_play,
            "shuttle_back": lambda: self.seek_relative(-2),
            "shuttle_forward": lambda: self.seek_relative(2),
            # Navigation
            "frame_back": step(-1),
            "frame_forward": step(1),
            "second_back": lambda: self.seek_relative(-1),
            "second_forward": lambda: self.seek_relative(1),
            # Timeline
            "toggle_snap": toggle_button("snap_button"),
            "toggle_ripple": toggle_button("ripple_button"),
            # Outils
            "tool_select": lambda: timeline().set_tool("select"),
            "tool_blade": toggle_tool("blade"),
            "tool_roll": toggle_tool("roll"),
            "tool_slip": toggle_tool("slip"),
            "tool_slide": toggle_tool("slide"),
            # Affichage
            "zoom_in": lambda: timeline().zoom_in(),
            "zoom_out": lambda: timeline().zoom_out(),
            "zoom_fit": lambda: timeline().fit_timeline(),
            "toggle_scopes": self.toggle_scopes_visible,
            "preferences": self.show_preferences,
            **self._page_shortcut_handlers(),
            # Audio
            "audio_record_toggle": lambda: timeline().record_button.click(),
            "audio_master_mute": self.toggle_master_mute,
            "track_toggle_mute": self.toggle_selected_track_muted,
            # Marqueurs
            "marker_add": lambda: self.add_marker_at(self.playhead_seconds),
            "marker_previous": lambda: self.goto_marker(-1),
            "marker_next": lambda: self.goto_marker(1),
            # Animation (images-clés)
            **self._animation_shortcut_handlers(),
            # Séquences
            **self._sequence_shortcut_handlers(),
            **self._multicam_shortcut_handlers(),
            **self._mograph_shortcut_handlers(),
            **self._time_shortcut_handlers(),
            **self._social_shortcut_handlers(),
            **self._beat_shortcut_handlers(),
            **self._auto_captions_shortcut_handlers(),
            **self._social_audio_shortcut_handlers(),
            **self._templates_shortcut_handlers(),
        }

    def _select_all_clips(self) -> None:
        timeline = self.timeline_panel
        ids = [view.id for view in timeline.clip_views]
        if ids:
            timeline._set_selection(ids, ids[0], announce=True)

    def toggle_master_mute(self) -> None:
        """Coupe / rétablit la sortie Master (commande clavier)."""
        self._master_muted = not self._master_muted
        self.mixer_panel.set_master(self._master_gain_db, self._master_muted)
        self._save_master_state()

    # Clés i18n des boutons de navigation supérieure (même ordre que le rail latéral).
    _TOP_NAV_KEYS = ("rail.edit", "rail.media", "rail.effects", "rail.color", "rail.audio", "rail.graphics")

    # (bouton de la timeline, commande, clé i18n du gabarit) : ``{hint}`` devient
    # `` (B)`` selon le raccourci courant, ou rien si la commande n'en a plus.
    _SHORTCUT_TOOLTIPS = (
        ("blade_button", "tool_blade", "timeline.tip.blade"),
        ("roll_button", "tool_roll", "timeline.tip.roll"),
        ("slip_button", "tool_slip", "timeline.tip.slip"),
        ("slide_button", "tool_slide", "timeline.tip.slide"),
        ("ripple_button", "toggle_ripple", "timeline.tip.ripple"),
        ("marker_button", "marker_add", "timeline.tip.marker"),
    )

    def _refresh_shortcut_tooltips(self) -> None:
        """Garde les info-bulles de la timeline fidèles aux raccourcis courants."""
        self._refresh_history_tooltips()
        timeline = getattr(self, "timeline_panel", None)
        if timeline is None:
            return
        for attribute, command_id, template in self._SHORTCUT_TOOLTIPS:
            button = getattr(timeline, attribute, None)
            if button is None:
                continue
            hint = self.shortcuts.hint(command_id)
            button.setToolTip(i18n.translate(template, hint=f" ({hint})" if hint else ""))

    def _on_shortcuts_changed(self, _overrides: dict) -> None:
        """Persiste la configuration des raccourcis dès qu'elle change."""
        try:
            save_user_settings(self._settings_snapshot())
        except OSError:
            # Un échec d'écriture ne doit pas gêner l'édition.
            pass

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

        # Sélectionne la nouvelle piste via ses boutons (lock/visible/mute).
        # L'UI se met à jour avec refresh_clip_widgets dans
        # ``_refresh_after_track_change``.

    # ------------------------------------------------------------------
    # Tâche 14 : préférences utilisateur (thème + langue)
    # ------------------------------------------------------------------

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
        for action, text_key in self._translated_actions:
            action.setText(i18n.translate(text_key))
        for submenu, title_key in self._translated_menus:
            submenu.setTitle(i18n.translate(title_key))
        # Mise à jour des widgets traduisibles les plus visibles.
        if hasattr(self.preview_panel, "update_translations"):
            self.preview_panel.update_translations()
        mixer = getattr(self, "mixer_panel", None)
        if mixer is not None:
            mixer.update_translations()
        self.history_panel.retranslate()
        self._sync_workspace_menu()
        self.timeline_panel.sequence_bar.retranslate()
        self.project_panel.sequence_view.retranslate()
        self.project_panel.retranslate()
        self.workspace.retranslate()
        self._retranslate_top_bar()
        self.side_rail.retranslate()
        self._refresh_shortcut_tooltips()
        self._set_preview_play_icon(bool(getattr(self, "is_playing", False)))
        self.timeline_panel.refresh_clip_widgets()
        self._refresh_undo_redo_state()

    def _retranslate_top_bar(self) -> None:
        """Textes de la barre supérieure qui ne dépendent pas de l'état du projet."""
        self._sequence_caption.setText(i18n.translate("sequence.nav.caption"))
        self.reset_layout_button.setToolTip(i18n.translate("topbar.reset_layout"))
        self.settings_button.setToolTip(i18n.translate("topbar.settings"))
        self.settings_button.setText(" " + i18n.translate("topbar.settings"))
        self.export_button.setToolTip(i18n.translate("tooltip.export"))
        self.export_button.setText(" " + i18n.translate("topbar.export"))
        for button, key in zip(self.top_nav_buttons, self._TOP_NAV_KEYS):
            button.setText(i18n.translate(key))
        self._retranslate_update_notice()
        self._retranslate_page_switcher()

    @staticmethod
    def _translate_menu_title(object_name: str | None) -> str:
        mapping = {
            "file_menu": "menu.file",
            "edit_menu": "menu.edit",
            "view_menu": "menu.view",
            "timeline_menu": "menu.timeline",
            "window_menu": "menu.window",
            "help_menu": "menu.help",
            "layers_menu": "menu.layers",
        }
        key = mapping.get(object_name or "")
        return i18n.translate(key) if key else ""
