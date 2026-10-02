"""Tests sur le **vrai** matériel (GPU, VideoToolbox…) — optionnels.

Activés automatiquement sur un Mac de développement (hors CI), ou partout avec
``KUT_STUDIO_GPU_TESTS=1``. La CI n'en a jamais besoin : tout ce qui est vérifié
ici l'est aussi, sans GPU, par la référence numpy et des backends simulés.

Chaque rendu tourne dans un sous-processus avec la plateforme Qt native (les
autres tests utilisent ``offscreen``, sans GPU).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

ENABLED = os.environ.get("KUT_STUDIO_GPU_TESTS") == "1" or (
    sys.platform == "darwin" and not os.environ.get("CI") and os.environ.get("KUT_STUDIO_GPU_TESTS") != "0"
)
pytestmark = pytest.mark.skipif(not ENABLED, reason="tests matériels désactivés (KUT_STUDIO_GPU_TESTS=1)")

APIS = {"darwin": ("metal", "opengl"), "win32": ("d3d11", "opengl")}.get(sys.platform, ("opengl",))


def _native_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
    env["PYTHONPATH"] = str(ROOT)
    return env


@pytest.mark.parametrize("api", APIS)
def test_real_gpu_matches_the_reference(api):
    pytest.importorskip("numpy")
    completed = subprocess.run([sys.executable, "-m", "tools.gpu.selfcheck", "--api", api], cwd=str(ROOT),
                               env=_native_env(), capture_output=True, text=True, timeout=180)
    if completed.returncode != 0 or not completed.stdout.strip():
        pytest.skip(f"GPU {api} indisponible : {completed.stderr[-300:]}")
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    errors = {k: v for k, v in result["cases"].items() if "error" in v}
    if errors and len(errors) == len(result["cases"]):
        pytest.skip(f"GPU {api} indisponible : {next(iter(errors.values()))}")
    assert not errors, errors
    for key, value in result["cases"].items():
        # 10 bits stockés en 16 bits : quelques niveaux d'écart de quantification au plus.
        assert value["mean"] < 1.0 and value["max"] < 6.0, (key, value)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")
def test_hardware_decoding_is_bit_exact_with_software(tmp_path):
    """H.264 / HEVC : le décodage matériel donne les mêmes pixels que le logiciel (aperçu = export)."""
    from core.hardware_cache import CapabilityService

    capabilities = CapabilityService(cache_path=tmp_path / "caps.json", environment={}).capabilities(refresh=True)
    backends = capabilities.decode_backends()
    if not backends:
        pytest.skip("aucun décodeur matériel validé sur cette machine")
    from core.hardware_decoding import decode_input_args

    backend = backends[0]
    for codec, encoder, pix in (("h264", "libx264", "yuv420p"), ("hevc", "libx265", "yuv420p")):
        if not capabilities.is_decode_usable(codec, backend):
            continue
        media = tmp_path / f"{codec}.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25",
                        "-t", "1", "-c:v", encoder, "-pix_fmt", pix, str(media)], check=True)

        def decode(args):
            return subprocess.run(["ffmpeg", "-v", "error", *args, "-i", str(media), "-pix_fmt", "yuv420p",
                                   "-f", "rawvideo", "-"], capture_output=True, check=True).stdout

        assert decode(decode_input_args(backend)) == decode(()), f"{backend.value}/{codec}"


def test_detection_finds_the_platform_decoder(tmp_path):
    from core.hardware_cache import CapabilityService
    from core.hardware_decoding import DecodeMode

    if shutil.which("ffmpeg") is None:
        pytest.skip("FFmpeg absent")
    capabilities = CapabilityService(cache_path=tmp_path / "caps.json", environment={}).capabilities(refresh=True)
    if sys.platform == "darwin" and "videotoolbox" in capabilities.hwaccels:
        assert capabilities.is_decode_usable("h264", DecodeMode.VIDEOTOOLBOX)
