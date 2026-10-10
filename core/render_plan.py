"""Plan de rendu vidéo pur pour Kut-Studio.

Ce module construit un :class:`RenderPlan` immuable à partir d'un
:class:`~core.project_model.Project`. Le plan décrit fidèlement la
timeline :

- position et durée des clips ;
- trims ``source_in`` / ``source_out`` ;
- pistes vidéo, audio, sous-titres et graphiques ;
- ordre des pistes : les pistes les plus basses dans ``project.tracks``
  sont visuellement au-dessus (overlay successif) ;
- clips activés (``enabled=True``) uniquement ;
- durée totale égale à :func:`core.timeline_evaluator.timeline_duration`.

Séquences imbriquées
--------------------

Un clip dont ``sequence_id`` est renseigné ne pointe pas vers un fichier :
il lit le **rendu composite** d'une autre séquence. Le plan ne l'aplatit
pas. Il enregistre la séquence comme un sous-plan
(:class:`NestedSequencePlan`) identifié par une clé, et la couche du clip
(``RenderLayer.nested_key`` / ``AudioLayer.nested_key``) référence cette
clé. Le consommateur (graphe FFmpeg) rend chaque sous-plan **une seule
fois**, puis le distribue à toutes ses instances : trois occurrences de
« Intro » ne rendent pas trois fois « Intro ».

Les sous-plans sont rangés dans ``RenderPlan.nested_sequences`` **à la
racine**, enfants avant parents (ordre topologique). La construction est
gardée contre les cycles (pile des séquences en cours) et contre une
profondeur excessive (:data:`core.sequences.MAX_NESTING_DEPTH`) : un clip
fautif est rendu vide et signalé dans ``RenderPlan.warnings``, jamais
dans une récursion infinie.

Ce module n'importe ni PySide6 ni FFmpeg : c'est une couche métier
pure, réutilisable par tout consommateur (FFmpeg via
``core.export_engine``, futur exporteur GPU, tests…).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from .effects_model import ClipEffect
from .project_model import Clip, MediaAsset, Project, Sequence, Track
from .subtitle_io import SubtitleCue
from .template_slots import is_photo_slot
from .text_style import TextStyle
from .time_map import TimeMap
from .time_remapping import TimeRemapping
from .transitions import TransitionType
from .visual_effects import ClipTransform, TransformKeyframe

if TYPE_CHECKING:
    # Typage seulement : le plan de rendu ne dépend pas du module ``compositing`` à l'exécution.
    from .compositing import Compositing


# ---------------------------------------------------------------------------
# Couches vidéo
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderLayer:
    """Description immuable d'un clip vidéo à rendre.

    Une :class:`RenderLayer` capture toutes les informations nécessaires
    pour composer une image sur la timeline finale, sans dépendre d'un
    état mutable. Les bornes ``source_in`` / ``source_out`` et
    ``timeline_start`` / ``timeline_end`` sont conservées à leur valeur
    d'origine (avant ou après déplacement / trim).

    Attributes:
        clip_id: Identifiant unique du clip dans le projet.
        asset_id: Identifiant du :class:`MediaAsset` source.
        track_id: Identifiant de la piste portant le clip.
        track_index: Position (0-based) de la piste dans
            ``project.tracks``.
        source_path: Chemin résolu du fichier média source.
        source_in: Point d'entrée dans le média source (secondes).
        source_out: Point de sortie du média source (secondes).
        timeline_start: Début du clip sur la timeline (secondes).
        timeline_end: Fin du clip sur la timeline (secondes).
    """

    clip_id: str
    asset_id: str
    track_id: str
    track_index: int
    source_path: str
    source_in: float
    source_out: float
    timeline_start: float
    timeline_end: float
    source_fps: float = 0.0
    transform: ClipTransform = field(default_factory=ClipTransform)
    transform_keyframes: tuple[TransformKeyframe, ...] = field(default_factory=tuple)
    time_remapping: TimeRemapping = field(default_factory=TimeRemapping)
    # Mapping temps timeline → temps source du clip (:mod:`core.time_map`), l'unique modèle temporel ; ``None`` pour un clip
    # qui n'est pas remappé. L'export, l'aperçu fidèle et les empreintes lisent ce mapping, jamais ``source_in`` × vitesse.
    time_map: TimeMap | None = None
    # Nombre d'images du média (``0`` : inconnu) : borne l'image la plus proche de la fin d'un média (voir
    # :func:`core.retime_graph.nearest_frame`) pour que la lecture inverse d'un clip entier ne soit pas décalée d'une image.
    source_frames: int = 0
    # Taille du média (``0`` : inconnue) : le cadrage « remplir » (``transform.fill``) en déduit la taille agrandie.
    source_width: int = 0
    source_height: int = 0
    effects: tuple[ClipEffect, ...] = field(default_factory=tuple)
    # Étalonnage couleur (tâche 29) : un objet ``ColorGrade`` ou
    # ``None`` si l'identité. On garde un type ``object`` pour ne
    # pas coupler le plan de rendu au module ``color_grading``.
    color_grade: object = None
    compositing: Compositing | None = None
    # Séquence imbriquée : clé du :class:`NestedSequencePlan` dont le rendu
    # composite remplace le fichier source (``source_path`` est alors vide).
    nested_key: str = ""
    # Animation générique du clip (masques animés…), voir ``Clip.animation``.
    animation: tuple = field(default_factory=tuple)
    # Image fixe (photo d'un emplacement de template) : FFmpeg la répète (``loop``) sur toute la durée du clip ; tout
    # le reste (cadrage, Ken Burns, effets, étalonnage, transitions, ordre des pistes) est celui d'une vidéo.
    still: bool = False
    # Calque image équivalent (``GraphicOverlay``) : ce que dessine le moniteur temps réel, qui ne décode pas de photo.
    photo: object = None

    @property
    def is_nested(self) -> bool:
        return bool(self.nested_key)


@dataclass(frozen=True)
class AudioLayer:
    """Description immuable d'un clip audio à mixer.

    Une :class:`AudioLayer` capture les mêmes informations qu'une
    :class:`RenderLayer` pour la partie son. Elle est utilisée pour :

    - les clips des pistes de type ``"audio"`` (ex. ``A1``) ;
    - les clips des pistes vidéo dont le :class:`MediaAsset`
      sous-jacent porte une piste audio (``has_audio=True``).

    Les clips dont le média source n'a pas de flux audio sont
    volontairement écartés en amont : on ne crée pas
    d':class:`AudioLayer` pour eux.

    Attributes:
        clip_id: Identifiant unique du clip dans le projet.
        asset_id: Identifiant du :class:`MediaAsset` source.
        track_id: Identifiant de la piste portant le clip.
        track_index: Position (0-based) de la piste dans
            ``project.tracks``.
        source_path: Chemin résolu du fichier média source.
        source_in: Point d'entrée dans le média source (secondes).
        source_out: Point de sortie du média source (secondes).
        timeline_start: Début du clip sur la timeline (secondes).
        timeline_end: Fin du clip sur la timeline (secondes).
        gain_db: Gain du clip seul (dB), hors volume de piste.
        pan: Panoramique du clip, borné à ``[-1, 1]``.
        fade_in: Durée du fondu d'entrée (secondes, ``>= 0``).
        fade_out: Durée du fondu de sortie (secondes, ``>= 0``).
    """

    clip_id: str
    asset_id: str
    track_id: str
    track_index: int
    source_path: str
    source_in: float
    source_out: float
    timeline_start: float
    timeline_end: float
    source_fps: float = 0.0
    gain_db: float = 0.0
    pan: float = 0.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    track_volume_db: float = 0.0
    track_pan: float = 0.0
    time_remapping: TimeRemapping = field(default_factory=TimeRemapping)
    time_map: TimeMap | None = None  # voir ``RenderLayer.time_map``
    # Effets audio non destructifs (tâche 27). Tuple pour respecter le
    # caractère immuable de l'AudioLayer. Les effets sont appliqués
    # dans l'ordre de la séquence au moment du rendu.
    audio_effects: tuple = field(default_factory=tuple)
    # Automation de volume par piste (tâche 28). Liste ordonnée de
    # :class:`AutomationPoint` ; ``gain_at`` est appelé pour appliquer
    # une enveloppe de gain à l'export. Vide par défaut.
    track_automation: tuple = field(default_factory=tuple)
    # Liste des :class:`DuckingSidechain` qui ciblent cette piste.
    # Vide par défaut : pas de ducking automatique.
    ducking_sidechains: tuple = field(default_factory=tuple)
    # Séquence imbriquée : clé du sous-plan dont le mixage audio remplace
    # le fichier source (``source_path`` est alors vide).
    nested_key: str = ""

    @property
    def is_nested(self) -> bool:
        return bool(self.nested_key)

    @property
    def duration(self) -> float:
        """Durée du clip sur la timeline."""
        return max(0.0, self.timeline_end - self.timeline_start)

    @property
    def total_gain_db(self) -> float:
        """Gain total appliqué : volume de piste + gain de clip."""
        return float(self.track_volume_db) + float(self.gain_db)

    @property
    def total_pan(self) -> float:
        """Panoramique total, borné (réglage clip + réglage piste)."""
        return max(-1.0, min(1.0, float(self.pan) + float(self.track_pan)))


@dataclass(frozen=True)
class RenderTransition:
    id: str
    from_clip_id: str
    to_clip_id: str
    type: TransitionType
    duration: float


@dataclass(frozen=True)
class GraphicLayer:
    """Calque motion graphics, composé au-dessus de la vidéo.

    ``role`` vaut ``"draw"`` pour un calque à dessiner, ``"rig"`` pour un
    calque présent **seulement** pour la hiérarchie : parent situé hors de
    la fenêtre d'un segment, parent masqué, ou clip vidéo parent
    (``graphic`` vaut alors ``None`` et sa boîte est le cadre). Ainsi un
    segment d'aperçu place les enfants exactement comme l'export.
    """

    clip_id: str
    track_id: str
    track_index: int
    timeline_start: float
    timeline_end: float
    graphic: object
    transform: ClipTransform = field(default_factory=ClipTransform)
    transform_keyframes: tuple[TransformKeyframe, ...] = field(default_factory=tuple)
    animation: tuple = field(default_factory=tuple)
    compositing: Compositing | None = None
    effects: tuple[ClipEffect, ...] = field(default_factory=tuple)
    color_grade: object = None
    role: str = "draw"
    label: str = ""

    @property
    def is_rig(self) -> bool:
        return self.role == "rig"


# ---------------------------------------------------------------------------
# Plan complet
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderPlan:
    """Plan immuable décrivant un export.

    Attributes:
        width: Largeur cible de la vidéo finale (pixels).
        height: Hauteur cible de la vidéo finale (pixels).
        fps: Fréquence d'images cible de la vidéo finale.
        duration: Durée totale du plan (secondes). Égale à
            :func:`timeline_duration` du projet source.
        video_layers: Couches vidéo à composer, dans l'ordre des pistes
            (les premières listées sont rendues en premier, les
            dernières apparaissent au-dessus).
        audio_layers: Couches audio à mixer. Couvre les pistes audio
            (``A1``…) **et** les clips vidéo dont le média source porte
            une piste audio (``has_audio=True``).
        subtitle_cues: Cues de sous-titres actifs du projet, triés par
            ``(start, end)``. Vides si le projet n'a aucun sous-titre
            actif ; le moteur d'export les utilise pour générer un
            fichier SRT temporaire et appliquer le filtre ``subtitles``.
        graphics_layers: Titres, formes, aplats et images à composer après
            les pistes vidéo et avant les sous-titres.
    """

    width: int
    height: int
    fps: float
    duration: float
    video_layers: tuple[RenderLayer, ...] = field(default_factory=tuple)
    audio_layers: tuple[AudioLayer, ...] = field(default_factory=tuple)
    subtitle_cues: tuple[SubtitleCue, ...] = field(default_factory=tuple)
    subtitle_styles: tuple["TextStyle", ...] = field(default_factory=tuple)
    master_gain_db: float = 0.0
    master_muted: bool = False
    transitions: tuple[RenderTransition, ...] = field(default_factory=tuple)
    graphics_layers: tuple[GraphicLayer, ...] = field(default_factory=tuple)
    # --- Séquences imbriquées ---
    # Séquence rendue par ce plan.
    sequence_id: str = ""
    # Sous-plans des séquences imbriquées, enfants avant parents. Rempli
    # uniquement sur le plan racine ; un sous-plan référence ses propres
    # enfants par clé dans ce même registre.
    nested_sequences: tuple["NestedSequencePlan", ...] = field(default_factory=tuple)
    # Problèmes rencontrés (référence cassée, cycle, profondeur) : les
    # clips concernés sont rendus vides au lieu de faire échouer le rendu.
    warnings: tuple[str, ...] = field(default_factory=tuple)
    # Identifiants des médias introuvables (supprimés de la bibliothèque) dont un clip actif dépend :
    # l'interface les tolère (clip hors ligne), mais un export refuse un plan qui en porte.
    missing_media: tuple[str, ...] = field(default_factory=tuple)
    empty_slots: tuple[str, ...] = field(default_factory=tuple)
    """Clips d'emplacement de template encore vides (rendus comme cartes, :mod:`core.template_slots`)."""
    # Flou de mouvement de la séquence (:class:`core.motion_blur.MotionBlurSettings`).
    motion_blur: object = None
    # Normalisation de loudness d'un export (réglage du preset, jamais du projet) : gain statique mesuré par
    # :mod:`core.loudness`, ajouté après le Master ; le limiteur final descend alors à −1 dBFS.
    loudness_gain_db: float | None = None

    def nested(self, key: str) -> "NestedSequencePlan | None":
        """Sous-plan de clé ``key`` (``None`` si inconnu)."""
        for entry in self.nested_sequences:
            if entry.key == key:
                return entry
        return None

    @property
    def is_audio_silent(self) -> bool:
        """Aucun son ne doit sortir : l'export vidéo reste valide."""
        if self.master_muted:
            return True
        return not self.audio_layers


@dataclass(frozen=True)
class CompositionRender:
    """Ce qu'il faut pour compiler une composition nodale (:mod:`core.composition_render`) : son graphe, l'animation
    de ses masques, et la clé du sous-plan de chaque source (un média, un calque graphique, rendus comme une séquence
    imbriquée d'un seul clip)."""

    graph: object
    animation: tuple = ()
    sources: tuple[tuple[str, str], ...] = ()
    sink: str = ""
    """Le nœud montré (aperçu du nœud choisi, page Composition) ; vide : la sortie."""

    def source_key(self, node_id: str) -> str | None:
        return next((key for node, key in self.sources if node == node_id), None)


@dataclass(frozen=True)
class NestedSequencePlan:
    """Rendu d'une séquence imbriquée, partagé par toutes ses instances.

    Attributes:
        key: Identité du sous-plan : l'identifiant de séquence, suivi de la
            plage lue quand le plan racine est fenêtré (segment d'aperçu).
            Deux instances qui lisent la même plage partagent la clé.
        sequence_id: Séquence rendue.
        name: Nom de la séquence (diagnostic).
        plan: Plan de la séquence, à sa propre résolution et à son fps.
            Sa ``duration`` couvre au moins le ``source_out`` le plus loin
            demandé par ses instances : au-delà de la fin de la séquence, le
            rendu est transparent et silencieux (jamais une image répétée).
        depth: Niveau d'imbrication (1 = clip de la séquence racine).
        composition: Une composition nodale (``plan`` n'en donne alors que la taille, la cadence et la durée) ; ses
            sources sont d'autres entrées du registre, placées avant elle.
    """

    key: str
    sequence_id: str
    name: str
    plan: RenderPlan
    depth: int = 1
    composition: CompositionRender | None = None


# ---------------------------------------------------------------------------
# Helpers privés
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------


def build_render_plan(
    project: Project,
    *,
    master_gain_db: float = 0.0,
    master_muted: bool = False,
    window: tuple[float, float] | None = None,
    window_index=None,
    sequence_id: str | None = None,
    composition_views: Mapping[str, str] | None = None,
) -> RenderPlan:
    """Construit un :class:`RenderPlan` à partir d'un :class:`Project`.

    Règles appliquées :

    - Pour la vidéo : seules les pistes de type ``"video"`` visibles
      (``track.visible is True``) sont conservées, avec leurs clips
      activés uniquement. Les couches sont émises dans l'ordre des
      pistes du projet (les pistes basses sont rendues en premier /
      au fond).
    - Pour l'audio : les pistes de type ``"audio"`` non muettes
      (``track.muted is False``) sont conservées intégralement ; les
      pistes vidéo non muettes ne contribuent une couche audio que si
      leur :class:`MediaAsset` porte ``has_audio=True``.
    - Chaque clip actif dont l'asset est introuvable lève une
      ``KeyError`` explicite.
    - La durée du plan est exactement
      :func:`core.timeline_evaluator.timeline_duration` du projet
      (les pistes invisibles sont prises en compte via ``enabled`` ;
      les pistes verrouillées ne modifient pas la durée).
    - Un clip imbriqué (``clip.sequence_id``) devient une couche qui lit
      le sous-plan de sa séquence (voir l'en-tête du module). Sur une
      piste vidéo il apporte l'image et le son de la séquence ; sur une
      piste audio, le son seul. Ses sous-titres sont reportés dans le
      plan parent, recalés sur la position du clip.

    Args:
        project: projet source (jamais muté).
        window: ``(début, fin)`` en secondes de timeline. Si fourni, le
            plan ne contient que les couches (vidéo, audio, graphiques,
            sous-titres) qui **chevauchent** cette fenêtre ; ``duration``
            reste celle du projet entier. C'est le plan d'un segment
            d'aperçu : un segment de 2 s ne doit ni ouvrir les milliers
            de médias d'un gros montage ni dépendre de leurs
            modifications (empreinte du segment). L'export n'utilise
            jamais ce paramètre. Les séquences imbriquées sont fenêtrées
            elles aussi, sur la plage qu'elles lisent pendant la fenêtre.

        window_index: :class:`core.timeline_index.TimelineIndex` du projet.
            Avec ``window``, il évite de parcourir tous les clips de chaque
            piste : le coût devient O(log n + clips de la fenêtre).
        sequence_id: séquence à rendre (la séquence active par défaut).
        composition_views: ``{clip: nœud}`` : pour l'aperçu, la composition
            de ces clips montre ce nœud au lieu de sa sortie.

    Returns:
        Le :class:`RenderPlan` correspondant au projet.

    Raises:
        KeyError: séquence demandée inconnue, ou média introuvable.
    """
    if sequence_id is None:
        sequence = project.active_sequence
    else:
        found = project.get_sequence(sequence_id)
        if found is None:
            raise KeyError(f"Séquence '{sequence_id}' introuvable dans le projet.")
        sequence = found
    builder = _PlanBuilder(project, window_index)
    builder.composition_views = dict(composition_views or {})
    plan = builder.build(
        sequence,
        window=window,
        window_index=window_index,
        stack=(sequence.id,),
        master_gain_db=master_gain_db,
        master_muted=master_muted,
    )
    return replace(
        plan,
        nested_sequences=builder.registry(),
        warnings=tuple(dict.fromkeys(builder.warnings)),
        missing_media=tuple(dict.fromkeys(builder.missing_media)),
        empty_slots=tuple(dict.fromkeys(builder.empty_slots)),
    )


class _PlanBuilder:
    """Construit un plan et le registre de ses séquences imbriquées.

    Le registre est mémorisé par clé pendant **une** construction : une
    séquence utilisée plusieurs fois à la même plage n'est planifiée
    qu'une fois (et sera rendue une fois par FFmpeg).
    """

    def __init__(self, project: Project, root_index=None) -> None:
        self.project = project
        # Index de la séquence racine : il fournit aussi ceux des séquences
        # imbriquées (``sub_index``), pour fenêtrer leurs sous-plans en
        # O(log n + k) au lieu de parcourir tous leurs clips.
        self.root_index = root_index
        # Une table id -> média évite un balayage de toute la bibliothèque
        # pour chaque clip (coût O(clips × médias) auparavant).
        self.assets_by_id = {asset.id: asset for asset in project.media_assets}
        self.entries: dict[str, NestedSequencePlan] = {}
        self.required: dict[str, float] = {}
        self.warnings: list[str] = []
        self.missing_media: list[str] = []
        self.empty_slots: list[str] = []
        self.composition_views: dict[str, str] = {}
        self._tracking_contexts: dict[str, object] = {}

    def tracking_context(self, sequence):
        """Contexte de tracking de ``sequence`` (construit au premier clip suivi)."""
        context = self._tracking_contexts.get(sequence.id)
        if context is None:
            from .tracking_bindings import TrackingContext

            context = TrackingContext(self.project, sequence)
            self._tracking_contexts[sequence.id] = context
        return context

    def effective(self, clip, sequence):
        """Animation rendue du clip : liaisons de tracking et stabilisation appliquées."""
        if getattr(clip, "tracking", None) is None:
            return None
        from .tracking_bindings import effective_clip_state

        state = effective_clip_state(clip, self.tracking_context(sequence))
        self.warnings.extend(f"{clip.label or clip.id} : {w}" for w in state.warnings)
        return state

    def registry(self) -> tuple[NestedSequencePlan, ...]:
        """Sous-plans, enfants avant parents, durée étendue aux besoins."""
        result = []
        for key, entry in self.entries.items():
            needed = self.required.get(key, 0.0)
            if needed > entry.plan.duration:
                entry = replace(entry, plan=replace(entry.plan, duration=needed))
            result.append(entry)
        return tuple(result)

    # ------------------------------------------------------------------

    def _nested_entry(
        self, clip: Clip, low: float, high: float, windowed: bool, stack: tuple[str, ...]
    ) -> NestedSequencePlan | None:
        """Sous-plan lu par ``clip`` (créé au besoin), ou ``None`` si impossible."""
        from .multicam import track_filter_for
        from .sequences import MAX_NESTING_DEPTH, nested_source_window

        child = self.project.get_sequence(clip.sequence_id)
        if child is None:
            self.warnings.append(
                f"Séquence imbriquée introuvable ({clip.sequence_id}) : "
                f"clip « {clip.label or clip.id} » rendu vide."
            )
            return None
        if child.id in stack:
            self.warnings.append(
                f"Imbrication circulaire ({' → '.join(stack + (child.id,))}) : "
                f"clip « {clip.label or clip.id} » rendu vide."
            )
            return None
        if len(stack) > MAX_NESTING_DEPTH:
            self.warnings.append(
                f"Imbrication de plus de {MAX_NESTING_DEPTH} niveaux : "
                f"clip « {clip.label or clip.id} » rendu vide."
            )
            return None
        # Source Multicam : seul l'angle choisi par le clip est rendu. Le filtre fait partie de la clé, donc deux
        # segments du même angle partagent un sous-plan et deux angles en ont chacun un (clé inchangée sans Multicam).
        track_filter = track_filter_for(child, clip)
        if windowed:
            a, b = nested_source_window(clip, low, high)
            # Une image de marge de chaque côté : l'arrondi au fps de la
            # séquence ne doit pas faire manquer la première/dernière image.
            margin = 1.0 / max(1.0, float(child.fps))
            inner_window = (max(0.0, a - margin), b + margin)
            key = f"{child.id}{track_filter.key}@{inner_window[0]:.4f}:{inner_window[1]:.4f}"
        else:
            inner_window = None
            key = f"{child.id}{track_filter.key}"
        entry = self.entries.get(key)
        if entry is None:
            sub_index = None
            if inner_window is not None and hasattr(self.root_index, "sub_index"):
                sub_index = self.root_index.sub_index(child)
            inner = self.build(
                child,
                window=inner_window,
                window_index=sub_index,
                stack=stack + (child.id,),
                track_filter=track_filter,
            )
            entry = NestedSequencePlan(
                key=key,
                sequence_id=child.id,
                name=child.name,
                plan=inner,
                depth=len(stack),
            )
            # Inséré après ses propres enfants : ordre topologique.
            self.entries[key] = entry
        self.required[key] = max(self.required.get(key, 0.0), float(clip.source_out))
        return entry

    def build(
        self,
        sequence: Sequence,
        *,
        window: tuple[float, float] | None,
        window_index,
        stack: tuple[str, ...],
        master_gain_db: float = 0.0,
        master_muted: bool = False,
        track_filter=None,
    ) -> RenderPlan:
        if window is None:
            low, high = float("-inf"), float("inf")
        else:
            low, high = float(window[0]), float(window[1])
        assets_by_id = self.assets_by_id
        tracks = sequence.tracks
        sidechains_all = list(getattr(sequence, "ducking_sidechains", []) or [])
        video_layers: list[RenderLayer] = []
        audio_layers: list[AudioLayer] = []
        graphics_layers: list[GraphicLayer] = []
        lifted_cues: list = []
        hide_video = track_filter.hide_video if track_filter is not None else frozenset()
        hide_audio = track_filter.hide_audio if track_filter is not None else frozenset()
        # Un solo ne compte que sur les pistes rendues : le solo d'un angle masqué ne doit pas vider l'angle actif.
        video_solo = {
            track.id for track in tracks if track.type == "video" and track.solo and track.id not in hide_video
        }
        audio_solo = {
            track.id for track in tracks if track.type == "audio" and track.solo and track.id not in hide_audio
        }
        graphics_solo = {
            track.id for track in tracks
            if track.type == "graphics" and track.solo
        }

        def clips_of(track_index: int, track):
            if window is not None and window_index is not None:
                found = window_index.clips_overlapping(track_index, low, high, track)
                if found is not None:
                    return found
            return track.clips

        def sidechains_for(track_id: str) -> list:
            return [
                s for s in sidechains_all
                if getattr(s, "music_track_id", None) == track_id
            ]

        for track_index, track in enumerate(tracks):
            if track.type == "graphics":
                if not track.visible or (graphics_solo and track.id not in graphics_solo):
                    continue
                for clip in clips_of(track_index, track):
                    graphic = getattr(clip, "graphic", None)
                    if not clip.enabled or graphic is None:
                        continue
                    if clip.timeline_start >= high or clip.timeline_start + clip.duration <= low:
                        continue
                    graphics_layers.append(
                        _graphic_layer(clip, track, track_index, state=self.effective(clip, sequence))
                    )
                continue
            if track.type not in {"video", "audio"}:
                continue
            # Source Multicam : image et son d'une piste d'angle sont masqués séparément (angle inactif, politique audio).
            show_video = track.id not in hide_video
            show_audio = track.id not in hide_audio
            if (track.type == "video" and not show_video and not show_audio) or (
                track.type == "audio" and not show_audio
            ):
                continue
            if track.type == "video" and not track.visible:
                # Une piste vidéo invisible n'apparaît pas dans le rendu.
                continue
            if video_solo and track.type == "video" and track.id not in video_solo:
                continue
            if track.type == "audio" and track.muted:
                # Une piste audio muette ne participe pas au mixage.
                continue
            if audio_solo and track.type == "audio" and track.id not in audio_solo:
                continue
            for clip in clips_of(track_index, track):
                if not clip.enabled:
                    continue
                if clip.timeline_start >= high or clip.timeline_start + clip.duration <= low:
                    continue
                if clip.composition is not None:
                    if track.type == "video":
                        self._add_composition_clip(
                            clip, track, track_index, sequence, low, high, window is not None, stack,
                            video_layers, audio_layers, audio_solo=audio_solo, sidechains=sidechains_for(track.id),
                            show_video=show_video, show_audio=show_audio,
                        )
                    continue
                if clip.sequence_id:
                    self._add_nested_clip(
                        clip, track, track_index, low, high, window is not None, stack,
                        video_layers, audio_layers, lifted_cues,
                        audio_solo=audio_solo,
                        sidechains=sidechains_for(track.id),
                        show_video=show_video,
                        show_audio=show_audio,
                    )
                    continue
                asset = assets_by_id.get(clip.asset_id)
                if asset is None and clip.template_slot and track.type == "video":
                    # Emplacement de template vide : une carte (calque Qt) tient sa place, même animation.
                    if show_video:
                        graphics_layers.append(_slot_layer(clip, track, track_index, sequence,
                                                           self.effective(clip, sequence)))
                    self.empty_slots.append(clip.id)
                    continue
                if asset is None:
                    # Média supprimé de la bibliothèque (les clips sont conservés, hors ligne) ou
                    # fichier retouché à la main : le clip ne montre rien, il ne fait pas échouer le plan
                    # (avant : KeyError au seek, à l'aperçu, à l'annulation, à l'export et à la réouverture).
                    self.warnings.append(
                        f"Média introuvable ({clip.asset_id}) : clip « {clip.label or clip.id} » rendu vide."
                    )
                    self.missing_media.append(clip.asset_id)
                    continue
                if track.type == "video":
                    if show_video:
                        state = self.effective(clip, sequence)
                        video_layers.append(
                            RenderLayer(
                                clip_id=clip.id,
                                asset_id=clip.asset_id,
                                track_id=track.id,
                                track_index=track_index,
                                source_path=asset.path,
                                source_in=clip.source_in,
                                source_out=clip.source_out,
                                timeline_start=clip.timeline_start,
                                timeline_end=clip.timeline_start + clip.duration,
                                source_fps=float(asset.fps),
                                transform=clip.transform,
                                transform_keyframes=(
                                    state.transform_keyframes if state is not None
                                    else tuple(clip.transform_keyframes)
                                ),
                                time_remapping=clip.time_remapping,
                                time_map=clip.time_map if clip.is_time_remapped else None,
                                source_frames=_frame_count(asset.duration, asset.fps),
                                source_width=int(asset.width or 0),
                                source_height=int(asset.height or 0),
                                effects=tuple(clip.effects),
                                # Étalonnage couleur (tâche 29) : si le clip ne
                                # porte pas de ``ColorGrade``, on garde ``None``
                                # pour signaler l'identité et économiser du
                                # travail au moteur d'export.
                                color_grade=getattr(clip, "color_grade", None),
                                compositing=(
                                    state.compositing if state is not None
                                    else getattr(clip, "compositing", None)
                                ),
                                animation=(
                                    state.animation if state is not None
                                    else tuple(getattr(clip, "animation", ()) or ())
                                ),
                                still=asset.media_type == "image",
                                photo=clip.graphic if is_photo_slot(clip) else None,
                            )
                        )
                    # Un solo audio ne laisse passer que les pistes audio armées
                    # en solo : le son embarqué des pistes vidéo est alors exclu.
                    if asset.has_audio and not track.muted and not audio_solo and show_audio:
                        audio_layers.append(
                            _build_audio_layer(
                                clip, asset, track.id, track_index, track,
                                ducking_sidechains=sidechains_for(track.id),
                            )
                        )
                else:  # track.type == "audio"
                    audio_layers.append(
                        _build_audio_layer(
                            clip, asset, track.id, track_index, track,
                            ducking_sidechains=sidechains_for(track.id),
                        )
                    )
        graphics_layers.extend(
            _rig_layers(tracks, graphics_layers, effective=lambda clip: self.effective(clip, sequence))
        )
        layer_ids = {layer.clip_id for layer in video_layers}
        transitions = tuple(
            RenderTransition(
                id=transition.id,
                from_clip_id=transition.from_clip_id,
                to_clip_id=transition.to_clip_id,
                type=transition.type,
                duration=transition.duration,
            )
            for transition in sequence.transitions
            if transition.from_clip_id in layer_ids and transition.to_clip_id in layer_ids
        )
        subtitle_entries = _subtitle_cues_for_tracks(tracks) + lifted_cues
        subtitle_entries.sort(key=lambda pair: (pair[0].start, pair[0].end))
        if window is not None:
            subtitle_entries = [
                entry for entry in subtitle_entries
                if entry[0].start < high and entry[0].end > low
            ]
        return RenderPlan(
            width=sequence.width,
            height=sequence.height,
            fps=float(sequence.fps),
            # Avec l'index de lecture (plan d'un segment), la durée est déjà connue :
            # inutile de reparcourir tous les clips pour chaque segment.
            duration=(
                window_index.duration
                if window is not None and window_index is not None
                else sequence.duration
            ),
            video_layers=tuple(video_layers),
            audio_layers=tuple(audio_layers),
            subtitle_cues=tuple(cue for cue, _style in subtitle_entries),
            subtitle_styles=tuple(style for _cue, style in subtitle_entries),
            master_gain_db=float(master_gain_db),
            master_muted=bool(master_muted),
            transitions=transitions,
            graphics_layers=tuple(graphics_layers),
            sequence_id=sequence.id,
            motion_blur=getattr(sequence, "motion_blur", None),
        )

    def _composition_entries(
        self, clip: Clip, sequence: Sequence, low: float, high: float, windowed: bool, stack: tuple[str, ...],
    ) -> tuple[NestedSequencePlan, NestedSequencePlan | None]:
        """Entrées du registre d'un clip de composition : chaque source rendue comme une séquence imbriquée d'un seul
        clip (placées avant elle), la composition elle-même, et son son (``None`` : muette). Une même composition à la
        même plage n'est planifiée qu'une fois."""
        from .composition import GraphicNode, MediaNode
        from .sequences import nested_source_window

        composition = clip.composition
        if composition is None:                              # appelée pour un clip de composition seulement
            raise ValueError(f"Le clip {clip.id} n'est pas une composition.")
        fps = float(sequence.fps) if sequence.fps > 0 else 30.0
        window: tuple[float, float] | None = None
        suffix = ""
        if windowed:
            a, b = nested_source_window(clip, low, high)
            margin = 1.0 / fps
            window = (max(0.0, a - margin), b + margin)
            suffix = f"@{window[0]:.4f}:{window[1]:.4f}"
        base = f"comp:{clip.id}"
        view = self.composition_views.get(clip.id, "")
        if view and not composition.graph.has_node(view):
            view = ""
        key = base + suffix + (f"#{view}" if view else "")
        nodes = composition.graph.rendered(view or None)

        def sub_plan(sub_key: str, synthetic: Sequence) -> NestedSequencePlan:
            entry = self.entries.get(sub_key)
            if entry is None:
                inner = self.build(synthetic, window=window, window_index=None, stack=stack)
                entry = NestedSequencePlan(key=sub_key, sequence_id="", name=synthetic.name,
                                           plan=replace(inner, duration=composition.duration), depth=len(stack))
                self.entries[sub_key] = entry
            self.required[sub_key] = max(self.required.get(sub_key, 0.0), composition.duration)
            return entry

        sources = []
        for node in nodes:
            if isinstance(node, (MediaNode, GraphicNode)):
                synthetic = _composition_source(sequence, clip, node)
                if synthetic is not None:
                    sources.append((node.id, sub_plan(f"{base}:{node.id}{suffix}", synthetic).key))
        # le son suit la sortie, jamais le nœud montré : choisir un nœud à voir ne change pas ce qu'on entend
        sound = _composition_sound(sequence, clip, composition.graph.rendered(), self.assets_by_id)
        audio = sub_plan(f"{base}:audio{suffix}", sound) if sound is not None else None
        entry = self.entries.get(key)
        if entry is None:
            entry = NestedSequencePlan(
                key=key, sequence_id="", name=clip.label or "composition", depth=len(stack),
                plan=RenderPlan(width=sequence.width, height=sequence.height, fps=fps,
                                duration=composition.duration),
                composition=CompositionRender(composition.graph, composition.animation, tuple(sources), view),
            )
            self.entries[key] = entry
        self.required[key] = max(self.required.get(key, 0.0), composition.duration)
        if audio is not None and not audio.plan.audio_layers:
            audio = None
        return entry, audio

    def _add_composition_clip(
        self, clip: Clip, track, track_index: int, sequence: Sequence, low: float, high: float, windowed: bool,
        stack: tuple[str, ...], video_layers: list, audio_layers: list, *, audio_solo: set, sidechains: list,
        show_video: bool = True, show_audio: bool = True,
    ) -> None:
        """Couches d'un clip de composition : son image (le graphe compilé, lu comme une séquence imbriquée) et son son
        (celui de ses médias), avec le transform, les effets, l'étalonnage et les masques du clip."""
        entry, audio = self._composition_entries(clip, sequence, low, high, windowed, stack)
        inner = entry.plan
        state = self.effective(clip, sequence)
        if show_video:
            video_layers.append(RenderLayer(
                clip_id=clip.id, asset_id="", track_id=track.id, track_index=track_index, source_path="",
                source_in=clip.source_in, source_out=clip.source_out, timeline_start=clip.timeline_start,
                timeline_end=clip.timeline_start + clip.duration, source_fps=float(inner.fps),
                transform=clip.transform,
                transform_keyframes=(state.transform_keyframes if state is not None
                                     else tuple(clip.transform_keyframes)),
                time_remapping=clip.time_remapping, time_map=clip.time_map if clip.is_time_remapped else None,
                source_frames=_frame_count(inner.duration, inner.fps), source_width=int(inner.width),
                source_height=int(inner.height), effects=tuple(clip.effects),
                color_grade=getattr(clip, "color_grade", None),
                compositing=state.compositing if state is not None else getattr(clip, "compositing", None),
                nested_key=entry.key,
                animation=state.animation if state is not None else tuple(getattr(clip, "animation", ()) or ()),
            ))
        if audio is not None and show_audio and not track.muted and not audio_solo:
            layer = _build_audio_layer(clip, None, track.id, track_index, track, ducking_sidechains=sidechains)
            audio_layers.append(replace(layer, source_fps=float(inner.fps), nested_key=audio.key))

    def _add_nested_clip(
        self,
        clip: Clip,
        track,
        track_index: int,
        low: float,
        high: float,
        windowed: bool,
        stack: tuple[str, ...],
        video_layers: list,
        audio_layers: list,
        lifted_cues: list,
        *,
        audio_solo: set,
        sidechains: list,
        show_video: bool = True,
        show_audio: bool = True,
    ) -> None:
        """Couches (vidéo, audio, sous-titres) d'un clip imbriqué."""
        entry = self._nested_entry(clip, low, high, windowed, stack)
        if entry is None:
            return
        inner = entry.plan
        timeline_end = clip.timeline_start + clip.duration
        if track.type == "video" and show_video:
            video_layers.append(
                RenderLayer(
                    clip_id=clip.id,
                    asset_id="",
                    track_id=track.id,
                    track_index=track_index,
                    source_path="",
                    source_in=clip.source_in,
                    source_out=clip.source_out,
                    timeline_start=clip.timeline_start,
                    timeline_end=timeline_end,
                    source_fps=float(inner.fps),
                    transform=clip.transform,
                    transform_keyframes=tuple(clip.transform_keyframes),
                    time_remapping=clip.time_remapping,
                    time_map=clip.time_map if clip.is_time_remapped else None,
                    source_frames=_frame_count(inner.duration, inner.fps),
                    source_width=int(inner.width),
                    source_height=int(inner.height),
                    effects=tuple(clip.effects),
                    color_grade=getattr(clip, "color_grade", None),
                    compositing=getattr(clip, "compositing", None),
                    nested_key=entry.key,
                    animation=tuple(getattr(clip, "animation", ()) or ()),
                )
            )
            lifted_cues.extend(_lift_nested_cues(clip, inner))
        # Le son de la séquence suit les mêmes règles qu'un média avec
        # piste audio : muet/solo de la piste parente, puis réglages du clip.
        audible = (
            show_audio
            and not track.muted
            and (track.type == "audio" or not audio_solo)
            and bool(inner.audio_layers)
        )
        if audible:
            layer = _build_audio_layer(
                clip, None, track.id, track_index, track,
                ducking_sidechains=sidechains,
            )
            audio_layers.append(
                replace(layer, source_fps=float(inner.fps), nested_key=entry.key)
            )


def _composition_source(sequence: Sequence, clip: Clip, node) -> Sequence | None:
    """La séquence synthétique d'un seul clip qui rend la source ``node`` d'une composition (un média sur une piste
    vidéo, un calque graphique sur une piste graphique), au temps de la composition ; ``None`` : plage vide."""
    from .compositing import Compositing
    from .composition import GraphicNode, MediaNode

    try:
        if isinstance(node, MediaNode):
            track = Track(id="C1", name="C1", type="video", clips=[Clip(
                id=f"{clip.id}:{node.id}", asset_id=node.asset_id, track_id="C1", timeline_start=node.start,
                source_in=node.source_in, source_out=node.source_out, label=node.label or node.id,
                transform=ClipTransform(fill=node.fill, pan_x=node.pan_x, pan_y=node.pan_y),
            )])
        elif isinstance(node, GraphicNode):
            track = Track(id="C1", name="C1", type="graphics", clips=[Clip(
                id=f"{clip.id}:{node.id}", asset_id="", track_id="C1", timeline_start=node.start, source_in=0.0,
                source_out=node.duration, label=node.label or node.id, graphic=node.graphic,
                transform=node.transform, transform_keyframes=list(node.transform_keyframes),
                animation=list(node.animation), compositing=Compositing(masks=node.masks),
            )])
        else:
            return None
    except ValueError:                                       # plage vide : la source ne montre rien
        return None
    return Sequence(id=f"comp:{clip.id}:{node.id}", name=node.label or node.id, width=sequence.width,
                    height=sequence.height, fps=sequence.fps, tracks=[track])


def _composition_sound(sequence: Sequence, clip: Clip, nodes, assets_by_id) -> Sequence | None:
    """Le son d'une composition : chaque média relié à la sortie (``nodes``), non muet et qui a du son, sur sa propre
    piste audio, à son instant."""
    from .composition import MediaNode

    tracks: list[Track] = []
    for node in nodes:
        if not isinstance(node, MediaNode) or node.muted or node.duration <= 0:
            continue
        asset = assets_by_id.get(node.asset_id)
        if asset is None or not asset.has_audio:
            continue
        track_id = f"A{len(tracks) + 1}"
        tracks.append(Track(id=track_id, name=track_id, type="audio", clips=[Clip(
            id=f"{clip.id}:{node.id}:audio", asset_id=node.asset_id, track_id=track_id, timeline_start=node.start,
            source_in=node.source_in, source_out=node.source_out, gain_db=node.gain_db,
        )]))
    if not tracks:
        return None
    return Sequence(id=f"comp:{clip.id}:audio", name="audio", width=sequence.width, height=sequence.height,
                    fps=sequence.fps, tracks=tracks)


def _graphic_layer(
    clip: Clip, track, track_index: int, *, role: str = "draw", state=None,
) -> GraphicLayer:
    """Calque de la scène motion graphics ; ``state`` : animation rendue (tracking)."""
    return GraphicLayer(
        clip_id=clip.id,
        track_id=track.id,
        track_index=track_index,
        timeline_start=clip.timeline_start,
        timeline_end=clip.timeline_start + clip.duration,
        graphic=getattr(clip, "graphic", None) if track.type == "graphics" else None,
        transform=clip.transform,
        transform_keyframes=(
            state.transform_keyframes if state is not None else tuple(clip.transform_keyframes)
        ),
        animation=(
            state.animation if state is not None else tuple(getattr(clip, "animation", ()) or ())
        ),
        compositing=state.compositing if state is not None else getattr(clip, "compositing", None),
        effects=tuple(clip.effects),
        color_grade=getattr(clip, "color_grade", None),
        role=role,
        label=clip.label,
    )


def _slot_layer(clip: Clip, track, track_index: int, sequence, state) -> GraphicLayer:
    """Carte d'un emplacement vide : un calque de texte à la taille du cadre, animé comme le clip."""
    from .template_slots import slot_card

    layer = _graphic_layer(clip, track, track_index, state=state)
    return replace(layer, graphic=slot_card(clip, int(sequence.width), int(sequence.height)))


def photo_monitor_layers(plan: "RenderPlan") -> tuple[GraphicLayer, ...]:
    """Photos d'emplacement du plan en calques image, pour le moniteur temps réel (qui ne décode pas de photo).

    L'export, l'aperçu fidèle et le moniteur GPU (segments) les rendent en calques vidéo, à leur place parmi les pistes
    vidéo ; le moniteur temps réel les dessine par le rastériseur des calques, avec la même animation."""
    return tuple(
        GraphicLayer(
            clip_id=layer.clip_id, track_id=layer.track_id, track_index=layer.track_index,
            timeline_start=layer.timeline_start, timeline_end=layer.timeline_end, graphic=layer.photo,
            transform=layer.transform, transform_keyframes=layer.transform_keyframes, animation=layer.animation,
            compositing=layer.compositing, effects=layer.effects, color_grade=layer.color_grade,
        )
        for layer in plan.video_layers if layer.photo is not None
    )


def _rig_layers(tracks, drawn: list[GraphicLayer], effective=None) -> list[GraphicLayer]:
    """Parents et groupes nécessaires aux calques dessinés mais absents du plan.

    Un parent hors de la fenêtre d'un segment, masqué, sur une piste cachée,
    ou un clip vidéo parent garde son rôle de transform : il est ajouté en
    ``rig`` (jamais dessiné). Parcours borné : chaque clip au plus une fois.
    """
    present = {layer.clip_id for layer in drawn}
    wanted: list[str] = []
    for layer in drawn:
        graphic = layer.graphic
        for ref in (getattr(graphic, "parent_id", ""), getattr(graphic, "group_id", "")):
            if ref and ref not in present:
                wanted.append(ref)
    if not wanted:
        return []
    by_id: dict[str, tuple] = {}
    for track_index, track in enumerate(tracks):
        if track.type not in ("graphics", "video"):
            continue
        for clip in track.clips:
            by_id[clip.id] = (clip, track, track_index)
    result: list[GraphicLayer] = []
    while wanted:
        clip_id = wanted.pop()
        if clip_id in present or clip_id not in by_id:
            continue
        clip, track, track_index = by_id[clip_id]
        if clip.sequence_id:
            continue  # un clip imbriqué n'est pas un parent de transform
        present.add(clip_id)
        state = effective(clip) if effective is not None else None
        layer = _graphic_layer(clip, track, track_index, role="rig", state=state)
        result.append(layer)
        graphic = layer.graphic
        for ref in (getattr(graphic, "parent_id", ""), getattr(graphic, "group_id", "")):
            if ref and ref not in present:
                wanted.append(ref)
    return result


def _frame_count(duration: float, fps: float) -> int:
    """Nombre d'images d'un flux de ``duration`` secondes à ``fps`` (``0`` si l'un des deux est inconnu)."""
    if duration <= 0.0 or fps <= 0.0:
        return 0
    return max(1, round(float(duration) * float(fps)))


def _lift_nested_cues(clip: Clip, inner: RenderPlan) -> list:
    """Sous-titres d'une séquence imbriquée, recalés dans le temps parent.

    Les sous-titres sont incrustés une fois, sur l'image finale (libass) :
    ceux d'une séquence imbriquée sont donc reportés dans le plan parent,
    bornés à la durée du clip et convertis par **le mapping du clip** (vitesse, courbe de vitesse, reverse, arrêt sur
    image) : une réplique suit la source. Si le mapping revient en arrière, une même réplique apparaît à chaque passage.
    Limite documentée : ils ne suivent pas le transform du clip imbriqué.
    """
    from .subtitle_io import SubtitleCue

    start = clip.timeline_start
    time_map = clip.time_map
    end = start + time_map.duration
    lifted = []
    for cue, style in zip(inner.subtitle_cues, inner.subtitle_styles):
        if time_map.is_hold:
            moment = time_map.source_time(0.0)
            intervals = [(0.0, time_map.duration)] if cue.start <= moment < cue.end else []
        else:
            intervals = time_map.timeline_intervals_of(cue.start, cue.end)
        for local_a, local_b in intervals:
            a, b = max(start + local_a, start), min(start + local_b, end)
            if b - a <= 1e-6:
                continue
            lifted.append((SubtitleCue(start=float(a), end=float(b), text=cue.text), style))
    return lifted


def _subtitle_cues_for_tracks(tracks):
    """Sous-titres exportés, en respectant visibilité et solo.

    Renvoie une liste de tuples ``(SubtitleCue, TextStyle)`` alignés sur
    l'ordre de tri ``(start, end)``. Les clips qui n'ont pas de style
    personnalisé portent le :class:`TextStyle` standard (cf.
    :mod:`core.text_style`).
    """
    from .subtitle_io import SubtitleCue
    from .text_style import default_text_style

    solo = {track.id for track in tracks if track.type == "subtitle" and track.solo}
    fallback = default_text_style()
    cues: list = []
    for track in tracks:
        if track.type != "subtitle" or not track.visible:
            continue
        if solo and track.id not in solo:
            continue
        for clip in track.clips:
            if not clip.enabled or not (clip.text or "").strip():
                continue
            cues.append(
                (
                    SubtitleCue(
                        start=float(clip.timeline_start),
                        end=float(clip.timeline_start + clip.duration),
                        text=clip.text.strip(),
                    ),
                    getattr(clip, "text_style", fallback),
                )
            )
    cues.sort(key=lambda pair: (pair[0].start, pair[0].end))
    return cues


def _build_audio_layer(
    clip, asset: MediaAsset | None, track_id: str, track_index: int, track=None,
    *,
    ducking_sidechains: list | None = None,
) -> AudioLayer:
    # Automation de volume (tâche 28) : les points ordonnés de la
    # :class:`TrackAutomation` du track parent (forme canonique du modèle).
    track_automation_points = tuple(track.automation.points) if track is not None else ()
    return AudioLayer(
        clip_id=clip.id,
        asset_id=clip.asset_id,
        track_id=track_id,
        track_index=track_index,
        source_path=asset.path if asset is not None else "",
        source_in=clip.source_in,
        source_out=clip.source_out,
        timeline_start=clip.timeline_start,
        timeline_end=clip.timeline_start + clip.duration,
        source_fps=float(asset.fps) if asset is not None else 0.0,
        gain_db=float(getattr(clip, "gain_db", 0.0)),
        pan=float(getattr(clip, "pan", 0.0)),
        fade_in=float(getattr(clip, "fade_in", 0.0)),
        fade_out=float(getattr(clip, "fade_out", 0.0)),
        track_volume_db=float(getattr(track, "volume_db", 0.0)),
        track_pan=float(getattr(track, "pan", 0.0)),
        time_remapping=getattr(clip, "time_remapping", TimeRemapping()),
        time_map=clip.time_map if getattr(clip, "is_time_remapped", False) else None,
        audio_effects=tuple(getattr(clip, "audio_effects", []) or []),
        track_automation=track_automation_points,
        ducking_sidechains=tuple(ducking_sidechains or []),
    )
