"""Moteur GPU de Kut-3D (phase 2b), PARTIE SANS GPU : registre, fabrique paresseuse, sonde, repli, shaders, plan de
dessin. Tourne partout, CI Linux sans GPU comprise ; les rendus réels sont dans `test_kut3d_gpu_render.py`."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import textwrap
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import kut3d
import kut3d.render.frame as frame_mod
from kut3d import (
    AmbientLight,
    DirectionalLight,
    Instance,
    PointLight,
    RenderBackendUnavailable,
    RenderError,
    Scene,
    available_backends,
    backend_available,
    render_frame,
)
from kut3d.gpu import availability, shader_files
from kut3d.gpu import backend as gpu_backend
from kut3d.gpu.build_shaders import qsb_executable
from kut3d.gpu.limits import MAX_GPU_JOINTS
from kut3d.gpu.prepare import (
    DRAW_BLOCK_BYTES,
    FRAME_BLOCK_BYTES,
    JOINT_BLOCK_BYTES,
    VERTEX_FLOATS,
    plan_frame,
)
from kut3d.mathutil import translation_matrix
from kut3d.model import Material, Mesh, Model, Node, Skeleton
from kut3d.render.assemble import assemble
from tests.kut3d_gpu_cases import CASES, diag, skinned_with_normals
from tests.kut3d_scenes import (
    WHITE_AMBIENT,
    checker_rgba,
    cube_model,
    flat,
    front_camera,
    quad_model,
    scene,
    skinned_model,
    sun,
    texture_ref,
)

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _fresh_probe_cache():
    availability.clear_probe_cache()
    yield
    availability.clear_probe_cache()


def tiny_scene() -> Scene:
    return scene(
        cube_model(flat((0.8, 0.3, 0.2, 1))), lights=[sun((-1, -1, -1)), WHITE_AMBIENT]
    )


# --- registre et fabrique paresseuse -------------------------------------------------------------------------


def test_qrhi_is_registered_with_a_lazy_factory_and_numpy_is_always_available() -> None:
    assert "qrhi" in available_backends() and "numpy" in available_backends()
    assert frame_mod.get_backend("qrhi") is gpu_backend.render_lazy
    assert backend_available("numpy") and not backend_available("nonexistent")
    assert frame_mod.backend_unavailable_reason("nonexistent") == "moteur inconnu"


def test_import_kut3d_loads_neither_pyside6_nor_the_qt_modules_of_the_gpu_engine() -> (
    None
):
    code = (
        "import sys, kut3d\n"
        "assert 'qrhi' in kut3d.available_backends()\n"
        "bad = [m for m in sys.modules if m.startswith('PySide6') or m in "
        "('kut3d.gpu.renderer', 'kut3d.gpu.qtapp', 'kut3d.gpu.probe_worker')]\n"
        "assert not bad, bad\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr


def test_without_pyside6_numpy_and_auto_work_and_qrhi_explains_why() -> None:
    code = textwrap.dedent(
        """
        import importlib.abc, sys
        class Block(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path, target=None):
                if name == "PySide6" or name.startswith("PySide6."):
                    raise ModuleNotFoundError("PySide6 rendu inimportable pour ce test", name=name)
        sys.meta_path.insert(0, Block())
        import numpy as np
        import kut3d
        from tests.kut3d_scenes import *
        sc = scene(cube_model(flat((1, 0, 0, 1))))
        ref = kut3d.render_frame(sc, size=(16, 16))
        auto = kut3d.render_frame(sc, size=(16, 16), backend="auto")
        assert auto.color.tobytes() == ref.color.tobytes()
        assert not kut3d.backend_available("qrhi")
        try:
            kut3d.render_frame(sc, size=(16, 16), backend="qrhi")
        except kut3d.RenderBackendUnavailable as error:
            assert "PySide6" in str(error), error
        else:
            raise SystemExit("aucune exception")
        assert not [m for m in sys.modules if m.startswith("PySide6")]
        """
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr


# --- sonde -------------------------------------------------------------------------------------------------


class _Run:
    """Remplace `subprocess.run` de la sonde et compte les appels."""

    def __init__(
        self,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        raises: Exception | None = None,
    ) -> None:
        self.calls = 0
        self.args = (returncode, stdout, stderr, raises)

    def __call__(self, *a: Any, **k: Any) -> subprocess.CompletedProcess[str]:
        self.calls += 1
        returncode, stdout, stderr, raises = self.args
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(a[0], returncode, stdout, stderr)


def _patch_run(monkeypatch: pytest.MonkeyPatch, run: _Run) -> _Run:
    monkeypatch.setattr(availability.subprocess, "run", run)
    monkeypatch.delenv(availability.ENV_SWITCH, raising=False)
    monkeypatch.setattr(availability, "_pyside_installed", lambda: True)
    return run


def test_probe_success_is_parsed_and_cached_per_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _patch_run(
        monkeypatch, _Run(stdout='bruit\n{"ok": true, "backend": "Metal"}\n')
    )
    first = availability.probe_qrhi(platform="cocoa")
    assert (
        first.ok
        and first.backend == "Metal"
        and first.reason == ""
        and first.platform == "cocoa"
    )
    assert (
        availability.probe_qrhi(platform="cocoa") is first and run.calls == 1
    )  # en cache
    assert (
        availability.probe_qrhi(platform="xcb").platform == "xcb" and run.calls == 2
    )  # autre plateforme : autre sonde
    availability.probe_qrhi(platform="cocoa", refresh=True)
    assert run.calls == 3


@pytest.mark.parametrize(
    "run, expected",
    [
        (
            _Run(returncode=2, stdout='{"ok": false, "reason": "pas de Metal ici"}'),
            "pas de Metal ici",
        ),
        (_Run(returncode=-11), "signal 11"),
        (_Run(returncode=1, stderr="Traceback\nValueError: boum"), "boum"),
        (_Run(returncode=0, stdout="pas du JSON"), "code 0"),
        (_Run(raises=subprocess.TimeoutExpired("x", 30)), "n'a pas répondu"),
        (_Run(raises=OSError("exec format error")), "exec format error"),
    ],
)
def test_probe_failures_carry_the_real_reason_and_never_raise(
    monkeypatch: pytest.MonkeyPatch, run: _Run, expected: str
) -> None:
    _patch_run(monkeypatch, run)
    result = availability.probe_qrhi(platform="p")
    assert not result.ok and expected in result.reason
    assert availability.qrhi_unavailable_reason() is not None


def test_a_hanging_worker_is_killed_at_the_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(availability.ENV_SWITCH, raising=False)
    monkeypatch.setattr(availability, "_pyside_installed", lambda: True)
    monkeypatch.setattr(
        availability,
        "_worker_command",
        lambda out_path=None: [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    result = availability.probe_qrhi(platform="p", timeout=0.5)
    assert not result.ok and "n'a pas répondu" in result.reason and result.seconds < 20


@pytest.mark.skipif(sys.platform == "win32", reason="signaux POSIX")
def test_a_crashing_worker_does_not_take_the_caller_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(availability.ENV_SWITCH, raising=False)
    monkeypatch.setattr(availability, "_pyside_installed", lambda: True)
    # SIGKILL et non SIGSEGV : un vrai plantage fait écrire un rapport par macOS et ouvre la fenêtre « Python s'est
    # arrêté de façon inattendue » à chaque lancement des tests ; la mort par signal est la même pour la sonde.
    crash = "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"
    monkeypatch.setattr(
        availability,
        "_worker_command",
        lambda out_path=None: [sys.executable, "-c", crash],
    )
    result = availability.probe_qrhi(platform="p")
    assert not result.ok and "signal" in result.reason


def test_environment_switch_disables_the_gpu_engine_and_frozen_application_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _patch_run(monkeypatch, _Run(stdout='{"ok": true, "backend": "Metal"}'))
    monkeypatch.setenv(availability.ENV_SWITCH, "off")
    off = availability.probe_qrhi(platform="p")
    assert not off.ok and availability.ENV_SWITCH in off.reason and run.calls == 0
    monkeypatch.delenv(availability.ENV_SWITCH)
    # Gelée, la sonde n'est plus refusée : elle part en sous-processus (commande testée dans test_kut3d_frozen.py).
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    frozen = availability.probe_qrhi(platform="q")
    assert frozen.ok and run.calls == 1


def test_platform_resolution_never_imports_qt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "PySide6.QtGui", raising=False)
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen:foo=1")
    assert availability.resolve_platform() == "offscreen"
    monkeypatch.delenv("QT_QPA_PLATFORM")
    assert availability.resolve_platform() == availability.NATIVE


def test_unavailable_backend_raises_with_the_reason_and_is_a_render_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_run(
        monkeypatch,
        _Run(returncode=2, stdout='{"ok": false, "reason": "aucun GPU Metal"}'),
    )
    assert not backend_available("qrhi")
    assert frame_mod.backend_unavailable_reason("qrhi") == "aucun GPU Metal"
    with pytest.raises(RenderBackendUnavailable, match="aucun GPU Metal") as err:
        render_frame(tiny_scene(), size=(8, 8), backend="qrhi")
    assert isinstance(err.value, RenderError) and isinstance(err.value, ValueError)


# --- auto ----------------------------------------------------------------------------------------------------


def test_auto_falls_back_to_numpy_when_the_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_run(monkeypatch, _Run(returncode=2, stdout='{"ok": false, "reason": "x"}'))
    sc = tiny_scene()
    auto = render_frame(sc, size=(24, 24), backend="auto")
    assert (
        auto.color.tobytes()
        == render_frame(sc, size=(24, 24), backend="numpy").color.tobytes()
    )


def test_auto_prefers_the_gpu_then_falls_back_when_it_declines_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake(scene_: Scene, t: float, size: tuple[int, int], samples: int) -> Any:
        calls.append("qrhi")
        raise RenderBackendUnavailable("image trop grande pour ce GPU")

    monkeypatch.setitem(frame_mod._BACKENDS, "qrhi", fake)
    monkeypatch.setattr(availability, "qrhi_unavailable_reason", lambda: None)
    sc = tiny_scene()
    auto = render_frame(sc, size=(24, 24), backend="auto")
    assert calls == ["qrhi"]  # le GPU a été essayé...
    assert (
        auto.color.tobytes() == render_frame(sc, size=(24, 24)).color.tobytes()
    )  # ...puis la référence numpy


def test_auto_uses_the_gpu_result_and_skips_it_outside_the_main_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = render_frame(tiny_scene(), size=(8, 8))
    calls: list[int] = []

    def fake(scene_: Scene, t: float, size: tuple[int, int], samples: int) -> Any:
        calls.append(1)
        return sentinel

    monkeypatch.setitem(frame_mod._BACKENDS, "qrhi", fake)
    monkeypatch.setattr(availability, "qrhi_unavailable_reason", lambda: None)
    assert render_frame(tiny_scene(), size=(8, 8), backend="auto") is sentinel
    out: dict[str, Any] = {}
    thread = threading.Thread(
        target=lambda: out.setdefault(
            "frame", render_frame(tiny_scene(), size=(8, 8), backend="auto")
        )
    )
    thread.start()
    thread.join(30)
    assert (
        calls == [1] and out["frame"] is not sentinel
    )  # fil secondaire : numpy, le GPU n'est pas appelé


def test_auto_still_validates_the_request() -> None:
    with pytest.raises(RenderError, match="samples"):
        render_frame(tiny_scene(), size=(8, 8), backend="auto", samples=3)


def test_qrhi_outside_the_main_thread_is_a_render_error_before_any_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _patch_run(monkeypatch, _Run(stdout='{"ok": true, "backend": "Metal"}'))
    out: dict[str, BaseException] = {}

    def work() -> None:
        try:
            render_frame(tiny_scene(), size=(8, 8), backend="qrhi")
        except BaseException as error:  # noqa: BLE001
            out["error"] = error

    thread = threading.Thread(target=work)
    thread.start()
    thread.join(30)
    error = out["error"]
    assert type(error) is RenderError and "fil principal" in str(error)
    assert run.calls == 0  # aucun sous-processus lancé pour rien


# --- shaders -------------------------------------------------------------------------------------------------


def test_committed_qsb_files_match_the_sources_and_the_manifest() -> None:
    assert shader_files.stale() == [], "régénérer : python -m kut3d.gpu.build_shaders"
    manifest = json.loads(shader_files.MANIFEST.read_text(encoding="utf-8"))
    assert sorted(manifest) == sorted(shader_files.SOURCES)
    for name in shader_files.SOURCES:
        assert len(shader_files.load_qsb(name)) > 500
    assert (
        subprocess.run(
            [sys.executable, "-m", "kut3d.gpu.build_shaders", "--check"],
            cwd=ROOT,
            capture_output=True,
            timeout=60,
        ).returncode
        == 0
    )


def test_changing_a_shader_source_without_regenerating_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for item in shader_files.SHADER_DIR.iterdir():
        (tmp_path / item.name).write_bytes(item.read_bytes())
    monkeypatch.setattr(shader_files, "SHADER_DIR", tmp_path)
    monkeypatch.setattr(shader_files, "MANIFEST", tmp_path / "manifest.json")
    assert shader_files.stale() == []
    frag = tmp_path / "kut3d.frag"
    frag.write_text(
        frag.read_text(encoding="utf-8") + "\n// modifié\n", encoding="utf-8"
    )
    assert shader_files.stale() == ["kut3d.frag"]
    (tmp_path / "kut3d.vert.qsb").unlink()
    assert shader_files.stale() == ["kut3d.vert", "kut3d.frag"]
    (tmp_path / "kut3d.vert.qsb").write_bytes(b"x")
    assert shader_files.stale() == ["kut3d.frag"]
    monkeypatch.setattr(
        shader_files, "TARGETS_TAG", "autres cibles"
    )  # la liste de cibles fait partie de l'empreinte
    assert shader_files.stale() == list(shader_files.SOURCES)


def test_a_missing_qsb_is_a_clear_error_not_a_runtime_compilation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shader_files, "SHADER_DIR", tmp_path)
    with pytest.raises(RenderBackendUnavailable, match="build_shaders"):
        shader_files.load_qsb("kut3d.vert")


def _block(source: str, name: str) -> str:
    start = source.index(f"uniform {name} {{")
    return source[start : source.index("};", start)]


def test_uniform_blocks_are_identical_in_both_stages_and_match_the_constants() -> None:
    vert = (shader_files.SHADER_DIR / "kut3d.vert").read_text(encoding="utf-8")
    frag = (shader_files.SHADER_DIR / "kut3d.frag").read_text(encoding="utf-8")
    for block in ("FrameBlock", "DrawBlock"):
        strip = lambda s: [ln.split("//")[0].strip() for ln in s.splitlines()]  # noqa: E731
        assert strip(_block(vert, block)) == strip(_block(frag, block))
    assert f"joints[{MAX_GPU_JOINTS}]" in vert
    assert (
        f"lightA[{kut3d.scene.MAX_LIGHTS}]" in vert
        and f"lightB[{kut3d.scene.MAX_LIGHTS}]" in vert
    )


def _reflection(name: str) -> dict[str, Any]:
    """Réflexion d'un `.qsb` committé, lue par `qsb -d` (outil de PySide6, sans GPU)."""
    try:
        tool = qsb_executable()
    except (
        SystemExit
    ) as error:  # PySide6 absent : ces deux tests de réflexion ne peuvent pas tourner
        pytest.skip(str(error))
    done = subprocess.run(
        [tool, "-d", str(shader_files.SHADER_DIR / f"{name}.qsb")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    text = done.stdout[
        done.stdout.index("Reflection info:") + len("Reflection info:") :
    ]
    data, _ = json.JSONDecoder().raw_decode(text.strip())
    return dict(data)


def test_baked_uniform_block_layout_matches_the_buffers_the_engine_uploads() -> None:
    """Relit les `.qsb` committés : tailles std140 des blocs, positions des membres et liaisons."""
    sizes: dict[str, set[int]] = {}
    for name in shader_files.SOURCES:
        for block in _reflection(name).get("uniformBlocks", []):
            sizes.setdefault(block["blockName"], set()).add(block["size"])
    assert sizes["FrameBlock"] == {FRAME_BLOCK_BYTES}
    assert sizes["DrawBlock"] == {DRAW_BLOCK_BYTES}
    assert sizes["JointBlock"] == {JOINT_BLOCK_BYTES}
    frag = _reflection("kut3d.frag")
    assert sorted(s["binding"] for s in frag["combinedImageSamplers"]) == [
        3,
        4,
        5,
        6,
        7,
    ]
    vert = {b["blockName"]: b for b in _reflection("kut3d.vert")["uniformBlocks"]}
    draw = {m["name"]: m["offset"] for m in vert["DrawBlock"]["members"]}
    assert draw == {
        "model": 0,
        "nmat": 64,
        "base": 128,
        "emissive": 144,
        "params": 160,
        "flags0": 176,
        "flags1": 192,
    }
    frame = {m["name"]: m["offset"] for m in vert["FrameBlock"]["members"]}
    assert frame == {
        "viewProj": 0,
        "eye": 64,
        "ambient": 80,
        "counts": 96,
        "lightA": 112,
        "lightB": 368,
    }


# --- architecture --------------------------------------------------------------------------------------------


NO_QT_GPU_MODULES = (
    "__init__", "availability", "backend", "build_shaders", "limits", "prepare", "probe_worker", "shader_files",
)  # fmt: skip


def test_gpu_modules_that_must_stay_qt_free_do_not_mention_pyside6() -> None:
    for name in NO_QT_GPU_MODULES:
        tree = ast.parse(
            (ROOT / "kut3d" / "gpu" / f"{name}.py").read_text(encoding="utf-8")
        )
        for node in ast.walk(tree):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            assert not any(
                n.split(".")[0] in ("PySide6", "PyQt5", "PyQt6") for n in names
            ), name


# --- plan de dessin (numpy pur) -----------------------------------------------------------------------------


def _floats(block: bytes) -> np.ndarray:
    return np.frombuffer(block, dtype=np.float32)


def test_a_textured_cube_is_one_run_with_its_textures_and_uniform_layout() -> None:
    tex = texture_ref(checker_rgba())
    sc = scene(
        cube_model(
            Material(
                base_color=(0.5, 0.25, 1, 0.75),
                base_color_texture=tex,
                metallic=0.3,
                roughness=0.6,
            )
        )
    )
    plan = plan_frame(sc, 0.0, (64, 48))
    assert plan.vertices.shape == (24, VERTEX_FLOATS) and plan.indices.shape == (36,)
    (run,) = plan.runs
    assert (run.first, run.count, run.blend, run.cull_back, run.joints) == (
        0,
        36,
        False,
        True,
        None,
    )
    assert run.textures[0] is not None and all(t is None for t in run.textures[1:])
    block = _floats(run.draw_block)
    assert block.size * 4 == DRAW_BLOCK_BYTES
    assert np.allclose(block[:16].reshape(4, 4), np.eye(4)) and np.allclose(
        block[16:32].reshape(4, 4), np.eye(4)
    )
    assert np.allclose(block[32:36], [0.5, 0.25, 1.0, 0.75])  # base
    assert np.allclose(
        block[40:44], [0.3, 0.6, 0.5, 0.0]
    )  # métal, rugosité, seuil, mode opaque
    assert np.allclose(block[44:48], [1, 0, 0, 0]) and np.allclose(
        block[48:52], [0, 0, 0, 0]
    )  # textures, peau


def test_runs_group_by_material_and_double_sided_materials_are_not_culled() -> None:
    a = Instance(quad_model(flat((1, 0, 0, 1))))
    b = Instance(quad_model(flat((0, 1, 0, 1), double_sided=True)))
    c = Instance(
        quad_model(flat((1, 0, 0, 1)), z=1.0)
    )  # même couleur que a, mais un autre objet Material
    plan = plan_frame(scene([a, b, c]), 0.0, (8, 8))
    assert sum(r.count for r in plan.runs) == 18 and len(plan.runs) == 3
    assert sorted(r.cull_back for r in plan.runs) == [False, True, True]


def test_blend_triangles_come_last_sorted_far_to_near_whatever_the_instance_order() -> (
    None
):
    cam = front_camera(z=1.0, fov_y_deg=90.0)
    red = Instance(
        quad_model(flat((1, 0, 0, 0.5), alpha_mode="blend"), half=2.0, z=0.5)
    )
    green = Instance(
        quad_model(flat((0, 1, 0, 0.5), alpha_mode="blend"), half=2.0, z=0.0)
    )
    wall = Instance(quad_model(flat((0, 0, 1, 1)), half=4.0, z=-1.0))
    runs_per_order = []
    for order in ([red, green, wall], [green, wall, red], [wall, red, green]):
        plan = plan_frame(scene(order, camera=cam), 0.0, (8, 8))
        assert [r.blend for r in plan.runs] == [False, True, True]  # l'opaque d'abord
        blend_colors = [
            tuple(_floats(r.draw_block)[32:35]) for r in plan.runs if r.blend
        ]
        runs_per_order.append(blend_colors)
    assert runs_per_order[0] == runs_per_order[1] == runs_per_order[2]
    assert runs_per_order[0] == [
        (0.0, 1.0, 0.0),
        (1.0, 0.0, 0.0),
    ]  # le vert (z=0, loin) avant le rouge (z=0,5)


def test_alpha_mode_is_carried_to_the_draw_block() -> None:
    mask = flat((1, 1, 1, 1), alpha_mode="mask", alpha_cutoff=0.3)
    (run,) = plan_frame(scene(quad_model(mask)), 0.0, (8, 8)).runs
    assert (
        np.allclose(_floats(run.draw_block)[40:44], [0.0, 1.0, 0.3, 1.0])
        and not run.blend
    )


def test_lights_are_packed_like_the_reference_shader_reads_them() -> None:
    lights = [
        AmbientLight((1.0, 0.5, 0.0), 0.5),
        PointLight((1, 2, 3), (1, 1, 0), 4.0, range=10.0),
        DirectionalLight((0, -1, 0), (0.5, 0.5, 1.0), 2.0),
        PointLight((0, 0, 5), intensity=1.0),
        AmbientLight((0.0, 0.0, 1.0), 1.0),
    ]
    plan = plan_frame(scene(cube_model(), lights=lights), 0.0, (8, 8))
    assert plan.light_count == 3 and np.allclose(plan.ambient, [0.5, 0.25, 1.0])
    assert np.allclose(plan.light_a[0], [0, 1, 0, 0]) and np.allclose(
        plan.light_b[0], [1, 1, 2, -1]
    )  # vers la lumière
    assert np.allclose(plan.light_a[1], [1, 2, 3, 1]) and np.allclose(
        plan.light_b[1], [4, 4, 0, 10]
    )
    assert (
        np.allclose(plan.light_a[2], [0, 0, 5, 1]) and plan.light_b[2, 3] == -1
    )  # portée illimitée
    corr = np.diag([1.0, -1.0, 0.5, 1.0])
    block = plan.frame_block(corr)
    assert len(block) == FRAME_BLOCK_BYTES
    head = _floats(block)
    assert np.allclose(head[:16].reshape(4, 4).T, corr @ plan.view_proj, atol=1e-6)
    assert np.allclose(head[16:19], plan.eye) and head[24] == 3.0


def test_default_assemble_is_unchanged_and_never_defers_the_skin() -> None:
    sc = CASES["skin_lit"].build()
    default = assemble(sc, 1.0)
    explicit = assemble(sc, 1.0, gpu_skin_limit=None)
    assert all(s is None for s in default.draw_skin) and np.array_equal(
        default.pos, explicit.pos
    )
    assert default.joints is not None and not default.joints.any()


def _world_from_deferred(sc: Scene, t: float) -> np.ndarray:
    """Applique côté CPU ce que fait le vertex shader, pour comparer au `assemble` de la référence."""
    geo = assemble(sc, t, gpu_skin_limit=MAX_GPU_JOINTS)
    assert (
        geo.draw_skin
        and geo.draw_skin[0] is not None
        and geo.joints is not None
        and geo.joint_weights is not None
    )
    skin = geo.draw_skin[0]
    m = np.einsum(
        "vk,vkij->vij", geo.joint_weights.astype(np.float64), skin.joints[geo.joints]
    )
    rest = np.concatenate([geo.pos, np.ones((geo.pos.shape[0], 1))], axis=1)
    return np.asarray(
        np.einsum("ij,vj->vi", skin.model, np.einsum("vij,vj->vi", m, rest))[:, :3]
    )


@pytest.mark.parametrize(
    "name", ["skin_ambient", "skin_lit", "skin_mirrored", "morph_then_skin"]
)
def test_deferred_skin_reproduces_the_cpu_deformation_including_morph_first(
    name: str,
) -> None:
    case = CASES[name]
    sc = case.build()
    expected = assemble(sc, case.t).pos
    assert np.allclose(_world_from_deferred(sc, case.t), expected, atol=1e-5)


def test_a_deferred_draw_is_planned_with_its_palette_and_skin_flag() -> None:
    sc = CASES["skin_lit"].build()
    plan = plan_frame(sc, 1.0, (64, 64))
    (run,) = plan.runs
    assert (
        plan.skinned_on_gpu == 1
        and run.joints is not None
        and len(run.joints) == JOINT_BLOCK_BYTES
    )
    assert _floats(run.draw_block)[48 + 1] == 1.0  # flags1.y : dessin à peau
    palette = _floats(run.joints).reshape(MAX_GPU_JOINTS, 4, 4).transpose(0, 2, 1)
    assert np.allclose(palette[2:], np.eye(4)) and np.allclose(
        palette[2], np.eye(4)
    )  # 2 os + identité, puis remplissage


def test_skin_falls_back_to_the_cpu_for_blend_no_normals_and_too_many_joints() -> None:
    base = skinned_with_normals(90.0)

    def deferred(model: Model) -> int:
        return plan_frame(
            Scene([Instance(model, clip="bend")], [WHITE_AMBIENT], front_camera()),
            1.0,
            (8, 8),
        ).skinned_on_gpu

    assert deferred(base) == 1
    assert deferred(skinned_model(90.0)) == 0  # sans normales
    glass = Material(base_color=(1, 1, 1, 0.5), alpha_mode="blend", double_sided=True)
    blended = replace(
        base, meshes=(replace(base.meshes[0], material_index=0),), materials=(glass,)
    )
    assert (
        deferred(blended) == 0
    )  # alpha-blending : sommets déformés côté CPU pour le tri
    n = MAX_GPU_JOINTS  # n os + l'identité dépasse la limite
    skel = base.skeletons[0]
    big = Skeleton(
        tuple(f"j{i}" for i in range(n)), [-1] * n, [np.eye(4)] * n, [[0, 0, 0]] * n, [[0, 0, 0, 1]] * n, [[1, 1, 1]] * n,
        list(skel.node_indices) + [1] * (n - 2),
    )  # fmt: skip
    assert deferred(replace(base, skeletons=(big,))) == 0
    ok = Skeleton(
        tuple(f"j{i}" for i in range(n - 1)), [-1] * (n - 1), [np.eye(4)] * (n - 1), [[0, 0, 0]] * (n - 1),
        [[0, 0, 0, 1]] * (n - 1), [[1, 1, 1]] * (n - 1), list(skel.node_indices) + [1] * (n - 3),
    )  # fmt: skip
    assert (
        deferred(replace(base, skeletons=(ok,))) == 1
    )  # 127 os + identité = 128 : la limite exacte


def test_fallback_geometry_equals_the_default_assemble_exactly() -> None:
    sc = CASES["skin_without_normals"].build()
    geo = assemble(sc, 1.0, gpu_skin_limit=MAX_GPU_JOINTS)
    assert all(s is None for s in geo.draw_skin)
    assert np.array_equal(geo.pos, assemble(sc, 1.0).pos)


def test_blend_over_the_triangle_limit_is_refused_like_the_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kut3d.gpu import prepare

    monkeypatch.setattr(prepare, "MAX_BLEND_TRIANGLES", 1)
    sc = scene(quad_model(flat((1, 0, 0, 0.5), alpha_mode="blend")))
    with pytest.raises(RenderError, match="alpha-blending"):
        plan_frame(sc, 0.0, (8, 8))


def test_empty_scene_has_an_empty_plan() -> None:
    plan = plan_frame(Scene([], [WHITE_AMBIENT], front_camera()), 0.0, (8, 8))
    assert (
        plan.runs == ()
        and plan.indices.size == 0
        and plan.vertices.shape == (0, VERTEX_FLOATS)
    )


def test_instances_of_one_model_share_a_run_and_mirrored_models_flip_winding() -> None:
    model = cube_model(flat((1, 1, 1, 1)))
    many = [Instance(model, translation_matrix([i, 0, 0])) for i in range(50)]
    plan = plan_frame(scene(many), 0.0, (8, 8))
    assert len(plan.runs) == 1 and plan.runs[0].count == 50 * 36
    mirror = plan_frame(scene([Instance(model, diag(-1, 1, 1))]), 0.0, (8, 8))
    straight = plan_frame(scene([Instance(model)]), 0.0, (8, 8))
    assert not np.array_equal(
        mirror.indices, straight.indices
    )  # le miroir inverse le sens des triangles


def test_uv_less_mesh_ignores_textures_in_the_plan() -> None:
    tex = texture_ref(checker_rgba())
    mesh_model = quad_model(flat((1, 1, 1, 1), base_color_texture=tex), uvs=False)
    (run,) = plan_frame(scene(mesh_model), 0.0, (8, 8)).runs
    assert run.textures[0] is None and _floats(run.draw_block)[44] == 0.0


def test_mesh_type_is_available_for_hand_built_models() -> None:
    mesh = Mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]])
    model = Model(nodes=(Node(mesh_index=0),), root_nodes=(0,), meshes=(mesh,))
    plan = plan_frame(scene(model), 0.0, (8, 8))
    assert plan.indices.tolist() == [0, 1, 2] and plan.runs[0].textures == (None,) * 5
