"""Moniteur GPU : exécution QRhi des passes de :mod:`core.gpu_composite`.

``GpuPreviewWidget`` est un ``QRhiWidget`` (Metal, Direct3D 11, OpenGL selon la
plateforme ; aucun moteur graphique maison) placé **sous** la vue
``QGraphicsView`` du viewer, rendue transparente : poignées, repères, calques
motion graphics et trackers restent dessinés par Qt au-dessus, sans changement.

Chemin d'une image ::

    QMediaPlayer (décodage Qt : VideoToolbox / D3D11VA / VAAPI… ou logiciel)
      └─ QVideoSink → QVideoFrame mappée (NV12 / P010 / YUV420P…, copie zéro côté Qt)
          └─ render() : 1 envoi par plan (pointeur direct, copie unique par Qt)
              └─ passes : prep → flou/netteté → composite → present (le GPU)

Aucune relecture GPU → CPU. Les formats que le shader ne lit pas passent par
``QVideoFrame.toImage()`` (conversion CPU de Qt), compté comme « format de
repli » dans les diagnostics.

Erreurs : toute exception pendant le rendu, ``renderFailed`` de Qt ou une perte
du périphérique (``QRhi.isDeviceLost``) émettent :attr:`GpuPreviewWidget.failed`.
Le viewer libère alors les ressources et revient au moniteur CPU ; l'application
continue.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, replace

from PySide6.QtCore import QByteArray, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QRhi,
    QRhiBuffer,
    QRhiColorAttachment,
    QRhiDepthStencilClearValue,
    QRhiGraphicsPipeline,
    QRhiSampler,
    QRhiShaderResourceBinding,
    QRhiShaderStage,
    QRhiTexture,
    QRhiTextureRenderTargetDescription,
    QRhiTextureSubresourceUploadDescription,
    QRhiTextureUploadDescription,
    QRhiTextureUploadEntry,
    QRhiVertexInputAttribute,
    QRhiVertexInputBinding,
    QRhiVertexInputLayout,
    QRhiViewport,
    QShader,
)
from PySide6.QtWidgets import QRhiWidget

from core.gpu_backend import FrameStats, shader_dir
from core.gpu_cache import GpuTextureCache
from core.gpu_composite import (
    UNIFORM_BYTES,
    CompositeFrame,
    FramePlan,
    PassSpec,
    VideoSource,
    plan_frame,
    present_uniforms,
)
from core.gpu_frames import LAYOUTS, QT_COLOR_RANGES, QT_COLOR_SPACES, layout_for
from core.gpu_grade import DOMAIN_RGB, DOMAIN_YUV, LUT_SIZE, GradeLutCache
from ui.i18n import translate

LOGGER = logging.getLogger("kut_studio.gpu")

SRB_CACHE_SIZE = 128
"""Liaisons de ressources gardées : une image en utilise une dizaine au plus ; le reste est du cache."""

_FORMATS = {
    "R8": QRhiTexture.Format.R8,
    "RG8": QRhiTexture.Format.RG8,
    "R16": QRhiTexture.Format.R16,
    "RG16": QRhiTexture.Format.RG16,
    "RGBA8": QRhiTexture.Format.RGBA8,
    "BGRA8": QRhiTexture.Format.BGRA8,
    "RGBA16F": QRhiTexture.Format.RGBA16F,
}

_APIS = {
    "metal": QRhiWidget.Api.Metal,
    "d3d11": QRhiWidget.Api.Direct3D11,
    "opengl": QRhiWidget.Api.OpenGL,
    "vulkan": QRhiWidget.Api.Vulkan,
    "null": QRhiWidget.Api.Null,
}

_FLIP_OFFSET = (16 * 3 + 2) * 4
"""Octet de ``target.z`` (retournement Y) dans le bloc ``Params``."""

QUAD = (
    -1.0, 1.0, 0.0, 0.0,
    1.0, 1.0, 1.0, 0.0,
    -1.0, -1.0, 0.0, 1.0,
    1.0, -1.0, 1.0, 1.0,
)
"""Bande de deux triangles : position NDC + uv (uv.y = 0 en haut)."""


class GpuUnavailable(RuntimeError):
    """Le GPU ne peut pas servir le moniteur (cause lisible dans le message)."""

    def __init__(self, message: str, *, out_of_memory: bool = False) -> None:
        super().__init__(message)
        self.out_of_memory = out_of_memory
        """Manque de mémoire GPU : classé ``out_of_memory`` (pas ``render``), quelle que soit la langue du message."""


def _with_flip(data: bytes, flip: float) -> bytes:
    import struct

    return data[:_FLIP_OFFSET] + struct.pack("<f", flip) + data[_FLIP_OFFSET + 4:]


def load_shader(name: str) -> QShader:
    path = shader_dir() / f"{name}.qsb"
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise GpuUnavailable(translate("gpu.error.shader_missing", name=path.name)) from error
    shader = QShader.fromSerialized(QByteArray(raw))
    if not shader.isValid():
        raise GpuUnavailable(translate("gpu.error.shader_invalid", name=path.name))
    return shader


# --- Image décodée en attente d'envoi ----------------------------------------------------------------


@dataclass
class PendingFrame:
    """Une ``QVideoFrame`` reçue ; mappée seulement au moment de l'envoi (dans ``render``)."""

    frame: object
    generation: int


def describe_qt_frame(frame) -> tuple[str, int, int, str, str]:
    """``(disposition, largeur, hauteur, espace, plage)`` ; disposition ``""`` = repli Qt."""
    pixel = frame.pixelFormat().name
    layout = layout_for(pixel)
    fmt = frame.surfaceFormat()
    space = QT_COLOR_SPACES.get(fmt.colorSpace().name, "")
    rng = QT_COLOR_RANGES.get(fmt.colorRange().name, "")
    return (layout.name if layout is not None else "", int(frame.width()), int(frame.height()), space, rng)


# --- Exécuteur ------------------------------------------------------------------------------------------


class RhiExecutor:
    """Ressources QRhi et exécution d'un :class:`~core.gpu_composite.FramePlan`."""

    SHADERS = ("clear", "prep", "blur", "sharpen", "shift", "haze", "glow", "grade", "composite")

    def __init__(self, rhi: QRhi) -> None:
        self.rhi = rhi
        self.backend = rhi.backendName()
        self.device = ""
        try:
            info = rhi.driverInfo()  # garder l'objet : son QByteArray meurt avec lui
            self.device = bytes(info.deviceName).decode("utf-8", "replace")
        except Exception:
            LOGGER.debug("Nom du périphérique GPU illisible : diagnostic sans nom de carte", exc_info=True)
        self._vertex = load_shader("quad.vert")
        self._fragments = {name: load_shader(f"{name}.frag") for name in (*self.SHADERS, "present")}
        self.texture_flip = 1.0 if (rhi.isYUpInNDC() and not rhi.isYUpInFramebuffer()) else -1.0
        self.display_flip = 1.0 if rhi.isYUpInNDC() else -1.0
        self.working_format = "RGBA16F" if rhi.isTextureFormatSupported(QRhiTexture.Format.RGBA16F) else "RGBA8"
        self.stride_uploads = rhi.isFeatureSupported(QRhi.Feature.ImageDataStride)
        self.unmappable_frames = 0
        """Images dont ``map()`` a échoué : converties par Qt (copie CPU) au lieu de faire échouer le GPU."""
        self._resources: list[object] = []
        self._vbuf = self._keep(rhi.newBuffer(QRhiBuffer.Type.Immutable, QRhiBuffer.UsageFlag.VertexBuffer, 64))
        if not self._vbuf.create():
            raise GpuUnavailable(translate("gpu.error.vertex_buffer"))
        self._vbuf_uploaded = False
        self._sampler = self._keep(rhi.newSampler(
            QRhiSampler.Filter.Linear, QRhiSampler.Filter.Linear, QRhiSampler.Filter.None_,
            QRhiSampler.AddressMode.ClampToEdge, QRhiSampler.AddressMode.ClampToEdge,
        ))
        self._sampler.create()
        self._dummy = self._keep(rhi.newTexture(QRhiTexture.Format.RGBA8, QSize(1, 1)))
        self._dummy.create()
        self._dummy_uploaded = False
        self._ubufs: list[QRhiBuffer] = []
        self._targets: dict[str, tuple] = {}  # nom -> (spec, texture, rt, rpd)
        self._sources: dict[str, tuple] = {}  # id -> (clé, [textures])
        self._template_rpds: dict[str, object] = {}
        self._pipelines: dict[tuple, QRhiGraphicsPipeline] = {}
        self._srbs: OrderedDict[tuple, object] = OrderedDict()
        self._pinned_srbs: dict[tuple, object] = {}
        self._present: QRhiGraphicsPipeline | None = None
        self._present_rpd = None
        self.texture_bytes = 0

    def _keep(self, resource):
        self._resources.append(resource)
        return resource

    # -- ressources -----------------------------------------------------------------------------

    def _ubuf(self, index: int) -> QRhiBuffer:
        while len(self._ubufs) <= index:
            buf = self.rhi.newBuffer(QRhiBuffer.Type.Dynamic, QRhiBuffer.UsageFlag.UniformBuffer,
                                     self.rhi.ubufAligned(UNIFORM_BYTES))
            if not buf.create():
                raise GpuUnavailable(translate("gpu.error.uniform_buffer"))
            self._ubufs.append(buf)
        return self._ubufs[index]

    def _target(self, name: str, spec) -> tuple:
        fmt = self.working_format if spec.format == "RGBA16F" else spec.format
        entry = self._targets.get(name)
        if entry is not None and entry[0] == (fmt, spec.width, spec.height):
            return entry
        if entry is not None:
            self._release_target(name)
        texture = self.rhi.newTexture(_FORMATS[fmt], QSize(spec.width, spec.height), 1,
                                      QRhiTexture.Flag.RenderTarget)
        if not texture.create():
            raise GpuUnavailable(translate("gpu.error.texture", width=spec.width, height=spec.height), out_of_memory=True)
        rt = self.rhi.newTextureRenderTarget(QRhiTextureRenderTargetDescription(QRhiColorAttachment(texture)))
        rpd = rt.newCompatibleRenderPassDescriptor()
        rt.setRenderPassDescriptor(rpd)
        if not rt.create():
            raise GpuUnavailable(translate("gpu.error.render_target"))
        if fmt not in self._template_rpds:
            # Modèle des pipelines de ce format, DISTINCT du descripteur de la cible : celui-ci est détruit
            # avec elle (redimensionnement, changement de séquence) alors que les pipelines suivants
            # s'appuyaient encore dessus.
            self._template_rpds[fmt] = rt.newCompatibleRenderPassDescriptor()
        entry = ((fmt, spec.width, spec.height), texture, rt, rpd)
        self._targets[name] = entry
        self.texture_bytes += spec.width * spec.height * (8 if fmt == "RGBA16F" else 4)
        self._srbs.clear()
        return entry

    def _release_target(self, name: str) -> None:
        entry = self._targets.pop(name, None)
        if entry is None:
            return
        (fmt, width, height), texture, rt, rpd = entry
        for resource in (rt, rpd, texture):
            resource.destroy()
        self.texture_bytes -= width * height * (8 if fmt == "RGBA16F" else 4)
        self._srbs.clear()

    def _pipeline(self, shader: str, fmt: str):
        key = (shader, fmt)
        pipeline = self._pipelines.get(key)
        if pipeline is None:
            pipeline = self._make_pipeline(self._fragments[shader], self._template_rpds[fmt])
            self._pipelines[key] = pipeline
        return pipeline

    def _layout(self):
        layout = QRhiVertexInputLayout()
        layout.setBindings([QRhiVertexInputBinding(16)])
        layout.setAttributes([QRhiVertexInputAttribute(0, 0, QRhiVertexInputAttribute.Format.Float4, 0)])
        return layout

    def _make_pipeline(self, fragment: QShader, rpd) -> QRhiGraphicsPipeline:
        pipeline = self.rhi.newGraphicsPipeline()
        pipeline.setShaderStages([
            QRhiShaderStage(QRhiShaderStage.Type.Vertex, self._vertex),
            QRhiShaderStage(QRhiShaderStage.Type.Fragment, fragment),
        ])
        pipeline.setVertexInputLayout(self._layout())
        pipeline.setTopology(QRhiGraphicsPipeline.Topology.TriangleStrip)
        pipeline.setShaderResourceBindings(self._srb_for(self._ubuf(0), (self._dummy,) * 3, pinned=True))
        pipeline.setRenderPassDescriptor(rpd)
        if not pipeline.create():
            raise GpuUnavailable(translate("gpu.error.pipeline"))
        return pipeline

    def _srb_for(self, ubuf, textures, *, pinned: bool = False):
        # La clé porte les OBJETS, pas leur ``id()`` : Python réutilise l'identifiant d'un objet libéré, et
        # une texture neuve (un masque animé en crée une par image) retombait sur la liaison construite
        # pour une texture déjà détruite. Tant que l'entrée existe, la clé garde ses objets vivants.
        key = (ubuf, *textures)
        table = self._pinned_srbs if pinned else self._srbs
        srb = table.get(key)
        if srb is not None:
            if not pinned:
                table.move_to_end(key)
            return srb
        stages = QRhiShaderResourceBinding.StageFlag.VertexStage | QRhiShaderResourceBinding.StageFlag.FragmentStage
        fragment = QRhiShaderResourceBinding.StageFlag.FragmentStage
        srb = self.rhi.newShaderResourceBindings()
        srb.setBindings([
            QRhiShaderResourceBinding.uniformBuffer(0, stages, ubuf),
            *(QRhiShaderResourceBinding.sampledTexture(i + 1, fragment, t, self._sampler)
              for i, t in enumerate(textures)),
        ])
        if not srb.create():
            raise GpuUnavailable(translate("gpu.error.bindings"))
        table[key] = srb
        while not pinned and len(table) > SRB_CACHE_SIZE:
            _key, oldest = table.popitem(last=False)   # le moins récemment utilisé, jamais celui de l'image courante
            try:
                oldest.destroy()
            except Exception:  # noqa: BLE001 - libérer ne doit jamais interrompre le rendu
                LOGGER.debug(
                    "Libération d'un jeu de liaisons GPU en échec : ressource laissée au pilote",
                    exc_info=True,
                )
        return srb

    def ensure_present_pipeline(self, rpd) -> None:
        if self._present is not None and self._present_rpd is not None and rpd.isCompatible(self._present_rpd):
            return
        if self._present is not None:
            self._present.destroy()
        self._present = self._make_pipeline(self._fragments["present"], rpd)
        self._present_rpd = rpd

    # -- envois --------------------------------------------------------------------------------------

    def upload_source(self, batch, source_id: str, frame) -> tuple[int, VideoSource]:
        """Envoie les plans d'une ``QVideoFrame`` ; retourne ``(octets, description réelle)``."""
        from PySide6.QtMultimedia import QVideoFrame

        layout_name, width, height, space, color_range = describe_qt_frame(frame)
        layout = LAYOUTS.get(layout_name)
        if layout is not None and any(
            not self.rhi.isTextureFormatSupported(_FORMATS[p.texture_format]) for p in layout.planes
        ):
            layout = None
        if layout is not None and not frame.map(QVideoFrame.MapMode.ReadOnly):
            # Image non mappable (surface matérielle, tampon repris par le décodeur…) : on la fait convertir par
            # Qt plutôt que de lever, ce qui condamnait le moniteur GPU pour toute la session.
            self.unmappable_frames += 1
            if self.unmappable_frames == 1:
                LOGGER.info("Image vidéo non mappable (%s) : conversion par Qt (copie CPU)", layout_name)
            layout = None
            mapped = False
        else:
            mapped = layout is not None
        if layout is None:
            image = frame.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
            textures = self._source_textures(source_id, LAYOUTS["rgba"], image.width(), image.height())
            batch.uploadTexture(textures[0], QRhiTextureUploadDescription(QRhiTextureUploadEntry(
                0, 0, QRhiTextureSubresourceUploadDescription(image))))
            return image.sizeInBytes(), VideoSource(source_id, "rgba", image.width(), image.height())
        sent = 0
        try:
            textures = self._source_textures(source_id, layout, width, height)
            for index, spec in enumerate(layout.planes):
                pw, ph = layout.plane_size(index, width, height)
                stride = int(frame.bytesPerLine(index))
                view = frame.bits(index)
                row = pw * spec.bytes_per_texel
                description = self._plane_description(view, stride, row, ph)
                batch.uploadTexture(textures[index], QRhiTextureUploadDescription(
                    QRhiTextureUploadEntry(0, 0, description)))
                sent += row * ph
        finally:
            if mapped:
                frame.unmap()
        return sent, VideoSource(source_id, layout.name, width, height, space, color_range)

    def _plane_description(self, view, stride: int, row: int, rows: int):
        """Description d'envoi d'un plan mappé.

        PySide6 6.11 refuse les ``memoryview`` (et les pointeurs bruts) dans
        ``QRhiTextureSubresourceUploadDescription`` malgré sa signature : le plan
        est donc copié **une fois** en ``bytes`` (mesuré : ~1 ms pour 8 Mo, ~3 ms
        pour une image 4K P010). Le pas de ligne du décodeur est transmis tel quel
        (``setDataStride``) quand le backend le permet : pas de recopie ligne à ligne.
        """
        view = memoryview(view).cast("B")
        if stride == row or self.stride_uploads:
            description = QRhiTextureSubresourceUploadDescription(view[: stride * (rows - 1) + row].tobytes())
            if stride != row:
                description.setDataStride(stride)
            return description
        tight = bytearray(row * rows)
        for line in range(rows):
            tight[line * row:(line + 1) * row] = view[line * stride:line * stride + row]
        return QRhiTextureSubresourceUploadDescription(bytes(tight))

    def _source_textures(self, source_id: str, layout, width: int, height: int) -> list:
        key = (layout.name, width, height)
        entry = self._sources.get(source_id)
        if entry is not None and entry[0] == key:
            return entry[1]
        if entry is not None:
            self.release_source(source_id)
        textures = []
        for index, spec in enumerate(layout.planes):
            pw, ph = layout.plane_size(index, width, height)
            texture = self.rhi.newTexture(_FORMATS[spec.texture_format], QSize(pw, ph))
            if not texture.create():
                raise GpuUnavailable(translate("gpu.error.video_texture"), out_of_memory=True)
            textures.append(texture)
            self.texture_bytes += pw * ph * spec.bytes_per_texel
        self._sources[source_id] = (key, textures, layout.bytes_per_frame(width, height))
        self._srbs.clear()
        return textures

    def release_source(self, source_id: str) -> None:
        entry = self._sources.pop(source_id, None)
        if entry is None:
            return
        for texture in entry[1]:
            texture.destroy()
        self.texture_bytes -= entry[2]
        self._srbs.clear()

    def has_source(self, source_id: str) -> bool:
        return source_id in self._sources

    def new_matte_texture(self, batch, image: QImage):
        image = image.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
        texture = self.rhi.newTexture(QRhiTexture.Format.RGBA8, image.size())
        if not texture.create():
            raise GpuUnavailable(translate("gpu.error.matte_texture"))
        batch.uploadTexture(texture, QRhiTextureUploadDescription(QRhiTextureUploadEntry(
            0, 0, QRhiTextureSubresourceUploadDescription(image))))
        return texture

    # -- exécution ----------------------------------------------------------------------------------

    def run(self, cb, plan: FramePlan, batch, mattes: dict, widget_rt, present: bytes, viewport) -> None:
        """Enregistre les passes de ``plan`` puis la présentation dans ``widget_rt``."""
        if not self._vbuf_uploaded:
            import struct

            batch.uploadStaticBuffer(self._vbuf, struct.pack("16f", *QUAD))
            self._vbuf_uploaded = True
        if not self._dummy_uploaded:
            blank = QImage(1, 1, QImage.Format.Format_RGBA8888_Premultiplied)
            blank.fill(Qt.transparent)
            batch.uploadTexture(self._dummy, QRhiTextureUploadDescription(QRhiTextureUploadEntry(
                0, 0, QRhiTextureSubresourceUploadDescription(blank))))
            self._dummy_uploaded = True
        for name, spec in plan.textures.items():
            self._target(name, spec)
        for name in [n for n in self._targets if n not in plan.textures]:
            self._release_target(name)
        pending = batch
        for index, step in enumerate(plan.passes):
            ubuf = self._ubuf(index)
            update = pending if pending is not None else self.rhi.nextResourceUpdateBatch()
            update.updateDynamicBuffer(ubuf, 0, _with_flip(step.uniforms, self.texture_flip))
            self._record(cb, step, ubuf, mattes, update)
            pending = None
        ubuf = self._ubuf(len(plan.passes))
        update = pending if pending is not None else self.rhi.nextResourceUpdateBatch()
        update.updateDynamicBuffer(ubuf, 0, _with_flip(present, self.display_flip))
        canvas = self._targets[plan.canvas][1]
        srb = self._srb_for(ubuf, (canvas, self._dummy, self._dummy))
        cb.beginPass(widget_rt, QColor(0, 0, 0, 255), QRhiDepthStencilClearValue(1.0, 0), update)
        cb.setGraphicsPipeline(self._present)
        cb.setViewport(QRhiViewport(0, 0, float(viewport[0]), float(viewport[1])))
        cb.setShaderResources(srb)
        cb.setVertexInput(0, [(self._vbuf, 0)])
        cb.draw(4)
        cb.endPass()

    def _input_texture(self, name: str, mattes: dict):
        if name == "none":
            return self._dummy
        if name.startswith("src:"):
            entry = self._sources.get(name[4:])
            if entry is None:
                return None
            textures = entry[1]
            return textures
        if name.startswith("matte:"):
            return mattes.get(name[6:], self._dummy)
        return self._targets[name][1]

    def _record(self, cb, step: PassSpec, ubuf, mattes: dict, batch) -> None:
        spec, texture, rt, _rpd = self._targets[step.target]
        fmt = spec[0]
        if step.shader == "prep":
            planes = self._input_texture(step.inputs[0], mattes)
            if planes is None:
                raise GpuUnavailable(translate("gpu.error.no_source"))
            if not isinstance(planes, list):  # calque d'effets : le cadre composé
                planes = [planes]
            textures = (list(planes) + [self._dummy] * 3)[:3]
        else:
            textures = [self._input_texture(name, mattes) for name in step.inputs]
            textures = (textures + [self._dummy] * 3)[:3]
        srb = self._srb_for(ubuf, tuple(textures))
        cb.beginPass(rt, QColor(0, 0, 0, 0), QRhiDepthStencilClearValue(1.0, 0), batch)
        cb.setGraphicsPipeline(self._pipeline(step.shader, fmt))
        cb.setViewport(QRhiViewport(0, 0, float(spec[1]), float(spec[2])))
        cb.setShaderResources(srb)
        cb.setVertexInput(0, [(self._vbuf, 0)])
        cb.draw(4)
        cb.endPass()

    def release(self) -> None:
        for name in list(self._targets):
            self._release_target(name)
        for source_id in list(self._sources):
            self.release_source(source_id)
        for resource in [*self._pipelines.values(), *self._srbs.values(), *self._pinned_srbs.values(),
                         *self._template_rpds.values(), *self._ubufs,
                         *([self._present] if self._present is not None else []), *self._resources]:
            try:
                resource.destroy()
            except Exception:
                LOGGER.debug(
                    "Libération d'une ressource GPU en échec à l'arrêt : ressource laissée au pilote",
                    exc_info=True,
                )
        self._pipelines.clear()
        self._template_rpds.clear()
        self._srbs.clear()
        self._pinned_srbs.clear()
        self._ubufs.clear()
        self._resources.clear()
        self._present = None
        self.texture_bytes = 0


# --- Widget -------------------------------------------------------------------------------------------


class GpuPreviewWidget(QRhiWidget):
    """Surface GPU du viewer (voir le module)."""

    failed = Signal(str, str)
    """``(type, détail)`` : ``init``, ``render``, ``device_lost``, ``out_of_memory``."""
    ready = Signal(str)
    """Première image réussie (nom de l'API et du GPU)."""

    def __init__(self, api: str = "metal", parent=None, cache_budget: int | None = None) -> None:
        super().__init__(parent)
        self.setApi(_APIS.get(api, QRhiWidget.Api.OpenGL))
        self.api = api
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.stats = FrameStats()
        self.cache = GpuTextureCache(cache_budget or 256 * 1024 * 1024, release=self._release_cached)
        # Le fil de cuisson ne touche à aucun objet Qt (il ne garde même pas de référence au widget : la dernière,
        # lâchée dans ce fil, détruirait le widget hors du fil de l'interface) ; le widget se redessine tant qu'une
        # cuisson est en cours (voir ``_poll_grades``).
        self.grades = GradeLutCache()
        self._grade_poll_pending = False
        self._lut_images: OrderedDict[str, QImage] = OrderedDict()
        self._last_luts: dict[str, str] = {}
        self.executor: RhiExecutor | None = None
        self._frame = CompositeFrame(1920, 1080)
        self._pending: dict[str, PendingFrame] = {}
        self._latest: dict[str, PendingFrame] = {}
        """Dernière image reçue par source : renvoyée au GPU quand Qt libère puis recrée ses ressources."""
        self._sources: dict[str, VideoSource] = {}
        self._generation = 0
        self._mattes: dict[str, QImage] = {}
        self._canvas_rect = (0.0, 0.0, 1.0, 1.0)
        self._background = (0.0, 0.0, 0.0)
        self._broken = False
        self._confirmed = False
        self.fallback_frames = 0
        self._logged_formats: set[str] = set()
        self.renderFailed.connect(self._on_render_failed)

    # -- état à afficher --------------------------------------------------------------------------

    def set_video_frame(self, source_id: str, frame) -> None:
        """Nouvelle image décodée (thread de l'interface, rapide : aucune copie)."""
        if self._broken or frame is None or not frame.isValid():
            return
        self._generation += 1
        self._pending[source_id] = self._latest[source_id] = PendingFrame(frame, self._generation)
        self.stats.note_arrival()
        self.update()

    def set_composite(self, frame: CompositeFrame, mattes: dict[str, QImage] | None = None) -> None:
        self._frame = frame
        self._mattes = dict(mattes or {})
        self.update()

    def set_canvas_rect(self, rect: tuple[float, float, float, float], background: tuple[float, float, float]) -> None:
        self._canvas_rect = rect
        self._background = background
        self.update()

    def composite(self) -> CompositeFrame:
        return self._frame

    def forget_source(self, source_id: str) -> None:
        """La source n'est plus affichée (trou de timeline, autre média)."""
        self._pending.pop(source_id, None)
        self._latest.pop(source_id, None)
        self._sources.pop(source_id, None)
        if self.executor is not None:
            self.executor.release_source(source_id)
        self.update()

    @property
    def broken(self) -> bool:
        return self._broken

    @property
    def confirmed(self) -> bool:
        return self._confirmed

    def device_label(self) -> str:
        executor = self.executor
        if executor is None:
            return ""
        return f"{executor.backend} · {executor.device}".strip(" ·")

    # -- QRhiWidget ---------------------------------------------------------------------------------

    def initialize(self, cb) -> None:  # noqa: N802 - API Qt
        try:
            rhi = self.rhi()
            if rhi is None:
                raise GpuUnavailable(translate("gpu.error.no_context"))
            if self.executor is None or self.executor.rhi is not rhi:
                if self.executor is not None:
                    self.executor.release()
                self.executor = RhiExecutor(rhi)
                self.cache.purge()
                LOGGER.info("Aperçu GPU initialisé : %s (%s)", self.executor.backend, self.executor.device)
            self.executor.ensure_present_pipeline(self.renderTarget().renderPassDescriptor())
        except Exception as error:  # jamais d'exception vers Qt
            self._fail("init", str(error))

    def render(self, cb) -> None:  # noqa: N802 - API Qt
        if self._broken or self.executor is None:
            return
        started = time.perf_counter()
        try:
            executor = self.executor
            batch = executor.rhi.nextResourceUpdateBatch()
            uploaded = 0
            for source_id, pending in list(self._pending.items()):
                sent, source = executor.upload_source(batch, source_id, pending.frame)
                uploaded += sent
                self._sources[source_id] = source
                if describe_qt_frame(pending.frame)[0] == "":
                    self.fallback_frames += 1
                    pixel = pending.frame.pixelFormat().name
                    if pixel not in self._logged_formats:  # une fois par format, pas à chaque image
                        self._logged_formats.add(pixel)
                        LOGGER.info("Format %s non lu par le shader : conversion par Qt (copie CPU)", pixel)
            self._pending.clear()
            frame = self._frame
            ready = [layer for layer in frame.layers
                     if executor.has_source(layer.source) and layer.source in self._sources]
            images = dict(self._mattes)
            ready = [self._resolve_grade(layer, images) for layer in ready]
            adjustments = tuple(self._resolve_grade(layer, images) for layer in frame.adjustments)
            frame = replace(frame, layers=tuple(ready), sources=tuple(self._sources.values()), adjustments=adjustments)
            mattes = {}
            for key, image in images.items():
                texture, _cached = self.cache.acquire(("matte", key), image.width() * image.height() * 4,
                                                      lambda image=image: executor.new_matte_texture(batch, image))
                mattes[key] = texture
            plan = plan_frame(frame)
            size = self.colorTexture().pixelSize() if self.colorTexture() is not None else QSize(1, 1)
            viewport = (float(size.width()), float(size.height()))
            dpr = self.devicePixelRatioF()
            x, y, w, h = self._canvas_rect
            present = present_uniforms(viewport, (x * dpr, y * dpr, w * dpr, h * dpr), self._background)
            executor.run(cb, plan, batch, mattes, self.renderTarget(), present, viewport)
        except Exception as error:
            self._fail("out_of_memory" if getattr(error, "out_of_memory", False) else "render", str(error))
            return
        self.stats.note_render((time.perf_counter() - started) * 1000.0, uploaded)
        if not self._confirmed:
            self._confirmed = True
            QTimer.singleShot(0, lambda: self.ready.emit(self.device_label()))

    def _resolve_grade(self, layer, images: dict):
        """Étalonnage demandé → passe ``grade`` si sa LUT est cuite (sinon le calque passe sans, le temps de la
        cuisson). L'espace d'entrée de la LUT est celui du média (YUV et ses propriétés de couleur, ou RVB)."""
        grade = getattr(layer, "grade", None)
        if grade is None or getattr(layer, "grade_lut", ""):
            return layer
        source = self._sources.get(getattr(layer, "source", ""))
        if source is not None and LAYOUTS.get(source.layout) is not None and LAYOUTS[source.layout].is_yuv:
            options = {"domain": DOMAIN_YUV, "colorspace": source.colorspace, "color_range": source.color_range}
        else:
            options = {"domain": DOMAIN_RGB, "colorspace": "", "color_range": ""}
        found = self.grades.lookup(grade, **options)
        slot = f"{getattr(layer, 'source', '')}:{options['domain']}"
        if found is None and not self.grades.failed(grade, **options):
            self._poll_grades()
        if found is None:
            # Cuisson en cours (un curseur qu'on glisse) : la LUT précédente du calque, plutôt qu'un clignotement
            # sans étalonnage ; aucune si le calque n'en a jamais eu.
            previous = self._last_luts.get(slot)
            if previous is not None and previous in self._lut_images and not self.grades.failed(grade, **options):
                images[previous] = self._lut_images[previous]
                return replace(layer, grade=None, grade_lut=previous)
            return replace(layer, grade=None)
        key, atlas = found
        name = f"lut:{key}"
        self._last_luts[slot] = name
        image = self._lut_images.get(name)
        if image is None:
            size = LUT_SIZE
            image = QImage(atlas, size * size, size, size * size * 3, QImage.Format.Format_RGB888).copy()
            self._lut_images[name] = image
            while len(self._lut_images) > 16:
                self._lut_images.popitem(last=False)
        images[name] = image
        return replace(layer, grade=None, grade_lut=name)

    def _poll_grades(self) -> None:
        """Une cuisson est en cours : nouvelle image dans 40 ms, qui reprendra la LUT dès qu'elle est prête."""
        if self._grade_poll_pending:
            return
        self._grade_poll_pending = True
        QTimer.singleShot(40, self, self._grade_poll_tick)

    def _grade_poll_tick(self) -> None:
        self._grade_poll_pending = False
        self.update()

    def releaseResources(self) -> None:  # noqa: N802 - API Qt
        # Qt libère les ressources quand le widget est masqué ou détaché, puis le recrée : en pause aucune
        # nouvelle image n'arrive, il faut donc lui redonner la dernière (sinon moniteur noir jusqu'à la lecture).
        self.release_gpu(keep_frames=True)

    # -- erreurs et libération ---------------------------------------------------------------------

    def release_gpu(self, keep_frames: bool = False) -> None:
        """Libère les ressources GPU. ``keep_frames`` : garde la dernière image de chaque source pour la renvoyer
        à la recréation ; sinon (changement de projet, pression mémoire) tout est abandonné, images décodées comprises.
        """
        self.cache.purge()
        if self.executor is not None:
            self.executor.release()
            self.executor = None
        self._pending = dict(self._latest) if keep_frames else {}
        if not keep_frames:
            self._latest.clear()
        self._sources.clear()

    def release_gpu_cache(self) -> int:
        """Vide le cache de textures (changement de projet, pression mémoire)."""
        freed = self.cache.purge()
        self.update()
        return freed

    def _release_cached(self, texture) -> None:
        try:
            texture.destroy()
        except Exception:
            LOGGER.debug("Libération d'une texture GPU du cache en échec : texture laissée au pilote", exc_info=True)

    def _on_render_failed(self) -> None:
        rhi = None
        try:
            rhi = self.rhi()
        except Exception:
            LOGGER.debug(
                "Contexte QRhi illisible après un rendu refusé : échec classé « render » faute de savoir si le périphérique est perdu",
                exc_info=True,
            )
        lost = bool(rhi is not None and rhi.isDeviceLost())
        self._fail("device_lost" if lost else "render",
                   translate("gpu.error.device_lost") if lost else translate("gpu.error.render_refused"))

    def _fail(self, kind: str, detail: str) -> None:
        if self._broken:
            return
        self._broken = True
        LOGGER.warning("Moniteur GPU en échec (%s) : %s", kind, detail)
        # Hors de render() : le viewer peut détruire ce widget sans danger.
        QTimer.singleShot(0, lambda: self.failed.emit(kind, detail))


def gpu_self_check() -> str:  # i18n-ignore: sortie console du smoke test de build, pas l'interface
    """``""`` si tous les shaders sont présents et valides (smoke test de l'application construite)."""
    from core.gpu_backend import SHADER_NAMES, missing_shaders

    missing = missing_shaders()
    if missing:
        return "shaders absents : " + ", ".join(missing)
    try:
        for name in SHADER_NAMES:
            load_shader(name)
    except GpuUnavailable as error:
        return str(error)
    return ""


__all__ = ["GpuPreviewWidget", "gpu_self_check", "GpuUnavailable", "RhiExecutor", "describe_qt_frame", "load_shader"]
