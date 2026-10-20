"""Application Qt du moteur GPU : réutilise celle de l'hôte, sinon en crée une, discrète et sans configuration.

Règles : (1) si une `QApplication` existe, on s'en sert telle quelle, sans toucher à l'environnement ni à ses
réglages ; (2) sinon on en crée une avec un nom neutre (aucun `QSettings`, rien n'est écrit chez l'utilisateur) et,
sur macOS, la plateforme `cocoa` demandée par l'argument `-platform` uniquement si `QT_QPA_PLATFORM` n'est pas
définie (l'environnement du processus n'est jamais modifié) ; (3) l'appel vient du fil qui possède l'application.
Sur macOS, une application créée ici passe en politique « accessoire » : pas d'icône dans le Dock, pas de barre de
menus. Les plugins `offscreen` et `minimal` n'ont pas de GPU : la sonde le détecte et le dit.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys

from PySide6.QtCore import QCoreApplication, QThread
from PySide6.QtWidgets import QApplication

from kut3d.errors import RenderBackendUnavailable, RenderError

APP_NAME = "kut3d-render"
_created: QApplication | None = (
    None  # garde une référence : l'application vit jusqu'à la fin du processus
)


def _hide_from_dock() -> None:
    """macOS : politique d'activation « accessoire » (NSApplicationActivationPolicyAccessory = 1)."""
    if sys.platform != "darwin":
        return
    try:
        lib = ctypes.util.find_library("objc")
        if lib is None:
            return
        objc = ctypes.cdll.LoadLibrary(lib)
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        proto = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        shared = proto(("objc_msgSend", objc))(
            objc.objc_getClass(b"NSApplication"),
            objc.sel_registerName(b"sharedApplication"),
        )
        setter = ctypes.CFUNCTYPE(
            ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long
        )(("objc_msgSend", objc))
        setter(shared, objc.sel_registerName(b"setActivationPolicy:"), 1)
    except Exception:  # noqa: BLE001 - cosmétique : un échec ne doit jamais empêcher le rendu
        return


_NO_GPU_PLATFORMS = (
    "offscreen",
    "minimal",
    "minimalegl",
    "vnc",
    "linuxfb",
    "eglfs_emu",
)


def _check_platform(app: QApplication) -> QApplication:
    name = str(app.platformName())
    if name in _NO_GPU_PLATFORMS:
        raise RenderBackendUnavailable(
            f"la plateforme Qt « {name} » n'a pas d'accès GPU : lancer l'application avec la plateforme native "
            '(sur macOS : cocoa) ou utiliser backend="numpy".'
        )
    return app


def ensure_application(*, require_gpu: bool = True) -> QApplication:
    """La `QApplication` à utiliser (existante ou créée ici). Lève `RenderError` hors du fil de l'application.
    `require_gpu=False` (texte 3D : seulement des polices) accepte aussi les plateformes sans GPU (`offscreen`, `minimal`...)."""
    global _created
    instance = QCoreApplication.instance()
    if instance is not None:
        if not isinstance(instance, QApplication):
            raise RenderBackendUnavailable(
                "une application Qt existe sans widgets (QCoreApplication ou QGuiApplication) : "
                "le moteur GPU a besoin d'une QApplication."
            )
        if instance.thread() is not QThread.currentThread():
            raise RenderError(
                "le moteur « qrhi » doit être appelé depuis le fil qui possède l'application Qt."
            )
        return _check_platform(instance) if require_gpu else instance
    QCoreApplication.setApplicationName(APP_NAME)
    QCoreApplication.setOrganizationName(APP_NAME)
    argv = [APP_NAME]
    if sys.platform == "darwin" and not os.environ.get("QT_QPA_PLATFORM"):
        argv += ["-platform", "cocoa"]
    _created = QApplication(argv)
    _created.setQuitOnLastWindowClosed(False)
    _hide_from_dock()
    return _check_platform(_created) if require_gpu else _created


def created_here() -> bool:
    """Vrai si l'application a été créée par ce module (et non par l'hôte)."""
    return _created is not None


def platform_name() -> str:
    """Nom du plugin de plateforme de l'application Qt courante (« cocoa », « windows », « xcb »...), ou ""."""
    app = QCoreApplication.instance()
    return str(app.platformName()) if isinstance(app, QApplication) else ""
