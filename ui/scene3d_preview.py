"""Aperçu d'un clip « scène 3D » dans le moniteur : mode de rendu, sonde GPU en arrière-plan, badge « approximatif » (lot B3).

Les règles de rendu (image figée, aucun rendu en lecture, carte de substitution) sont dans :mod:`core.scene3d_interactive` ;
ce module n'en garde que la part d'interface :

- :func:`monitor_scene3d` (appelé par ``MotionGraphicsMixin._refresh_viewer_graphics``) : mode de rendu du moniteur
  (``frozen`` en lecture, ``interactive`` à l'arrêt), lancement de la sonde au premier clip 3D vu, badge ;
- :class:`GpuProbe` : la sonde ``kut3d.gpu.availability.probe_qrhi`` (jusqu'à 30 s : sous-processus) tourne dans un fil Python
  ordinaire qui ne touche à AUCUN objet Qt et ne dépose qu'un état simple ; le fil principal le lit avec un ``QTimer`` (aucun
  ``QObject`` ne vit ni ne meurt dans le fil de travail : pas d'interblocage Qt + GIL). Tant qu'elle n'a pas répondu, l'aperçu
  est un petit rendu numpy ; ensuite ``auto`` (GPU si la sonde a réussi, sinon numpy) ;
- :func:`set_scene3d_badge` : l'étiquette « Aperçu approximatif » du moniteur.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QLabel

from core.scene3d import MODE_INTERACTIVE, problem
from core.scene3d_interactive import INTERIM_SIDE, MODE_FROZEN, BackendChoice, set_backend_provider
from ui.i18n import translate
from ui.theme import COLORS

LOGGER = logging.getLogger(__name__)

POLL_MS = 400
"""Intervalle du ``QTimer`` qui attend la réponse de la sonde (il s'arrête dès qu'elle est là)."""

BADGE_NAME = "scene3d_preview_badge"


class GpuProbe:
    """Sonde du moteur GPU, lancée au plus une fois, hors du fil principal. Un état simple, lisible de partout."""

    IDLE, RUNNING, READY, UNAVAILABLE = "idle", "running", "ready", "unavailable"

    def __init__(self, probe: Callable[..., Any] | None = None) -> None:
        self._lock = threading.Lock()
        self._probe = probe
        self.status = self.IDLE
        self.reason = ""

    def start(self, platform: str | None) -> bool:
        """Lance la sonde dans un fil de travail si elle ne l'a pas déjà été ; rend la main tout de suite. ``True`` si lancée ici."""
        with self._lock:
            if self.status != self.IDLE:
                return False
            self.status = self.RUNNING
        threading.Thread(target=self._run, args=(platform,), name="kut3d-gpu-probe", daemon=True).start()
        return True

    def _run(self, platform: str | None) -> None:
        ok, reason = False, ""
        try:
            probe = self._probe
            if probe is None:
                from kut3d.gpu import availability

                probe = availability.probe_qrhi          # cherchée à l'appel (un test la remplace par une sonde lente)
            result = probe(platform=platform)
            ok, reason = bool(result.ok), str(result.reason)
        except Exception as exc:                    # la sonde ne doit jamais emporter l'application
            reason = f"{type(exc).__name__}: {exc}"
        with self._lock:
            self.reason = reason
            self.status = self.READY if ok else self.UNAVAILABLE

    @property
    def done(self) -> bool:
        return self.status in (self.READY, self.UNAVAILABLE)

    def choice(self) -> BackendChoice:
        """``(moteur, côté)`` de l'aperçu : ``auto`` une fois la sonde réussie (le résultat est en cache : jamais d'attente)."""
        if self.status == self.READY:
            return "auto", None
        return "numpy", INTERIM_SIDE


class _Controller(QObject):
    """Un par fenêtre : la sonde, le ``QTimer`` d'attente (fil principal) et le rappel « moteur connu »."""

    def __init__(self, window: Any, probe: GpuProbe | None = None) -> None:
        super().__init__(window)
        self.window = window
        self.probe = probe if probe is not None else GpuProbe()
        self.timer = QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self._poll)
        set_backend_provider(self.probe.choice)

    def ensure_probe(self) -> None:
        from kut3d.gpu.availability import resolve_platform

        if self.probe.status != GpuProbe.IDLE:
            return
        self.probe.start(resolve_platform())            # lu ici, sur le fil principal (il interroge l'application Qt)
        self.timer.start()

    def _poll(self) -> None:
        if not self.probe.done:
            return
        self.timer.stop()
        LOGGER.info("Moteur d'aperçu 3D : %s%s", self.probe.choice()[0], f" ({self.probe.reason})" if self.probe.reason else "")
        refresh = getattr(self.window, "_schedule_viewer_graphics", None)
        if refresh is not None:
            refresh()                                   # l'image du moteur définitif remplace l'image numpy provisoire


def controller_of(window: Any) -> _Controller:
    """Le contrôleur de la fenêtre (créé au premier besoin)."""
    controller = getattr(window, "_scene3d_preview", None)
    if controller is None:
        controller = _Controller(window)
        window._scene3d_preview = controller
    return controller


def scene3d_layers_at(plan: Any) -> list[Any]:
    """Les calques 3D dessinés par le moniteur : ``role == "draw"`` d'un plan déjà limité à l'instant (pistes visibles / solo,
    clips activés, fenêtre temporelle : les règles de ``build_render_plan``) et dont la scène se lit (sinon c'est la carte)."""
    layers = []
    for layer in getattr(plan, "graphics_layers", ()) or ():
        graphic = getattr(layer, "graphic", None)
        if getattr(layer, "role", "") != "draw" or getattr(graphic, "type", None) != "scene3d":
            continue
        if problem(graphic.source_path) is None:
            layers.append(layer)
    return layers


def set_scene3d_badge(panel: Any, visible: bool) -> None:
    """Montre ou cache l'étiquette « Aperçu approximatif » du moniteur (créée au premier besoin, dans la grille du moniteur)."""
    badge = getattr(panel, "scene3d_badge", None)
    if badge is None:
        if not visible:
            return
        container = panel.graphics_view.parentWidget() or panel
        badge = QLabel("", container)
        badge.setObjectName(BADGE_NAME)
        badge.setStyleSheet(
            "background: %s; color: %s; border-radius: 6px; padding: 3px 8px;" % (COLORS["accent_dark"], COLORS["accent"]))
        badge.hide()
        panel.scene3d_badge = badge
        layout = container.layout()
        if layout is not None:
            try:
                layout.addWidget(badge, 0, 0, Qt.AlignTop | Qt.AlignRight)
            except TypeError:                           # un layout qui n'est pas une grille : le badge reste flottant
                LOGGER.debug("Badge 3D hors grille : posé sans layout", exc_info=True)
    badge.setText(translate("scene3d.preview.approximate"))
    badge.setToolTip(translate("scene3d.preview.approximate.tooltip"))
    if visible:
        badge.show()
        badge.raise_()
    else:
        badge.hide()


def monitor_scene3d(window: Any, plan: Any, playing: bool) -> str:
    """Mode de rendu des calques 3D du moniteur pour ce plan ; lance la sonde au premier clip 3D ; gère le badge.

    ``plan`` ``None`` : aucun calque dessiné (badge masqué). Ne bloque jamais : la sonde tourne en arrière-plan.
    """
    panel = getattr(window, "preview_panel", None)
    try:
        layers = scene3d_layers_at(plan) if plan is not None else []
        if layers:
            controller_of(window).ensure_probe()
        if panel is not None:
            set_scene3d_badge(panel, bool(layers))
    except Exception:                                   # l'habillage de l'aperçu ne doit jamais empêcher de dessiner les calques
        LOGGER.warning("Badge ou sonde de l'aperçu 3D en échec", exc_info=True)
    return MODE_FROZEN if playing else MODE_INTERACTIVE


__all__ = ["BADGE_NAME", "GpuProbe", "controller_of", "monitor_scene3d", "scene3d_layers_at", "set_scene3d_badge"]
