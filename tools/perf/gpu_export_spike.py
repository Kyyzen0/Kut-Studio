"""Essai d'ADR-0001 : exporter un vrai montage par le compositeur GPU, et le comparer à l'export actuel.

Usage ::

    KUT_STUDIO_CONFIG_DIR=<dossier jetable> python -m tools.perf.gpu_export_spike \\
        --project ~/Documents/onlyfab_tiktok_fr.kut --workdir <dossier> [--seconds 6] [--no-reference]

Le projet n'est que lu. Chaque image ``t = n / fps`` est composée ainsi :

- **vidéo** : FFmpeg décode chaque clip actif en ``yuv420p`` (un processus par clip, lecture en avance dans un
  fil) ; les plans arrivent dans une ``QVideoFrame`` que le moniteur envoie au GPU tels quels ;
- **calques graphiques** : le **même** découpage que l'export (:func:`core.mograph_program.graphics_program`) et le
  **même** rastériseur Qt (:class:`core.mograph_raster.MographRenderer`, qualité « export », flou de mouvement de la
  séquence) ; chaque élément devient une texture RGBA, renvoyée au GPU seulement quand son contenu change ;
- **composition** : :func:`core.gpu_composite.plan_frame` exécuté par le vrai ``GpuPreviewWidget`` (Metal…), relu
  par ``grabFramebuffer`` (le binding PySide6 6.11 de ``QRhi.beginOffscreenFrame`` est inutilisable : il attend un
  ``QRhiCommandBuffer`` au lieu du pointeur de sortie) ;
- **encodage** : les images RVBA partent dans un FFmpeg qui fait la conversion BT.709 de l'export
  (:data:`core.export_engine.OUTPUT_COLOR_STAGE`) et encode avec l'encodeur que choisirait l'export
  (:func:`core.video_encoders.resolve_video_encoder`, VideoToolbox par défaut).

La référence est l'export actuel du même projet (``ExportEngine``, même encodeur, cache des calques vide), mesuré
comme dans :mod:`tools.perf.engine_bench`. Le rapport JSON donne les durées par étape, la vitesse (images par
seconde), PSNR / SSIM contre la référence, les images les plus éloignées et une planche-contact
« référence | GPU ». Le fichier final reprend la piste audio de la référence.

Couverture : ce dont les montages sociaux mesurés ont besoin (clips vidéo à transform / opacité / mode de fusion /
effets GPU, calques graphiques en bandes et en calques à mode de fusion). Le reste (séquences imbriquées,
transitions, remappage temporel, masques et étalonnage des clips, calques d'effets, effets des calques graphiques,
sous-titres) est refusé d'emblée, avec la liste de ce qui manque.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# --- Couverture ---------------------------------------------------------------------------------------------------


def unsupported(plan, scene) -> list[str]:
    """Ce que l'essai ne sait pas encore composer dans ``plan`` (vide : tout est couvert)."""
    from core.mograph_program import graphics_program

    missing = []
    if plan.nested_sequences:
        missing.append(f"{len(plan.nested_sequences)} séquence(s) imbriquée(s)")
    if plan.transitions:
        missing.append(f"{len(plan.transitions)} transition(s)")
    if plan.subtitle_cues:
        missing.append(f"{len(plan.subtitle_cues)} sous-titre(s)")
    for layer in plan.video_layers:
        name = Path(layer.source_path).name or layer.clip_id
        if layer.time_map is not None:
            missing.append(f"remappage temporel ({name})")
        if layer.color_grade is not None:
            missing.append(f"étalonnage ({name})")
        if getattr(layer.compositing, "masks", ()):
            missing.append(f"masques ({name})")
        if getattr(getattr(layer.compositing, "chroma_key", None), "enabled", False):
            missing.append(f"incrustation chroma-key ({name})")
        if layer.still or layer.is_nested:
            missing.append(f"image fixe ou imbrication ({name})")
        if layer.animation:
            missing.append(f"animation générique ({name})")
    for element in graphics_program(scene):
        if element.kind == "adjustment":
            missing.append(f"calque d'effets ({element.clip_id})")
        elif element.effects or element.color_grade is not None:
            missing.append(f"effets d'un calque graphique ({element.clip_id})")
    return missing


# --- Décodage -----------------------------------------------------------------------------------------------------


def probe_color(ffprobe: str, path: str) -> tuple[str, str]:
    """``(espace, plage)`` du flux vidéo, dans les noms de :mod:`core.gpu_frames` (``""`` : inconnu)."""
    completed = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=color_space,color_range",
         "-of", "json", path], capture_output=True, text=True, check=False)
    try:
        stream = json.loads(completed.stdout)["streams"][0]
    except (ValueError, KeyError, IndexError):
        return "", ""
    space = {"bt709": "bt709", "smpte170m": "bt601", "bt470bg": "bt601", "bt2020nc": "bt2020"}.get(
        stream.get("color_space", ""), "")
    rng = {"tv": "video", "pc": "full"}.get(stream.get("color_range", ""), "")
    return space, rng


class ClipDecoder:
    """Images ``yuv420p`` d'un clip, à la cadence de la séquence, lues en avance par un fil."""

    def __init__(self, ffmpeg: str, layer, fps: float, color: tuple[str, str]) -> None:
        self.width, self.height = int(layer.source_width), int(layer.source_height)
        self.color = color
        self.frame_bytes = self.width * self.height * 3 // 2
        duration = max(0.0, layer.timeline_end - layer.timeline_start)
        self.process = subprocess.Popen(
            [ffmpeg, "-v", "error", "-nostdin", "-ss", f"{layer.source_in:.6f}", "-i", layer.source_path,
             "-t", f"{duration:.6f}", "-map", "0:v:0", "-vf", f"fps={fps}", "-f", "rawvideo", "-pix_fmt", "yuv420p",
             "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.frames: queue.Queue = queue.Queue(maxsize=8)
        self.index = -1
        self.current: bytes | None = None
        self.wait_s = 0.0
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        stream = self.process.stdout
        while True:
            data = stream.read(self.frame_bytes)
            if len(data) < self.frame_bytes:
                self.frames.put(None)
                return
            self.frames.put(data)

    def frame(self, index: int) -> bytes | None:
        """Image ``index`` du clip (la dernière lue si le fichier finit plus tôt)."""
        started = time.perf_counter()
        while self.index < index:
            data = self.frames.get()
            if data is None:
                self.frames.put(None)
                break
            self.current, self.index = data, self.index + 1
        self.wait_s += time.perf_counter() - started
        return self.current

    def close(self) -> None:
        self.process.kill()
        self.process.wait()


def yuv_frame(data: bytes, width: int, height: int, color: tuple[str, str] = ("", "")):
    """``QVideoFrame`` YUV420P remplie avec ``data`` (plans Y, U, V serrés) : une copie, sans conversion.

    ``color`` : ``(espace, plage)`` sondés (:func:`probe_color`), posés sur le format comme le ferait le lecteur de
    Qt ; sans eux, :func:`ui.gpu_preview.describe_qt_frame` les lirait inconnus et le GPU convertirait en BT.601."""
    import numpy as np
    from PySide6.QtCore import QSize
    from PySide6.QtMultimedia import QVideoFrame, QVideoFrameFormat

    from core.gpu_frames import QT_COLOR_RANGES, QT_COLOR_SPACES

    fmt = QVideoFrameFormat(QSize(width, height), QVideoFrameFormat.PixelFormat.Format_YUV420P)
    space, rng = color
    spaces = {value: key for key, value in QT_COLOR_SPACES.items() if key != "ColorSpace_AdobeRgb"}
    ranges = {value: key for key, value in QT_COLOR_RANGES.items()}
    if space in spaces:
        fmt.setColorSpace(getattr(QVideoFrameFormat.ColorSpace, spaces[space]))
    if rng in ranges:
        fmt.setColorRange(getattr(QVideoFrameFormat.ColorRange, ranges[rng]))
    frame = QVideoFrame(fmt)
    if not frame.map(QVideoFrame.MapMode.WriteOnly):
        raise RuntimeError("QVideoFrame YUV420P non mappable")
    try:
        offset = 0
        for plane, (pw, ph) in enumerate(((width, height), (width // 2, height // 2), (width // 2, height // 2))):
            source = np.frombuffer(data, dtype=np.uint8, count=pw * ph, offset=offset).reshape(ph, pw)
            offset += pw * ph
            stride = frame.bytesPerLine(plane)
            target = np.frombuffer(frame.bits(plane), dtype=np.uint8)[: stride * ph].reshape(ph, stride)
            target[:, :pw] = source
    finally:
        frame.unmap()
    return frame


# --- Encodage -----------------------------------------------------------------------------------------------------


class Encoder:
    """FFmpeg qui reçoit des images RVBA sur son entrée standard (écrites par un fil)."""

    def __init__(self, ffmpeg: str, path: Path, width: int, height: int, fps: float, hardware: str) -> None:
        from core.export_engine import OUTPUT_COLOR_STAGE, OUTPUT_COLOR_TAGS
        from core.video_encoders import resolve_video_encoder

        choice = resolve_video_encoder("h264", quality=18, hardware=hardware, width=width, height=height, fps=fps)
        self.label = choice.label
        stage = OUTPUT_COLOR_STAGE + (f",{choice.video_filter}" if choice.video_filter else "")
        self.process = subprocess.Popen(
            [ffmpeg, "-v", "error", "-y", *choice.pre_input_args, "-f", "rawvideo", "-pix_fmt", "rgba",
             "-s", f"{width}x{height}", "-framerate", f"{fps}", "-i", "-", "-vf", stage, *choice.args,
             *OUTPUT_COLOR_TAGS, "-an", str(path)], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        self.frames: queue.Queue = queue.Queue(maxsize=8)
        self.wait_s = 0.0
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        stdin = self.process.stdin
        while True:
            data = self.frames.get()
            if data is None:
                stdin.close()
                return
            stdin.write(data)

    def push(self, data: bytes) -> None:
        started = time.perf_counter()
        self.frames.put(data)
        self.wait_s += time.perf_counter() - started

    def finish(self) -> None:
        self.frames.put(None)
        self._thread.join()
        error = self.process.stderr.read().decode("utf-8", "replace")
        if self.process.wait() != 0:
            raise RuntimeError(f"encodage en échec : {error.strip()[-400:]}")


# --- Composition GPU ----------------------------------------------------------------------------------------------


@dataclass
class Timings:
    raster_s: float = 0.0
    upload_s: float = 0.0
    gpu_s: float = 0.0
    readback_s: float = 0.0
    raster_frames: int = 0
    keys_s: float = 0.0
    raster_wait_s: float = 0.0
    gpu_frames: int = 0
    extra: dict = field(default_factory=dict)


class GpuExporter:
    """Compose les images d'un plan de rendu sur le GPU, une à une, comme l'export les compose avec FFmpeg."""

    LOOKAHEAD = 12
    """Images d'avance de la rastérisation en parallèle (assez pour couvrir une bande qui change à chaque image)."""

    def __init__(self, plan, ffmpeg: str, ffprobe: str, api: str, raster_threads: int = 0) -> None:
        from concurrent.futures import ThreadPoolExecutor

        from PySide6.QtCore import QSize, Qt

        from core.mograph_program import graphics_program
        from core.mograph_raster import MographRenderer, scene_for_plan
        from ui.gpu_preview import GpuPreviewWidget

        self.plan = plan
        self.ffmpeg, self.ffprobe = ffmpeg, ffprobe
        self.width, self.height, self.fps = int(plan.width), int(plan.height), float(plan.fps)
        self.scene = scene_for_plan(plan)
        self.program = graphics_program(self.scene)
        self.renderer = MographRenderer(self.scene, self.width, self.height, fps=self.fps, quality="export",
                                        motion_blur=plan.motion_blur)
        self.widget = GpuPreviewWidget(api=api, cache_budget=1024 * 1024 * 1024)
        self.failures: list[tuple[str, str]] = []
        self.widget.failed.connect(lambda kind, detail: self.failures.append((kind, detail)))
        self.widget.setFixedColorBufferSize(QSize(self.width, self.height))
        # Jamais montré, un QRhiWidget recrée un QRhi à CHAQUE grabFramebuffer, alors que l'exécuteur garde les
        # textures et pipelines du premier (« belongs to QRhi … used with QRhi … »). « Montré » hors écran, il en
        # garde un seul.
        self.widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.widget.resize(64, 64)
        self.widget.show()
        dpr = self.widget.devicePixelRatioF() or 1.0
        self.widget.set_canvas_rect((0.0, 0.0, self.width / dpr, self.height / dpr), (0.0, 0.0, 0.0))
        self.decoders: dict[str, ClipDecoder] = {}
        self.colors: dict[str, tuple[str, str]] = {}
        self.pushed: dict[str, object] = {}
        self._images: dict = {}
        self.timings = Timings()
        # Rastérisation en avance : QPainter sur des QImage distinctes est sûr d'un fil à l'autre ; les clés (ce qui
        # change l'apparence) restent calculées ici, dans l'ordre des images.
        self.pool = ThreadPoolExecutor(raster_threads) if raster_threads > 0 else None
        self.ahead: dict[tuple[int, str], tuple[str, object]] = {}
        self.scheduled = -1
        self.last_key: dict[str, str] = {}

    # -- une image ---------------------------------------------------------------------------------------------

    def _video_layers(self, t: float, index: int) -> list:
        from core.blend_modes import coerce_blend_mode
        from core.gpu_composite import CompositeLayer
        from core.gpu_effects import VIGNETTE_EXPORT, program_for
        from core.tracking_motion import fit_box, video_layer_matrix
        from core.visual_effects import evaluate_transform

        layers = []
        for layer in self.plan.video_layers:
            if not layer.timeline_start <= t < layer.timeline_end:
                decoder = self.decoders.pop(layer.clip_id, None)
                if decoder is not None and t >= layer.timeline_end:
                    self.timings.extra["decode_wait_s"] = self.timings.extra.get("decode_wait_s", 0.0) + decoder.wait_s
                    decoder.close()
                continue
            decoder = self.decoders.get(layer.clip_id)
            if decoder is None:
                color = self.colors.setdefault(layer.source_path, probe_color(self.ffprobe, layer.source_path))
                decoder = self.decoders[layer.clip_id] = ClipDecoder(self.ffmpeg, layer, self.fps, color)
            local = t - layer.timeline_start
            data = decoder.frame(int(round(local * self.fps)))
            if data is None:
                continue
            source = f"clip:{layer.clip_id}"
            started = time.perf_counter()
            self.widget.set_video_frame(source, yuv_frame(data, decoder.width, decoder.height, decoder.color))
            self.timings.upload_s += time.perf_counter() - started
            values = evaluate_transform(layer.transform, layer.transform_keyframes, local,
                                        layer.timeline_end - layer.timeline_start)
            box = fit_box(decoder.width, decoder.height, self.width, self.height, fill=bool(values.fill),
                          pan_x=values.pan_x, pan_y=values.pan_y)
            effects = [effect for effect in layer.effects if getattr(effect, "enabled", True)]
            layers.append(CompositeLayer(
                source=source,
                matrix=tuple(video_layer_matrix(values, self.width, self.height)),
                fit=box.rect,
                opacity=float(values.opacity),
                blend=coerce_blend_mode(getattr(layer.compositing, "blend_mode", "normal")),
                program=program_for(effects, vignette_extent=VIGNETTE_EXPORT, time=local),
                effect_scale=(values.scale * values.scale_x, values.scale * values.scale_y),
            ))
        return layers

    def _paint(self, element, t: float):
        from PySide6.QtGui import QImage

        # Une bande aplatit ses calques avec leurs modes ; un calque seul est fusionné par le shader.
        image = self.renderer.render(element.layer_ids, t, blend_modes=element.kind == "band")
        return image.convertToFormat(QImage.Format.Format_RGBA8888)

    def _schedule(self, last: int) -> None:
        """Clés des éléments jusqu'à l'image ``last`` ; une rastérisation lancée pour chaque contenu nouveau."""
        started = time.perf_counter()
        for index in range(self.scheduled + 1, last + 1):
            t = index / self.fps
            for number, element in enumerate(self.program):
                if not element.start <= t < element.end or not self.renderer.any_active(element.layer_ids, t):
                    continue
                source = f"graphics{number}"
                key = repr(self.renderer.frame_key(element.layer_ids, t))
                future = None
                if self.last_key.get(source) != key:
                    future = self.pool.submit(self._paint, element, t)
                    self.last_key[source] = key
                    self.timings.raster_frames += 1
                self.ahead[(index, source)] = (key, future)
        self.scheduled = max(self.scheduled, last)
        self.timings.keys_s += time.perf_counter() - started

    def _graphics_layers_parallel(self, index: int, mattes: dict) -> list:
        from PySide6.QtMultimedia import QVideoFrame

        from core.blend_modes import BlendMode
        from core.gpu_composite import CompositeLayer
        from core.gpu_effects import program_for

        self._schedule(index + self.LOOKAHEAD)
        layers = []
        for number, element in enumerate(self.program):
            source = f"graphics{number}"
            entry = self.ahead.pop((index, source), None)
            if entry is None:
                continue
            key, future = entry
            if future is not None:
                started = time.perf_counter()
                image = future.result()
                self.timings.raster_wait_s += time.perf_counter() - started
                started = time.perf_counter()
                self.widget.set_video_frame(source, QVideoFrame(image))
                self.timings.upload_s += time.perf_counter() - started
                self._images[source] = image
            matte = f"{source}:{hash(key)}"
            mattes[matte] = self._images[source]
            layers.append(CompositeLayer(
                source=source, matrix=(1.0, 0.0, 0.0, 1.0, 0.0, 0.0), fit=(0.0, 0.0, float(self.width),
                                                                            float(self.height)),
                blend=element.blend if element.kind == "layer" else BlendMode.NORMAL,
                program=program_for(()), matte=matte,
            ))
        return layers

    def _graphics_layers(self, t: float, mattes: dict) -> list:
        from PySide6.QtMultimedia import QVideoFrame

        from core.blend_modes import BlendMode
        from core.gpu_composite import CompositeLayer
        from core.gpu_effects import program_for

        layers = []
        for number, element in enumerate(self.program):
            if not element.start <= t < element.end or not self.renderer.any_active(element.layer_ids, t):
                continue
            source = f"graphics{number}"
            started = time.perf_counter()
            key = repr(self.renderer.frame_key(element.layer_ids, t))
            if self.pushed.get(source) != key:
                image = self._paint(element, t)
                self.timings.raster_s += time.perf_counter() - started
                self.timings.raster_frames += 1
                started = time.perf_counter()
                self.widget.set_video_frame(source, QVideoFrame(image))
                self.timings.upload_s += time.perf_counter() - started
                self.pushed[source] = key
                self._images[source] = image
            else:
                self.timings.raster_s += time.perf_counter() - started
            matte = f"{source}:{hash(key)}"
            mattes[matte] = self._images[source]
            layers.append(CompositeLayer(
                source=source, matrix=(1.0, 0.0, 0.0, 1.0, 0.0, 0.0), fit=(0.0, 0.0, float(self.width),
                                                                            float(self.height)),
                blend=element.blend if element.kind == "layer" else BlendMode.NORMAL,
                program=program_for(()), matte=matte,
            ))
        return layers

    def frame(self, index: int) -> bytes:
        """Image ``index`` de la séquence, en RVBA serré."""
        from PySide6.QtGui import QImage

        from core.gpu_composite import CompositeFrame

        t = index / self.fps
        mattes: dict = {}
        graphics = self._graphics_layers_parallel(index, mattes) if self.pool is not None \
            else self._graphics_layers(t, mattes)
        layers = self._video_layers(t, index) + graphics
        self.widget.set_composite(CompositeFrame(self.width, self.height, 1.0, tuple(layers)), mattes)
        started = time.perf_counter()
        image = self.widget.grabFramebuffer()
        self.timings.gpu_s += time.perf_counter() - started
        if image.isNull() or self.failures:
            raise RuntimeError(f"rendu GPU en échec : {self.failures or 'image vide'}")
        started = time.perf_counter()
        image = image.convertToFormat(QImage.Format.Format_RGBA8888)
        data = bytes(image.constBits())[: self.width * self.height * 4] if image.bytesPerLine() == self.width * 4 \
            else _tight(image, self.width, self.height)
        self.timings.readback_s += time.perf_counter() - started
        self.timings.gpu_frames += 1
        return data

    def close(self) -> None:
        if self.pool is not None:
            self.pool.shutdown(wait=True, cancel_futures=True)
        for decoder in self.decoders.values():
            decoder.close()
        self.decoders.clear()
        self.widget.release_gpu()
        self.widget.deleteLater()


def _tight(image, width: int, height: int) -> bytes:
    stride = image.bytesPerLine()
    raw = bytes(image.constBits())
    return b"".join(raw[row * stride:row * stride + width * 4] for row in range(height))


# --- Mesures ------------------------------------------------------------------------------------------------------


def run_gpu(project, workdir: Path, ffmpeg: str, ffprobe: str, *, seconds: float | None, hardware: str,
            api: str, raster_threads: int = 0) -> dict:
    from PySide6.QtWidgets import QApplication

    from core.render_plan import build_render_plan

    app = QApplication.instance() or QApplication([])
    plan = build_render_plan(project)
    exporter = GpuExporter(plan, ffmpeg, ffprobe, api, raster_threads)
    missing = unsupported(plan, exporter.scene)
    if missing:
        exporter.close()
        raise SystemExit("Non couvert par l'essai : " + " ; ".join(missing))
    duration = plan.duration if seconds is None else min(plan.duration, seconds)
    count = int(round(duration * plan.fps))
    output = workdir / f"gpu_video_t{raster_threads}.mp4"
    encoder = Encoder(ffmpeg, output, exporter.width, exporter.height, exporter.fps, hardware)
    started = time.perf_counter()
    device = ""
    try:
        for index in range(count):
            encoder.push(exporter.frame(index))
            if not device:
                device = exporter.widget.device_label()  # avant ``close`` : ``release_gpu`` oublie l'exécuteur
            if index % 30 == 0:
                app.processEvents()
        compose_s = time.perf_counter() - started
        encoder.finish()
    finally:
        exporter.close()
    wall = time.perf_counter() - started
    timings = exporter.timings
    return {
        "output": str(output),
        "frames": count,
        "encoder": encoder.label,
        "device": device or api,
        "wall_s": round(wall, 2),
        "fps": round(count / wall, 1),
        "compose_s": round(compose_s, 2),
        "stages_s": {
            "raster_qt": round(timings.raster_s, 2),
            "raster_keys": round(timings.keys_s, 2),
            "raster_wait": round(timings.raster_wait_s, 2),
            "upload_prep": round(timings.upload_s, 2),
            "gpu_render_and_grab": round(timings.gpu_s, 2),
            "readback_convert": round(timings.readback_s, 2),
            "decode_wait": round(timings.extra.get("decode_wait_s", 0.0), 2),
            "encode_wait": round(encoder.wait_s, 2),
        },
        "raster_frames": timings.raster_frames,
        "graphics_elements": len(exporter.program),
        "raster_threads": raster_threads,
    }


def run_reference(project, workdir: Path, *, seconds: float | None, hardware: str) -> dict:
    """L'export actuel (FFmpeg) du même projet, cache des calques vide, comme :mod:`tools.perf.engine_bench`."""
    from dataclasses import replace

    from core.export_engine import ExportEngine, ExportFormat, ExportPreset, ExportRequest
    from core.render_plan import build_render_plan

    os.environ["KUT_STUDIO_CACHE_DIR"] = tempfile.mkdtemp(prefix="cache-", dir=workdir)
    plan = build_render_plan(project)
    if seconds is not None and seconds < plan.duration:
        plan = replace(plan, duration=float(seconds))
    width, height, fps = plan.width, plan.height, plan.fps
    started = time.perf_counter()
    ExportEngine._build_filter_complex(plan, width, height, fps, None)
    layers_s = time.perf_counter() - started
    engine = ExportEngine()
    engine._prepare_temporary_files(plan)
    output = workdir / "reference.mp4"
    request = ExportRequest(plan, str(output), ExportFormat.MP4_H264, ExportPreset("essai", (width, height), 18, "192k"),
                            fps=fps, hardware=hardware)
    try:
        command = engine._build_command(request)
        started = time.perf_counter()
        completed = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=False)
        export_s = time.perf_counter() - started
    finally:
        engine._cleanup_temporary_files()
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.decode("utf-8", "replace")[-600:])
    frames = int(round(plan.duration * fps))
    return {
        "output": str(output),
        "frames": frames,
        "layers_cold_s": round(layers_s, 2),
        "ffmpeg_s": round(export_s, 2),
        "wall_s": round(layers_s + export_s, 2),
        "fps": round(frames / (layers_s + export_s), 1),
    }


def compare(ffmpeg: str, gpu: Path, reference: Path, workdir: Path, fps: float) -> dict:
    """PSNR / SSIM image par image, les images les plus éloignées, planche-contact « référence | GPU »."""
    stats = workdir / "psnr.log"
    graph = (f"[0:v]setpts=PTS-STARTPTS[g];[1:v]setpts=PTS-STARTPTS[r];[g]split[g1][g2];[r]split[r1][r2];"
             f"[g1][r1]psnr=stats_file={stats.name}[p];[p]nullsink;[g2][r2]ssim")
    completed = subprocess.run([ffmpeg, "-v", "info", "-nostdin", "-i", str(gpu), "-i", str(reference),
                                "-filter_complex", graph, "-f", "null", "-"], cwd=workdir, capture_output=True,
                               text=True, check=False)
    text = completed.stderr
    psnr = re.search(r"PSNR y:([\d.inf]+) u:([\d.inf]+) v:([\d.inf]+) average:([\d.inf]+)", text)
    ssim = re.search(r"SSIM Y:([\d.]+).*All:([\d.]+)", text)
    per_frame = []
    for line in stats.read_text().splitlines() if stats.exists() else ():
        number = re.search(r"n:(\d+)", line)
        value = re.search(r"psnr_avg:([\d.inf]+)", line)
        if number and value:
            per_frame.append((float(value.group(1)) if value.group(1) != "inf" else 99.0, int(number.group(1)) - 1))
    worst = sorted(per_frame)[:3]
    result = {
        "psnr_avg_db": float(psnr.group(4)) if psnr else None,
        "psnr_y_db": float(psnr.group(1)) if psnr else None,
        "ssim_all": float(ssim.group(2)) if ssim else None,
        "worst_frames": [{"frame": n, "time_s": round(n / fps, 2), "psnr_db": round(v, 2)} for v, n in worst],
    }
    total = len(per_frame)
    if total:
        chosen = sorted({int(total * f) for f in (0.05, 0.3, 0.6, 0.85)} | {n for _v, n in worst})[:6]
        select = "+".join(f"eq(n\\,{n})" for n in chosen)
        sheet = workdir / "contact_sheet.png"
        graph = (f"[1:v]select='{select}',scale=360:-2,setpts=N[r];[0:v]select='{select}',scale=360:-2,setpts=N[g];"
                 f"[r][g]hstack,tile={min(3, len(chosen))}x{-(-len(chosen) // 3)}:padding=8:color=white")
        subprocess.run([ffmpeg, "-v", "error", "-y", "-nostdin", "-i", str(gpu), "-i", str(reference),
                        "-filter_complex", graph, "-frames:v", "1", "-update", "1", str(sheet)], check=False)
        result["contact_sheet"] = str(sheet)
        result["contact_frames"] = chosen
    return result


def mux_audio(ffmpeg: str, video: Path, reference: Path, output: Path) -> bool:
    completed = subprocess.run([ffmpeg, "-v", "error", "-y", "-nostdin", "-i", str(video), "-i", str(reference),
                                "-map", "0:v:0", "-map", "1:a:0?", "-c", "copy", "-shortest", str(output)],
                               check=False)
    return completed.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=None, help="limiter aux N premières secondes")
    parser.add_argument("--hardware", default="videotoolbox", help="encodeur, comme l'export (cpu, videotoolbox…)")
    parser.add_argument("--api", default="metal" if sys.platform == "darwin" else "d3d11")
    parser.add_argument("--raster-threads", type=int, nargs="+", default=[0],
                        help="fils de rastérisation des calques (0 : fil principal) ; plusieurs valeurs : plusieurs essais")
    parser.add_argument("--no-reference", action="store_true")
    args = parser.parse_args(argv)
    if not os.environ.get("KUT_STUDIO_CONFIG_DIR"):
        parser.error("définir KUT_STUDIO_CONFIG_DIR (dossier jetable) : l'essai ne touche pas à la configuration")

    from core.project_io import load_project
    from core.tool_paths import find_media_tool

    ffmpeg, ffprobe = find_media_tool("ffmpeg"), find_media_tool("ffprobe")
    workdir = args.workdir.expanduser().resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    project = load_project(str(args.project.expanduser()))
    report: dict = {"project": str(args.project), "size": [project.width, project.height, project.fps]}
    runs = [run_gpu(project, workdir, ffmpeg, ffprobe, seconds=args.seconds, hardware=args.hardware, api=args.api,
                    raster_threads=threads) for threads in args.raster_threads]
    for run in runs:
        print(json.dumps(run, ensure_ascii=False), flush=True)
    report["gpu_runs"] = runs
    report["gpu"] = min(runs, key=lambda run: run["wall_s"])
    if not args.no_reference:
        report["reference"] = run_reference(project, workdir, seconds=args.seconds, hardware=args.hardware)
        print(json.dumps(report["reference"], ensure_ascii=False), flush=True)
        report["speedup"] = round(report["reference"]["wall_s"] / report["gpu"]["wall_s"], 2)
        report["compare"] = compare(ffmpeg, Path(report["gpu"]["output"]), Path(report["reference"]["output"]),
                                    workdir, float(project.fps))
        final = workdir / f"{args.project.stem}-gpu.mp4"
        if mux_audio(ffmpeg, Path(report["gpu"]["output"]), Path(report["reference"]["output"]), final):
            report["final"] = str(final)
    (workdir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in report if k not in ("gpu", "gpu_runs", "reference")}, ensure_ascii=False,
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
