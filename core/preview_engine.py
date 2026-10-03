"""Moteur d'apercu non destructif (tache 30) : partie 1/3.

Le moteur ne fait aucun travail lui-meme : il planifie des segments dans
une :class:`~core.task_queue.TaskQueue`, lit et ecrit le cache disque de
:mod:`core.preview_cache`, et publie son etat aux panneaux Qt. Le rendu
utilise le meme graphe de filtres que l'export
(:mod:`core.filter_graph`), donc le moniteur montre ce que l'export
produira.

Invariants garantis ici :

- un segment deja en cache n'est jamais re-rendu ;
- un rendu en vol qui n'est plus d'actualite (invalidation d'un clip,
  ``cancel_all``) se jette lui-meme au lieu d'ecrire dans le cache :
  une invalidation ne peut donc pas etre « ressuscitee » ;
- une demande identique (meme cle : clip, plage, qualite, empreinte des
  parametres) ne relance rien tant qu'elle est deja planifiee ou en
  cours, sinon chaque tick de l'interface annulerait le rendu precedent ;
- aucun fichier temporaire (segment ou sous-titres) ne survit a un
  rendu, qu'il reussisse, echoue ou soit jete ;
- l'etat publie est toujours relu sous verrou, jamais partage en direct.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass

LOGGER = logging.getLogger("kut_studio.preview")

# Nombre de segments pre-rendus autour de la tete de lecture.
PREFETCH_SEGMENTS = 4

# Cles de clip utilisees pour les segments qui ne dependent pas d'un clip
# unique (trou de timeline). Comme ils peuvent etre touches par n'importe
# quel clip, toute invalidation de clip les concerne aussi : sinon un
# rendu en vol reecrirait un segment devenu faux.
UNATTRIBUTED_CLIP_IDS = ("timeline", "")

# Message unique quand la build FFmpeg ne peut pas incruster les
# sous-titres (le meme diagnostic que l'export).
SUBTITLES_UNSUPPORTED = (
    "La build FFmpeg ne supporte pas le filtre 'subtitles' (libass requis). "
    "Installez un FFmpeg avec libass pour incruster les sous-titres."
)


@dataclass
class PreviewJob:
    key: object
    plan: object
    width: int = 1920
    height: int = 1080
    fps: float = 30
    quality: str = "standard"
    start: float = 0.0
    duration: float = 2.0
    srt_path: str | None = None
    priority: int = 100
    """Urgence dans la file (plus petit = plus urgent), voir :mod:`core.prefetch`."""


@dataclass
class PreviewEngineState:
    pending: int = 0
    running: int = 0
    cached_segments: int = 0
    last_error: str = ""
    paused_for_playback: bool = False


class PreviewEngine:
    """Planificateur de segments d'apercu fideles, mis en cache et annulable."""

    def __init__(self, task_queue=None, cache=None, **kwargs):
        from .preview_cache import DiskPreviewCache
        from .task_queue import TaskQueue

        render = kwargs.get("render_fn", None)
        self.tasks = task_queue or TaskQueue()
        self.cache = cache or DiskPreviewCache()
        self.render_fn = render or self._default_render
        # Le diagnostic libass ne concerne que le rendu FFmpeg par defaut :
        # un ``render_fn`` injecte gere ses entrees comme il l'entend.
        self._uses_default_render = render is None
        self._subtitles_supported = None
        self._lock = threading.RLock()
        # token_key -> generation courante ; un rendu dont la generation
        # a change est perime et se jette sans ecrire.
        self._generations: dict[str, int] = {}
        # token_key -> clip proprietaire (invalidation chirurgicale).
        self._key_clips: dict[str, str] = {}
        # token_key -> jeton de la derniere soumission (annulation ciblee).
        self._tokens: dict[str, object] = {}
        # token_key -> debut du segment (timeline) : sert a abandonner les
        # demandes devenues lointaines quand la tete de lecture bouge.
        self._key_starts: dict[str, float] = {}
        # token_key des rendus deja demarres : seuls les rendus encore
        # en file peuvent etre abandonnes sans gaspiller un travail entame.
        self._started: set[str] = set()
        # Fichiers produits par le rendu par defaut : seuls ceux-la
        # peuvent etre supprimes par le moteur (jamais ceux d'un
        # ``render_fn`` injecte par l'appelant).
        self._temp_outputs: set[str] = set()
        # Fichiers SRT/ASS créés par le rendu par défaut et encore en cours
        # d'utilisation. Les enregistrer permet à ``cancel_all`` de les
        # supprimer aussi quand une fenêtre se ferme pendant un rendu.
        self._temporary_subtitles: set[str] = set()
        # Invalidation globale (``cancel_all``) : incremente l'epoque.
        self._epoch = 0
        # Compteur de generations monotone (jamais remis a zero).
        self._generation_seq = 0
        self._running_count = 0
        self._max_concurrent = max(1, int(kwargs.get("max_concurrent", 1)))
        # Images intermediaires (melange d'images, flux optique) : cache des vecteurs et backend demande.
        self._flow_cache_instance = kwargs.get("flow_cache")
        self._flow_preference = kwargs.get("flow_preference")
        if self._flow_preference is None:
            from .optical_flow import BackendPreference

            self._flow_preference = BackendPreference.AUTO
        self._paused = False
        self._last_error = ""
        self._listeners = []
        self._cached_segments = self._count_cached()

    # ------------------------------------------------------------------
    # Etat publie aux panneaux
    # ------------------------------------------------------------------

    def subscribe(self, callback):
        """Abonne un rappel ``callback(state)``."""
        with self._lock:
            self._listeners.append(callback)

    def _count_cached(self):
        """Nombre de segments actuellement sur disque (0 si inconnu)."""
        stats = getattr(self.cache, "stats", None)
        if not callable(stats):
            return 0
        try:
            return max(0, int(stats().get("entries", 0)))
        except Exception:
            return 0

    def _snapshot(self):
        """Vue coherente de l'etat (a appeler verrou tenu)."""
        return PreviewEngineState(
            pending=len(self.tasks),
            running=self._running_count,
            cached_segments=self._cached_segments,
            last_error=self._last_error,
            paused_for_playback=self._paused,
        )

    def _notify(self):
        with self._lock:
            state = self._snapshot()
            listeners = list(self._listeners)
        for callback in listeners:
            try:
                callback(state)
            except Exception:
                pass

    def set_playing(self, playing):
        """Suspend les rendus d'arriere-plan pendant la lecture.

        Rien n'est perdu : les segments restent en file et repartiront
        au premier ``set_playing(False)``.
        """
        playing = bool(playing)
        with self._lock:
            if self._paused == playing:
                return
            self._paused = playing
        self._notify()

    def request(self, job, priority=None):
        """Planifie le rendu d'un segment.

        ``priority`` (plus petit = plus urgent, voir :mod:`core.task_queue`)
        place le segment courant devant le travail de fond. Une demande
        deja en file est **remontee** si on la redemande avec une
        priorite plus urgente (la tete de lecture s'est rapprochee).

        Returns:
            ``{"status": "cached", "path": ...}`` si le segment est deja
            sur disque, ``{"status": "unavailable", "reason": ...}`` si
            le rendu est impossible sur cette machine (par ex. plan
            sous-titre et FFmpeg sans libass), sinon
            ``{"status": "pending", "key": ...}``. Une demande identique
            deja planifiee ou en cours ne cree aucun doublon (sinon
            chaque tick de l'interface annulerait le rendu precedent,
            qui n'avancerait jamais).
        """
        from .preview_cache import segment_key_string
        from .task_queue import PRIORITY_BACKGROUND

        key = job.key
        if priority is None:
            priority = PRIORITY_BACKGROUND
        cached = self.cache.lookup(key)
        if cached is not None:
            return {"status": "cached", "path": str(cached)}
        if self._needs_unsupported_subtitles(job):
            with self._lock:
                self._last_error = SUBTITLES_UNSUPPORTED
            self._notify()
            return {"status": "unavailable", "reason": "subtitles"}
        token_key = "preview:" + segment_key_string(key)
        with self._lock:
            if token_key in self._generations:
                # Meme cle : deja en file ou en cours de rendu.
                reprioritize = getattr(self.tasks, "reprioritize", None)
                if callable(reprioritize) and token_key not in self._started:
                    reprioritize(token_key, priority)
                return {"status": "pending", "key": token_key}
            # Compteur monotone global : meme apres un oubli de la cle,
            # un rendu plus ancien ne peut pas se croire encore actuel.
            self._generation_seq += 1
            generation = self._generation_seq
            self._generations[token_key] = generation
            self._key_clips[token_key] = str(getattr(key, "clip_id", ""))
            self._key_starts[token_key] = float(getattr(job, "start", 0.0))
            epoch = self._epoch

        def _run(token):
            if not self._claim(token, token_key, epoch, generation):
                return None
            output = None
            try:
                output = self.render_fn(job, token)
                if output is None:
                    return None
                if self._stale(token, token_key, epoch, generation):
                    return None
                path = self.cache.store(key, str(output))
                with self._lock:
                    self._cached_segments += 1
                    # Une erreur transitoire ne doit pas rester collee a
                    # l'etat publie une fois le rendu reparti.
                    self._last_error = ""
                return str(path)
            except Exception as exc:
                with self._lock:
                    self._last_error = str(exc)
                return None
            finally:
                self._release(token_key, generation, output)

        token = self.tasks.submit(token_key, _run, priority=priority)
        with self._lock:
            self._tokens[token_key] = token
        self._notify()
        return {"status": "pending", "key": token_key}

    # ------------------------------------------------------------------
    # Cycle de vie d'un rendu
    # ------------------------------------------------------------------

    def _needs_unsupported_subtitles(self, job):
        """Le plan exige-t-il des sous-titres que FFmpeg ne sait pas incruster ?

        Sans ce garde-fou, chaque tick de l'interface relancerait un
        rendu condamne a l'echec sur une build sans libass. Le diagnostic
        est mis en cache : une seule sonde FFmpeg par moteur.
        """
        if not self._uses_default_render:
            return False
        if getattr(job, "srt_path", None):
            return False
        if getattr(job, "plan", None) is None:
            return False
        if not getattr(job.plan, "subtitle_cues", ()):
            return False
        with self._lock:
            known = self._subtitles_supported
        if known is None:
            from .filter_graph import ffmpeg_supports_subtitles

            try:
                known = bool(ffmpeg_supports_subtitles())
            except Exception:
                # Build inconnue : on laisse FFmpeg trancher au rendu.
                known = True
            with self._lock:
                self._subtitles_supported = known
        return not known

    def _claim(self, token, token_key, epoch, generation):
        """Reserve un creneau ; ``False`` si le rendu n'est plus d'actualite.

        Le nombre de rendus simultanes est borne en amont par
        :meth:`pump` : une tache qui arrive ici est toujours rendue,
        jamais silencieusement abandonnee.
        """
        with self._lock:
            stale = self._stale_locked(token, token_key, epoch, generation)
            if stale:
                self._forget_locked(token_key)
            else:
                self._running_count += 1
                self._started.add(token_key)
        self._notify()
        return not stale

    def _release(self, token_key, generation, output):
        """Nettoie le temporaire, libere le creneau et publie l'etat."""
        if output is not None:
            self._discard_output(output)
        with self._lock:
            self._running_count = max(0, self._running_count - 1)
            if self._generations.get(token_key) == generation:
                self._forget_locked(token_key)
        self._notify()

    def _forget_locked(self, token_key):
        """Oublie une cle terminee / annulee (a appeler verrou tenu)."""
        self._generations.pop(token_key, None)
        self._key_clips.pop(token_key, None)
        self._tokens.pop(token_key, None)
        self._key_starts.pop(token_key, None)
        self._started.discard(token_key)

    def cancel_outside(self, low, high):
        """Abandonne les segments **en file** dont le debut est hors de ``[low, high]``.

        Appele quand la tete de lecture se deplace vite : les segments
        planifies pour l'ancienne position ne serviront plus et ne
        doivent ni occuper la file ni remplir le cache. Un rendu deja
        demarre n'est pas interrompu (le travail est entame, le resultat
        pourra resservir). Retourne le nombre de demandes abandonnees.
        """
        low, high = float(low), float(high)
        with self._lock:
            victims = [
                token_key
                for token_key, start in self._key_starts.items()
                if token_key not in self._started and not (low <= start <= high)
            ]
            tokens = [
                self._tokens[token_key]
                for token_key in victims
                if token_key in self._tokens
            ]
            for token_key in victims:
                self._forget_locked(token_key)
        cancel_key = getattr(self.tasks, "cancel_key", None)
        if callable(cancel_key):
            for token_key in victims:
                try:
                    cancel_key(token_key)
                except Exception:
                    pass
        for token in tokens:
            try:
                token.cancel()
            except Exception:
                pass
        if victims:
            self._notify()
        return len(victims)

    def pending_starts(self):
        """Debuts (timeline) des segments encore en file, tries (diagnostic, tests)."""
        with self._lock:
            return sorted(
                start
                for token_key, start in self._key_starts.items()
                if token_key not in self._started
            )

    def _stale(self, token, token_key, epoch, generation):
        """Le rendu a-t-il ete remplace, annule ou invalide ?"""
        with self._lock:
            return self._stale_locked(token, token_key, epoch, generation)

    def _stale_locked(self, token, token_key, epoch, generation):
        """Version verrou tenu de :meth:`_stale`."""
        if token is not None and getattr(token, "cancelled", False):
            return True
        if self._epoch != epoch:
            return True
        return self._generations.get(token_key) != generation

    def _discard_output(self, path):
        """Supprime un temporaire produit par le rendu par defaut.

        Un ``render_fn`` injecte par l'appelant garde la propriete de ses
        fichiers : seuls les chemins crees ici sont supprimables.
        """
        name = str(path)
        with self._lock:
            owned = name in self._temp_outputs
            self._temp_outputs.discard(name)
        if not owned:
            return False
        self._remove_file(name)
        return True

    @staticmethod
    def _remove_file(path):
        """Suppression au mieux d'un fichier temporaire."""
        if not path:
            return False
        try:
            os.remove(str(path))
            return True
        except OSError:
            return False

    def fallback_source(self, job):
        """Media source a afficher tant que le segment n'est pas pret.

        ``plan.video_layers`` est ordonne de bas en haut : la derniere
        couche couvrante est donc celle qui apparait au-dessus a cet
        instant (et non la premiere, qui peut etre cachee).
        """
        layers = tuple(getattr(job.plan, "video_layers", ()) or ())
        if not layers:
            return ""
        start = float(job.start)
        covering = []
        for layer in layers:
            low = float(getattr(layer, "timeline_start", 0.0))
            high = float(getattr(layer, "timeline_end", 0.0))
            if low <= start < high:
                covering.append(layer)
        if covering:
            return self._layer_source(covering[-1])
        # Aucune couche a cet instant : la derniere commencee avant la
        # tete de lecture, sinon la premiere du plan.
        started = [
            layer
            for layer in layers
            if float(getattr(layer, "timeline_start", 0.0)) <= start
        ]
        if started:
            latest = max(
                started,
                key=lambda layer: float(getattr(layer, "timeline_start", 0.0)),
            )
            return self._layer_source(latest)
        return self._layer_source(layers[0])

    @staticmethod
    def _layer_source(layer):
        """Chemin du media d'une couche, sinon un identifiant de repli."""
        path = getattr(layer, "source_path", "") or ""
        if path:
            return str(path)
        clip_id = getattr(layer, "clip_id", "")
        return "source://%s" % clip_id if clip_id else ""

    def prefetch_around(self, center, jobs):
        """Planifie les segments les plus proches de ``center``.

        Les demandes deja en file ou en cours sont ignorees par
        :meth:`request` : appeler cette methode a chaque tick de
        l'interface ne relance donc jamais un rendu en vol.
        """
        ordered = sorted(jobs, key=lambda j: abs(float(j.start) - float(center)))
        return [self.request(job) for job in ordered[:PREFETCH_SEGMENTS]]

    def invalidate_clip(self, clip_id):
        """Invalide les segments d'un clip et arrete leur rendu en cours.

        Les fichiers du clip sont supprimes et les rendus en vol
        deviennent perimes : ils se jettent d'eux-memes au lieu de
        reecrire le disque juste apres l'invalidation. Les segments non
        attribues a un clip (cle ``timeline``) sont traites comme
        dependants de ce clip, donc invalides eux aussi. Un ``clip_id``
        vide invalide tout le cache d'apercu.

        Returns:
            Le nombre de fichiers supprimes.
        """
        clip = str(clip_id or "")
        removed = 0
        if clip:
            invalidate = getattr(self.cache, "invalidate_clip", None)
            if callable(invalidate):
                for bucket in [clip] + [
                    name for name in UNATTRIBUTED_CLIP_IDS if name and name != clip
                ]:
                    try:
                        removed += int(invalidate(bucket))
                    except Exception:
                        pass
        else:
            invalidate_all = getattr(self.cache, "invalidate_all", None)
            if callable(invalidate_all):
                try:
                    removed = int(invalidate_all())
                except Exception:
                    removed = 0
        with self._lock:
            victims = [
                token_key
                for token_key, owner in list(self._key_clips.items())
                if not clip or owner == clip or owner in UNATTRIBUTED_CLIP_IDS
            ]
            tokens = [
                self._tokens[token_key]
                for token_key in victims
                if token_key in self._tokens
            ]
            for token_key in victims:
                self._forget_locked(token_key)
            self._cached_segments = max(0, self._cached_segments - removed)
        # Hors verrou : TaskQueue appelle le corps d'une tache sans tenir
        # le sien, donc annuler ici ne peut pas interbloquer. La methode
        # est ``cancel_key`` (un nom errone levait un AttributeError
        # silencieux : l'annulation ciblee ne faisait rien).
        cancel_key = getattr(self.tasks, "cancel_key", None)
        if callable(cancel_key):
            for token_key in victims:
                try:
                    cancel_key(token_key)
                except Exception:
                    pass
        for token in tokens:
            try:
                token.cancel()
            except Exception:
                pass
        self._notify()
        return removed

    def cancel_all(self):
        """Arrete tout rendu d'apercu et perime les rendus en cours."""
        with self._lock:
            self._epoch += 1
            self._generations.clear()
            self._key_clips.clear()
            tokens = list(self._tokens.values())
            self._tokens.clear()
            self._key_starts.clear()
            self._started.clear()
            subtitles = tuple(self._temporary_subtitles)
            self._temporary_subtitles.clear()
        try:
            self.tasks.cancel_all()
        except Exception:
            pass
        # Les jetons des rendus déjà lancés : sans eux FFmpeg continuait jusqu'à son délai (120 s)
        # après la fermeture ou le changement de projet, et laissait son fichier temporaire.
        for token in tokens:
            try:
                token.cancel()
            except Exception:
                pass
        for path in subtitles:
            self._remove_file(path)
        self._notify()

    def pump(self, limit=1):
        """Execute des segments en file, sans depasser la concurrence.

        Les creneaux disponibles bornent le lot : une tache en file
        n'est jamais abandonnee, elle attend son tour. Deux threads qui
        pompent (l'interface et un ``QueueWorker``) ne lancent donc
        jamais plus de ``max_concurrent`` rendus a la fois.
        """
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 1
        with self._lock:
            if self._paused or limit <= 0:
                return 0
            slots = self._max_concurrent - self._running_count
        if slots <= 0:
            return 0
        return self.tasks.pump(limit=min(limit, slots))

    def state(self):
        """Etat courant : file, creneaux, cache, derniere erreur."""
        with self._lock:
            return self._snapshot()

    def _default_render(self, job, token):
        """Rend un segment via FFmpeg, graphe de filtres identique a l'export.

        Les sous-titres du plan sont ecrits dans un fichier temporaire
        (meme format que l'export) quand l'appelant n'en fournit pas :
        sans cela, tout projet sous-titre echouerait ici.

        Les medias sont decodes selon :mod:`core.decode_policy` (materiel si
        valide et utile) ; un echec avec decodage materiel est relance une
        fois en CPU (repli transparent, compte dans les diagnostics). FFmpeg
        est tue des que le jeton est annule : un segment perime ne garde pas
        le processeur.
        """
        import tempfile

        from .decode_policy import DecodePurpose, run_with_decode_fallback
        from .filter_graph import build_preview_command

        subtitle_path = None
        owns_subtitle = False
        tmp_path = None
        graph_files: list[str] = []
        media = _plan_media_paths(job.plan)
        try:
            if token is not None and getattr(token, "cancelled", False):
                return None
            subtitle_path = job.srt_path or self._write_subtitles(job.plan)
            owns_subtitle = bool(subtitle_path and subtitle_path != job.srt_path)
            if owns_subtitle:
                with self._lock:
                    self._temporary_subtitles.add(str(subtitle_path))
            prepared = self._prepare_interpolation(job, token)
            if prepared is False:
                return None                   # annulé pendant le calcul des images intermédiaires
            fd, tmp_path = tempfile.mkstemp(prefix="kut-preview-", suffix=".mp4")
            os.close(fd)

            def build(args_for):
                for graph_file in graph_files:  # un graphe deja ecrit par l'essai precedent
                    self._remove_file(graph_file)
                graph_files.clear()
                return build_preview_command(
                    job.plan,
                    width=job.width,
                    height=job.height,
                    fps=job.fps,
                    quality=job.quality,
                    start=job.start,
                    duration=job.duration,
                    output_path=tmp_path,
                    srt_path=subtitle_path,
                    temporary_files=graph_files,
                    input_args=lambda path: args_for(path) if path in media else (),
                    prepared=prepared or None,
                )

            def run(command):
                code, errors = _run_cancellable(command, token, timeout=120.0)
                if token is not None and getattr(token, "cancelled", False):
                    return 0, ""  # une annulation n'est pas une panne du décodeur : pas de repli
                return code, errors

            returncode, stderr, _fell_back = run_with_decode_fallback(
                build,
                run,
                paths=sorted(media),
                purpose=DecodePurpose.SEGMENT,
            )
        except Exception as exc:
            self._remove_file(tmp_path)
            raise RuntimeError("Echec du rendu d'apercu : %s" % exc) from exc
        except BaseException:
            # Interruption (fermeture de l'application, Ctrl-C) : meme
            # nettoyage, aucun temporaire ne doit survivre.
            self._remove_file(tmp_path)
            raise
        finally:
            for graph_file in graph_files:  # graphe trop long passé par fichier
                self._remove_file(graph_file)
            # Le fichier de sous-titres n'a servi qu'au filtre libass.
            if owns_subtitle and subtitle_path:
                with self._lock:
                    self._temporary_subtitles.discard(str(subtitle_path))
                self._remove_file(subtitle_path)
        if token is not None and getattr(token, "cancelled", False):
            self._remove_file(tmp_path)
            return None
        if returncode != 0:
            self._remove_file(tmp_path)
            detail = (stderr or "").strip()
            LOGGER.error("Aperçu fidèle : FFmpeg a échoué (code %s) : %s", returncode, detail.splitlines()[-1] if detail else "sans détail")
            raise RuntimeError(
                "FFmpeg apercu a echoue." + (" " + detail[-400:] if detail else "")
            )
        try:
            produced = os.path.getsize(tmp_path)
        except OSError:
            produced = 0
        if produced <= 0:
            # Code de sortie 0 mais rien d'ecrit (le fichier temporaire vient de ``mkstemp``) : mis en cache, ce
            # segment vide serait servi tel quel pendant sept jours sans jamais etre refait.
            self._remove_file(tmp_path)
            raise RuntimeError("FFmpeg apercu n'a produit aucun fichier.")
        # Le segment ne sera supprimable qu'une fois recopie dans le
        # cache : on l'enregistre comme appartenant au moteur.
        with self._lock:
            self._temp_outputs.add(str(tmp_path))
        return tmp_path

    def _prepare_interpolation(self, job, token):
        """Images intermediaires (melange d'images, flux optique) des seuls ticks du segment.

        L'apercu fidele garde la couche entiere et ne decoupe qu'a la sortie (``-ss`` / ``-t``) : on ne fabrique que la
        fenetre du segment, le reste du clip est du noir jete. Les vecteurs de mouvement, eux, sont ranges dans le cache :
        le segment suivant les relit. Retourne les flux par clip (``{}`` si rien n'est a fabriquer) ou ``False`` si annule.
        """
        from .filter_graph import preview_output_size
        from .retime_layers import plan_needs_preparation, prepare_plan
        from .retime_prepare import PrepareCancelled

        width, height = preview_output_size(job.width, job.height, job.quality)
        window = (float(job.start), float(job.start) + float(job.duration or 0.0))
        preference = self._flow_preference
        if not plan_needs_preparation(job.plan, width, height, job.fps, preference, window):
            return {}
        try:
            return prepare_plan(
                job.plan, width, height, job.fps, self._flow_cache(), preference=preference, window=window,
                cancelled=lambda: token is not None and bool(getattr(token, "cancelled", False)),
            ).streams
        except PrepareCancelled:
            return False

    def _flow_cache(self):
        if self._flow_cache_instance is None:
            from .flow_cache import FlowCache

            self._flow_cache_instance = FlowCache()
        return self._flow_cache_instance

    def _write_subtitles(self, plan):
        """Ecrit le SRT/ASS du plan ; ``None`` s'il n'y a aucun sous-titre."""
        if not getattr(plan, "subtitle_cues", ()):
            return None
        from .filter_graph import write_subtitle_file

        return write_subtitle_file(plan)


def _plan_media_paths(plan) -> set[str]:
    """Fichiers video du plan (et de ses sequences imbriquees) : seuls eux sont decodes en materiel."""
    paths: set[str] = set()
    plans = [plan] + [entry.plan for entry in getattr(plan, "nested_sequences", ()) or ()]
    for current in plans:
        for layer in getattr(current, "video_layers", ()) or ():
            path = getattr(layer, "source_path", "")
            if path and not getattr(layer, "nested_key", ""):
                paths.add(str(path))
    return paths


def _run_cancellable(command, token, *, timeout: float):
    """Lance FFmpeg ; le tue si ``token`` est annule ou si le delai expire.

    Retourne ``(code, stderr)``. Un rendu perime (tete de lecture partie,
    clip modifie, fermeture) libere ainsi le processeur en quelques
    dizaines de millisecondes au lieu de finir un segment inutile.
    """
    import subprocess
    import threading
    import time

    from .process_supervisor import supervised_popen

    # Supervisé : ce FFmpeg meurt aussi avec l'application tuée brutalement (sinon jusqu'à ``timeout``).
    with supervised_popen(list(command), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) as process:
        stderr_pipe = process.stderr
        if stderr_pipe is None:                              # jamais : ``PIPE`` demandé ci-dessus
            raise RuntimeError("FFmpeg a été lancé sans sa sortie d'erreur.")
        chunks: list[bytes] = []
        drain = threading.Thread(target=lambda: chunks.append(stderr_pipe.read()), daemon=True)
        drain.start()
        deadline = time.monotonic() + float(timeout)
        killed = False
        while process.poll() is None:
            if (token is not None and getattr(token, "cancelled", False)) or time.monotonic() > deadline:
                process.kill()
                killed = True
                break
            try:
                process.wait(timeout=0.05)
            except subprocess.TimeoutExpired:
                pass
        process.wait()
        drain.join(timeout=2.0)
    stderr = b"".join(c for c in chunks if c).decode("utf-8", "replace")
    if killed and not stderr:
        stderr = "rendu interrompu"
    return int(process.returncode or (1 if killed else 0)), stderr


__all__ = ["PreviewEngine", "PreviewEngineState", "PreviewJob"]
