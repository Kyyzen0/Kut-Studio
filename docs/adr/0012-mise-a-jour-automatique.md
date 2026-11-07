# ADR-0012 : mise à jour automatique — signature Ed25519 des releases (lot 1) et frontière de sécurité

**Statut :** Proposé le 2026-10-22 (l'utilisateur le validera à la relecture de la PR). Le **lot 1** (signature, outil, workflow, vérification, doc) est livré avec cet ADR ; le **lot 2** (installation automatique macOS, programme de remplacement, retour arrière) s'appuie sur l'interface décrite plus bas et fait l'objet de sa propre PR.
**Date :** 2026-10-22
**Décideurs :** mainteneur de Kut-Studio
**S'appuie sur :** `docs/updates.md` (sections 2 et 3 : « intégrité, pas authenticité » et « pourquoi pas automatique »), [ADR-0005](0005-mode-script-frontiere-de-securite.md) (même démarche : écrire la frontière avant le code).

## Contexte : le problème que ce lot règle

Aujourd'hui l'application télécharge un paquet depuis GitHub Releases et le vérifie par sa **taille** et son **SHA-256**, lu dans `SHA256SUMS.txt`, un fichier de la **même release**. Cela prouve que le fichier est complet et identique à celui publié ; cela ne prouve pas **qui** l'a publié. Quiconque peut remplacer le paquet dans la release (compte GitHub compromis, jeton volé, action de workflow compromise) remplace aussi l'empreinte, et l'application accepte. Tant que l'installation reste **assistée** (l'utilisateur déplace lui-même l'application, et macOS contrôle la signature Developer ID et la notarisation à l'ouverture), ce défaut est borné par le système. Une installation **automatique** supprime ce filet : l'application elle-même remplacerait son exécutable par un fichier qu'elle a choisi de croire. Il faut donc une **racine de confiance indépendante de GitHub** avant tout automatisme.

Contraintes : l'application gelée (PyInstaller) n'embarque que PySide6 et numpy ; aucune dépendance nouvelle non figée (`constraints.txt`, test du workflow) ; la clé privée ne doit jamais passer par un agent, un journal ou le dépôt ; le projet est publié par un seul mainteneur depuis GitHub Actions.

## Décision

1. **Une signature détachée Ed25519 du fichier `SHA256SUMS.txt`**, publiée comme `SHA256SUMS.txt.sig`. `SHA256SUMS.txt` nomme chaque paquet et donne son SHA-256 : le signer authentifie, par transitivité, chaque paquet **et son nom** (donc sa version et sa cible). La vérification de taille et d'empreinte existante reste, et la signature s'y ajoute.
2. **La clé publique est dans le dépôt** (`core/update_keys.py`, une liste de clés à identifiant, pour pouvoir faire tourner la clé). **La clé privée n'existe que** dans le secret GitHub `UPDATE_SIGNING_KEY` (graine de 32 octets en base64) et dans un fichier que le mainteneur garde chez lui. Elle ne passe par aucun agent : l'outil `python -m tools.release gen-update-key` est lancé par le mainteneur, écrit la graine dans un fichier de droits 600 (jamais écrasé) et n'affiche que la clé publique et son identifiant.
3. **Trois états côté application** (`core.update_signature.SignatureStatus`) : `VERIFIED`, `ABSENT`, `INVALID`. `ABSENT` (release sans `.sig`) n'est **jamais** une erreur : l'installation assistée continue. `INVALID` (fichier illisible, clé inconnue, signature fausse, `SHA256SUMS.txt` modifié depuis la signature) refuse la version comme une empreinte fausse. Seul `VERIFIED` ouvrira l'installation automatique (lot 2).
4. **Workflow** : une seule étape de signature, dans le job qui assemble les notes, après `checksums` et `verify`. Secret absent : la release part **sans signature** et le dit en clair (résumé du job, notes bilingues : « mises à jour automatiques indisponibles pour cette version »), comme la signature macOS. Secret présent et signature impossible ou invalide : le job **échoue**. La signature est vérifiée dans le workflow avec **le même code que l'application**, contre la liste de clés du dépôt : un secret dont la clé publique n'a pas été collée dans `UPDATE_PUBLIC_KEYS` fait échouer la release au lieu de produire une version que toutes les copies refuseraient.
5. **Retour en arrière** : une version égale ou plus ancienne que l'installée n'est jamais proposée (déjà vrai pour la recherche) ni **téléchargée** (nouveau : `UpdateErrorKind.NOT_NEWER`, vérifié dans `UpdateDownloader.start`, donc aussi sur le chemin de l'installation). Une signature authentifie un contenu, pas une date : sans cette règle, l'attaquant rejouerait une ancienne release signée mais vulnérable.
6. **Pas de nouvelle dépendance** : Ed25519 est implémenté en Python pur (`core/ed25519.py`, RFC 8032, `hashlib.sha512`), pour la vérification (application) comme pour la signature (outil de publication).

## Alternatives écartées

| Option | Pour | Contre | Verdict |
| --- | --- | --- | --- |
| **Ed25519 maison, fichier `.sig` de notre format** | aucune dépendance, 150 lignes, vérifiable par vecteurs, même code des deux côtés | notre format et notre code de crypto à maintenir ; pas de temps constant | **retenu** |
| minisign | format et outil existants, Ed25519 | l'outil `minisign` devrait être installé dans le workflow (action tierce épinglée de plus) ; l'application doit de toute façon implémenter Ed25519 pour vérifier (pas de bibliothèque) ; pas de gain de confiance, la racine reste notre clé | écarté : le format est trivial à reproduire et le vérificateur reste à écrire |
| Sparkle (EdDSA) | standard macOS | n'existe que pour les applications Cocoa ; Kut-Studio est PySide6 + PyInstaller, sans framework Sparkle ; macOS seulement | écarté |
| `cryptography` / PyNaCl | audités, rapides, temps constant | dépendance native de plus dans le paquet gelé (taille, builds par système, bibliothèque dynamique à signer et notariser), à figer dans `constraints.txt` | écarté tant qu'une signature par release et une vérification par mise à jour suffisent à Python pur ; réexaminer si l'application embarque un jour `cryptography` pour une autre raison |
| Signature GPG | écosystème connu, chaîne de confiance | trousseau et gpg à installer chez l'utilisateur ou à embarquer ; ergonomie et échecs silencieux ; gestion de clés bien plus lourde que nécessaire pour une clé unique | écarté |
| Rien (statu quo) | aucun travail | l'installation automatique ne peut pas être livrée honnêtement : le fichier qui désigne le paquet vient du même endroit que le paquet | écarté pour l'automatique ; **reste le comportement** des releases non signées (installation assistée) |

**Limites assumées de l'implémentation en Python pur.** Pas de protection contre les attaques par canaux auxiliaires (durées dépendantes de la clé) : sans importance pour la **vérification** (tout est public), importante pour la **signature**. La graine ne tourne donc que dans le workflow isolé de GitHub Actions (runner éphémère, un seul usage) et, brièvement, sur la machine du mainteneur pour `gen-update-key` (génération, pas de signature). Comparaisons d'identifiants et de signatures par `hmac.compare_digest`. La vérification est stricte (S < L, encodages canoniques de la clé et de R, équation vérifiée par comparaison des encodages) et testée contre les vecteurs de la RFC 8032 et des signatures d'OpenSSL ; elle tourne dans l'application gelée, ce que le smoke test (`main.py --smoke-test`) prouve avec un vecteur de démonstration.

## Format du fichier de signature

Une ligne ASCII : `ed25519 <identifiant de clé, 16 hexadécimaux> <signature, 64 octets en base64>` + saut de ligne, au plus 1 024 octets lus. Texte plutôt que binaire : lisible, sans ambiguïté de transfert, et **l'identifiant de clé** (début du SHA-256 de la clé publique) distingue « clé inconnue de cette version » (la release est signée par une clé plus récente : message dédié, installation manuelle) de « signature fausse ». L'identifiant n'est qu'un aiguillage : la validité ne dépend que de la vérification Ed25519.

## Rotation de la clé

Deux clés au plus dans `UPDATE_PUBLIC_KEYS` : la courante et la suivante. Procédure **en deux versions** : (1) la version N ajoute la clé suivante en second dans la liste, signée par l'ancienne ; (2) une fois N répandue, le secret passe à la nouvelle graine et la version N+1 est signée par la nouvelle ; la version suivante retire l'ancienne clé. Une copie qui saute N refuse N+1 (clé inconnue) et l'utilisateur installe à la main : refus voulu, jamais de confiance accordée à une clé non listée. Détail opérationnel : `docs/updates.md`, section 10.

## Ce que la signature ne protège pas

* **Compromission du secret GitHub ou du runner de build** : l'attaquant signe ce qu'il veut. La signature atteste « produit par ce workflow avec cette clé », pas « le code est honnête ». Atténuation : secret limité à une étape d'un seul job, jamais affiché (variable d'environnement, masquage GitHub), actions épinglées par SHA (déjà vérifié par `tests/test_release_workflow.py`).
* **Compromission de la machine du mainteneur** (fichier de graine volé) : même conséquence. Atténuation : droits 600, coffre, jamais dans le dépôt (`*.update-key` ignoré).
* **Un attaquant qui contrôle le dépôt** peut changer `core/update_keys.py` : les copies existantes, qui ont l'ancienne liste, refuseront ses releases, mais quiconque installe ou met à jour **manuellement** une version piégée lui fait confiance. D'où l'**identifiant de clé affiché dans Aide › À propos** : un utilisateur averti le compare à celui annoncé par le mainteneur (page du site, annonces). Ce n'est pas une garantie automatique.
* **Un code de l'application compromis avant la mise à jour**, une version déjà installée malveillante, ou un système d'exploitation compromis.
* **Le contenu des paquets** : la signature authentifie l'origine, pas l'absence de bogue ou de porte dérobée introduite dans le code source avant la construction.
* **Le retour en arrière** n'est écarté que par la règle de version (5) : la signature seule n'a pas de date.
* **Windows et Linux** : rien ne contrôle la signature du *système* sur ces paquets (non signés Authenticode) ; la signature Kut-Studio s'applique déjà à toutes les cibles, mais l'installation automatique n'y est pas faite.

## En cas de fuite de la clé

Révoquer le secret GitHub immédiatement et cesser de publier ; générer une nouvelle clé ; publier un **avis de sécurité** (page de la release, site, README) donnant l'identifiant de l'ancienne clé comme compromis. Si la clé suivante est déjà dans la liste des copies installées : signer la version de remplacement avec elle et **retirer l'ancienne** de la liste dans cette version. Sinon, les copies existantes ne connaissent aucune clé de remplacement : elles refuseront la nouvelle release, et chaque utilisateur doit réinstaller à la main, avec l'identifiant annoncé comme repère. C'est la raison de la procédure en deux versions : **une clé suivante toujours déjà distribuée** transforme une fuite en simple rotation.

## Interface avec le lot 2 (installation automatique macOS)

Dans `core/update_signature.py` : `SignatureStatus` (`VERIFIED`, `ABSENT`, `INVALID`), `SignatureResult(status, key_id, detail, unknown_key)` et `verify_release_signature(sums: bytes, signature: bytes | None, keys=UPDATE_PUBLIC_KEYS) -> SignatureResult`. `UpdateDownloader` vérifie la signature sur les octets exacts de `SHA256SUMS.txt` **avant** de télécharger le paquet (une signature fausse n'écrit rien sur le disque) et expose `DownloadResult.signature` et `DownloadResult.signature_status` (`ABSENT` si le téléchargement a échoué ou si la release n'est pas signée). Paramètres optionnels, valeurs par défaut sans effet sur l'interface actuelle : `current` (version installée, défaut `APP_VERSION`), `keys` (défaut `UPDATE_PUBLIC_KEYS`), `require_signature` (défaut `False` : `True` refuse une release non signée avec `SIGNATURE_MISSING`, pour l'installation automatique). Erreurs nouvelles dans `UpdateErrorKind` : `SIGNATURE_MISSING`, `SIGNATURE_INVALID`, `SIGNATURE_UNKNOWN_KEY`, `NOT_NEWER`. Pour les tests du lot 2 : `tests/fake_update_keys.py` (`FAKE_KEYS`, `sign_with_fake_key`) simule `VERIFIED` sans le vrai secret.

**Condition d'installation automatique** : `signature_status is VERIFIED` **et** version strictement plus récente que l'installée ; sinon, installation assistée.

## Hors périmètre

* L'installation automatique elle-même (lot 2) et, après lui, Windows et Linux (verrouillage des exécutables, droits d'écriture, `WinVerifyTrust`) : non faits, et non vérifiables sans ces systèmes.
* La signature de code des plates-formes (Authenticode, Developer ID) : inchangée (section 7 de `docs/updates.md`).
* Un journal de transparence ou une seconde signature indépendante (deux mainteneurs) : à envisager si le projet a un jour plusieurs personnes pouvant publier.

## Conséquences

* Les releases publiées **sans** secret restent valides et installables à la main ou de façon assistée.
* La première release signée suppose, une fois, `gen-update-key`, une PR qui colle la clé publique et `gh secret set` ; avant cela l'application affiche « aucune clé » dans À propos et se comporte comme avant.
* Ce qui n'est **pas vérifiable sans la vraie clé** : la signature d'une vraie release dans GitHub Actions, le secret `UPDATE_SIGNING_KEY` côté GitHub, et la vérification d'un vrai paquet publié (les tests couvrent le code avec des clés factices et un faux GitHub local).
