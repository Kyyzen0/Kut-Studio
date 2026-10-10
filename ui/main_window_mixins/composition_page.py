"""Page Composition de la fenêtre principale : la composition nodale du clip choisi.

La page montre le clip choisi s'il est un clip de composition : ses nœuds, les réglages du nœud choisi, et dans le
viewer sa sortie ou, à la demande, l'image du nœud choisi (segments fidèles, à l'arrêt). Chaque geste (ajouter,
supprimer, relier, régler) est une étape d'historique ; une rafale sur un même champ n'en fait qu'une. Commandes :
convertir des calques en composition (sens unique), créer une composition vide, ouvrir une composition (double-clic
sur son clip).
"""

from __future__ import annotations

import logging

from core.composition import (
    CompositionView,
    EffectsNode,
    GradeNode,
    GraphicNode,
    KeyNode,
    MaskNode,
    MediaNode,
    MergeNode,
    OutputNode,
    SolidNode,
    TransformNode,
)
from core.composition_ops import CompositionError, convert_to_composition, create_composition
from core.compositing import Mask
from core.node_graph import NodeGraphError
from core.workspace_state import PAGE_COMPOSITION
from ui.i18n import translate

LOGGER = logging.getLogger(__name__)

NEW_COMPOSITION_SECONDS = 5.0
_PREFIXES = {"media": "m", "graphic": "g", "solid": "s", "transform": "t", "merge": "f", "mask": "k", "key": "i",
             "effects": "e", "grade": "c"}


class CompositionPageMixin:
    def _init_composition_page(self) -> None:
        from ui.composition_page.panel import CompositionPanel

        self._comp_node_id: str | None = None
        self._comp_viewing = False                          # le viewer montre le nœud choisi plutôt que la sortie
        self.composition_panel = CompositionPanel()
        panel = self.composition_panel
        nodes = panel.nodes
        nodes.node_selected.connect(self.on_comp_node_selected)
        nodes.add_requested.connect(self.on_comp_add)
        nodes.remove_requested.connect(self.on_comp_remove)
        nodes.connect_requested.connect(self.on_comp_connect)
        nodes.disconnect_requested.connect(self.on_comp_disconnect)
        nodes.rewire_requested.connect(self.on_comp_rewire)
        panel.inspector.changed.connect(self.on_comp_node_changed)
        panel.view_toggled.connect(self.on_comp_view_toggled)
        panel.new_requested.connect(self.new_composition)
        panel.shown.connect(self._refresh_composition_panel)
        self.properties_panel.clip_shown.connect(self._refresh_composition_panel)
        self.timeline_panel.composition_open_requested.connect(self.open_composition)
        self.timeline_panel.composition_convert_requested.connect(self.convert_selection_to_composition)

    def _composition_shortcut_handlers(self) -> dict:
        return {
            "composition_convert": lambda: self.convert_selection_to_composition(),
            "composition_new": lambda: self.new_composition(),
        }

    # -- lecture ------------------------------------------------------------------------------------------------

    def _composition_clip(self):
        """Le clip de composition affiché par l'inspecteur (``None`` : un autre clip, ou aucun)."""
        from core.timeline_operations import find_clip

        view = getattr(self.properties_panel, "selected_clip", None)
        if view is None or not getattr(view, "is_composition", False):
            return None
        try:
            clip = find_clip(self.project, view.id)
        except KeyError:
            return None
        return clip if clip.composition is not None else None

    def _refresh_composition_panel(self, *_args) -> None:
        from shiboken6 import isValid

        panel = getattr(self, "composition_panel", None)
        if panel is None or not isValid(panel) or not panel.isVisible():
            return
        clip = self._composition_clip()
        if clip is None:
            panel.set_target(None, None, editable=False)
            return
        _clip, track = self._find_clip_and_track(clip.id)
        assets = [(asset.id, asset.name) for asset in self.project.media_assets
                  if asset.media_type in ("video", "image")]
        panel.set_target(clip.composition, self._comp_node_id, editable=track is not None and not track.locked,
                         assets=assets)
        panel.set_viewing(self._comp_viewing)

    def _composition_preview_overrides(self) -> dict | None:
        """Page Composition, nœud montré : ``{clip: CompositionView(nœud)}`` pour les segments fidèles."""
        panel = getattr(self, "composition_panel", None)
        if not getattr(self, "_comp_viewing", False) or panel is None or not panel.isVisible():
            return None                                     # (aussi avant la construction de la page)
        clip = self._composition_clip()
        node_id = self._comp_node_id
        if clip is None or not node_id or not clip.composition.graph.has_node(node_id):
            return None
        if isinstance(clip.composition.graph.node(node_id), OutputNode):
            return None
        return {clip.id: CompositionView(node_id)}

    # -- édition ------------------------------------------------------------------------------------------------

    def _edit_composition(self, change, label_key: str, *, merge_key: str | None = None) -> bool:
        """Applique ``change`` (composition → composition) au clip affiché : une étape d'historique (fusionnée avec
        la précédente de même ``merge_key``), aperçu et panneau à jour."""
        clip = self._composition_clip()
        if clip is None:
            return False
        _found, track = self._find_clip_and_track(clip.id)
        if track is None or track.locked:
            return False
        try:
            updated = change(clip.composition)
        except (NodeGraphError, ValueError) as exc:
            self._report_edit_refused(exc)
            return False
        if updated == clip.composition:
            return False
        clip.composition = updated
        self._record_history(translate(label_key), merge_key=merge_key)
        self._invalidate_preview_for_clip(clip.id)
        self._reload_timeline_preserving_selection(clip.id)
        self._sync_preview_to_timeline()
        self._refresh_composition_panel()
        return True

    def _new_comp_node(self, kind: str, composition):
        graph = composition.graph
        node_id = graph.next_id(_PREFIXES[kind])
        if kind == "media":
            asset = next((item for item in self.project.media_assets if item.media_type in ("video", "image")), None)
            duration = composition.duration if asset is None or asset.media_type == "image" \
                else min(float(asset.duration), composition.duration)
            return MediaNode(node_id, asset.id if asset is not None else "", 0.0, 0.0, duration)
        if kind == "graphic":
            from core.graphics import GraphicType, graphic_defaults

            graphic = graphic_defaults(GraphicType.TEXT, project_width=self.project.width,
                                       project_height=self.project.height)
            return GraphicNode(node_id, graphic, 0.0, composition.duration)
        return {
            "solid": lambda: SolidNode(node_id),
            "transform": lambda: TransformNode(node_id),
            "merge": lambda: MergeNode(node_id),
            "mask": lambda: MaskNode(node_id, (Mask(),)),
            "key": lambda: KeyNode(node_id),
            "effects": lambda: EffectsNode(node_id),
            "grade": lambda: GradeNode(node_id),
        }[kind]()

    def on_comp_node_selected(self, node_id: str) -> None:
        self._comp_node_id = node_id
        self._refresh_composition_panel()
        clip = self._composition_clip()
        if self._comp_viewing and clip is not None:
            self._invalidate_preview_for_clip(clip.id)          # l'aperçu suit le nœud choisi
            self._sync_preview_to_timeline()

    def on_comp_add(self, kind: str, after: str) -> None:
        added: list[str] = []

        def change(composition):
            node = self._new_comp_node(kind, composition)
            added.append(node.id)
            return composition.with_graph(composition.graph.inserted(node, after or None))

        clip = self._composition_clip()
        if clip is not None and self._edit_composition(change, "history.comp.add"):
            self._comp_node_id = added[0]
            self._refresh_composition_panel()

    def on_comp_remove(self, node_id: str) -> None:
        from dataclasses import replace

        def change(composition):
            graph = composition.graph.without(node_id)
            kept = {mask.id for node in graph.nodes if isinstance(node, MaskNode) for mask in node.masks}
            animation = tuple(kf for kf in composition.animation if not (
                kf.property_name.startswith("mask.") and kf.property_name.split(".")[1] not in kept))
            return replace(composition.with_graph(graph), animation=animation)

        if self._edit_composition(change, "history.comp.remove"):
            self._comp_node_id = None
            self._refresh_composition_panel()

    def on_comp_connect(self, source: str, target: str, port: int) -> None:
        self._edit_composition(lambda c: c.with_graph(c.graph.connected(source, target, port)), "history.comp.connect")

    def on_comp_disconnect(self, target: str, port: int) -> None:
        self._edit_composition(lambda c: c.with_graph(c.graph.disconnected(target, port)), "history.comp.disconnect")

    def on_comp_rewire(self, source: str, old_target: str, old_port: int, target: str, port: int) -> None:
        self._edit_composition(
            lambda c: c.with_graph(c.graph.disconnected(old_target, old_port).connected(source, target, port)),
            "history.comp.connect")

    def on_comp_node_changed(self, node, field: str) -> None:
        clip = self._composition_clip()
        if clip is None:
            return
        self._edit_composition(lambda c: c.with_graph(c.graph.with_node(node)), "history.comp.edit",
                               merge_key=f"comp:{clip.id}:{node.id}:{field}")

    def on_comp_view_toggled(self, shown: bool) -> None:
        self._comp_viewing = bool(shown)
        clip = self._composition_clip()
        if clip is not None:
            self._invalidate_preview_for_clip(clip.id)
            self._sync_preview_to_timeline()

    # -- commandes ----------------------------------------------------------------------------------------------

    def _composition_status(self, message: str) -> None:
        self.statusBar().showMessage(message, 6000)

    def convert_selection_to_composition(self):
        """« Convertir en composition » : les clips sélectionnés deviennent les nœuds d'un clip de composition."""
        timeline = self.timeline_panel
        ids = list(timeline.selected_clip_ids) or ([timeline.selected_clip_id] if timeline.selected_clip_id else [])
        if not ids:
            self._composition_status(translate("status.comp.refused", reason=translate("comp.panel.no_clip")))
            return None
        try:
            result = convert_to_composition(self.project, ids)
        except (KeyError, CompositionError) as error:
            self._composition_status(translate("status.comp.refused", reason=str(error)))
            return None
        self._record_history(translate("history.comp.convert"))
        self._after_sequence_edit(select_clip_id=result.clip.id)
        message = translate("status.comp.converted", count=len(ids))
        if result.dropped:
            message += " " + translate("status.comp.dropped", items=" ; ".join(result.dropped))
        self._composition_status(message)
        self._comp_node_id = None
        self.open_composition(result.clip.id)
        return result

    def new_composition(self):
        """Une composition vide à la tête de lecture, sur la piste vidéo du clip choisi (sinon la première libre)."""
        timeline = self.timeline_panel
        at = float(self.playhead_seconds)
        tracks = [track for track in self.project.tracks if track.type == "video" and not track.locked]
        selected = timeline.selected_clip_id
        if selected:
            _clip, track = self._find_clip_and_track(selected)
            if track is not None and track in tracks:
                tracks.remove(track)
                tracks.insert(0, track)
        clip = None
        for track in tracks:
            try:
                clip = create_composition(self.project, track.id, at, NEW_COMPOSITION_SECONDS)
                break
            except CompositionError:
                continue
        if clip is None:
            self._composition_status(translate("status.comp.refused",
                                               reason=translate("comp.panel.no_clip")))
            return None
        self._record_history(translate("history.comp.new"))
        self._after_sequence_edit(select_clip_id=clip.id)
        self._comp_node_id = None
        self.open_composition(clip.id)
        return clip

    def open_composition(self, clip_id: str | None = None) -> None:
        """Ouvre la page Composition sur le clip ``clip_id`` (double-clic, menu de la timeline)."""
        if clip_id:
            self.timeline_panel.select_clip(clip_id)
            self.on_clip_selected(clip_id)
        self.switch_page(PAGE_COMPOSITION)
        self._refresh_composition_panel()
