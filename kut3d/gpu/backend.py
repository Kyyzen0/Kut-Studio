"""Fabrique paresseuse du moteur « qrhi » : PySide6 n'est importé qu'au premier rendu."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kut3d.gpu.availability import require_main_thread, require_qrhi

if TYPE_CHECKING:
    from kut3d.render.frame import Frame
    from kut3d.scene import Scene


def render_lazy(scene: Scene, t: float, size: tuple[int, int], samples: int) -> Frame:
    """Moteur « qrhi » : vérifie le fil principal, puis la sonde (sous-processus, en cache), puis rend.

    Lève `RenderError` hors du fil principal et `RenderBackendUnavailable` (avec la raison réelle) si le GPU n'est pas
    utilisable. Le module Qt (`kut3d.gpu.renderer`) n'est importé qu'ici.
    """
    require_main_thread()
    require_qrhi()
    from kut3d.gpu.renderer import render_qrhi

    return render_qrhi(scene, t, size, samples)
