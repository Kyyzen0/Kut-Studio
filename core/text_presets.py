"""Modèles de sous-titres / titres prêts à l'emploi (tâche 24).

Ce module complète :mod:`core.text_style` avec une bibliothèque de
*modèles* — un modèle encapsule un :class:`~core.text_style.TextStyle`
et un texte par défaut à proposer à la création d'un clip. Les six
modèles livrés couvrent les cas d'usage les plus courants :

- Sous-titre standard — bas centré, blanc cerclé de noir ;
- Titre — haut centré, grand, fond sombre translucide ;
- Titre centré — milieu centré, sans fond, contour accentué ;
- Carton inférieur — bas-gauche avec bandeau coloré ;
- Citation — milieu-centré italique, fond doux et marge large ;
- Générique simple — haut, petit, espacement large.

Règles de conception :

- module pur (aucune dépendance PySide6) ;
- les modèles sont immuables : on partage la même instance
  ``builtin_text_presets()`` entre tous les consommateurs ;
- les modèles utilisateur sont persistés via ``TextPresetStore`` dans
  le répertoire de configuration (JSON atomique).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .text_style import (
    TextAlignment,
    TextStyle,
    default_text_style,
)


# ---------------------------------------------------------------------------
# Modèles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextPreset:
    """Modèle de texte : un nom, un texte par défaut et un style."""

    id: str
    name: str
    description: str
    style: TextStyle
    default_text: str = ""
    builtin: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("Un modèle doit porter un identifiant non vide.")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("Un modèle doit porter un nom non vide.")
        if not isinstance(self.description, str):
            raise ValueError("La description d'un modèle doit être une chaîne.")
        object.__setattr__(self, "style", TextStyle(**self.style.to_dict()))
        # ``default_text`` peut être vide : on normalise en chaîne.
        object.__setattr__(self, "default_text", str(self.default_text))


def make_text_preset(
    preset_id: str,
    name: str,
    description: str,
    style: TextStyle,
    *,
    default_text: str = "",
    builtin: bool = False,
) -> TextPreset:
    """Construit un :class:`TextPreset` validé."""
    return TextPreset(
        id=preset_id,
        name=name,
        description=description,
        style=TextStyle(**style.to_dict()),
        default_text=default_text,
        builtin=builtin,
    )


# ---------------------------------------------------------------------------
# Bibliothèque intégrée
# ---------------------------------------------------------------------------


def _standard_subtitle_preset() -> TextPreset:
    return make_text_preset(
        preset_id="standard_subtitle",
        name="Sous-titre standard",
        description=(
            "Sous-titre par défaut en bas, centré, blanc cerclé de noir. "
            "Lisible sur la majorité des fonds."
        ),
        style=default_text_style(),
        default_text="Sous-titre",
        builtin=True,
    )


def _title_preset() -> TextPreset:
    return make_text_preset(
        preset_id="title",
        name="Titre",
        description=(
            "Titre d'ouverture, en haut centré, plus grand, avec un fond "
            "sombre translucide pour le détacher du plan."
        ),
        style=TextStyle(
            font_family="Arial",
            font_size=64.0,
            color="#ffffff",
            opacity=1.0,
            outline_color="#000000",
            outline_width=3.0,
            shadow_color="#000000",
            shadow_offset=2.0,
            background_color="#000000",
            background_opacity=0.55,
            padding_x=24.0,
            padding_y=14.0,
            alignment=TextAlignment.TOP_CENTER,
            position_x=0.0,
            position_y=0.0,
            margin_x=80.0,
            margin_y=96.0,
        ),
        default_text="Titre",
        builtin=True,
    )


def _centered_title_preset() -> TextPreset:
    return make_text_preset(
        preset_id="centered_title",
        name="Titre centré",
        description=(
            "Titre plein cadre au centre, sans fond, contour prononcé "
            "pour ressortir sur n'importe quel plan."
        ),
        style=TextStyle(
            font_family="Arial",
            font_size=72.0,
            color="#ffffff",
            opacity=1.0,
            outline_color="#000000",
            outline_width=4.0,
            shadow_color="#000000",
            shadow_offset=3.0,
            background_color=None,
            background_opacity=0.0,
            padding_x=12.0,
            padding_y=6.0,
            alignment=TextAlignment.MIDDLE_CENTER,
            position_x=0.0,
            position_y=0.0,
            margin_x=64.0,
            margin_y=64.0,
        ),
        default_text="Titre",
        builtin=True,
    )


def _lower_third_preset() -> TextPreset:
    return make_text_preset(
        preset_id="lower_third",
        name="Carton inférieur",
        description=(
            "Carton bas-gauche typique des reportages : fond coloré, "
            "padding généreux, marge latérale réduite."
        ),
        style=TextStyle(
            font_family="Arial",
            font_size=28.0,
            color="#ffffff",
            opacity=1.0,
            outline_color="#000000",
            outline_width=1.0,
            shadow_color="#000000",
            shadow_offset=0.0,
            background_color="#0f766e",
            background_opacity=0.9,
            padding_x=18.0,
            padding_y=12.0,
            alignment=TextAlignment.BOTTOM_LEFT,
            position_x=0.0,
            position_y=0.0,
            margin_x=24.0,
            margin_y=56.0,
        ),
        default_text="Carton",
        builtin=True,
    )


def _quote_preset() -> TextPreset:
    return make_text_preset(
        preset_id="quote",
        name="Citation",
        description=(
            "Citation milieu-centré : fond doux translucide, contour "
            "fin, marges larges — idéal pour des interviews."
        ),
        style=TextStyle(
            font_family="Georgia",
            font_size=36.0,
            color="#fffaf0",
            opacity=1.0,
            outline_color="#222222",
            outline_width=1.0,
            shadow_color="#000000",
            shadow_offset=1.0,
            background_color="#1f2937",
            background_opacity=0.7,
            padding_x=28.0,
            padding_y=18.0,
            alignment=TextAlignment.MIDDLE_CENTER,
            position_x=0.0,
            position_y=0.0,
            margin_x=120.0,
            margin_y=96.0,
        ),
        default_text="« Citation »",
        builtin=True,
    )


def _credits_preset() -> TextPreset:
    return make_text_preset(
        preset_id="credits_simple",
        name="Générique simple",
        description=(
            "Bloc générique haut centré, petite taille, marges "
            "généreuses pour empiler plusieurs rôles."
        ),
        style=TextStyle(
            font_family="Arial",
            font_size=22.0,
            color="#ffffff",
            opacity=0.95,
            outline_color="#000000",
            outline_width=1.0,
            shadow_color="#000000",
            shadow_offset=1.0,
            background_color=None,
            background_opacity=0.0,
            padding_x=8.0,
            padding_y=4.0,
            alignment=TextAlignment.TOP_CENTER,
            position_x=0.0,
            position_y=0.0,
            margin_x=64.0,
            margin_y=64.0,
        ),
        default_text="Générique",
        builtin=True,
    )


def builtin_text_presets() -> tuple[TextPreset, ...]:
    """Retourne la bibliothèque livrée."""
    return (
        _standard_subtitle_preset(),
        _title_preset(),
        _centered_title_preset(),
        _lower_third_preset(),
        _quote_preset(),
        _credits_preset(),
    )


def builtin_text_preset_ids() -> frozenset[str]:
    """Ensemble des identifiants de modèles intégrés."""
    return frozenset(preset.id for preset in builtin_text_presets())


def get_builtin_preset(preset_id: str) -> TextPreset | None:
    """Recherche un modèle intégré par identifiant."""
    for preset in builtin_text_presets():
        if preset.id == preset_id:
            return preset
    return None


# ---------------------------------------------------------------------------
# Filtrage
# ---------------------------------------------------------------------------


def filter_text_presets(
    presets: Iterable[TextPreset],
    *,
    search: str = "",
) -> list[TextPreset]:
    """Filtre les modèles selon une recherche libre.

    La recherche est faite sur le nom, l'identifiant (avec les
    underscores remplacés par des espaces) et la description. La
    recherche « titre » matche ``title`` et ``centered_title`` mais
    pas ``standard_subtitle`` : la recherche exige un mot complet
    (délimité par des séparateurs de mot, pas par des tirets) pour
    éviter les faux positifs du type « titre » ⊂ « sous-titre ».
    """
    needle = (search or "").strip().lower()
    if not needle:
        return list(presets)
    candidates: list[TextPreset] = []
    for preset in presets:
        id_text = preset.id.lower().replace("_", " ")
        name_text = preset.name.lower()
        desc_text = preset.description.lower()
        if (
            _word_match(needle, id_text)
            or _word_match(needle, name_text)
            or _word_match(needle, desc_text)
        ):
            candidates.append(preset)
    return candidates


def _word_match(needle: str, haystack: str) -> bool:
    """``True`` si ``needle`` est un mot entier de ``haystack``.

    On utilise une frontière de mot personnalisée qui exclut le tiret
    (``-``) afin d'éviter les faux positifs sur les libellés composés
    comme « sous-titre ».
    """
    import re

    # Les séparateurs de mot incluent les espaces, la ponctuation et
    # les underscores, mais pas le tiret ni l'apostrophe.
    pattern = r"(?:^|[\s.,!?;:/\\()\[\]{}+=*&^%$#@~`|])" + re.escape(needle)
    return re.search(pattern, haystack) is not None


# ---------------------------------------------------------------------------
# Persistance : modèles utilisateur
# ---------------------------------------------------------------------------


TEXT_PRESETS_FILE: str = "text_presets.json"


def _user_presets_dir(
    settings_dir: str | os.PathLike[str] | None = None,
) -> Path:
    if settings_dir is not None:
        return Path(settings_dir)
    env = os.environ.get("KUT_STUDIO_CONFIG_DIR")
    if env:
        return Path(env)
    home = Path.home()
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Kut-Studio"
        return home / "Kut-Studio"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Kut-Studio"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "kut-studio"
    return home / ".config" / "kut-studio"


def text_presets_path(
    settings_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Chemin du fichier de modèles utilisateur."""
    return _user_presets_dir(settings_dir) / TEXT_PRESETS_FILE


def _coerce_user_preset(payload: object) -> TextPreset | None:
    if not isinstance(payload, dict):
        return None
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    description = payload.get("description", "")
    if not isinstance(description, str):
        description = ""
    default_text = payload.get("default_text", "")
    if not isinstance(default_text, str):
        default_text = ""
    style_payload = payload.get("style")
    if not isinstance(style_payload, dict):
        return None
    try:
        style = TextStyle.from_dict(style_payload)
    except ValueError:
        return None
    preset_id = payload.get("id")
    if not isinstance(preset_id, str) or not preset_id:
        return None
    try:
        return make_text_preset(
            preset_id=preset_id,
            name=name,
            description=description,
            style=style,
            default_text=default_text,
            builtin=False,
        )
    except ValueError:
        return None


def load_user_text_presets(
    settings_dir: str | os.PathLike[str] | None = None,
) -> list[TextPreset]:
    """Charge les modèles utilisateur depuis le disque."""
    path = text_presets_path(settings_dir)
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [
        preset
        for preset in (_coerce_user_preset(entry) for entry in data)
        if preset is not None and not preset.builtin
    ]


def save_user_text_presets(
    presets: Sequence[TextPreset],
    settings_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Persiste les modèles utilisateur (écriture atomique)."""
    base = _user_presets_dir(settings_dir)
    base.mkdir(parents=True, exist_ok=True)
    target = base / TEXT_PRESETS_FILE
    payload = [
        {
            "id": preset.id,
            "name": preset.name,
            "description": preset.description,
            "default_text": preset.default_text,
            "style": preset.style.to_dict(),
        }
        for preset in presets
        if not preset.builtin
    ]
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{target.name}.",
        suffix=".tmp",
        dir=str(base),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            json.dump(payload, tmp_file, indent=2, ensure_ascii=False)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, target)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    return target


class TextPresetStore:
    """Gestionnaire en mémoire des modèles utilisateur."""

    def __init__(
        self,
        presets: Sequence[TextPreset] | None = None,
        settings_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self._settings_dir = settings_dir
        initial = list(presets) if presets is not None else load_user_text_presets(
            settings_dir
        )
        self._presets: dict[str, TextPreset] = {
            preset.id: preset for preset in initial if not preset.builtin
        }
        self._listeners: list = []

    # ----- API publique --------------------------------------------------

    def all(self) -> list[TextPreset]:
        return list(self._presets.values())

    def get(self, preset_id: str) -> TextPreset | None:
        return self._presets.get(preset_id)

    def all_presets(self) -> list[TextPreset]:
        """Bibliothèque complète (intégrés + utilisateur)."""
        library = list(builtin_text_presets())
        library.extend(self._presets.values())
        return library

    def add(self, preset: TextPreset) -> TextPreset:
        if preset.builtin:
            raise ValueError("Les modèles intégrés ne peuvent pas être ajoutés.")
        if preset.id in self._presets:
            raise ValueError(f"Un modèle '{preset.id}' existe déjà.")
        self._presets[preset.id] = preset
        self._persist()
        self._notify_changed()
        return preset

    def remove(self, preset_id: str) -> TextPreset:
        if preset_id not in self._presets:
            raise KeyError(f"Modèle '{preset_id}' introuvable.")
        removed = self._presets.pop(preset_id)
        self._persist()
        self._notify_changed()
        return removed

    def subscribe(self, callback) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    # ----- Persistance ---------------------------------------------------

    def _persist(self) -> None:
        save_user_text_presets(list(self._presets.values()), self._settings_dir)

    def _notify_changed(self) -> None:
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:
                pass


def make_user_text_preset(
    *,
    name: str,
    description: str,
    style: TextStyle,
    default_text: str = "",
) -> TextPreset:
    """Fabrique un :class:`TextPreset` utilisateur avec un id UUID."""
    if not name or not name.strip():
        raise ValueError("Le nom du modèle utilisateur est obligatoire.")
    return make_text_preset(
        preset_id=f"user-{uuid.uuid4().hex[:12]}",
        name=name.strip(),
        description=(description or "").strip(),
        style=style,
        default_text=default_text,
        builtin=False,
    )


__all__ = [
    "TEXT_PRESETS_FILE",
    "TextPreset",
    "TextPresetStore",
    "builtin_text_preset_ids",
    "builtin_text_presets",
    "filter_text_presets",
    "get_builtin_preset",
    "load_user_text_presets",
    "make_text_preset",
    "make_user_text_preset",
    "save_user_text_presets",
    "text_presets_path",
]