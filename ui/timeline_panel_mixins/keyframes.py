"""Images-clés dans la timeline : sélection et déplacement temporel.

Les clips affichent leurs images-clés (losanges en bas du clip). La timeline
ne fait que la manipulation **temporelle** : sélectionner (Maj pour ajouter
ou retirer), glisser avec aimantation (images, tête de lecture, bords,
marqueurs, autres images-clés), supprimer. Les courbes se règlent dans le
Graph Editor. Les changements passent par des signaux : la fenêtre applique
:func:`core.keyframe_editing.move_keyframes` et enregistre une seule entrée
d'historique par geste.
"""

from __future__ import annotations

from core.keyframe_editing import KeyframeRef


class KeyframesTimelineMixin:
    """Mixin de ``TimelinePanel`` : sélection et glisser des images-clés."""

    def _init_keyframe_state(self) -> None:
        self.selected_keyframes: set[KeyframeRef] = set()
        self.keyframe_drag_delta = 0.0

    def set_keyframe_selection(self, refs) -> None:
        refs = set(refs)
        if refs == self.selected_keyframes:
            return
        touched = {ref.clip_id for ref in refs | self.selected_keyframes}
        self.selected_keyframes = refs
        for clip_id in touched:
            widget = self.clip_widgets.get(clip_id)
            if widget is not None:
                widget.update()

    def keyframe_selected(self, ref: KeyframeRef) -> bool:
        return ref in self.selected_keyframes

    def press_keyframes(self, refs: list[KeyframeRef], additive: bool) -> None:
        """Clic sur un losange : sélection (Maj = ajouter / retirer)."""
        if not additive and all(ref in self.selected_keyframes for ref in refs):
            return  # garder la sélection multiple pour la glisser d'un bloc
        self.keyframes_selected.emit(list(refs), bool(additive))

    def preview_keyframe_drag(self, anchor_global_time: float, raw_delta: float, anchor_id: str) -> float:
        """Décalage aimanté (images de la timeline, puis points d'aimantation)."""
        proposed = max(0.0, anchor_global_time + raw_delta)
        snapped = self.snap_time(proposed, anchor_id=anchor_id)
        fps = float(getattr(self.project, "fps", 0.0) or 0.0)
        if fps > 0:
            snapped = round(snapped * fps) / fps
        self.keyframe_drag_delta = snapped - anchor_global_time
        for ref_clip in {ref.clip_id for ref in self.selected_keyframes}:
            widget = self.clip_widgets.get(ref_clip)
            if widget is not None:
                widget.update()
        return self.keyframe_drag_delta

    def finish_keyframe_drag(self) -> None:
        delta, self.keyframe_drag_delta = self.keyframe_drag_delta, 0.0
        self.snap_line_x = None
        if self.selected_keyframes and abs(delta) > 1e-9:
            self.keyframes_move_requested.emit(list(self.selected_keyframes), delta)
        for widget in self.clip_widgets.values():
            widget.update()
