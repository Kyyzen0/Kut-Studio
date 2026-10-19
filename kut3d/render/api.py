"""Point d'entrée `render_frame`."""

from __future__ import annotations

from kut3d.errors import RenderBackendUnavailable
from kut3d.render.frame import Frame, check_request, get_backend
from kut3d.scene import Scene

AUTO = "auto"


def render_frame(
    scene: Scene,
    t: float = 0.0,
    size: tuple[int, int] = (640, 360),
    *,
    backend: str = "numpy",
    samples: int = 1,
) -> Frame:
    """Rend `scene` à l'instant `t` (secondes) en `size` = (largeur, hauteur).

    `samples` = sur-échantillonnage par axe (1, 2 ou 4). `backend` : "numpy" (référence, partout), "qrhi" (GPU,
    fil principal ; lève `RenderBackendUnavailable` s'il est inutilisable) ou "auto" (qrhi si la sonde GPU réussit et
    qu'on est dans le fil principal, sinon numpy). Lève `RenderError` (une `ValueError`) si la demande dépasse les
    bornes (`kut3d.render.limits`) ou si le moteur est inconnu.
    """
    if not isinstance(scene, Scene):
        raise ValueError("scene doit être une Scene.")
    if backend == AUTO:
        return _render_auto(scene, t, size, samples)
    fn = get_backend(backend)
    checked_size, checked_samples, checked_t = check_request(size, samples, t)
    return fn(scene, checked_t, checked_size, checked_samples)


def _render_auto(scene: Scene, t: float, size: tuple[int, int], samples: int) -> Frame:
    from kut3d.gpu.availability import auto_prefers_gpu

    checked_size, checked_samples, checked_t = check_request(size, samples, t)
    if auto_prefers_gpu():
        try:
            return get_backend("qrhi")(scene, checked_t, checked_size, checked_samples)
        except RenderBackendUnavailable:
            pass  # ex. image trop grande pour ce GPU : la référence numpy sait la rendre
    return get_backend("numpy")(scene, checked_t, checked_size, checked_samples)
