"""Méthodes de ``MainWindow`` regroupées : séquences et séquences imbriquées.

Toutes les opérations passent par :mod:`core.sequences` (règles métier,
cycles) et sont enregistrées dans l'historique : une seule entrée par
action utilisateur, donc un seul ``Ctrl+Z`` pour l'annuler.

Changer de séquence n'est **pas** une modification du projet : la
navigation ne crée pas d'entrée d'historique. Elle recharge la timeline,
l'aperçu, le mixeur et restaure la tête de lecture laissée dans la
séquence ouverte.
"""

from __future__ import annotations

from core.sequence_navigation import SequenceNavigator
from core.sequences import (
    SequenceError,
    SequenceInUseError,
    clamp_nested_clips,
    create_sequence,
    create_sequence_from_selection,
    delete_sequence,
    dependent_nested_clip_ids,
    duplicate_sequence,
    insert_sequence_clip,
    nested_source_time,
    rename_sequence,
    sequence_issues,
    sequence_usages,
    unique_sequence_name,
)
from core.timeline_evaluator import timeline_duration
from ui import i18n


def _main_window():
    """Module ``ui.main_window`` (les tests y remplacent les dialogues Qt)."""
    import ui.main_window as main_window

    return main_window


class SequencesMixin:
    """Mixin de ``MainWindow`` (séquences)."""

    # ------------------------------------------------------------------
    # Initialisation et rafraîchissement
    # ------------------------------------------------------------------

    def _init_sequences(self) -> None:
        self.sequence_navigator = SequenceNavigator()
        self.sequence_navigator.reset(self.project)
        timeline = self.timeline_panel
        timeline.nested_open_requested.connect(self.open_nested_clip)
        timeline.nest_selection_requested.connect(self.nest_selected_clips)
        timeline.sequence_open_requested.connect(self.open_sequence)
        timeline.sequence_back_requested.connect(self.navigate_sequence_back)
        timeline.sequence_forward_requested.connect(self.navigate_sequence_forward)
        timeline.sequence_parent_requested.connect(self.go_to_parent_sequence)
        timeline.sequence_dropped.connect(self.on_sequence_dropped)
        library = self.project_panel.sequence_view
        library.create_requested.connect(lambda: self.create_new_sequence())
        library.open_requested.connect(self.open_sequence)
        library.insert_requested.connect(lambda sid: self.insert_sequence_into_active(sid))
        library.rename_requested.connect(lambda sid: self.rename_sequence_command(sid))
        library.duplicate_requested.connect(lambda sid: self.duplicate_sequence_command(sid))
        library.delete_requested.connect(lambda sid: self.delete_sequence_command(sid))
        self._refresh_sequence_ui()

    def _reset_sequence_navigation(self) -> None:
        """Nouveau projet / chargement : la navigation repart de zéro."""
        navigator = getattr(self, "sequence_navigator", None)
        if navigator is None:
            return
        navigator.reset(self.project)
        self._refresh_sequence_ui()

    def _sequence_entries(self):
        from ui.project_panel_widgets.sequence_library import SequenceEntry

        issues: dict[str, str] = {}
        for issue in sequence_issues(self.project):
            issues.setdefault(issue.sequence_id, issue.message)
        entries = []
        for sequence in self.project.sequences:
            usages = [
                usage for usage in sequence_usages(self.project, sequence.id)
                if usage.parent_sequence_id != sequence.id
            ]
            entries.append(
                SequenceEntry(
                    id=sequence.id,
                    name=sequence.name,
                    width=int(sequence.width),
                    height=int(sequence.height),
                    fps=float(sequence.fps),
                    duration=sequence.duration,
                    usage_count=len(usages),
                    active=sequence.id == self.project.active_sequence_id,
                    issue=issues.get(sequence.id, ""),
                )
            )
        return entries

    def _refresh_sequence_ui(self) -> None:
        """Fil d'Ariane, boutons de navigation, bibliothèque et barre du haut."""
        navigator = getattr(self, "sequence_navigator", None)
        timeline = getattr(self, "timeline_panel", None)
        if navigator is None or timeline is None:
            return
        navigator.sync(self.project)
        timeline.set_sequence_navigation(
            navigator.breadcrumb(self.project),
            [(sequence.id, sequence.name) for sequence in self.project.sequences],
            can_back=navigator.can_go_back,
            can_forward=navigator.can_go_forward,
            can_parent=navigator.parent_target(self.project) is not None,
        )
        panel = getattr(self, "project_panel", None)
        if panel is not None:
            panel.set_sequences(self._sequence_entries())
        if hasattr(self, "project_label"):  # barre du haut construite plus tard
            self._update_top_bar()

    def _with_nested_clamp(self, label: str) -> str:
        """Applique la politique de durée avant un enregistrement d'historique.

        Une édition qui raccourcit une séquence ramène les clips imbriqués
        qui la dépassent (:func:`core.sequences.clamp_nested_clips`). Le
        recadrage entre dans **la même** entrée d'historique que l'édition
        et il est annoncé : visible, et annulé avec elle.
        """
        adjustments = clamp_nested_clips(self.project)
        if not adjustments:
            return label
        count = len(adjustments)
        sources = {
            clip.sequence_id
            for sequence in self.project.sequences
            for track in sequence.tracks
            for clip in track.clips
            if clip.id in {item.clip_id for item in adjustments}
        }
        names = ", ".join(
            self.project.get_sequence(sid).name for sid in sources if self.project.get_sequence(sid)
        )
        self._show_sequence_status(i18n.translate("sequence.message.clamped", count=count, name=names))
        return f"{label} (+{i18n.translate('sequence.history.clamped', count=count)})"

    def _invalidate_nested_dependents(self) -> None:
        """Invalide l'aperçu des clips qui montrent la séquence active.

        Les segments des séquences parentes ont de toute façon une nouvelle
        empreinte (le contenu imbriqué en fait partie) ; les supprimer libère
        le disque et arrête les rendus devenus inutiles. Seuls les clips
        dépendants sont touchés.
        """
        try:
            dependents = dependent_nested_clip_ids(self.project, self.project.active_sequence_id)
        except Exception:
            return
        for clip_id in dependents:
            self._invalidate_preview_for_clip(clip_id)

    def _show_sequence_status(self, message: str) -> None:
        bar = self.statusBar() if hasattr(self, "statusBar") else None
        if bar is not None:
            bar.showMessage(message, 6000)

    def _sequence_error(self, message: str) -> None:
        _main_window().QMessageBox.warning(
            self, i18n.translate("sequence.dialog.error_title"), message
        )

    def _ask_sequence_name(self, title_key: str, default: str) -> str | None:
        name, accepted = _main_window().QInputDialog.getText(
            self, i18n.translate(title_key), i18n.translate("sequence.dialog.name_label"), text=default
        )
        if not accepted:
            return None
        name = (name or "").strip()
        return name or None

    def _after_sequence_edit(self, *, select_clip_id: str | None = None) -> None:
        """Rafraîchit l'interface après une opération qui modifie le projet."""
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._refresh_project_library()
        if select_clip_id:
            self.timeline_panel.select_clip(select_clip_id)
        self._sync_preview_to_timeline()
        self._refresh_sequence_ui()
        self._mark_dirty()

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _activate_sequence(self, sequence_id: str, *, playhead: float | None = None) -> bool:
        """Ouvre ``sequence_id`` dans la timeline (sans toucher à la navigation)."""
        sequence = self.project.get_sequence(sequence_id)
        if sequence is None:
            return False
        self._finalize_pending_edit_sessions()
        if self.is_playing:
            self.stop_playback()
        navigator = self.sequence_navigator
        navigator.remember_playhead(self.project.active_sequence_id, self.playhead_seconds)
        engine = getattr(self, "preview_engine", None)
        if engine is not None and sequence_id != self.project.active_sequence_id:
            try:
                engine.cancel_all()  # rendus de la séquence quittée : obsolètes
            except Exception:
                pass
        self.project.active_sequence_id = sequence.id
        target = navigator.playhead_for(sequence.id) if playhead is None else playhead
        self.timeline_panel.set_project(self.project)
        self._reset_selection_and_inspector()
        self._update_timeline_duration()
        duration = timeline_duration(self.project)
        self.playhead_seconds = max(0.0, min(float(target), duration)) if duration > 0 else 0.0
        self.timeline_panel.set_playhead_seconds(self.playhead_seconds)
        self._sync_preview_to_timeline()
        mixer = getattr(self, "mixer_panel", None)
        if mixer is not None:
            mixer.set_project(self.project)
        self._refresh_project_library()
        return True

    def open_sequence(self, sequence_id: str) -> None:
        """Ouvre une séquence directement (bibliothèque, fil d'Ariane, menu)."""
        if sequence_id == self.project.active_sequence_id:
            self._refresh_sequence_ui()
            return
        if not self._activate_sequence(sequence_id):
            return
        self.sequence_navigator.open(sequence_id)
        self._refresh_sequence_ui()

    def open_nested_clip(self, clip_id: str | None = None) -> None:
        """Ouvre la séquence d'un clip imbriqué, à l'image correspondante."""
        clip_id = clip_id or self.timeline_panel.selected_clip_id
        if not clip_id:
            return
        clip = next(
            (item for track in self.project.tracks for item in track.clips if item.id == clip_id),
            None,
        )
        if clip is None or not clip.sequence_id:
            return
        sequence = self.project.get_sequence(clip.sequence_id)
        if sequence is None:
            self._sequence_error(i18n.translate("sequence.message.missing"))
            return
        inner = nested_source_time(clip, self.playhead_seconds)
        target = inner if inner is not None else clip.source_in
        if not self._activate_sequence(sequence.id, playhead=target):
            return
        self.sequence_navigator.descend(sequence.id)
        self._refresh_sequence_ui()

    def go_to_parent_sequence(self) -> None:
        navigator = self.sequence_navigator
        target = navigator.parent_target(self.project)
        if target is None:
            self._show_sequence_status(i18n.translate("sequence.message.no_parent"))
            return
        if self._activate_sequence(target):
            navigator.go_parent(self.project)
            self._refresh_sequence_ui()

    def navigate_sequence_back(self) -> None:
        navigator = self.sequence_navigator
        if not navigator.can_go_back:
            return
        target = navigator.back_stack[-1][-1]
        if self._activate_sequence(target):
            navigator.back()
        self._refresh_sequence_ui()

    def navigate_sequence_forward(self) -> None:
        navigator = self.sequence_navigator
        if not navigator.can_go_forward:
            return
        target = navigator.forward_stack[-1][-1]
        if self._activate_sequence(target):
            navigator.forward()
        self._refresh_sequence_ui()

    # ------------------------------------------------------------------
    # Commandes
    # ------------------------------------------------------------------

    def create_new_sequence(self, name: str | None = None, *, open_it: bool = True):
        """Crée une séquence vide (mêmes réglages et pistes que l'active)."""
        if name is None:
            name = self._ask_sequence_name(
                "sequence.dialog.new_title", unique_sequence_name(self.project, "Séquence")
            )
            if name is None:
                return None
        sequence = create_sequence(self.project, name)
        if open_it:
            # Ouverte avant l'enregistrement : l'entrée d'historique mémorise
            # cette séquence comme active, un redo y ramène.
            self.open_sequence(sequence.id)
        self._record_history(i18n.translate("sequence.history.new", name=sequence.name))
        self._refresh_sequence_ui()
        self._mark_dirty()
        return sequence

    def nest_selected_clips(self, name: str | None = None):
        """« Créer une séquence à partir de la sélection »."""
        timeline = self.timeline_panel
        ids = list(timeline.selected_clip_ids) or (
            [timeline.selected_clip_id] if timeline.selected_clip_id else []
        )
        if not ids:
            self._show_sequence_status(i18n.translate("sequence.message.no_selection"))
            return None
        if name is None:
            name = self._ask_sequence_name(
                "sequence.dialog.nest_title",
                unique_sequence_name(self.project, "Séquence imbriquée"),
            )
            if name is None:
                return None
        try:
            result = create_sequence_from_selection(self.project, ids, name)
        except (KeyError, SequenceError) as error:
            self._sequence_error(str(error))
            return None
        self._record_history(i18n.translate("sequence.history.nest", name=result.sequence.name))
        self._after_sequence_edit(select_clip_id=result.clip.id)
        return result

    def insert_sequence_into_active(
        self, sequence_id: str, track_id: str | None = None, timeline_start: float | None = None
    ):
        """Insère une séquence comme clip imbriqué dans la séquence active."""
        sequence = self.project.get_sequence(sequence_id)
        if sequence is None:
            return None
        if track_id is None:
            track_id = self._default_nest_track_id()
            if track_id is None:
                self._sequence_error(i18n.translate("sequence.message.no_track"))
                return None
        start = self.playhead_seconds if timeline_start is None else float(timeline_start)
        try:
            clip = insert_sequence_clip(self.project, sequence_id, track_id, max(0.0, start))
        except (KeyError, SequenceError) as error:
            self._sequence_error(str(error).strip("'\""))
            return None
        self._record_history(i18n.translate("sequence.history.insert", name=sequence.name))
        self._after_sequence_edit(select_clip_id=clip.id)
        return clip

    def on_sequence_dropped(self, sequence_id: str, track_id: str, timeline_start: float) -> None:
        self.insert_sequence_into_active(sequence_id, track_id, timeline_start)

    def _default_nest_track_id(self) -> str | None:
        """Piste de la sélection si compatible, sinon la première piste vidéo libre."""
        selected = self.timeline_panel.selected_clip_id
        tracks = self.project.tracks
        if selected:
            for track in tracks:
                if any(clip.id == selected for clip in track.clips):
                    if track.type in {"video", "audio"} and not track.locked:
                        return track.id
        for kind in ("video", "audio"):
            for track in tracks:
                if track.type == kind and not track.locked:
                    return track.id
        return None

    def rename_sequence_command(self, sequence_id: str | None = None, name: str | None = None):
        sequence_id = sequence_id or self.project.active_sequence_id
        sequence = self.project.get_sequence(sequence_id)
        if sequence is None:
            return None
        if name is None:
            name = self._ask_sequence_name("sequence.dialog.rename_title", sequence.name)
            if name is None or name == sequence.name:
                return None
        try:
            rename_sequence(self.project, sequence_id, name)
        except SequenceError as error:
            self._sequence_error(str(error))
            return None
        self._record_history(i18n.translate("sequence.history.rename", name=sequence.name))
        # Les clips imbriqués affichent le nom de leur séquence.
        self.timeline_panel.set_project(self.project)
        self._refresh_sequence_ui()
        self._mark_dirty()
        return sequence

    def duplicate_sequence_command(self, sequence_id: str | None = None):
        sequence_id = sequence_id or self.project.active_sequence_id
        try:
            clone = duplicate_sequence(self.project, sequence_id)
        except KeyError:
            return None
        self._record_history(i18n.translate("sequence.history.duplicate", name=clone.name))
        self._refresh_sequence_ui()
        self.project_panel.sequence_view.select_sequence(clone.id)
        self._mark_dirty()
        return clone

    def delete_sequence_command(self, sequence_id: str | None = None, *, confirmed: bool | None = None):
        """Supprime une séquence ; liste ses usages et demande confirmation."""
        sequence_id = sequence_id or self.project.active_sequence_id
        sequence = self.project.get_sequence(sequence_id)
        if sequence is None:
            return False
        name = sequence.name
        usages = [
            usage for usage in sequence_usages(self.project, sequence_id)
            if usage.parent_sequence_id != sequence_id
        ]
        if confirmed is None:
            confirmed = self._confirm_sequence_delete(name, usages)
        if not confirmed:
            return False
        was_active = sequence_id == self.project.active_sequence_id
        fallback = self.sequence_navigator.parent_target(self.project) if was_active else None
        if was_active:
            # On quitte d'abord la séquence (tête de lecture mémorisée, rendus
            # d'aperçu annulés), de préférence vers son parent.
            others = [item.id for item in self.project.sequences if item.id != sequence_id]
            target = fallback if fallback in others else (others[0] if others else None)
            if target is not None:
                self._activate_sequence(target)
        try:
            delete_sequence(self.project, sequence_id, force=True)
        except (SequenceInUseError, SequenceError) as error:
            self._sequence_error(str(error))
            return False
        self._record_history(i18n.translate("sequence.history.delete", name=name))
        self._after_sequence_edit()
        return True

    def _confirm_sequence_delete(self, name: str, usages) -> bool:
        box = _main_window().QMessageBox
        if not usages:
            answer = box.question(
                self,
                i18n.translate("sequence.dialog.delete_title"),
                i18n.translate("sequence.dialog.delete_confirm", name=name),
                box.Yes | box.No,
                box.No,
            )
            return answer == box.Yes
        places = "\n".join(
            f"• {usage.parent_sequence_name} — {usage.clip_label or usage.clip_id} "
            f"({usage.timeline_start:.2f} s)"
            for usage in usages[:12]
        )
        if len(usages) > 12:
            places += f"\n… (+{len(usages) - 12})"
        answer = box.warning(
            self,
            i18n.translate("sequence.dialog.delete_title"),
            i18n.translate(
                "sequence.dialog.delete_in_use", name=name, count=len(usages), places=places
            ),
            box.Yes | box.Cancel,
            box.Cancel,
        )
        return answer == box.Yes

    def _report_sequence_issues(self) -> None:
        """Après un chargement : signale références cassées et cycles (sans rien modifier)."""
        issues = sequence_issues(self.project)
        if not issues:
            return
        details = "\n".join(f"• {issue.message}" for issue in issues[:10])
        _main_window().QMessageBox.warning(
            self,
            i18n.translate("sequence.dialog.error_title"),
            i18n.translate("sequence.message.load_issues", count=len(issues), details=details),
        )

    def _sequence_shortcut_handlers(self) -> dict:
        return {
            "sequence_new": lambda: self.create_new_sequence(),
            "sequence_nest_selection": lambda: self.nest_selected_clips(),
            "sequence_open_nested": lambda: self.open_nested_clip(),
            "sequence_parent": self.go_to_parent_sequence,
            "sequence_back": self.navigate_sequence_back,
            "sequence_forward": self.navigate_sequence_forward,
        }
