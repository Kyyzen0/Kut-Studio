"""Sonde GPU, exécutée dans un SOUS-PROCESSUS : `python -m kut3d.gpu.probe_worker`.

Rend un minuscule triangle avec le vrai pipeline (shaders, cibles flottantes, deux cibles de couleur, relecture) et
affiche une ligne JSON `{"ok": true, "backend": "Metal"}` ou `{"ok": false, "reason": "..."}`. Un plantage ou un
blocage du pilote ne tue ainsi que ce processus (le père applique un délai). Code de sortie : 0 si le rendu est
correct, 2 sinon.
"""

from __future__ import annotations

import json
import sys


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload), flush=True)


def main() -> int:
    try:
        import numpy as np
        from kut3d.errors import RenderError
        from kut3d.gpu.limits import PROBE_TRIANGLE_SIZE
        from kut3d.gpu.qtapp import platform_name
        from kut3d.gpu.renderer import get_renderer, shutdown
        from kut3d.model import Material, Mesh, Model, Node
        from kut3d.scene import AmbientLight, Camera, Instance, Scene
    except ImportError as error:
        _emit({"ok": False, "reason": f"import impossible : {error}"})
        return 2
    try:
        mesh = Mesh(
            np.array([[-1, -1, 0], [1, -1, 0], [0, 1, 0]], dtype=np.float32),
            np.array([[0, 1, 2]], dtype=np.int64),
            material_index=0,
        )
        model = Model(
            nodes=(Node(mesh_index=0),),
            root_nodes=(0,),
            meshes=(mesh,),
            materials=(
                Material(
                    base_color=(1.0, 1.0, 1.0, 1.0), roughness=1.0, double_sided=True
                ),
            ),
        )
        scene = Scene(
            [Instance(model)],
            [AmbientLight((1.0, 1.0, 1.0), 1.0)],
            Camera((0, 0, 3), (0, 0, 0)),
        )
        renderer = get_renderer()
        frame = renderer.render(
            scene, 0.0, (PROBE_TRIANGLE_SIZE, PROBE_TRIANGLE_SIZE), 1
        )
        centre = frame.color[PROBE_TRIANGLE_SIZE // 2, PROBE_TRIANGLE_SIZE // 2]
        corner = frame.color[0, 0]
        ok = bool(centre[3] == 255 and centre[0] > 200 and corner[3] == 0)
        backend = renderer.backend_name
        platform = platform_name()
        shutdown()
        if not ok:
            _emit(
                {
                    "ok": False,
                    "reason": f"image de sonde inattendue (centre {centre.tolist()}, coin {corner.tolist()}).",
                }
            )
            return 2
        _emit({"ok": True, "backend": backend, "platform": platform})
        return 0
    except Exception as error:  # noqa: BLE001 - la raison réelle est transmise au père
        own = isinstance(error, RenderError)  # nos messages sont déjà complets
        _emit(
            {
                "ok": False,
                "reason": str(error)[:300]
                if own
                else f"{type(error).__name__}: {str(error)[:300]}",
            }
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
