"""Génération **déterministe** des médias de test : FFmpeg (lavfi) pour l'image, NumPy pour le son, une graine par média.

Aucun média n'est commité : le dossier de travail de la recette (hors du dépôt) les reçoit, avec un ``manifest.json`` qui
porte, par média, l'empreinte de sa **recette** (paramètres + version du générateur : identique sur toutes les machines) et
le SHA-256 du **fichier** produit. Un média est réutilisé seulement si sa recette n'a pas changé ET si le SHA-256 du fichier
sur disque est celui du manifeste (fichier tronqué ou modifié : régénéré). Les empreintes de fichier ne sont comparables
d'une machine à l'autre que pour la même version de FFmpeg (l'encodeur change les octets sans changer l'image) ; le rapport
note la version de FFmpeg avec elles.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import GENERATOR_VERSION, audio
from .env import ffmpeg_has, ffmpeg_version
from .scene3d import scene_document

MANIFEST_NAME = "manifest.json"
KINDS = ("video", "wav", "scene3d")
VIDEO_THREADS = 4
"""Nombre de fils de x264 fixé : x264 donne la même sortie pour un même nombre de fils, pas d'un nombre à l'autre."""


class MediaError(RuntimeError):
    """Un média n'a pas pu être généré (outil absent, FFmpeg en échec)."""


@dataclass(frozen=True)
class MediaItem:
    key: str
    path: str                       # relatif au dossier du parcours, séparateur « / »
    kind: str                       # « video » | « wav » | « scene3d »
    params: dict[str, Any] = field(default_factory=dict)

    def digest(self) -> str:
        return _sha256_text(json.dumps({"v": GENERATOR_VERSION, "kind": self.kind, "params": self.params}, sort_keys=True))


def recipe_digest(items: Sequence[MediaItem]) -> str:
    """Empreinte de la recette entière (paramètres de tous les médias) : identique partout, sans FFmpeg ni NumPy."""
    return _sha256_text(json.dumps([[item.key, item.path, item.digest()] for item in items], sort_keys=True))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest(directory: Path) -> dict[str, Any]:
    try:
        data = json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def verify(items: Sequence[MediaItem], directory: Path) -> dict[str, str]:
    """État de chaque média : ``ok``, ``missing``, ``stale`` (recette changée), ``modified`` (fichier différent du manifeste)."""
    manifest = load_manifest(directory)
    entries = manifest.get("items", {}) if isinstance(manifest.get("items"), dict) else {}
    states: dict[str, str] = {}
    for item in items:
        target = directory / item.path
        entry = entries.get(item.key)
        if not target.is_file():
            states[item.key] = "missing"
        elif not isinstance(entry, dict) or entry.get("recipe") != item.digest():
            states[item.key] = "stale"
        elif entry.get("bytes") != target.stat().st_size or entry.get("sha256") != file_sha256(target):
            states[item.key] = "modified"
        else:
            states[item.key] = "ok"
    return states


def generate(
    items: Sequence[MediaItem],
    directory: Path,
    *,
    ffmpeg: str | None,
    progress: Callable[[str], None] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Génère (ou réutilise) les médias dans ``directory`` et écrit le manifeste ; rend le manifeste."""
    say = progress or (lambda message: None)
    directory.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    states = {item.key: "missing" for item in items} if force else verify(items, directory)
    previous = load_manifest(directory)
    current_ffmpeg = ffmpeg_version(ffmpeg)
    if previous and previous.get("ffmpeg") != current_ffmpeg:
        # Autre chaîne d'outils : les vidéos déjà encodées gardent leurs octets mais pas leur provenance ; on les régénère plutôt
        # que d'attribuer d'anciens SHA-256 à la version courante (les références sont qualifiées par version de FFmpeg).
        for item in items:
            if item.kind == "video" and states[item.key] == "ok":
                states[item.key] = "toolchain"
    entries: dict[str, Any] = dict(previous.get("items", {})) if isinstance(previous.get("items"), dict) else {}
    generated: list[str] = []
    encoder = str(previous.get("video_encoder", ""))
    cache: dict[Any, Any] = {}
    for item in items:
        if states[item.key] == "ok":
            say(f"  media {item.key}: réutilisé ({entries[item.key]['bytes']} octets)")
            continue
        if item.kind not in KINDS:
            raise MediaError(f"Genre de média inconnu : {item.kind!r}")
        target = directory / item.path
        target.parent.mkdir(parents=True, exist_ok=True)
        began = time.monotonic()
        temporary = target.with_name(f".{target.stem}.partial{target.suffix}")
        try:
            if item.kind == "video":
                encoder = _make_video(item, temporary, ffmpeg, cache)
            elif item.kind == "wav":
                _make_wav(item, temporary)
            else:
                temporary.write_text(json.dumps(scene_document(item.params), indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        entries[item.key] = {
            "path": item.path, "kind": item.kind, "recipe": item.digest(), "bytes": target.stat().st_size,
            "sha256": file_sha256(target), "seconds": round(time.monotonic() - began, 3),
        }
        generated.append(item.key)
        say(f"  media {item.key}: généré en {entries[item.key]['seconds']} s ({entries[item.key]['bytes']} octets)")
    cache.clear()
    manifest = {
        "generator_version": GENERATOR_VERSION, "recipe_digest": recipe_digest(items), "ffmpeg": current_ffmpeg,
        "video_encoder": encoder, "items": {item.key: entries[item.key] for item in items}, "generated": generated,
        "reused": [item.key for item in items if item.key not in generated],
        "generation_seconds": round(time.monotonic() - started, 3),
        "total_bytes": sum(entries[item.key]["bytes"] for item in items),
    }
    (directory / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


# --- Son ---------------------------------------------------------------------------------------------------------------


def _master(spec: dict[str, Any], cache: dict[Any, Any]) -> Any:
    """Le son « de la salle », commun à toutes les caméras d'un plateau (calculé une fois par génération)."""
    key = ("room", spec["seed"], spec["master_seconds"], tuple(spec.get("claps", ())))
    if key not in cache:
        cache[key] = audio.speech(spec["seed"], spec["master_seconds"], clap_times=tuple(spec.get("claps", ())))
    return cache[key]


def _audio_signal(spec: dict[str, Any], cache: dict[Any, Any]) -> tuple[Any, int]:
    kind = spec["kind"]
    if kind == "room":
        master = _master(spec, cache)
        first = int(round(spec["start"] * audio.SPEECH_RATE))
        length = int(round(spec["duration"] * audio.SPEECH_RATE))
        window = master[first:first + length]
        if len(window) < length:
            window = _pad_to(window, length)
        return window + audio.mic_noise(spec["mic_seed"], spec["duration"]), audio.SPEECH_RATE
    if kind == "ambient":
        seconds = spec["duration"]
        return audio.pad(spec["seed"], seconds) * 0.6 + audio.mic_noise(spec["seed"], seconds, audio.MUSIC_RATE), audio.MUSIC_RATE
    raise MediaError(f"Signal sonore inconnu : {kind!r}")


def _pad_to(signal: Any, length: int) -> Any:
    import numpy as np

    return np.concatenate([signal, np.zeros(length - len(signal), dtype=signal.dtype)])


def _make_wav(item: MediaItem, target: Path) -> None:
    spec = item.params
    signal = spec["signal"]
    seconds = float(spec["duration"])
    if signal == "speech":
        data, rate = audio.speech(spec["seed"], seconds), audio.SPEECH_RATE
    elif signal == "music":
        data, rate = audio.music(spec["seed"], seconds, float(spec["bpm"])), audio.MUSIC_RATE
    elif signal == "pad":
        data, rate = audio.pad(spec["seed"], seconds), audio.MUSIC_RATE
    else:
        raise MediaError(f"Signal sonore inconnu : {signal!r}")
    audio.write_wav(target, data, rate)


# --- Image -------------------------------------------------------------------------------------------------------------


def video_encoder_args(ffmpeg: str | None) -> tuple[str, list[str]]:
    """Encodeur de génération : x264 rapide à nombre de fils fixé, sinon MPEG-4 (FFmpeg sans libx264)."""
    if ffmpeg_has(ffmpeg, "encoders", "libx264"):
        return "libx264", ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-threads", str(VIDEO_THREADS), "-g", "50",
                           "-pix_fmt", "yuv420p"]
    return "mpeg4", ["-c:v", "mpeg4", "-q:v", "5", "-g", "50", "-pix_fmt", "yuv420p"]


_BANDS = ("0xd04040", "0x40a0d0", "0x50c060", "0xd0b030", "0xa050d0", "0xe08030")


def video_source(spec: dict[str, Any]) -> str:
    """Graphe lavfi d'une vidéo de test : le motif ``testsrc2`` (déterministe, contrairement à ``gradients`` dont deux
    exécutions donnent des images différentes), calculé à la moitié de la taille puis agrandi, décalé de ``seed`` pour que deux
    plans ne se ressemblent pas, teinté, avec une bande de couleur propre au plan (on reconnaît l'angle d'un coup d'œil)."""
    width, height, seed = int(spec["width"]), int(spec["height"]), int(spec["seed"])
    divisor = int(spec.get("divisor", 2))
    skip = round((seed % 7) * 1.3, 3)
    seconds = float(spec["duration"])
    chain = [f"testsrc2=size={width // divisor}x{height // divisor}:rate={spec['fps']}:duration={round(seconds + skip, 6)}"]
    if skip:
        chain.append(f"trim=start={skip},setpts=PTS-STARTPTS")
    chain += [f"scale={width}:{height}:flags=bicubic", f"hue=h={(seed * 37) % 360}:s={1.0 + (seed % 4) * 0.12:.2f}",
              f"drawbox=x=0:y=0:w=iw:h=ih/10:color={_BANDS[seed % len(_BANDS)]}@1:t=fill", "format=yuv420p"]
    return ",".join(chain)


def video_command(item: MediaItem, target: Path, wav: Path | None, ffmpeg: str, encoder_args: Sequence[str]) -> list[str]:
    spec = item.params
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi", "-i", video_source(spec)]
    if wav is not None:
        command += ["-i", str(wav)]
    command += ["-map", "0:v:0"] + (["-map", "1:a:0"] if wav is not None else [])
    command += list(encoder_args)
    if wav is not None:
        command += ["-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-ac", "1"]
    command += ["-t", str(spec["duration"]), "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
                "-map_metadata", "-1", "-movflags", "+faststart", "-f", "mp4", str(target)]
    return command


def _make_video(item: MediaItem, target: Path, ffmpeg: str | None, cache: dict[Any, Any]) -> str:
    if not ffmpeg:
        raise MediaError("FFmpeg est introuvable : impossible de générer les vidéos de test.")
    encoder, encoder_args = video_encoder_args(ffmpeg)
    spec = item.params
    with tempfile.TemporaryDirectory(prefix="recette-audio-") as scratch:
        wav: Path | None = None
        if spec.get("audio"):
            signal, rate = _audio_signal(spec["audio"], cache)
            wav = Path(scratch) / "audio.wav"
            audio.write_wav(wav, signal, rate)
        done = subprocess.run(video_command(item, target, wav, ffmpeg, encoder_args), capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise MediaError(f"FFmpeg a échoué pour {item.key} (code {done.returncode}) : {done.stderr.strip()[-400:]}")
    return encoder
