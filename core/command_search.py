"""Recherche de fonctions de la palette de commandes (« Rechercher une fonction », ⌘K / Ctrl+K).

Module **pur** (aucun Qt) : la normalisation, le score et le classement par niveau d'usage se testent sans fenêtre.

Règles :

- la comparaison ignore la casse **et** les accents (``étalonnage`` = ``ETALONNAGE``), la ponctuation et les ``&`` de
  mnémonique des menus ;
- la recherche porte sur le titre (dans la langue courante **et** en anglais), l'indication (chemin de menu, aide) et les
  mots-clés ; chaque mot saisi doit se retrouver quelque part (ET) ;
- les familles de synonymes français / anglais / espagnols (:data:`SYNONYMS`) rapprochent « couper », « cut » et « split » ;
  un synonyme pèse moins qu'une correspondance directe ;
- un résultat d'un niveau d'usage **supérieur** au niveau courant n'est pas écarté : il est **marqué verrouillé**
  (``available=False``) pour que l'interface propose « Passer en Studio / Avancé » (découvrabilité, voir
  ``docs/usage-levels.md``) ;
- les entrées, la saisie et le nombre de mots sont bornés (une saisie de plusieurs Mo ne coûte rien).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from core.usage_level import UsageLevel

MAX_RESULTS = 6
"""Nombre maximal de résultats affichés."""

MAX_QUERY_CHARS = 200
"""Au-delà, la saisie est tronquée avant tout traitement."""

MAX_TOKENS = 8
"""Nombre maximal de mots pris en compte dans une saisie."""

LOCKED_FACTOR = 0.85
"""Pondération du score d'une entrée d'un niveau d'usage supérieur (elle reste affichée, avec « Passer en … »)."""

DISABLED_FACTOR = 0.6
"""Pondération du score d'une entrée grisée dans le contexte courant."""

MAX_ENTRIES = 5000
"""Nombre maximal d'entrées examinées (le registre d'un menu en compte quelques centaines)."""

_NON_WORD = re.compile(r"[^0-9a-z]+")
_LIGATURES = str.maketrans({"œ": "oe", "Œ": "oe", "æ": "ae", "Æ": "ae", "ß": "ss", "ø": "o", "Ø": "o"})
_MNEMONIC = re.compile(r"&(?=\S)")


def normalize(text: str) -> str:
    """Minuscules, sans accents, mots séparés par une espace (``« Sous‑titres… »`` -> ``sous titres``)."""
    if not text:
        return ""
    folded = unicodedata.normalize("NFKD", text.translate(_LIGATURES))
    stripped = "".join(ch for ch in folded if not unicodedata.combining(ch)).casefold()
    return _NON_WORD.sub(" ", stripped).strip()


def clean_label(text: str) -> str:
    """Libellé de menu lisible : sans mnémonique ``&``, sans raccourci après une tabulation, sans « … » final."""
    label = text.split("\t", 1)[0].replace("&&", "\0")
    label = _MNEMONIC.sub("", label).replace("\0", "&").strip()
    for ending in ("…", "..."):
        if label.endswith(ending):
            label = label[: -len(ending)].rstrip()
    return label


def tokens(query: str) -> list[str]:
    """Mots normalisés d'une saisie (bornés)."""
    return normalize(query[:MAX_QUERY_CHARS]).split()[:MAX_TOKENS]


# Familles de synonymes (déjà normalisées : minuscules, sans accent). Un mot saisi qui est le début d'un terme d'une famille
# rapproche toute entrée contenant un terme de cette famille.
SYNONYMS: tuple[frozenset[str], ...] = tuple(frozenset(group.split()) for group in (
    "couper coupe decouper trancher cut split blade razor lame cortar dividir",
    "suivi suivre suivant track tracking tracker pistage seguimiento seguir",
    "titres soustitres captions caption subtitle subtitles srt transcription subtitulos subtitulo",
    "couleur couleurs etalonnage etalonner color colour grade grading colorimetrie correction",
    "vitesse speed ralenti accelere slow fast retime velocidad",
    "export exporter exportation render rendu publier exportar",
    "importer import importation importar",
    "supprimer suppression effacer delete remove retirer borrar eliminar",
    "dupliquer duplicate copy copier duplicar",
    "annuler undo retablir redo deshacer rehacer",
    "enregistrer sauvegarder save guardar",
    "ouvrir open abrir",
    "piste track pistes tracks pista",
    "audio son sons sound volume voix voice sonido",
    "marqueur marker repere marcador",
    "transition transitions fondu fade dissolve transicion",
    "texte titre titres text title titles texto",
    "effet effets effect effects efecto efectos",
    "keyframe keyframes animation animer fotograma cles",
    "recadrer recadrage crop cadrer reframe recortar",
    "masque masques mask masks mascara",
    "aimantation snap magnetisme aimant",
    "zoom agrandir",
    "niveau niveaux level usage simple studio avance advanced nivel",
    "preferences reglages settings options prefs ajustes",
    "sequence sequences timeline montage secuencia",
    "calque calques layer layers capa capas",
    "proxy proxies",
    "multicam multi camera angle",
    "stabilisation stabiliser stabilize stabilise estabilizar",
    "mouvement motion",
))


def _expand(token: str) -> tuple[str, ...]:
    """Le mot saisi puis les termes de ses familles de synonymes (le mot saisi pèse le plus)."""
    out = [token]
    for group in SYNONYMS:
        if token in group or (len(token) >= 3 and any(term.startswith(token) for term in group)):
            out.extend(term for term in sorted(group) if term not in out)
    return tuple(out)


@dataclass(frozen=True)
class SearchEntry:
    """Une fonction trouvable. ``id`` est stable (historique, tests) ; les textes sont dans la langue courante.

    ``title_en`` / ``hint_en`` : mêmes textes en anglais (optionnels), cherchés en plus de la langue courante.
    ``level`` : niveau d'usage minimal ; ``enabled`` / ``reason`` : la commande est grisée dans le contexte courant.
    """

    id: str
    title: str
    hint: str = ""
    title_en: str = ""
    hint_en: str = ""
    keywords: tuple[str, ...] = ()
    level: UsageLevel = UsageLevel.SIMPLE
    enabled: bool = True
    reason: str = ""
    shortcut: str = ""
    icon: str = ""
    kind: str = "command"
    context: str = ""
    """Sous-menu parent, affiché devant le titre (« Panneaux › Couleur ») ; il n'entre pas dans le score du titre (le chemin
    complet est dans l'indication)."""


@dataclass(frozen=True)
class SearchResult:
    """Une entrée retenue : son score et sa disponibilité au niveau d'usage demandé."""

    entry: SearchEntry
    score: int
    available: bool

    @property
    def locked(self) -> bool:
        """``True`` si l'entrée demande un niveau d'usage supérieur au niveau courant."""
        return not self.available


def _words(*texts: str) -> list[str]:
    out: list[str] = []
    for text in texts:
        out.extend(normalize(text).split())
    return out


def _field_score(token: str, words: Sequence[str], joined: str, weights: tuple[int, int, int]) -> int:
    """Meilleur score d'un mot sur un champ : début du premier mot, début d'un autre mot, fragment."""
    first, later, fragment = weights
    best = 0
    for index, word in enumerate(words):
        if word.startswith(token):
            best = max(best, first if index == 0 else later)
            if best == first:
                return best
    if best == 0 and len(token) >= 3 and token in joined:
        best = fragment
    return best


def _token_score(token: str, fields: dict[str, tuple[list[str], str]]) -> int:
    """Score d'un mot sur l'ensemble des champs, synonymes compris (un synonyme pèse 70 % d'une correspondance directe)."""
    best = 0
    for rank, alternative in enumerate(_expand(token)):
        factor = 1.0 if rank == 0 else 0.7
        for name, weights in _WEIGHTS.items():
            words, joined = fields[name]
            found = _field_score(alternative, words, joined, weights)
            if found:
                best = max(best, int(found * factor))
    return best


# (début du premier mot, début d'un autre mot, fragment) par champ.
_WEIGHTS: dict[str, tuple[int, int, int]] = {
    "title": (100, 80, 55),
    "keywords": (60, 60, 35),
    "hint": (40, 40, 25),
}


def score_entry(entry: SearchEntry, query_tokens: Sequence[str]) -> int:
    """Score de l'entrée pour ces mots (``0`` si un mot ne se retrouve nulle part)."""
    if not query_tokens:
        return 0
    title_words = _words(entry.title, entry.title_en)
    hint_words = _words(entry.hint, entry.hint_en)
    keyword_words = _words(*entry.keywords)
    fields = {
        "title": (title_words, " ".join(title_words)),
        "keywords": (keyword_words, " ".join(keyword_words)),
        "hint": (hint_words, " ".join(hint_words)),
    }
    total = 0
    for token in query_tokens:
        found = _token_score(token, fields)
        if found == 0:
            return 0
        total += found
    normalized_query = " ".join(query_tokens)
    for title in (entry.title, entry.title_en):
        normalized_title = normalize(title)
        if normalized_title == normalized_query:
            total += 60
        elif normalized_title.startswith(normalized_query):
            total += 30
    return total


def search(
    entries: Iterable[SearchEntry],
    query: str,
    level: UsageLevel,
    limit: int = MAX_RESULTS,
) -> list[SearchResult]:
    """Entrées qui répondent à ``query``, les mieux classées d'abord, au plus ``limit``.

    Le score d'une entrée verrouillée (niveau supérieur) ou grisée est pondéré à la baisse ; à score égal : les entrées
    disponibles avant les verrouillées, puis les actives avant les grisées, puis l'ordre d'origine. Une saisie vide ne renvoie rien.
    """
    query_tokens = tokens(query)
    if not query_tokens or limit <= 0:
        return []
    scored: list[tuple[int, int, int, int, SearchResult]] = []
    for index, entry in enumerate(entries):
        if index >= MAX_ENTRIES:
            break
        score = score_entry(entry, query_tokens)
        if score <= 0:
            continue
        available = level.at_least(entry.level)
        # Une fonction grisée ou d'un niveau supérieur reste trouvable, mais à correspondance égale elle passe après.
        score = int(score * (1.0 if available else LOCKED_FACTOR) * (1.0 if entry.enabled else DISABLED_FACTOR))
        result = SearchResult(entry, score, available)
        scored.append((-score, 0 if available else 1, 0 if entry.enabled else 1, index, result))
    scored.sort(key=lambda item: item[:4])
    return [item[4] for item in scored[:limit]]


def recent_results(
    entries: Iterable[SearchEntry], recent_ids: Sequence[str], level: UsageLevel, limit: int = MAX_RESULTS
) -> list[SearchResult]:
    """Entrées récemment choisies (la plus récente d'abord), pour une saisie vide ; les identifiants disparus sont ignorés."""
    by_id = {entry.id: entry for entry in entries}
    out: list[SearchResult] = []
    for entry_id in recent_ids:
        entry = by_id.get(entry_id)
        if entry is not None:
            out.append(SearchResult(entry, 0, level.at_least(entry.level)))
        if len(out) >= limit:
            break
    return out


__all__ = [
    "DISABLED_FACTOR", "LOCKED_FACTOR", "MAX_ENTRIES", "MAX_QUERY_CHARS", "MAX_RESULTS", "MAX_TOKENS", "SYNONYMS", "SearchEntry", "SearchResult",
    "clean_label", "normalize", "recent_results", "score_entry", "search", "tokens",
]
