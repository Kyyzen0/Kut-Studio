"""Encodeurs matériels et balises de couleur : un **vrai** mini export par encodeur disponible, relu avec ffprobe.

Pour le témoin logiciel (libx264) puis VideoToolbox, NVENC, Quick Sync, AMF et VAAPI, en MP4 et en MOV : la
commande est celle de l'application (plan de rendu → graphe → étape BT.709 → encodeur), le fichier produit est
relu avec ``ffprobe`` (matrice, primaires, transfert, plage = ``OUTPUT_COLOR_TAGS``) et trois bandes de couleur
connue sont décodées comme le fait un lecteur.

Honnêteté : un backend est vérifié **seulement** s'il existe ici, selon la détection de l'application (une
seule détection : ``CapabilityService``). Sinon le test se saute avec la raison (« encodeur absent de ce
FFmpeg », « présent mais refusé à la validation : … »), jamais en silence et jamais compté comme réussi.
``KUT_STUDIO_REQUIRE_HARDWARE=videotoolbox,nvenc`` transforme l'absence en échec (machines dédiées).
"""

from __future__ import annotations

import pytest

from core.export_engine import ExportFormat
from core.hardware_cache import CACHE_FILE_NAME, DECODE_DISABLE_VARIABLE, CapabilityService, set_default_service
from core.hardware_encoding import FFMPEG_ENCODER_NAMES, HardwareEncoder
from core.hardware_validation import (
    PATCHES,
    PIXEL_TOLERANCE,
    REQUIRE_VARIABLE,
    VALIDATED_BACKENDS,
    Outcome,
    expected_color_tags,
    format_validation,
    media_tools,
    prepare_source,
    required_backends,
    skip_reason,
    validate_encoder,
)


@pytest.fixture(scope="module")
def tools():
    found = media_tools()
    if found is None:
        pytest.skip("FFmpeg ou ffprobe absent : aucun encodeur ne peut être vérifié")
    return found


@pytest.fixture(scope="module")
def service(tmp_path_factory, tools):
    """La détection de l'application, une fois pour le module, avec un cache isolé (jamais celui de l'utilisateur).

    Les décodeurs ne servent pas ici : leur validation est coupée pour garder le module rapide.
    """
    cache = tmp_path_factory.mktemp("hardware-cache") / CACHE_FILE_NAME
    detected = CapabilityService(
        command_provider=lambda: [tools.ffmpeg], cache_path=cache, environment={DECODE_DISABLE_VARIABLE: "off"},
    )
    detected.capabilities()
    return detected


@pytest.fixture(scope="module")
def source(tmp_path_factory, tools):
    return prepare_source(tools, tmp_path_factory.mktemp("hardware-source"))


@pytest.fixture
def capabilities(service):
    set_default_service(service)  # la commande d'export de l'application lit le service global
    return service.capabilities()


BT709_LIMITED = {"color_space": "bt709", "color_primaries": "bt709", "color_transfer": "bt709", "color_range": "tv"}
"""Ce que tout export doit porter, écrit en clair : les attendus du module en dérivent (``OUTPUT_COLOR_TAGS``)."""


def _required() -> frozenset[HardwareEncoder]:
    try:
        return required_backends()
    except ValueError as error:  # une faute de frappe ne doit pas rendre l'exigence muette
        pytest.fail(str(error))


@pytest.mark.parametrize("export_format", [ExportFormat.MP4_H264, ExportFormat.MOV_H264], ids=["mp4", "mov"])
@pytest.mark.parametrize("backend", VALIDATED_BACKENDS, ids=lambda backend: backend.value)
def test_an_export_with_each_available_encoder_is_tagged_bt709_and_keeps_its_colours(
    backend, export_format, capabilities, tools, source, tmp_path
):
    required = _required()
    reason = skip_reason(capabilities, backend)
    if reason:
        if backend in required:
            pytest.fail(f"{reason} — exigé par {REQUIRE_VARIABLE}")
        pytest.skip(reason)

    result = validate_encoder(backend, capabilities, tools=tools, source=source, workdir=tmp_path,
                              export_format=export_format, required=required)

    assert result.outcome is Outcome.PASSED, format_validation(result)
    # Rien de vide : l'encodeur attendu a réellement produit un fichier, relu champ par champ et pixel par pixel.
    assert result.encoder_used == FFMPEG_ENCODER_NAMES[("h264", backend)]
    assert result.returncode == 0 and result.size_bytes > 0
    assert result.expected_tags == expected_color_tags() == BT709_LIMITED
    assert {name: result.obtained_tags.get(name) for name in BT709_LIMITED} == BT709_LIMITED
    assert [pixel.name for pixel in result.pixels] == [patch.name for patch in PATCHES]
    assert all(pixel.deviation <= PIXEL_TOLERANCE for pixel in result.pixels), format_validation(result)
