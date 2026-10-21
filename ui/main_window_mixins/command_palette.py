"""Méthodes de ``MainWindow`` : palette de commandes « Rechercher une fonction » (⌘K / Ctrl+K), registre des fonctions.

Le **registre** est construit à chaque ouverture à partir des vrais menus de la fenêtre : toute nouvelle entrée de menu est
donc trouvable sans maintenance. Pour chaque ``QAction`` : titre traduit (et titre anglais quand la clé de traduction est
connue), raccourci, chemin de menu en indication, état activé / grisé, et **niveau d'usage minimal** lu dans la table de
``menu_levels`` (``MENU_GATES`` : y compris pour les entrées que le niveau courant a retirées du menu). S'y ajoutent les
destinations « Aller à … » : pages, onglets de l'inspecteur, bibliothèques.

Exception **voulue** au niveau Simple : les menus n'y montrent aucun outil du niveau supérieur, mais la palette les montre
(découvrabilité) avec un bouton « Passer en Studio / Avancé » qui change le niveau (``set_usage_level`` : persistance et
signal habituels) puis lance la fonction. Rien n'est désactivé ni caché de plus ; voir ``docs/usage-levels.md``.

Ouvrir ou fermer la palette ne touche ni au projet, ni à l'historique, ni à la mise en page. Seule la commande choisie agit.
Les cinq dernières fonctions choisies sont gardées **en mémoire** (aucun réglage n'est écrit).
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QMenu, QToolButton

from core.command_search import SearchEntry, clean_label
from core.usage_level import UsageLevel
from core.workspace_state import PAGE_AUDIO, PAGE_COLOR, PAGE_COMPOSITION, PAGE_EDIT, PanelId
from ui import i18n
from ui.command_palette import CommandPalette, shortcut_text
from ui.icons import IconName
from ui.main_window_mixins.menu_levels import PAGE_LEVELS
from ui.properties_panel_mixins.construction import TRACKING_TAB
from ui.side_rail import SECTION_LEVELS

RECENT_LIMIT = 5

_MENU_ICONS = {
    "file_menu": IconName.PROJECT, "edit_menu": IconName.EDIT, "timeline_menu": IconName.FILM,
    "layers_menu": IconName.SHAPE, "social_menu": IconName.STAR, "window_menu": IconName.PANEL_DOCK,
    "help_menu": IconName.INFO,
}
_PAGE_COMMANDS = ("page_edit", "page_color", "page_composition", "page_audio")
_PAGES = (
    (PAGE_EDIT, IconName.FILM), (PAGE_COMPOSITION, IconName.NODES), (PAGE_COLOR, IconName.COLOR), (PAGE_AUDIO, IconName.AUDIO),
)
# (nom court, clé du libellé, indice d'onglet, attribut du groupe qui porte le niveau, icône)
_TABS = (
    ("clip", "inspector.tab.clip", 0, None, IconName.EDIT),
    ("color", "group.color", 1, "color_group", IconName.COLOR),
    ("effects", "rail.effects", 2, "effects_group", IconName.EFFECTS),
    ("audio", "rail.audio", 3, "audio_group", IconName.AUDIO),
    ("graphics", "rail.graphics", 4, "graphics_group", IconName.SUBTITLE),
    ("compositing", "inspector.tab.compositing", 5, "compositing_group", IconName.NODES),
    ("tracking", "tracking.title", TRACKING_TAB, "tracking_group", IconName.PIPETTE),
)
_LIBRARIES = (
    ("media", IconName.MEDIA), ("text", IconName.TEXT), ("effects", IconName.EFFECTS), ("transitions", IconName.TRANSITIONS),
    ("audio", IconName.AUDIO), ("graphics", IconName.SUBTITLE), ("sequences", IconName.FILM),
)


def _english(key: str, **values: object) -> str:
    """Texte d'une clé en anglais, ``""`` si la clé n'a pas d'anglais (le titre de la langue courante suffit alors)."""
    try:
        return i18n.translate_strict(key, "en", **values)
    except (i18n.MissingTranslationError, ValueError):
        return ""


def _higher(level: UsageLevel, floor: UsageLevel) -> UsageLevel:
    return level if level.at_least(floor) else floor


class CommandPaletteMixin:
    """Mixin de ``MainWindow`` (palette de commandes)."""

    command_palette: CommandPalette
    palette_button: QToolButton
    _palette_runners: dict[str, Callable[[], object]]
    _palette_entries_by_id: dict[str, SearchEntry]
    _palette_recent: list[str]

    # -- construction --------------------------------------------------------------------------------------------------

    def _build_palette_button(self) -> QToolButton:
        """Bouton « Rechercher… ⌘K » de la barre du haut (loupe, texte et raccourci affiché)."""
        from ui.icons import make_icon

        button = QToolButton()
        button.setObjectName("paletteButton")
        button.setIcon(make_icon(IconName.SEARCH))
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.NoFocus)
        button.clicked.connect(lambda _checked=False: self.toggle_command_palette())  # type: ignore[attr-defined]
        self.palette_button = button
        self._retranslate_command_palette()
        return button

    def _install_command_palette(self) -> None:
        """Crée la boîte (une fois, enfant de la fenêtre) ; à appeler quand les menus sont construits."""
        self._palette_runners = {}
        self._palette_entries_by_id = {}
        self._palette_recent = []
        palette = CommandPalette(self)  # type: ignore[arg-type]
        palette.entries_provider = self.command_palette_entries
        palette.level_provider = lambda: self.usage_level  # type: ignore[attr-defined]
        palette.recent_provider = lambda: list(self._palette_recent)
        palette.entry_chosen.connect(self._run_palette_entry)
        self.command_palette = palette
        self.shortcuts.changed.connect(self._retranslate_command_palette)  # type: ignore[attr-defined]

    def _retranslate_command_palette(self) -> None:
        """Libellé, raccourci affiché et noms accessibles du bouton (langue et raccourci configuré)."""
        button = getattr(self, "palette_button", None)
        if button is None:
            return
        sequences = self.shortcuts.shortcut_map.sequences("command_palette")  # type: ignore[attr-defined]
        shortcut = shortcut_text(sequences[0]) if sequences else ""
        label = i18n.translate("palette.button")
        button.setText(f" {label}   {shortcut}".rstrip())
        button.setToolTip(i18n.translate("palette.button.tooltip", shortcut=shortcut or "—"))
        button.setAccessibleName(i18n.translate("palette.button.name"))
        palette = getattr(self, "command_palette", None)
        if palette is not None:
            palette.retranslate()

    # -- ouverture ------------------------------------------------------------------------------------------------------

    def toggle_command_palette(self, text: str = "") -> None:
        """Ouvre la palette (saisie ``text``), ou la ferme si elle est déjà ouverte."""
        if self.command_palette.is_open():
            self.command_palette.close_palette()
        else:
            self.command_palette.open_palette(text)

    # -- registre -------------------------------------------------------------------------------------------------------

    def command_palette_entries(self) -> list[SearchEntry]:
        """Registre complet (menus puis destinations) ; reconstruit à chaque ouverture, donc toujours à jour."""
        self._palette_runners = {}
        entries: list[SearchEntry] = []
        taken: set[str] = set()
        self._collect_menu_entries(entries, taken)
        self._collect_destination_entries(entries)
        self._palette_entries_by_id = {entry.id: entry for entry in entries}
        return entries

    def _collect_menu_entries(self, entries: list[SearchEntry], taken: set[str]) -> None:
        keys = {id(action): key for action, key in self._translated_actions}  # type: ignore[attr-defined]
        menu_keys = {id(menu): key for menu, key in self._translated_menus}  # type: ignore[attr-defined]
        gated = {id(entry.menu): entry for entry in getattr(self, "_menu_levels", [])}
        skipped = {id(action) for command in _PAGE_COMMANDS if (action := self.shortcuts.action(command)) is not None}  # type: ignore[attr-defined]
        seen: set[int] = set()
        keep: list[object] = []              # garde les objets vivants : sinon un ``id`` libéré serait réutilisé par un autre
        sep = i18n.translate("palette.path_separator")

        def walk(menu: QMenu, path: list[str], path_en: list[str], floor: UsageLevel, icon: IconName) -> None:
            gate = gated.get(id(menu))
            for action in gate.items if gate is not None else menu.actions():
                if action.isSeparator() or id(action) in seen:
                    continue
                level = _higher(gate.levels.get(id(action), UsageLevel.SIMPLE), floor) if gate is not None else floor
                title = clean_label(action.text())
                submenu = action.menu()
                if submenu is not None:
                    key = menu_keys.get(id(submenu))
                    name_en = clean_label(_english(key)) if key else ""
                    walk(submenu, [*path, title], [*path_en, name_en or title], level, icon)
                    continue
                seen.add(id(action))
                keep.append(action)
                if not title or id(action) in skipped:
                    continue
                key = keys.get(id(action))
                title_en = clean_label(_english(key)) if key else ""
                entry_id = f"menu:{key}" if key else f"menu:{sep.join(path)}/{title}"
                while entry_id in taken:
                    entry_id += "+"
                taken.add(entry_id)
                reason = ""
                if not action.isEnabled():
                    tip = action.toolTip().strip()
                    reason = tip if tip and tip not in (title, action.text(), action.text().replace("&", "")) else ""
                entries.append(SearchEntry(
                    id=entry_id, title=title, title_en=title_en, hint=sep.join(path), hint_en=sep.join(path_en),
                    context=path[-1] if len(path) > 1 else "",       # « Panneaux › Couleur » : le titre seul serait ambigu
                    level=level, enabled=action.isEnabled(), reason=reason, kind="command",
                    shortcut=action.shortcut().toString(QKeySequence.PortableText), icon=icon.value,
                ))
                self._palette_runners[entry_id] = lambda a=action: a.trigger() if a.isEnabled() else None

        for bar_action in self.menuBar().actions():  # type: ignore[attr-defined]
            menu = bar_action.menu()
            if menu is None:
                continue
            title = clean_label(menu.title())
            key = {"file_menu": "menu.file", "edit_menu": "menu.edit", "timeline_menu": "menu.timeline", "layers_menu": "menu.layers",
                   "social_menu": "menu.social", "window_menu": "menu.window", "help_menu": "menu.help"}.get(menu.objectName())
            title_en = clean_label(_english(key)) if key else ""
            walk(menu, [title], [title_en or title], UsageLevel.SIMPLE, _MENU_ICONS.get(menu.objectName(), IconName.MENU))

    def _collect_destination_entries(self, entries: list[SearchEntry]) -> None:
        """Pages, onglets de l'inspecteur et bibliothèques : « Aller à … »."""

        def add(entry_id: str, name_key: str, group: str, level: UsageLevel, icon: IconName, run: Callable[[], object]) -> None:
            name, name_en = i18n.translate(name_key), _english(name_key)
            entries.append(SearchEntry(
                id=entry_id, title=i18n.translate(f"palette.goto.{group}.title", name=name),
                title_en=_english(f"palette.goto.{group}.title", name=name_en or name),
                hint=i18n.translate(f"palette.goto.{group}"), hint_en=_english(f"palette.goto.{group}"),
                keywords=(name, name_en, i18n.translate(f"palette.keywords.{entry_id[5:].replace(':', '.')}"),
                          _english(f"palette.keywords.{entry_id[5:].replace(':', '.')}")),
                level=level, icon=icon.value, kind="goto",
            ))
            self._palette_runners[entry_id] = run

        for page, icon in _PAGES:
            add(f"goto:page:{page}", f"page.{page}", "page", PAGE_LEVELS.get(page, UsageLevel.SIMPLE), icon,
                lambda target=page: self.switch_page(target))  # type: ignore[attr-defined]
        panel = self.properties_panel  # type: ignore[attr-defined]
        for short, label_key, index, group_attr, icon in _TABS:
            group = getattr(panel, group_attr, None) if group_attr else None
            level = panel._group_levels.get(group, UsageLevel.SIMPLE) if group is not None else UsageLevel.SIMPLE
            add(f"goto:tab:{short}", label_key, "tab", level, icon, lambda tab=index: self._palette_open_tab(tab))
        for section, icon in _LIBRARIES:
            add(f"goto:library:{section}", f"rail.{section}", "library", SECTION_LEVELS.get(section, UsageLevel.SIMPLE), icon,
                lambda target=section: self._palette_open_library(target))

    def _palette_open_tab(self, index: int) -> None:
        if not self.workspace.is_visible(PanelId.INSPECTOR):  # type: ignore[attr-defined]
            self.workspace.set_panel_visible(PanelId.INSPECTOR, True)  # type: ignore[attr-defined]
        self.properties_panel._select_inspector_tab(index)  # type: ignore[attr-defined]

    def _palette_open_library(self, section: str) -> None:
        self.side_rail.set_active(section)  # type: ignore[attr-defined]
        self._on_side_rail_changed(section)  # type: ignore[attr-defined]

    # -- exécution ------------------------------------------------------------------------------------------------------

    def _run_palette_entry(self, entry_id: str, switch_level: bool) -> None:
        """Lance la fonction choisie ; si elle est d'un niveau supérieur et que « Passer en … » a été demandé, change d'abord le niveau."""
        entry = self._palette_entries_by_id.get(entry_id)
        runner = self._palette_runners.get(entry_id)
        if entry is None or runner is None:
            return
        if not self.usage_level.at_least(entry.level):  # type: ignore[attr-defined]
            if not switch_level:
                return
            self.set_usage_level(entry.level)  # type: ignore[attr-defined]
        runner()
        if entry_id in self._palette_recent:
            self._palette_recent.remove(entry_id)
        self._palette_recent.insert(0, entry_id)
        del self._palette_recent[RECENT_LIMIT:]


__all__ = ["RECENT_LIMIT", "CommandPaletteMixin"]
