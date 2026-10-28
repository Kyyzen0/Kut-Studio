"""Sonde du spike bac à sable (ADR-0013 étape 1) : s'exécute DANS l'application signée et imprime un JSON par ligne.

Sans dépendance hors bibliothèque standard. Lancée par ``replay.sh`` (``open --stdout``). Modes :
  (sans argument)        toutes les mesures ;
  --child                processus relancé par la mesure « relance de sys.executable » (écrit une ligne, sort 0).
"""

from __future__ import annotations

import ctypes
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path


def out(name: str, ok: bool, detail: object = "") -> None:
    line = json.dumps({"test": name, "ok": ok, "detail": str(detail)[:300]}, ensure_ascii=False)
    print(line, flush=True)
    try:  # copie dans le conteneur : relisible par ``KUT_PROBE_CAT`` quand l'application n'a pas de stdout (Finder)
        with open(os.path.join(tempfile.gettempdir(), "probe-last.log"), "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def attempt(name: str, fn) -> None:
    try:
        out(name, True, fn())
    except BaseException as error:  # noqa: BLE001 : on mesure, tout échec est une donnée
        out(name, False, f"{type(error).__name__}: {error}")


def run(command: list[str], timeout: float = 20.0) -> str:
    done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if done.returncode != 0:
        raise RuntimeError(f"rc={done.returncode} stderr={done.stderr.strip()[:150]} stdout={done.stdout.strip()[:80]}")
    return done.stdout.strip()[:150]


def write_check(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    path.unlink()
    return f"écriture ok dans {path.parent}"


def file_tests(path: Path) -> None:
    """Mesures sur un fichier remis par le système (extension de bac à sable de LaunchServices)."""
    attempt("fichier_ouvert_lecture", lambda: f"{len(path.read_bytes())} octets")
    attempt("fichier_ouvert_reecriture_en_place", lambda: (path.write_bytes(path.read_bytes()), "ok")[1])
    attempt("fichier_ouvert_voisin_lock_O_EXCL", lambda: (os.close(os.open(str(path) + ".lock", os.O_CREAT | os.O_EXCL | os.O_WRONLY)), os.unlink(str(path) + ".lock"), "ok")[2])
    attempt("fichier_ouvert_voisin_autosave", lambda: (Path(str(path) + ".autosave").write_text("x"), os.unlink(str(path) + ".autosave"), "ok")[2])
    attempt("fichier_ouvert_temp_voisin_puis_rename", lambda: (Path(str(path.parent / ".t.partial")).write_text("x"), os.replace(str(path.parent / ".t.partial"), str(path)), "ok")[2])
    attempt("fichier_ouvert_dossier_listing", lambda: len(os.listdir(path.parent)))
    attempt("fichier_voisin_non_ouvert_lecture", lambda: len(path.parent.joinpath("neighbor.txt").read_bytes()))
    attempt("fichier_ouvert_temp_dans_conteneur_puis_rename_vers_fichier", lambda: (Path(tempfile.gettempdir(), "t.partial").write_text("x"), os.replace(str(Path(tempfile.gettempdir(), "t.partial")), str(path)), "ok")[2])


def main() -> int:
    if "--child" in sys.argv:
        print(json.dumps({"child": "vivant", "pid": os.getpid(), "ppid": os.getppid()}), flush=True)
        return 0
    cat = os.environ.get("KUT_PROBE_CAT")
    if cat:  # relit un fichier du conteneur (illisible depuis le shell) et l'imprime
        for name in cat.split(os.pathsep):
            attempt("cat_" + Path(name).name, lambda name=name: Path(name).read_text()[:1500])
        return 0
    exe = Path(sys.executable)
    bundle = exe.parents[2]
    out("contexte", True, {"exe": str(exe), "frozen": getattr(sys, "frozen", False), "home": str(Path.home()),
                          "HOME": os.environ.get("HOME"), "tmp": tempfile.gettempdir(), "sandbox_container": os.environ.get("APP_SANDBOX_CONTAINER_ID")})
    # --- Fichiers ---
    attempt("ecriture_conteneur_AppSupport", lambda: write_check(Path.home() / "Library" / "Application Support" / "Kut-Studio" / "t"))
    attempt("ecriture_tmp_python", lambda: write_check(Path(tempfile.gettempdir()) / "kut-probe"))
    attempt("ecriture_/tmp", lambda: write_check(Path("/tmp") / "kut-probe-sandbox"))
    attempt("ecriture_vrai_Home_Documents", lambda: write_check(Path("/Users") / os.environ.get("USER", "x") / "Documents" / "kut-probe"))
    attempt("lecture_/opt/homebrew/bin/ffmpeg", lambda: f"{len(open('/opt/homebrew/bin/ffmpeg', 'rb').read(64))} octets")
    attempt("lecture_/Users/_home_listing", lambda: len(os.listdir(os.path.expanduser("/Users/" + os.environ.get("USER", "x") + "/Documents"))))
    attempt("lecture_bundle_ressources", lambda: len(os.listdir(bundle / "Contents" / "Frameworks")))
    if os.environ.get('KUT_PROBE_FILE'):
        file_tests(Path(os.environ['KUT_PROBE_FILE']))
    # --- Relance de l'exécutable principal (gardien, sonde GPU) ---
    def relaunch() -> str:
        return run([str(exe), "--child"])
    attempt("relance_sys.executable_--child", relaunch)
    aux = os.environ.get("KUT_PROBE_HELPER")
    if aux:
        attempt("relance_auxiliaire_inherit", lambda: run([aux, "--child"]))
    # Popen détaché + attente : la forme du gardien (survit au parent ? ici : démarre-t-il ?)
    def popen_pipe() -> str:
        child = subprocess.Popen([str(exe), "--child"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        text = child.stdout.readline().strip()  # type: ignore[union-attr]
        child.wait(timeout=10)
        return f"{text} rc={child.returncode}"
    attempt("popen_pipe_sys.executable", popen_pipe)
    # --- Outils système ---
    attempt("pmset_-g", lambda: run(["pmset", "-g"]))
    attempt("pmset_-g_batt", lambda: run(["pmset", "-g", "batt"]))
    attempt("pmset_-g_assertions", lambda: run(["pmset", "-g", "assertions"]))
    attempt("sysctl_hw.memsize", lambda: run(["sysctl", "-n", "hw.memsize"]))
    attempt("sysctl_vm.swapusage", lambda: run(["sysctl", "-n", "vm.swapusage"]))
    attempt("sw_vers", lambda: run(["sw_vers", "-productVersion"]))
    attempt("ps_-p_self", lambda: run(["ps", "-p", str(os.getpid()), "-o", "pid,ppid,comm"]))
    attempt("caffeinate_1s", lambda: run(["caffeinate", "-i", "-t", "1"]))
    def libproc() -> str:
        lib = ctypes.CDLL("/usr/lib/libproc.dylib")
        buffer = (ctypes.c_int * 1024)()
        count = lib.proc_listchildpids(os.getpid(), buffer, ctypes.sizeof(buffer))
        child = subprocess.Popen(["sleep", "3"])
        count2 = lib.proc_listchildpids(os.getpid(), buffer, ctypes.sizeof(buffer))
        child.kill(); child.wait()
        return f"enfants avant={count} apres={count2}"
    attempt("libproc_listchildpids", libproc)
    def pidinfo() -> str:
        lib = ctypes.CDLL("/usr/lib/libproc.dylib")
        buffer = ctypes.create_string_buffer(136)
        child = subprocess.Popen(["sleep", "3"])
        own = lib.proc_pidinfo(os.getpid(), 3, 0, buffer, 136)
        kid = lib.proc_pidinfo(child.pid, 3, 0, buffer, 136)
        other = lib.proc_pidinfo(1, 3, 0, buffer, 136)
        child.kill(); child.wait()
        return f"proc_pidinfo(PROC_PIDTBSDINFO) retours (136 = ok) : soi={own} enfant={kid} launchd(pid 1)={other}"
    attempt("libproc_proc_pidinfo", pidinfo)
    def killpg() -> str:
        child = subprocess.Popen(["sleep", "30"], start_new_session=True)
        import signal
        os.killpg(child.pid, signal.SIGKILL); child.wait()
        return "start_new_session + killpg ok"
    attempt("setsid_killpg_enfant", killpg)
    # --- FFmpeg embarqué (hérite : app-sandbox + inherit) ---
    ffmpeg = bundle / "Contents" / "Frameworks" / "bin" / "ffmpeg"
    if ffmpeg.exists():
        attempt("ffmpeg_embarque_-version", lambda: run([str(ffmpeg), "-version"]).splitlines()[0])
        tmpdir = tempfile.gettempdir()
        attempt("ffmpeg_embarque_encode_tmp", lambda: run([str(ffmpeg), "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=1", "-pix_fmt", "yuv420p", os.path.join(tmpdir, "k.mp4")]) or "ok")
        attempt("ffmpeg_embarque_encode_videotoolbox", lambda: run([str(ffmpeg), "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=1", "-c:v", "h264_videotoolbox", os.path.join(tmpdir, "kv.mp4")]) or "ok")
        attempt("ffmpeg_embarque_ecriture_hors_conteneur", lambda: run([str(ffmpeg), "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=25:duration=1", "/Users/" + os.environ.get("USER", "x") + "/Documents/kut-probe.mp4"]) or "ok")
    attempt("ffmpeg_homebrew_-version", lambda: run(["/opt/homebrew/bin/ffmpeg", "-version"]).splitlines()[0])
    # --- Réseau ---
    attempt("dns_github.com", lambda: socket.gethostbyname("github.com"))
    def connect() -> str:
        sock = socket.create_connection(("1.1.1.1", 443), timeout=5)
        sock.close()
        return "connexion TCP ok"
    attempt("tcp_1.1.1.1:443", connect)
    def local_listen() -> str:
        server = socket.socket(); server.bind(("127.0.0.1", 0)); server.listen(1)
        port = server.getsockname()[1]
        client = socket.create_connection(("127.0.0.1", port), timeout=3)
        client.close(); server.close()
        return f"boucle locale ok port={port}"
    attempt("tcp_boucle_locale", local_listen)
    # --- Mémoire exécutable (libffi / ctypes) ---
    def closure() -> str:
        callback = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int)(lambda x: x + 1)
        return str(callback(41))
    attempt("ctypes_callback_libffi", closure)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
