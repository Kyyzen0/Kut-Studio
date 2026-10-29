"""Sonde du pont StoreKit (spike ADR-0013 étape 13) : mesures au chargement, par appel, au démontage. JETABLE.

Usage : python bridge_probe.py [chemin de libkutstore.dylib]   (dans un bundle PyInstaller : la bibliothèque est à côté).
Imprime un JSON par mesure. Sans compte ni configuration StoreKit, les appels rendent « aucun droit » / « pas de compte » :
c'est le résultat attendu hors Xcode (voir STOREKIT.md).
"""
from __future__ import annotations

import json
import os
import resource
import sys
import threading
import time
from pathlib import Path


def emit(**kw):
    print(json.dumps(kw, default=str), flush=True)


def default_dylib() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    for candidate in (base / "libkutstore.dylib", base / "build" / "libkutstore.dylib", Path(sys.executable).parent / "libkutstore.dylib"):
        if candidate.exists():
            return candidate
    return base / "libkutstore.dylib"


def main() -> int:
    sys.path.insert(0, str(Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))))
    import storekit_contract as contract

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else default_dylib()
    rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    t0 = time.perf_counter()
    bridge = contract.StoreBridge(path)
    emit(step="load", abi=bridge.abi_version, ms=round((time.perf_counter() - t0) * 1000, 1), path=str(path))
    for name, op, arg in (("entitlements", contract.OP_ENTITLEMENTS, ""), ("app_transaction", contract.OP_APP_TRANSACTION, ""),
                          ("products", contract.OP_PRODUCTS, "com.kutstudio.app.monthly,com.kutstudio.app.lifetime"),
                          ("start_updates", contract.OP_START_UPDATES, "")):
        t = time.perf_counter()
        status, payload = bridge.call(op, arg, timeout_ms=8000)
        emit(step=name, status=status, payload=payload, ms=round((time.perf_counter() - t) * 1000, 1))
    timings = []
    for _ in range(20):
        t = time.perf_counter()
        bridge.call(contract.OP_ENTITLEMENTS, "", timeout_ms=8000)
        timings.append((time.perf_counter() - t) * 1000)
    timings.sort()
    emit(step="entitlements_warm_x20", median_ms=round(timings[10], 1), max_ms=round(timings[-1], 1))
    # Démontage : un fil de travail attend un événement, le thread principal appelle shutdown, puis le processus se termine.
    result: dict = {}
    worker = threading.Thread(target=lambda: result.update(zip(("status", "payload"), bridge.poll_update(60000))))
    worker.start()
    time.sleep(0.2)
    t = time.perf_counter()
    bridge.shutdown()
    worker.join(5)
    emit(step="shutdown_wakes_poller", status=result.get("status"), joined=not worker.is_alive(), ms=round((time.perf_counter() - t) * 1000, 1))
    emit(step="after_shutdown_call", result=bridge.call(contract.OP_ENTITLEMENTS))
    emit(step="rss", home=os.environ.get("HOME"), sandboxed=bool(os.environ.get("APP_SANDBOX_CONTAINER_ID")), before_kb=rss0 // 1024 if sys.platform == "darwin" else rss0, after_kb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
