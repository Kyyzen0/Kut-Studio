"""Sonde GPU, exécutée dans un SOUS-PROCESSUS : `python -m kut3d.gpu.probe_worker`.

Rend un minuscule triangle avec le vrai pipeline (shaders, cibles flottantes, deux cibles de couleur, relecture) et
produit un résultat JSON `{"ok": true, "backend": "Metal"}` ou `{"ok": false, "reason": "..."}`. Un plantage ou un
blocage du pilote ne tue ainsi que ce processus (le père applique un délai). Code de sortie : 0 si le rendu est
correct, 2 sinon.

Canal du résultat : avec `--kut3d-gpu-probe-out <fichier>` (application empaquetée), le JSON est écrit dans ce fichier
(écriture atomique, UTF-8) ; sans lui, sur une ligne de stdout. Le fichier est indispensable gelé : en mode fenêtré de
PyInstaller (Windows), `sys.stdout` et `sys.stderr` valent `None` et `print` n'écrit alors nulle part.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence

# Application empaquetée (PyInstaller) : pas de `python -m` ; l'application se relance avec ce drapeau, reconnu en tête
# de `main.py` par `core.process_supervisor.helper_mode_exit_code`, qui appelle `main()` ci-dessous.
PROBE_FLAG = "--kut3d-gpu-probe"
OUT_FLAG = "--kut3d-gpu-probe-out"  # suivi du chemin du fichier de résultat (dossier temporaire privé du père)


def out_path_from(argv: Sequence[str]) -> str | None:
    """Chemin qui suit `OUT_FLAG` dans `argv`, ou None."""
    if OUT_FLAG in argv:
        index = list(argv).index(OUT_FLAG)
        if index + 1 < len(argv) and argv[index + 1]:
            return str(argv[index + 1])
    return None


def _write_file(path: str, text: str) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def _emit(payload: dict[str, object], out_path: str | None = None) -> None:
    """Écrit le résultat dans le fichier demandé, sinon sur stdout. Ne lève jamais (stdout peut valoir None)."""
    text = json.dumps(payload)
    try:
        if out_path:
            _write_file(out_path, text)
        elif sys.stdout is not None:
            print(text, flush=True)
    except (OSError, ValueError):
        pass


def main(argv: Sequence[str] | None = None) -> int:
    out = out_path_from(sys.argv[1:] if argv is None else argv)
    try:
        import numpy as np
        from kut3d.errors import RenderError
        from kut3d.gpu.limits import PROBE_TRIANGLE_SIZE
        from kut3d.gpu.qtapp import platform_name
        from kut3d.gpu.renderer import get_renderer, shutdown
        from kut3d.model import Material, Mesh, Model, Node
        from kut3d.scene import AmbientLight, Camera, Instance, Scene
    except ImportError as error:
        _emit({"ok": False, "reason": f"import impossible : {error}"}, out)
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
                },
                out,
            )
            return 2
        _emit({"ok": True, "backend": backend, "platform": platform}, out)
        return 0
    except Exception as error:  # noqa: BLE001 - la raison réelle est transmise au père
        own = isinstance(error, RenderError)  # nos messages sont déjà complets
        _emit(
            {
                "ok": False,
                "reason": str(error)[:300]
                if own
                else f"{type(error).__name__}: {str(error)[:300]}",
            },
            out,
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
