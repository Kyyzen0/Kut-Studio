"""Traductions du décodage matériel et de l'aperçu GPU (fusionnées dans :mod:`ui.i18n`)."""

from __future__ import annotations


def _t(fr: str, en: str, es: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "es": es}


HARDWARE_TRANSLATIONS: dict[str, dict[str, str]] = {
    "perf.hardware.title": _t("Matériel", "Hardware", "Hardware"),
    "perf.decode": _t("Décodage vidéo", "Video decoding", "Decodificación de vídeo"),
    "perf.decode.auto": _t("Auto", "Auto", "Auto"),
    "perf.decode.cpu": _t("CPU (logiciel)", "CPU (software)", "CPU (software)"),
    "perf.preview_backend": _t("Rendu de l'aperçu", "Preview rendering", "Renderizado de la vista previa"),
    "perf.preview_backend.auto": _t("Auto", "Auto", "Auto"),
    "perf.preview_backend.cpu": _t("CPU", "CPU", "CPU"),
    "perf.preview_backend.gpu": _t("GPU", "GPU", "GPU"),
    "perf.decode.restart_hint": _t(
        "Le moniteur temps réel applique le nouveau décodage au prochain démarrage ; "
        "l'aperçu fidèle, les proxies et le tracking l'utilisent tout de suite.",
        "The real-time monitor uses the new decoding after a restart; "
        "the faithful preview, proxies and tracking use it right away.",
        "El monitor en tiempo real aplica la nueva decodificación tras reiniciar; "
        "la vista previa fiel, los proxies y el seguimiento la usan de inmediato.",
    ),
    "perf.encoding.export": _t("Encodeur d'export par défaut", "Default export encoder",
                               "Codificador de exportación predeterminado"),
    "preview.gpu_fallback": _t(
        "Aperçu GPU indisponible ({detail}) : retour au CPU.",
        "GPU preview unavailable ({detail}): back to CPU.",
        "Vista previa GPU no disponible ({detail}): vuelta a la CPU.",
    ),
    "preview.memory_pressure": _t(
        "Mémoire saturée : caches libérés, aperçu allégé.",
        "Memory is low: caches freed, lighter preview.",
        "Memoria saturada: cachés liberadas, vista previa aligerada.",
    ),
}
