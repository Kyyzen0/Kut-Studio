"""Espion de rendu pour les tests de l'aperçu d'un clip « scène 3D » (lot B3)."""

from __future__ import annotations

import kut3d


class Spy:
    """Compte les appels à ``kut3d.render_frame`` (taille et moteur demandés) sans changer le résultat."""

    def __init__(self, monkeypatch, *, fail: Exception | None = None) -> None:
        self.calls: list[dict] = []
        real = kut3d.render_frame

        def spy(scene, t=0.0, size=(640, 360), *, backend="numpy", samples=1):
            self.calls.append({"t": t, "size": tuple(size), "backend": backend})
            if fail is not None:
                raise fail
            return real(scene, t=t, size=size, backend=backend, samples=samples)

        monkeypatch.setattr(kut3d, "render_frame", spy)
