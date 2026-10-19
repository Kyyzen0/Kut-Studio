"""Géométrie et matrices du cube (numpy pur). Main droite, Y vers le haut, caméra regardant vers -Z."""

from __future__ import annotations

import numpy as np

# Constantes de scène partagées par toutes les voies de rendu (pour qu'elles soient comparables).
CAMERA_POS = (0.0, 0.0, 4.0)
FOV_DEG = 45.0
NEAR = 0.1
FAR = 20.0
LIGHT_DIR = (0.4, 0.7, 0.6)  # direction VERS la lumière, monde
AMBIENT = 0.3
BACKGROUND = (30, 34, 44, 255)

# (normale, axe u, axe v) par face : u x v = normale (face vue de l'extérieur, sens trigonométrique).
_FACES = (
    ((0, 0, 1), (1, 0, 0), (0, 1, 0)),
    ((0, 0, -1), (-1, 0, 0), (0, 1, 0)),
    ((1, 0, 0), (0, 0, -1), (0, 1, 0)),
    ((-1, 0, 0), (0, 0, 1), (0, 1, 0)),
    ((0, 1, 0), (1, 0, 0), (0, 0, -1)),
    ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
)


def cube_geometry() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Retourne (positions 24x3, uv 24x2, normales 24x3, indices 36 uint32) d'un cube de côté 2."""
    pos: list[list[float]] = []
    uvs: list[list[float]] = []
    nrm: list[list[float]] = []
    idx: list[int] = []
    for f, (n, u, v) in enumerate(_FACES):
        n_a, u_a, v_a = (np.array(a, dtype=np.float32) for a in (n, u, v))
        for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            pos.append((n_a + su * u_a + sv * v_a).tolist())
            uvs.append([(su + 1) / 2, 1 - (sv + 1) / 2])
            nrm.append(n_a.tolist())
        b = 4 * f
        idx += [b, b + 1, b + 2, b, b + 2, b + 3]
    return (
        np.array(pos, np.float32),
        np.array(uvs, np.float32),
        np.array(nrm, np.float32),
        np.array(idx, np.uint32),
    )


def model_matrix(angle_deg: float) -> np.ndarray:
    """Matrice modèle 4x4 : rotation de angle_deg autour de Y puis inclinaison fixe de 25° autour de X."""
    a = np.radians(angle_deg)
    t = np.radians(25.0)
    ry = np.array(
        [
            [np.cos(a), 0, np.sin(a), 0],
            [0, 1, 0, 0],
            [-np.sin(a), 0, np.cos(a), 0],
            [0, 0, 0, 1],
        ],
        np.float32,
    )
    rx = np.array(
        [
            [1, 0, 0, 0],
            [0, np.cos(t), -np.sin(t), 0],
            [0, np.sin(t), np.cos(t), 0],
            [0, 0, 0, 1],
        ],
        np.float32,
    )
    return (rx @ ry).astype(np.float32)


def view_matrix() -> np.ndarray:
    """Matrice vue 4x4 : translation inverse de la caméra (placée sur +Z, regardant l'origine)."""
    m = np.eye(4, dtype=np.float32)
    m[:3, 3] = -np.array(CAMERA_POS, np.float32)
    return m


def perspective_matrix(aspect: float) -> np.ndarray:
    """Projection perspective 4x4 main droite, profondeur NDC OpenGL [-1, 1] (z_clip = Az + B, w = -z)."""
    f = 1.0 / np.tan(np.radians(FOV_DEG) / 2)
    m = np.zeros((4, 4), np.float32)
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (FAR + NEAR) / (NEAR - FAR)
    m[2, 3] = 2 * FAR * NEAR / (NEAR - FAR)
    m[3, 2] = -1.0
    return m
