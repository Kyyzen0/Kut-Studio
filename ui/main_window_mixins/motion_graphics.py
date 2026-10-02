"""Méthodes de ``MainWindow`` : motion graphics (calques, viewer, presets).

Relie les vues (panneau Calques, viewer interactif, inspecteur) aux
opérations pures de :mod:`core.mograph_layers`. Règle commune : chaque
action modifie le projet puis enregistre **une** entrée d'historique ; un
glisser dans le viewer modifie le projet en direct et n'enregistre qu'au
relâchement.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox


LOGGER = logging.getLogger(__name__)


class MotionGraphicsMixin:
    """Mixin de ``MainWindow`` (motion graphics)."""

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _init_motion_graphics(self) -> None:
        self._layer_clipboard = None
        self._attribute_clipboard = None
        self._viewer_composited = False
        self._viewer_dragging = False
        self._viewer_timer = QTimer(self)
        self._viewer_timer.setSingleShot(True)
        self._viewer_timer.setInterval(0)
        self._viewer_timer.timeout.connect(self._refresh_viewer_graphics)
        overlay = self.preview_panel.overlay
        overlay.transform_dragged.connect(self._on_viewer_transform_dragged)
        overlay.transform_released.connect(self._on_viewer_transform_released)
        overlay.layer_clicked.connect(self._on_viewer_layer_clicked)
        overlay.guide_moved.connect(self._on_viewer_guide_moved)
        overlay.guide_released.connect(lambda _guide_id: self._commit_layer_edit("Déplacer un guide"))
        self.preview_panel.canvas_changed.connect(self._schedule_viewer_graphics)
        panel = self.layers_panel
        panel.layer_selected.connect(self._on_layer_panel_selected)
        panel.visibility_toggled.connect(self._on_layer_visibility)
        panel.lock_toggled.connect(self._on_layer_lock)
        panel.rename_requested.connect(self._on_layer_rename)
        panel.move_requested.connect(self._on_layer_move)
        panel.parent_requested.connect(self._on_layer_parent)
        panel.group_requested.connect(self.group_selected_layers)
        panel.ungroup_requested.connect(self._ungroup_layer)
        panel.add_requested.connect(self.add_layer_at_playhead)
        panel.duplicate_requested.connect(self._duplicate_layers)
        panel.delete_requested.connect(self._delete_layers)
        panel.copy_attributes_requested.connect(self._copy_layer_attributes)
        panel.paste_attributes_requested.connect(self._paste_layer_attributes)
        panel.save_preset_requested.connect(self._save_layers_as_preset)
        panel.preset_apply_requested.connect(self._apply_mograph_preset)
        panel.motion_blur_toggled.connect(self._on_layer_motion_blur)
        panel.parent_choices_provider = self._parent_choices_for
        properties = self.properties_panel
        properties.advanced_transform_changed.connect(self._on_advanced_transform_changed)
        properties.advanced_keyframe_toggled.connect(self._on_advanced_keyframe_toggled)
        properties.graphic_parent_changed.connect(self._on_layer_parent)
        properties.compositing_changed.connect(self._on_compositing_changed)
        self._reload_presets()
        self._refresh_layers_panel()

    @property
    def layers_panel(self):
        return self.project_panel.graphics_view.layers_panel

    def _mograph_shortcut_handlers(self) -> dict:
        return {
            "layer_add_text": lambda: self.add_layer_at_playhead("text", ""),
            "layer_add_shape": lambda: self.add_layer_at_playhead("shape", "rectangle"),
            "layer_add_null": lambda: self.add_layer_at_playhead("null", ""),
            "layer_add_adjustment": lambda: self.add_layer_at_playhead("adjustment", ""),
            "layer_group": lambda: self.group_selected_layers(self._selected_layer_ids()),
            "layer_ungroup": self._ungroup_selected_layer,
            "layer_copy_attributes": lambda: self._copy_layer_attributes(self._primary_selected_id()),
            "layer_paste_attributes": lambda: self._paste_layer_attributes(self._selected_layer_ids()),
            "view_safe_areas": lambda: self._toggle_viewer_flag("show_safe_areas"),
            "view_guides": lambda: self._toggle_viewer_flag("show_guides"),
            "view_grid": lambda: self._toggle_viewer_flag("show_grid"),
            "mograph_snapping": lambda: self._toggle_viewer_flag("snapping"),
        }

    def _build_layers_menu(self, menu_bar) -> QMenu:
        """Menu « Calques » : création, groupes, attributs, repères, flou."""
        from ui import i18n

        menu = QMenu(i18n.translate("menu.layers"), self)
        menu.setObjectName("layers_menu")
        add_menu = menu.addMenu("Ajouter")
        add_menu.addAction(self._command_action("layer_add_text", "menu.item.add_text_layer"))
        add_menu.addAction(self._command_action("layer_add_shape", "menu.item.add_shape_layer"))
        add_menu.addAction(self._command_action("layer_add_null", "menu.item.add_null_layer"))
        add_menu.addAction(self._command_action("layer_add_adjustment", "menu.item.add_adjustment_layer"))
        menu.addSeparator()
        menu.addAction(self._command_action("layer_group", "menu.item.group_layers"))
        menu.addAction(self._command_action("layer_ungroup", "menu.item.ungroup_layers"))
        menu.addSeparator()
        copy_layers = self._labelled_action("menu.item.copy_layers")
        copy_layers.triggered.connect(self.copy_selected_layers)
        paste_layers = self._labelled_action("menu.item.paste_layers")
        paste_layers.triggered.connect(self.paste_layers_at_playhead)
        menu.addAction(copy_layers)
        menu.addAction(paste_layers)
        menu.addAction(self._command_action("layer_copy_attributes", "menu.item.copy_attributes"))
        menu.addAction(self._command_action("layer_paste_attributes", "menu.item.paste_attributes"))
        save_preset = self._labelled_action("menu.item.save_preset")
        save_preset.triggered.connect(lambda: self._save_layers_as_preset(self._selected_layer_ids()))
        menu.addAction(save_preset)
        menu.addSeparator()
        self._viewer_flag_actions = {}
        for command, key, flag, default in (
            ("view_safe_areas", "menu.item.safe_areas", "show_safe_areas", False),
            ("view_guides", "menu.item.show_guides", "show_guides", True),
            ("view_grid", "menu.item.show_grid", "show_grid", False),
            ("mograph_snapping", "menu.item.viewer_snapping", "snapping", True),
        ):
            action = self._command_action(command, key, checkable=True)
            action.setChecked(default)
            self._viewer_flag_actions[flag] = action
            menu.addAction(action)
        center = self._labelled_action("menu.item.show_center")
        center.setCheckable(True)
        center.toggled.connect(lambda checked: self._set_viewer_flag("show_center", checked))
        menu.addAction(center)
        guides = menu.addMenu("Guides")
        add_h = self._labelled_action("menu.item.add_guide_h")
        add_h.triggered.connect(lambda: self.add_viewer_guide("horizontal"))
        add_v = self._labelled_action("menu.item.add_guide_v")
        add_v.triggered.connect(lambda: self.add_viewer_guide("vertical"))
        lock = self._labelled_action("menu.item.lock_guides")
        lock.triggered.connect(self._lock_all_guides)
        clear = self._labelled_action("menu.item.clear_guides")
        clear.triggered.connect(self.clear_viewer_guides)
        for action in (add_h, add_v, lock, clear):
            guides.addAction(action)
        menu.addSeparator()
        self.motion_blur_action = self._labelled_action("menu.item.motion_blur")
        self.motion_blur_action.setCheckable(True)
        self.motion_blur_action.setChecked(True)
        self.motion_blur_action.toggled.connect(self.set_sequence_motion_blur)
        menu.addAction(self.motion_blur_action)
        settings = self._labelled_action("menu.item.motion_blur_settings")
        settings.triggered.connect(self._edit_motion_blur_settings)
        menu.addAction(settings)
        menu.addSeparator()
        tracking = self._labelled_action("menu.item.tracking_panel")
        tracking.triggered.connect(lambda: self.show_tracking_panel())
        menu.addAction(tracking)
        menu_bar.addMenu(menu)
        self._translated_menus.append((menu, "menu.layers"))
        return menu

    # ------------------------------------------------------------------
    # Sélection
    # ------------------------------------------------------------------

    def _primary_selected_id(self) -> str:
        selected = getattr(self.properties_panel, "selected_clip", None)
        return getattr(selected, "id", "") or ""

    def _selected_layer_ids(self) -> list[str]:
        ids = list(self.layers_panel.selected_ids())
        if not ids:
            primary = self._primary_selected_id()
            if primary:
                ids = [primary]
        timeline_ids = getattr(self.timeline_panel, "selected_clip_ids", None)
        if callable(timeline_ids):
            timeline_ids = timeline_ids()
        for clip_id in sorted(timeline_ids or ()):
            if clip_id not in ids:
                ids.append(clip_id)
        from core.mograph_layers import LayerError, find_layer

        result = []
        for clip_id in ids:
            try:
                find_layer(self.project, clip_id)
            except LayerError:
                continue
            result.append(clip_id)
        return result

    def _select_layer(self, clip_id: str) -> None:
        self._restore_clip_selection(clip_id)
        self._refresh_layers_panel()
        self._refresh_mograph_inspector()
        self._refresh_viewer_graphics()

    def _on_layer_panel_selected(self, clip_id: str) -> None:
        if clip_id and clip_id != self._primary_selected_id():
            self._finalize_pending_edit_sessions()
            self._restore_clip_selection(clip_id)
            self._refresh_mograph_inspector()
            self._schedule_viewer_graphics()

    def _on_viewer_layer_clicked(self, clip_id: str) -> None:
        if clip_id != self._primary_selected_id():
            self._finalize_pending_edit_sessions()
            self._select_layer(clip_id)
            self._refresh_viewer_graphics()

    # ------------------------------------------------------------------
    # Rafraîchissements
    # ------------------------------------------------------------------

    def _after_preview_sync(self) -> None:
        """Appelé après chaque synchronisation du moniteur (tête de lecture)."""
        panel = getattr(self, "preview_panel", None)
        if panel is None or not hasattr(self, "_viewer_timer"):
            return
        panel.set_canvas_size(self.project.width, self.project.height)
        self.layers_panel.set_playhead(float(self.playhead_seconds))
        self._refresh_mograph_inspector()
        self._schedule_viewer_graphics()
        refresh_tracking = getattr(self, "_refresh_tracking_ui", None)
        if refresh_tracking is not None:
            refresh_tracking()

    def _schedule_viewer_graphics(self) -> None:
        timer = getattr(self, "_viewer_timer", None)
        if timer is not None:
            timer.start()

    def _refresh_viewer_graphics(self) -> None:
        """Calques à la tête de lecture + repères + poignées du calque sélectionné."""
        panel = self.preview_panel
        t = float(self.playhead_seconds)
        composited = bool(getattr(self, "_viewer_composited", False))
        try:
            from core.render_plan import build_render_plan

            plan = build_render_plan(
                self.project, window=(t, t + 1e-3), window_index=self._ensure_timeline_index(),
            )
        except Exception:
            plan = None
        drawable = plan is not None and any(layer.role == "draw" for layer in plan.graphics_layers)
        panel.set_graphics_present(drawable)
        if plan is not None and not composited and plan.graphics_layers:
            from core.mograph_raster import MographRenderer, scene_for_plan
            from core.motion_blur import preview_quality

            rect = panel.canvas_rect()
            playing = bool(getattr(self, "is_playing", False))
            scale = 0.5 if playing else 1.0
            width = max(2, int(rect.width() * scale))
            height = max(2, int(rect.height() * scale))
            try:
                scene = scene_for_plan(plan)
                renderer = MographRenderer(
                    scene, width, height, fps=float(self.project.fps),
                    # Flou de mouvement adapté au niveau d'aperçu (jamais en lecture).
                    quality=preview_quality(self.runtime.preview_divisor(), playing=playing),
                    motion_blur=getattr(plan, "motion_blur", None),
                )
                panel.set_mograph_image(renderer.render(scene.top_level(), t))
                if panel.gpu_active:
                    self._sync_gpu_adjustments(plan, scene, t)
            except Exception as exc:  # un calque fautif ne bloque jamais le viewer
                LOGGER.warning("Aperçu des calques indisponible : %s", exc)
                panel.set_mograph_image(None)
            panel.set_mograph_visible(True)
        else:
            panel.set_mograph_image(None)
            panel.set_mograph_visible(False)
        self._refresh_viewer_overlay(t)

    def _sync_gpu_adjustments(self, plan, scene, t: float) -> None:
        """Calques d'effets actifs → moniteur GPU (effets sur la vidéo, couverture exacte)."""
        from core.graphics import GraphicType
        from core.mograph_raster import MographRenderer

        panel = self.preview_panel
        layers = [
            layer for layer in plan.graphics_layers
            if getattr(getattr(layer, "graphic", None), "type", None) == GraphicType.ADJUSTMENT
            and layer.timeline_start <= t < layer.timeline_end and getattr(layer, "effects", ())
        ]
        if not layers:
            panel.set_adjustments(())
            return
        width, height = panel.gpu_render_size()
        renderer = MographRenderer(scene, width, height, fps=float(self.project.fps), quality="draft")
        adjustments = []
        for layer in layers:
            coverage = renderer.render_coverage(layer.clip_id, t)
            key = f"adjust:{layer.clip_id}:{width}x{height}:{renderer.coverage_key(layer.clip_id, t)!r}"
            adjustments.append((key, tuple(layer.effects), coverage, 1.0))
        panel.set_adjustments(adjustments)

    def _refresh_viewer_overlay(self, t: float) -> None:
        from core.graphics import CONTAINER_TYPES, GraphicOverlay
        from core.mograph_layers import scene_for_project
        from ui.viewer_overlay import SelectionGeometry

        overlay = self.preview_panel.overlay
        overlay.set_guides(getattr(self.project.active_sequence, "guides", ()))
        try:
            scene = scene_for_project(self.project)
        except Exception:
            overlay.set_selection(None)
            overlay.set_layer_boxes(())
            return
        boxes = []
        for clip_id in scene.top_level() + [m for g in scene.layers for m in scene.members(g)]:
            layer = scene.layers.get(clip_id)
            graphic = getattr(layer, "graphic", None)
            if not isinstance(graphic, GraphicOverlay) or graphic.type in CONTAINER_TYPES:
                continue
            evaluated = scene.evaluate(clip_id, t)
            if evaluated.active and not graphic.locked:
                boxes.append((clip_id, evaluated.world, evaluated.box))
        boxes.sort(key=lambda item: _stack_rank(scene, item[0]))
        overlay.set_layer_boxes(boxes)
        selected = getattr(self.properties_panel, "selected_clip", None)
        clip_id = getattr(selected, "id", None)
        tracking_mode = getattr(self, "_tracking_mode_active", None)
        if tracking_mode is not None and tracking_mode():
            overlay.set_selection(None)  # les trackers remplacent les poignées du clip
            return
        if (
            clip_id is None
            or getattr(selected, "track_type", None) not in ("video", "graphics")
            or clip_id not in scene.layers
            or getattr(selected, "sequence_id", "")
        ):
            overlay.set_selection(None)
            return
        layer = scene.layers[clip_id]
        if not (layer.timeline_start - 1e-9 <= t < layer.timeline_end):
            overlay.set_selection(None)
            return
        evaluated = scene.evaluate(clip_id, t)
        parent = scene.parent_of(clip_id)
        parent_world = scene.evaluate(parent, t).world if parent else (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        graphic = evaluated.graphic
        from core.graphics import LayerLayout
        from core.visual_effects import TRANSFORM_PROPERTY_NAMES

        values = {name: getattr(evaluated.transform, name) for name in TRANSFORM_PROPERTY_NAMES}
        values.update(self._user_transform_values(clip_id, t))
        editable = not bool(getattr(selected, "locked", False))
        overlay.set_selection(SelectionGeometry(
            clip_id=clip_id, world=evaluated.world, parent_world=parent_world, box=evaluated.box,
            values=values, editable=editable,
            anchor_editable=graphic is None or graphic.layout is LayerLayout.ANCHOR,
        ))

    def _user_transform_values(self, clip_id: str, t: float) -> dict:
        """Valeurs **saisies** d'un clip dont le tracking pilote le transform.

        Le cadre du viewer suit le rendu (liaisons, stabilisation), mais un
        glisser modifie la saisie : partir des valeurs rendues ajouterait le
        mouvement suivi une seconde fois. Vide pour un clip sans tracking.
        """
        from core.timeline_operations import find_clip
        from core.visual_effects import TRANSFORM_PROPERTY_NAMES, evaluate_transform

        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return {}
        tracking = getattr(clip, "tracking", None)
        if tracking is None or not tracking.drives_rendering:
            return {}
        user = evaluate_transform(clip.transform, clip.transform_keyframes, t - clip.timeline_start, clip.duration)
        return {name: getattr(user, name) for name in TRANSFORM_PROPERTY_NAMES}

    def _refresh_layers_panel(self) -> None:
        if not hasattr(self, "project_panel"):
            return
        from core.mograph_layers import layer_tree
        from core.timeline_evaluator import timeline_duration

        try:
            nodes = layer_tree(self.project)
        except Exception:
            nodes = []
        selected = self._primary_selected_id()
        names = {}
        for track in self.project.tracks:
            for clip in track.clips:
                names[clip.id] = clip.label or clip.id
        motion_blur = {
            clip.id: bool(getattr(clip.graphic, "motion_blur", False))
            for track in self.project.tracks if track.type == "graphics" for clip in track.clips
            if clip.graphic is not None
        }
        self.layers_panel.set_layers(
            nodes, selected_ids=[selected] if selected else [],
            duration=max(1.0, timeline_duration(self.project)),
            playhead=float(self.playhead_seconds), names=names, motion_blur=motion_blur,
        )
        action = getattr(self, "motion_blur_action", None)
        if action is not None:
            settings = getattr(self.project.active_sequence, "motion_blur", None)
            action.blockSignals(True)
            action.setChecked(bool(getattr(settings, "enabled", True)))
            action.blockSignals(False)

    def _refresh_mograph_inspector(self) -> None:
        """Valeurs avancées et parent du calque sélectionné (tête de lecture)."""
        selected = getattr(self.properties_panel, "selected_clip", None)
        clip_id = getattr(selected, "id", None)
        if clip_id is None or getattr(selected, "track_type", None) not in ("video", "graphics"):
            return
        from core.keyframe_editing import curve_of
        from core.project_model import Clip
        from core.timeline_operations import find_clip
        from core.visual_effects import ADVANCED_TRANSFORM_PROPERTIES, evaluate_transform

        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return
        if not isinstance(clip, Clip):
            return
        local = float(self.playhead_seconds) - clip.timeline_start
        values = evaluate_transform(clip.transform, clip.transform_keyframes, local, clip.duration)
        animated = set()
        keyed = set()
        for name in ADVANCED_TRANSFORM_PROPERTIES:
            curve = curve_of(clip, name)
            if curve:
                animated.add(name)
                if curve.keyframe_at(round(local, 6), 1e-3) is not None:
                    keyed.add(name)
        self.properties_panel.advanced_transform.set_values(
            {name: getattr(values, name) for name in ADVANCED_TRANSFORM_PROPERTIES},
            animated=animated, keyed=keyed,
        )
        if getattr(selected, "track_type", None) == "graphics" and clip.graphic is not None:
            self.properties_panel.graphics_group.set_parent_choices(
                self._parent_choices_for(clip_id), clip.graphic.parent_id
            )

    def _parent_choices_for(self, clip_id: str) -> list[tuple[str, str]]:
        from core.mograph_layers import parent_candidates

        try:
            return parent_candidates(self.project, clip_id)
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Édition générique
    # ------------------------------------------------------------------

    def _apply_property_values(self, clip_id: str, values: dict) -> bool:
        """Applique des valeurs animables : image-clé si la propriété est animée."""
        from core import keyframe_editing
        from core.animation_targets import get_target
        from core.timeline_operations import find_clip

        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return False
        local = keyframe_editing.snap_to_frame(
            clip, float(self.playhead_seconds) - clip.timeline_start, float(self.project.fps)
        )
        changed = False
        for name, value in values.items():
            try:
                target = get_target(name)
                value = target.spec.clamp(value)
                if keyframe_editing.is_animated(clip, name):
                    keyframe_editing.set_value_at(self.project, clip_id, name, local, value)
                else:
                    target.set_static(clip, value)
                changed = True
            except (KeyError, ValueError) as exc:
                self._report_edit_refused(exc)
        return changed

    def _after_live_change(self, clip_id: str) -> None:
        """Retour visuel immédiat (sans historique)."""
        self._mark_dirty()
        try:
            self._invalidate_preview_for_clip(clip_id)
        except Exception:
            pass
        from core.timeline_operations import find_clip

        try:
            clip = find_clip(self.project, clip_id)
            self.properties_panel.update_transform_from_clip(
                clip.transform, clip.transform_keyframes, playhead_seconds=self.playhead_seconds,
            )
        except KeyError:
            pass
        self._sync_preview_to_timeline()
        self._refresh_mograph_inspector()

    def _commit_layer_edit(self, label: str, *, select: str | None = None) -> None:
        """Une entrée d'historique + rechargement des vues."""
        self._finalize_pending_edit_sessions()
        self._record_history(label)
        self._mark_dirty()
        target = select if select is not None else self._primary_selected_id()
        if target:
            self._reload_timeline_preserving_selection(target)
        else:
            self._reload_timeline_preserving_selection()
        self._update_timeline_duration()
        self._refresh_project_library()
        self._refresh_layers_panel()
        self._sync_preview_to_timeline()
        self._refresh_mograph_inspector()

    def _layer_operation(self, label: str, operation, *, select: str | None = None):
        from core.mograph_layers import LayerError

        self._finalize_pending_edit_sessions()
        try:
            result = operation()
        except (LayerError, ValueError, KeyError) as exc:
            self.statusBar().showMessage(str(exc), 5000)
            return None
        self._commit_layer_edit(label, select=select)
        return result

    # ------------------------------------------------------------------
    # Viewer : manipulation directe
    # ------------------------------------------------------------------

    def _on_viewer_transform_dragged(self, clip_id: str, values: dict) -> None:
        if not self._viewer_dragging:
            self._finalize_pending_edit_sessions()
            self._viewer_dragging = True
        if self._apply_property_values(clip_id, values):
            self._after_live_change(clip_id)

    def _on_viewer_transform_released(self, clip_id: str, label: str) -> None:
        self._viewer_dragging = False
        self._commit_layer_edit(label, select=clip_id)

    def _on_viewer_guide_moved(self, guide_id: str, position: float) -> None:
        from core.canvas_guides import move_guide

        try:
            move_guide(self.project.active_sequence, guide_id, position)
        except (KeyError, ValueError):
            return
        self.preview_panel.overlay.set_guides(self.project.active_sequence.guides)

    def add_viewer_guide(self, orientation: str) -> None:
        from core.canvas_guides import add_guide

        add_guide(self.project.active_sequence, orientation, 0.5)
        self._set_viewer_flag("show_guides", True)
        self._commit_layer_edit("Ajouter un guide")

    def clear_viewer_guides(self) -> None:
        from core.canvas_guides import clear_guides

        if clear_guides(self.project.active_sequence):
            self._commit_layer_edit("Effacer les guides")

    def _lock_all_guides(self) -> None:
        from core.canvas_guides import set_guide_locked

        sequence = self.project.active_sequence
        if not sequence.guides:
            return
        lock = not all(g.locked for g in sequence.guides)
        for guide in list(sequence.guides):
            set_guide_locked(sequence, guide.id, lock)
        self._commit_layer_edit("Verrouiller les guides")

    def _toggle_viewer_flag(self, flag: str) -> None:
        overlay = self.preview_panel.overlay
        self._set_viewer_flag(flag, not bool(getattr(overlay, flag, False)))

    def _set_viewer_flag(self, flag: str, value: bool) -> None:
        self.preview_panel.overlay.set_display(**{flag: value})
        action = getattr(self, "_viewer_flag_actions", {}).get(flag)
        if action is not None and action.isChecked() != bool(value):
            action.blockSignals(True)
            action.setChecked(bool(value))
            action.blockSignals(False)

    # ------------------------------------------------------------------
    # Inspecteur
    # ------------------------------------------------------------------

    def _on_advanced_transform_changed(self, clip_id: str, name: str, value) -> None:
        if not bool(getattr(self, "_transform_session_active", False)):
            self._finalize_graphic_history()
        if self._apply_property_values(clip_id, {name: value}):
            self._schedule_transform_history("Modifier la transformation avancée")
            self._after_live_change(clip_id)

    def _on_advanced_keyframe_toggled(self, clip_id: str, name: str) -> None:
        from core import keyframe_editing
        from core.timeline_operations import find_clip

        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return
        local = keyframe_editing.snap_to_frame(
            clip, float(self.playhead_seconds) - clip.timeline_start, float(self.project.fps)
        )

        def toggle():
            if not keyframe_editing.remove_keyframe_at(self.project, clip_id, name, local):
                keyframe_editing.add_keyframe(self.project, clip_id, name, local)

        self._layer_operation("Image-clé (transformation avancée)", toggle, select=clip_id)

    def _on_compositing_changed(self, clip_id: str, value) -> None:
        from core.timeline_operations import find_clip

        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return
        if clip.compositing == value:
            return
        if not bool(getattr(self, "_transform_session_active", False)):
            self._finalize_graphic_history()
        clip.compositing = value
        # Les images-clés d'un masque supprimé disparaissent avec lui.
        kept_ids = {mask.id for mask in value.masks}
        clip.animation = [
            kf for kf in clip.animation
            if not kf.property_name.startswith("mask.") or kf.property_name.split(".")[1] in kept_ids
        ]
        self._schedule_transform_history("Modifier le compositing")
        self._after_live_change(clip_id)

    # ------------------------------------------------------------------
    # Panneau Calques
    # ------------------------------------------------------------------

    def add_layer_at_playhead(self, kind: str, shape: str = "") -> None:
        from core.mograph_layers import add_layer

        def create():
            return add_layer(
                self.project, kind, at=float(self.playhead_seconds), duration=5.0,
                shape=shape or "rectangle",
            )

        clip = self._layer_operation("Ajouter un calque", create)
        if clip is not None:
            self._select_layer(clip.id)
            self.properties_panel._select_inspector_tab(4)

    def _on_layer_visibility(self, clip_id: str, visible: bool) -> None:
        from core.mograph_layers import set_layer_visible

        self._layer_operation(
            "Afficher le calque" if visible else "Masquer le calque",
            lambda: set_layer_visible(self.project, clip_id, visible),
        )

    def _on_layer_lock(self, clip_id: str, locked: bool) -> None:
        from core.mograph_layers import set_layer_locked

        self._layer_operation(
            "Verrouiller le calque" if locked else "Déverrouiller le calque",
            lambda: set_layer_locked(self.project, clip_id, locked),
        )

    def _on_layer_rename(self, clip_id: str, name: str) -> None:
        from core.mograph_layers import rename_layer

        self._layer_operation("Renommer le calque", lambda: rename_layer(self.project, clip_id, name))

    def _on_layer_motion_blur(self, clip_id: str, enabled: bool) -> None:
        from core.mograph_layers import set_motion_blur

        self._layer_operation("Flou de mouvement du calque", lambda: set_motion_blur(self.project, clip_id, enabled))

    def _on_layer_move(self, clip_id: str, group_id: str, rank: int) -> None:
        from core.mograph_layers import find_layer, move_into_group, reorder_layer, siblings

        def move():
            _track, clip = find_layer(self.project, clip_id)
            if clip.graphic.group_id != group_id:
                move_into_group(self.project, clip_id, group_id, at_time=float(self.playhead_seconds))
            target = len(siblings(self.project, clip_id)) - 1 if rank < 0 else rank
            reorder_layer(self.project, clip_id, target)

        self._layer_operation("Réordonner les calques", move, select=clip_id)

    def _on_layer_parent(self, clip_id: str, parent_id: str) -> None:
        from core.mograph_layers import set_parent

        self._layer_operation(
            "Changer le parent" if parent_id else "Détacher du parent",
            lambda: set_parent(self.project, clip_id, parent_id, at_time=float(self.playhead_seconds)),
            select=clip_id,
        )

    def group_selected_layers(self, clip_ids: list[str]) -> None:
        from core.mograph_layers import group_layers

        if not clip_ids:
            self.statusBar().showMessage("Sélectionnez au moins un calque à grouper.", 4000)
            return
        group = self._layer_operation("Grouper les calques", lambda: group_layers(self.project, clip_ids))
        if group is not None:
            self._select_layer(group.id)

    def _ungroup_selected_layer(self) -> None:
        primary = self._primary_selected_id()
        if primary:
            self._ungroup_layer(primary)

    def _ungroup_layer(self, group_id: str) -> None:
        from core.mograph_layers import ungroup

        self._layer_operation("Dégrouper", lambda: ungroup(self.project, group_id), select="")

    def _duplicate_layers(self, clip_ids: list[str]) -> None:
        from core.mograph_layers import duplicate_layers

        created = self._layer_operation("Dupliquer les calques", lambda: duplicate_layers(self.project, clip_ids))
        if created:
            self._select_layer(created[0].id)

    def _delete_layers(self, clip_ids: list[str]) -> None:
        from core.mograph_layers import delete_layers

        self._layer_operation("Supprimer les calques", lambda: delete_layers(self.project, clip_ids), select="")
        self._reset_selection_and_inspector()

    def copy_selected_layers(self) -> None:
        from core.mograph_layers import LayerError, copy_layers

        ids = self._selected_layer_ids()
        if not ids:
            return
        try:
            self._layer_clipboard = copy_layers(self.project, ids)
        except LayerError as exc:
            self.statusBar().showMessage(str(exc), 4000)
            return
        self.statusBar().showMessage(f"{len(self._layer_clipboard.clips)} calque(s) copié(s).", 3000)

    def paste_layers_at_playhead(self) -> None:
        from core.mograph_layers import paste_layers

        clipboard = getattr(self, "_layer_clipboard", None)
        if clipboard is None:
            return
        created = self._layer_operation(
            "Coller les calques", lambda: paste_layers(self.project, clipboard, at=float(self.playhead_seconds)),
        )
        if created:
            self._select_layer(created[0].id)

    def _copy_layer_attributes(self, clip_id: str) -> None:
        from core.mograph_layers import copy_attributes

        if not clip_id:
            return
        try:
            self._attribute_clipboard = copy_attributes(self.project, clip_id)
        except Exception as exc:
            self.statusBar().showMessage(str(exc), 4000)
            return
        self.statusBar().showMessage("Transform, masques, effets et animation copiés.", 3000)

    def _paste_layer_attributes(self, clip_ids: list[str]) -> None:
        from core.mograph_layers import ATTRIBUTE_KINDS, paste_attributes

        clipboard = getattr(self, "_attribute_clipboard", None)
        if clipboard is None or not clip_ids:
            self.statusBar().showMessage("Copiez d'abord les attributs d'un calque.", 4000)
            return
        labels = {
            "transform": "Transform", "effects": "Effets et couleur",
            "masks": "Masques", "keyframes": "Animation (images-clés)",
        }
        choice, ok = QInputDialog.getItem(
            self, "Coller les attributs", "Attributs à coller :",
            ["Tout", *[labels[k] for k in ATTRIBUTE_KINDS]], 0, False,
        )
        if not ok:
            return
        kinds = ATTRIBUTE_KINDS if choice == "Tout" else [k for k, v in labels.items() if v == choice]
        self._layer_operation(
            "Coller les attributs", lambda: paste_attributes(self.project, clip_ids, clipboard, kinds),
        )

    # ------------------------------------------------------------------
    # Presets
    # ------------------------------------------------------------------

    def _reload_presets(self) -> None:
        from core.mograph_presets import all_presets

        try:
            presets = all_presets()
        except Exception as exc:
            LOGGER.warning("Presets de motion graphics indisponibles : %s", exc)
            presets = []
        self.layers_panel.set_presets(presets)

    def _apply_mograph_preset(self, preset) -> None:
        from core.mograph_presets import apply_preset

        created = self._layer_operation(
            f"Preset « {preset.name} »", lambda: apply_preset(self.project, preset, at=float(self.playhead_seconds)),
        )
        if created:
            top = created[-1]
            for clip in created:
                if not clip.graphic.group_id:
                    top = clip
            self._select_layer(top.id)

    def _save_layers_as_preset(self, clip_ids: list[str]) -> None:
        from core.mograph_presets import save_preset

        if not clip_ids:
            self.statusBar().showMessage("Sélectionnez les calques à enregistrer.", 4000)
            return
        name, ok = QInputDialog.getText(self, "Enregistrer comme preset", "Nom du preset :")
        if not ok or not name.strip():
            return
        try:
            save_preset(self.project, clip_ids, name.strip())
        except Exception as exc:
            QMessageBox.warning(self, "Preset", str(exc))
            return
        self._reload_presets()
        self.statusBar().showMessage(f"Preset « {name.strip()} » enregistré.", 3000)

    # ------------------------------------------------------------------
    # Flou de mouvement
    # ------------------------------------------------------------------

    def set_sequence_motion_blur(self, enabled: bool) -> None:
        from dataclasses import replace

        from core.motion_blur import MotionBlurSettings

        sequence = self.project.active_sequence
        current = getattr(sequence, "motion_blur", None) or MotionBlurSettings()
        if current.enabled == bool(enabled):
            return
        sequence.motion_blur = replace(current, enabled=bool(enabled))
        self._commit_layer_edit("Flou de mouvement de la séquence")

    def _edit_motion_blur_settings(self) -> None:
        from dataclasses import replace

        from core.motion_blur import MotionBlurSettings

        sequence = self.project.active_sequence
        current = getattr(sequence, "motion_blur", None) or MotionBlurSettings()
        angle, ok = QInputDialog.getDouble(
            self, "Flou de mouvement", "Angle d'obturation (°) :", current.shutter_angle, 0.0, 720.0, 0,
        )
        if not ok:
            return
        samples, ok = QInputDialog.getInt(
            self, "Flou de mouvement", "Échantillons à l'export (2–32) :", current.samples, 2, 32,
        )
        if not ok:
            return
        sequence.motion_blur = replace(current, shutter_angle=angle, samples=samples)
        self._commit_layer_edit("Réglages du flou de mouvement")


def _stack_rank(scene, clip_id: str) -> tuple:
    """Rang d'affichage : un membre de groupe se range au niveau de son groupe."""
    from core.mograph_scene import stack_key

    chain = [clip_id]
    group = scene.group_of(clip_id)
    while group and group not in chain:
        chain.append(group)
        group = scene.group_of(group)
    return tuple(stack_key(scene.layers[cid]) for cid in reversed(chain))


__all__ = ["MotionGraphicsMixin"]
