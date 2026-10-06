"""Méthodes de ``MainWindow`` regroupées : animation (images-clés).

Ce mixin ne contient que le **câblage** entre l'inspecteur, la timeline, le
Graph Editor, les raccourcis et :mod:`core.keyframe_editing` : chaque action
modifie le projet par une opération pure, enregistre **une** entrée
d'historique, puis rafraîchit les vues. Les glisser continus (timeline,
Graph Editor) n'enregistrent qu'à la fin du geste.
"""

from __future__ import annotations

import logging

from core.animation import InterpolationType
from core.animation_targets import get_target, targets_for_clip
from core.time_map import SPEED_PROPERTY
from core.time_ops import move_speed_points, remove_speed_points
from core.keyframe_editing import (
    AnimationClipboard,
    KeyframeRef,
    add_keyframe,
    can_paste,
    copy_keyframes,
    find_clip,
    keyframe_by_id,
    move_keyframes,
    next_keyframe_time,
    paste_keyframes,
    previous_keyframe_time,
    remove_keyframe_at,
    remove_keyframes,
    set_animation_enabled,
    set_interpolation,
    set_keyframe_time,
    set_keyframe_values,
    set_tangents,
)
from ui import i18n

LOGGER = logging.getLogger(__name__)


class AnimationMixin:
    """Mixin de ``MainWindow`` : keyframes, navigation, copier/coller, Graph Editor."""

    # ------------------------------------------------------------------
    # État
    # ------------------------------------------------------------------

    def _init_animation(self) -> None:
        self.keyframe_selection: set[KeyframeRef] = set()
        self.active_animation_property: str | None = None
        self.animation_clipboard = AnimationClipboard(())
        self.graph_editor = None

    def _animation_clip(self):
        """Clip vidéo / graphique sélectionné (porteur de propriétés animables), sinon ``None``."""
        timeline = getattr(self, "timeline_panel", None)
        clip_id = getattr(timeline, "selected_clip_id", None) if timeline is not None else None
        if not clip_id:
            return None
        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return None
        return clip if self._animation_properties(clip) else None

    def _track_type_of(self, clip) -> str:
        for track in self.project.tracks:
            if any(item is clip for item in track.clips):
                return track.type
        return ""

    def _animation_properties(self, clip) -> list[str]:
        # Propriétés de **ce** clip : transform, plus celles de son type de
        # calque (texte, forme) et de ses masques.
        return [target.id for target in targets_for_clip(clip, self._track_type_of(clip))]

    def _local_playhead(self, clip) -> float:
        local = float(self.playhead_seconds) - float(clip.timeline_start)
        return min(max(0.0, local), float(clip.duration))

    def _playhead_in_clip(self, clip) -> bool:
        return clip.timeline_start - 1e-6 <= self.playhead_seconds <= clip.timeline_start + clip.duration + 1e-6

    def _command_properties(self, clip) -> list[str]:
        """Propriétés visées par une commande : l'active, sinon les animées, sinon toutes."""
        available = self._animation_properties(clip)
        if self.active_animation_property in available:
            return [self.active_animation_property]
        animated = [pid for pid in available if get_target(pid).get_keyframes(clip)]
        return animated or available

    def set_active_animation_property(self, property_id: str | None) -> None:
        self.active_animation_property = property_id
        editor = self.graph_editor
        if editor is not None and property_id:
            editor.set_property(property_id)

    # ------------------------------------------------------------------
    # Après chaque édition
    # ------------------------------------------------------------------

    def _after_animation_edit(self, clip_id: str, label: str, *, record: bool = True) -> None:
        if record:
            self._finalize_transform_session()
            self._record_history(label)
        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            clip = None
        self._prune_keyframe_selection()
        self._reload_timeline_preserving_selection(clip_id)
        if clip is not None:
            self.properties_panel.update_transform_from_clip(
                clip.transform, clip.transform_keyframes, playhead_seconds=self.playhead_seconds
            )
        self._sync_preview_to_timeline()
        try:
            self._invalidate_preview_for_clip(clip_id)
        except Exception:
            LOGGER.warning(
                "Invalidation de l'aperçu en échec après la modification des images-clés du clip %s : des segments périmés peuvent s'afficher",
                clip_id, exc_info=True,
            )
        self._mark_dirty()
        self._refresh_graph_editor()

    def _prune_keyframe_selection(self) -> None:
        kept = set()
        for ref in self.keyframe_selection:
            try:
                clip = find_clip(self.project, ref.clip_id)
            except KeyError:
                continue
            if keyframe_by_id(clip, ref.property_id, ref.keyframe_id) is not None:
                kept.add(ref)
        self.set_keyframe_selection(kept)

    def set_keyframe_selection(self, refs) -> None:
        self.keyframe_selection = set(refs)
        timeline = getattr(self, "timeline_panel", None)
        if timeline is not None and hasattr(timeline, "set_keyframe_selection"):
            timeline.set_keyframe_selection(self.keyframe_selection)
        editor = self.graph_editor
        if editor is not None:
            editor.set_selection(self.keyframe_selection)

    def _refresh_graph_editor(self) -> None:
        editor = self.graph_editor
        if editor is not None:
            editor.refresh()

    # ------------------------------------------------------------------
    # Inspecteur
    # ------------------------------------------------------------------

    def on_animation_toggled(self, clip_id: str, property_id: str, enabled: bool) -> None:
        clip = find_clip(self.project, clip_id)
        set_animation_enabled(self.project, clip_id, property_id, enabled, self._local_playhead(clip))
        self.set_active_animation_property(property_id)
        label = i18n.translate("animation.menu.enable") if enabled else i18n.translate("history.animation.disable")
        self._after_animation_edit(clip_id, label)

    def on_keyframe_navigation(self, property_id: str | None, direction: int) -> bool:
        """Place la tête sur le keyframe précédent / suivant (propriété ou commande)."""
        clip = self._animation_clip()
        if clip is None:
            return False
        if property_id:
            self.set_active_animation_property(property_id)
        props = [property_id] if property_id else self._command_properties(clip)
        local = float(self.playhead_seconds) - float(clip.timeline_start)
        target = (next_keyframe_time if direction > 0 else previous_keyframe_time)(clip, props, local)
        if target is None:
            return False
        self.seek_to_position(float(clip.timeline_start) + target)
        return True

    def on_keyframe_interpolation_requested(self, property_id: str | None, interpolation: str) -> int:
        """Change l'interpolation de la sélection, sinon des keyframes sous la tête."""
        refs = self._keyframes_for_command(property_id)
        if not refs:
            return 0
        changed = set_interpolation(self.project, refs, interpolation)
        if changed:
            self._after_animation_edit(next(iter(refs)).clip_id, i18n.translate("history.keyframes.interpolation"))
        return changed

    def _keyframes_for_command(self, property_id: str | None = None) -> list[KeyframeRef]:
        if self.keyframe_selection and property_id is None:
            return sorted(self.keyframe_selection, key=lambda r: (r.clip_id, r.property_id, r.keyframe_id))
        clip = self._animation_clip()
        if clip is None:
            return []
        local = self._local_playhead(clip)
        props = [property_id] if property_id else self._command_properties(clip)
        refs = []
        for pid in props:
            keyframe = get_target(pid).curve(clip).keyframe_at(local, tolerance=1e-3)
            if keyframe is not None:
                refs.append(KeyframeRef(clip.id, pid, keyframe.id))
        return refs

    # ------------------------------------------------------------------
    # Commandes (raccourcis configurables)
    # ------------------------------------------------------------------

    def add_keyframe_at_playhead(self) -> int:
        clip = self._animation_clip()
        if clip is None or not self._playhead_in_clip(clip):
            return 0
        local = self._local_playhead(clip)
        try:
            local = min(max(0.0, self._snap_local(clip, local)), clip.duration)
        except Exception:
            LOGGER.debug(
                "Magnétisme de la nouvelle image-clé en échec : position non aimantée conservée",
                exc_info=True,
            )
        props = self._command_properties(clip)
        for pid in props:
            add_keyframe(self.project, clip.id, pid, local)
        self._after_animation_edit(clip.id, i18n.translate("history.keyframes.add"))
        return len(props)

    def _snap_local(self, clip, local: float) -> float:
        from core.keyframe_editing import snap_to_frame

        return snap_to_frame(clip, local, float(self.project.fps or 30.0))

    def remove_keyframes_command(self) -> int:
        """Supprime la sélection, sinon les keyframes sous la tête de lecture."""
        refs = self._keyframes_for_command()
        if not refs:
            return 0
        clip = find_clip(self.project, refs[0].clip_id)
        speed = [ref for ref in refs if ref.property_id == SPEED_PROPERTY]
        others = [ref for ref in refs if ref.property_id != SPEED_PROPERTY]
        count = 0
        try:
            if speed:
                count += remove_speed_points(self.project, speed, mode=self._ripple_mode())
            if others:
                count += remove_keyframes(self.project, others, local_time=self._local_playhead(clip))
        except (KeyError, ValueError) as error:
            self._report_edit_refused(error)
            return 0
        self.set_keyframe_selection(set())
        self._after_animation_edit(refs[0].clip_id, i18n.translate("history.keyframes.remove"))
        return count

    def select_all_keyframes(self) -> int:
        clip = self._animation_clip()
        if clip is None:
            return 0
        refs = {
            KeyframeRef(clip.id, pid, keyframe.id)
            for pid in self._command_properties(clip)
            for keyframe in get_target(pid).get_keyframes(clip)
        }
        self.set_keyframe_selection(refs)
        return len(refs)

    def copy_animation(self, property_id: str | None = None) -> bool:
        clip = self._animation_clip()
        if clip is None:
            return False
        selected = {r.keyframe_id for r in self.keyframe_selection if r.clip_id == clip.id}
        props = [property_id] if property_id else (
            sorted({r.property_id for r in self.keyframe_selection if r.clip_id == clip.id})
            or self._command_properties(clip)
        )
        clipboard = copy_keyframes(self.project, clip.id, props, ids=selected or None)
        if clipboard:
            self.animation_clipboard = clipboard
        return bool(clipboard)

    def paste_animation(self, property_id: str | None = None) -> int:
        clip = self._animation_clip()
        clipboard = self.animation_clipboard
        if clip is None or not clipboard or not self._playhead_in_clip(clip):
            return 0
        target = property_id if property_id and clipboard.is_single_property else None
        if target is not None and not can_paste(clipboard, target):
            self._show_animation_message(i18n.translate("animation.paste_incompatible"))
            return 0
        created = paste_keyframes(self.project, clip.id, clipboard, self._local_playhead(clip),
                                  target_property=target)
        if created:
            self.set_keyframe_selection(created)
            self._after_animation_edit(clip.id, i18n.translate("history.keyframes.paste"))
        return len(created)

    def _show_animation_message(self, text: str) -> None:
        bar = self.statusBar() if hasattr(self, "statusBar") else None
        if bar is not None:
            bar.showMessage(text, 4000)

    def delete_selection_or_clip(self) -> None:
        """Suppr : les keyframes sélectionnés d'abord, sinon le clip."""
        if self.keyframe_selection:
            self.remove_keyframes_command()
            return
        self.delete_selected_clip_with_check()

    def _animation_shortcut_handlers(self) -> dict:
        def interpolation(kind: InterpolationType):
            return lambda: self.on_keyframe_interpolation_requested(None, kind.value)

        handlers = {
            "keyframe_add": self.add_keyframe_at_playhead,
            "keyframe_remove": self.remove_keyframes_command,
            "keyframe_previous": lambda: self.on_keyframe_navigation(None, -1),
            "keyframe_next": lambda: self.on_keyframe_navigation(None, 1),
            "keyframe_select_all": self.select_all_keyframes,
            "keyframe_copy": self.copy_animation,
            "keyframe_paste": self.paste_animation,
            "graph_editor": self.toggle_graph_editor,
        }
        for kind in InterpolationType:
            handlers[f"keyframe_interpolation_{kind.value}"] = interpolation(kind)
        return handlers

    # ------------------------------------------------------------------
    # Timeline et Graph Editor
    # ------------------------------------------------------------------

    def on_timeline_keyframes_selected(self, refs, additive: bool) -> None:
        refs = set(refs)
        if additive:
            refs = self.keyframe_selection ^ refs
        self.set_keyframe_selection(refs)
        if refs:
            self.set_active_animation_property(next(iter(sorted(refs, key=lambda r: r.property_id))).property_id)

    def on_keyframes_move_requested(self, refs, delta_seconds: float) -> None:
        """Fin d'un glisser : une seule entrée d'historique pour tout le groupe."""
        refs = list(refs)
        if not refs or abs(delta_seconds) < 1e-9:
            return
        fps = float(self.project.fps or 30.0)
        speed = [ref for ref in refs if ref.property_id == SPEED_PROPERTY]
        others = [ref for ref in refs if ref.property_id != SPEED_PROPERTY]
        try:
            # Un point de vitesse change la durée du clip : transaction du temps (durée mini, ripple), jamais le déplacement nu.
            if speed:
                move_speed_points(self.project, speed, float(delta_seconds), fps=fps, mode=self._ripple_mode())
            if others:
                move_keyframes(self.project, others, float(delta_seconds), fps=fps)
        except (KeyError, ValueError) as error:
            self._report_edit_refused(error)
            self._after_animation_edit(refs[0].clip_id, "", record=False)             # la timeline revient à l'état réel
            return
        self._after_animation_edit(refs[0].clip_id, i18n.translate("history.keyframes.move"))

    def on_graph_edit(self, label: str, clip_id: str, *, record: bool) -> None:
        """Le Graph Editor a modifié le projet ; ``record`` à la fin d'un geste."""
        self._after_animation_edit(clip_id, label, record=record)

    def graph_set_keyframe(self, ref: KeyframeRef, *, time: float | None = None, value=None) -> None:
        if time is not None:
            set_keyframe_time(self.project, ref, time)
        if value is not None:
            set_keyframe_values(self.project, {ref: value})

    def graph_set_tangents(self, ref: KeyframeRef, **kwargs) -> None:
        set_tangents(self.project, ref, **kwargs)

    def toggle_graph_editor(self) -> None:
        editor = self.graph_editor
        if editor is not None and editor.isVisible():
            editor.close()
            return
        self.open_graph_editor(self.active_animation_property)

    def open_graph_editor(self, property_id: str | None = None):
        from ui.graph_editor import GraphEditorWindow

        if self.graph_editor is None:
            self.graph_editor = GraphEditorWindow(self, parent=self)
        if property_id:
            self.active_animation_property = property_id
        self.graph_editor.refresh()
        if property_id:
            self.graph_editor.set_property(property_id)
        self.graph_editor.show()
        self.graph_editor.raise_()
        return self.graph_editor

    # Compatibilité : ancien chemin de suppression d'un keyframe par instant.
    def remove_keyframe_at_playhead(self, property_id: str) -> bool:
        clip = self._animation_clip()
        if clip is None:
            return False
        removed = remove_keyframe_at(self.project, clip.id, property_id, self._local_playhead(clip))
        if removed:
            self._after_animation_edit(clip.id, i18n.translate("history.keyframes.remove"))
        return removed
