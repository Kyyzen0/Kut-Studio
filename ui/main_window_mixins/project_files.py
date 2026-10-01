"""Méthodes de ``MainWindow`` regroupées : project_files."""

from __future__ import annotations

import os
from core.autosave import autosave_is_newer, discard_autosave, sidecar_path
from core.project_factory import create_default_project
from core.project_io import load_project, save_project
from core.render_plan import RenderPlan, build_render_plan


def _main_window():
    """Module ``ui.main_window`` résolu à l'appel (noms remplaçables en test)."""
    from ui import main_window

    return main_window


class ProjectFilesMixin:
    """Mixin de ``MainWindow`` (project_files)."""

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
            _main_window().QMessageBox.critical(
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
        path, _ = _main_window().QFileDialog.getSaveFileName(
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
            _main_window().QMessageBox.critical(
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
        path, _ = _main_window().QFileDialog.getOpenFileName(
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
            _main_window().QMessageBox.critical(
                self,
                "Impossible d'ouvrir le projet",
                f"Le fichier {path} n'a pas pu être ouvert :\n\n{exc}",
            )
            return
        restored_autosave = False
        if autosave_is_newer(path):
            answer = _main_window().QMessageBox.question(
                self,
                "Récupération",
                "Une sauvegarde automatique plus récente que ce projet "
                "a été trouvée.\n\nVoulez-vous la restaurer ?",
                _main_window().QMessageBox.Yes | _main_window().QMessageBox.No,
                _main_window().QMessageBox.Yes,
            )
            if answer == _main_window().QMessageBox.Yes:
                try:
                    loaded = load_project(str(sidecar_path(path)))
                    restored_autosave = True
                except (FileNotFoundError, ValueError, OSError, TypeError) as exc:
                    _main_window().QMessageBox.critical(
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
        path, _ = _main_window().QFileDialog.getSaveFileName(
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
        _main_window().QMessageBox.information(
            self,
            "Export terminé",
            f"L'export est terminé avec succès.\n\nFichier : {output_path}",
        )

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
