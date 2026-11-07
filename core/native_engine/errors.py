"""Erreurs typées du moteur natif : un code ``kut_status_t`` devient une exception, rien d'autre ne traverse la frontière."""

from __future__ import annotations

from . import abi


class NativeEngineError(RuntimeError):
    """Échec d'un appel natif. ``status`` est le code de l'ABI, ``status_name`` son nom stable ; le message vient de la bibliothèque
    (court, sans donnée de l'utilisateur au-delà de 48 octets) ou est fixe quand le refus est local."""

    def __init__(self, status: int, message: str = "") -> None:
        self.status = status
        self.status_name = abi.STATUS_NAMES.get(status, "unknown")
        self.detail = message
        super().__init__(f"{self.status_name}: {message}" if message else self.status_name)


class InvalidArgumentError(NativeEngineError): ...
class InvalidHandleError(NativeEngineError): ...
class InvalidStateError(NativeEngineError): ...
class UnsupportedError(NativeEngineError): ...
class InvalidPlanError(NativeEngineError): ...
class NotFoundError(NativeEngineError): ...
class QueueFullError(NativeEngineError): ...
class SeekRequiredError(NativeEngineError): ...
class BadFrameError(NativeEngineError): ...
class DeviceError(NativeEngineError): ...
class BufferTooSmallError(NativeEngineError): ...
class InternalError(NativeEngineError): ...


_BY_STATUS: dict[int, type[NativeEngineError]] = {
    abi.ERR_INVALID_ARGUMENT: InvalidArgumentError, abi.ERR_INVALID_HANDLE: InvalidHandleError,
    abi.ERR_INVALID_STATE: InvalidStateError, abi.ERR_UNSUPPORTED: UnsupportedError, abi.ERR_INVALID_PLAN: InvalidPlanError,
    abi.ERR_NOT_FOUND: NotFoundError, abi.ERR_QUEUE_FULL: QueueFullError, abi.ERR_SEEK_REQUIRED: SeekRequiredError,
    abi.ERR_BAD_FRAME: BadFrameError, abi.ERR_DEVICE: DeviceError, abi.ERR_BUFFER_TOO_SMALL: BufferTooSmallError,
    abi.ERR_INTERNAL: InternalError,
}


def error_for(status: int, message: str = "") -> NativeEngineError:
    """L'exception qui correspond à ``status`` ; un code inconnu (bibliothèque plus récente) donne l'erreur générique."""
    return _BY_STATUS.get(status, NativeEngineError)(status, message)
