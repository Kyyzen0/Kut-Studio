"""Comptabilité des ressources de ``RhiExecutor``, exécutée sur le QRhi *Null* (aucun GPU requis).

Le backend Null fait tourner le vrai code Python de l'exécuteur avec un faux tampon de commandes : ces tests
ne vérifient pas le rendu, mais ce que l'exécuteur crée, garde et libère. Trois défauts constatés :

- le cache des liaisons de ressources (SRB) était indexé par ``id()`` : Python réutilise l'identifiant d'un
  objet libéré, donc une texture neuve (un masque animé en crée une par image) retombait sur la liaison
  construite pour une texture DÉJÀ DÉTRUITE ; et il grossissait sans borne (303 liaisons après 300 images) ;
- le descripteur de passe modèle des pipelines appartenait à la première cible, détruit avec elle lors d'un
  redimensionnement, puis réutilisé pour créer de nouveaux pipelines.
"""

from __future__ import annotations

import gpu_harness
import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import (
    QImage,
    QRhi,
    QRhiColorAttachment,
    QRhiNullInitParams,
    QRhiTexture,
    QRhiTextureRenderTargetDescription,
)

from core.gpu_cache import GpuTextureCache
from core.gpu_composite import CompositeFrame, CompositeLayer, VideoSource, plan_frame, present_uniforms
from core.gpu_effects import program_for
from ui.gpu_preview import SRB_CACHE_SIZE, RhiExecutor


class _CommandBuffer:
    """Faux ``QRhiCommandBuffer`` : libère le lot de mises à jour comme le ferait ``beginPass``."""

    def beginPass(self, _target, _color, _depth, update=None):
        if update is not None:
            update.release()

    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


class _Harness:
    def __init__(self):
        self.rhi = QRhi.create(QRhi.Implementation.Null, QRhiNullInitParams())
        self.executor = RhiExecutor(self.rhi)
        target = self.rhi.newTexture(QRhiTexture.Format.RGBA8, QSize(64, 64), 1, QRhiTexture.Flag.RenderTarget)
        assert target.create()
        self.window_target = self.rhi.newTextureRenderTarget(QRhiTextureRenderTargetDescription(QRhiColorAttachment(target)))
        self._texture = target
        self._rpd = self.window_target.newCompatibleRenderPassDescriptor()
        self.window_target.setRenderPassDescriptor(self._rpd)
        assert self.window_target.create()
        self.executor.ensure_present_pipeline(self._rpd)
        batch = self.rhi.nextResourceUpdateBatch()
        self.executor.upload_source(batch, "main", gpu_harness.make_frame(gpu_harness.test_pattern(64, 36), "nv12"))

    def draw(self, width=128, height=72, matte="", mattes=None, batch=None):
        layer = CompositeLayer(source="main", matrix=(1, 0, 0, 1, 0, 0), fit=(0, 0, width, height), matte=matte,
                               program=program_for(()))
        plan = plan_frame(CompositeFrame(width, height, 1.0, (layer,),
                                         sources=(VideoSource("main", "nv12", 64, 36),)))
        batch = batch or self.rhi.nextResourceUpdateBatch()   # le lot d'envoi du masque est consommé par la passe
        self.executor.run(_CommandBuffer(), plan, batch, mattes or {}, self.window_target,
                          present_uniforms((64.0, 64.0), (0.0, 0.0, 64.0, 64.0), (0.0, 0.0, 0.0)), (64.0, 64.0))


@pytest.fixture
def harness(qapp):
    try:
        return _Harness()
    except Exception as error:  # noqa: BLE001 - un Qt sans backend Null ne doit pas faire échouer la suite
        pytest.skip(f"QRhi Null indisponible : {error}")


def test_the_binding_cache_stays_bounded_with_an_animated_mask(harness):
    """Un masque animé crée une texture par image : le cache de liaisons ne doit pas grossir sans fin."""
    cache = GpuTextureCache(256 * 2**20, release=lambda texture: texture.destroy())
    for index in range(300):
        batch = harness.rhi.nextResourceUpdateBatch()
        image = QImage(32, 32, QImage.Format.Format_RGBA8888)
        image.fill(index)
        texture, _hit = cache.acquire(("matte", f"clip:{index}"), 32 * 32 * 4,
                                      lambda image=image, batch=batch: harness.executor.new_matte_texture(batch, image))
        harness.draw(matte="m", mattes={"m": texture}, batch=batch)
    assert len(harness.executor._srbs) <= SRB_CACHE_SIZE, len(harness.executor._srbs)


def test_a_binding_is_never_handed_back_for_a_different_texture(harness):
    """Avant : l'``id()`` d'une texture détruite était réutilisé par la suivante, qui recevait l'ancienne liaison."""
    executor = harness.executor
    uniforms = executor._ubuf(0)
    handed_out = []        # les objets eux-mêmes : suivre leur id() retomberait dans le piège testé
    for _ in range(200):
        texture = harness.rhi.newTexture(QRhiTexture.Format.RGBA8, QSize(8, 8))
        assert texture.create()
        binding = executor._srb_for(uniforms, (texture, texture, texture))
        assert not any(binding is earlier for earlier in handed_out), \
            "une liaison déjà construite pour une autre texture a été renvoyée"
        handed_out.append(binding)
        texture.destroy()
        del texture


def test_the_template_render_pass_is_not_owned_by_a_target(harness):
    """Le modèle des pipelines survit à la destruction d'une cible (redimensionnement du moniteur).

    Avant, le modèle d'un format ÉTAIT le descripteur de la première cible : détruit avec elle au
    redimensionnement, il servait pourtant à créer les pipelines suivants.
    """
    harness.draw(128, 72)
    ever_owned = [entry[3] for entry in harness.executor._targets.values()]      # objets (références fortes)
    harness.draw(256, 144)                                    # nouvelle taille : l'ancienne cible est libérée
    ever_owned += [entry[3] for entry in harness.executor._targets.values()]
    assert harness.executor._template_rpds
    for template in harness.executor._template_rpds.values():
        assert not any(template is owned for owned in ever_owned), "le modèle appartient à une cible"


def test_release_frees_everything_the_executor_tracks(harness):
    harness.draw(128, 72)
    harness.executor.release()
    executor = harness.executor
    assert executor.texture_bytes == 0
    assert not (executor._targets or executor._sources or executor._srbs or executor._pinned_srbs
                or executor._template_rpds or executor._pipelines)
