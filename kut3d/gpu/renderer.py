"""Moteur « qrhi » : rendu 3D par QRhi (Metal, Direct3D 11, OpenGL...) dans un `QRhiWidget` JAMAIS affiché.

PySide6 ne permet pas l'API QRhi « hors écran » pure (`beginOffscreenFrame` exige un `QRhiCommandBuffer`
non instanciable) : on utilise donc un `QRhiWidget` avec `WA_DontShowOnScreen`, dont `grabFramebuffer()` déclenche
`render()` de façon synchrone. Le rendu va dans NOS textures (pas celle du widget, réduite à 1x1) : une couleur
RGBA 32 bits flottants (linéaire, prémultipliée, mélange en matériel) et une profondeur de vue R32F, écrites par
la même passe (deux cibles de couleur), relues par `readBackTexture`. Le sur-échantillonnage, la conversion sRGB et
le fond sont ensuite faits côté CPU par le code du moteur numpy (`reduce_samples`, `finish_frame`).
"""

from __future__ import annotations

import os
import sys
import time

import numpy as np
from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QImage,
    QRhi,
    QRhiBuffer,
    QRhiColorAttachment,
    QRhiDepthStencilClearValue,
    QRhiGraphicsPipeline,
    QRhiReadbackDescription,
    QRhiReadbackResult,
    QRhiRenderBuffer,
    QRhiRenderPassDescriptor,
    QRhiResourceUpdateBatch,
    QRhiSampler,
    QRhiShaderResourceBinding,
    QRhiShaderStage,
    QRhiTexture,
    QRhiTextureRenderTarget,
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

from kut3d.errors import RenderBackendUnavailable
from kut3d.gpu.limits import TEXTURE_CACHE_BYTES
from kut3d.gpu.prepare import (
    DRAW_BLOCK_BYTES,
    JOINT_BLOCK_BYTES,
    VERTEX_STRIDE,
    FramePlan,
    plan_frame,
)
from kut3d.gpu.qtapp import ensure_application
from kut3d.gpu.shader_files import load_qsb
from kut3d.mathutil import Array
from kut3d.render.frame import Frame
from kut3d.render.numpy_backend import finish_frame, reduce_samples
from kut3d.scene import Scene

_STAGES = QRhiShaderResourceBinding.StageFlag
_VERTEX_FORMATS = (
    (QRhiVertexInputAttribute.Format.Float3, 0),  # pos
    (QRhiVertexInputAttribute.Format.Float3, 12),  # nrm
    (QRhiVertexInputAttribute.Format.Float2, 24),  # uv
    (QRhiVertexInputAttribute.Format.Float4, 32),  # col
    (QRhiVertexInputAttribute.Format.Float3, 48),  # tan
    (QRhiVertexInputAttribute.Format.Float, 60),  # tan_sign
    (QRhiVertexInputAttribute.Format.Float4, 64),  # joints
    (QRhiVertexInputAttribute.Format.Float4, 80),  # weights
)
_TEXTURE_BINDINGS = (3, 4, 5, 6, 7)  # base, normal, mr, emissive, occlusion
_SRGB_SLOTS = (True, False, False, True, False)


def _api() -> QRhiWidget.Api:
    """API graphique : Metal sur macOS, Direct3D 11 sous Windows, OpenGL ailleurs ; KUT3D_GPU_API force un choix."""
    names = {
        "metal": QRhiWidget.Api.Metal,
        "d3d11": QRhiWidget.Api.Direct3D11,
        "opengl": QRhiWidget.Api.OpenGL,
        "vulkan": QRhiWidget.Api.Vulkan,
        "null": QRhiWidget.Api.Null,
    }
    forced = os.environ.get("KUT3D_GPU_API", "").strip().lower()
    if forced in names:
        return names[forced]
    if sys.platform == "darwin":
        return QRhiWidget.Api.Metal
    if sys.platform == "win32":
        return QRhiWidget.Api.Direct3D11
    return QRhiWidget.Api.OpenGL


class _Job:
    def __init__(self, plan: FramePlan) -> None:
        self.plan = plan
        self.color: QRhiReadbackResult | None = None
        self.depth: QRhiReadbackResult | None = None
        self.error = ""


class _Widget(QRhiWidget):
    """Le widget qui exécute `render()` ; toutes les ressources QRhi vivent ici, détruites avant le QRhi."""

    def __init__(self) -> None:
        super().__init__()
        self.setApi(_api())
        self.setColorBufferFormat(QRhiWidget.TextureFormat.RGBA8)
        self.setFixedColorBufferSize(1, 1)
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.resize(1, 1)
        self.job: _Job | None = None
        self.failure = ""
        self.backend_name = ""
        self.y_up = False
        self.max_texture = 0
        self.timings: dict[str, float] = {}
        # mises à jour (texture blanche...) à joindre à la première passe
        self._pending_batch: QRhiResourceUpdateBatch | None = None
        self._frame_srbs: list = []  # liaisons de l'image en cours, détruites une fois la grab terminée
        self._rhi: QRhi | None = None
        self._reset_resources()
        self.renderFailed.connect(self._on_render_failed)
        self.show()  # « montré » hors écran : un seul QRhi pour toute la vie du widget (sinon un par grab)

    # -- cycle de vie -----------------------------------------------------------------------------------------

    def _reset_resources(self) -> None:
        self._targets: tuple[int, int] | None = None
        self._color_tex: QRhiTexture | None = None
        self._depth_tex: QRhiTexture | None = None
        self._ds: QRhiRenderBuffer | None = None
        self._rt: QRhiTextureRenderTarget | None = None
        self._rpd: QRhiRenderPassDescriptor | None = None
        self._sampler: QRhiSampler | None = None
        self._white: QRhiTexture | None = None
        self._frame_ub: QRhiBuffer | None = None
        self._dummy_joints: QRhiBuffer | None = None
        self._vb: QRhiBuffer | None = None
        self._ib: QRhiBuffer | None = None
        self._draw_ubs: list[QRhiBuffer] = []
        self._joint_ubs: list[QRhiBuffer] = []
        self._layout_srb = None
        self._pipelines: dict[tuple[bool, bool], QRhiGraphicsPipeline] = {}
        self._shaders: tuple[QShader, QShader] | None = None
        self._textures: dict[tuple[int, bool], tuple[QRhiTexture, Array]] = {}
        self._texture_bytes = 0

    def initialize(self, cb) -> None:  # noqa: N802, ARG002 - API Qt
        rhi = self.rhi()
        if rhi is not self._rhi:
            self._reset_resources()  # nouveau QRhi : les anciennes ressources ne lui appartiennent pas
            self._rhi = rhi
            self.backend_name = str(rhi.backendName())
            self.y_up = bool(rhi.isYUpInFramebuffer())
            self.max_texture = int(rhi.resourceLimit(QRhi.ResourceLimit.TextureSizeMax))

    def releaseResources(self) -> None:  # noqa: N802 - API Qt : appelé quand le QRhi va disparaître
        self._reset_resources()
        self._rhi = None

    def _on_render_failed(self) -> None:
        self.failure = "le rendu Qt a échoué (renderFailed)"

    def release_frame(self) -> None:
        """Libère les liaisons de ressources de l'image terminée (la grab est synchrone : le GPU a fini)."""
        self._frame_srbs = []

    def shutdown(self) -> None:
        self.job = None
        self._reset_resources()

    # -- ressources -------------------------------------------------------------------------------------------

    def _new_buffer(self, rhi: QRhi, usage, size: int) -> QRhiBuffer:
        buf = rhi.newBuffer(QRhiBuffer.Type.Dynamic, usage, max(16, int(size)))
        if not buf.create():
            raise RenderBackendUnavailable(
                "création d'un tampon GPU impossible (mémoire ?)."
            )
        return buf

    def _ensure_device(self, rhi: QRhi) -> None:
        if self._layout_srb is not None:
            return
        self._shaders = (
            QShader.fromSerialized(QByteArray(load_qsb("kut3d.vert"))),
            QShader.fromSerialized(QByteArray(load_qsb("kut3d.frag"))),
        )
        if not all(s.isValid() for s in self._shaders):
            raise RenderBackendUnavailable(
                "les shaders .qsb de kut3d sont illisibles : les régénérer."
            )
        sampler = rhi.newSampler(
            QRhiSampler.Filter.Linear, QRhiSampler.Filter.Linear, QRhiSampler.Filter.None_,
            QRhiSampler.AddressMode.Repeat, QRhiSampler.AddressMode.Repeat,
        )  # fmt: skip
        sampler.create()
        self._sampler = sampler
        white = rhi.newTexture(QRhiTexture.Format.RGBA8, QSize(1, 1))
        white.create()
        self._white = white
        self._frame_ub = self._new_buffer(rhi, QRhiBuffer.UsageFlag.UniformBuffer, 640)
        self._dummy_joints = self._new_buffer(
            rhi, QRhiBuffer.UsageFlag.UniformBuffer, JOINT_BLOCK_BYTES
        )
        batch = rhi.nextResourceUpdateBatch()
        self._upload_texture(batch, white, np.full((1, 1, 4), 255, np.uint8))
        batch.updateDynamicBuffer(self._dummy_joints, 0, bytes(JOINT_BLOCK_BYTES))
        self._pending_batch = batch
        self._layout_srb = self._make_srb(
            rhi,
            self._new_buffer(rhi, QRhiBuffer.UsageFlag.UniformBuffer, DRAW_BLOCK_BYTES),
            self._dummy_joints,
            [white] * 5,
        )

    def _make_srb(self, rhi: QRhi, draw_ub: QRhiBuffer, joint_ub: QRhiBuffer, textures):
        assert self._frame_ub is not None and self._sampler is not None
        srb = rhi.newShaderResourceBindings()
        both = _STAGES.VertexStage | _STAGES.FragmentStage
        bindings = [
            QRhiShaderResourceBinding.uniformBuffer(0, both, self._frame_ub),
            QRhiShaderResourceBinding.uniformBuffer(1, both, draw_ub),
            QRhiShaderResourceBinding.uniformBuffer(2, _STAGES.VertexStage, joint_ub),
        ]
        for slot, tex in zip(_TEXTURE_BINDINGS, textures, strict=True):
            bindings.append(
                QRhiShaderResourceBinding.sampledTexture(
                    slot, _STAGES.FragmentStage, tex, self._sampler
                )
            )
        srb.setBindings(bindings)
        if not srb.create():
            raise RenderBackendUnavailable(
                "création des liaisons de ressources GPU impossible."
            )
        return srb

    def _ensure_targets(self, rhi: QRhi, width: int, height: int) -> None:
        if self._targets == (width, height):
            return
        size = QSize(width, height)
        flags = QRhiTexture.Flag.RenderTarget | QRhiTexture.Flag.UsedAsTransferSource
        color = rhi.newTexture(QRhiTexture.Format.RGBA32F, size, 1, flags)
        depth = rhi.newTexture(QRhiTexture.Format.R32F, size, 1, flags)
        ds = rhi.newRenderBuffer(QRhiRenderBuffer.Type.DepthStencil, size)
        if not (color.create() and depth.create() and ds.create()):
            raise RenderBackendUnavailable(
                f"image {width}x{height} : mémoire GPU insuffisante pour les cibles de rendu."
            )
        desc = QRhiTextureRenderTargetDescription()
        desc.setColorAttachments(
            [QRhiColorAttachment(color), QRhiColorAttachment(depth)]
        )
        desc.setDepthStencilBuffer(ds)
        rt = rhi.newTextureRenderTarget(desc)
        if self._rpd is None:
            self._rpd = rt.newCompatibleRenderPassDescriptor()
        rt.setRenderPassDescriptor(self._rpd)
        if not rt.create():
            raise RenderBackendUnavailable(
                "création de la cible de rendu GPU impossible."
            )
        self._rt, self._color_tex, self._depth_tex, self._ds = rt, color, depth, ds
        self._targets = (width, height)

    def _pipeline(
        self, rhi: QRhi, cull_back: bool, blend: bool
    ) -> QRhiGraphicsPipeline:
        key = (cull_back, blend)
        pipe = self._pipelines.get(key)
        if pipe is not None:
            return pipe
        assert (
            self._shaders is not None
            and self._layout_srb is not None
            and self._rpd is not None
        )
        pipe = rhi.newGraphicsPipeline()
        pipe.setShaderStages(
            [
                QRhiShaderStage(QRhiShaderStage.Type.Vertex, self._shaders[0]),
                QRhiShaderStage(QRhiShaderStage.Type.Fragment, self._shaders[1]),
            ]
        )
        layout = QRhiVertexInputLayout()
        layout.setBindings([QRhiVertexInputBinding(VERTEX_STRIDE)])
        layout.setAttributes(
            [
                QRhiVertexInputAttribute(0, i, fmt, off)
                for i, (fmt, off) in enumerate(_VERTEX_FORMATS)
            ]
        )
        pipe.setVertexInputLayout(layout)
        pipe.setTopology(QRhiGraphicsPipeline.Topology.Triangles)
        pipe.setCullMode(
            QRhiGraphicsPipeline.CullMode.Back
            if cull_back
            else QRhiGraphicsPipeline.CullMode.None_
        )
        pipe.setFrontFace(QRhiGraphicsPipeline.FrontFace.CCW)
        pipe.setDepthTest(True)
        pipe.setDepthWrite(not blend)
        pipe.setDepthOp(QRhiGraphicsPipeline.CompareOp.Less)
        color_blend = QRhiGraphicsPipeline.TargetBlend()
        depth_blend = QRhiGraphicsPipeline.TargetBlend()
        if blend:  # « over » prémultiplié, comme le moteur numpy ; la profondeur n'est pas écrite
            color_blend.enable = True
            one, inv = (
                QRhiGraphicsPipeline.BlendFactor.One,
                QRhiGraphicsPipeline.BlendFactor.OneMinusSrcAlpha,
            )
            color_blend.srcColor = color_blend.srcAlpha = one
            color_blend.dstColor = color_blend.dstAlpha = inv
            depth_blend.colorWrite = QRhiGraphicsPipeline.ColorMask(0)
        pipe.setTargetBlends([color_blend, depth_blend])
        pipe.setShaderResourceBindings(self._layout_srb)
        pipe.setRenderPassDescriptor(self._rpd)
        if not pipe.create():
            raise RenderBackendUnavailable("création du pipeline graphique impossible.")
        self._pipelines[key] = pipe
        return pipe

    # -- textures ---------------------------------------------------------------------------------------------

    @staticmethod
    def _upload_texture(batch, tex: QRhiTexture, rgba: Array) -> None:
        h, w = rgba.shape[:2]
        image = QImage(
            np.ascontiguousarray(rgba).tobytes(),
            w,
            h,
            w * 4,
            QImage.Format.Format_RGBA8888,
        )
        sub = QRhiTextureSubresourceUploadDescription(image.copy())
        batch.uploadTexture(
            tex, QRhiTextureUploadDescription(QRhiTextureUploadEntry(0, 0, sub))
        )

    def _texture_for(
        self, rhi: QRhi, batch, array: Array | None, srgb: bool
    ) -> QRhiTexture:
        assert self._white is not None
        if array is None:
            return self._white
        key = (id(array), srgb)
        hit = self._textures.get(key)
        if hit is not None and hit[1] is array:
            self._textures[key] = self._textures.pop(
                key
            )  # le plus récemment utilisé en dernier
            return hit[0]
        h, w = array.shape[:2]
        flags = QRhiTexture.Flag.sRGB if srgb else QRhiTexture.Flag(0)
        tex = rhi.newTexture(QRhiTexture.Format.RGBA8, QSize(w, h), 1, flags)
        if not tex.create():
            raise RenderBackendUnavailable(
                f"texture {w}x{h} : création GPU impossible."
            )
        self._upload_texture(batch, tex, array)
        self._textures[key] = (tex, array)
        self._texture_bytes += w * h * 4
        return tex

    def _evict_textures(self, keep: set[int]) -> None:
        while self._texture_bytes > TEXTURE_CACHE_BYTES and self._textures:
            victim = next(
                (k for k in self._textures if id(self._textures[k][1]) not in keep),
                None,
            )
            if victim is None:
                return
            tex, arr = self._textures.pop(victim)
            self._texture_bytes -= arr.shape[0] * arr.shape[1] * 4

    # -- rendu ------------------------------------------------------------------------------------------------

    def render(self, cb) -> None:  # type: ignore[override]  # noqa: A003 - API Qt (QRhiWidget::render)
        job = self.job
        if job is None:
            return  # rendu spontané de Qt (affichage) : rien à dessiner
        self.job = None
        try:
            self._draw(cb, job)
        except Exception as error:  # noqa: BLE001 - jamais d'exception vers Qt : on la relaie par le job
            job.error = f"{type(error).__name__}: {error}"

    def _draw(self, cb, job: _Job) -> None:
        started = time.perf_counter()
        rhi = self.rhi()
        plan = job.plan
        width, height = plan.size
        self._ensure_device(rhi)
        self._ensure_targets(rhi, width, height)
        assert (
            self._rt is not None
            and self._frame_ub is not None
            and self._dummy_joints is not None
        )
        batch = self._pending_batch or rhi.nextResourceUpdateBatch()
        self._pending_batch = None
        corr = np.array(rhi.clipSpaceCorrMatrix().data(), dtype=np.float64).reshape(
            4, 4, order="F"
        )
        batch.updateDynamicBuffer(self._frame_ub, 0, plan.frame_block(corr))
        count = len(plan.runs)
        draw_calls = []
        if count:
            vdata = np.ascontiguousarray(plan.vertices).tobytes()
            idata = plan.indices.tobytes()
            if self._vb is None or self._vb.size() < len(vdata):
                self._vb = self._new_buffer(
                    rhi, QRhiBuffer.UsageFlag.VertexBuffer, len(vdata) * 3 // 2
                )
            if self._ib is None or self._ib.size() < len(idata):
                self._ib = self._new_buffer(
                    rhi, QRhiBuffer.UsageFlag.IndexBuffer, len(idata) * 3 // 2
                )
            batch.updateDynamicBuffer(self._vb, 0, vdata)
            batch.updateDynamicBuffer(self._ib, 0, idata)
            while len(self._draw_ubs) < count:
                self._draw_ubs.append(
                    self._new_buffer(
                        rhi, QRhiBuffer.UsageFlag.UniformBuffer, DRAW_BLOCK_BYTES
                    )
                )
            used_ids = {
                id(t) for run in plan.runs for t in run.textures if t is not None
            }
            joint_i = 0
            for i, run in enumerate(plan.runs):
                batch.updateDynamicBuffer(self._draw_ubs[i], 0, run.draw_block)
                joint_ub = self._dummy_joints
                if run.joints is not None:
                    if joint_i >= len(self._joint_ubs):
                        self._joint_ubs.append(
                            self._new_buffer(
                                rhi,
                                QRhiBuffer.UsageFlag.UniformBuffer,
                                JOINT_BLOCK_BYTES,
                            )
                        )
                    joint_ub = self._joint_ubs[joint_i]
                    batch.updateDynamicBuffer(joint_ub, 0, run.joints)
                    joint_i += 1
                textures = [
                    self._texture_for(rhi, batch, tex, srgb)
                    for tex, srgb in zip(run.textures, _SRGB_SLOTS, strict=True)
                ]
                draw_calls.append(
                    (run, self._make_srb(rhi, self._draw_ubs[i], joint_ub, textures))
                )
            self._evict_textures(used_ids)
        self.timings["upload_s"] = time.perf_counter() - started
        cb.beginPass(
            self._rt,
            QColor.fromRgbF(0.0, 0.0, 0.0, 0.0),
            QRhiDepthStencilClearValue(1.0, 0),
            batch,
        )
        cb.setViewport(QRhiViewport(0, 0, width, height))
        for run, srb in draw_calls:
            cb.setGraphicsPipeline(self._pipeline(rhi, run.cull_back, run.blend))
            cb.setShaderResources(srb)
            assert self._vb is not None and self._ib is not None
            cb.setVertexInput(
                0, [(self._vb, 0)], self._ib, 0, cb.IndexFormat.IndexUInt32
            )
            cb.drawIndexed(run.count, 1, run.first, 0, 0)
        cb.endPass()
        assert self._color_tex is not None and self._depth_tex is not None
        job.color, job.depth = QRhiReadbackResult(), QRhiReadbackResult()
        update = rhi.nextResourceUpdateBatch()
        update.readBackTexture(QRhiReadbackDescription(self._color_tex), job.color)
        update.readBackTexture(QRhiReadbackDescription(self._depth_tex), job.depth)
        cb.resourceUpdate(update)
        self._frame_srbs = [
            srb for _, srb in draw_calls
        ]  # détruites à l'image suivante (la grab est synchrone)


class QrhiRenderer:
    """Un moteur de rendu : le widget caché + la conversion des relectures en `Frame`."""

    def __init__(self) -> None:
        ensure_application()
        self.widget = _Widget()
        self.last_timings: dict[str, float] = {}

    @property
    def backend_name(self) -> str:
        return self.widget.backend_name

    def render(
        self, scene: Scene, t: float, size: tuple[int, int], samples: int
    ) -> Frame:
        width, height = size
        sw, sh = width * samples, height * samples
        w = self.widget
        if w.max_texture and (sw > w.max_texture or sh > w.max_texture):
            raise RenderBackendUnavailable(
                f"image sur-échantillonnée {sw}x{sh} : dépasse la taille de texture de ce GPU ({w.max_texture})."
            )
        t0 = time.perf_counter()
        plan = plan_frame(scene, t, (sw, sh))
        t1 = time.perf_counter()
        job = _Job(plan)
        w.job, w.failure = job, ""
        image = w.grabFramebuffer()
        w.job = None
        w.release_frame()
        t2 = time.perf_counter()
        if job.error:
            raise RenderBackendUnavailable(f"échec du rendu GPU : {job.error}")
        if w.failure or job.color is None or job.depth is None or image.isNull():
            raise RenderBackendUnavailable(
                f"échec du rendu GPU : {w.failure or 'aucune image produite (rendu non exécuté)'}"
            )
        rgba = self._decode(job.color, sw, sh, 4)
        depth = self._decode(job.depth, sw, sh, 1)
        t3 = time.perf_counter()
        if w.y_up:
            rgba, depth = rgba[::-1], depth[::-1]
        acc_rgb = np.ascontiguousarray(rgba[..., :3])
        acc_a = np.ascontiguousarray(rgba[..., 3])
        depth = np.where(depth > 0.0, depth, np.inf).astype(np.float32)
        rgb, a, d = reduce_samples(acc_rgb, acc_a, depth, samples)
        frame = finish_frame(rgb, a, d, scene.background, size)
        self.last_timings = {
            "plan_s": t1 - t0, "gpu_s": t2 - t1, "decode_s": t3 - t2, "finish_s": time.perf_counter() - t3,
            "upload_s": w.timings.get("upload_s", 0.0), "runs": float(len(plan.runs)),
            "skinned_draws": float(plan.skinned_on_gpu),
        }  # fmt: skip
        return frame

    @staticmethod
    def _decode(
        result: QRhiReadbackResult, width: int, height: int, channels: int
    ) -> Array:
        size = result.pixelSize
        data = bytes(result.data.data())
        if (
            size.width() != width
            or size.height() != height
            or len(data) < width * height * channels * 4
        ):
            raise RenderBackendUnavailable(
                f"relecture GPU inattendue ({size.width()}x{size.height()}, {len(data)} octets) "
                f"pour {width}x{height}."
            )
        arr = np.frombuffer(data, dtype=np.float32, count=width * height * channels)
        return (
            arr.reshape(height, width, channels)
            if channels > 1
            else arr.reshape(height, width)
        )

    def close(self) -> None:
        widget, self.widget = self.widget, None  # type: ignore[assignment]
        if widget is None:
            return
        widget.shutdown()
        widget.close()
        widget.deleteLater()


_renderer: QrhiRenderer | None = None


def get_renderer() -> QrhiRenderer:
    global _renderer
    if _renderer is None:
        _renderer = QrhiRenderer()
        import atexit

        atexit.register(shutdown)
    return _renderer


def shutdown() -> None:
    """Détruit le widget et ses ressources GPU (appelé à la sortie du processus ; réentrant)."""
    global _renderer
    renderer, _renderer = _renderer, None
    if renderer is None:
        return
    from PySide6.QtCore import QCoreApplication, QEvent

    renderer.close()
    app = QCoreApplication.instance()
    if app is not None:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


def render_qrhi(scene: Scene, t: float, size: tuple[int, int], samples: int) -> Frame:
    """Rendu qrhi SANS les gardes (fil principal, sonde) : voir `kut3d.gpu.backend.render_lazy`."""
    return get_renderer().render(scene, t, size, samples)
