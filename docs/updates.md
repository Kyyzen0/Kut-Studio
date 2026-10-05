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
| Paquet vérifié | Ce que garantit la vérification, l'authenticité selon le système, les étapes d'installation du système, **Ouvrir le dossier**, **Quitter et installer…**. |
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

Sinon le fichier est supprimé, jamais ouvert. Une release sans `SHA256SUMS.txt`, ou qui n'y liste pas le paquet, n'est
pas téléchargeable depuis l'application (seule la page de la version est proposée).

**Intégrité, pas authenticité.** `SHA256SUMS.txt` vient de la même release que le paquet : il prouve que le fichier est
complet et identique à celui publié (pas de coupure, de corruption, de mauvais fichier servi par un cache). Il ne prouve
pas *qui* a publié : quelqu'un qui pourrait remplacer le paquet pourrait remplacer l'empreinte. L'authenticité repose
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

Une installation **automatique** n'est pas faite dans cette version, volontairement. Avec le packaging actuel
(PyInstaller *onedir*, archives sans installeur), une installation fiable demanderait : un processus distinct lancé
après la fermeture (un exécutable Windows et ses DLL sont verrouillés tant qu'ils tournent), la vérification de la
signature du **nouveau** bundle avant de toucher à l'ancien (`codesign`/`spctl`, `WinVerifyTrust`), la gestion des
droits (Applications, Program Files, dossiers système Linux), une sauvegarde et un retour arrière si l'échange échoue
ou si la nouvelle version ne démarre pas, et la relance. Rien de cela ne peut être vérifié ici pour Windows et Linux ;
le livrer sans vérification serait une auto-installation fragile. La base est prête (paquet vérifié, contexte
d'installation `core.updates.install_context`, fermeture propre) pour l'ajouter ensuite.

## 4. Architecture

| Information | Source unique | Lue par | Test |
| --- | --- | --- | --- |
| Version de l'application | `core/app_version.py` (`APP_VERSION`) | recherche, À propos, Préférences, `build.py` (`Info.plist`), `tools.release check-tag` | `test_versioning`, `test_release_tools` |
| Ordre des versions | `core/versioning.py` (`Version`, SemVer 2.0, jamais de comparaison de texte) | `core.updates` | `test_versioning` |
| Convention des paquets, cibles, `SHA256SUMS.txt` | `core/release_assets.py` | application **et** `tools/release` | `test_release_assets`, `test_release_tools`, `test_release_workflow` |
| Que proposer, que refuser | `core/updates.py` (pur : ni réseau ni Qt) | `core.update_service`, interface | `test_updates_core` |
| Transport HTTPS, délais, annulation, vérification | `core/update_service.py` (`QNetworkAccessManager`) | `UpdatesMixin` | `test_update_transport` (faux GitHub local) |
| Parcours et textes | `ui/main_window_mixins/updates.py`, `ui/update_dialog.py`, `ui/i18n_updates.py` | — | `test_update_ui` |

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

1. Mettre `APP_VERSION` à jour dans `core/app_version.py` (ex. `0.2.0`), fusionner dans `main`, attendre la CI verte.
2. Facultatif mais conseillé : *Actions › Release › Run workflow* sur `main`. C'est une **construction d'essai** : les
   trois paquets, `SHA256SUMS.txt` et les notes sont produits en artefacts, **rien n'est publié**.
3. Créer et pousser le tag : `git tag v0.2.0 && git push origin v0.2.0`.
4. Le workflow `.github/workflows/release.yml` :
   * refuse un tag différent de `APP_VERSION` (`python -m tools.release check-tag`) ;
   * sur chaque système : vérifie la machine (`check-target`), lint, tests, smoke test des sources, construction
     (`build.py`), smoke test de l'application construite ; sous macOS, signature et notarisation si les secrets
     existent ;
   * produit les archives conventionnelles, écrit et vérifie `SHA256SUMS.txt` (`checksums`, `verify`) ;
   * rédige les notes (état réel de la signature macOS, en clair, puis les notes générées par GitHub) ;
   * publie la release (`gh release create` : brouillon, envoi de tous les fichiers, puis publication — l'application
     ne voit jamais une release incomplète). Seul ce job a le droit d'écrire sur le dépôt.
5. Relire la release publiée ; au besoin compléter ses notes (elles s'affichent dans l'application).

Toutes les étapes sont rejouables à la main : `python -m tools.release --help`.

## 7. Signature Developer ID et notarisation macOS

Sans secrets, le paquet macOS est **signé ad hoc et non notarisé** : le journal, le résumé du job et les notes de la
release le disent (« non signé et non notarisé » ; macOS bloquera la première ouverture). Avec les secrets, `build.py`
fait signer chaque binaire par PyInstaller avec le runtime renforcé et `tools/release/entitlements.plist`, puis le
workflow notarise et agrafe le ticket (`tools/release/macos_signing.sh`). Si les secrets sont là et que la notarisation
échoue, le job échoue et **rien n'est publié**.

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

Le site (`https://kut-studio.pages.dev`) propose aujourd'hui des liens nightly.link vers les artefacts de
*Multiplatform*. Après la première release, il devrait pointer vers `https://github.com/Kyyzen0/Kut-Studio/releases/latest`
(ou lire l'API pour lier directement les fichiers conventionnels de la dernière version) et rappeler
`SHA256SUMS.txt`. Les sources du site ne sont pas dans ce dépôt.

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
  lue, « à jour » (aucune release publiée à ce jour), date de recherche enregistrée ;
* la fluidité : un paquet réel de 54,6 Mo servi en local au plus vite, vérifié au fil de l'eau, n'a jamais bloqué la
  boucle d'événements plus de 23 ms (`fsync` et renommage compris) ;
* les deux workflows validés contre le schéma officiel des workflows GitHub (`check-jsonschema`).

**Couvert seulement par simulation ou par lecture** : les chemins Windows et Linux (contexte d'installation, flux
`Zone.Identifier` — un test réel ne s'exécute que sur la CI Windows —, archives `zip`/`tar.gz` construites par les tests
mais jamais installées), les redirections HTTPS de GitHub vers son CDN pour un vrai paquet, le workflow de release
(jamais exécuté : aucun tag n'a été créé), la signature Developer ID et la notarisation (aucun certificat disponible :
le chemin « secrets présents » de `macos_signing.sh` n'a jamais tourné), les droits du runtime renforcé, et
l'architecture `macos-x64` (aucun paquet construit : un Mac Intel est informé qu'il n'y en a pas). La construction
locale utilisait Python 3.14 ; la CI et le workflow de release utilisent Python 3.11.
