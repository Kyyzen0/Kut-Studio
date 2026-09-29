"""Moteur d'apercu non destructif (tache 30) : partie 1/3."""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass
class PreviewJob:
    key: object
    plan: object
    width: int = 1920
    height: int = 1080
    fps: int = 30
    quality: str = "standard"
    start: float = 0.0
    duration: float = 2.0
    srt_path: str | None = None


@dataclass
class PreviewEngineState:
    pending: int = 0
    running: int = 0
    cached_segments: int = 0
    last_error: str = ""
    paused_for_playback: bool = False


class PreviewEngine:
    def __init__(self, task_queue=None, cache=None, **kwargs):
        from .preview_cache import DiskPreviewCache
        from .task_queue import TaskQueue

        render = kwargs.get("render_fn", None)
        self.tasks = task_queue or TaskQueue()
        self.cache = cache or DiskPreviewCache()
        self.render_fn = render or self._default_render
        self._lock = threading.RLock()
        self._generations = {}
        self._running_count = 0
        self._max_concurrent = max(1, int(kwargs.get("max_concurrent", 1)))
        self._paused = False
        self._state = PreviewEngineState()
        self._listeners = []

    def subscribe(self, callback):
        with self._lock:
            self._listeners.append(callback)

    def _notify(self):
        with self._lock:
            state = PreviewEngineState(
                pending=len(self.tasks),
                running=self._running_count,
                cached_segments=self._state.cached_segments,
                last_error=self._state.last_error,
                paused_for_playback=self._paused,
            )
            listeners = list(self._listeners)
        for callback in listeners:
            try:
                callback(state)
            except Exception:
                pass

    def set_playing(self, playing):
        with self._lock:
            self._paused = bool(playing)
        self._notify()

    def request(self, job):
        from .preview_cache import segment_key_string
        from .task_queue import PRIORITY_BACKGROUND

        key = job.key
        cached = self.cache.lookup(key)
        if cached is not None:
            with self._lock:
                self._state.cached_segments += 1
            self._notify()
            return {"status": "cached", "path": str(cached)}
        token_key = "preview:" + segment_key_string(key)
        with self._lock:
            self._generations[token_key] = self._generations.get(token_key, 0) + 1
            generation = self._generations[token_key]

        def _run(token):
            if token.cancelled:
                return None
            if self._is_obsolete(token_key, generation):
                return None
            with self._lock:
                if self._running_count >= self._max_concurrent:
                    return None
                self._running_count += 1
            self._notify()
            try:
                if self._is_obsolete(token_key, generation):
                    return None
                if token.cancelled:
                    return None
                output = self.render_fn(job, token)
                if output is None:
                    return None
                if self._is_obsolete(token_key, generation):
                    try:
                        import os

                        os.remove(str(output))
                    except OSError:
                        pass
                    return None
                self.cache.store(key, str(output))
                with self._lock:
                    self._state.cached_segments += 1
                return str(output)
            except Exception as exc:
                with self._lock:
                    self._state.last_error = str(exc)
                return None
            finally:
                with self._lock:
                    self._running_count = max(0, self._running_count - 1)
                self._notify()

        self.tasks.submit(token_key, _run, priority=PRIORITY_BACKGROUND)
        self._notify()
        return {"status": "pending", "key": token_key}

    def fallback_source(self, job):
        layers = getattr(job.plan, "video_layers", ())
        for layer in layers:
            start = float(getattr(layer, "timeline_start", 0.0))
            end = float(getattr(layer, "timeline_end", 0.0))
            if start <= float(job.start) < end:
                path = getattr(layer, "source_path", "") or ""
                if path:
                    return path
                return "source://%s" % getattr(layer, "clip_id", "clip")
        if layers:
            path = getattr(layers[0], "source_path", "") or ""
            if path:
                return path
            return "source://%s" % getattr(layers[0], "clip_id", "clip")
        return ""

    def prefetch_around(self, center, jobs):
        ordered = sorted(jobs, key=lambda j: abs(float(j.start) - float(center)))
        results = []
        for job in ordered[:4]:
            results.append(self.request(job))
        return results

    def invalidate_clip(self, clip_id):
        removed = self.cache.invalidate_clip(clip_id)
        with self._lock:
            victims = [k for k in list(self._generations)]
        for key in victims:
            try:
                self.tasks.cancel(key)
            except Exception:
                pass
        self._notify()
        return removed

    def cancel_all(self):
        try:
            self.tasks.cancel_all()
        except Exception:
            pass
        self._notify()

    def pump(self, limit=1):
        if self._paused:
            return 0
        return self.tasks.pump(limit=limit)

    def state(self):
        with self._lock:
            return PreviewEngineState(
                pending=len(self.tasks),
                running=self._running_count,
                cached_segments=self._state.cached_segments,
                last_error=self._state.last_error,
                paused_for_playback=self._paused,
            )

    def _is_obsolete(self, token_key, generation):
        with self._lock:
            return self._generations.get(token_key, 0) != generation

    def _default_render(self, job, token):
        import os
        import subprocess
        import tempfile

        from .filter_graph import build_preview_command

        if token is not None and getattr(token, "cancelled", False):
            return None
        fd, tmp_path = tempfile.mkstemp(prefix="kut-preview-", suffix=".mp4")
        os.close(fd)
        command = build_preview_command(
            job.plan,
            width=job.width,
            height=job.height,
            fps=job.fps,
            quality=job.quality,
            start=job.start,
            duration=job.duration,
            output_path=tmp_path,
            srt_path=job.srt_path,
        )
        try:
            completed = subprocess.run(
                command, capture_output=True, timeout=120, check=False
            )
        except Exception as exc:
            raise RuntimeError("Echec du rendu d'apercu : %s" % exc)
        if completed.returncode != 0:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise RuntimeError("FFmpeg apercu a echoue.")
        return tmp_path


__all__ = ["PreviewEngine", "PreviewEngineState", "PreviewJob"]

