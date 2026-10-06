"""Galerie des templates « Night » : noms et textes traduits, vignettes rendues par le rastériseur des calques.

Une vignette est le template lui-même, construit en petit et dessiné à son instant d'affiche par
``MographRenderer`` (les emplacements vides sont des cartes, les titres sont déjà entrés) : ce qu'on choisit est ce
qu'on obtient. Les vignettes sont gardées en mémoire par langue et par taille.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter

from core.project_templates import TEMPLATES, TEXT_DEFAULTS, create_from_template, get_template
from ui import i18n

_THUMBS: dict[tuple[str, str, int], QImage] = {}


def template_texts() -> dict[str, str]:
    """Textes affichés par les templates, dans la langue de l'interface."""
    return {key: i18n.translate(f"template.text.{key}") for key in TEXT_DEFAULTS}


def template_thumbnail(template_id: str, height: int) -> QImage:
    """Image 9:16 du template à son instant d'affiche, sur fond noir."""
    key = (template_id, i18n.current_language(), int(height))
    if key in _THUMBS:
        return _THUMBS[key]
    from core.mograph_raster import MographRenderer, scene_for_plan
    from core.render_plan import build_render_plan

    template = get_template(template_id)
    project = create_from_template(template_id, "vertical", 30, texts=template_texts())
    plan = build_render_plan(project, window=(template.poster, template.poster + 1e-3))
    width = max(2, round(height * 9 / 16))
    scene = scene_for_plan(plan)
    layers = MographRenderer(scene, width, int(height), fps=30.0, quality="draft").render(scene.top_level(),
                                                                                          template.poster)
    image = QImage(width, int(height), QImage.Format_RGB32)
    image.fill(Qt.black)
    painter = QPainter(image)
    painter.drawImage(0, 0, layers)
    painter.end()
    _THUMBS[key] = image
    return image


def template_entries(thumb_height: int) -> list[tuple[str, str, str, QImage]]:
    """``(identifiant, nom, description, vignette)`` de chaque template, pour l'assistant."""
    return [(template.id, i18n.translate(f"template.{template.id}.name"),
             i18n.translate(f"template.{template.id}.desc"), template_thumbnail(template.id, thumb_height))
            for template in TEMPLATES]


__all__ = ["template_entries", "template_texts", "template_thumbnail"]
