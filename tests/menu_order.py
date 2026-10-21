"""Entrées ordonnées des menus et des surfaces (outil des tests de niveau d'usage, lot 4).

``window_dump`` relève, dans l'ordre où l'écran les montre : les entrées des sept menus de la barre (récursivement,
séparateurs compris, avec le raccourci de chaque action), les boutons visibles de la barre de la timeline, du rail et des pages.
``context_dump`` relève les menus contextuels (clip, piste, média) en interceptant ``QMenu.exec``. Le fichier
``tests/data/menus_before_usage_levels.json`` a été produit par ce même code sur la fenêtre d'``origin/main`` d'avant le lot :
au niveau Avancé, rien ne doit en différer.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QMenu

TOOLBAR_BUTTONS = ("snap_button", "blade_button", "roll_button", "slip_button", "slide_button", "record_button",
                   "ripple_button", "marker_button")


def menu_tree(menu: QMenu) -> list:
    """Entrées ordonnées d'un menu : « - » pour un séparateur, ``{titre: [...]}`` pour un sous-menu, sinon le texte."""
    out: list = []
    for action in menu.actions():
        if action.isSeparator():
            out.append("-")
        elif action.menu() is not None:
            out.append({action.text(): menu_tree(action.menu())})
        else:
            shortcut = action.shortcut().toString()
            out.append(f"{action.text()} [{shortcut}]" if shortcut else action.text())
    return out


def window_dump(window) -> dict:
    menus = {}
    for action in window.menuBar().actions():
        menu = action.menu()
        if menu is not None:
            menus[menu.objectName()] = menu_tree(menu)
    timeline = window.timeline_panel
    toolbar = [name for name in TOOLBAR_BUTTONS if not getattr(timeline, name).isHidden()]
    rail = window.side_rail
    rail_ids = [section.id for section in rail._sections if not rail._buttons[section.id].isHidden()]
    pages = [page for page, button in window.page_buttons.items() if not button.isHidden()]
    return {"menus": menus, "toolbar": toolbar, "rail": rail_ids, "pages": pages}


def capture_menus(monkeypatch) -> list[list]:
    """Remplace le ``QMenu`` du menu d'un clip par une sous-classe dont ``exec`` relève l'arbre puis rend la main."""
    import ui.timeline_panel_mixins.drag_tools as drag_tools

    shown: list[list] = []

    class _Capturing(QMenu):
        def exec(self, *_args, **_kwargs):  # noqa: A003 - Qt
            shown.append(menu_tree(self))
            return None

    monkeypatch.setattr(drag_tools, "QMenu", _Capturing)
    return shown


def clip_menu_dump(window, clip_id: str, shown: list[list]) -> list:
    window.timeline_panel.open_clip_menu(clip_id, QPoint(0, 0))
    return shown.pop()


def track_menu_dump(window, track_id: str) -> list:
    """Menu ⋯ d'une piste : le relevé suit l'ouverture réelle (``aboutToShow`` pose les entrées dynamiques)."""
    menu = window.timeline_panel.track_header_widgets[track_id]._more_menu
    menu.aboutToShow.emit()
    return menu_tree(menu)


def asset_menu_dump(window, monkeypatch, shown: list[list]) -> list:
    """Menu d'un média vidéo utilisé (proxy, matrice couleur, un dossier, une étiquette) par le vrai chemin du panneau."""
    import ui.project_panel as project_panel
    from core.library_organization import LibraryOrganization

    class _Shown:
        """Le menu construit par le panneau ; ``exec`` relève l'arbre au lieu de bloquer."""

        def __init__(self, menu: QMenu) -> None:
            self._menu = menu

        def __getattr__(self, name: str):
            return getattr(self._menu, name)

        def exec(self, *_args, **_kwargs):  # noqa: A003 - Qt
            shown.append(menu_tree(self._menu))

    class _Builder(project_panel.AssetContextMenuBuilder):
        def build(self, parent=None):
            return _Shown(super().build(parent))

    monkeypatch.setattr(project_panel, "AssetContextMenuBuilder", _Builder)
    panel = window.project_panel
    organization = LibraryOrganization(window.project)
    if organization.get_folder("f") is None:
        organization.create_folder("Rushes", folder_id="f")
        organization.create_tag("Pris", tag_id="t")
    panel.set_library(organization)
    panel.request_asset_context_menu("camA", QPoint(0, 0))
    return shown.pop()


def load_scenario(window, monkeypatch) -> dict[str, str]:
    """Projet de relevé : une vidéo simple, un segment multicam, deux pistes audio. Renvoie les identifiants utiles."""
    from multicam_stubs import keep_preview_player_off_the_disk

    from core.multicam_ops import AngleSpec, create_multicam_source, insert_multicam_clip
    from core.project_model import Clip, MediaAsset, Project, Track

    def video(asset_id: str) -> MediaAsset:
        return MediaAsset(asset_id, f"/media/{asset_id}.mp4", asset_id, 60.0, 1920, 1080, 30.0, "video", True)

    project = Project(
        "Relevé", media_assets=[video("camA"), video("camB")],
        tracks=[Track("V1", "V1", "video"), Track("V2", "V2", "video"), Track("A1", "A1", "audio"),
                Track("A2", "A2", "audio")],
    )
    source = create_multicam_source(project, [AngleSpec(asset_id="camA", name="A"), AngleSpec(asset_id="camB", name="B")],
                                    name="Concert")
    segment = insert_multicam_clip(project, source.id, "V2", 0.0, angle_id="angle-1")
    plain = Clip("plain", "camA", "V1", 0.0, 0.0, 5.0)
    project.tracks[0].clips.append(plain)
    keep_preview_player_off_the_disk(window, monkeypatch)
    window.project = project
    window.history.reset(project)
    window.timeline_panel.set_project(project)
    window._update_timeline_duration()
    return {"plain": plain.id, "multicam": segment.id}


def full_dump(window, monkeypatch) -> dict:
    """Menus de la barre, boutons, et menus contextuels (clip simple, segment multicam, pistes, média)."""
    ids = load_scenario(window, monkeypatch)
    shown = capture_menus(monkeypatch)
    out = window_dump(window)
    out["clip_menu"] = clip_menu_dump(window, ids["plain"], shown)
    out["multicam_clip_menu"] = clip_menu_dump(window, ids["multicam"], shown)
    out["video_track_menu"] = track_menu_dump(window, "V1")
    out["audio_track_menu"] = track_menu_dump(window, "A1")
    out["asset_menu"] = asset_menu_dump(window, monkeypatch, shown)
    return out
