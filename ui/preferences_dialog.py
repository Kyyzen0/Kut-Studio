"""Boîte de dialogue Préférences de Kut-Studio.

Cette boîte de dialogue permet de choisir :

- le thème visuel (Sombre / Clair / Système) ;
- la langue de l'interface (Français / English / Español) ;
- le profil de performance (Auto / Léger / Équilibré / Puissant) ;
- la qualité d'aperçu temps réel (Plein, 1/2, 1/4, 1/8) ;
- la qualité de rendu des segments d'aperçu (Brouillon / Standard / Haute) ;
- les mises à jour (recherche au démarrage, préversions).

Chaque modification est appliquée **immédiatement** par
:class:`MainWindow` : le thème est commuté via :class:`ThemeManager`,
la langue via :func:`ui.i18n.set_language`. Les préférences sont
persistées automatiquement à chaque modification par
``MainWindow.on_user_setting_changed``.

Trois invariants sont verrouillés par les tests :

- les options et leurs valeurs par défaut viennent de
  :mod:`core.user_settings` (source de vérité du modèle) : elles ne
  peuvent plus dériver d'une liste recopiée dans l'interface ;
- une valeur courante inconnue retombe sur la valeur par défaut, si
  bien que chaque groupe affiche **toujours** exactement un bouton
  coché (aucun bouton coché laisserait croire à l'utilisateur qu'il
  n'a aucune préférence) ;
- le dialogue s'abonne à :mod:`ui.i18n` et se retraduit à chaud : la
  langue est appliquée pendant que la fenêtre est ouverte.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.app_version import APP_VERSION
from core.user_settings import (
    DEFAULT_CHECK_UPDATES,
    DEFAULT_INCLUDE_PRERELEASES,
    DEFAULT_LANGUAGE,
    DEFAULT_PERFORMANCE_PROFILE,
    DEFAULT_PREVIEW_QUALITY,
    DEFAULT_RENDER_QUALITY,
    DEFAULT_THEME,
    VALID_PERFORMANCE_PROFILES,
    VALID_PREVIEW_QUALITIES,
    VALID_RENDER_QUALITIES,
    VALID_THEME_MODES,
)
from ui.design_system import DIALOG_MARGINS, TextRoles
from ui.i18n import (
    available_languages,
    current_language,
    subscribe,
    translate,
    unsubscribe,
)
from ui.keyboard_navigation import set_single_default
from ui.performance_settings import PerformanceSettingsTab
from ui.shortcut_manager import ShortcutManager
from ui.shortcuts_editor import ShortcutsEditor
from ui.theme import set_role


# ---------------------------------------------------------------------------
# Description déclarative des groupes de boutons radio
# ---------------------------------------------------------------------------


def _option_keys(codes, prefix: str) -> tuple[tuple[str, str], ...]:
    """Associe chaque code d'option à sa clé de traduction ``prefs.<prefix>.<code>``."""
    return tuple((code, f"prefs.{prefix}.{code}") for code in codes)


@dataclass(frozen=True)
class _ChoiceGroup:
    """Un réglage du dialogue, décrit une seule fois.

    Les noms d'attributs publics sont dérivés de :attr:`key`, ce qui
    garantit qu'ils restent cohérents entre construction, retraduction
    et réinitialisation. Pour ``key="render"`` le dialogue expose donc
    ``render_group``, ``_render_radios``, la propriété ``render_code``
    et le slot ``_on_render_chosen``.

    Attributes:
        key: identifiant interne (``"theme"``, ``"render"``, …).
        title_key: clé i18n du titre du groupe.
        options: couples ``(code, clé i18n)`` dans l'ordre d'affichage.
        signal_name: signal émis quand le choix change.
        current_attribute: attribut mémorisant la valeur courante.
        default: valeur par défaut, ou ``None`` pour suivre la langue
            active du module :mod:`ui.i18n` (jamais figée à l'import).
    """

    key: str
    title_key: str
    options: tuple[tuple[str, str], ...]
    signal_name: str
    current_attribute: str
    default: str | None

    @property
    def group_attribute(self) -> str:
        """Nom de l'attribut :class:`QButtonGroup` (``theme_group``)."""
        return f"{self.key}_group"

    @property
    def buttons_attribute(self) -> str:
        """Nom du dictionnaire de boutons (``_theme_radios``)."""
        return f"_{self.key}_radios"

    @property
    def code_property(self) -> str:
        """Nom de la propriété Qt portée par chaque bouton (``theme_code``)."""
        return f"{self.key}_code"

    @property
    def handler_name(self) -> str:
        """Nom du slot de clic (``_on_theme_chosen``)."""
        return f"_on_{self.key}_chosen"

    @property
    def codes(self) -> tuple[str, ...]:
        """Codes valides du groupe, dans l'ordre d'affichage."""
        return tuple(code for code, _ in self.options)


_CHOICE_GROUPS: tuple[_ChoiceGroup, ...] = (
    _ChoiceGroup(
        key="theme",
        title_key="prefs.appearance",
        options=_option_keys(VALID_THEME_MODES, "theme"),
        signal_name="theme_changed",
        current_attribute="current_theme",
        default=DEFAULT_THEME,
    ),
    _ChoiceGroup(
        key="language",
        title_key="prefs.language",
        options=_option_keys(available_languages(), "language"),
        signal_name="language_changed",
        current_attribute="current_language_code",
        default=None,
    ),
    _ChoiceGroup(
        key="performance",
        title_key="prefs.performance",
        options=_option_keys(VALID_PERFORMANCE_PROFILES, "performance"),
        signal_name="performance_changed",
        current_attribute="current_performance",
        default=DEFAULT_PERFORMANCE_PROFILE,
    ),
    _ChoiceGroup(
        key="preview",
        title_key="prefs.preview",
        options=_option_keys(VALID_PREVIEW_QUALITIES, "preview"),
        signal_name="preview_quality_changed",
        current_attribute="current_preview_quality",
        default=DEFAULT_PREVIEW_QUALITY,
    ),
    _ChoiceGroup(
        key="render",
        title_key="prefs.render",
        options=_option_keys(VALID_RENDER_QUALITIES, "render"),
        signal_name="render_quality_changed",
        current_attribute="current_render_quality",
        default=DEFAULT_RENDER_QUALITY,
    ),
)

_CHOICES_BY_KEY: dict[str, _ChoiceGroup] = {item.key: item for item in _CHOICE_GROUPS}



class PreferencesDialog(QDialog):
    """Fenêtre de préférences (thème, langue, performance, aperçus)."""

    theme_changed = Signal(str)
    language_changed = Signal(str)
    performance_changed = Signal(str)
    preview_quality_changed = Signal(str)
    render_quality_changed = Signal(str)
    update_check_changed = Signal(bool)
    update_prereleases_changed = Signal(bool)
    restore_defaults_requested = Signal()

    def __init__(
        self,
        current_theme: str = "dark",
        current_language_code: str | None = None,
        current_performance: str = "auto",
        current_preview_quality: str = "auto",
        current_render_quality: str = "standard",
        current_check_updates: bool = DEFAULT_CHECK_UPDATES,
        current_include_prereleases: bool = DEFAULT_INCLUDE_PRERELEASES,
        parent=None,
        shortcut_manager: ShortcutManager | None = None,
        performance_host=None,
    ) -> None:
        """Construit le dialogue sur l'état courant de l'application.

        Les valeurs reçues sont mémorisées telles quelles puis
        normalisées par :meth:`_resolve_choice` : une valeur inconnue
        (fichier de préférences bricolé, appel direct en test) retombe
        sur la valeur par défaut du modèle au lieu de laisser le groupe
        sans aucun bouton coché.
        """
        super().__init__(parent)
        self._shortcut_manager = shortcut_manager
        self._performance_host = performance_host
        self.performance_tab: PerformanceSettingsTab | None = None
        self.shortcuts_editor: ShortcutsEditor | None = None
        self.tabs: QTabWidget | None = None
        self.current_theme = current_theme
        self.current_language_code = current_language_code
        self.current_performance = current_performance
        self.current_preview_quality = current_preview_quality
        self.current_render_quality = current_render_quality
        self.current_check_updates = bool(current_check_updates)
        self.current_include_prereleases = bool(current_include_prereleases)
        self.setModal(True)
        self.setMinimumWidth(360)
        # Le dialogue est détruit à sa fermeture : ``show_preferences``
        # peut être appelé en boucle sans accumuler d'enfants du
        # MainWindow ni d'abonnés i18n orphelins.
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self._group_boxes: dict[str, QGroupBox] = {}
        self._i18n_callback = None
        self._build_ui()
        self._retranslate()
        # La langue est appliquée immédiatement par MainWindow : sans cet
        # abonnement, un dialogue resté ouvert garderait ses libellés
        # dans l'ancienne langue jusqu'à sa réouverture.
        self._i18n_callback = self._on_language_changed
        subscribe(self._i18n_callback)
        self.finished.connect(self._release_language_subscription)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(*DIALOG_MARGINS)
        root.setSpacing(14)

        # Sans gestionnaire de raccourcis, le dialogue garde sa forme
        # historique (pas d'onglets).
        if self._shortcut_manager is None and self._performance_host is None:
            layout = root
        else:
            self.tabs = QTabWidget()
            general_page = QWidget()
            layout = QVBoxLayout(general_page)
            layout.setContentsMargins(0, 12, 0, 0)
            layout.setSpacing(14)
            shortcuts_page = None
            if self._shortcut_manager is not None:
                self.shortcuts_editor = ShortcutsEditor(self._shortcut_manager)
                shortcuts_page = QWidget()
                shortcuts_layout = QVBoxLayout(shortcuts_page)
                shortcuts_layout.setContentsMargins(0, 12, 0, 0)
                shortcuts_layout.addWidget(self.shortcuts_editor)
            # Défilement : les groupes ne tiennent pas toujours dans la hauteur de l'onglet, et ne doivent jamais être écrasés
            # (c'est vrai du Général comme de la Performance : ses listes de matériel étaient réduites à une ligne de 16 px).
            general_scroll = self._scrollable(general_page)
            self._tab_order: list[str] = ["general"]
            self.tabs.addTab(general_scroll, "")
            if shortcuts_page is not None:
                self.tabs.addTab(shortcuts_page, "")
                self._tab_order.append("shortcuts")
            if self._performance_host is not None:
                self.performance_tab = PerformanceSettingsTab(self._performance_host)
                self.tabs.addTab(self._scrollable(self.performance_tab), "")
                self._tab_order.append("performance")
            root.addWidget(self.tabs, 1)
            self.setMinimumSize(680, 680)

        for choice in _CHOICE_GROUPS:
            layout.addWidget(self._build_choice_group(choice))
        layout.addWidget(self._build_updates_group())

        # ----- Bouton "Restaurer les réglages par défaut" -----------------
        actions_row = QHBoxLayout()
        actions_row.addStretch()
        self.restore_button = QPushButton()
        self.restore_button.clicked.connect(self._on_restore_defaults)
        actions_row.addWidget(self.restore_button)
        layout.addLayout(actions_row)
        if self.tabs is not None:
            layout.addStretch(1)

        # ----- Bouton de fermeture ----------------------------------------
        # Le libellé d'un bouton standard vient de Qt (donc de sa
        # traduction anglaise) : on le remplace par la nôtre.
        self.buttons_box = QDialogButtonBox(QDialogButtonBox.Close)
        self.close_button = self.buttons_box.button(QDialogButtonBox.Close)
        self.buttons_box.rejected.connect(self.reject)
        root.addWidget(self.buttons_box)
        # Un seul bouton par défaut : « Fermer ». Sans cela Entrée déclenchait le premier bouton créé
        # (« Restaurer les réglages par défaut », ou « Retirer » un raccourci dans l'éditeur).
        set_single_default(self, self.close_button)

    @staticmethod
    def _scrollable(page: QWidget) -> QScrollArea:
        """``page`` dans une zone défilante transparente (le fond du thème reste celui du dialogue)."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        # Le viewport prendrait le fond natif de l'OS : on le laisse transparent pour garder le fond du thème du dialogue.
        scroll.setObjectName("preferencesScroll")
        scroll.setFocusPolicy(Qt.NoFocus)  # simple conteneur : pas d'arrêt de Tab invisible
        scroll.setStyleSheet(
            "QScrollArea#preferencesScroll,"
            " QScrollArea#preferencesScroll > QWidget > QWidget"
            " { background: transparent; }"
        )
        scroll.setWidget(page)
        return scroll

    # ------------------------------------------------------------------
    # Construction des groupes de radios
    # ------------------------------------------------------------------

    def _build_choice_group(self, choice: _ChoiceGroup) -> QGroupBox:
        """Construit un groupe de radios et l'expose sous ses noms publics.

        Le choix courant est validé par :meth:`_resolve_choice` avant
        d'être coché : une valeur inconnue retombe sur le défaut du
        modèle, donc le groupe affiche toujours un bouton coché.
        """
        box = QGroupBox()
        box_layout = QVBoxLayout(box)
        box_layout.setSpacing(6)
        box_layout.setContentsMargins(14, 12, 14, 12)
        group = QButtonGroup(self)
        group.setExclusive(True)
        # QButtonGroup.buttonClicked est déprécié depuis Qt 5.15 : on
        # utilise idClicked (id = position du bouton dans le groupe).
        slot = getattr(self, choice.handler_name)
        group.idClicked.connect(partial(slot, choice))
        buttons: dict[str, QRadioButton] = {}
        for index, (code, key) in enumerate(choice.options):
            radio = QRadioButton()
            radio.setProperty(choice.code_property, code)
            group.addButton(radio, index)
            buttons[code] = radio
            box_layout.addWidget(radio)
        current = self._resolve_choice(choice)
        buttons[current].setChecked(True)
        setattr(self, choice.current_attribute, current)
        setattr(self, choice.group_attribute, group)
        setattr(self, choice.buttons_attribute, buttons)
        self._group_boxes[choice.key] = box
        return box

    def _build_updates_group(self) -> QGroupBox:
        """Groupe « Mises à jour » : deux cases (appliquées tout de suite) et la version installée."""
        self.updates_box = QGroupBox()
        box_layout = QVBoxLayout(self.updates_box)
        box_layout.setSpacing(6)
        box_layout.setContentsMargins(14, 12, 14, 12)
        self.check_updates_box = QCheckBox()
        self.check_updates_box.setChecked(self.current_check_updates)
        self.check_updates_box.toggled.connect(self._on_check_updates_toggled)
        self.prereleases_box = QCheckBox()
        self.prereleases_box.setChecked(self.current_include_prereleases)
        self.prereleases_box.toggled.connect(self._on_prereleases_toggled)
        self.updates_note = QLabel()
        self.updates_note.setWordWrap(True)
        set_role(self.updates_note, TextRoles.label_secondary)
        for widget in (self.check_updates_box, self.prereleases_box, self.updates_note):
            box_layout.addWidget(widget)
        return self.updates_box

    def _on_check_updates_toggled(self, checked: bool) -> None:
        if bool(checked) != self.current_check_updates:
            self.current_check_updates = bool(checked)
            self.update_check_changed.emit(self.current_check_updates)

    def _on_prereleases_toggled(self, checked: bool) -> None:
        if bool(checked) != self.current_include_prereleases:
            self.current_include_prereleases = bool(checked)
            self.update_prereleases_changed.emit(self.current_include_prereleases)

    def _default_for(self, choice: _ChoiceGroup) -> str:
        """Valeur de repli d'un groupe : défaut de :mod:`core.user_settings`.

        ``default=None`` signifie « défaut dynamique » : la valeur doit
        être lue à l'exécution (langue active), jamais figée à l'import.
        """
        if choice.default is not None:
            return choice.default
        active = current_language()
        return active if active in choice.codes else DEFAULT_LANGUAGE

    def _resolve_choice(self, choice: _ChoiceGroup) -> str:
        """Valeur courante validée : une valeur inconnue retombe sur le défaut."""
        value = getattr(self, choice.current_attribute, None)
        if isinstance(value, str) and value in choice.codes:
            return value
        return self._default_for(choice)

    # ------------------------------------------------------------------
    # Traduction à chaud
    # ------------------------------------------------------------------

    def _retranslate(self) -> None:
        """Applique les libellés de la langue courante (rappelez à chaud).

        Aucun texte n'est figé à la construction : c'est le seul endroit
        qui écrit dans les widgets, ce qui évite qu'un libellé ajouté
        plus tard soit oublié lors d'un changement de langue.
        """
        self.setWindowTitle(translate("prefs.title"))
        for choice in _CHOICE_GROUPS:
            box = self._group_boxes.get(choice.key)
            if box is not None:
                box.setTitle(translate(choice.title_key))
            buttons = getattr(self, choice.buttons_attribute, {})
            for code, key in choice.options:
                radio = buttons.get(code)
                if radio is not None:
                    radio.setText(translate(key))
        if self.tabs is not None:
            titles = {
                "general": "shortcuts.tab.general",
                "shortcuts": "shortcuts.tab.shortcuts",
                "performance": "perf.tab",
            }
            for index, name in enumerate(self._tab_order):
                self.tabs.setTabText(index, translate(titles[name]))
            if self.shortcuts_editor is not None:
                self.shortcuts_editor.retranslate()
            if self.performance_tab is not None:
                self.performance_tab.retranslate()
        self.updates_box.setTitle(translate("prefs.updates"))
        self.check_updates_box.setText(translate("prefs.updates.check"))
        self.prereleases_box.setText(translate("prefs.updates.prereleases"))
        self.updates_note.setText(translate("prefs.updates.note", version=APP_VERSION))
        self.restore_button.setText(translate("prefs.restore_defaults"))
        self.close_button.setText(translate("prefs.close"))

    # ------------------------------------------------------------------
    # Cycle de vie de l'abonnement i18n
    # ------------------------------------------------------------------

    def _on_language_changed(self, code: str) -> None:
        """Callback i18n : retraduit le dialogue pendant qu'il est ouvert."""
        del code
        self._retranslate()

    def _release_language_subscription(self, *_args) -> None:
        """Désabonne le dialogue fermé (idempotent).

        Appelé à la fermeture (bouton Fermer, Échap, croix) : un dialogue
        disparu ne doit plus être retenu par :mod:`ui.i18n`. Le drapeau
        ``WA_DeleteOnClose`` détruit l'objet Qt juste après.
        """
        callback = self._i18n_callback
        if callback is not None:
            unsubscribe(callback)
            self._i18n_callback = None

    # ------------------------------------------------------------------
    # Réactions aux choix
    # ------------------------------------------------------------------

    def _on_choice_chosen(
        self,
        choice: _ChoiceGroup,
        choice_or_button,
        index: int | None = None,
    ) -> None:
        """Émet le signal du groupe quand son choix change.

        ``idClicked`` émet la position du bouton dans le groupe : on en
        déduit le code, sans dépendre de ``buttonClicked`` (déprécié).
        Le chemin historique ``handler(radio)`` reste accepté pour les
        extensions et tests qui appellent directement ces slots.
        """
        if index is None:
            button = choice_or_button
            code = button.property(choice.code_property)
        else:
            if not 0 <= index < len(choice.options):
                return
            code = choice.options[index][0]
        if code not in choice.codes:
            return
        if code == getattr(self, choice.current_attribute):
            return
        setattr(self, choice.current_attribute, code)
        getattr(self, choice.signal_name).emit(code)

    def _on_theme_chosen(self, choice_or_button, index: int | None = None) -> None:
        """Slot du groupe thème (nom public, conservé)."""
        self._on_choice_chosen(
            _CHOICES_BY_KEY["theme"], choice_or_button, index
        )

    def _on_language_chosen(self, choice_or_button, index: int | None = None) -> None:
        """Slot du groupe langue (nom public, conservé)."""
        self._on_choice_chosen(
            _CHOICES_BY_KEY["language"], choice_or_button, index
        )

    def _on_performance_chosen(
        self, choice_or_button, index: int | None = None
    ) -> None:
        """Slot du groupe performance (nom public, conservé)."""
        self._on_choice_chosen(
            _CHOICES_BY_KEY["performance"], choice_or_button, index
        )

    def _on_preview_chosen(self, choice_or_button, index: int | None = None) -> None:
        """Slot du groupe aperçu (nom public, conservé)."""
        self._on_choice_chosen(
            _CHOICES_BY_KEY["preview"], choice_or_button, index
        )

    def _on_render_chosen(self, choice_or_button, index: int | None = None) -> None:
        """Slot du groupe rendu (nom public, conservé)."""
        self._on_choice_chosen(
            _CHOICES_BY_KEY["render"], choice_or_button, index
        )

    # ------------------------------------------------------------------
    # Réinitialisation
    # ------------------------------------------------------------------

    def _on_restore_defaults(self) -> None:
        """Restaure les réglages affichés par la boîte de dialogue.

        Chaque valeur vient de :mod:`core.user_settings` (source de
        vérité du modèle) et non d'une copie en dur : faire dériver les
        deux listes est impossible par construction. Note : le Master
        (gain/muet) n'est pas affiché ici — voir
        ``MainWindow._restore_default_preferences`` qui le préserve.
        """
        defaults = {
            "theme": DEFAULT_THEME,
            "language": DEFAULT_LANGUAGE,
            "performance": DEFAULT_PERFORMANCE_PROFILE,
            "preview": DEFAULT_PREVIEW_QUALITY,
            "render": DEFAULT_RENDER_QUALITY,
        }
        for key, code in defaults.items():
            choice = _CHOICES_BY_KEY[key]
            buttons = getattr(self, choice.buttons_attribute)
            buttons[code].setChecked(True)
            setattr(self, choice.current_attribute, code)
        # Les cases suivent sans réémettre : ``restore_defaults_requested`` applique tout d'un coup.
        for box, value, attribute in (
            (self.check_updates_box, DEFAULT_CHECK_UPDATES, "current_check_updates"),
            (self.prereleases_box, DEFAULT_INCLUDE_PRERELEASES, "current_include_prereleases"),
        ):
            setattr(self, attribute, value)
            box.setChecked(value)
        self.restore_defaults_requested.emit()
