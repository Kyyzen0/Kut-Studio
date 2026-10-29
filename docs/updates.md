# Mises à jour et releases

Ce document décrit la recherche de mises à jour de Kut-Studio, ce que garantit sa vérification, la convention des
fichiers de release, et la procédure pour publier une version compatible (dont la signature et la notarisation macOS).

La seule source des mises à jour est **GitHub Releases** du dépôt `Kyyzen0/Kut-Studio`, avec des versions numérotées.
Les archives des builds d'intégration (artefacts du workflow *Multiplatform*, servis par nightly.link) sont temporaires,
non numérotées et sans empreinte : l'application ne les lit jamais.

## 1. Le parcours de l'utilisateur

| Situation | Comportement |
| --- | --- |
| Démarrage, option active, dernière recherche réussie il y a ≥ 24 h | Recherche en arrière-plan 4 s après l'ouverture. |
| … aucune nouvelle version, ou erreur (hors ligne, limite GitHub, réponse illisible) | **Silence** (trace dans le journal de diagnostic). Une erreur n'avance pas la date : nouvel essai au démarrage suivant. |
| … nouvelle version | **Aucune fenêtre ne s'ouvre** (elle volerait le clavier en plein montage) : un bouton « Mise à jour X » apparaît dans la barre supérieure. |
| **Aide › Rechercher des mises à jour…** | Toujours un résultat explicite : à jour (avec la préversion non proposée s'il y en a une), version disponible, ou erreur claire avec « Réessayer ». |
| Version disponible | Version, date, notes de publication (Markdown, sans HTML), paquet choisi et sa taille ; **Télécharger**, **Plus tard**, **Ignorer cette version**, page de la version. |
| Ignorer cette version | Mémorisée (`skipped_update_version`) : la recherche automatique la tait ; une version plus récente est de nouveau annoncée ; la recherche manuelle la montre toujours (« Ne plus ignorer cette version »). |
| Plus tard | Rien n'est mémorisé ; elle sera rappelée à la prochaine recherche automatique. |
| Téléchargement | Barre de progression, « Annuler » ; le dialogue est non modal (on continue à monter). Fermer le dialogue ou l'application annule et supprime le fichier partiel. |
| Paquet vérifié | Ce que garantit la vérification, l'authenticité selon le système, les étapes d'installation du système, **Ouvrir le dossier**, **Quitter et installer…**. Sous macOS, signature Ed25519 vérifiée et application remplaçable : **Installer et redémarrer** à la place (section 3 bis). |
| Exécution depuis les sources | La version disponible et ses notes sont présentées, avec le commit courant ; **rien n'est téléchargé ni remplacé** (« mettez à jour avec git pull »). |

Préférences › Général › **Mises à jour** : recherche au démarrage (par défaut : oui), préversions (par défaut : non ; une
préversion installée reçoit toujours la suivante), version installée. La recherche envoie seulement
`User-Agent: Kut-Studio/<version>` à `api.github.com` : ni identifiant, ni cookie, ni donnée du projet.
`KUT_STUDIO_UPDATE_CHECK=off` coupe la recherche automatique (tests, outils de capture, smoke test) ; la recherche
manuelle reste possible. **Aide › À propos de Kut-Studio** donne la version, le mode d'installation (dont le commit
pour les sources), le système et les versions de Qt, PySide6 et Python.

## 2. Ce que garantit la vérification — et ce qu'elle ne garantit pas

Le paquet est écrit dans `<cache utilisateur>/updates/<nom>.part`. Pendant la réception, sa taille est bornée par celle
qu'annonce l'API et son SHA-256 est calculé au fil de l'eau. Il n'est renommé sous son nom définitif que si :

1. sa **taille** est exactement celle annoncée par GitHub ;
2. son **SHA-256** est celui de `SHA256SUMS.txt` publié dans la release ;
3. quand l'API fournit l'empreinte qu'elle a calculée à l'envoi (champ `digest`), celle-ci est identique aux deux autres
   (une contradiction arrête tout, avant même de télécharger le paquet).
4. sous macOS et Windows, le système a accepté la marque « téléchargé d'Internet » (voir plus bas), posée sur le `.part`
   avant le renommage. Si elle est refusée (système de fichiers sans attributs étendus ni flux alternatifs), le paquet
   est **refusé** : il échapperait sinon au contrôle de Gatekeeper ou de SmartScreen. Linux n'a pas de marque : rien
   n'est exigé.

Avant même de télécharger le paquet, si la release publie `SHA256SUMS.txt.sig`, la **signature Ed25519** de
`SHA256SUMS.txt` est vérifiée (section 10) : fausse ou faite par une clé inconnue, elle refuse tout, sans rien écrire sur
le disque. Une version égale ou plus ancienne que celle installée n'est jamais téléchargée.

Sinon le fichier est supprimé, jamais ouvert. Une release sans `SHA256SUMS.txt`, ou qui n'y liste pas le paquet, n'est
pas téléchargeable depuis l'application (seule la page de la version est proposée).

**Intégrité, pas authenticité — sauf si la release est signée.** `SHA256SUMS.txt` vient de la même release que le
paquet : il prouve que le fichier est complet et identique à celui publié (pas de coupure, de corruption, de mauvais
fichier servi par un cache). Il ne prouve pas *qui* a publié : quelqu'un qui pourrait remplacer le paquet pourrait
remplacer l'empreinte. Depuis l'ADR-0012, une release peut porter en plus `SHA256SUMS.txt.sig`, signature Ed25519 que
seul le détenteur de la clé privée du mainteneur sait produire : l'application la vérifie avec la clé publique du dépôt
(section 10) et le dialogue dit si la version est signée. Pour une release **non signée**, l'authenticité repose encore
sur les signatures vérifiées par le système, et l'application le dit dans le dialogue :

* **macOS** : le paquet vérifié reçoit l'attribut `com.apple.quarantine`, comme un téléchargement par navigateur
  (l'utilitaire d'archive le propage à `Kut-Studio.app`) : Gatekeeper contrôle la signature Developer ID et la
  notarisation à la première ouverture. Kut-Studio ne retire jamais cet attribut et ne désactive aucune protection. Une
  version non notarisée est bloquée par macOS ; l'utilisateur seul peut l'autoriser (Réglages Système › Confidentialité
  et sécurité), ce que le dialogue réserve au cas où il fait confiance à sa provenance.
* **Windows** : le paquet reçoit le flux `Zone.Identifier` (« Mark of the Web », zone Internet) : SmartScreen et les
  protections liées s'appliquent. Les paquets Windows ne sont **pas encore signés** (Authenticode) : SmartScreen peut
  signaler un éditeur inconnu, et le dialogue le dit.
* **Linux** : aucune vérification de signature par le système.

## 3. Installation assistée (et pourquoi pas automatique)

Kut-Studio ne remplace jamais une application en cours d'exécution et n'interrompt jamais un montage. **Quitter et
installer…** demande d'abord l'accord (bouton par défaut : non), puis ferme la fenêtre **par le chemin normal** : un
projet non enregistré est proposé à l'enregistrement, un rendu en cours demande confirmation ; si l'utilisateur annule,
tout reste ouvert et rien d'autre ne se passe. Une fois la fenêtre fermée, le dossier du paquet vérifié s'ouvre, et le
dialogue a déjà donné les étapes du système (glisser `Kut-Studio.app` dans Applications ; remplacer le dossier
`Kut-Studio` sous Windows et Linux — les projets et préférences sont ailleurs et sont conservés). Une copie macOS lancée
depuis un emplacement de translocation est signalée.

Sous **Windows et Linux**, l'installation reste assistée, volontairement : avec le packaging actuel (PyInstaller *onedir*,
archives sans installeur), une installation fiable demanderait un processus distinct lancé après la fermeture (un
exécutable Windows et ses DLL sont verrouillés tant qu'ils tournent), la vérification de la signature du nouveau
bundle (`WinVerifyTrust`), la gestion des droits (Program Files, dossiers système Linux), une sauvegarde et un retour
arrière, et la relance. Rien de cela ne peut être vérifié ici pour ces deux systèmes. **Sous macOS**, c'est fait, dans les
conditions strictes de la section suivante ; partout ailleurs, et chaque fois qu'une condition manque, c'est ce parcours
assisté, inchangé, qui s'applique.

## 3 bis. Installation automatique (macOS)

« **Installer et redémarrer** » remplace `Kut-Studio.app` par la nouvelle version, la rouvre, et remet l'ancienne si la
nouvelle ne démarre pas. Elle n'est proposée que si **toutes** les conditions suivantes sont réunies ; sinon le dialogue
garde « Quitter et installer… » (parcours assisté de la section 3) et dit pourquoi quand la raison aide l'utilisateur.

| Condition | Raison dite si elle manque |
| --- | --- |
| La **signature Ed25519** de la release est `VERIFIED` (ADR-0012) : `DownloadResult.signature_status` vaut `SignatureStatus.VERIFIED` **et** la clé qui l'a validée figure dans `core/update_keys.py` (`core.update_install.SignaturePolicy`, qui lit le vrai statut ; `FixedPolicy` sert aux tests). Non signé (liste de clés vide aujourd'hui), signature fausse ou clé inconnue (le paquet est alors refusé par le téléchargement), statut absent : parcours assisté. | « la signature de cette version n'est pas vérifiée » |
| Application gelée, `Kut-Studio.app` macOS | exécution depuis les sources / autre système (silence) |
| Pas de translocation (`/AppTranslocation/`) | « macOS exécute cette copie depuis un emplacement temporaire » |
| Bundle présent, volume non en lecture seule (`statvfs`) | volume en lecture seule |
| `os.access(W_OK \| X_OK)` sur le dossier parent **et** sur le bundle | pas le droit d'écrire / de remplacer |

**Rien n'est installé sans accord explicite** : le bouton demande confirmation (défaut : non). Aucune mise à jour
silencieuse : la recherche automatique garde son comportement (un bouton dans la barre, jamais de fenêtre ni
d'installation sans clic).

### Déroulé

1. **Accord**, puis **staging** (dans un fil, annulable, barre indéterminée) : après la vérification existante (taille +
   empreinte + signature), l'archive est contrôlée (`zipfile` : au plus 200 000 entrées, 8 Gio décompressés, aucun chemin
   absolu ni `..`, tout sous `Kut-Studio.app/`, espace libre suffisant) puis extraite avec `ditto -x -k --noqtn` dans
   `.Kut-Studio-update-<pid>`, un dossier caché **frère** de l'application : même volume, donc des renommages atomiques.
2. **Quarantaine.** `com.apple.quarantine` est retirée du bundle de staging (`xattr -dr`) **uniquement** parce que la
   signature Ed25519 est vérifiée : c'est notre garantie d'authenticité, et une quarantaine posée par Kut-Studio
   lui-même ne prouverait rien de plus que ce que nous avons déjà prouvé ; la laisser ferait afficher à Gatekeeper un
   avertissement sur une application que l'utilisateur vient d'autoriser. `stage_update` refuse de tourner sans
   `signature_verified=True`. Le **paquet** téléchargé garde sa quarantaine (la section 2 est inchangée).
3. **Validation du nouveau bundle avant de toucher à l'ancien** (`validate_bundle`) : `CFBundleIdentifier` identique
   (`com.kutstudio.app`), `CFBundleShortVersionString` égale à la version annoncée **et** strictement supérieure à
   l'installée, `CFBundleExecutable` présent, `lipo -archs` contient l'architecture du paquet (`arm64`),
   `codesign --verify --deep --strict` correct, **même équipe** de signature que l'application installée (une application
   signée ne devient pas ad hoc, ni signée par un autre éditeur, en silence) ; si le bundle porte une signature Developer
   ID, `spctl --assess --type execute` doit aussi l'accepter (notarisation comprise) ; **ad hoc** : `codesign` seul (la
   garantie est alors la signature Ed25519, documentée ici). Tout échec supprime le staging : l'application n'a pas été
   modifiée, le dialogue dit pourquoi et retombe sur l'installation assistée.
4. **Fermeture par le chemin normal** (`close`) : projet non enregistré proposé à l'enregistrement, rendu en cours confirmé.
   **Annuler arrête tout** : le staging est supprimé, rien n'est lancé.
5. **Assistant** (`core/update_helper.py`). Un script `/bin/sh` **généré à l'exécution** dans `<cache>/updates/helper-<pid>/`
   (droits 0700), plutôt qu'un fichier des ressources : il ne peut pas vivre dans le bundle qu'il remplace, et un fichier de
   plus dans le bundle changerait sa signature. Son texte est constant ; les chemins arrivent par des variables
   `KUT_UPD_*` (espaces, accents, guillemets, `$` : de simples valeurs). Lancé détaché (`start_new_session`, entrée
   `/dev/null`, sorties vers `install.log`, environnement minimal sans `DYLD_*`) **après** la fermeture. Il :
   * attend la fin du processus de l'application, **120 s au plus** ; au-delà il abandonne **sans rien modifier** (statut
     `wait_timeout`) ;
   * renomme `Kut-Studio.app` en `.Kut-Studio.app.previous-<version>`, puis le staging en `Kut-Studio.app` (renommages
     atomiques ; si le second échoue, la précédente est remise aussitôt) ;
   * lance `open` sur la nouvelle application, puis attend le fichier **« démarré »** (`<cache>/updates/started.json`,
     `{"version":…,"pid":…}`) que la nouvelle application écrit au démarrage, **90 s au plus** (premier lancement d'un
     gros bundle compris) ; la nouvelle application doit encore vivre **3 s** plus tard ;
   * **succès** : statut `ok`, sauvegarde supprimée. **Échec** (délai, application morte, `open` en erreur, signal
     reçu) : **retour arrière** — processus issus du nouveau bundle (repérés par le chemin exact `<bundle>/Contents/MacOS/` dans `ps -axo pid=,command=`, jamais un motif large) arrêtés : SIGTERM, attente de 5 s, SIGKILL des seuls pids qui correspondent encore, attente de 5 s ; si l'un survit, **retour arrière impossible** (statut `rollback_impossible`, rien n'est déplacé ni supprimé, la précédente n'est pas lancée) ; nouvelle application écartée puis supprimée,
     précédente remise et relancée, statut `rolled_back` ;
   * journalise chaque étape dans `<cache>/updates/install.log` (recréé à chaque installation) ; tout `rm -rf` passe par
     `safe_rm`, qui refuse un nom qui n'est pas un staging ou une sauvegarde de Kut-Studio.
6. **Idempotence et interruption.** Le script lit l'état des dossiers (neuf / à moitié échangé / échangé) au lieu de
   supposer où il en était : relancé après un arrêt brutal à n'importe quelle étape, il termine ou remet l'ancienne.
   Il prend d'abord un **verrou exclusif** : `install.lock` est un dossier créé par `mkdir` (atomique) qui contient son pid. Un second assistant (deux instances de Kut-Studio) ne l'obtient pas et sort avec le code 6 **avant d'examiner ou de modifier quoi que ce soit** (journal, et statut `already_running` s'il n'y en a pas déjà un) ; l'application ne lance d'ailleurs pas d'assistant si un verrou vivant existe (avant même de fermer la fenêtre). Un verrou dont le propriétaire est mort est renommé vers un nom unique puis supprimé, jamais supprimé en place ni pris s'il est vivant ; il est libéré à toute sortie (`trap EXIT`, y compris SIGTERM), sauf SIGKILL (couvert par la reprise du verrou périmé). Tous les renommages passent par `safe_mv` : jamais vers une destination existante (`mv` y imbriquerait le dossier ; macOS n'a pas `mv -T`) — test avant, passage par un nom unique, test juste avant le dernier renommage, vérification après. Les variables `KUT_UPD_*` sont refusées (code 2, rien de touché) si elles sont vides, relatives, avec `..`, `//` ou `/` final, si le staging et la sauvegarde ne sont pas des frères nommés comme attendu de `Kut-Studio.app`, ou si le pid est ≤ 1. Au démarrage suivant, `recover_leftovers` : verrou vivant → ne touche à
   rien ; `Kut-Studio.app` absent ou invalide et sauvegarde valide → **restaurée** ; application valide et sauvegarde d'une
   autre version (assistant mort après l'échange) → l'application qui tourne prouve que la nouvelle version démarre, la
   sauvegarde est supprimée ; stagings et dossiers d'assistant orphelins supprimés.
   *Limite honnête* : un arrêt brutal (SIGKILL de l'assistant, coupure de courant) **entre** les deux renommages laisse un
   court instant sans `Kut-Studio.app` ; l'application ne peut alors pas se lancer pour se réparer elle-même. La sauvegarde
   est intacte dans le dossier caché `.Kut-Studio.app.previous-<version>` : la renommer en `Kut-Studio.app` suffit
   (le message de `rollback_failed` l'explique). macOS n'offre pas d'échange atomique de deux dossiers depuis le shell
   (`renamex_np` demande un programme C ou Python) ; la fenêtre est de l'ordre de la milliseconde.
7. **Suivi.** Au démarrage, l'application écrit le fichier « démarré » (version et pid), répare ce qu'une installation
   interrompue a laissé, puis lit `install-status.json` : « **Mise à jour installée : X.Y.Z** » (avec « Notes de version »)
   ou « **La mise à jour a échoué** » avec ce qui s'est passé (version précédente restaurée / version actuelle conservée /
   restauration manuelle) et « Ouvrir le journal ». Le message ne s'affiche **qu'une fois** (le statut est consommé) ; la
   nouvelle application attend jusqu'à 40 s que l'assistant, encore vivant, écrive le statut.

### Ce qui n'est vérifié que sur un vrai Mac

Les tests exécutent l'assistant sur de **vrais dossiers temporaires** avec un faux `Kut-Studio.app` et un `open` remplacé par
un stub (jamais le vrai), et les outils système sont simulés ; `ditto` n'est lancé pour de vrai que sur macOS. Restent à
vérifier à la main : voir [recette/mise-a-jour-automatique.md](recette/mise-a-jour-automatique.md).

**Mise à jour (ADR-0012).** Une installation automatique ne sera proposée que pour un paquet dont la signature est
`VERIFIED` (`DownloadResult.signature_status`) ; sans signature, l'installation reste assistée comme ci-dessus.

## 4. Architecture

| Information | Source unique | Lue par | Test |
| --- | --- | --- | --- |
| Version de l'application | `core/app_version.py` (`APP_VERSION`) | recherche, À propos, Préférences, `build.py` (`Info.plist`), `tools.release check-tag` | `test_versioning`, `test_release_tools` |
| Ordre des versions | `core/versioning.py` (`Version`, SemVer 2.0, jamais de comparaison de texte) | `core.updates` | `test_versioning` |
| Convention des paquets, cibles, `SHA256SUMS.txt` | `core/release_assets.py` | application **et** `tools/release` | `test_release_assets`, `test_release_tools`, `test_release_workflow` |
| Que proposer, que refuser | `core/updates.py` (pur : ni réseau ni Qt) | `core.update_service`, interface | `test_updates_core` |
| Transport HTTPS, délais, annulation, vérification | `core/update_service.py` (`QNetworkAccessManager`) | `UpdatesMixin` | `test_update_transport` (faux GitHub local) |
| Parcours et textes | `ui/main_window_mixins/updates.py`, `ui/update_dialog.py`, `ui/i18n_updates.py` | — | `test_update_ui` |
| Installation automatique macOS | `core/update_install.py` (décision, staging, validation, reprise), `core/update_helper.py` (script `/bin/sh`), `ui/update_auto_install.py` (parcours), `ui/i18n_update_install.py` | `UpdatesMixin` | `test_update_install`, `test_update_install_ui` |

Le réseau passe par Qt plutôt que par `urllib` : Qt utilise le magasin de certificats du système (un `urllib` figé par
PyInstaller cherche les certificats là où ils étaient sur la machine de construction et échoue chez l'utilisateur), et
tout reste asynchrone sur la boucle d'événements (progression, délais, annulation sans thread). Erreurs typées
(`UpdateErrorKind`) : hors ligne, délai dépassé, limite GitHub (`X-RateLimit-*`, `Retry-After`, avec l'heure de
reprise), erreur HTTP, TLS, réponse invalide, trop grand, taille ou empreinte fausse, empreinte absente, pas de paquet,
disque, annulé. Délais : 20 s pour l'API, 30 s sans donnée pour un téléchargement. Le smoke test (`main.py
--smoke-test`) vérifie qu'un moteur TLS est disponible dans l'application construite.

## 5. Convention des releases

* **Tag** : `vMAJEUR.MINEUR.CORRECTIF`, ou une préversion `vX.Y.Z-beta.N` / `-rc.N` (cochée « pre-release » par le
  workflow). Il doit être **égal** à `core/app_version.py`.
* **Version dans le bundle macOS** : Apple exige 1 à 3 entiers dans `CFBundleShortVersionString` et `CFBundleVersion`.
  Une préversion (`0.9.1-beta.1`) y est donc écrite `0.9.1` ; la version **complète** est dans la clé `KutStudioVersion`
  d'`Info.plist`, que l'installation automatique lit de préférence (`validate_bundle`) pour la comparer à la release.
  `CFBundleVersion` reste `X.Y.Z` pour toutes les préversions d'un même numéro (non strictement croissant entre `beta.1`
  et `beta.2` : seule la version complète les distingue).
* **Paquets** : `Kut-Studio-<version>-<système>-<architecture>.<extension>`

  | Fichier | Contenu | Construit sur |
  | --- | --- | --- |
  | `Kut-Studio-X.Y.Z-macos-arm64.zip` | `Kut-Studio.app` (archive `ditto`) | `macos-latest` (Apple Silicon, vérifié) |
  | `Kut-Studio-X.Y.Z-windows-x64.zip` | dossier `Kut-Studio` | `windows-latest` |
  | `Kut-Studio-X.Y.Z-linux-x64.tar.gz` | dossier `Kut-Studio` (droits conservés) | `ubuntu-24.04` (glibc ≥ 2.39) |

  Architectures reconnues : `x64`, `arm64`. Le choix est **exact** : un système sans paquet à son architecture se voit
  expliquer qu'il n'y en a pas (un Mac Intel ne reçoit jamais le paquet Apple Silicon). Exception voulue : une copie x64
  sous Rosetta reçoit le paquet `arm64`, natif pour la machine.
* **Empreintes** : `SHA256SUMS.txt`, une ligne `<sha256>  <nom>` par paquet (format `sha256sum`), vérifiable à la main :
  `shasum -a 256 -c SHA256SUMS.txt --ignore-missing`.

## 6. Publier une version

1. Mettre `APP_VERSION` à jour dans `core/app_version.py` (ex. `0.2.0`) et, dans le même commit, renommer la section
   `[Unreleased]` de `CHANGELOG.md` en `[0.2.0] - AAAA-MM-JJ` puis rouvrir une section `[Unreleased]` vide ;
   fusionner dans `main`, attendre la CI verte.
   Si les dépendances doivent changer pour cette version, mettre `constraints.txt` à jour dans ce commit : la release
   installe exactement ces versions (`pip install -c constraints.txt`), alors que la CI suit les plages de
   `requirements*.txt`. Reprendre les versions de la ligne « Successfully installed » d'un run vert de la CI sur `main`
   (`gh run view <id> --log | grep "Successfully installed"`) ; `tests/test_release_workflow.py` vérifie que chaque
   dépendance directe y est figée dans sa plage.
2. Facultatif mais conseillé : *Actions › Release › Run workflow* sur `main`. C'est une **construction d'essai** : les
   trois paquets, `SHA256SUMS.txt` et les notes sont produits en artefacts, **rien n'est publié**.
3. Créer et pousser le tag : `git tag v0.2.0 && git push origin v0.2.0`.
4. Le workflow `.github/workflows/release.yml` :
   * refuse un tag différent de `APP_VERSION` (`python -m tools.release check-tag`) ;
   * sur chaque système : vérifie la machine (`check-target`), lint, tests, smoke test des sources, construction
     (`build.py`), smoke test de l'application construite ; sous macOS, signature et notarisation si les secrets
     existent ;
   * produit les archives conventionnelles, écrit et vérifie `SHA256SUMS.txt` (`checksums`, `verify`) ;
   * signe `SHA256SUMS.txt` si le secret `UPDATE_SIGNING_KEY` existe (`sign`, section 10) ; sinon la release part
     **sans signature** et le dit dans le résumé du job et dans les notes ;
   * rédige les notes (état réel de la signature macOS, en clair, puis les notes générées par GitHub) ;
   * publie la release (`gh release create` : brouillon, envoi de tous les fichiers, puis publication — l'application
     ne voit jamais une release incomplète). Seul ce job a le droit d'écrire sur le dépôt.
5. Relire la release publiée ; au besoin compléter ses notes (elles s'affichent dans l'application).

Toutes les étapes sont rejouables à la main : `python -m tools.release --help`.

**Si un job de construction reste bloqué** (v0.3.0 : le job Windows, muet 50 min à 94 % des tests, annulé à 75 min).
Une suite saine a duré jusqu'à 38 min sous Windows (v0.7.0) ; l'étape « Run test suite » est bornée à 60 min. Avant cela, un test
bloqué depuis 7 min écrit `Timeout (0:07:00)!` dans le journal, suivi de la pile Python de chaque thread de son worker
(`faulthandler_timeout`, qui fonctionne même quand un blocage natif tient le GIL et fige le délai de pytest-timeout) :
le haut de la pile du thread principal désigne le test et l'appel natif en cause. Le test n'est pas interrompu par ce
vidage. Relancer ensuite le seul job en échec, sur le même tag : `gh run rerun <id> --failed`.

## 7. Signature Developer ID et notarisation macOS

Sans secrets, le paquet macOS est **signé ad hoc et non notarisé** : le journal, le résumé du job et les notes de la
release le disent (« non signé et non notarisé » ; macOS bloquera la première ouverture). Avec les secrets, `build.py`
fait signer chaque binaire par PyInstaller avec le runtime renforcé et `tools/release/entitlements.plist`, puis le
workflow notarise et agrafe le ticket (`tools/release/macos_signing.sh`). Si les secrets sont là et que la notarisation
échoue, le job échoue et **rien n'est publié**.

Budget de temps du job de construction : 150 min = tests 60 + construction/signature ~10 + notarisation 60
(`xcrun notarytool submit --wait --timeout 60m`) + marge ; `test_release_workflow` vérifie cette relation. Avec l'ancien
plafond de 75 min, la première soumission (v0.9.1-beta.1) était encore « en cours » chez Apple quand le job a été annulé :
les secrets et la signature étaient bons, mais le tag est resté sans release (« rien n'est publié » = aucun fichier, aucune
release, les utilisateurs ne voient rien). `gh run rerun <id> --failed` ne suffit pas si le workflow a changé : une relance
reprend la définition du commit du tag ; il faut un nouveau tag (version suivante dans `core/app_version.py`).

Secrets (*Settings › Secrets and variables › Actions*, jamais dans le dépôt — `.gitignore` refuse `*.p12`, `*.p8`… et
`test_release_workflow` vérifie qu'aucun n'est suivi) :

| Secret | Contenu | Comment l'obtenir |
| --- | --- | --- |
| `MACOS_CERTIFICATE_P12_BASE64` | Certificat **Developer ID Application** et sa clé privée, export `.p12`, en base64 | Compte Apple Developer (programme payant) › Certificates › Developer ID Application ; l'importer dans Trousseau, l'exporter en `.p12`, puis `base64 -i cert.p12 \| pbcopy` |
| `MACOS_CERTIFICATE_PASSWORD` | Mot de passe choisi à l'export du `.p12` | — |
| `MACOS_SIGNING_IDENTITY` | Nom exact de l'identité, ex. `Developer ID Application: Nom (TEAMID1234)` | `security find-identity -v -p codesigning` |
| `APPLE_API_KEY_P8_BASE64` | Clé d'API App Store Connect (`.p8`), en base64 | App Store Connect › Users and Access › Integrations › App Store Connect API › clé avec le rôle *Developer* ; le `.p8` ne se télécharge qu'une fois |
| `APPLE_API_KEY_ID` | Identifiant de cette clé | même page |
| `APPLE_API_ISSUER_ID` | *Issuer ID* | même page |

Les trois premiers suffisent pour signer (paquet « signé, non notarisé », annoncé comme tel) ; les trois derniers
ajoutent la notarisation. Le certificat est importé dans un trousseau temporaire supprimé en fin de job (même en échec),
la clé `.p8` est décodée dans le dossier temporaire du runner puis effacée ; aucun secret n'est affiché.

Droits du runtime renforcé (`tools/release/entitlements.plist`) : `device.audio-input` (enregistrement de voix off ;
`build.py` ajoute aussi `NSMicrophoneUsageDescription` à `Info.plist`) et `cs.allow-unsigned-executable-memory`
(rappels ctypes / libffi, par prudence). Aucune exception à la validation des bibliothèques. **À vérifier lors de la
première construction notarisée** : smoke test, aperçu, export et voix off ; retirer `allow-unsigned-executable-memory`
si tout fonctionne sans.

Windows (Authenticode) n'est pas signé à ce jour : c'est l'étape suivante pour éviter l'avertissement SmartScreen.

## 8. Site public

Le site (`https://kut-studio.pages.dev`) lie directement les fichiers d'une version précise. Constaté le 7 octobre 2026
(pages `/`, `/en` et `/es`) : les trois paquets et `SHA256SUMS.txt` de **v0.1.0**
(`…/releases/download/v0.1.0/…`) et ses notes (`…/releases/tag/v0.1.0`) ; plus aucun lien nightly.link. Ces liens
sont écrits en dur (le script du site n'interroge pas l'API) : ils ne suivent pas la release v0.2.0 du 6 octobre et
devront être mis à jour à chaque version, à moins de pointer vers `https://github.com/Kyyzen0/Kut-Studio/releases/latest`
ou de lire l'API pour lier les fichiers conventionnels de la dernière version. Les sources du site ne sont pas dans ce
dépôt.

## 9. Vérifications et limites connues

**Exécuté sur macOS (Apple Silicon, octobre 2026)** :

* la logique pure (versions, convention, choix, empreintes, limites GitHub, contextes des trois systèmes) ;
* le transport Qt réel contre un faux GitHub local (`tests/update_server.py`) : succès, limites 403/429, 404/500,
  réponses illisibles, hors ligne, délais total et d'inactivité, annulation, taille et empreinte fausses, empreinte
  absente, redirection, erreurs disque ;
* le parcours complet dans la vraie fenêtre, la traduction stricte de chaque état dans les trois langues, l'attribut
  de quarantaine réellement posé et relu ;
* une construction PyInstaller locale : version et `NSMicrophoneUsageDescription` dans `Info.plist`, re-signature ad hoc
  validée par `codesign --verify --deep --strict`, moteurs TLS de Qt embarqués, smoke test réussi ; archive `ditto`
  conforme, `shasum -a 256 -c SHA256SUMS.txt` correct, signature intacte après extraction ;
* une vraie recherche HTTPS vers `api.github.com` depuis l'application **construite** (configuration jetable) : réponse
  lue, « à jour » (aucune release n'était encore publiée), date de recherche enregistrée ;
* la fluidité : un paquet réel de 54,6 Mo servi en local au plus vite, vérifié au fil de l'eau, n'a jamais bloqué la
  boucle d'événements plus de 23 ms (`fsync` et renommage compris) ;
* les deux workflows validés contre le schéma officiel des workflows GitHub (`check-jsonschema`).

**Exécuté sur GitHub Actions (releases v0.1.0 et v0.2.0)** : le workflow de release, déclenché par le push du tag, a
réussi deux fois, tous jobs compris (tag et version, construction macos-arm64, windows-x64 et linux-x64, empreintes et
notes, publication) : run 37294236977 pour v0.1.0 (publiée le 5 octobre 2026), run 37522688658 pour v0.2.0 (publiée le
6 octobre 2026). Chaque release, ni brouillon ni préversion, porte les trois paquets conventionnels
(`Kut-Studio-<version>-macos-arm64.zip`, `-windows-x64.zip`, `-linux-x64.tar.gz`) et `SHA256SUMS.txt` (vérifié le
7 octobre 2026 avec `gh release view` et `gh run view`). Dans les deux runs, le job macOS a signalé l'absence de
`MACOS_CERTIFICATE_P12_BASE64` et produit un paquet signé ad hoc, non notarisé, annoncé comme tel dans les notes.

**Couvert seulement par simulation ou par lecture** : les chemins Windows et Linux (contexte d'installation, flux
`Zone.Identifier` — un test réel ne s'exécute que sur la CI Windows —, archives `zip`/`tar.gz` construites par les tests
mais jamais installées : aucune installation réelle des paquets publiés n'a été vérifiée sous Windows ni sous Linux),
les redirections HTTPS de GitHub vers son CDN pour un vrai paquet, la signature Developer ID et la notarisation (aucun
certificat disponible :
le chemin « secrets présents » de `macos_signing.sh` n'a jamais tourné), les droits du runtime renforcé, et
l'architecture `macos-x64` (aucun paquet construit : un Mac Intel est informé qu'il n'y en a pas). La construction
locale utilisait Python 3.14 ; la CI et le workflow de release utilisent Python 3.11.

## 10. Signature des mises à jour

Décision et justification : [ADR-0012](adr/0012-mise-a-jour-automatique.md).

**Ce que la signature prouve.** `SHA256SUMS.txt` (qui nomme chaque paquet et donne son SHA-256) a été signé par un
détenteur de la clé privée Ed25519 dont la moitié publique est dans `core/update_keys.py`. Un attaquant qui remplace un
paquet et l'empreinte sur GitHub, ou qui modifie `SHA256SUMS.txt` après coup, ne sait pas fabriquer une signature
valide. Elle ne dit **rien** de plus : voir l'ADR, « Ce que la signature ne protège pas ».

**Fichier.** `SHA256SUMS.txt.sig` : une ligne ASCII `ed25519 <identifiant de clé> <signature en base64>`. L'identifiant est
les 16 premiers caractères hexadécimaux du SHA-256 de la clé publique. Il permet de distinguer une clé inconnue de cette
version (release signée par une clé plus récente) d'une signature fausse.

**Résultat dans l'application.** `VERIFIED` (signature valide d'une clé connue), `ABSENT` (pas de `.sig` : comportement
d'avant, installation assistée, pas une erreur), `INVALID` (illisible, clé inconnue, signature fausse, ou
`SHA256SUMS.txt` modifié depuis la signature : paquet refusé, message clair). Aide › À propos affiche l'identifiant des
clés de confiance de cette copie : comparez-le à celui annoncé par le mainteneur.

**Première mise en place (une fois, par le mainteneur, sur sa machine).**

1. `python -m tools.release gen-update-key --out ~/kut-studio.update-key` : crée la graine secrète (droits 600, jamais
   écrasée, **jamais affichée**) et affiche la clé publique et son identifiant.
2. Coller la ligne `PublicKey.from_base64("…")` affichée dans `UPDATE_PUBLIC_KEYS` de `core/update_keys.py`, ouvrir la PR,
   relire, fusionner.
3. `gh secret set UPDATE_SIGNING_KEY < ~/kut-studio.update-key` (le secret ne passe pas par l'écran). Garder le fichier
   dans un coffre ; ne jamais l'ajouter au dépôt (`*.update-key` est ignoré).
4. Publier normalement. Si la clé publique du secret n'est pas dans `UPDATE_PUBLIC_KEYS`, le job échoue (sinon chaque
   mise à jour serait refusée par les applications). Sans secret, la release part sans signature et les notes disent
   « mises à jour automatiques indisponibles pour cette version ».

**Rotation (deux versions).** En régime normal, `UPDATE_PUBLIC_KEYS` contient la clé courante. (1) Version N : générer la clé
suivante, ajouter sa clé publique **en second** dans la liste (sans changer le secret) et publier N signée par l'ancienne
clé : toutes les copies à jour connaissent alors les deux clés. (2) Version N+1 (ou plus tard, quand la plupart des
copies ont N) : remplacer le secret `UPDATE_SIGNING_KEY` par la nouvelle graine, publier N+1 signée par la nouvelle clé,
puis retirer l'ancienne clé de la liste dans la version suivante. Une copie qui saute N ne connaît pas la nouvelle clé :
elle refuse N+1 (« clé inconnue ») et l'utilisateur installe à la main.

**Fuite ou perte de la clé.** Fuite : révoquer le secret GitHub, générer une nouvelle clé, publier un avis de sécurité et une
version dont **toute** la liste est remplacée, signée par la clé **suivante** déjà en service si elle existe (sinon par la
nouvelle, et les copies existantes demandent une installation manuelle). Une release signée par la clé compromise reste
refusée dès que la liste ne la contient plus. Perte sans fuite : même procédure, sans urgence.

**Fonctionnement et limites.** Pas de nouvelle dépendance : `core/ed25519.py` (RFC 8032 en Python pur, `hashlib.sha512`)
signe et vérifie ; il est testé contre les vecteurs de la RFC et d'OpenSSL, et le smoke test de l'application construite
vérifie une signature de démonstration. Le code n'est pas à temps constant : la graine ne doit tourner que dans le workflow
isolé de GitHub Actions et sur la machine du mainteneur pour `gen-update-key`.

## 11. Version Mac App Store (ADR-0013, étape 4)

Le Mac App Store est le seul canal de mise à jour d'un bundle Store (Guideline 2.4.5 [A]). `core/build_flavor.py` expose
`STORE` (faux par défaut : sources, `python build.py`, CI, Developer ID : **aucun changement**). Il n'est vrai que dans un
bundle construit par `python build.py --flavor store`, qui écrit `core/_build_flavor_generated.py` (ignoré par git, retiré après
la construction, ajouté au bundle par `--hidden-import`). **Jamais** une variable d'environnement ni une option de
l'utilisateur.

Quand `STORE` est vrai :

* pas d'entrée Aide › « Rechercher des mises à jour… » (donc pas dans la palette), pas de bouton « Mise à jour X » dans la barre,
  pas de groupe « Mises à jour » dans les préférences, pas de recherche au démarrage ;
* `core.updates`, `core.update_service`, `core.update_install`, `core.update_helper`, `core.update_keys`,
  `core.update_signature`, `ui.update_auto_install`, `ui.update_dialog` et `ui.main_window_mixins.updates` ne sont **pas importés**
  (`ui/main_window_mixins/update_flavor.py` choisit le mixin léger `updates_store.py`) et sont exclus du bundle ; aucun
  `QNetworkAccessManager` n'est construit ; le smoke test ne vérifie plus TLS ni les signatures de mise à jour ;
* gardes côté modèle (un signal ou un appel direct ne passe pas) : `HttpClient`, `UpdateChecker.check`, `UpdateDownloader.start`,
  `update_helper.launch_helper`, `update_install.stage_update` lèvent `StoreRestrictionError` ; `automatic_checks_allowed` répond
  toujours faux (même sans `KUT_STUDIO_UPDATE_CHECK=off`) ;
* Aide › À propos dit « Mac App Store » : ni page de releases, ni lien du site, ni clés de signature ;
* les quatre champs de réglages « mises à jour » sont relus et réécrits tels quels (format du fichier inchangé), jamais lus ;
* FFmpeg et ffprobe ne se résolvent que dans le bundle (`core/tool_paths.py`) : ni `PATH`, ni Homebrew / MacPorts, ni
  `KUT_STUDIO_FFMPEG`, `KUT_STUDIO_FFPROBE`, `KUT_STUDIO_FFMPEG_DIR`. S'ils manquent : « FFmpeg est introuvable dans l'application…
  réinstallez depuis le Mac App Store », sans repli. `--flavor store` refuse de construire sans eux (`KUT_STUDIO_FFMPEG_DIR` au moment
  de la construction, voir [ffmpeg-lgpl.md](ffmpeg-lgpl.md)).

Tests : `tests/test_build_flavor_store.py` (sous-processus hors écran qui démarre l'application dans chaque saveur).

