"""Voie QRhi hors fenêtre (Metal sur macOS) : rendu dans une QRhiTexture, relecture via readBackTexture.

Les shaders GLSL 440 de ``shaders/`` sont convertis en ``.qsb`` à l'exécution avec ``pyside6-qsb``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np

import cube_math as cm
from texture import checker_texture

HERE = Path(__file__).resolve().parent


def bake_shaders(out_dir: Path) -> dict[str, Path]:
    """Compile cube.vert / cube.frag en .qsb (GLSL 150, MSL 1.2, HLSL 5.0) ; retourne les chemins."""
    out_dir.mkdir(parents=True, exist_ok=True)
    qsb = Path(sys.executable).parent / "pyside6-qsb"
    exe = str(qsb) if qsb.exists() else "pyside6-qsb"
    paths = {}
    for name in ("cube.vert", "cube.frag"):
        dst = out_dir / (name + ".qsb")
        subprocess.run(
            [
                exe,
                "--glsl",
                "150,120,100 es",
                "--hlsl",
                "50",
                "--msl",
                "12",
                "-o",
                str(dst),
                str(HERE / "shaders" / name),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        paths[name] = dst
    return paths


def available_names() -> list[str]:
    """Noms QRhi* exposés par PySide6 (pour le rapport)."""
    from PySide6 import QtGui

    return sorted(n for n in dir(QtGui) if n.startswith("QRhi") or n == "QShader")


def probe_offscreen_frame() -> str:
    """Tente l'API QRhi « pure » hors écran et décrit ce que PySide6 6.11.2 permet réellement.

    ``QRhi.beginOffscreenFrame(cb, flags)`` attend un ``QRhiCommandBuffer`` en argument, mais la classe n'est pas
    instanciable en Python et la fonction ne renvoie que ``FrameOpResult`` : le tampon de commandes créé par Qt
    n'est jamais rendu à l'appelant, donc aucune commande de rendu ne peut être enregistrée.
    """
    from PySide6.QtGui import QRhi, QRhiCommandBuffer, QRhiMetalInitParams

    rhi = QRhi.create(QRhi.Implementation.Metal, QRhiMetalInitParams())
    if rhi is None:
        return "QRhi.create(Metal) a retourné None"
    lines = [f"QRhi.create(Metal) OK, backend={rhi.backendName()}"]
    try:
        QRhiCommandBuffer()
    except Exception as error:  # noqa: BLE001 - on consigne le message réel
        lines.append(f"QRhiCommandBuffer() : {error!r}")
    lines.append(
        f"beginOffscreenFrame(None) -> {rhi.beginOffscreenFrame(None)} (aucun cb renvoyé)"
    )
    return " | ".join(lines)


def make_widget_class() -> type:
    """Construit le QRhiWidget (importé tard : QtWidgets coûte cher) qui dessine le cube."""
    from PySide6.QtCore import QByteArray
    from PySide6.QtGui import (
        QColor,
        QImage,
        QRhiBuffer,
        QRhiDepthStencilClearValue,
        QRhiGraphicsPipeline,
        QRhiSampler,
        QRhiShaderResourceBinding,
        QRhiShaderStage,
        QRhiTexture,
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

    class CubeWidget(QRhiWidget):
        """Widget jamais affiché : ``grabFramebuffer()`` déclenche initialize/render sans fenêtre visible."""

        def __init__(self) -> None:
            super().__init__()
            self.setApi(QRhiWidget.Api.Metal)
            self.angle = 0.0
            self.backend = ""
            self.error = ""
            self._ready = False

        def initialize(self, cb) -> None:  # noqa: N802 - API Qt
            try:
                self._init(cb)
            except Exception as error:  # noqa: BLE001
                self.error = repr(error)

        def _init(self, cb) -> None:
            rhi = self.rhi()
            self.backend = rhi.backendName()
            shaders = bake_shaders(HERE / "out" / "shaders")
            vs = QShader.fromSerialized(QByteArray(shaders["cube.vert"].read_bytes()))
            fs = QShader.fromSerialized(QByteArray(shaders["cube.frag"].read_bytes()))
            pos, uv, nrm, idx = cm.cube_geometry()
            inter = np.ascontiguousarray(
                np.concatenate([pos, uv, nrm], axis=1), np.float32
            )
            self.vbuf = rhi.newBuffer(
                QRhiBuffer.Type.Immutable,
                QRhiBuffer.UsageFlag.VertexBuffer,
                inter.nbytes,
            )
            self.vbuf.create()
            self.ibuf = rhi.newBuffer(
                QRhiBuffer.Type.Immutable, QRhiBuffer.UsageFlag.IndexBuffer, idx.nbytes
            )
            self.ibuf.create()
            self.ubuf = rhi.newBuffer(
                QRhiBuffer.Type.Dynamic, QRhiBuffer.UsageFlag.UniformBuffer, 144
            )
            self.ubuf.create()
            tex = np.ascontiguousarray(checker_texture())
            from PySide6.QtCore import QSize

            self.tex = rhi.newTexture(
                QRhiTexture.Format.RGBA8, QSize(tex.shape[1], tex.shape[0])
            )
            self.tex.create()
            self.sampler = rhi.newSampler(
                QRhiSampler.Filter.Nearest,
                QRhiSampler.Filter.Nearest,
                QRhiSampler.Filter.None_,
                QRhiSampler.AddressMode.ClampToEdge,
                QRhiSampler.AddressMode.ClampToEdge,
            )
            self.sampler.create()
            self.srb = rhi.newShaderResourceBindings()
            both = (
                QRhiShaderResourceBinding.StageFlag.VertexStage
                | QRhiShaderResourceBinding.StageFlag.FragmentStage
            )
            self.srb.setBindings(
                [
                    QRhiShaderResourceBinding.uniformBuffer(0, both, self.ubuf),
                    QRhiShaderResourceBinding.sampledTexture(
                        1,
                        QRhiShaderResourceBinding.StageFlag.FragmentStage,
                        self.tex,
                        self.sampler,
                    ),
                ]
            )
            self.srb.create()
            pipe = rhi.newGraphicsPipeline()
            pipe.setShaderStages(
                [
                    QRhiShaderStage(QRhiShaderStage.Type.Vertex, vs),
                    QRhiShaderStage(QRhiShaderStage.Type.Fragment, fs),
                ]
            )
            layout = QRhiVertexInputLayout()
            layout.setBindings([QRhiVertexInputBinding(32)])
            layout.setAttributes(
                [
                    QRhiVertexInputAttribute(
                        0, 0, QRhiVertexInputAttribute.Format.Float3, 0
                    ),
                    QRhiVertexInputAttribute(
                        0, 1, QRhiVertexInputAttribute.Format.Float2, 12
                    ),
                    QRhiVertexInputAttribute(
                        0, 2, QRhiVertexInputAttribute.Format.Float3, 20
                    ),
                ]
            )
            pipe.setVertexInputLayout(layout)
            pipe.setTopology(QRhiGraphicsPipeline.Topology.Triangles)
            pipe.setCullMode(QRhiGraphicsPipeline.CullMode.Back)
            pipe.setFrontFace(QRhiGraphicsPipeline.FrontFace.CCW)
            pipe.setDepthTest(True)
            pipe.setDepthWrite(True)
            pipe.setShaderResourceBindings(self.srb)
            pipe.setRenderPassDescriptor(self.renderTarget().renderPassDescriptor())
            if not pipe.create():
                raise RuntimeError("QRhiGraphicsPipeline.create() a échoué")
            self.pipe = pipe
            batch = rhi.nextResourceUpdateBatch()
            batch.uploadStaticBuffer(self.vbuf, inter.tobytes())
            batch.uploadStaticBuffer(self.ibuf, idx.tobytes())
            img = QImage(
                tex.data,
                tex.shape[1],
                tex.shape[0],
                tex.shape[1] * 4,
                QImage.Format.Format_RGBA8888,
            )
            sub = QRhiTextureSubresourceUploadDescription(img.copy())
            batch.uploadTexture(
                self.tex,
                QRhiTextureUploadDescription(QRhiTextureUploadEntry(0, 0, sub)),
            )
            cb.resourceUpdate(batch)
            self._ready = True

        def render(self, cb) -> None:  # noqa: A003 - API Qt
            if not self._ready:
                return
            rhi = self.rhi()
            size = self.renderTarget().pixelSize()
            model = cm.model_matrix(self.angle)
            corr = np.array(rhi.clipSpaceCorrMatrix().data(), np.float32).reshape(
                4, 4, order="F"
            )
            mvp = (
                corr
                @ cm.perspective_matrix(size.width() / size.height())
                @ cm.view_matrix()
                @ model
            )
            light = np.array(cm.LIGHT_DIR, np.float32)
            light /= np.linalg.norm(light)
            data = (
                np.ascontiguousarray(mvp.T, np.float32).tobytes()
                + np.ascontiguousarray(model.T, np.float32).tobytes()
                + np.array([*light, cm.AMBIENT], np.float32).tobytes()
            )
            batch = rhi.nextResourceUpdateBatch()
            batch.updateDynamicBuffer(self.ubuf, 0, len(data), data)
            cb.beginPass(
                self.renderTarget(),
                QColor(*cm.BACKGROUND),
                QRhiDepthStencilClearValue(1.0, 0),
                batch,
            )
            cb.setGraphicsPipeline(self.pipe)
            cb.setViewport(QRhiViewport(0, 0, size.width(), size.height()))
            cb.setShaderResources()
            cb.setVertexInput(
                0, [(self.vbuf, 0)], self.ibuf, 0, cb.IndexFormat.IndexUInt32
            )
            cb.drawIndexed(36)
            cb.endPass()

    return CubeWidget


class QrhiRenderer:
    """QRhi (Metal) via un QRhiWidget jamais affiché ; relecture par ``grabFramebuffer``."""

    def __init__(self, width: int, height: int) -> None:
        self.widget = make_widget_class()()
        self.widget.setFixedColorBufferSize(width, height)
        self.widget.resize(width, height)
        self.width, self.height = width, height

    @property
    def backend(self) -> str:
        return str(self.widget.backend)

    def render(self, angle_deg: float) -> np.ndarray:
        from PySide6.QtGui import QImage

        self.widget.angle = angle_deg
        img = self.widget.grabFramebuffer()
        if self.widget.error:
            raise RuntimeError(self.widget.error)
        if img.isNull():
            raise RuntimeError("grabFramebuffer() a retourné une image nulle")
        img = img.convertToFormat(QImage.Format.Format_RGBA8888)
        arr = np.frombuffer(img.constBits(), np.uint8).reshape(
            img.height(), img.bytesPerLine() // 4, 4
        )
        return arr[:, : img.width()].copy()


def render_cube(width: int, height: int, angle_deg: float) -> np.ndarray:
    """Rend une image via QRhi (Metal)."""
    return QrhiRenderer(width, height).render(angle_deg)


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])
    print(probe_offscreen_frame())
    img = render_cube(64, 64, 30)
    print(img.shape, [w.isVisible() for w in app.topLevelWidgets()])
