"""Méthodes de ``MainWindow`` regroupées : project_files."""

from __future__ import annotations

import logging
import os
import time
from core.autosave import autosave_is_newer, discard_autosave, sidecar_path
from core.export_paths import default_export_directory, export_file_name
from core.project_factory import DEFAULT_PROJECT_NAME, create_default_project
from core.project_io import load_project, save_project
from core.project_lock import ProjectLock, ProjectLockedError, lock_file_for
from core.render_plan import RenderPlan, build_render_plan
from ui import i18n


def _main_window():
    """Module ``ui.main_window`` résolu à l'appel (noms remplaçables en test)."""
    from ui import main_window

    return main_window


LOGGER = logging.getLogger("kut_studio.project_files")


class ProjectFilesMixin:
    """Mixin de ``MainWindow`` (project_files)."""

    def _acquire_project_lock(self, path: str) -> ProjectLock | None:
        """Prend le verrou de ``path`` **sans** quitter encore le projet ouvert (ADR-0005) ; ``None`` s'il n'a pas pu être posé.

        Ne bloque jamais l'ouverture : si quelqu'un d'autre tient déjà le projet, l'utilisateur est **prévenu** (enregistrer depuis deux
        endroits fait perdre des modifications) et le projet s'ouvre quand même. Le projet déjà ouvert garde son verrou (rien à reprendre)."""
        current = getattr(self, "_project_lock", None)
        if current is not None and current.path == lock_file_for(path):
            return current
        lock = ProjectLock(path, kind="app")
        try:
            lock.acquire()
        except ProjectLockedError as error:
            LOGGER.warning("Projet déjà verrouillé : %s", error)
            owner = error.owner
            if owner is None:
                text = i18n.translate("dialog.locked.unreadable", lock_file=str(error.lock_file))
            else:
                text = i18n.translate(
                    "dialog.locked.text",
                    who=i18n.translate(f"cli.lock.kind.{owner.kind}"), pid=owner.pid, host=owner.host,
                    since=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(owner.started)), lock_file=str(error.lock_file),
                )
            _main_window().QMessageBox.warning(self, i18n.translate("dialog.locked.title"), text)
            return None
        except OSError as error:                     # dossier en lecture seule, volume réseau capricieux : pas de verrou, pas d'obstacle
            LOGGER.info("Verrou du projet non posé (%s)", error)
            return None
        return lock

    def _adopt_project_lock(self, lock: ProjectLock | None) -> None:
        """Le projet est installé : son verrou devient celui de la fenêtre, et l'ancien est relâché."""
        current = getattr(self, "_project_lock", None)
        if current is not None and current is not lock:
            current.release()
        self._project_lock = lock

    def _discard_candidate_lock(self, lock: ProjectLock | None) -> None:
        """Le projet n'a pas pu être ouvert : on relâche le verrou pris pour lui (jamais celui du projet resté ouvert)."""
        if lock is not None and lock is not getattr(self, "_project_lock", None):
            lock.release()

    def _lock_project(self, path: str) -> None:
        self._adopt_project_lock(self._acquire_project_lock(path))

    def _unlock_project(self) -> None:
        """Retire le verrou du projet qu'on quitte (le fichier n'est retiré que s'il est bien le nôtre)."""
        lock = getattr(self, "_project_lock", None)
        self._project_lock = None
        if lock is not None:
            lock.release()

    def project_display_name(self) -> str:
        """Le nom du projet tel que l'utilisateur le voit, partout (barre du haut, dialogue « Enregistrer ? »).

        Un projet jamais nommé (nom vide ou nom par défaut du cœur) s'appelle « Mon montage » dans la langue de l'interface.
        """
        name = (getattr(getattr(self, "project", None), "name", "") or "").strip()
        if not name or name == DEFAULT_PROJECT_NAME:
            return i18n.translate("topbar.default_name")
        return name

    def _confirm_discard_changes(self) -> bool:
        """Propose d'enregistrer un projet modifié avant de le quitter.

        ``False`` annule l'action en cours (fermer, nouveau, ouvrir) : l'utilisateur a refusé, ou
        l'enregistrement a échoué / été annulé, et rien ne doit être perdu.
        """
        if not getattr(self, "project_dirty", False):
            return True
        self._finalize_pending_edit_sessions()
        box = _main_window().QMessageBox
        name = self.project_display_name()
        answer = box.question(
            self,
            i18n.translate("project.unsaved.title"),
            i18n.translate("project.unsaved.text", name=name),
            box.Save | box.Discard | box.Cancel,
            box.Save,
        )
        if answer == box.Cancel:
            return False
        if answer == box.Save:
            self.save_project_file()
            return not self.project_dirty
        return True

    def new_project(self) -> None:
        """Crée un nouveau projet vierge via ``create_default_project()``."""
        if not self._confirm_discard_changes():
            return
        self._install_new_project(create_default_project())

    def _install_new_project(self, project) -> None:
        """Remplace le projet ouvert par ``project`` (jamais enregistré) ; les changements ont déjà été confirmés."""
        self._finalize_pending_edit_sessions()
        self._release_open_project()
        self.project = project
        self.current_project_path = None
        self._unlock_project()
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
        self._reset_sequence_navigation()
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
        except (OSError, ValueError) as exc:
            _main_window().QMessageBox.critical(
                self,
                i18n.translate("dialog.save.failed_title"),
                i18n.translate("dialog.save.failed_text", error=exc),
            )
            return
        self._mark_clean()

    def save_project_as(self) -> None:
        """Ouvre un dialogue pour choisir un chemin ``.kut`` et enregistre le projet."""
        self._flush_subtitle_history_record()
        if self.current_project_path is not None:
            default_path = self.current_project_path
        else:
            default_path = os.path.join(
                os.path.expanduser("~"), export_file_name(self.project.name, "kut", fallback=i18n.translate("dialog.save.default_name"))
            )
        path, _ = _main_window().QFileDialog.getSaveFileName(
            self,
            i18n.translate("dialog.save.as_title"),
            default_path,
            i18n.translate("dialog.filter.project"),
        )
        if not path:
            return
        if not path.lower().endswith(".kut"):
            path = path + ".kut"
        try:
            save_project(self.project, path)
            discard_autosave(path)
        except (OSError, ValueError) as exc:
            _main_window().QMessageBox.critical(
                self,
                i18n.translate("dialog.save.failed_title"),
                i18n.translate("dialog.save.failed_text", error=exc),
            )
            return
        self.current_project_path = path
        self._lock_project(path)
        self._mark_clean()

    def open_project_file(self) -> None:
        """Ouvre un dialogue et charge un projet ``.kut`` sélectionné."""
        default_dir = os.path.expanduser("~")
        path, _ = _main_window().QFileDialog.getOpenFileName(
            self,
            i18n.translate("dialog.open.title"),
            default_dir,
            i18n.translate("dialog.filter.project"),
        )
        if not path:
            return
        self._load_project_from_path(path)

    def open_external_project(self, path: str) -> None:
        """Ouvre un projet que le système a confié à l'application (Finder, double-clic, ligne de commande)."""
        self._load_project_from_path(path)
        self.raise_()
        self.activateWindow()

    def _load_project_from_path(self, path: str) -> None:
        """Charge ``path`` et remplace ``self.project`` uniquement en cas de succès.

        Toutes les erreurs de chargement (fichier absent, JSON invalide,
        format/version non supportés, **ou structure interne incomplète
        qui lèverait un ``TypeError`` lors de la désérialisation des
        dataclasses**) sont traitées de la même façon : un message
        d'erreur est affiché et l'état courant de l'application reste
        intact.
        """
        if not self._confirm_discard_changes():
            return
        self._finalize_pending_edit_sessions()
        # Le verrou se prend AVANT la première lecture : un script qui écrirait le projet pendant qu'on le charge (ou qu'une boîte de
        # récupération attend l'utilisateur) serait sinon écrasé par l'instantané périmé que la fenêtre installe ensuite.
        candidate_lock = self._acquire_project_lock(path)
        try:
            loaded = load_project(path)
        except Exception as exc:  # noqa: BLE001 - quoi qu'il arrive, un fichier abîmé ne doit rien casser
            self._discard_candidate_lock(candidate_lock)
            _main_window().QMessageBox.critical(
                self,
                i18n.translate("dialog.open.failed_title"),
                i18n.translate("dialog.open.failed_text", path=path, error=exc),
            )
            return
        restored_autosave = False
        if autosave_is_newer(path):
            answer = _main_window().QMessageBox.question(
                self,
                i18n.translate("dialog.recover.title"),
                i18n.translate("dialog.recover.text"),
                _main_window().QMessageBox.Yes | _main_window().QMessageBox.No,
                _main_window().QMessageBox.Yes,
            )
            if answer == _main_window().QMessageBox.Yes:
                try:
                    loaded = load_project(str(sidecar_path(path)))
                    restored_autosave = True
                except Exception as exc:  # noqa: BLE001 - idem pour la sauvegarde automatique
                    _main_window().QMessageBox.critical(
                        self,
                        i18n.translate("dialog.recover.failed_title"),
                        i18n.translate("dialog.recover.failed_text", error=exc),
                    )
        self._release_open_project()
        self.project = loaded
        self.current_project_path = path
        self._adopt_project_lock(candidate_lock)
        # Les étiquettes de couleur des médias ne sont pas dans le fichier : sondées en arrière-plan, pour que le moniteur ne les attende jamais.
        from core.media_probe import warm_color_labels

        warm_color_labels([a.path for a in self.project.media_assets if a.media_type == "video" and a.color_matrix == "auto"])
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
        self._reset_sequence_navigation()
        if restored_autosave:
            self._refresh_undo_redo_state()
        else:
            self._mark_clean()
        # Références cassées ou cycles : signalés, jamais corrigés en silence.
        self._report_sequence_issues()

    def launch_export(self):
        """Export en une étape : choisir le fichier, ajouter à la file, lancer."""
        self._enqueue_current_export(start=True)

    def enqueue_export(self):
        """Ajoute l'export courant à la file de rendu sans le lancer."""
        self._enqueue_current_export(start=False)

    def _enqueue_current_export(self, *, start: bool):
        """Ajoute le preset courant à la file ; retourne le job, ou ``None``.

        Le projet est copié dans un instantané au moment de l'ajout : on
        peut continuer à monter (ou fermer le projet) pendant que la file
        rend la version ajoutée.
        """
        if self._export_refused_ui():                 # lecture seule (Store) : pas de dialogue, pas de job
            return None
        spec = self.export_panel.current_spec()
        path = self._ask_export_path(spec)
        if not path:
            return None
        try:
            job = self.render_queue.enqueue(
                self.project,
                spec,
                path,
                master_gain_db=self._master_gain_db,
                master_muted=self._master_muted,
                playhead_seconds=float(self.playhead_seconds),
            )
        except (ValueError, OSError, KeyError) as exc:
            self.export_panel.mark_export_error(i18n.translate("render.export.invalid", error=exc))
            return None
        if start:
            self.render_queue.start_job(job.id)
        else:
            self.export_panel.set_status(
                i18n.translate("render.added", name=job.name), "ready"
            )
        warning = self.empty_slot_warning()
        if warning:                                   # template pas encore rempli : les cartes partent à l'export
            self.statusBar().showMessage(warning, 8000)
        return job

    def _ask_export_path(self, spec) -> str:
        path, _ = _main_window().QFileDialog.getSaveFileName(
            self,
            i18n.translate("render.export.save_title"),
            os.path.join(
                default_export_directory(), export_file_name(getattr(self.project, "name", ""), spec.container)
            ),
            i18n.translate("dialog.filter.video", ext=spec.container),
        )
        return path

    def get_render_plan(self, at: float | None = None) -> RenderPlan:
        """Construit le :class:`RenderPlan` du projet courant.

        Le plan décrit fidèlement la timeline (positions, trims, trous,
        ordre des pistes, clips activés). C'est désormais l'entrée
        unique du moteur d'export.

        ``at`` : instant de la timeline ramené à l'origine du plan (voir
        :func:`core.playhead_snapshot.project_at_playhead`), pour n'extraire qu'une image
        sans payer le coût de tout ce qui précède.
        """
        from core.playhead_snapshot import project_at_playhead

        return build_render_plan(
            self.project if at is None else project_at_playhead(self.project, at),
            master_gain_db=self._master_gain_db,
            master_muted=self._master_muted,
        )

    def cancel_export(self):
        """Annule le rendu en cours (file de rendu ou export direct du moteur)."""
        current = self.render_queue.current_job
        if current is not None:
            self.render_queue.cancel(current.id)
        else:
            self.export_engine.cancel()

    def _on_render_run_finished(self, summary: dict) -> None:
        """Résumé de fin d'exécution : statut du panneau et message à l'utilisateur."""
        completed = int(summary.get("completed", 0))
        failed = int(summary.get("failed", 0))
        cancelled = int(summary.get("cancelled", 0))
        if failed:
            failed_job = next(
                (j for j in reversed(self.render_queue.jobs) if j.error_message), None
            )
            self.export_panel.mark_job_failed(failed_job.name if failed_job else "")
        elif completed:
            self.export_panel.mark_export_finished()
        else:
            self.export_panel.mark_export_cancelled()
        total = completed + failed + cancelled
        if not completed and total <= 1:
            return  # l'échec ou l'annulation d'un job unique est déjà visible dans la file
        if total == 1:
            text = i18n.translate("render.summary.one", path=summary["outputs"][0])
        else:
            text = i18n.translate(
                "render.summary.many", completed=completed, failed=failed, cancelled=cancelled
            )
        _main_window().QMessageBox.information(
            self, i18n.translate("render.summary.title"), text
        )

    def _confirm_close_during_render(self) -> bool:
        """Demande confirmation si un rendu est en cours ; ``False`` annule la fermeture."""
        queue = getattr(self, "render_queue", None)
        if queue is None or not queue.is_busy:
            return True
        from core.render_job import JobStatus

        waiting = sum(1 for job in queue.jobs if job.status is JobStatus.WAITING)
        text = i18n.translate("render.close.text")
        if waiting:
            text += i18n.translate("render.close.waiting", count=waiting)
        text += i18n.translate("render.close.question")
        answer = _main_window().QMessageBox.question(
            self, i18n.translate("render.close.title"), text
        )
        return answer == _main_window().QMessageBox.Yes

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
        cancel_tracking = getattr(self, "_cancel_tracking_jobs", None)
        if cancel_tracking is not None:
            cancel_tracking()
        cancel_flow = getattr(self, "_cancel_flow_analysis", None)
        if cancel_flow is not None:
            cancel_flow()
        release_gpu = getattr(self, "_release_gpu_for_project_change", None)
        if release_gpu is not None:
            release_gpu()
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
