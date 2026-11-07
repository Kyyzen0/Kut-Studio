"""Raisons pour lesquelles le moteur natif n'est pas disponible (``core.native_engine``), fusionnées dans :mod:`ui.i18n`.

Une clé par code de :data:`core.native_engine.loader.REASON_MESSAGES` (``native_engine.reason.<code>``) ; aucun chemin n'y est recopié.
Rien ne les affiche encore : l'intégration au moniteur arrive dans une PR suivante (``docs/native-engine.md``).
"""

from __future__ import annotations


def _t(fr: str, en: str, es: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "es": es}


NATIVE_ENGINE_TRANSLATIONS: dict[str, dict[str, str]] = {
    "native_engine.reason.platform": _t(
        "Le moteur natif n'existe que sous macOS.",
        "The native engine only exists on macOS.",
        "El motor nativo solo existe en macOS.",
    ),
    "native_engine.reason.store": _t(
        "Le moteur natif n'est pas chargé dans la version Mac App Store.",
        "The native engine is not loaded in the Mac App Store version.",
        "El motor nativo no se carga en la versión de la Mac App Store.",
    ),
    "native_engine.reason.not_found": _t(
        "Aucune bibliothèque du moteur natif n'est installée.",
        "No native engine library is installed.",
        "No hay ninguna biblioteca del motor nativo instalada.",
    ),
    "native_engine.reason.bad_path": _t(
        "Le chemin de la bibliothèque du moteur natif est invalide (il doit être absolu).",
        "The path of the native engine library is invalid (it must be absolute).",
        "La ruta de la biblioteca del motor nativo no es válida (debe ser absoluta).",
    ),
    "native_engine.reason.unreadable": _t(
        "La bibliothèque du moteur natif est illisible.",
        "The native engine library cannot be read.",
        "No se puede leer la biblioteca del motor nativo.",
    ),
    "native_engine.reason.not_macho": _t(
        "Le fichier du moteur natif n'est pas une bibliothèque macOS.",
        "The native engine file is not a macOS library.",
        "El archivo del motor nativo no es una biblioteca de macOS.",
    ),
    "native_engine.reason.wrong_arch": _t(
        "La bibliothèque du moteur natif est faite pour une autre architecture.",
        "The native engine library is built for another architecture.",
        "La biblioteca del motor nativo está hecha para otra arquitectura.",
    ),
    "native_engine.reason.not_dylib": _t(
        "Le fichier du moteur natif n'est pas une bibliothèque dynamique.",
        "The native engine file is not a dynamic library.",
        "El archivo del motor nativo no es una biblioteca dinámica.",
    ),
    "native_engine.reason.load_failed": _t(
        "La bibliothèque du moteur natif n'a pas pu être chargée.",
        "The native engine library could not be loaded.",
        "No se pudo cargar la biblioteca del motor nativo.",
    ),
    "native_engine.reason.no_abi_symbol": _t(
        "La bibliothèque du moteur natif n'annonce pas sa version d'interface.",
        "The native engine library does not report its interface version.",
        "La biblioteca del motor nativo no indica la versión de su interfaz.",
    ),
    "native_engine.reason.abi_mismatch": _t(
        "La bibliothèque du moteur natif a une autre version d'interface que celle attendue.",
        "The native engine library has a different interface version than expected.",
        "La biblioteca del motor nativo tiene una versión de interfaz distinta de la esperada.",
    ),
    "native_engine.reason.incomplete": _t(
        "La bibliothèque du moteur natif est incomplète.",
        "The native engine library is incomplete.",
        "La biblioteca del motor nativo está incompleta.",
    ),
}
