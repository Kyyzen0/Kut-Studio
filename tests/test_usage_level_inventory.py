"""Énumération des contrôles par niveau d'usage (ADR-0006, étape 4, lot 5) : le critère de l'ADR.

    « chaque contrôle appartient à un niveau, vérifié par un test qui énumère les contrôles de l'inspecteur ; aucune
    fonction n'est inaccessible en mode simple (elle est seulement repliée) »

Tout part de la vraie fenêtre hors écran (``tests/usage_level_inventory.py``) : le niveau que le **code** donne à chaque
contrôle (``MENU_GATES``, ``PAGE_LEVELS``, ``SECTION_LEVELS``, ``_group_levels``, ``LevelGate``, ``_level_limits``) est comparé
au manifeste ``tests/data/usage_level_controls.json`` et à ce que l'écran montre à chaque niveau. Un contrôle neuf, absent du
manifeste, fait échouer le premier test : classe-le, puis régénère le manifeste
(``KUT_REGEN_USAGE_MANIFEST=1 pytest tests/test_usage_level_inventory.py -k regenerate``) et fais relire la différence.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest
import usage_level_inventory as inv
from inspector_order import clip_states
from menu_order import capture_menus, load_scenario
from PySide6.QtWidgets import QApplication

from core.usage_level import UsageLevel
from ui import i18n

SIMPLE, STUDIO, ADVANCED = inv.LEVELS
GOLDEN_MENUS = inv.MANIFEST.parent / "menus_before_usage_levels.json"


class World:
    """Une fenêtre réelle, les contrôles déclarés par le code et ceux que l'écran montre à chaque niveau."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ui.main_window import MainWindow

        monkeypatch.setenv(
            "KUT_STUDIO_CONFIG_DIR", tempfile.mkdtemp(prefix="kut-inventory-")
        )
        monkeypatch.setattr(
            "ui.main_window.QMessageBox.information", lambda *_, **__: None
        )
        monkeypatch.setattr(
            "ui.main_window.QMessageBox.critical", lambda *_, **__: None
        )
        i18n.set_language("fr")
        self.window = MainWindow()
        self.window.timeline_timer.stop()
        self.window.set_usage_level("advanced")
        self.monkeypatch = monkeypatch
        self.ids = load_scenario(self.window, monkeypatch)
        self.window.resize(1440, 900)
        self.window.show()
        self.panel = self.window.properties_panel
        self.states = clip_states()
        self.declared: dict[str, UsageLevel] = {
            **inv.declared_surfaces(self.window),
            **inv.declared_inspector(self.panel),
        }
        self.helpers = inv.helper_keys(self.panel)
        self.observed: dict[UsageLevel, set[str]] = {}
        self.opened: dict[UsageLevel, set[str]] = {}
        shown = capture_menus(monkeypatch)
        for level in (
            ADVANCED,
            STUDIO,
            SIMPLE,
        ):  # de haut en bas : le projet de relevé reste celui du niveau Avancé
            self._at(level)
            self.observed[level] = inv.observed_surfaces(
                self.window, monkeypatch, shown, self.ids
            ) | inv.visible_inspector(self.panel, self.states)
            inv.open_everything(self.panel)
            self.opened[level] = inv.visible_inspector(self.panel, self.states)
            inv.close_everything(self.panel)
        self._at(ADVANCED)
        # Un menu contextuel n'a pas de table : son niveau est le premier où l'écran le montre.
        for key in sorted(k for k in self.observed[ADVANCED] if k.startswith("ctx_")):
            self.declared[key] = next(
                lvl for lvl in inv.LEVELS if key in self.observed[lvl]
            )

    def _at(self, level: UsageLevel) -> None:
        self.window.set_usage_level(level)
        QApplication.processEvents()

    def close(self) -> None:
        self.window.close()


@pytest.fixture(scope="module")
def world(qapp):
    patch = pytest.MonkeyPatch()
    built = World(patch)
    yield built
    built.close()
    patch.undo()


def _manifest() -> dict[str, str]:
    return inv.load_manifest()


def _fmt(keys, limit: int = 12) -> str:
    keys = sorted(keys)
    return ", ".join(keys[:limit]) + (
        f" … (+{len(keys) - limit})" if len(keys) > limit else ""
    )


# ---------------------------------------------------------------- (a), (b), (e) : tout contrôle est classé


def test_every_control_has_an_explicit_level_in_the_manifest(world):
    manifest = _manifest()
    declared = {key: level.value for key, level in world.declared.items()}
    unclassified = set(declared) - set(manifest)
    assert not unclassified, (
        "contrôle sans classement : donne-lui un niveau dans le code (MENU_GATES, PAGE_LEVELS, SECTION_LEVELS, "
        f"_group_levels, LevelGate…), puis ajoute-le au manifeste : {_fmt(unclassified)}"
    )
    vanished = set(manifest) - set(declared)
    assert not vanished, (
        f"contrôle du manifeste qui n'existe plus (retire-le ou rends-lui sa clé) : {_fmt(vanished)}"
    )
    differing = {k for k in manifest if manifest[k] != declared[k]}
    assert not differing, (
        f"niveau du code différent du manifeste : {_fmt(f'{k}: {declared[k]} != {manifest[k]}' for k in differing)}"
    )


def test_the_menu_gates_all_find_their_entry(world):
    assert world.window._menu_level_unresolved == []


def test_the_manifest_is_not_empty_on_any_surface(world):
    prefixes = {key.split("/")[0] for key in _manifest()}
    assert {
        "file_menu",
        "edit_menu",
        "timeline_menu",
        "layers_menu",
        "social_menu",
        "window_menu",
        "help_menu",
        "toolbar",
        "rail",
        "pages",
        "inspector",
        "ctx_clip",
        "ctx_multicam",
        "ctx_video_track",
        "ctx_audio_track",
        "ctx_asset",
        "ctx_layer",
        "ctx_layer_group",
        "ctx_layer_background",
        "ctx_layer_add_button",
        "ctx_folder",
        "ctx_folder_synthetic",
        "ctx_folder_empty",
        "ctx_lane_point",
        "ctx_lane_band",
        "ctx_color_node",
        "ctx_comp_node",
        "layers_panel",
    } <= prefixes


@pytest.mark.parametrize("level", inv.LEVELS, ids=lambda level: level.value)
def test_a_control_is_shown_at_its_level_and_above_and_never_below(world, level):
    """Pas d'orphelin : ce que l'écran montre à ce niveau est classé, au plus ce niveau ; et tout ce qui est classé à ce
    niveau ou au-dessous y est montré (sauf les contrôles de navigation qui changent de côté à dessein)."""
    manifest = _manifest()
    shown = world.observed[level]
    orphans = {k for k in shown if k not in manifest}
    assert not orphans, f"affiché sans classement : {_fmt(orphans)}"
    too_low = {k for k in shown if not level.at_least(UsageLevel(manifest[k]))}
    assert not too_low, f"affiché au-dessous de son niveau : {_fmt(too_low)}"
    expected = {
        k
        for k, v in manifest.items()
        if level.at_least(UsageLevel(v))
        and k in world.observed[ADVANCED] | world.helpers
    }
    missing = {k for k in expected - shown if k not in world.helpers}
    assert not missing, f"classé à ce niveau mais absent de l'écran : {_fmt(missing)}"


# ---------------------------------------------------------------- (c) monotonie, (d) Avancé = origin/main


def test_each_level_shows_at_least_what_the_level_below_shows(world):
    for lower, upper in ((SIMPLE, STUDIO), (STUDIO, ADVANCED)):
        lost = {
            k
            for k in world.observed[lower] - world.observed[upper]
            if k not in world.helpers
        }
        assert not lost, (
            f"{lower.value} montre ce que {upper.value} ne montre plus : {_fmt(lost)}"
        )
    plain = {level: world.observed[level] - world.helpers for level in inv.LEVELS}
    assert (
        plain[SIMPLE] < plain[STUDIO] < plain[ADVANCED]
    )  # chaque niveau montre strictement plus que le précédent


def test_the_navigation_helpers_are_hidden_at_advanced_and_only_those_change_side(
    world,
):
    shown_advanced = {k for k in world.helpers if k in world.observed[ADVANCED]}
    folds = {
        k
        for k in world.helpers
        if "AdvancedFold" in k or "advanced_fold" in k or "more_options" in k
    }
    assert not (folds & shown_advanced), (
        f"pliant ou lien du bas visible au niveau Avancé : {_fmt(folds & shown_advanced)}"
    )
    for lower, upper in ((SIMPLE, STUDIO), (STUDIO, ADVANCED)):
        moved = {k for k in world.observed[lower] - world.observed[upper]}
        assert moved <= world.helpers, (
            f"change de côté sans être un contrôle de navigation : {_fmt(moved - world.helpers)}"
        )


def test_advanced_shows_what_origin_main_showed(world):
    """Le niveau Avancé : les menus, boutons et menus contextuels du golden d'origin/main, rien de plus ni de moins."""
    golden = json.loads(GOLDEN_MENUS.read_text(encoding="utf-8"))
    expected: list[str] = []
    for name, tree in golden["menus"].items():
        expected.extend(inv._tree_keys(name, tree))
    expected.extend(f"toolbar/{n}" for n in golden["toolbar"])
    expected.extend(f"rail/{n}" for n in golden["rail"])
    expected.extend(f"pages/{n}" for n in golden["pages"])
    for name, key in (
        ("clip_menu", "ctx_clip"),
        ("multicam_clip_menu", "ctx_multicam"),
        ("video_track_menu", "ctx_video_track"),
        ("audio_track_menu", "ctx_audio_track"),
        ("asset_menu", "ctx_asset"),
    ):
        expected.extend(inv._tree_keys(key, golden[name]))
    # Les menus contextuels du lot « Calques, dossiers, automation, nœuds » et les boutons du panneau Calques n'existent pas dans
    # le golden (jamais régénéré sur une branche) : ils sont vérifiés par le manifeste seul.
    later = (
        "inspector/",
        "layers_panel/",
        "ctx_layer",
        "ctx_folder",
        "ctx_lane",
        "ctx_color",
        "ctx_comp",
    )
    surfaces = {k for k in world.observed[ADVANCED] if not k.startswith(later)}
    assert surfaces == set(expected), _fmt(surfaces ^ set(expected))


def test_advanced_keeps_the_inspector_of_origin_main(qtbot):
    """Inspecteur au niveau Avancé : l'ordre et l'ensemble d'avant les niveaux (golden produit sur origin/main).

    Inspecteur neuf : la fenêtre du relevé garde l'état du dernier clip montré (le golden part d'aucun clip)."""
    import inspector_order
    from ui.properties_panel import PropertiesPanel

    panel = PropertiesPanel(lambda *_args: None)
    qtbot.addWidget(panel)
    panel.resize(320, 900)
    panel.show()
    panel.set_usage_level(ADVANCED)
    golden = json.loads(
        (inv.MANIFEST.parent / "inspector_order_before_usage_levels.json").read_text(
            encoding="utf-8"
        )
    )
    assert inspector_order.ordered_dump(panel, inspector_order.clip_states()) == golden


# ---------------------------------------------------------------- aucune fonction n'est inaccessible


def test_with_the_gestures_of_the_adr_every_inspector_control_is_reachable_from_simple(
    world,
):
    """« Afficher plus d'options » puis les pliants « Avancé » ouverts rendent accessible tout ce que l'Avancé montre."""
    advanced = {k for k in world.observed[ADVANCED] if k.startswith("inspector/")}
    for level in inv.LEVELS:
        unreachable = {
            k for k in advanced - world.opened[level] if k not in world.helpers
        }
        assert not unreachable, (
            f"inaccessible depuis {level.value} même avec les gestes : {_fmt(unreachable)}"
        )


# ---------------------------------------------------------------- régénération du manifeste


@pytest.mark.skipif(
    not os.environ.get("KUT_REGEN_USAGE_MANIFEST"),
    reason="régénération explicite du manifeste seulement",
)
def test_regenerate_the_manifest(world):
    data = {key: level.value for key, level in sorted(world.declared.items())}
    inv.MANIFEST.write_text(
        json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
