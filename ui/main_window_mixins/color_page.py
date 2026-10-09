"""Méthodes de ``MainWindow`` regroupées : page Couleur (pages, nœuds d'étalonnage, roues).

Le **nœud courant** (``_color_node_id``) est celui que choisit l'éditeur de nœuds : les roues, les réglages de
l'inspecteur, les courbes, la LUT et les presets s'appliquent à lui (:meth:`ColorGradingMixin._color_node_for`). Il
reste choisi d'un clip à l'autre : un clip qui a un nœud de ce nom le montre, les autres montrent leur premier nœud.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QToolButton, QWidget

from core.color_grading import ColorGradingError, ColorGradingService
from core.color_nodes import MixerKind, as_graph
from core.color_render import Highlight
from core.node_graph import NodeGraphError
from core.workspace_state import PAGE_COLOR, PAGE_EDIT, PAGES
from ui.i18n import translate

_COLOR_TAB = 1                                       # onglet Couleur de l'inspecteur


class ColorPageMixin:
    """Mixin de ``MainWindow`` (page Couleur)."""

    def _init_color_page(self) -> None:
        from ui.color_page.clip_strip import ClipStrip
        from ui.color_page.panel import ColorPanel

        self._color_node_id: str | None = None
        self._color_highlight = False                       # « afficher la sélection » (moniteur seulement)
        self._color_compare: float | None = None            # avant / après : part gauche sans étalonnage
        self._scopes_on_edit_page: bool | None = None
        self.color_panel = ColorPanel()
        editor = self.color_panel.nodes
        editor.node_selected.connect(self.on_color_node_selected)
        editor.add_requested.connect(self.on_color_node_add)
        editor.branch_requested.connect(self.on_color_node_branch)
        editor.remove_requested.connect(self.on_color_node_remove)
        editor.toggle_requested.connect(self.on_color_node_toggle)
        editor.rename_requested.connect(self.on_color_node_rename)
        editor.move_requested.connect(self.on_color_node_move)
        editor.reset_requested.connect(self.on_color_node_reset)
        self.color_panel.wheel_changed.connect(self.on_color_wheel_changed)
        self.color_panel.qualifier_changed.connect(self.on_color_qualifier_changed)
        self.color_panel.highlight_toggled.connect(self.on_color_highlight_toggled)
        self.color_panel.compare_toggled.connect(lambda shown: self._set_color_compare(0.5 if shown else None))
        self.preview_panel.overlay.compare_moved.connect(self._set_color_compare)
        self.color_panel.shown.connect(self._refresh_color_panel)
        self.properties_panel.clip_shown.connect(self._refresh_color_panel)
        self.clip_strip = ClipStrip()
        self.clip_strip.thumbnail_provider = self.timeline_panel.clip_thumbnail
        self.clip_strip.clip_requested.connect(self.timeline_panel.select_clip)
        self.clip_strip.shown.connect(self._refresh_clip_strip)
        self.timeline_panel.previews_arrived.connect(self.clip_strip.refresh_thumbnails)
        self.properties_panel.clip_shown.connect(self._refresh_clip_strip)

    # -- pages --------------------------------------------------------------------------------------------------

    def _build_page_switcher(self) -> QWidget:
        """Montage | Couleur, au centre de la barre supérieure : des puces, la page affichée cochée."""
        switcher = QWidget()
        switcher.setObjectName("page_switcher")
        switcher.setAccessibleName(translate("page.switcher"))
        layout = QHBoxLayout(switcher)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.page_buttons: dict[str, QToolButton] = {}
        group = QButtonGroup(switcher)
        group.setExclusive(True)
        for page in PAGES:
            button = QToolButton()
            button.setText(translate(f"page.{page}"))
            button.setObjectName("chipButton")
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.setCheckable(True)
            button.setChecked(page == self.workspace.page)
            button.setToolTip(translate(f"page.{page}.tip"))
            button.clicked.connect(lambda _checked=False, target=page: self.switch_page(target))
            group.addButton(button)
            layout.addWidget(button)
            self.page_buttons[page] = button
        self._page_button_group = group
        return switcher

    def _retranslate_page_switcher(self) -> None:
        for page, button in self.page_buttons.items():
            button.setText(translate(f"page.{page}"))
            button.setToolTip(translate(f"page.{page}.tip"))
        self.color_panel.retranslate()
        self.clip_strip.retranslate()

    def switch_page(self, page: str) -> None:
        """Passe à la page ``page`` : sa disposition, l'onglet Couleur et les scopes sur la page Couleur."""
        self._finalize_color_history()
        if self.workspace.switch_page(page):
            self._sync_workspace_menu()
        for name, button in self.page_buttons.items():
            button.setChecked(name == self.workspace.page)
        rail = self.side_rail
        if self.workspace.page == PAGE_COLOR:
            rail.set_active("color")
            self.properties_panel._select_inspector_tab(_COLOR_TAB)
            self._show_page_scopes(True)
            self._refresh_color_panel()
        else:
            if rail.active() == "color":
                rail.set_active("edit")
            self._show_page_scopes(False)
            self._set_color_compare(None)                   # la comparaison est un outil de la page Couleur

    def _show_page_scopes(self, color_page: bool) -> None:
        """Scopes affichés sur la page Couleur ; en revenant au Montage, l'état qu'on y avait laissé."""
        if color_page:
            if self._scopes_on_edit_page is None:
                self._scopes_on_edit_page = self._scopes_visible
            wanted = True
        else:
            wanted = self._scopes_visible if self._scopes_on_edit_page is None else self._scopes_on_edit_page
            self._scopes_on_edit_page = None
        if wanted != self._scopes_visible:
            self.toggle_scopes_visible(persist=False)

    def _page_shortcut_handlers(self) -> dict:
        return {"page_edit": lambda: self.switch_page(PAGE_EDIT), "page_color": lambda: self.switch_page(PAGE_COLOR)}

    # -- panneau ------------------------------------------------------------------------------------------------

    def _color_clip_id(self) -> str | None:
        """Clip vidéo affiché par l'inspecteur : celui que la page Couleur étalonne."""
        view = getattr(self.properties_panel, "selected_clip", None)
        if view is None or getattr(view, "track_type", None) != "video":
            return None
        return view.id

    def _refresh_color_panel(self, *_args) -> None:
        """Réaffiche les nœuds et les roues du clip affiché (après une sélection, une édition, un Annuler…).

        Panneau fermé (page Montage) ou déjà détruit (fermeture de la fenêtre) : rien ; il se remet à jour quand il
        s'affiche (:attr:`ColorPanel.shown`)."""
        from shiboken6 import isValid

        panel = getattr(self, "color_panel", None)
        if panel is None or not isValid(panel) or not isValid(panel.nodes) or not panel.isVisible():
            return
        clip_id = self._color_clip_id()
        try:
            graph = ColorGradingService().get_graph(self.project, clip_id) if clip_id else None
        except ColorGradingError:
            graph = None
        if graph is None:
            panel.set_target(None, None, editable=False)
            return
        locked = bool(getattr(self.properties_panel.selected_clip, "locked", False))
        panel.set_target(graph, graph.node_or_first(self._color_node_id).id, editable=not locked)
        panel.set_highlight(self._color_highlight)
        panel.set_compare(self._color_compare is not None)
        gpu = getattr(self.preview_panel, "gpu_view", None) is not None
        panel.qualifier.highlight_button.setEnabled(gpu and not locked)
        panel.qualifier.highlight_button.setToolTip(translate(
            "color.qualifier.highlight_tip" if gpu else "color.qualifier.highlight_unavailable"))
        panel.compare_button.setEnabled(gpu)
        panel.compare_button.setToolTip(translate("color.compare" if gpu else "color.qualifier.highlight_unavailable"))

    def _set_color_compare(self, split: float | None) -> None:
        """Comparaison avant / après du moniteur : ``split`` (part gauche sans étalonnage) ou ``None`` (arrêtée)."""
        self._color_compare = split
        self.preview_panel.set_grade_split(split)
        panel = getattr(self, "color_panel", None)
        if panel is not None:
            panel.set_compare(split is not None)

    def _monitor_color_grade(self, clip):
        """Ce que le moniteur montre pour ``clip`` : son étalonnage, ou la sélection du nœud courant quand *Afficher
        la sélection* est coché pour le clip de la page Couleur (jamais à l'export)."""
        value = getattr(clip, "color_grade", None)
        panel = getattr(self, "color_panel", None)
        if not self._color_highlight or panel is None or not panel.isVisible():
            return value
        if clip is None or getattr(clip, "id", None) != self._color_clip_id():
            return value
        graph = as_graph(value)
        node = graph.node_or_first(self._color_node_id)
        return Highlight(graph, node.id) if node.qualifier is not None and node.qualifier.enabled else value

    def _refresh_clip_strip(self, *_args) -> None:
        """Les plans vidéo de la séquence affichée, dans l'ordre du montage, et le plan de l'inspecteur."""
        from shiboken6 import isValid

        from core.color_nodes import ColorNodeGraph
        from core.gpu_grade import grade_is_active
        from ui.color_page.clip_strip import StripClip

        strip = getattr(self, "clip_strip", None)
        if strip is None or not isValid(strip) or not strip.isVisible():
            return
        views = sorted((view for view in self.timeline_panel.clip_views if view.track_type == "video"),
                       key=lambda view: (view.start, view.track_id))
        clips = [StripClip(view.id, view.label, grade_is_active(view.color_grade),
                           len(view.color_grade.correctors) if isinstance(view.color_grade, ColorNodeGraph) else 1)
                 for view in views]
        strip.set_clips(clips, self._color_clip_id())

    # -- nœuds --------------------------------------------------------------------------------------------------

    def on_color_node_selected(self, node_id: str) -> None:
        self._finalize_color_history()
        self._color_node_id = node_id
        self.properties_panel.set_color_node(node_id)
        self._refresh_color_panel()
        clip_id = self._color_clip_id()
        if self._color_highlight and clip_id is not None:
            self._refresh_color_monitor(clip_id)            # la sélection montrée est celle du nouveau nœud

    def _edit_color_nodes(self, change, label_key: str) -> bool:
        """Applique ``change`` (graphe → graphe) aux nœuds du clip affiché : une étape d'historique, moniteur à jour."""
        clip_id = self._color_clip_id()
        if clip_id is None:
            return False
        _clip, track = self._find_clip_and_track(clip_id)
        if track is None or track.locked:                    # comme pour les réglages : piste verrouillée, rien
            return False
        self._finalize_color_history()
        try:
            ColorGradingService().edit_nodes(self.project, clip_id, change)
        except (ColorGradingError, NodeGraphError) as exc:
            self._report_edit_refused(exc)
            return False
        self.properties_panel.set_color_node(self._color_node_id)
        self._record_history(translate(label_key))
        self._reload_timeline_preserving_selection(clip_id)
        self._refresh_color_monitor(clip_id)
        self._refresh_color_panel()
        return True

    def on_color_node_add(self, after_id: str) -> None:
        def add(graph):
            added, node_id = graph.with_node_after(after_id or None)
            self._color_node_id = node_id                   # le nouveau nœud devient le nœud courant
            return added

        previous = self._color_node_id
        if not self._edit_color_nodes(add, "history.color.node_add"):
            self._color_node_id = previous

    def on_color_node_branch(self, node_id: str, kind: str) -> None:
        """Un nœud parallèle ou de calque à côté de ``node_id`` (Alt+P, Alt+L) ; il devient le nœud courant."""
        def branch(graph):
            branched, new_id = graph.with_branch(node_id, MixerKind(kind))
            self._color_node_id = new_id
            return branched

        previous = self._color_node_id
        if not self._edit_color_nodes(branch, f"history.color.node_{kind}"):
            self._color_node_id = previous

    def on_color_node_remove(self, node_id: str) -> None:
        self._edit_color_nodes(lambda graph: graph.without(node_id), "history.color.node_remove")

    def on_color_node_toggle(self, node_id: str) -> None:
        clip_id = self._color_clip_id()
        if clip_id is None:
            return
        try:
            enabled = ColorGradingService().get_graph(self.project, clip_id).node(node_id).enabled
        except (ColorGradingError, NodeGraphError):
            return
        self._edit_color_nodes(
            lambda graph: graph.with_grade(node_id, graph.node(node_id).grade.with_enabled(not enabled)),
            "history.color.node_bypass" if enabled else "history.color.node_enable",
        )

    def on_color_node_rename(self, node_id: str, label: str) -> None:
        self._edit_color_nodes(lambda graph: graph.with_label(node_id, label), "history.color.node_rename")

    def on_color_node_move(self, node_id: str, index: int) -> None:
        self._edit_color_nodes(lambda graph: graph.moved(node_id, index), "history.color.node_move")

    def on_color_node_reset(self, node_id: str) -> None:
        from core.color_grading import ColorGrade

        self._edit_color_nodes(lambda graph: graph.with_grade(node_id, ColorGrade()), "history.color.node_reset")

    def on_color_qualifier_changed(self, qualifier) -> None:
        """Le qualifieur du nœud courant change : rafale regroupée en une étape, moniteur à jour."""
        clip_id = self._color_clip_id()
        if clip_id is None:
            return
        _clip, track = self._find_clip_and_track(clip_id)
        if track is None or track.locked:
            return
        node_id = self._color_node_for(clip_id)
        try:
            ColorGradingService().edit_nodes(self.project, clip_id, lambda graph: graph.with_qualifier(node_id, qualifier))
        except (ColorGradingError, NodeGraphError) as exc:
            self._report_edit_refused(exc)
            return
        self._schedule_color_history(translate("history.color.qualifier"), clip_id)
        self._refresh_color_monitor(clip_id)

    def on_color_highlight_toggled(self, shown: bool) -> None:
        self._color_highlight = bool(shown)
        clip_id = self._color_clip_id()
        if clip_id is not None:
            self._refresh_color_monitor(clip_id)

    def on_color_wheel_changed(self, name: str, wheel) -> None:
        """Une roue du nœud courant bouge : rafale regroupée en une étape, comme les curseurs de l'inspecteur."""
        clip_id = self._color_clip_id()
        if clip_id is None:
            return
        try:
            updated = self._color_grade_of(clip_id).with_wheel(name, wheel)
        except ColorGradingError as exc:
            self._report_edit_refused(exc)
            return
        label = translate("history.color.wheel", wheel=translate(f"color.wheel.{name}"))
        self._commit_color_grade(clip_id, updated, label, coalesce=True)
