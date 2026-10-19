"""Moteur GPU de Kut-3D (phase 2b), PARTIE AVEC GPU : le moteur « qrhi » rend la même chose que la référence numpy.

Les tests de ce module sautent (avec la RAISON RÉELLE de la sonde) quand aucun GPU n'est utilisable. La suite de
tests force la plateforme Qt `offscreen`, qui n'a pas de GPU : les rendus se font donc dans un sous-processus à
plateforme Qt native (`tests/kut3d_gpu_worker.py`), qui appelle `render_frame(backend="qrhi")` comme le fait un
programme ordinaire, puis les images sont comparées ici à `backend="numpy"`.

Tolérances MESURÉES sur macOS / Metal (la mesure complète est dans `docs/kut3d-render.md`) et justifiées :
- couleur : sur les 27 scènes, 99,7 % à 100 % des pixels à +-6 niveaux (écart maximal de 1 ou 2 niveaux hors arêtes) (float32 contre float64) ; on exige
  99,5 % à +-6 niveaux. Les pixels restants sont des BORDS : un pixel dont le centre est exactement sur une arête
  (diagonale d'un carré, arête d'un maillage miroir) est tranché autrement en float32 par le GPU qu'en float64 par numpy ;
- alpha : même cause (couverture d'un pixel d'arête, jusqu'à 1/4 de pixel avec 4 échantillons) : 99,5 % à +-0,02 ;
- profondeur : relative <= 1e-3 (float32 ; mesuré <= 1,6e-4 sur les scènes simples) pour 99,9 % des pixels couverts par
  les deux (sur le terrain de 20 000 triangles, 4 pixels d'arête sur 920 000 choisissent le triangle voisin, à une autre
  profondeur : écart maximal 11 %), et au plus 0,5 % de pixels vide pour l'un et pas pour l'autre.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from kut3d import Frame, render_frame
from kut3d.gpu import NATIVE, probe_qrhi
from tests.kut3d_gpu_cases import CASES
from tests.kut3d_scenes import lin_to_srgb255

ROOT = Path(__file__).resolve().parent.parent
# Sur la CI, ces tests ne tournent que sur demande (KUT3D_GPU_TESTS=1) : un runner peut exposer un GPU
# paravirtualisé, dont la sonde réussit sans que le rendu soit fiable. La sonde n'est alors même pas lancée.
CI_OPT_OUT = bool(os.environ.get("CI")) and os.environ.get("KUT3D_GPU_TESTS") != "1"
PROBE = None if CI_OPT_OUT else probe_qrhi(platform=NATIVE)
pytestmark = pytest.mark.skipif(
    PROBE is None or not PROBE.ok,
    reason=(
        "tests GPU désactivés sur la CI (KUT3D_GPU_TESTS=1 pour les lancer)"
        if PROBE is None
        else f"moteur GPU indisponible : {PROBE.reason}"
    ),
)

COLOR_TOL, COLOR_FRACTION = 6, 0.995
ALPHA_TOL, ALPHA_FRACTION = 0.02, 0.995
DEPTH_REL, DEPTH_FRACTION, DEPTH_MISMATCH = 1e-3, 0.999, 0.005


def _native_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
    env["PYTHONPATH"] = str(ROOT)
    return env


def _run_python(
    code: str, *args: str, timeout: float = 240
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code, *args],
        cwd=ROOT,
        env=_native_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class GpuRun:
    """Résultat du worker : les images rendues par le GPU, par nom de cas, et les informations annexes."""

    def __init__(self, path: Path) -> None:
        data = np.load(path)
        self.info: dict[str, Any] = json.loads(bytes(data["info"]).decode())
        self.frames = {
            name: Frame(
                data[f"{name}/color"].copy(), data[f"{name}/alpha"].copy(), data[f"{name}/depth"].copy(),
                (data[f"{name}/color"].shape[1], data[f"{name}/color"].shape[0]),
            )
            for name in CASES
        }  # fmt: skip


@pytest.fixture(scope="module")
def gpu(tmp_path_factory: pytest.TempPathFactory) -> GpuRun:
    out = tmp_path_factory.mktemp("kut3d_gpu") / "frames.npz"
    done = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "kut3d_gpu_worker.py"), str(out)],
        cwd=ROOT, env=_native_env(), capture_output=True, text=True, timeout=600,
    )  # fmt: skip
    # Le code de sortie 0 prouve aussi le démontage propre (shutdown() puis fin de processus, sans plantage).
    assert done.returncode == 0, (
        f"worker GPU : code {done.returncode}\n{done.stderr[-2000:]}"
    )
    return GpuRun(out)


def numpy_frame(name: str) -> Frame:
    case = CASES[name]
    return render_frame(
        case.build(), case.t, case.size, backend="numpy", samples=case.samples
    )


def compare(name: str, gpu_frame: Frame, ref: Frame) -> dict[str, float]:
    diff = np.abs(ref.color.astype(int) - gpu_frame.color.astype(int)).max(axis=2)
    within = float((diff <= COLOR_TOL).mean())
    alpha_ok = float((np.abs(ref.alpha - gpu_frame.alpha) <= ALPHA_TOL).mean())
    a_fin, g_fin = np.isfinite(ref.depth), np.isfinite(gpu_frame.depth)
    mismatch = float((a_fin != g_fin).mean())
    both = a_fin & g_fin
    rel_all = np.abs(ref.depth[both] - gpu_frame.depth[both]) / ref.depth[both]
    rel = float((rel_all <= DEPTH_REL).mean()) if both.any() else 1.0
    stats = {
        "color": within,
        "alpha": alpha_ok,
        "depth_mismatch": mismatch,
        "depth_within": rel,
    }
    assert within >= COLOR_FRACTION, (
        f"{name} : couleur {within:.4f} < {COLOR_FRACTION} ({stats})"
    )
    assert alpha_ok >= ALPHA_FRACTION, f"{name} : alpha {alpha_ok:.4f} ({stats})"
    assert mismatch <= DEPTH_MISMATCH, (
        f"{name} : profondeur vide/pleine {mismatch:.4f} ({stats})"
    )
    assert rel >= DEPTH_FRACTION, f"{name} : profondeur relative {rel:.4f} ({stats})"
    return stats


@pytest.mark.parametrize("name", [n for n in CASES if n != "terrain_20k"])
def test_gpu_matches_the_numpy_reference(gpu: GpuRun, name: str) -> None:
    compare(name, gpu.frames[name], numpy_frame(name))


def test_gpu_matches_the_numpy_reference_on_a_20000_triangle_720p_scene(
    gpu: GpuRun,
) -> None:
    compare("terrain_20k", gpu.frames["terrain_20k"], numpy_frame("terrain_20k"))


def test_gpu_is_much_faster_than_numpy_on_a_20000_triangle_720p_scene(
    gpu: GpuRun,
) -> None:
    case = CASES["terrain_20k"]
    scene = case.build()
    render_frame(scene, 0.0, case.size, backend="numpy")  # chauffe (textures décodées)
    start = time.perf_counter()
    render_frame(scene, 0.0, case.size, backend="numpy")
    numpy_s = time.perf_counter() - start
    gpu_s = gpu.info["times"]["terrain_20k"]  # 2e image du worker, après chauffe
    print(
        f"\n720p, 20 000 triangles : numpy {numpy_s * 1e3:.0f} ms, GPU ({gpu.info['backend']}) {gpu_s * 1e3:.1f} ms"
    )
    assert gpu_s < numpy_s  # seuil volontairement très large : mesuré ~20x sur Metal


# --- propriétés vérifiées SANS passer par numpy ---------------------------------------------------------------


def test_bone_deformation_bends_the_quad_in_the_vertex_shader(gpu: GpuRun) -> None:
    rest, bent = gpu.frames["skin_rest"], gpu.frames["skin_ambient"]
    area_rest, area_bent = int((rest.alpha > 0).sum()), int((bent.alpha > 0).sum())
    assert (
        area_bent < area_rest and area_bent > 0
    )  # l'os replié réduit la surface visible (comme test_kut3d_deform)
    assert not np.array_equal(rest.color, bent.color)
    assert gpu.info["backend"]


def test_morph_targets_are_applied_before_the_bones(gpu: GpuRun) -> None:
    """Forme d'abord : sommets (1,0,0), (2,0,0), (1,1,0) ; os ensuite (90 degrés) : (0,1,0), (0,2,0), (-1,1,0)."""
    frame = gpu.frames["morph_then_skin"]
    width, height = frame.size
    cam_y, f = 0.5, 1.0 / np.tan(np.radians(40.0) / 2)

    def pixel(x: float, y: float) -> tuple[int, int]:
        ndc_x = (x / 4.0) * f / (width / height)
        ndc_y = ((y - cam_y) / 4.0) * f
        return int((1 - ndc_y) * 0.5 * height), int((ndc_x + 1) * 0.5 * width)

    right = pixel(-1 / 3, 4 / 3)  # centre du triangle attendu : ((0,1)+(0,2)+(-1,1))/3
    wrong = pixel(
        4 / 3, 1 / 3
    )  # centre du triangle « os d'abord » (forme ajoutée après la rotation)
    assert frame.alpha[right] == 1.0
    assert frame.alpha[wrong] == 0.0


def test_blend_triangles_are_sorted_far_to_near_on_the_gpu(gpu: GpuRun) -> None:
    """Mur bleu opaque, verre vert (z=0) puis verre rouge (z=0,5, devant), 50 % chacun, déclarés dans l'ordre inverse."""
    pixel = gpu.frames["blend_sorted"].color[32, 32]
    lin = np.array(
        [0.5, 0.25, 0.25]
    )  # rouge sur (vert sur bleu) = 0,5 R + 0,5 (0,5 G + 0,5 B)
    expected = [lin_to_srgb255(float(v)) for v in lin]
    assert np.allclose(pixel[:3], expected, atol=2)
    assert pixel[3] == 255
    frame = gpu.frames["blend_sorted"]
    assert frame.depth[32, 32] == pytest.approx(
        2.0, abs=1e-3
    )  # le mur (z=-1) vu de z=1 : le verre n'écrit pas la profondeur


def test_blend_behind_an_opaque_object_is_hidden_by_the_depth_test(gpu: GpuRun) -> None:
    assert tuple(gpu.frames["blend_behind_opaque"].color[32, 32]) == (0, 0, 255, 255)


def test_alpha_mask_cuts_holes_and_shows_what_is_behind(gpu: GpuRun) -> None:
    frame = gpu.frames["mask"]
    assert tuple(frame.color[4, 4]) == (255, 0, 0, 255) and frame.depth[
        4, 4
    ] == pytest.approx(1.0, abs=1e-4)
    assert tuple(frame.color[4, 12]) == (
        0,
        0,
        255,
        255,
    )  # trou : le quad bleu, plus loin (z=-1)
    assert frame.depth[4, 12] == pytest.approx(2.0, abs=1e-3)


def test_background_transparent_by_default_and_opaque_when_asked(gpu: GpuRun) -> None:
    assert gpu.frames["checker"].alpha[0, 0] == 0.0 and tuple(
        gpu.frames["checker"].color[0, 0]
    ) == (0, 0, 0, 0)
    opaque = gpu.frames["background_opaque"]
    assert tuple(opaque.color[0, 0]) == (
        round(0.1 * 255),
        round(0.3 * 255),
        round(0.6 * 255),
        255,
    )
    translucent = gpu.frames["background_translucent"]
    assert translucent.color[0, 0, 3] == round(0.4 * 255) and translucent.alpha[
        0, 0
    ] == pytest.approx(0.4)
    assert np.isinf(opaque.depth[0, 0])  # le fond n'a pas de profondeur


def test_linear_view_depth_and_void(gpu: GpuRun) -> None:
    frame = gpu.frames[
        "depth_two_layers"
    ]  # caméra z=4 : quad rouge à z=0 (distance 4), bleu à z=-2 (distance 6)
    assert frame.depth[32, 32] == pytest.approx(4.0, rel=1e-4)
    assert frame.depth[14, 32] == pytest.approx(
        6.0, rel=1e-4
    )  # au-dessus du rouge, sur le bleu
    assert frame.depth.dtype == np.float32 and np.isinf(
        gpu.frames["checker"].depth[0, 0]
    )


def test_supersampling_keeps_the_size_and_gives_intermediate_alpha_on_edges(
    gpu: GpuRun,
) -> None:
    for name, size in (
        ("supersampled_cube", (160, 120)),
        ("supersampled_alpha4", (80, 60)),
    ):
        frame = gpu.frames[name]
        assert frame.size == size and frame.color.shape == (size[1], size[0], 4)
        edge = (frame.alpha > 0) & (frame.alpha < 1)
        assert edge.sum() > 20  # des pixels d'arête à couverture partielle
    ref = numpy_frame("supersampled_alpha4")
    assert np.abs(gpu.frames["supersampled_alpha4"].alpha - ref.alpha).mean() < 0.002


def test_culling_and_double_sided_on_the_gpu(gpu: GpuRun) -> None:
    frame = gpu.frames[
        "double_sided_and_culled"
    ]  # deux quads vus par DERRIÈRE : rouge simple face (à gauche), vert double
    left, right = (
        frame.alpha[:, : frame.size[0] // 2],
        frame.alpha[:, frame.size[0] // 2 :],
    )
    assert left.max() == 0.0 and right.max() == 1.0


def test_no_window_is_ever_shown_and_the_api_is_reported(gpu: GpuRun) -> None:
    assert gpu.info["windows"] == []
    assert gpu.info["backend"] in {
        "Metal",
        "Direct3D 11",
        "OpenGL",
        "Vulkan",
        "Direct3D 12",
    }, gpu.info["backend"]


def test_repeated_renders_are_identical_and_leave_no_state_behind(gpu: GpuRun) -> None:
    assert gpu.info["repeat_identical"] is True


def test_oversized_request_is_declined_with_a_clear_reason(gpu: GpuRun) -> None:
    assert "taille de texture" in gpu.info["oversize"]


# --- cycle de vie ----------------------------------------------------------------------------------------------

LIFECYCLE = """
import sys
import kut3d
from tests.kut3d_gpu_cases import CASES
mode = sys.argv[1]
if mode == "host":
    from PySide6.QtWidgets import QApplication
    app = QApplication(["hote"])           # application Qt préexistante : le moteur s'en sert telle quelle
    assert app.applicationName() != "kut3d-render"
for name in ("checker", "pbr_lit", "checker", "skin_lit"):
    c = CASES[name]
    f = kut3d.render_frame(c.build(), c.t, c.size, backend="qrhi", samples=c.samples)
    assert f.alpha.max() > 0
if mode == "explicit":
    kut3d.gpu.shutdown()
    kut3d.gpu.shutdown()                    # réentrant
    f = kut3d.render_frame(CASES["checker"].build(), size=(16, 16), backend="qrhi")  # et le moteur renaît
print("ok", mode)
"""


@pytest.mark.parametrize("mode", ["atexit", "explicit", "host"])
def test_process_exits_cleanly_after_several_renders(mode: str) -> None:
    done = _run_python(LIFECYCLE, mode)
    assert done.returncode == 0, done.stderr[-2000:]
    assert f"ok {mode}" in done.stdout


def test_a_host_application_keeps_its_own_name_and_settings() -> None:
    code = (
        "import kut3d\nfrom PySide6.QtWidgets import QApplication\nfrom PySide6.QtCore import QCoreApplication\n"
        "QCoreApplication.setApplicationName('Hote'); QCoreApplication.setOrganizationName('Orga')\n"
        "app = QApplication(['hote'])\n"
        "from tests.kut3d_gpu_cases import CASES\nc = CASES['checker']\n"
        "kut3d.render_frame(c.build(), c.t, (32, 32), backend='qrhi')\n"
        "assert app.applicationName() == 'Hote' and app.organizationName() == 'Orga'\n"
        "assert QApplication.instance() is app\nprint('ok')\n"
    )
    done = _run_python(code)
    assert done.returncode == 0 and "ok" in done.stdout, done.stderr[-2000:]


def test_the_gpu_engine_writes_nothing_in_the_user_configuration(
    tmp_path: Path,
) -> None:
    home, config = tmp_path / "home", tmp_path / "config"
    home.mkdir()
    config.mkdir()
    env = _native_env() | {"HOME": str(home), "KUT_STUDIO_CONFIG_DIR": str(config)}
    code = "import kut3d\nfrom tests.kut3d_gpu_cases import CASES\nc = CASES['checker']\nkut3d.render_frame(c.build(), c.t, (32, 32), backend='qrhi')\n"
    done = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    leftovers = [
        p
        for p in list(home.rglob("*")) + list(config.rglob("*"))
        if p.is_file() and "kut3d-render" in str(p).lower()
    ]
    assert (
        leftovers == []
    )  # aucune préférence « kut3d-render » (QSettings) n'a été écrite
