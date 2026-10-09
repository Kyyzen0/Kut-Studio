"""Gestionnaire de l'espace de travail de Kut-Studio.

Le :class:`WorkspaceManager` est la **seule** autorité sur la disposition
de l'interface. Il possède :

- un registre ``PanelId -> (widget, zone, hôte, fenêtre)`` ;
- l'état sérialisable (:class:`~core.workspace_state.WorkspaceState`) ;
- la construction des zones dock (splitters imbriqués) ;
- les opérations : visible / maximisé / détaché / rattaché / réinitialisé.

Points de conception importants :

**Une seule instance par panneau.** Détacher un panneau ne crée rien de
nouveau : le *widget existant* est reparenté dans une
:class:`~ui.workspace.panel_host.PanelWindow`. Les signaux, le modèle de
projet et l'état d'édition restent donc partagés — il ne peut pas y avoir
deux timelines divergentes.

**Un seul état.** Les composants ne stockent pas leurs propres
coordonnées ; le manager publie un :class:`WorkspaceState` immuable.
Les mutations passent par des méthodes qui renvoient un nouvel état.

**Pas de recalcul global pendant un drag.** Les zones utilisent
nativement :class:`QSplitter`, qui ne recompose que ce qui est
nécessaire. Le manager n'écoute pas les déplacements du séparateur pour
reconstruire quoi que ce soit ; il ne lit les tailles qu'au moment de
sauvegarder l'état.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QMainWindow,
    QMenu,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core.workspace_state import (
    DEFAULT_SIZE,
    MIN_SIZE,
    PAGE_EDIT,
    PAGES,
    DockArea,
    FloatingGeometry,
    PanelId,
    WorkspaceState,
    delete_named_workspace,
    list_named_workspaces,
    load_named_workspace,
    load_page_state,
    load_workspace_state,
    page_default_state,
    save_named_workspace,
    save_page_state,
)
from ui.icons import IconName
from ui.i18n import translate
from ui.workspace.panel_host import PanelHost, PanelWindow


#: Clés i18n des libellés lisibles des zones de dock, pour les menus.
_AREA_LABELS: dict[DockArea, str] = {
    DockArea.LEFT: "workspace.area.left",
    DockArea.CENTER: "workspace.area.center",
    DockArea.RIGHT: "workspace.area.right",
    DockArea.BOTTOM: "workspace.area.bottom",
}


def _close_floating_window(window: PanelWindow, host: PanelHost) -> None:
    """Ferme et détruit une fenêtre détachée en gardant son contenu.

    L'ordre est important : le contenu doit revenir dans son
    :class:`PanelHost` *avant* que la fenêtre soit détruite. Le rattacher
    à la fenêtre principale ne suffit pas : il sortirait alors du layout
    de son hôte et deviendrait invisible une fois docké.

    On ne passe volontairement pas par ``window.close()`` ici. Cette
    méthode déclenche ``PanelWindow.closeEvent`` et donc une seconde
    demande de rattachement au manager. Le contenu est déjà sauvé ; on
    masque simplement la fenêtre puis on programme sa destruction Qt.
    """
    content = host.content
    window.blockSignals(True)
    try:
        content.setParent(host)
        layout = host.layout()
        if layout is not None:
            layout.addWidget(content)
        window.hide()
        window.setParent(None)
        window.deleteLater()
    finally:
        window.blockSignals(False)


def separator_action(parent: QWidget | None = None) -> QAction:
    """Séparateur de menu partagé par tous les menus de panneaux."""
    action = QAction(parent)
    action.setSeparator(True)
    return action


class _Zone(QWidget):
    """Zone de dock : contient un ou plusieurs panneaux.

    Les panneaux d'une même zone sont empilés dans un
    :class:`QSplitter` orienté selon la zone (horizontal pour LEFT /
    CENTER / RIGHT, vertical pour BOTTOM). Le séparateur est donc
    réellement déplaçable par l'utilisateur : c'est ce qui permet
    d'agrandir la timeline ou le mixeur sans passer par un menu.
    """

    def __init__(self, area: DockArea, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.area = area
        self.setObjectName(f"dockZone_{area.value}")
        orientation = (
            Qt.Vertical if area is DockArea.BOTTOM else Qt.Horizontal
        )
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._splitter = QSplitter(orientation, self)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setHandleWidth(6)
        self._layout.addWidget(self._splitter)

    # -- contenu -------------------------------------------------------

    def widgets(self) -> list[QWidget]:
        """Panneaux actuellement dans la zone, dans l'ordre."""
        return [
            self._splitter.widget(index)
            for index in range(self._splitter.count())
        ]

    def contains(self, widget: QWidget) -> bool:
        return widget in self.widgets()

    def add(self, widget: QWidget) -> None:
        if self.contains(widget):
            return
        widget.setParent(None)
        self._splitter.addWidget(widget)

    def remove(self, widget: QWidget) -> None:
        """Détache un panneau de la zone.

        :class:`QSplitter` ne propose pas ``removeWidget`` : le
        reparentage suffit à faire sortir le widget de la découpe, et
        c'est la méthode qu'utilise Qt elle-même.
        """
        if not self.contains(widget):
            return
        widget.setParent(None)

    def clear(self) -> None:
        for widget in self.widgets():
            self.remove(widget)

    def take_all(self) -> list[QWidget]:
        """Retire et retourne tous les panneaux de la zone."""
        taken = self.widgets()
        self.clear()
        return taken

    def index_of(self, widget: QWidget) -> int | None:
        widgets = self.widgets()
        return widgets.index(widget) if widget in widgets else None

    def remove_at(self, index: int) -> QWidget | None:
        if 0 <= index < self._splitter.count():
            widget = self._splitter.widget(index)
            self.remove(widget)
            return widget
        return None

    def set_stretch(self, widget: QWidget, stretch: int) -> None:
        index = self.index_of(widget)
        if index is not None:
            self._splitter.setStretchFactor(index, stretch)

    def set_timeline_first(self, first_size: int) -> None:
        """Donne au premier panneau une taille fixe, le reste aux autres.

        Utilisé pour la zone basse : la timeline est le panneau de
        travail principal et mérite la part calculée, les autres
        (mixeur) prennent le reliquat, sans jamais passer sous leur
        minimum.
        """
        widgets = self.widgets()
        if len(widgets) < 2 or self.area is not DockArea.BOTTOM:
            return
        available = self.height()
        if available <= 0:
            return
        primary_floor = max(1, widgets[0].minimumSizeHint().height())
        floors = [max(1, widget.minimumSizeHint().height()) for widget in widgets[1:]]
        other_floor = sum(floors)
        preferred_first = max(primary_floor, int(first_size))

        if available >= preferred_first + other_floor:
            first = preferred_first
        elif available >= primary_floor + other_floor:
            # Priorité à la lisibilité des panneaux secondaires : la
            # timeline absorbe la réduction disponible, jamais le mixeur.
            first = available - other_floor
        else:
            # L'hôte est encore plus petit que le minimum cumulé. On
            # fournit tout de même les vrais minima à Qt, qui les
            # respectera dès que la fenêtre aura été redimensionnée.
            first = primary_floor

        remaining = max(0, available - first)
        sizes = [first]
        if remaining <= other_floor:
            sizes.extend(floors)
        else:
            surplus = remaining - other_floor
            per_panel, remainder = divmod(surplus, len(floors))
            sizes.extend(
                floor + per_panel + (1 if index < remainder else 0)
                for index, floor in enumerate(floors)
            )
        self._splitter.setSizes(sizes)

    def distribute(self, available: int) -> None:
        """Répartit la hauteur/largeur disponible selon les préférences.

        Sans cela, un ``QSplitter`` partage selon les ``sizeHint`` et le
        panneau le plus gourmet peut écraser ses voisins. On répartit
        donc au prorata des tailles préférées, avec un plancher igual au
        minimum de chaque panneau.
        """
        widgets = self.widgets()
        if len(widgets) < 2 or available <= 0:
            return
        weights = []
        for widget in widgets:
            hint = widget.sizeHint()
            preferred = (
                hint.height()
                if self.area is DockArea.BOTTOM
                else hint.width()
            )
            minimum = widget.minimumSizeHint()
            floor = (
                minimum.height()
                if self.area is DockArea.BOTTOM
                else minimum.width()
            )
            weights.append(max(int(preferred), int(floor), 1))
        total = sum(weights)
        if total <= 0:
            return
        sizes = [max(1, int(available * w / total)) for w in weights]
        # Le dernier absorbe l'arrondi pour que la somme soit exacte.
        sizes[-1] = max(1, available - sum(sizes[:-1]))
        self._splitter.setSizes(sizes)


class WorkspaceManager(QObject):
    """Orchestre la disposition, la visibilité et le détachage des panneaux.

    Args:
        window: fenêtre principale, utilisée comme parent des fenêtres
            détachées et pour l'intégration des menus.
        settings_dir: répertoire de persistance ; ``None`` = répertoire
            utilisateur standard.
    """

    #: Panneaux de l'application, dans l'ordre de construction.
    DEFAULT_PANELS: tuple[PanelId, ...] = (
        PanelId.MEDIA,
        PanelId.VIEWER,
        PanelId.INSPECTOR,
        PanelId.TIMELINE,
    )

    def __init__(
        self,
        window: QMainWindow,
        settings_dir=None,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._settings_dir = settings_dir
        self._panels: dict[PanelId, QWidget] = {}
        self._hosts: dict[PanelId, PanelHost] = {}
        self._windows: dict[PanelId, PanelWindow] = {}
        self._zones: dict[DockArea, _Zone] = {}
        self._state: WorkspaceState = WorkspaceState.default()
        # Disposition sauvegardée avant maximisation, pour pouvoir
        # restaurer exactement l'espace de travail précédent.
        self._pre_maximize: WorkspaceState | None = None
        # Page affichée (Montage, Couleur…) et disposition laissée sur chacune.
        self._page = PAGE_EDIT
        self._page_states: dict[str, WorkspaceState] = {}
        self._suspend_capture = False
        self._root: QWidget | None = None
        self._splitters: list[QSplitter] = []
        # Actions de panneaux créées une seule fois (voir
        # ``build_actions``) : évite de peupler la fenêtre principale
        # d'actions orphelines à chaque changement d'état.
        self._action_cache: dict[PanelId, dict[str, QAction]] = {}
        self._separator = separator_action(window)
        # ``MainWindow`` fixe un minimum de départ. Celui-ci reste la
        # référence quand le mixeur est replié ; il augmente seulement
        # quand deux panneaux verticaux doivent cohabiter dans la zone
        # basse.
        self._base_minimum_height = max(1, window.minimumHeight())

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def register(self, panel: PanelId, widget: QWidget) -> None:
        """Enregistre un composant de panneau (sans le docker encore)."""
        if panel in self._panels:
            raise ValueError(f"Panneau déjà enregistré : {panel.value}")  # i18n-ignore: erreur de programmation
        self._panels[panel] = widget

    def build(
        self,
        state: WorkspaceState | None = None,
        *,
        load_persisted: bool = True,
    ) -> QWidget:
        """Construit la disposition et retourne le widget racine.

        La disposition initiale est lue depuis les préférences si
        ``load_persisted`` est vrai, sinon depuis ``state`` ou les valeurs
        par défaut.
        """
        if state is not None:
            self._state = state.normalized()
        elif load_persisted:
            self._state = load_workspace_state(self._settings_dir)
        else:
            self._state = WorkspaceState.default()

        for area in DockArea:
            self._zones[area] = _Zone(area)

        for panel, widget in self._panels.items():
            host = PanelHost(panel, widget, manager=self)
            # La barre d'options ne doit jamais couvrir une commande
            # existante : on la décale sous la barre d'outils des
            # panneaux qui en ont une.
            toolbar = int(getattr(widget, "header_height", 0) or 0)
            host.set_options_offset_y(toolbar)
            self._hosts[panel] = host

        self._root = self._compose()
        self._apply_state(self._state)
        return self._root

    def _compose(self) -> QWidget:
        """Reconstitue l'arbre de splitters à partir de l'état courant.

        Refait à chaque application d'état ; le nombre de manipulations
        est constant (4 panneaux), donc le coût est négligeable et
        happens seulement sur une action explicite de l'utilisateur.
        """
        for zone in self._zones.values():
            zone.take_all()
        for window in self._windows.values():
            window.hide()

        # Colonne centrale : media | viewer | inspector
        center = self._zones[DockArea.LEFT]
        viewer = self._zones[DockArea.CENTER]
        right = self._zones[DockArea.RIGHT]
        top = QSplitter(Qt.Horizontal)
        top.setObjectName("workspace_top")
        top.addWidget(center)
        top.addWidget(viewer)
        top.addWidget(right)
        top.setChildrenCollapsible(False)
        top.setHandleWidth(6)
        self._apply_splitter_minimums(top)
        self._top_splitter = top

        bottom = self._zones[DockArea.BOTTOM]
        root = QSplitter(Qt.Vertical)
        root.setObjectName("workspace_root")
        root.addWidget(top)
        root.addWidget(bottom)
        root.setChildrenCollapsible(False)
        root.setHandleWidth(6)
        root.setStretchFactor(0, 1)
        root.setStretchFactor(1, 1)
        self._root_splitter = root
        self._splitters = [top, root]

        # Les widgets flottants ne doivent pas rester dans les zones.
        for panel, host in self._hosts.items():
            if self._state.is_floating(panel):
                host.setParent(None)
            else:
                host.setParent(None)
            self._place_host(panel, self._state.area_of(panel))

        return root

    def _place_host(self, panel: PanelId, area: DockArea) -> None:
        """Insère l'hôte d'un panneau dans la zone demandée.

        Retire d'abord l'hôte de toute autre zone : sans cela un
        déplacement de zone laisserait le panneau dans les deux.
        """
        host = self._hosts[panel]
        for other, zone in self._zones.items():
            index = zone.index_of(host)
            if index is not None and other is not area:
                zone.remove_at(index)
        zone = self._zones[area]
        zone.add(host)
        host.show()
        self._apply_area_minimum(area)

    def _apply_splitter_minimums(self, splitter: QSplitter) -> None:
        """Applique les tailles minimales par zone (anti-effondrement)."""
        for index, area in enumerate(
            (DockArea.LEFT, DockArea.CENTER, DockArea.RIGHT)
        ):
            zone = self._zones[area]
            width = MIN_SIZE.get(self._primary_panel(area), 200)
            zone.setMinimumWidth(width)
            if splitter.count() > index:
                splitter.setSizes(splitter.sizes())

    def _apply_area_minimum(self, area: DockArea) -> None:
        """Applique la taille minimale d'une zone.

        Une zone peut contenir plusieurs panneaux (couteau, timeline et
        mixeur partagent la zone basse). Comme ils sont **côte à côte**
        dans un même ``QSplitter``, la zone doit réserver la *somme* de
        leurs minimums : avec un simple maximum, le séparateur ne peut
        pas satisfaire les deux et l'un des panneaux se retrouve
        écrasé.
        """
        zone = self._zones.get(area)
        if zone is None:
            return
        panels = [
            p
            for p in self._panels
            if self._state.area_of(p) is area
            and not self._state.is_floating(p)
            and self._state.is_visible(p)
        ]
        if not panels:
            zone.setMinimumWidth(0)
            zone.setMinimumHeight(0)
            return
        # Les poignées entre panneaux prennent aussi de la place : sans elles, le dernier panneau perdait
        # quelques pixels sous son minimum (jusqu'ici masqué par la marge du panneau Timeline, plus haut
        # que son minimum déclaré).
        handles = zone._splitter.handleWidth() * (len(panels) - 1)
        if area is DockArea.BOTTOM:
            zone.setMinimumHeight(sum(MIN_SIZE.get(p, 200) for p in panels) + handles)
            zone.setMinimumWidth(0)
        else:
            zone.setMinimumWidth(sum(MIN_SIZE.get(p, 200) for p in panels) + handles)
            zone.setMinimumHeight(0)

    def _primary_panel(self, area: DockArea) -> PanelId:
        """Panneau de référence d'une zone (pour les minima)."""
        for panel in self._panels:
            if self._state.area_of(panel) is area:
                return panel
        return PanelId.VIEWER

    # ------------------------------------------------------------------
    # Lecture de l'état
    # ------------------------------------------------------------------

    @property
    def state(self) -> WorkspaceState:
        """Instantané courant (immuable)."""
        return self._state

    def host_of(self, panel: PanelId) -> PanelHost | None:
        return self._hosts.get(panel)

    def is_floating(self, panel: PanelId) -> bool:
        return panel in self._windows and self._windows[panel].isVisible()

    def is_shown(self, panel: PanelId) -> bool:
        """Le panneau est-il réellement à l'écran ?

        Diffère de :meth:`is_visible` : un panneau peut être *ouvert*
        tout en étant temporairement masqué, parce qu'un autre panneau
        est maximisé.
        """
        if not self.is_visible(panel):
            return False
        maximized = self._state.maximized
        if maximized is not None:
            return maximized is panel
        if self._state.is_floating(panel):
            window = self._windows.get(panel)
            return window is not None and window.isVisible()
        host = self._hosts.get(panel)
        return host is not None and not host.isHidden()

    def is_maximized(self, panel: PanelId) -> bool:
        return self._state.maximized is panel

    def is_visible(self, panel: PanelId) -> bool:
        """Le panneau est-il ouvert ?

        L'état fait foi : l'état des widgets ne peut pas être utilisé
        comme référence car un panneau d'une fenêtre pas encore
        affichée n'est pas « visible » alors qu'il est bien ouvert.
        """
        entry = self._state.get(panel)
        if not entry.visible:
            return False
        if entry.floating:
            window = self._windows.get(panel)
            return window is not None and window.isVisible()
        return True

    # ------------------------------------------------------------------
    # Opérations
    # ------------------------------------------------------------------

    def toggle_panel(self, panel: PanelId) -> None:
        """Ouvre ou ferme un panneau."""
        if self._state.is_visible(panel) and self.is_visible(panel):
            self.set_panel_visible(panel, False)
        else:
            self.set_panel_visible(panel, True)

    def set_panel_visible(self, panel: PanelId, visible: bool) -> None:
        """Affiche ou masque un panneau."""
        if panel not in self._panels:
            return
        area = self._state.area_of(panel)
        if visible and self._state.is_floating(panel):
            window = self._windows.get(panel)
            if window is not None:
                window.show()
                window.raise_()
        else:
            host = self._hosts.get(panel)
            if host is not None:
                host.setVisible(visible)
        self._commit(self._state.with_panel(panel, visible=visible))
        # La zone du panneau doit être recalculée : une zone devenue
        # vide se masque, une zone réactivée réapparaît. L'ordre
        # compte — les tailles ne peuvent être redistribuées qu'une
        # fois le minimum de la zone libéré, sinon le séparateur
        # refuse de la réduire et laisse un vide.
        self._normalize_area(area)
        self._sync_window_minimum_height()
        self._apply_sizes()
        if area is DockArea.BOTTOM:
            # Ouvrir le mixeur agrandit la zone basse : on rééquilibre
            # pour que la timeline garde sa hauteur confortable.
            QTimer.singleShot(0, self.balance_vertical_split)
        self._refresh_dependents()

    def float_panel(self, panel: PanelId) -> None:
        """Détache un panneau dans une fenêtre séparée.

        Le composant existant est reparenté : aucune logique n'est
        dupliquée, le projet reste la source de vérité unique.
        """
        if panel not in self._panels:
            return
        if panel in self._windows:
            window = self._windows[panel]
            window.show()
            window.raise_()
            return

        host = self._hosts[panel]
        content = host.content
        window = PanelWindow(panel, content, manager=self, parent=self._window)
        self._windows[panel] = window

        # Le panneau quitte sa zone mais garde sa zone de retour.
        host.setParent(None)
        host.hide()
        self._normalize_area(self._state.area_of(panel))

        geom = self._state.floating_geometry(panel)
        window.setGeometry(geom.x, geom.y, geom.width, geom.height)
        window.show()
        window.refresh_actions()

        self._commit(self._state.with_panel(panel, floating=True, visible=True))
        self._normalize_area(self._state.area_of(panel))
        self._sync_window_minimum_height()
        self._refresh_dependents()

    def dock_panel(self, panel: PanelId) -> None:
        """Rattache un panneau détaché à sa zone d'origine.

        Le composant est d'abord reparenté **dans son hôte** : sans
        cela, la destruction de la fenêtre détachée emporterait le
        panneau avec elle.
        """
        window = self._windows.pop(panel, None)
        host = self._hosts[panel]
        if window is not None:
            _close_floating_window(window, host)

        host.show()
        self._place_host(panel, self._state.area_of(panel))
        self._commit(self._state.with_panel(panel, floating=False))
        self._normalize_area(self._state.area_of(panel))
        self._sync_window_minimum_height()
        self._refresh_dependents()

    def move_panel(self, panel: PanelId, area: DockArea) -> None:
        """Déplace un panneau docké vers une autre zone.

        Fondation du docking : l'identité du panneau ne change jamais,
        seule sa zone d'accueil est mise à jour. Un panneau détaché est
        d'abord rattaché, puis déplacé.
        """
        if panel not in self._panels:
            return
        if self._state.is_floating(panel):
            self.dock_panel(panel)
        origin = self._state.area_of(panel)
        if area is origin:
            return
        self._commit(self._state.with_panel(panel, area=area, floating=False))
        self._place_host(panel, area)
        self._normalize_area(origin)
        self._normalize_area(area)
        self._sync_window_minimum_height()
        self._apply_sizes()
        self.refresh_panel_actions()
        self._refresh_dependents()

    def maximize_panel(self, panel: PanelId) -> None:
        """Maximise temporairement un panneau dans l'espace de travail."""
        if self._state.maximized is panel:
            self.restore_layout()
            return
        if panel not in self._panels or not self.is_visible(panel):
            return
        if self._pre_maximize is None:
            self._pre_maximize = self._state
        self._state = self._state.with_maximized(panel)
        self._apply_maximized(panel)
        self._refresh_dependents()

    def restore_layout(self) -> None:
        """Restaure la disposition précédant une maximisation."""
        if self._state.maximized is None:
            return
        self._state = self._state.with_maximized(None)
        if self._pre_maximize is not None:
            self._state = self._pre_maximize
            self._pre_maximize = None
        self._apply_state(self._state)
        self._refresh_dependents()

    def reset_panel_size(self, panel: PanelId) -> None:
        """Réinitialise la taille d'un panneau à sa valeur préférée."""
        if panel not in self._panels:
            return
        self._commit(self._state.with_panel(panel, size=DEFAULT_SIZE[panel]))
        if self._state.is_floating(panel):
            window = self._windows.get(panel)
            if window is not None:
                geom = self._state.floating_geometry(panel)
                window.resize(geom.width, geom.height)
        else:
            self._apply_sizes()
        self._refresh_dependents()

    def reset_layout(self) -> None:
        """Rétablit la disposition d'origine de la page affichée."""
        self._pre_maximize = None
        for panel in list(self._windows):
            self.dock_panel(panel)
        self._commit(page_default_state(self._page))
        self._apply_state(self._state)
        self._refresh_dependents()

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    @property
    def page(self) -> str:
        """Page affichée (:data:`core.workspace_state.PAGES`)."""
        return self._page

    def switch_page(self, page: str) -> bool:
        """Passe à une autre page : la disposition de la page quittée est gardée, celle de la page ouverte reprend
        telle qu'on l'avait laissée (sa disposition d'origine la première fois)."""
        if page not in PAGES or page == self._page:
            return False
        self._page_states[self._page] = self._layout_to_keep()
        target = self._page_states.get(page) or load_page_state(page, self._settings_dir)
        self._pre_maximize = None
        for panel in list(self._windows):
            window = self._windows.pop(panel)
            _close_floating_window(window, self._hosts[panel])
        self._page = page
        self._apply_state(target)
        self._refresh_dependents()
        return True

    def _layout_to_keep(self) -> WorkspaceState:
        """La disposition à retrouver plus tard : jamais un panneau resté maximisé."""
        if self._state.maximized is not None:
            return self._pre_maximize or self._state.with_maximized(None)
        return self.capture_state()

    # ------------------------------------------------------------------
    # Équilibrage
    # ------------------------------------------------------------------

    def balance_vertical_split(self) -> None:
        """Répartit la hauteur entre la zone haute et la timeline.

        La timeline reçoit la hauteur nécessaire pour montrer toutes ses
        pistes, sans jamais descendre sous 40 % de l'espace disponible
        (le viewer reste le panneau majoritaire). Appelée à l'ouverture
        et à chaque changement de pistes — pas pendant un drag.
        """
        root = getattr(self, "_root_splitter", None)
        if root is None:
            return
        total = root.height()
        if total <= 0:
            QTimer.singleShot(0, self.balance_vertical_split)
            return
        timeline = self._panels.get(PanelId.TIMELINE)
        if timeline is None:
            return
        track_count = len(self._project_tracks()) or 1
        pitch = timeline.track_height + timeline.track_gap
        timeline_needed = (
            timeline.header_height
            + timeline.ruler_height
            + 8
            + pitch * max(track_count, 1)
            + 34  # marges + barres de défilement
        )
        # La zone basse peut héberger d'autres panneaux (le mixeur) :
        # chacun réclame son minimum, sinon l'un se fait écraser.
        extras = 0
        for panel in self._panels:
            if panel is PanelId.TIMELINE:
                continue
            if (
                self._state.area_of(panel) is DockArea.BOTTOM
                and self._state.is_visible(panel)
            ):
                extras += MIN_SIZE.get(panel, 200) + self._zones[DockArea.BOTTOM]._splitter.handleWidth()
        needed = timeline_needed + extras
        bottom = max(needed, int(total * 0.40))
        bottom = min(bottom, int(total * 0.80))
        bottom = max(bottom, timeline.minimumHeight() + extras)
        # La rangée du haut garde la hauteur naturelle de la bibliothèque quand la fenêtre le permet (900 px) :
        # c'était jusqu'ici son minimum de layout, trop haut pour 720 px, qui l'imposait. La timeline cède
        # alors, jamais sous son minimum.
        library = self._panels.get(PanelId.MEDIA)
        if library is not None and not self._state.is_floating(PanelId.MEDIA):
            wanted_top = library.sizeHint().height()
            bottom = min(bottom, max(total - wanted_top, timeline.minimumHeight() + extras))
        top = max(total - bottom, 160)
        root.setSizes([top, bottom])
        # Partage interne déterministe : la timeline prend ce dont elle a
        # besoin, le reste va aux autres panneaux de la zone (mixeur).
        zone = self._zones.get(DockArea.BOTTOM)
        if zone is not None and len(zone.widgets()) > 1:
            zone.set_timeline_first(timeline_needed)

    def _project_tracks(self) -> list:
        """Pistes du projet courant, si le panneau timeline le porte."""
        timeline = self._panels.get(PanelId.TIMELINE)
        project = getattr(timeline, "project", None)
        return list(getattr(project, "tracks", []) or [])

    # ------------------------------------------------------------------
    # Actions de panneau
    # ------------------------------------------------------------------

    def build_actions(self, panel: PanelId) -> list[QAction]:
        """Menu contextuel d'un panneau.

        Les actions sont **créées une seule fois** puis rafraîchies.
        Les recréer à chaque appel (au survol, à chaque changement
        d'état) empilait des dizaines d'``QAction`` orphelins dans la
        fenêtre principale.

        L'API reste en ``QAction`` afin que la même liste alimente la
        barre d'options, le menu « Fenêtre » et d'éventuels
        raccourcis, sans duplication de logique.
        """
        actions = self._action_cache.get(panel)
        if actions is None:
            actions = self._create_actions(panel)
            self._action_cache[panel] = actions
        else:
            self._refresh_actions(panel, actions)
        maximize_restore = (
            actions["restore"]
            if self._state.maximized is panel
            else actions["maximize"]
        )
        return [
            actions["place"],
            actions["move"],
            maximize_restore,
            actions["reset"],
            self._separator,
            actions["close"],
        ]

    def _create_actions(self, panel: PanelId) -> dict[str, QAction]:
        parent = self._window
        icons = {
            "float": self._icon(IconName.PANEL_FLOAT),
            "dock": self._icon(IconName.PANEL_DOCK),
            "maximize": self._icon(IconName.PANEL_MAXIMIZE),
            "restore": self._icon(IconName.PANEL_RESTORE),
            "reset": self._icon(IconName.PANEL_RESET),
            "close": self._icon(IconName.PANEL_CLOSE),
        }
        place = QAction(translate("workspace.action.float"), parent)
        place.setIcon(icons["float"])
        place.triggered.connect(lambda _c=False, p=panel: self.float_panel(p))

        maximize = QAction(translate("workspace.action.maximize"), parent)
        maximize.setIcon(icons["maximize"])
        maximize.triggered.connect(
            lambda _c=False, p=panel: self.maximize_panel(p)
        )

        restore = QAction(translate("menu.item.restore_layout"), parent)
        restore.setIcon(icons["restore"])
        restore.triggered.connect(self.restore_layout)

        reset = QAction(translate("workspace.action.reset_size"), parent)
        reset.setIcon(icons["reset"])
        reset.triggered.connect(lambda _c=False, p=panel: self.reset_panel_size(p))

        close = QAction(translate("workspace.action.close"), parent)
        close.setIcon(icons["close"])
        close.triggered.connect(
            lambda _c=False, p=panel: self.set_panel_visible(p, False)
        )

        # Déplacement vers une autre zone : effectif dès aujourd'hui, il
        # pose les fondations du docking complet.
        move = QMenu(translate("workspace.action.move_to"), parent)
        for area in DockArea:
            entry = QAction(translate(_AREA_LABELS[area]), move)
            entry.triggered.connect(
                lambda _c=False, a=area, p=panel: self.move_panel(p, a)
            )
            move.addAction(entry)

        return {
            "place": place,
            "maximize": maximize,
            "restore": restore,
            "reset": reset,
            "close": close,
            "move": move,
        }

    def _refresh_actions(self, panel: PanelId, actions: dict) -> None:
        """Met à jour l'état des actions d'un panneau (sans les recréer)."""
        floating = self.is_floating(panel)
        actions["place"].setText(translate("workspace.action.dock" if floating else "workspace.action.float"))
        actions["place"].setIcon(
            self._icon(IconName.PANEL_DOCK if floating else IconName.PANEL_FLOAT)
        )
        actions["maximize"].setEnabled(self.is_visible(panel) or floating)
        actions["restore"].setEnabled(True)
        actions["reset"].setEnabled(self.is_visible(panel) or floating)
        actions["close"].setEnabled(self.is_visible(panel))

    def retranslate(self) -> None:
        """Textes des menus d'options et des hôtes dans la langue courante (changement de langue à chaud)."""
        for panel, actions in self._action_cache.items():
            actions["maximize"].setText(translate("workspace.action.maximize"))
            actions["restore"].setText(translate("menu.item.restore_layout"))
            actions["reset"].setText(translate("workspace.action.reset_size"))
            actions["close"].setText(translate("workspace.action.close"))
            move = actions["move"]
            move.setTitle(translate("workspace.action.move_to"))
            for entry, area in zip(move.actions(), DockArea):
                entry.setText(translate(_AREA_LABELS[area]))
            self._refresh_actions(panel, actions)
        for host in self._hosts.values():
            host.retranslate()
        for window in self._windows.values():
            window.retranslate()

    def _icon(self, name: IconName):
        from ui.icons import make_icon
        from ui.design_system import Iconography

        return make_icon(name, size=Iconography.md)

    # ------------------------------------------------------------------
    # Application de l'état
    # ------------------------------------------------------------------

    def _commit(self, state: WorkspaceState) -> None:
        """Adopte un nouvel état et rafraîchit l'affichage."""
        self._state = state.normalized()
        self._apply_sizes()
        self.refresh_panel_actions()

    def _apply_state(self, state: WorkspaceState) -> None:
        """Applique intégralement un état (restauration complète)."""
        self._state = state.normalized()
        maximized = self._state.maximized
        for panel, host in self._hosts.items():
            entry = self._state.get(panel)
            if entry.floating:
                host.hide()
                if panel not in self._windows:
                    window = PanelWindow(
                        panel, host.content, manager=self, parent=self._window
                    )
                    self._windows[panel] = window
                window = self._windows[panel]
                geom = self._state.floating_geometry(panel)
                window.setGeometry(geom.x, geom.y, geom.width, geom.height)
                window.setVisible(entry.visible)
                window.refresh_actions()
            else:
                existing = self._windows.pop(panel, None)
                if existing is not None:
                    # Idem : on récupère le contenu avant de détruire
                    # la fenêtre, sinon le panneau part avec elle.
                    _close_floating_window(existing, host)
                self._place_host(panel, self._state.area_of(panel))
                host.setVisible(entry.visible)
        for area in DockArea:
            self._normalize_area(area)
        if maximized is not None:
            self._apply_maximized(maximized)
        else:
            self._apply_sizes()
        self._sync_window_minimum_height()
        self.refresh_panel_actions()

    def _sync_window_minimum_height(self) -> None:
        """Garantit la place réelle des panneaux superposés.

        La zone basse peut contenir timeline + mixeur. Leur splitter
        applique bien leurs minima, mais une ``QMainWindow`` avec un
        minimum explicite plus petit peut autrement les comprimer avant
        que Qt ait une chance de répartir leurs tailles. On relève donc
        le minimum de fenêtre uniquement pendant cette cohabitation.
        """
        root = getattr(self, "_root_splitter", None)
        top = getattr(self, "_top_splitter", None)
        bottom = self._zones.get(DockArea.BOTTOM)
        if root is None or top is None or bottom is None:
            return
        if self._state.maximized is not None:
            self._window.setMinimumHeight(self._base_minimum_height)
            return

        chrome = max(0, self._window.height() - root.height())
        required = (
            top.minimumSizeHint().height()
            + bottom.minimumSizeHint().height()
            + root.handleWidth()
            + chrome
        )
        self._window.setMinimumHeight(
            max(self._base_minimum_height, int(required))
        )

    def _normalize_area(self, area: DockArea) -> None:
        """Aligne le contenu d'une zone sur l'état courant.

        Fonction idempotente et réparatrice : elle retire les hôtes qui
        n'ont plus leur place dans la zone et y réinsère ceux qui
        manquent. C'est le seul endroit qui manipule le contenu des
        zones, ce qui évite toute divergence entre l'état et l'affichage.
        """
        zone = self._zones[area]
        expected = self._state.visible_panels(area)
        expected_set = set(expected)

        present = [w for w in zone.widgets() if w is not None]
        # Retire les hôtes qui ne doivent plus être ici.
        for widget in present:
            panel = getattr(widget, "panel", None)
            if panel is None or panel not in expected_set:
                zone.remove(widget)
        # Réinsère les hôtes attendus manquants, dans l'ordre de l'état.
        for panel in expected:
            host = self._hosts.get(panel)
            if host is None or host in present:
                continue
            host.setParent(None)
            zone.add(host)
            host.show()
        for widget in present:
            panel = getattr(widget, "panel", None)
            if panel in expected_set:
                widget.show()

        self._apply_area_minimum(area)
        zone.setVisible(bool(expected))
        if not expected:
            zone.setMinimumWidth(0)
            zone.setMinimumHeight(0)
            return
        # Répartition interne : chaque panneau reçoit la part que sa
        # taille préférée demande, pas un simple partage par défaut.
        if area is DockArea.BOTTOM:
            zone.distribute(max(0, zone.height()))
        else:
            zone.distribute(max(0, zone.width()))

    def _apply_maximized(self, panel: PanelId) -> None:
        """Affiche un seul panneau dans l'espace de travail."""
        for other, host in self._hosts.items():
            if other is panel:
                continue
            if self._state.is_floating(other):
                window = self._windows.get(other)
                if window is not None:
                    window.hide()
            else:
                host.hide()
        if self._state.is_floating(panel):
            window = self._windows.get(panel)
            if window is not None:
                window.show()
                window.raise_()
        else:
            host = self._hosts[panel]
            host.setParent(None)
            # On place le panneau maximisé dans la zone racine (centre),
            # sans reconstruire la disposition entière.
            zone = self._zones[DockArea.CENTER]
            zone.take_all()
            zone.add(host)
            host.show()
            for other_area in DockArea:
                if other_area is not DockArea.CENTER:
                    self._zones[other_area].setVisible(False)
            self._zones[DockArea.BOTTOM].setVisible(False)
            root = self._root_splitter
            root.setSizes([root.height(), 0])
        self._refresh_dependents()

    def _apply_sizes(self) -> None:
        """Applique les tailles préférées aux zones."""
        if self._state.maximized is not None:
            return
        top = getattr(self, "_top_splitter", None)
        if top is None:
            return
        left = self._zone_width(DockArea.LEFT)
        center = self._zone_width(DockArea.CENTER)
        right = self._zone_width(DockArea.RIGHT)
        total = max(top.width(), left + center + right)
        if total <= 0:
            return
        top.setSizes([left, center, right])
        root = getattr(self, "_root_splitter", None)
        if root is not None:
            ratio = float(self._state.center_ratio)
            root.setSizes(
                [int(root.height() * ratio), int(root.height() * (1 - ratio))]
            )

    def _zone_width(self, area: DockArea) -> int:
        panels = [
            p for p in self._panels
            if self._state.area_of(p) is area
            and not self._state.is_floating(p)
            and self._state.is_visible(p)
        ]
        if not panels:
            return 0
        if area is DockArea.CENTER:
            return max(480, DEFAULT_SIZE[PanelId.VIEWER])
        return max(MIN_SIZE.get(panels[0], 200), self._state.get(panels[0]).size)

    def refresh_panel_actions(self) -> None:
        """Reconstruit les menus d'options de tous les panneaux."""
        for host in self._hosts.values():
            host.refresh_actions()
        for window in self._windows.values():
            window.refresh_actions()

    def _refresh_dependents(self) -> None:
        """Notifie l'hôte principal que la disposition a changé."""
        callback = getattr(self._window, "on_workspace_changed", None)
        if callable(callback):
            callback()

    # ------------------------------------------------------------------
    # Persistance
    # ------------------------------------------------------------------

    def capture_state(self) -> WorkspaceState:
        """Lit l'état réel des séparateurs pour le persister.

        Appelé à la fermeture (et lors d'une sauvegarde explicite), donc
        le coût n'affecte pas l'interaction.
        """
        if self._state.maximized is not None:
            return self._state
        state = self._state
        top = getattr(self, "_top_splitter", None)
        root = getattr(self, "_root_splitter", None)
        if top is not None and top.width() > 0:
            sizes = top.sizes()
            if len(sizes) == 3 and sizes[1] > 0:
                # La largeur d'une zone est celle de son premier panneau (comme
                # ``_zone_width`` la relit) : Médias et Inspecteur ne sont pas
                # toujours à gauche et à droite (page Couleur), et une zone vide
                # (largeur 0) ne réécrit rien.
                for area, size in ((DockArea.LEFT, sizes[0]), (DockArea.RIGHT, sizes[2])):
                    panels = self._state.visible_panels(area)
                    if panels and size > 0:
                        state = state.with_panel(panels[0], size=max(MIN_SIZE[panels[0]], size))
        if root is not None and root.height() > 0:
            sizes = root.sizes()
            if len(sizes) == 2 and sum(sizes) > 0:
                ratio = max(0.1, min(0.9, sizes[0] / sum(sizes)))
                state = state.with_center_ratio(ratio)
                state = state.with_panel(
                    PanelId.TIMELINE,
                    size=max(MIN_SIZE[PanelId.TIMELINE], sizes[1]),
                )
        for panel, window in self._windows.items():
            if window.isVisible():
                geom = window.geometry()
                state = state.with_floating_geometry(
                    panel,
                    FloatingGeometry(
                        geom.x(), geom.y(), geom.width(), geom.height()
                    ),
                )
        return state.normalized()

    def list_workspaces(self) -> tuple[str, ...]:
        """Espaces de travail disponibles (natifs puis personnalisés)."""
        return list_named_workspaces(self._settings_dir)

    def apply_workspace(self, name: str) -> bool:
        """Applique un espace de travail nommé."""
        state = load_named_workspace(name, self._settings_dir)
        if state is None:
            return False
        self._pre_maximize = None
        # Les fenêtres flottantes de la disposition courante sont
        # fermées avant d'appliquer : un panneau ne peut pas être
        # simultanément dans deux dispositions.
        for panel in list(self._windows):
            window = self._windows.pop(panel)
            _close_floating_window(window, self._hosts[panel])
        self._apply_state(state)
        self._refresh_dependents()
        return True

    def save_workspace_as(self, name: str) -> bool:
        """Enregistre la disposition courante sous un nom."""
        if not name.strip():
            return False
        try:
            save_named_workspace(
                name.strip(), self.capture_state(), self._settings_dir
            )
        except OSError:
            return False
        return True

    def delete_workspace(self, name: str) -> bool:
        """Supprime un espace de travail personnalisé."""
        return delete_named_workspace(name, self._settings_dir)

    def save(self) -> None:
        """Persiste la disposition de chaque page visitée (le Montage dans ``workspace.json``)."""
        self._state = self.capture_state()
        self._page_states[self._page] = self._state
        try:
            for page, state in self._page_states.items():
                save_page_state(page, state, self._settings_dir)
        except OSError:
            # Une préférence non persistée ne doit jamais empêcher la
            # fermeture de l'application.
            pass

    # ------------------------------------------------------------------
    # Nettoyage
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Ferme les fenêtres détachées et libère les ressources."""
        self.save()
        for window in list(self._windows.values()):
            _close_floating_window(window, self._hosts[window.panel])
        self._windows.clear()
        self._hosts.clear()
        self._panels.clear()
        self._zones.clear()
        self._action_cache.clear()
        self._splitters = []


__all__ = ["WorkspaceManager"]
