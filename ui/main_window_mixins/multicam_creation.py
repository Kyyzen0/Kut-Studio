"""Méthodes de ``MainWindow`` regroupées : création d'une séquence Multicam et synchronisation des angles.

Parcours voulu (``docs/multicam.md``) : sélectionner des rushs, clic droit, « Créer une séquence Multicam… », choisir la
méthode, la fenêtre analyse puis crée la source et place un segment sur la timeline. La règle est dans :mod:`core.multicam_ops` ;
ce mixin interroge l'utilisateur, lance l'analyse sonore sur la file d'analyses (jamais dans le thread de l'interface) et
enregistre **une** entrée d'historique pour toute la création.

Méthodes : par le son (analyse locale, résultat dit honnêtement), par le timecode (un média sans timecode ne bloque rien :
il est placé au début), par les repères, au début des clips, aux positions actuelles de la timeline, ou à régler à la main.
La boîte de résultat n'apparaît que si elle sert : synchronisation incertaine ou ratée, ou politique audio ambiguë.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QProgressDialog

from core.audio_sync import AudioSyncJob, SyncResult, SyncSource
from core.media_describe import describe_media
from core.multicam_model import AudioMode, MulticamAudio, SyncMethod, SyncStatus
from core.multicam_naming import suggest_angle_names
from core.multicam_ops import (
    AngleSpec,
    MulticamError,
    SyncOutcome,
    create_multicam_from_clips,
    create_multicam_source,
    insert_multicam_clip,
    suggest_audio_policy,
)
from core.project_model import Clip, MediaAsset
from core.timecode import asset_start_seconds
from ui import i18n
from ui.multicam_dialogs import (
    CreationChoice,
    MulticamCreateDialog,
    SourceRow,
    SummaryRow,
    SyncSummaryDialog,
)

LOGGER = logging.getLogger("kut_studio.multicam")

_SYNC_FAILURES = {SyncStatus.UNCERTAIN, SyncStatus.FAILED}


@dataclass
class _Source:
    """Une source de la création : média de la bibliothèque, ou clip de la timeline."""

    row: SourceRow
    asset: MediaAsset | None = None
    clip: Clip | None = None
    track_type: str = "video"

    @property
    def key(self) -> str:
        return self.row.key


@dataclass
class _PendingSync:
    job: AudioSyncJob
    dialog: QProgressDialog
    timer: QTimer
    finish: object
    names: dict[str, str] = field(default_factory=dict)


def _main_window():
    import ui.main_window as main_window

    return main_window


class MulticamCreationMixin:
    """Mixin de ``MainWindow`` (création Multicam et synchronisation)."""

    def _init_multicam_creation(self) -> None:
        self._multicam_syncs: dict[str, _PendingSync] = {}
        self.project_panel.multicam_create_requested.connect(self.create_multicam_from_assets)
        self.timeline_panel.multicam_create_requested.connect(self.create_multicam_from_timeline_selection)

    # ------------------------------------------------------------------
    # Sources
    # ------------------------------------------------------------------

    def _media_row(self, asset: MediaAsset, name: str, *, offset_seconds: float = 0.0) -> SourceRow:
        description = dict(describe_media(asset, decimal=","))
        short = " · ".join(description[key] for key in ("resolution", "fps", "timecode") if key in description)
        tooltip = "\n".join(
            f"{i18n.translate('multicam.info.' + key)} : {value}" for key, value in description.items()
        )
        start = asset_start_seconds(asset)
        return SourceRow(
            key=asset.id, name=name, kind="audio" if asset.media_type == "audio" else "video", info=short,
            tooltip=tooltip, has_audio=bool(asset.has_audio),
            start_seconds=None if start is None else start + offset_seconds,
        )

    def _sources_from_assets(self, asset_ids: list[str]) -> list[_Source]:
        assets = [asset for asset_id in asset_ids
                  for asset in self.project.media_assets if asset.id == asset_id and asset.media_type in {"video", "audio"}]
        names = suggest_angle_names(
            assets, angle_word=i18n.translate("multicam.angle_word"), audio_word=i18n.translate("multicam.audio_word"),
        )
        return [
            _Source(self._media_row(asset, name), asset=asset, track_type="audio" if asset.media_type == "audio" else "video")
            for asset, name in zip(assets, names)
        ]

    def _sources_from_clips(self, clip_ids: list[str]) -> list[_Source]:
        timeline = self.timeline_panel
        lookup = {clip.id: (clip, track) for track in self.project.tracks for clip in track.clips}
        assets = {asset.id: asset for asset in self.project.media_assets}
        markers = sorted(marker.time_seconds for marker in self.project.markers)
        found = [(lookup[clip_id][0], lookup[clip_id][1]) for clip_id in clip_ids if clip_id in lookup]
        found.sort(key=lambda pair: (pair[1].type != "video", pair[0].timeline_start))
        sources: list[_Source] = []
        for clip, track in found:
            asset = assets.get(clip.asset_id)
            inside = [m for m in markers if clip.timeline_start <= m < clip.timeline_start + clip.duration]
            marker_offset = clip.timeline_start - inside[0] if inside else None
            if asset is not None:
                row = self._media_row(asset, clip.label or asset.name, offset_seconds=clip.source_in)
            else:                                       # clip imbriqué : pas de fichier, donc ni son à analyser ni timecode
                row = SourceRow(key=clip.id, name=clip.label or clip.id, kind="video", has_audio=False)
            row.key = clip.id
            row.marker_offset = marker_offset
            sources.append(_Source(row, asset=asset, clip=clip, track_type=track.type))
        del timeline
        return sources

    # ------------------------------------------------------------------
    # Entrées utilisateur
    # ------------------------------------------------------------------

    def create_multicam_from_assets(self, asset_ids: list[str], *, choice: CreationChoice | None = None):
        """Menu de la bibliothèque : « Créer une séquence Multicam… » sur des médias sélectionnés."""
        sources = self._sources_from_assets(list(asset_ids))
        if len(sources) < 2:
            self._report_edit_refused(i18n.translate("multicam.message.need_two"))
            return None
        chosen = {source.key for source in sources}
        addable = [
            row for row in (
                self._sources_from_assets([asset.id])[0].row
                for asset in self.project.media_assets if asset.id not in chosen and asset.media_type in {"video", "audio"}
            )
        ]
        if choice is None:
            choice = self._ask_multicam_choice(
                [source.row for source in sources], from_timeline=False, addable=addable,
            )
            if choice is None:
                return None
        by_key = {source.key: source for source in sources}
        for row in addable:                             # sources ajoutées dans la boîte
            if row.key in {key for key, _ in choice.sources} and row.key not in by_key:
                asset = next(a for a in self.project.media_assets if a.id == row.key)
                by_key[row.key] = _Source(row, asset=asset, track_type="audio" if asset.media_type == "audio" else "video")
        ordered = [by_key[key] for key, _name in choice.sources if key in by_key]
        return self._run_multicam_creation(ordered, choice, from_timeline=False)

    def create_multicam_from_timeline_selection(self, *, choice: CreationChoice | None = None):
        """Menu du clip / commande : « Créer une séquence Multicam… » sur des clips déjà alignés."""
        timeline = self.timeline_panel
        ids = [view.id for view in timeline.clip_views if view.id in timeline.selected_clip_ids]
        sources = self._sources_from_clips(ids)
        if len(sources) < 2:
            self._report_edit_refused(i18n.translate("multicam.message.select_two"))
            return None
        if choice is None:
            choice = self._ask_multicam_choice([source.row for source in sources], from_timeline=True, addable=[])
            if choice is None:
                return None
        by_key = {source.key: source for source in sources}
        ordered = [by_key[key] for key, _name in choice.sources if key in by_key]
        return self._run_multicam_creation(ordered, choice, from_timeline=True)

    def _ask_multicam_choice(self, rows, *, from_timeline: bool, addable) -> CreationChoice | None:
        dialog = MulticamCreateDialog(
            rows, default_name=i18n.translate("multicam.default_name"), from_timeline=from_timeline,
            addable=addable, parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.choice()

    # ------------------------------------------------------------------
    # Exécution
    # ------------------------------------------------------------------

    def _run_multicam_creation(self, sources: list[_Source], choice: CreationChoice, *, from_timeline: bool):
        names = dict(choice.sources)
        for source in sources:
            source.row.name = names.get(source.key, source.row.name)
        method = choice.method
        if method is SyncMethod.AUDIO:
            started = self._start_multicam_sync(
                sources, lambda result: self._finish_multicam_creation(sources, choice, from_timeline, result)
            )
            return started
        return self._finish_multicam_creation(sources, choice, from_timeline, None)

    def _offline_outcomes(self, sources: list[_Source], method: SyncMethod) -> dict[str, SyncOutcome]:
        """Décalages des méthodes sans analyse sonore (timecode, repères, début, positions, manuel)."""
        if method is SyncMethod.TIMECODE:
            starts = {s.key: s.row.start_seconds for s in sources if s.row.start_seconds is not None}
            reference = min(starts.values()) if starts else 0.0
            return {
                s.key: (
                    SyncOutcome(starts[s.key] - reference, SyncMethod.TIMECODE, SyncStatus.NONE)
                    if s.key in starts else SyncOutcome(0.0, SyncMethod.START, SyncStatus.NONE)
                )
                for s in sources
            }
        if method is SyncMethod.MARKER:
            return {s.key: SyncOutcome(float(s.row.marker_offset or 0.0), SyncMethod.MARKER, SyncStatus.NONE) for s in sources}
        if method is SyncMethod.START:
            return {s.key: SyncOutcome(0.0, SyncMethod.START, SyncStatus.NONE) for s in sources}
        if method is SyncMethod.MANUAL:
            return {s.key: SyncOutcome(0.0, SyncMethod.MANUAL, SyncStatus.NONE) for s in sources}
        return {}   # positions actuelles : les clips gardent leur place

    def _audio_choices(self, sources: list[_Source]) -> list[tuple[str, MulticamAudio]]:
        """Politiques audio proposables : le son suit l'image, le son d'une source en continu, ou toutes mélangées."""
        choices: list[tuple[str, MulticamAudio]] = [
            (i18n.translate("multicam.audio.follow"), MulticamAudio(AudioMode.FOLLOW_VIDEO, ()))
        ]
        capable = [(index, s) for index, s in enumerate(sources) if s.row.has_audio]
        for index, source in capable:
            choices.append((
                i18n.translate("multicam.audio.fixed", name=source.row.name),
                MulticamAudio(AudioMode.FIXED, (f"angle-{index + 1}",)),
            ))
        if len(capable) > 1:
            choices.append((
                i18n.translate("multicam.audio.mix"),
                MulticamAudio(AudioMode.MIX, tuple(f"angle-{index + 1}" for index, _s in capable)),
            ))
        return choices

    def _finish_multicam_creation(
        self, sources: list[_Source], choice: CreationChoice, from_timeline: bool, result: SyncResult | None,
    ):
        method = choice.method
        if result is not None:
            by_key = result.by_key()
            outcomes = {
                s.key: SyncOutcome(by_key[s.key].offset, SyncMethod.AUDIO, by_key[s.key].status, by_key[s.key].confidence)
                for s in sources
            }
            # Un résultat sans décalage (mesure échouée) garde la place d'origine : jamais « synchronisé » faute de mesure.
        else:
            outcomes = self._offline_outcomes(sources, method)
        audio = None
        needs_summary = result is not None and any(o.status in _SYNC_FAILURES for o in outcomes.values())
        audio_only_ids = [f"angle-{i + 1}" for i, s in enumerate(sources) if s.row.kind == "audio"]
        probe = [self._probe_angle(i, s, outcomes.get(s.key)) for i, s in enumerate(sources)]
        suggestion = suggest_audio_policy(probe, audio_only_ids)
        needs_summary = needs_summary or suggestion.needs_choice
        if needs_summary:
            rows = [
                SummaryRow(
                    s.row.name, outcomes[s.key].status, outcomes[s.key].offset,
                    detail=result.by_key()[s.key].detail if result is not None else "",
                    reference=result is not None and result.reference == s.key,
                )
                for s in sources
            ]
            dialog = SyncSummaryDialog(rows, audio_choices=self._audio_choices(sources) if suggestion.needs_choice else None,
                                       parent=self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None
            audio = dialog.audio()
        try:
            created = self._build_multicam(sources, choice, from_timeline, outcomes, audio)
        except (KeyError, MulticamError) as error:
            self._report_edit_refused(error)
            return None
        sequence, segment = created
        self._record_history(i18n.translate("multicam.history.create", name=sequence.name))
        self._after_multicam_edit(select_clip_id=segment.id if segment is not None else None)
        self._refresh_project_library()
        self._refresh_sequence_ui()
        summary = self._creation_summary(sources, method, outcomes)
        LOGGER.info("Source Multicam « %s » créée : %s (%d angles)", sequence.name, summary, len(sources))
        self._show_sequence_status(i18n.translate("multicam.message.created", name=sequence.name, summary=summary))
        return sequence, segment

    @staticmethod
    def _probe_angle(index: int, source: _Source, outcome: SyncOutcome | None):
        from core.multicam_model import MulticamAngle

        return MulticamAngle(
            id=f"angle-{index + 1}", name=source.row.name, track_id="?",
            sync_status=outcome.status if outcome is not None else SyncStatus.NONE,
        )

    def _build_multicam(self, sources, choice, from_timeline, outcomes, audio):
        if from_timeline:
            clip_ids = [s.key for s in sources]
            segment_pair = create_multicam_from_clips(
                self.project, clip_ids, name=choice.name, sync_method=choice.method,
                offsets={key: o.offset for key, o in outcomes.items() if o.offset is not None} or None,
                names={s.key: s.row.name for s in sources}, outcomes=outcomes or None, audio=audio,
            )
            return segment_pair
        specs = []
        for source in sources:
            outcome = outcomes.get(source.key)
            specs.append(AngleSpec(
                asset_id=source.key, offset=float(outcome.offset) if outcome is not None and outcome.offset is not None else 0.0,
                name=source.row.name, sync_method=outcome.method if outcome is not None else choice.method,
                sync_status=outcome.status if outcome is not None else SyncStatus.NONE,
                sync_confidence=outcome.confidence if outcome is not None else None,
            ))
        sequence = create_multicam_source(self.project, specs, name=choice.name, sync_method=choice.method, audio=audio)
        track_id = self._default_nest_track_id()
        segment = None
        if track_id is not None and any(track.id == track_id and track.type == "video" for track in self.project.tracks):
            try:
                segment = insert_multicam_clip(self.project, sequence.id, track_id, max(0.0, self.playhead_seconds))
            except MulticamError as error:
                self._report_edit_refused(error)
        return sequence, segment

    def _creation_summary(self, sources, method: SyncMethod, outcomes) -> str:
        total = len(sources)
        if method is SyncMethod.AUDIO:
            good = sum(1 for o in outcomes.values() if o.status in {SyncStatus.EXCELLENT, SyncStatus.GOOD, SyncStatus.NONE})
            return i18n.translate("multicam.message.synced", count=good, total=total)
        if method is SyncMethod.TIMECODE:
            missing = sum(1 for s in sources if s.row.start_seconds is None)
            label = i18n.translate("multicam.message.timecode")
            return label if not missing else f"{label}, " + i18n.translate("multicam.message.no_timecode", count=missing)
        key = {
            SyncMethod.MARKER: "multicam.message.markers", SyncMethod.POSITIONS: "multicam.message.positions",
            SyncMethod.START: "multicam.message.starts", SyncMethod.MANUAL: "multicam.message.manual",
        }[method]
        return i18n.translate(key)

    # ------------------------------------------------------------------
    # Analyse sonore (file d'analyses, jamais le thread de l'interface)
    # ------------------------------------------------------------------

    def _multicam_cache(self):
        manager = getattr(self, "cache_manager", None)
        return getattr(manager, "multicam", None)

    def _sync_sources(self, sources: list[_Source]) -> tuple[SyncSource, ...]:
        result = []
        for source in sources:
            asset = source.asset
            path = asset.path if asset is not None else ""
            if source.clip is not None and asset is not None:
                clip = source.clip
                result.append(SyncSource(source.key, path, start=clip.source_in, duration=clip.source_out - clip.source_in))
            else:
                result.append(SyncSource(source.key, path))
        return tuple(result)

    def _start_multicam_sync(self, sources: list[_Source], finish) -> str:
        """Lance l'analyse en arrière-plan avec une boîte de progression annulable ; ``finish(résultat)`` à la fin."""
        names = {source.key: source.row.name for source in sources}
        job = AudioSyncJob(self._sync_sources(sources), cache=self._multicam_cache(), session_id=self.runtime.session_id)
        key = f"multicam-sync:{id(job)}"
        dialog = QProgressDialog(i18n.translate("multicam.progress.title"), i18n.translate("multicam.progress.cancel"), 0, 100, self)
        dialog.setWindowTitle(i18n.translate("multicam.progress.title"))
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.canceled.connect(job.cancel)
        timer = QTimer(self)
        timer.setInterval(100)
        pending = _PendingSync(job, dialog, timer, finish, names)
        self._multicam_syncs[key] = pending
        timer.timeout.connect(lambda: self._poll_multicam_sync(key))
        LOGGER.info("Synchronisation audio lancée : %d source(s)", len(sources))
        self.runtime.schedule_analysis(key, job.run)
        timer.start()
        dialog.show()
        return key

    def _poll_multicam_sync(self, key: str) -> None:
        pending = self._multicam_syncs.get(key)
        if pending is None:
            return
        snapshot = pending.job.snapshot()
        pending.dialog.setValue(int(snapshot.progress * 100))
        kind, _, source_key = snapshot.stage.partition(" ")
        if source_key:
            label = "multicam.progress.envelope" if kind.startswith("enveloppe") else "multicam.progress.measure"
            pending.dialog.setLabelText(i18n.translate(label, name=pending.names.get(source_key, source_key)))
        if snapshot.state not in {"finished", "cancelled", "failed"}:
            return
        pending.timer.stop()
        pending.dialog.close()
        self._multicam_syncs.pop(key, None)
        if pending.job.session_id != self.runtime.session_id:
            return                                      # le projet a changé entre-temps : résultat périmé
        if snapshot.state == "cancelled":
            self._show_sequence_status(i18n.translate("multicam.message.sync_cancelled"))
        elif snapshot.state == "failed" or snapshot.result is None:
            self._report_edit_refused(i18n.translate("multicam.message.sync_failed"))
        else:
            pending.finish(snapshot.result)             # type: ignore[operator]

    def _cancel_multicam_syncs(self) -> None:
        """Fermeture / nouveau projet : aucune analyse ni minuteur ne survit."""
        for pending in list(getattr(self, "_multicam_syncs", {}).values()):
            pending.job.cancel()
            pending.timer.stop()
            pending.dialog.close()
        getattr(self, "_multicam_syncs", {}).clear()
