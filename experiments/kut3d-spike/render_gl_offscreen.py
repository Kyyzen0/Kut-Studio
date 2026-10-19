"""Voie GL hors écran : QOffscreenSurface + QOpenGLContext + QOpenGLFramebufferObject (profil Core)."""

from __future__ import annotations

import os
import sys

import numpy as np

import cube_math as cm
from texture import checker_texture

VERT = """#version 330 core
layout(location=0) in vec3 aPos;
layout(location=1) in vec2 aUv;
layout(location=2) in vec3 aNrm;
uniform mat4 uMvp;
uniform mat4 uNrm;
out vec2 vUv;
out float vShade;
uniform vec4 uLightAmbient; // xyz = vers la lumière, w = ambiant
void main() {
    gl_Position = uMvp * vec4(aPos, 1.0);
    vUv = aUv;
    float l = clamp(dot(normalize(mat3(uNrm) * aNrm), uLightAmbient.xyz), 0.0, 1.0);
    vShade = min(uLightAmbient.w + (1.0 - uLightAmbient.w) * l, 1.0);
}
"""
FRAG = """#version 330 core
in vec2 vUv;
in float vShade;
uniform sampler2D uTex;
out vec4 fragColor;
void main() {
    vec4 c = texture(uTex, vUv);
    fragColor = vec4(c.rgb * vShade, 1.0);
}
"""

GL_TRIANGLES, GL_UNSIGNED_INT, GL_FLOAT = 0x0004, 0x1405, 0x1406
GL_ARRAY_BUFFER, GL_ELEMENT_ARRAY_BUFFER, GL_STATIC_DRAW = 0x8892, 0x8893, 0x88E4
GL_DEPTH_TEST, GL_CULL_FACE, GL_BACK = 0x0B71, 0x0B44, 0x0405
GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT = 0x4000, 0x0100
GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER = 0x0DE1, 0x2801, 0x2800
GL_NEAREST, GL_RGBA, GL_UNSIGNED_BYTE, GL_TEXTURE0 = 0x2600, 0x1908, 0x1401, 0x84C0


class GlRenderer:
    """Contexte GL Core hors écran, réutilisable d'une image à l'autre."""

    def __init__(self, width: int, height: int) -> None:
        from PySide6.QtGui import (
            QOffscreenSurface,
            QOpenGLContext,
            QOpenGLExtraFunctions,
            QSurfaceFormat,
        )
        from PySide6.QtOpenGL import (
            QOpenGLFramebufferObject,
            QOpenGLFramebufferObjectFormat,
            QOpenGLShaderProgram,
        )
        from PySide6.QtOpenGL import QOpenGLShader

        fmt = QSurfaceFormat()
        fmt.setVersion(4, 1)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        fmt.setDepthBufferSize(24)
        self.surface = QOffscreenSurface()
        self.surface.setFormat(fmt)
        self.surface.create()
        self.ctx = QOpenGLContext()
        self.ctx.setFormat(fmt)
        if not self.ctx.create():
            raise RuntimeError("QOpenGLContext.create() a échoué")
        if not self.ctx.makeCurrent(self.surface):
            raise RuntimeError("makeCurrent(QOffscreenSurface) a échoué")
        self.backend = (
            "GL "
            + str(self.ctx.format().majorVersion())
            + "."
            + str(self.ctx.format().minorVersion())
        )
        self.f = QOpenGLExtraFunctions(self.ctx)
        self.f.initializeOpenGLFunctions()
        self.width, self.height = width, height
        ffmt = QOpenGLFramebufferObjectFormat()
        ffmt.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
        self.fbo = QOpenGLFramebufferObject(width, height, ffmt)
        if not self.fbo.isValid():
            raise RuntimeError("QOpenGLFramebufferObject invalide")
        self.prog = QOpenGLShaderProgram()
        if not self.prog.addShaderFromSourceCode(
            QOpenGLShader.ShaderTypeBit.Vertex, VERT
        ):
            raise RuntimeError("vertex: " + self.prog.log())
        if not self.prog.addShaderFromSourceCode(
            QOpenGLShader.ShaderTypeBit.Fragment, FRAG
        ):
            raise RuntimeError("fragment: " + self.prog.log())
        if not self.prog.link():
            raise RuntimeError("link: " + self.prog.log())
        self._upload_geometry()

    def _upload_geometry(self) -> None:
        # QOpenGLExtraFunctions n'expose pas glBufferData en PySide6 6.11.2 : on passe par QOpenGLBuffer / VAO / Texture.
        from PySide6.QtGui import QImage
        from PySide6.QtOpenGL import (
            QOpenGLBuffer,
            QOpenGLTexture,
            QOpenGLVertexArrayObject,
        )

        pos, uv, nrm, idx = cm.cube_geometry()
        inter = np.concatenate([pos, uv, nrm], axis=1)  # 8 floats / sommet
        # glDrawElements(..., indices) ne dessine rien via PySide6 6.11.2 (le pointeur d'offset ctypes n'est pas
        # transmis comme valeur ; un int est refusé) : on déplie donc les 36 indices et on utilise glDrawArrays.
        inter = np.ascontiguousarray(inter[idx], np.float32)
        self.vertex_count = len(idx)
        self.vao = QOpenGLVertexArrayObject()
        self.vao.create()
        self.vao.bind()
        self.vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.vbo.create()
        self.vbo.bind()
        self.vbo.allocate(inter.tobytes(), inter.nbytes)
        self.prog.bind()
        for loc, n, off in ((0, 3, 0), (1, 2, 12), (2, 3, 20)):
            self.prog.enableAttributeArray(loc)
            self.prog.setAttributeBuffer(loc, GL_FLOAT, off, n, 32)
        self.vao.release()
        tex = np.ascontiguousarray(checker_texture())
        qtex = QImage(
            tex.data,
            tex.shape[1],
            tex.shape[0],
            tex.shape[1] * 4,
            QImage.Format.Format_RGBA8888,
        )
        self.qtex = QOpenGLTexture(qtex.copy())
        self.qtex.setMinMagFilters(
            QOpenGLTexture.Filter.Nearest, QOpenGLTexture.Filter.Nearest
        )
        self.qtex.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)

    def render(self, angle_deg: float) -> np.ndarray:
        from PySide6.QtGui import QMatrix4x4, QVector4D

        f = self.f
        model = cm.model_matrix(angle_deg)
        mvp = (
            cm.perspective_matrix(self.width / self.height) @ cm.view_matrix() @ model
        ).astype(np.float32)
        self.fbo.bind()
        f.glViewport(0, 0, self.width, self.height)
        bg = [c / 255 for c in cm.BACKGROUND]
        f.glClearColor(*bg)
        f.glEnable(GL_DEPTH_TEST)
        f.glEnable(GL_CULL_FACE)
        f.glCullFace(GL_BACK)
        f.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        self.prog.bind()
        self.prog.setUniformValue("uMvp", QMatrix4x4(mvp.flatten().tolist()))
        # QMatrix3x3 (liste) donne un résultat faux en PySide6 6.11.2 : on passe la matrice modèle 4x4.
        self.prog.setUniformValue("uNrm", QMatrix4x4(model.flatten().tolist()))
        light = np.array(cm.LIGHT_DIR, np.float32)
        light /= np.linalg.norm(light)
        # setUniformValue(nom, float) ne fixe pas la bonne valeur en PySide6 6.11.2 (ambiant resté à 0) : vec4.
        self.prog.setUniformValue(
            "uLightAmbient", QVector4D(*light.tolist(), cm.AMBIENT)
        )
        self.qtex.bind(0)
        self.prog.setUniformValue("uTex", 0)
        self.vao.bind()
        f.glDrawArrays(GL_TRIANGLES, 0, self.vertex_count)
        self.vao.release()
        f.glFinish()
        qimg = self.fbo.toImage(True)
        self.fbo.release()
        from PySide6.QtGui import QImage

        qimg = qimg.convertToFormat(QImage.Format.Format_RGBA8888)
        ptr = qimg.constBits()
        arr = np.frombuffer(ptr, np.uint8).reshape(
            qimg.height(), qimg.bytesPerLine() // 4, 4
        )
        return arr[:, : qimg.width()].copy()

    def close(self) -> None:
        """Libère les ressources GL avec le contexte courant (évite les avertissements Qt à la sortie)."""
        self.ctx.makeCurrent(self.surface)
        self.qtex.destroy()
        self.vao.destroy()
        self.vbo.destroy()
        del self.fbo
        self.ctx.doneCurrent()


def render_cube(width: int, height: int, angle_deg: float) -> np.ndarray:
    """Rend une image GL hors écran (crée un contexte à chaque appel ; voir run_spike pour la mesure à chaud)."""
    r = GlRenderer(width, height)
    try:
        return r.render(angle_deg)
    finally:
        r.close()


def make_app() -> object:
    """QGuiApplication (jamais de fenêtre) ; la plateforme vient de QT_QPA_PLATFORM si définie."""
    from PySide6.QtGui import QGuiApplication

    return QGuiApplication.instance() or QGuiApplication(sys.argv[:1])


if __name__ == "__main__":
    app = make_app()
    print(os.environ.get("QT_QPA_PLATFORM"), render_cube(64, 64, 30).shape)
