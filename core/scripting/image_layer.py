"""Lecture bornée d'une image fixe pour le verbe de script ``add_image_layer`` (ADR-0005 : lecture seule, dossier du script).

Seul l'en-tête est lu (``QImageReader.size()``) : ni décodage complet, ni allocation proportionnelle à l'image, avant d'avoir
accepté les dimensions. Le format vient du **contenu**, jamais de l'extension, et les deux doivent concorder."""

from __future__ import annotations

from pathlib import Path

from ..ken_burns import photo_size
from ..template_slots import PHOTO_EXTENSIONS

MAX_IMAGE_SIDE = 8192
"""Pixels : le plus grand côté d'un calque graphique (``GraphicOverlay`` borne sa largeur et sa hauteur à cette valeur)."""
MAX_IMAGE_PIXELS = 64_000_000
"""Pixels au total (≈ 256 Mo décodée en RGBA) : au-delà, le moteur de rendu rastérise une image démesurée à chaque calque."""
MAX_IMAGE_BYTES = 256 * 1024 * 1024
"""Octets du fichier : une image plus lourde n'est pas un calque, c'est une erreur ou une attaque."""

# Format détecté par Qt dans le contenu → extensions acceptées pour ce format.
_FORMAT_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "png": (".png",), "jpeg": (".jpg", ".jpeg"), "jpg": (".jpg", ".jpeg"), "webp": (".webp",), "bmp": (".bmp",),
    "tiff": (".tif", ".tiff"), "tif": (".tif", ".tiff"),
}


def read_image_size(path: Path) -> tuple[int, int]:
    """``(largeur, hauteur)`` de l'image fixe ``path``, ou ``ValueError`` (message clair, sans recopier de contenu)."""
    from PySide6.QtGui import QImageReader

    name = path.name if len(path.name) <= 80 else path.name[:77] + "…"
    extension = path.suffix.lower()
    if extension not in PHOTO_EXTENSIONS:
        raise ValueError(f"« {name} » n'est pas une image acceptée (extensions : {', '.join(PHOTO_EXTENSIONS)}).")
    try:
        size_bytes = path.stat().st_size
    except OSError as error:
        raise ValueError(f"Image illisible : « {name} » ({error.strerror or 'erreur'}).") from error
    if size_bytes <= 0:
        raise ValueError(f"L'image « {name} » est vide.")
    if size_bytes > MAX_IMAGE_BYTES:
        raise ValueError(f"L'image « {name} » est trop lourde ({size_bytes // (1024 * 1024)} Mo ; au plus {MAX_IMAGE_BYTES // (1024 * 1024)} Mo).")
    reader = QImageReader(str(path))
    reader.setAutoTransform(False)
    reader.setDecideFormatFromContent(True)             # le contenu décide ; l'extension est ensuite comparée
    detected = bytes(reader.format().data()).decode("ascii", "ignore").lower()
    size = reader.size()
    if not detected or not size.isValid():
        raise ValueError(f"« {name} » n'est pas une image lisible (fichier corrompu ou format inconnu).")
    if extension not in _FORMAT_EXTENSIONS.get(detected, ()):
        raise ValueError(f"« {name} » : l'extension {extension} ne correspond pas au contenu (format {detected}).")
    width, height = int(size.width()), int(size.height())
    if width <= 0 or height <= 0:
        raise ValueError(f"« {name} » : dimensions invalides ({width}x{height}).")
    if max(width, height) > MAX_IMAGE_SIDE or width * height > MAX_IMAGE_PIXELS:
        raise ValueError(
            f"L'image « {name} » est trop grande ({width}x{height} ; au plus {MAX_IMAGE_SIDE} pixels de côté et "
            f"{MAX_IMAGE_PIXELS // 1_000_000} millions de pixels)."
        )
    return width, height


def fitted_size(image_width: int, image_height: int, frame_width: int, frame_height: int) -> tuple[int, int]:
    """Taille du calque : l'image tient dans le cadre du projet sans être agrandie (comme l'import par l'interface, sans « remplir »)."""
    return photo_size(image_width, image_height, frame_width, frame_height, fill=False)
