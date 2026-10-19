"""Point d'entrée `render_frame`."""

from __future__ import annotations

from kut3d.render.frame import Frame, check_request, get_backend
from kut3d.scene import Scene


def render_frame(
    scene: Scene,
    t: float = 0.0,
    size: tuple[int, int] = (640, 360),
    *,
    backend: str = "numpy",
    samples: int = 1,
) -> Frame:
    """Rend `scene` à l'instant `t` (secondes) en `size` = (largeur, hauteur).

    `samples` = sur-échantillonnage par axe (1, 2 ou 4). Lève `RenderError` (une `ValueError`) si la demande
    dépasse les bornes (`kut3d.render.limits`) ou si le moteur est inconnu.
    """
    fn = get_backend(backend)
    if not isinstance(scene, Scene):
        raise ValueError("scene doit être une Scene.")
    checked_size, checked_samples, checked_t = check_request(size, samples, t)
    return fn(scene, checked_t, checked_size, checked_samples)
