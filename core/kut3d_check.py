"""Auto-contrôle de Kut-3D pour le smoke test de l'application construite (sans fenêtre ni sonde GPU)."""

from __future__ import annotations


def kut3d_self_check() -> str:  # i18n-ignore: sortie console du smoke test de build, pas l'interface
    """``""`` si `kut3d` est embarqué, ses shaders `.qsb` sont lisibles et un triangle se rend (numpy) ; sinon la cause."""
    try:
        import numpy as np

        from kut3d.errors import RenderBackendUnavailable
        from kut3d.gpu import shader_files
        from kut3d.model import Material, Mesh, Model, Node
        from kut3d.render import render_frame
        from kut3d.scene import AmbientLight, Camera, Instance, Scene
    except Exception as error:  # noqa: BLE001 - un module absent de l'empaquetage ne doit pas faire planter le contrôle
        return f"kut3d est absent ou illisible dans l'application ({type(error).__name__}: {error})"
    try:
        for name in shader_files.SOURCES:
            if not shader_files.load_qsb(name):
                return f"shader {name}.qsb vide"
    except RenderBackendUnavailable as error:
        return f"shaders de kut3d absents de l'application : {error}"
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
            materials=(Material(base_color=(1.0, 1.0, 1.0, 1.0), roughness=1.0, double_sided=True),),
        )
        scene = Scene([Instance(model)], [AmbientLight((1.0, 1.0, 1.0), 1.0)], Camera((0, 0, 3), (0, 0, 0)))
        frame = render_frame(scene, 0.0, (16, 16))
    except Exception as error:  # noqa: BLE001 - toute cause est rapportée au smoke test
        return f"rendu numpy de kut3d impossible ({type(error).__name__}: {error})"
    centre = frame.color[8, 8]
    if centre[3] != 255 or centre[0] < 200 or frame.color[0, 0][3] != 0:
        return f"rendu numpy de kut3d inattendu (centre {centre.tolist()})"
    return ""
