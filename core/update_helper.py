"""Assistant d'installation macOS : le script ``/bin/sh`` qui remplace ``Kut-Studio.app`` après la fermeture.

Pourquoi un script généré à l'exécution plutôt qu'un fichier des ressources : l'assistant ne peut **pas** vivre dans le
bundle qu'il remplace (l'ancien bundle est renommé en cours de route), et un fichier de plus dans le bundle changerait
sa signature. Le texte est constant (:data:`HELPER_SCRIPT`) : aucun chemin n'y est interpolé, tout passe par des
variables d'environnement ``KUT_UPD_*`` (un chemin avec espaces, accents ou guillemets reste donc une simple valeur).
:func:`write_helper_script` l'écrit dans un dossier privé du cache (``<cache>/updates/helper-<pid>/``), et
:func:`launch_helper` le lance détaché (``start_new_session``), sorties vers ``install.log``.

Déroulé (le script est **idempotent** : il lit l'état des dossiers au lieu de supposer où il en était) :

1. attendre la fin du processus de l'application (``KUT_UPD_WAIT_SECONDS``, 120 s) ; au-delà, **rien n'est modifié** ;
2. ``Kut-Studio.app`` → ``.Kut-Studio.app.previous-<version>`` (renommage atomique), puis le staging →
   ``Kut-Studio.app`` ; un échec du second renommage remet aussitôt la précédente ;
3. ``open`` de la nouvelle application, puis attente du fichier « démarré » qu'elle écrit (version attendue, processus
   vivant ``KUT_UPD_GRACE_SECONDS`` plus tard), au plus ``KUT_UPD_START_SECONDS`` (90 s) ;
4. succès : statut ``ok``, sauvegarde supprimée ; échec ou signal : **retour arrière** (processus de la nouvelle version
   arrêtés : SIGTERM, attente, SIGKILL, attente ; nouvelle supprimée, précédente remise, relancée) et statut
   ``rolled_back``. Si un processus de la nouvelle version survit à SIGKILL : statut ``rollback_impossible``, rien n'est
   déplacé ni supprimé, la précédente n'est pas lancée.

**Verrou** : ``<cache>/updates/install.lock`` est un **dossier** créé par ``mkdir`` (exclusif, atomique) qui contient le pid de
l'assistant. Un second assistant ne l'obtient pas et sort (code 6) avant d'examiner quoi que ce soit ; un verrou dont le
propriétaire est mort est renommé vers un nom unique puis supprimé, jamais supprimé en place. Libéré à toute sortie
(``trap EXIT``) sauf SIGKILL. **Renommages** : ``safe_mv`` n'écrit jamais dans une destination existante (``mv`` y
imbriquerait le dossier) : test avant, nom unique, test juste avant le dernier renommage, vérification après.

Chaque étape écrit dans ``install.log``. Tout ``rm -rf`` passe par ``safe_rm``, qui refuse un chemin dont le nom n'est
pas celui d'un staging ou d'une sauvegarde de Kut-Studio.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .build_flavor import require_not_store
from .updates import updates_directory

STAGING_PREFIX = ".Kut-Studio-update-"
PREVIOUS_PREFIX = ".Kut-Studio.app.previous-"
WAIT_SECONDS = 120
"""Attente maximale de la fermeture de l'application avant d'abandonner sans rien modifier."""
START_SECONDS = 90
"""Attente maximale du fichier « démarré » de la nouvelle application (premier lancement d'un gros bundle compris)."""
GRACE_SECONDS = 3
"""La nouvelle application doit encore vivre ce délai après avoir écrit le fichier « démarré »."""
LOG_NAME = "install.log"
STATUS_NAME = "install-status.json"
STARTED_NAME = "started.json"
LOCK_NAME = "install.lock"
_SAFE_VERSION = re.compile(r"[0-9A-Za-z.+-]{1,64}")

HELPER_SCRIPT = r"""#!/bin/sh
# Kut-Studio : remplacement de l'application pendant une mise à jour (macOS). Généré par core/update_helper.py.
# Les entrées sont les variables KUT_UPD_* ; aucun chemin n'est écrit dans ce texte.
set -u
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH

: "${KUT_UPD_OPEN:=/usr/bin/open}"
: "${KUT_UPD_WAIT_SECONDS:=120}"
: "${KUT_UPD_START_SECONDS:=90}"
: "${KUT_UPD_GRACE_SECONDS:=3}"
: "${KUT_UPD_TERM_SECONDS:=5}"
: "${KUT_UPD_KILL_SECONDS:=5}"
PHASE=init
OWNS_LOCK=0

log() {
    printf '%s [%s] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$$" "$*" >> "$KUT_UPD_LOG" 2>/dev/null
}

status() {
    _tmp="$KUT_UPD_STATUS.tmp.$$"
    printf '{"result":"%s","version":"%s","previous":"%s","reason":"%s"}\n' \
        "$1" "$KUT_UPD_VERSION" "$KUT_UPD_FROM" "$2" > "$_tmp" 2>/dev/null && mv -f "$_tmp" "$KUT_UPD_STATUS"
}

status_if_absent() {
    # Écrit le statut seulement s'il n'existe pas (lien dur : atomique, ne remplace jamais le statut d'un autre assistant).
    _tmp="$KUT_UPD_STATUS.tmp.$$"
    printf '{"result":"%s","version":"%s","previous":"%s","reason":"%s"}\n' \
        "$1" "$KUT_UPD_VERSION" "$KUT_UPD_FROM" "$2" > "$_tmp" 2>/dev/null && ln "$_tmp" "$KUT_UPD_STATUS" 2>/dev/null
    rm -f "$_tmp"
}

halt_after() {
    # Crochet de test : simule un arrêt brutal (SIGKILL) du script, sans nettoyage, après l'étape nommée.
    if [ "${KUT_UPD_HALT_AFTER:-}" = "$1" ]; then
        log "arrêt simulé après $1"
        kill -9 $$
    fi
}

safe_rm() {
    _base=${1##*/}
    _lockbase=${KUT_UPD_LOCK##*/}
    case "$_base" in
        .Kut-Studio-update-*|.Kut-Studio.app.previous-*) ;;
        "$_lockbase".stale.*) ;;
        *) log "suppression refusée : $1"; return 1 ;;
    esac
    case "$1" in
        /?*/?*) ;;
        *) log "suppression refusée (chemin) : $1"; return 1 ;;
    esac
    rm -rf -- "$1"
}

safe_mv() {
    # Renomme le dossier $1 en $2 sans jamais l'imbriquer dans une destination existante (``mv`` le ferait) :
    # la destination est testée avant, le dossier passe par un nom unique, la destination est testée à nouveau juste
    # avant le dernier renommage, puis on vérifie après coup que rien n'a été imbriqué.
    _src=$1
    _dst=$2
    if [ ! -d "$_src" ] || [ -L "$_src" ]; then
        log "renommage refusé : source absente ou lien : $_src"
        return 1
    fi
    if [ -e "$_dst" ] || [ -L "$_dst" ]; then
        log "renommage refusé : la destination existe : $_dst"
        return 1
    fi
    _tmp="$_dst.mv.$$"
    if [ -e "$_tmp" ] || [ -L "$_tmp" ]; then
        log "renommage refusé : le nom temporaire existe : $_tmp"
        return 1
    fi
    if ! mv "$_src" "$_tmp"; then
        log "ÉCHEC du renommage de $_src"
        return 1
    fi
    if [ -e "$_dst" ] || [ -L "$_dst" ]; then
        log "la destination est apparue : $_src est remis en place"
        mv "$_tmp" "$_src" || log "ÉCHEC de la remise en place de $_src (reste sous $_tmp)"
        return 1
    fi
    if ! mv "$_tmp" "$_dst"; then
        log "ÉCHEC de la mise en place de $_dst : $_src est remis en place"
        mv "$_tmp" "$_src" || log "ÉCHEC de la remise en place de $_src (reste sous $_tmp)"
        return 1
    fi
    if [ -e "$_dst/${_tmp##*/}" ]; then
        log "imbrication détectée dans $_dst : annulation"
        mv "$_dst/${_tmp##*/}" "$_src" || log "ÉCHEC de l'annulation (reste sous $_dst/${_tmp##*/})"
        return 1
    fi
    return 0
}

do_kill() {
    # KUT_UPD_KILL : crochet de test (simule un processus qui survit à un signal).
    if [ -n "${KUT_UPD_KILL:-}" ]; then
        "$KUT_UPD_KILL" "$@"
    else
        kill "$@"
    fi
}

app_pids() {
    # Processus lancés depuis CE bundle uniquement : commande dont le début est « <bundle>/Contents/MacOS/ »
    # (chemin exact, jamais un motif large ; ``ps -axo pid=,command=`` filtré par ce préfixe).
    ps -axo pid=,command= 2>/dev/null | while read -r _pid _cmd; do
        case "$_cmd" in
            "$1"/Contents/MacOS/*) [ "$_pid" != "$$" ] && echo "$_pid" ;;
        esac
    done
}

wait_app_gone() {
    _i=0
    _max=$(($2 * 5))
    while [ -n "$(app_pids "$1")" ]; do
        _i=$((_i + 1))
        if [ "$_i" -gt "$_max" ]; then
            return 1
        fi
        sleep 0.2
    done
    return 0
}

stop_app() {
    # SIGTERM, attente (KUT_UPD_TERM_SECONDS, 5 s), puis SIGKILL des processus qui correspondent ENCORE (la liste est
    # relue : un pid réutilisé par un autre programme ne correspond plus), attente (KUT_UPD_KILL_SECONDS, 5 s).
    # Échec (1) si un processus du bundle tourne toujours.
    _pids=$(app_pids "$1")
    if [ -z "$_pids" ]; then
        return 0
    fi
    log "arrêt (SIGTERM) des processus de $1 : $(echo $_pids)"
    for _p in $_pids; do
        do_kill -TERM "$_p" 2>/dev/null
    done
    if wait_app_gone "$1" "$KUT_UPD_TERM_SECONDS"; then
        return 0
    fi
    _pids=$(app_pids "$1")
    log "SIGKILL des processus qui résistent : $(echo $_pids)"
    for _p in $_pids; do
        do_kill -KILL "$_p" 2>/dev/null
    done
    if wait_app_gone "$1" "$KUT_UPD_KILL_SECONDS"; then
        return 0
    fi
    log "processus encore vivants : $(echo $(app_pids "$1"))"
    return 1
}

launch() {
    "$KUT_UPD_OPEN" "$1" >> "$KUT_UPD_LOG" 2>&1
}

restore_if_missing() {
    # Entre les deux renommages : l'application a disparu, la précédente est là.
    if [ ! -e "$KUT_UPD_APP" ] && [ -d "$KUT_UPD_PREVIOUS" ]; then
        if safe_mv "$KUT_UPD_PREVIOUS" "$KUT_UPD_APP"; then
            log "précédente remise en place"
            return 0
        fi
        log "ÉCHEC de la remise en place de la précédente"
        return 1
    fi
    return 0
}

rollback() {
    log "retour arrière : $1"
    PHASE=rollback
    if ! stop_app "$KUT_UPD_APP"; then
        # Rien n'est déplacé ni supprimé tant qu'un processus de la nouvelle version tourne ; la précédente n'est pas lancée.
        log "retour arrière impossible : un processus de la nouvelle version tourne encore ; bundles intacts"
        status failed rollback_impossible
        exit 7
    fi
    if [ -d "$KUT_UPD_PREVIOUS" ]; then
        _failed="$KUT_UPD_PREVIOUS.failed"
        safe_rm "$_failed"
        if [ -e "$KUT_UPD_APP" ]; then
            if ! safe_mv "$KUT_UPD_APP" "$_failed"; then
                log "ÉCHEC : la nouvelle application ne peut pas être écartée"
                status failed rollback_failed
                exit 1
            fi
        fi
        if safe_mv "$KUT_UPD_PREVIOUS" "$KUT_UPD_APP"; then
            log "précédente restaurée"
        else
            log "ÉCHEC : la précédente ne peut pas être restaurée"
            status failed rollback_failed
            exit 1
        fi
        safe_rm "$_failed"
    fi
    safe_rm "$KUT_UPD_STAGING"
    rm -f "$KUT_UPD_STARTED"
    status rolled_back "$1"
    launch "$KUT_UPD_APP"
    exit 1
}

release_lock() {
    # Libère le verrou s'il est à nous (jamais celui d'un autre) ; appelé à toute sortie (trap EXIT).
    if [ "$OWNS_LOCK" = 1 ] && [ "$(cat "$KUT_UPD_LOCK/pid" 2>/dev/null)" = "$$" ]; then
        rm -f "$KUT_UPD_LOCK/pid"
        rmdir "$KUT_UPD_LOCK" 2>/dev/null
    fi
}

acquire_lock() {
    # Verrou exclusif = ``mkdir`` (atomique : un seul créateur), pid du propriétaire écrit dedans. Verrou périmé
    # (propriétaire mort) : le dossier est RENOMMÉ vers un nom unique (un seul assistant y réussit) puis supprimé,
    # jamais supprimé en place ; un verrou vivant n'est jamais pris.
    _attempt=0
    while [ "$_attempt" -lt 5 ]; do
        _attempt=$((_attempt + 1))
        if mkdir "$KUT_UPD_LOCK" 2>/dev/null; then
            if echo $$ > "$KUT_UPD_LOCK/pid"; then
                OWNS_LOCK=1
                return 0
            fi
            rmdir "$KUT_UPD_LOCK" 2>/dev/null
            return 1
        fi
        _owner=$(cat "$KUT_UPD_LOCK/pid" 2>/dev/null)
        _idle=0
        while [ -z "$_owner" ] && [ "$_idle" -lt 5 ]; do
            sleep 0.2                                  # le propriétaire écrit peut-être son pid à l'instant
            _idle=$((_idle + 1))
            _owner=$(cat "$KUT_UPD_LOCK/pid" 2>/dev/null)
        done
        case "$_owner" in *[!0-9]*) _owner= ;; esac
        if [ -n "$_owner" ] && kill -0 "$_owner" 2>/dev/null; then
            log "verrou détenu par le processus $_owner"
            return 1
        fi
        _stale="$KUT_UPD_LOCK.stale.$$"
        safe_rm "$_stale" >/dev/null 2>&1
        if mv "$KUT_UPD_LOCK" "$_stale" 2>/dev/null; then
            _moved=$(cat "$_stale/pid" 2>/dev/null)
            if [ -n "$_moved" ] && [ "$_moved" != "$_owner" ] && kill -0 "$_moved" 2>/dev/null; then
                log "le verrou écarté appartenait à un assistant vivant ($_moved) : remis en place"
                mv "$_stale" "$KUT_UPD_LOCK" 2>/dev/null
                return 1
            fi
            safe_rm "$_stale"
            log "verrou périmé (propriétaire ${_owner:-inconnu}) repris"
        fi
    done
    return 1
}

on_signal() {
    log "signal reçu (phase $PHASE)"
    case "$PHASE" in
        swap) restore_if_missing ;;
        wait_start) rollback signal ;;
    esac
    exit 1
}

# ---- fin des fonctions ----

for _name in KUT_UPD_PID KUT_UPD_APP KUT_UPD_STAGING KUT_UPD_PREVIOUS KUT_UPD_STARTED KUT_UPD_STATUS KUT_UPD_LOG \
             KUT_UPD_LOCK KUT_UPD_VERSION KUT_UPD_FROM; do
    eval "_value=\${$_name:-}"
    if [ -z "$_value" ]; then
        echo "variable manquante : $_name" >&2
        exit 2
    fi
done
for _name in KUT_UPD_APP KUT_UPD_STAGING KUT_UPD_PREVIOUS KUT_UPD_STARTED KUT_UPD_STATUS KUT_UPD_LOG KUT_UPD_LOCK; do
    eval "_value=\$$_name"
    case "$_value" in
        /?*) ;;
        *) echo "chemin absolu attendu : $_name" >&2; exit 2 ;;
    esac
    case "$_value" in
        */../*|*/..|*/./*|*/.|*//*|*/) echo "chemin refusé : $_name" >&2; exit 2 ;;
    esac
done
case "$KUT_UPD_APP" in /?*/Kut-Studio.app) ;; *) echo "chemin d'application refusé" >&2; exit 2 ;; esac
case "${KUT_UPD_STAGING##*/}" in .Kut-Studio-update-?*) ;; *) echo "nom de staging refusé" >&2; exit 2 ;; esac
case "${KUT_UPD_PREVIOUS##*/}" in .Kut-Studio.app.previous-?*) ;; *) echo "nom de sauvegarde refusé" >&2; exit 2 ;; esac
if [ "${KUT_UPD_STAGING%/*}" != "${KUT_UPD_APP%/*}" ] || [ "${KUT_UPD_PREVIOUS%/*}" != "${KUT_UPD_APP%/*}" ]; then
    echo "staging et sauvegarde doivent être à côté de l'application (même volume)" >&2
    exit 2
fi
case "$KUT_UPD_PID" in *[!0-9]*) echo "pid refusé" >&2; exit 2 ;; esac
if [ "$KUT_UPD_PID" -le 1 ]; then
    echo "pid refusé" >&2
    exit 2
fi
case "$KUT_UPD_VERSION$KUT_UPD_FROM" in *[!0-9A-Za-z.+-]*) echo "version refusée" >&2; exit 2 ;; esac

trap release_lock EXIT
trap on_signal HUP INT TERM
if ! acquire_lock; then
    # Une autre mise à jour est en cours : on sort AVANT d'examiner ou de modifier quoi que ce soit.
    log "une autre mise à jour est déjà en cours : rien n'est examiné ni modifié"
    status_if_absent failed already_running
    exit 6
fi
log "début : $KUT_UPD_FROM -> $KUT_UPD_VERSION, application $KUT_UPD_APP, attente du processus $KUT_UPD_PID"

_n=0
_max=$((KUT_UPD_WAIT_SECONDS * 5))
while kill -0 "$KUT_UPD_PID" 2>/dev/null; do
    _n=$((_n + 1))
    if [ "$_n" -ge "$_max" ]; then
        log "l'application ne s'est pas fermée en $KUT_UPD_WAIT_SECONDS s : rien n'est modifié"
        safe_rm "$KUT_UPD_STAGING"
        status failed wait_timeout
        exit 3
    fi
    sleep 0.2
done
log "application fermée"

# État des dossiers (l'exécution peut reprendre après une interruption).
if [ -d "$KUT_UPD_STAGING" ] && [ ! -e "$KUT_UPD_PREVIOUS" ] && [ -d "$KUT_UPD_APP" ]; then
    _state=fresh
elif [ -d "$KUT_UPD_STAGING" ] && [ -d "$KUT_UPD_PREVIOUS" ] && [ ! -e "$KUT_UPD_APP" ]; then
    _state=half_swapped
elif [ ! -e "$KUT_UPD_STAGING" ] && [ -d "$KUT_UPD_PREVIOUS" ] && [ -d "$KUT_UPD_APP" ]; then
    _state=swapped
elif [ ! -e "$KUT_UPD_STAGING" ] && [ -d "$KUT_UPD_PREVIOUS" ] && [ ! -e "$KUT_UPD_APP" ]; then
    log "état : seule la précédente subsiste, remise en place"
    restore_if_missing
    status rolled_back interrupted
    launch "$KUT_UPD_APP"
    exit 1
else
    log "état inattendu, rien n'est modifié"
    status failed unexpected_state
    exit 4
fi
log "état : $_state"

if [ "$_state" != swapped ]; then
    if [ ! -f "$KUT_UPD_STAGING/Contents/Info.plist" ]; then
        log "le staging n'est pas un bundle : rien n'est modifié"
        safe_rm "$KUT_UPD_STAGING"
        status failed staging_invalid
        launch "$KUT_UPD_APP"
        exit 4
    fi
    PHASE=swap
    if [ "$_state" = fresh ]; then
        if ! safe_mv "$KUT_UPD_APP" "$KUT_UPD_PREVIOUS"; then
            log "ÉCHEC du renommage de l'ancienne application : rien n'est modifié"
            safe_rm "$KUT_UPD_STAGING"
            status failed swap_failed
            launch "$KUT_UPD_APP"
            exit 5
        fi
        log "ancienne application renommée en $KUT_UPD_PREVIOUS"
        halt_after renamed_previous
    fi
    if ! safe_mv "$KUT_UPD_STAGING" "$KUT_UPD_APP"; then
        log "ÉCHEC de la mise en place de la nouvelle application"
        restore_if_missing
        safe_rm "$KUT_UPD_STAGING"
        status failed swap_failed
        launch "$KUT_UPD_APP"
        exit 5
    fi
    log "nouvelle application en place"
    halt_after installed
fi

PHASE=wait_start
if [ "$_state" != swapped ]; then
    rm -f "$KUT_UPD_STARTED"
fi
if ! launch "$KUT_UPD_APP"; then
    rollback open_failed
fi
log "relance demandée"
halt_after launched

_n=0
_max=$((KUT_UPD_START_SECONDS * 5))
while ! { [ -f "$KUT_UPD_STARTED" ] && grep -Fq "\"version\":\"$KUT_UPD_VERSION\"" "$KUT_UPD_STARTED"; }; do
    _n=$((_n + 1))
    if [ "$_n" -ge "$_max" ]; then
        rollback start_timeout
    fi
    sleep 0.2
done
log "la nouvelle application a écrit le fichier démarré"
sleep "$KUT_UPD_GRACE_SECONDS"
_apppid=$(sed -n 's/.*"pid":\([0-9][0-9]*\).*/\1/p' "$KUT_UPD_STARTED" 2>/dev/null | head -n 1)
if [ -z "$_apppid" ]; then
    rollback started_marker_invalid
fi
if ! kill -0 "$_apppid" 2>/dev/null; then
    rollback crashed_after_start
fi

status ok started
log "succès : la nouvelle application tourne (processus $_apppid)"
PHASE=done
safe_rm "$KUT_UPD_PREVIOUS" || log "la sauvegarde n'a pas pu être supprimée"
log "terminé"
exit 0
"""


@dataclass(frozen=True)
class HelperPlan:
    """Tout ce que l'assistant doit savoir ; chaque chemin est absolu et dans le dossier de l'application."""

    app: Path
    staging: Path
    previous: Path
    pid: int
    version: str
    from_version: str
    started: Path
    status: Path
    log: Path
    lock: Path
    wait_seconds: int = WAIT_SECONDS
    start_seconds: int = START_SECONDS
    open_command: str = "/usr/bin/open"


def make_plan(
    app: Path,
    *,
    pid: int,
    version: str,
    from_version: str,
    directory: Path | None = None,
) -> HelperPlan:
    """Plan d'installation de ``version`` sur ``app`` (même dossier parent : les renommages sont atomiques)."""
    for text in (version, from_version):
        if not _SAFE_VERSION.fullmatch(text):
            raise ValueError(f"version refusée : {text[:40]!r}")
    if pid <= 0:
        raise ValueError("pid invalide")
    folder = directory if directory is not None else updates_directory()
    return HelperPlan(
        app=app,
        staging=app.parent / f"{STAGING_PREFIX}{pid}",
        previous=app.parent / f"{PREVIOUS_PREFIX}{from_version}",
        pid=pid,
        version=version,
        from_version=from_version,
        started=folder / STARTED_NAME,
        status=folder / STATUS_NAME,
        log=folder / LOG_NAME,
        lock=folder / LOCK_NAME,
    )


def helper_environment(plan: HelperPlan, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environnement minimal de l'assistant : ni ``DYLD_*`` ni ``PYTHON*`` hérités de l'application."""
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": os.environ.get("HOME", ""),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "KUT_UPD_PID": str(plan.pid),
        "KUT_UPD_APP": str(plan.app),
        "KUT_UPD_STAGING": str(plan.staging),
        "KUT_UPD_PREVIOUS": str(plan.previous),
        "KUT_UPD_STARTED": str(plan.started),
        "KUT_UPD_STATUS": str(plan.status),
        "KUT_UPD_LOG": str(plan.log),
        "KUT_UPD_LOCK": str(plan.lock),
        "KUT_UPD_VERSION": plan.version,
        "KUT_UPD_FROM": plan.from_version,
        "KUT_UPD_WAIT_SECONDS": str(plan.wait_seconds),
        "KUT_UPD_START_SECONDS": str(plan.start_seconds),
        "KUT_UPD_OPEN": plan.open_command,
    }
    if extra:
        env.update(extra)
    return env


def write_helper_script(directory: Path, *, pid: int) -> Path:
    """Écrit le script dans un dossier privé (``helper-<pid>``, droits 0700) hors du bundle ; retourne son chemin."""
    folder = directory / f"helper-{pid}"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    script = folder / "install.sh"
    script.write_text(HELPER_SCRIPT, encoding="utf-8")
    script.chmod(0o700)
    return script


def launch_helper(
    plan: HelperPlan,
    *,
    extra_env: Mapping[str, str] | None = None,
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
) -> int:
    """Lance l'assistant détaché (nouvelle session) ; ses sorties vont dans ``install.log`` (recréé). Retourne son pid."""
    require_not_store("Installation de mise à jour")
    plan.log.parent.mkdir(parents=True, exist_ok=True)
    plan.status.unlink(missing_ok=True)
    script = write_helper_script(plan.log.parent, pid=plan.pid)
    with open(plan.log, "wb") as journal:
        process = popen(
            ["/bin/sh", str(script)],
            stdin=subprocess.DEVNULL,
            stdout=journal,
            stderr=subprocess.STDOUT,
            env=helper_environment(plan, extra_env),
            start_new_session=True,
            close_fds=True,
        )
    return int(process.pid)


__all__ = [
    "GRACE_SECONDS",
    "HELPER_SCRIPT",
    "HelperPlan",
    "LOCK_NAME",
    "LOG_NAME",
    "PREVIOUS_PREFIX",
    "STAGING_PREFIX",
    "START_SECONDS",
    "STARTED_NAME",
    "STATUS_NAME",
    "WAIT_SECONDS",
    "helper_environment",
    "launch_helper",
    "make_plan",
    "write_helper_script",
]
