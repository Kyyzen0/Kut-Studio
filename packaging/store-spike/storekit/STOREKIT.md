# Spike du pont StoreKit 2 (ADR-0013, étape 13)

**Question.** Un petit module Swift (bibliothèque dynamique, interface C minimale) appelé par `ctypes` depuis Python peut-il porter
`Transaction.currentEntitlements`, `AppTransaction.shared`, `Transaction.updates` et `AppStore.sync()` ? Rien dans `core/` ni `ui/` ne
change : tout est dans ce dossier, **jetable**.

Étiquettes : **[V]** mesuré sur cette machine (Xcode 27.1, Swift 6.4, macOS 27.2, Apple Silicon, 2026-10-29), **[S]** supposé,
**[A]** à vérifier chez Apple (compte payant, App Store Connect, TestFlight).

## Verdict

**Faisable [V].** Le pont Swift + `ctypes` compile, se charge dans le Python du venv et dans un bundle PyInstaller signé **sous App Sandbox
sans `com.apple.security.network.client`**, appelle les quatre API, refuse tout résultat `.unverified`, et se démonte proprement. PyObjC
n'est **pas** une option pour StoreKit 2 [V] (voir la table). Reste, avec un vrai compte : l'`AppTransaction` réelle, le comportement des
essais, le hors ligne réel et l'achat réel (liste en fin de document).

## Contenu

| Fichier | Rôle |
| --- | --- |
| `kutstore.h` | Interface C (version d'ABI 1, codes d'état, opérations). |
| `KutStoreBridge.swift` | Le pont (une seule source, ~215 lignes). |
| `storekit_contract.py` | Contrat Python `ctypes` : lit l'ABI avant tout, copie puis libère les chaînes, borne l'argument. **Jetable, pas dans `core/`.** |
| `build.sh` | `swiftc -emit-library` arm64, macOS 13+. |
| `Kut-Studio.storekit` | Configuration StoreKit locale : groupe « Kut-Studio Pro » (mensuel `com.kutstudio.app.monthly`, annuel `com.kutstudio.app.yearly`) + non consommable `com.kutstudio.app.lifetime`. **Identifiants provisoires.** |
| `XCTestHost/` | Projet Xcode jetable : application hôte vide + tests XCTest avec `SKTestSession` (`run_tests.sh`). |
| `bridge_probe.py`, `build_bridge_probe.sh` | Sonde exécutée dans un bundle PyInstaller (comme `probe.py` du spike bac à sable). |
| `../../../tests/test_store_spike_storekit.py` | Tests CI macOS **sans compte** : compilation, chargement, version, arguments, arrêt. |

## Conception de l'interface C

* **Bloquante, sans rappel.** `kutstore_call(op, arg, timeout_ms, &out)` lance une `Task` Swift détachée et attend un sémaphore
  (délai borné à 1 ms – 120 s). **Aucun appel de Swift vers Python** : pas de rappel, donc aucun risque de réveiller du Python depuis
  un thread inconnu, ni de verrou GIL pris au mauvais moment. `ctypes` relâche le GIL pendant l'appel. L'appelant est un **thread de
  travail Python, jamais le thread Qt principal** (un appel dure ~0,4 s hors Xcode, voir plus bas).
* **Mises à jour par scrutation.** `Transaction.updates` alimente une file côté Swift ; `kutstore_poll_update(timeout_ms, &out)` bloque
  jusqu'à un événement. Chaque transaction vérifiée est terminée (`finish()`) avant d'être publiée ; une non vérifiée est publiée comme
  `{"verified": false}` **sans** `finish()` et sans droit.
* **Mémoire.** `*out` est une chaîne JSON UTF-8 allouée par le pont (`strdup`), **toujours renseignée** (même en erreur), libérée **une
  fois** par l'appelant avec `kutstore_free`. Le contrat Python copie puis libère dans un `finally`.
* **Aucune exception ne franchit la frontière** : toute erreur Swift devient `{"error": ..., "detail": <300 caractères>}` + un code
  (`STOREKIT_ERROR`, `UNVERIFIED`, `TIMEOUT`…). `UNVERIFIED` (4) pour une `AppTransaction` non vérifiée JWS ; pour les droits, les
  transactions non vérifiées sont **comptées** (`rejected`) et jamais rendues.
* **Délai dépassé.** La tâche est annulée ; le résultat tardif est écrit dans une boîte partagée que personne ne lit (pas de pointeur
  qui pend). Une annulation devient `SHUTDOWN` (5).
* **Arrêt.** `kutstore_shutdown()` (idempotent) annule les tâches, **réveille** les `kutstore_poll_update` en attente (code 5) et refuse
  les appels suivants (code 5). Mesuré : le fil en attente se réveille en 0,2 ms et le processus se termine avec le code 0 sans blocage.
* **Entrées démesurées.** Argument limité à 8192 octets côté Swift **avant** allocation (`strnlen` borné, testé en contournant la garde
  Python) et côté Python ; 32 identifiants de produit au plus, 200 octets chacun ; les messages d'erreur sont tronqués et ne recopient
  pas l'entrée.
* **Version.** `kutstore_abi_version()` est lu au chargement ; s'il diffère de 1, `StoreBridge` lève `StoreBridgeError` et ne lie aucune
  autre fonction. Un `.h` et un contrat Python décalés sont détectés par un test.

## Mesures

### (1) Compilation [V]

| | |
| --- | --- |
| Commande | `swiftc -emit-library -O -module-name KutStoreBridge -target arm64-apple-macos13.0 -swift-version 5 -install_name @rpath/libkutstore.dylib` |
| Durée | ≈ 4 s |
| Taille | 122 192 octets ; 139 776 une fois signée ; 103 024 après `strip -x` (≈ 0,1 Mo) |
| `otool -L` | `libSystem`, `CoreFoundation`, `Foundation`, **`StoreKit`**, `libobjc`, et `/usr/lib/swift/libswiftCore`, `libswift_Concurrency`, `libswiftDispatch`… (plus des `libswift*` en liaison faible) : **tout est du système**, rien à embarquer ni à signer en plus que la bibliothèque. Un test le vérifie. |
| Mode Swift | `-swift-version 5` : en mode Swift 6, `NSLock` dans un contexte asynchrone est une erreur ; le verrou est donc dans une fonction synchrone. |
| Cible | macOS 13 minimum (`AppTransaction`, StoreKit 2 sur Mac). `Product.purchase(confirmIn:)` avec une fenêtre n'existe qu'à partir de macOS 15.2 (voir « Achat »). |

### (2) Chargement par `ctypes` [V]

* Python du venv : `CDLL` + lecture de l'ABI : **2,2 – 3,7 ms**.
* Bundle PyInstaller `--windowed` (`BridgeProbe.app`, identifiant `com.kutstudio.app`, 20 Mo) : PyInstaller place la bibliothèque dans
  `Contents/Frameworks/` (lien dans `Resources/`), `sys._MEIPASS` la retrouve. Signée binaire par binaire avec `../sign.sh` (même
  identité que le reste du bundle, runtime renforcé, **sans** `--deep`) : **la validation de bibliothèque passe**, pas besoin de
  `disable-library-validation`. Lancée par `open` : mêmes résultats.
* Dépendance de plateforme : `StoreBridge` lève `StoreBridgeError` hors macOS.

### (3) Test local sans compte payant [V sauf mention]

`SKTestSession` refuse de tourner hors d'un processus de test (abort dans `-[SKTestSession bundleID]`) **et** dans le processus nu
`xctest` : storekitd répond « Unable to get entitlements for com.apple.dt.xctest.tool: kLSApplicationNotFoundErr » car LaunchServices
ne connaît pas cet identifiant. **Contournement retenu : application hôte vide** (`XCTestHost/`, signature ad hoc, aucun compte) ; le
processus de test porte alors l'identifiant `com.kutstudio.storekitspike`. Les tests XCTest `dlopen` **la même** `libkutstore.dylib`
que Python et n'appellent que son interface C. `./run_tests.sh` : **9 tests, 0 échec, 0,33 s**.

| Cas | Résultat |
| --- | --- |
| `Product.products(for:)` sur le fichier `.storekit` | 3 produits (non consommable, mensuel, annuel avec période) [V] |
| Aucun achat | `currentEntitlements` = 0 [V] |
| Achat du mensuel (`Product.purchase`) | 1 droit vérifié : `productID`, `expirationDate` à +1 mois, `environment` = `Xcode` [V] |
| Achat unique | 1 droit, `expirationDate` nul [V] |
| `expireSubscription` | plus de droit [V] |
| `Transaction.updates` après `forceRenewalOfSubscription` | 1 événement vérifié, `finish()` appelé [V] |
| `AppStore.sync()` | code 0 [V] |
| `AppTransaction.shared` | vérifiée, `environment` = `Xcode`, **`originalPurchaseDate` = 1970-01-01** [V] |
| Erreur de service simulée (`setSimulatedError`, réseau) sur le chargement des produits | code 3 (`STOREKIT_ERROR`), pas d'exception [V] ; `currentEntitlements` reste lisible [V] |
| Arguments démesurés / inconnus / vides | code 2 [V] |

**Ce que les tests locaux ne prouvent pas, et pourquoi :**

* `SKTestSession.buyProduct(identifier:)` (achat « hors appareil ») échoue dans cette configuration (« Off-device purchase… Unable to
  Complete Request ») ; on achète donc par l'API publique `Product.purchase(confirmIn:)`, qui exige **une fenêtre** (sinon
  `invalidPresentationContext`) et macOS 15.2+.
* `SKTestSession.refundTransaction(identifier:)` ne retourne pas d'erreur mais **n'a aucun effet observable** (ni `revocationDate`, ni
  sortie de `currentEntitlements`, même lu directement par `Transaction.latest`). **Le remboursement n'est donc pas prouvé** : seul le
  code de repli (une transaction `revocationDate` non nulle doit être exclue) est à relire, et à tester avec un vrai compte sandbox [A].
* Le chemin `.unverified` (JWS invalide) **n'est pas déclenchable** sous `SKTestSession` : les transactions de test sont signées par
  la clé locale d'Xcode et vérifiées [S]. Le code refuse bien `.unverified` (lecture), mais aucun test ne l'exerce [A].
* **`AppTransaction` réelle** : sous Xcode, `originalPurchaseDate` vaut 1970 et `environment` = `Xcode`. La date **réelle** d'installation
  (qui doit dater l'essai de 15 jours) n'existe qu'avec une app signée Store : TestFlight (`Sandbox`) puis production. **Règle à
  retenir pour l'étape 15 : ne dater un essai qu'avec `environment` = `Production` ou `Sandbox` et une date postérieure à 2007 ;** sinon
  se comporter comme « essai en cours de développement ». Que `originalPurchaseDate` soit renseignée pour une **app gratuite** en
  production est **[A]**.
* Dates d'essai Apple (offre d'introduction, `Product.SubscriptionInfo.Status`) : non exercées (aucune offre dans le fichier de test).
  L'essai de 15 jours reste géré par l'application (ADR-0013 D3).

Sans configuration ni compte (processus de développement ordinaire, signé ou non), **le pont rend proprement** : `entitlements` = `[]`,
`products` = `[]`, `AppTransaction` en `STOREKIT_ERROR` (« noAccount » / « unknown »), jamais d'exception ni de blocage [V].

### (4) App Sandbox, sans `network.client` [V pour l'accès au service, A pour les achats]

`BridgeProbe.app` signé avec `entitlements/main-no-ume.plist` (`app-sandbox`, fichiers choisis, signets, micro ; **ni
`network.client` ni `allow-unsigned-executable-memory`**), lancé par `open` (le `HOME` est bien dans le conteneur) :

* chargement, ABI, `currentEntitlements`, `AppTransaction`, `Product.products`, `Transaction.updates`, arrêt : **tous répondent comme hors
  bac à sable** (mêmes codes, mêmes charges JSON) ;
* **aucun refus « Sandbox: » dans le journal** pour ces appels ;
* le même bundle signé **avec** `network.client` donne des résultats identiques.

Conclusion [V] : **le pont n'a pas besoin de `network.client`** pour joindre `storekitagent` (la requête réseau est faite par le service
système, hors du bac à sable de l'application). Cela confirme ADR-0013 §1 (« absent et inutile »). **[A]** : on n'a pas pu obtenir une
réponse *positive* (produits réels, achat) sous bac à sable sans enregistrement App Store Connect du bundle `com.kutstudio.app` ; le
cas « achat réel sans `network.client` » reste à confirmer en TestFlight (à l'étape 14).

### (5) Hors ligne

* **[V, simulé]** erreur réseau forcée sur le chargement des produits → code 3 ; `currentEntitlements` ne dépend pas du réseau et reste lisible.
* **[S]** `Transaction.currentEntitlements` lit le magasin local de transactions vérifiées (JWS) et fonctionne sans réseau ; la
  `AppTransaction` est mise en cache par le système. Le réseau n'a **pas** été coupé réellement (couper le réseau est un réglage système :
  non fait). **[A]** à confirmer en TestFlight en mode avion.
* La politique ADR-0013 (dernier état vérifié mis en cache, délai de grâce borné) reste du ressort de l'étape 15 : le pont ne cache rien.

### (6) Coût de démarrage et d'appel [V]

| Mesure | Valeur |
| --- | --- |
| `dlopen` + lecture de l'ABI | 2,2 – 3,7 ms |
| Premier `currentEntitlements` hors Xcode (storekitd, sans compte) | ≈ 360 – 700 ms |
| `currentEntitlements` suivants (×20) | médiane ≈ 400 – 510 ms, max ≈ 1,05 s |
| `Product.products` / `AppTransaction` hors Xcode | 3 – 1100 ms (selon le cache de storekitd) |
| Mêmes appels sous `SKTestSession` | ≈ 10 – 100 ms |
| Mémoire du processus Python après les appels | + ≈ 16 Mo (21 → 37 Mo, StoreKit et ses dépendances chargés) |

Conséquence : **ne jamais appeler le pont au démarrage de l'interface ni sur le thread Qt**. Interroger au lancement dans un fil de
travail, mettre le résultat en cache signé, et lire ce cache pour décider (l'application s'ouvre sans attendre Apple).

## Table de décision : Swift + `ctypes` ou PyObjC

| Critère | Swift + `ctypes` | PyObjC |
| --- | --- | --- |
| Accès à StoreKit 2 (`Transaction`, `AppTransaction`, `Product`) | **Oui** [V] | **Non [V]** : ces types sont des API Swift `async` ; les en-têtes Objective-C du SDK (`StoreKit.framework/Headers`) ne contiennent que StoreKit 1 (`SKPaymentQueue`, `SKReceiptRefreshRequest`…). Aucun pont n'est généré. |
| Vérification JWS | `VerificationResult` natif | StoreKit 1 : reçu PKCS#7 à valider soi-même [S] |
| `Transaction.updates` / `currentEntitlements` | Oui | Non (équivalent : file de paiement StoreKit 1, déprécié) |
| Dépendances | 1 fichier Swift, 0 paquet Python | `pyobjc-framework-StoreKit` (+ Cocoa), à embarquer et signer ; non installé aujourd'hui |
| Sûreté des threads | Un thread de travail bloquant, aucun rappel | Rappels Objective-C vers Python depuis des threads Apple (GIL, `@objc` de Qt…) |
| Signature / bac à sable | Une `.dylib` signée avec le reste [V] | Beaucoup de `.so` de plus à signer |
| Coût de maintenance | Un petit module Swift, un en-tête C versionné | Faible, mais sur une API qui n'est plus la bonne |
| Test sans compte | `SKTestSession` via XCTest (non trivial : hôte requis) | Impossible pour StoreKit 2 |

**Recommandation : Swift + `ctypes`.** PyObjC est écarté pour StoreKit 2 (réfuté [V]) ; il ne resterait que StoreKit 1, dont la
vérification locale d'un reçu et l'absence de `currentEntitlements` coûteraient plus cher que ce module.

## Ce qui reste à faire dans le produit (hors de ce spike)

1. Déplacer le module hors du dossier jetable (par exemple `packaging/store/storekit/`) et ajouter son étape de construction et de
   signature à `build.py` (avant la signature du `.app`, `--add-binary` PyInstaller).
2. Un `core/` pur et testable (`StoreState` : essai, abonné, achat unique, repli, délai de grâce) qui reçoit des dictionnaires et ne
   connaît pas `ctypes` ; le pont n'est qu'une source d'événements.
3. Cache signé du dernier état vérifié (ADR-0013 §étape 13/15).
4. Un test d'intégration XCTest rejoué en CI macOS (hôte vide + `run_tests.sh` ; durée de bout en bout **[S]**, non mesurée sur un runner) : possible, **non** ajouté
   ici (dépend de LaunchServices et d'une fenêtre ; à valider sur le runner).
5. Achat, restauration : `Product.purchase(confirmIn:)` et `AppStore.sync()` côté bouton ; **macOS 15.2+** pour la variante avec fenêtre.
   Le pont actuel ne propose pas d'achat : l'ajouter réclame une fenêtre cible (`NSWindow`), donc une interface C de plus (pointeur de
   fenêtre fourni par Qt via `winId()`) **à concevoir** (**décision à prendre**).

## Actions UTILISATEUR (aucune n'a été faite ici ; ne rien toucher au compte en ligne sans toi)

1. **App Store Connect** : accepter l'accord **Paid Apps** (coordonnées bancaires, fiscales), nécessaire même pour tester en TestFlight
   les achats intégrés [A].
2. **Créer l'app** `com.kutstudio.app` (l'App ID existe déjà via la coquille du spike) et ses **produits** : groupe d'abonnement,
   mensuel, annuel, non consommable. **Fixer les identifiants** (non modifiables ensuite [A]) ; remplacer ceux du fichier `.storekit`.
3. **Testeurs sandbox** (App Store Connect > Utilisateurs et accès > Sandbox) : au moins un, avec une adresse non utilisée ailleurs.
4. **Un build TestFlight** (étape 5 du plan) pour mesurer les points **[A]** : `AppTransaction.originalPurchaseDate` réelle d'une
   app gratuite, hors ligne en mode avion, achat/restauration/remboursement réels, comportement sans `network.client`.
5. Optionnel : exécuter `XCTestHost/run_tests.sh` et `build_bridge_probe.sh` + `../sign.sh` sur ta machine pour recouper ces mesures.

## Rejouer

```sh
packaging/store-spike/storekit/build.sh                      # libkutstore.dylib dans build/
packaging/store-spike/storekit/XCTestHost/run_tests.sh       # SKTestSession : 9 tests (Xcode requis, aucune connexion de compte)
PYTHON=.venv/bin/python packaging/store-spike/storekit/build_bridge_probe.sh /tmp/kutbp
KUT_MAIN_EXE=BridgeProbe packaging/store-spike/sign.sh /tmp/kutbp/dist/BridgeProbe.app packaging/store-spike/entitlements/main-no-ume.plist
open -n -W --stdout /tmp/kutbp/out.txt /tmp/kutbp/dist/BridgeProbe.app   # lancée par LaunchServices (conteneur)
```

Pièges : un **hôte** est indispensable pour `SKTestSession` (voir (3)) ; `swift test` seul échoue pour la même raison ; sous
`xcodebuild test`, les variables d'environnement des tests passent par le préfixe `TEST_RUNNER_` (fait par `run_tests.sh`).
