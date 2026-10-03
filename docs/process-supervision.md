# Supervision des processus FFmpeg

Aperçu fidèle, proxies, export, file de rendu, tracking, miniatures, scopes et détection matérielle lancent FFmpeg
(ou ffprobe) en processus enfant. La fermeture normale les arrête (`MainWindow._shutdown_steps`) ; ce document
décrit ce qui les arrête quand Kut-Studio meurt **sans** passer par la fermeture (`kill -9`, plantage, « Forcer à
quitter », gestionnaire des tâches). Avant ce mécanisme, un segment d'aperçu survivait jusqu'à 120 s et un export
jusqu'à sa fin.

## Conception (résumé)

* **Un seul point de lancement** : `core/process_supervisor.py`. `supervised_run()` remplace `subprocess.run`,
  `supervised_popen()` remplace `subprocess.Popen` (gestionnaire de contexte) ; le seul `QProcess` (l'export, que la
  file de rendu réutilise) enregistre son PID dès `started` via `register_pid()`. Aucun autre lancement dans
  `core/` ni `ui/` (test garde-fou par analyse AST, `tests/test_process_launch_guard.py`).
* **Rien au démarrage** : le superviseur naît au premier lancement d'un enfant.
* **Registre par instance**, sur disque, sous `user_cache_dir()/processes/<instance>/` : `owner.json` (PID et
  jeton d'identité de l'application) et un fichier par enfant vivant (PID, jeton, nom d'exécutable), créé après le
  lancement et retiré après la fin de l'enfant.
* **Identité** (`core/process_platform.py`) : un PID seul ne prouve rien (réutilisation). Le jeton est l'heure de
  début du processus (`/proc/<pid>/stat` champ 22 + identifiant de démarrage sous Linux, `proc_pidinfo` sous macOS,
  heure de création sous Windows), complété par le nom d'exécutable. **Aucun arrêt sans identité vérifiée.**
* **Windows** : un objet Job par instance (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, via `ctypes`) ; chaque enfant y est
  affecté juste après son lancement. À la mort de l'application, le noyau ferme la poignée et tue le job.
* **macOS / Linux** : un petit processus **gardien** (Python pur, l'application elle-même lancée avec
  `--kut-process-reaper`, traité dans `main.py` avant Qt, donc valable en source comme dans l'application gelée)
  bloque sur la lecture d'un tube dont seul le parent tient le bout d'écriture. EOF = le parent est mort, par
  n'importe quelle voie. Le gardien relit alors le registre de l'instance, tue les enfants dont l'identité est
  vérifiée, puis tue **son propre groupe de processus**, que les enfants `Popen` rejoignent avant `exec`
  (`process_group=`) : pas de fenêtre de course pour eux.
* **Balayage au démarrage** (`main.py`) : les registres d'instances mortes (propriétaire disparu ou jeton différent)
  sont relus ; leurs enfants encore vivants et vérifiés sont tués, le registre est supprimé. Une instance vivante
  n'est jamais touchée.
* **Fermeture normale** : étape « processus enfants » de `_shutdown_steps()` : tout ce qui reste enregistré est tué
  (identité vérifiée) puis attendu.
* Toute défaillance de la supervision va au journal de diagnostic ; elle n'empêche jamais de lancer FFmpeg.

## Points de lancement

| Usage | Module | API |
| --- | --- | --- |
| Aperçu fidèle (segments) | `core/preview_engine.py` (`_run_cancellable`) | `supervised_popen` |
| Proxies | `core/proxy_manager.py` (`run_ffmpeg`) | `ProcessSupervisor.popen` / `finish` |
| Images d'analyse du tracking | `core/tracking_frames.py` (`FrameReader`) | `ProcessSupervisor.popen` / `finish` |
| Mesures de décodage | `core/decode_policy.py` (`run_measured`) | `ProcessSupervisor.popen` / `finish` |
| Sonde de flux (décodage) | `core/decode_policy.py` (`_default_probe`) | `supervised_run` |
| Détection matérielle | `core/hardware_encoding.py` (`default_runner`) | `supervised_run` |
| Import (ffprobe) | `core/media_probe.py` | `supervised_run` |
| Miniatures, ondes | `core/media_previews.py` | `supervised_run` |
| Scopes | `core/scopes_analyzer.py` (`extract_frame_png`) | `supervised_run` |
| Version et filtres FFmpeg | `core/export_engine.py` | `supervised_run` |
| Export, file de rendu | `core/export_engine.py` (`QProcess`) | `register_pid` à `started`, `release` à `finished` |

`supervised_run` a le contrat de `subprocess.run` (mêmes arguments, `OSError`, `TimeoutExpired` après avoir tué
l'enfant, `CalledProcessError` avec `check=True`). Par défaut `stdin` est fermé (`DEVNULL` : FFmpeg ne lit jamais le
terminal, ce qui compte aussi pour un enfant placé dans un autre groupe de processus) et aucune console ne s'ouvre
sous Windows (`CREATE_NO_WINDOW`, qui manquait aux sondes, miniatures et scopes).

## Stratégie par plateforme

### Windows : objet Job « tuer à la fermeture »

`KillOnCloseJob` (`core/process_platform.py`) crée, au premier enfant, un objet Job avec
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (`CreateJobObjectW` + `SetInformationJobObject`, via `ctypes`, aucune
dépendance). Chaque enfant y est placé juste après son lancement (`OpenProcess` + `AssignProcessToJobObject`) ; le
PID est stable à ce moment, puisque `Popen` ou `QProcess` garde une poignée sur le processus. La poignée du job
n'est **pas héritable** (attributs de sécurité nuls) : un enfant qui l'hériterait garderait le job ouvert après la
mort de l'application. Quand Kut-Studio meurt, par n'importe quelle voie, le noyau ferme ses poignées ; c'était la
dernière, il tue tous les processus du job.

Cas dégradés, tous journalisés une fois et jamais bloquants (l'enfant tourne alors sans le job ; le registre et le
balayage au démarrage prennent le relais) :

* création du job ou pose de la limite refusée : job désactivé pour la session ;
* `AssignProcessToJobObject` refusé (`ERROR_ACCESS_DENIED`), typiquement une application déjà placée dans un job qui
  n'autorise pas l'imbrication (Windows 7) ;
* un processus déjà terminé (sonde éclair) n'est **pas** un échec : il n'y a rien à protéger, rien n'est journalisé.

Le PID reste par ailleurs enregistré dans le registre de l'instance : pour le balayage au démarrage et pour la
fermeture normale.

### macOS et Linux : un processus gardien

Au premier enfant, le superviseur lance un **gardien** : l'application elle-même avec
`--kut-process-reaper <registre>` (en source : `python main.py --kut-process-reaper …` ; gelée par PyInstaller,
`Kut-Studio --kut-process-reaper …`, puisque `sys.executable -m …` n'existe pas). `main.py` traite cet argument en
tout premier, avant le journal et avant Qt : le gardien n'importe que la bibliothèque standard et quelques modules
de `core`.

* Son entrée standard est un tube dont **seul le parent** tient le bout d'écriture (descripteur non héritable,
  `close_fds`). Le gardien bloque sur `read()` ; l'EOF arrive quand plus personne ne tient ce bout : le parent est
  mort (le noyau ferme ses descripteurs, quelle que soit la cause) ou l'a fermé (fermeture normale).
* Il ignore `SIGINT`, `SIGHUP`, `SIGTERM`, `SIGQUIT` : un Ctrl-C ou un terminal fermé ne le tue pas avant son parent.
* Il est lancé **chef d'un groupe de processus neuf** (`process_group=0`), dans la même session. Les enfants
  `Popen` rejoignent ce groupe **avant `exec`** (`process_group=<pid du gardien>`, fait par `_posixsubprocess`,
  sans `preexec_fn`).
* À l'EOF : il relit le registre de son instance, tue chaque enfant **dont l'identité est vérifiée**, supprime le
  registre, écrit un bilan dans le journal de diagnostic (seulement s'il a tué ou épargné quelque chose), puis, en
  dernier geste, envoie `SIGKILL` à son propre groupe (lui compris) — seulement s'il en est bien le chef
  (`getpgrp() == getpid()`), sinon ce groupe pourrait être celui du terminal.

Les deux couches se recouvrent : le groupe attrape les enfants `Popen`, y compris ceux lancés à l'instant de la mort
(aucune fenêtre de course) ; le registre vérifié attrape le `QProcess` de l'export (un `QProcess` ne peut pas
rejoindre un groupe avant `exec`) et les enfants d'un groupe précédent si le gardien a dû être relancé. Les tests
vérifient chaque couche seule (voir plus bas).

Le gardien est surveillé à chaque lancement (`poll()`) : s'il est mort, il est relancé (journalisé), au plus trois
échecs de lancement par session. Un groupe refusé (gardien mort entre-temps) relance l'enfant hors groupe ; il reste
protégé par le registre.

### Options écartées

| Option | Pourquoi elle n'a pas été retenue |
| --- | --- |
| `prctl(PR_SET_PDEATHSIG)` | Linux seulement. Le signal part quand le **thread** qui a lancé l'enfant se termine, pas le processus : Kut-Studio lance FFmpeg depuis des threads de travail (file d'aperçu, proxies, tracking, détection), un enfant serait tué à la fin d'un thread qui l'a seulement démarré. Il faut en outre l'appeler dans l'enfant entre `fork` et `exec` : `preexec_fn` n'est pas sûr dans un processus multithread (verrous tenus par d'autres threads au moment du `fork`), et le `QProcess` ne l'offre qu'au travers d'un rappel Python dans l'enfant, avec le même défaut. |
| `kqueue` / `EVFILT_PROC` + `NOTE_EXIT` (macOS), `pidfd` (Linux) | Il faut un observateur **hors** de l'application (un observateur interne meurt avec elle) : c'est le gardien. L'EOF d'un tube donne la même garantie noyau, avec un seul code pour macOS et Linux, sans connaître ni surveiller le PID du parent (pas de course sur sa réutilisation). |
| Groupes / sessions seuls (`start_new_session=True`, `killpg` à la fermeture) | Utiles à la fermeture normale, mais après un `kill -9` personne n'appelle `killpg`. Retenu seulement comme couche du gardien : le groupe appartient au gardien, qui le tue lui-même. |
| Mettre l'application elle-même dans un Job (Windows) | Plus de fenêtre de course (les enfants héritent du job), mais **tout** ce que l'application lance mourrait avec elle, par exemple un lecteur ouvert par `QDesktopServices` sur un export. |
| Lancer chaque FFmpeg suspendu, l'affecter au job puis le reprendre (Windows) | Ferme la fenêtre de course, mais `subprocess.Popen` jette la poignée du thread principal et n'expose pas `PROC_THREAD_ATTRIBUTE_JOB_LIST` : il faudrait réécrire `CreateProcess` en `ctypes` ou passer par `NtResumeProcess` (non documenté). Disproportionné pour une fenêtre de quelques microsecondes. |
| Un lanceur intermédiaire par enfant | Un démarrage de Python (~30 à 50 ms) par miniature ou sonde : coût inacceptable. |
| `psutil` | Dépendance native nouvelle, pour quelques appels système faciles à faire avec `ctypes`. |

## Identité et propriété

Un PID est réutilisé dès que le processus précédent est récolté. Avant **tout** arrêt (gardien, balayage,
fermeture normale), `kill_if_same` relit l'identité actuelle du PID et la compare à celle notée au lancement :

| Système | Jeton (heure de début) | Nom | Arrêt |
| --- | --- | --- | --- |
| Linux | `/proc/<pid>/stat` champ 22 (tics depuis le démarrage), préfixé par `/proc/sys/kernel/random/boot_id` | champ 2 de `stat` | `pidfd_open` **avant** la vérification, puis `pidfd_send_signal(SIGKILL)` : aucune course avec la réutilisation (repli sur `kill()` si `pidfd` est indisponible) |
| macOS | `proc_pidinfo(PROC_PIDTBSDINFO)` : secondes + microsecondes (un appel, ~5 µs) | `pbi_name` / `pbi_comm` | `kill(SIGKILL)` juste après la vérification (fenêtre de quelques microsecondes ; macOS attribue les PID séquentiellement) |
| Windows | `GetProcessTimes` : heure de création (100 ns) | `QueryFullProcessImageNameW` | vérification et `TerminateProcess` sur **la même poignée** : le PID ne peut pas être réutilisé tant qu'elle est ouverte |

* Le préfixe de démarrage (Linux) rend les registres d'un démarrage précédent inoffensifs : leurs jetons ne peuvent
  plus correspondre.
* Le nom est un garde-fou supplémentaire, comparé **normalisé** (casse, dossier, `.exe`, suffixe de version, shells
  confondus) : un lanceur peut se ré-exécuter sous un autre nom en gardant PID et heure de début (le Python
  « framework » de macOS passe de `python3.14` à `Python`, le `/bin/sh` de macOS devient `bash`). Le jeton suffit
  en pratique à distinguer deux processus ; le nom ne fait que refuser davantage.
* Un **zombie** (terminé, pas encore récolté) est « disparu » : il répond encore à `kill(pid, 0)`, la sonde non
  (état `Z` sous Linux, `proc_pidinfo` refusé sous macOS ; objet signalé sous Windows).
* Une identité illisible (droits, API absente) n'est **jamais** tuée. Une plateforme inconnue (ni Linux, ni macOS,
  ni Windows) sait dire « disparu », jamais « c'est bien lui » : rien n'y est tué par vérification.
* **Autres instances** : chaque instance a son registre et son gardien ; le gardien ne relit que le registre de son
  propre parent et ne tue que son propre groupe. Le balayage au démarrage ne touche un registre que si son
  propriétaire est **prouvé** mort : PID disparu, ou PID vivant avec un autre jeton (réutilisé). Propriétaire
  illisible ou de même jeton mais d'un autre nom : on ne touche à rien.

## Registre et balayage au démarrage

```
<user_cache_dir()>/processes/
  <pid>-<aléa>/                 une instance (jamais le dossier temporaire : il doit survivre à l'application)
    owner.json                  {"format": 1, "instance": …, "owner": {"pid", "token", "name"}, "created": …}
    <pid>-<jeton>.child         {"pid", "token", "name"} : un enfant vivant
```

* `owner.json` est écrit à part puis renommé : un balayage ne lit jamais un propriétaire à moitié écrit.
* Un fichier par enfant plutôt qu'un journal : la liste des vivants est la liste du dossier, sans compactage.
* `sweep_dead_instances()` (appelé par `main.py`, après l'installation du journal) : pour chaque instance dont le
  propriétaire est prouvé mort, tue les enfants vérifiés et supprime le registre. Un enfant vérifié qui n'a pas pu
  être tué (droits) garde le registre pour le démarrage suivant. Un dossier sans `owner.json` lisible et sans enfant
  (création interrompue) est supprimé après 24 h. Le bilan va au journal de diagnostic s'il y a eu quelque chose à
  faire ; le balayage ne lève jamais.
* Cas couverts : gardien tué en même temps que l'application (par exemple tout un arbre de processus tué, ou le
  « OOM killer ») ; objet Job refusé (Windows) ; registre du gardien illisible.

## Fermeture normale

* `MainWindow._shutdown_steps()` se termine par l'étape « processus enfants » (`shutdown_children`) : après tous les
  arrêts ciblés (file de rendu, proxies, aperçu, tracking, runtime), tout enfant encore enregistré est tué (identité
  vérifiée) puis attendu (3 s) ; un survivant est journalisé. Comme les autres étapes, un échec est journalisé et
  n'interrompt pas la fermeture.
* À la sortie de l'interpréteur (`atexit`), le superviseur ferme le tube du gardien (qui balaie un registre vide et
  sort), ferme le job et supprime le registre.

## Coût (mesuré sur Apple M4, macOS, Python 3.14)

| Mesure | Valeur |
| --- | --- |
| Démarrage de l'application, aucun FFmpeg lancé | aucun processus, aucun fichier ; balayage d'un dossier absent : ~20 µs, vide : ~50 µs |
| Import de `core.process_supervisor` + `core.process_platform` | ~8 ms propres (`ctypes`, `subprocess`, `json` sont déjà importés par l'application) |
| Premier enfant (registre + lancement du gardien, sans l'attendre) | ~5 ms |
| Surcoût par lancement (identité + fichier de registre + retrait) | médiane ~95 µs, 95e centile ~330 µs |
| `ffmpeg -version` par `subprocess.run` / par `supervised_run` | 26,98 ms / 27,07 ms (médianes sur 150 lancements) |

## Limites connues

* **Fenêtre de course** entre le lancement et l'enregistrement, pour les enfants qui ne rejoignent pas le groupe du
  gardien : sous Windows (de `CreateProcess` à `AssignProcessToJobObject`, quelques microsecondes) et pour le
  `QProcess` de l'export sous POSIX (de `fork` au traitement de `started` par la boucle d'événements, quelques
  millisecondes). Un arrêt brutal pile dans cette fenêtre laisse l'enfant non enregistré : ni tué, ni balayé ; il
  finit seul (un export, jusqu'à sa fin). Les enfants `Popen` POSIX n'ont pas cette fenêtre (groupe rejoint avant
  `exec`), sauf pendant la relance d'un gardien mort.
* **Gardien et application tués ensemble** : les enfants tournent jusqu'au prochain démarrage (balayage) ou jusqu'à
  leur fin.
* Un `fork()` sans `exec` dans l'application garderait le bout d'écriture du tube : l'EOF n'arriverait qu'à la fin
  de ce processus. Kut-Studio n'en fait pas (ni `multiprocessing`, ni `os.fork`).
* **Jeton** : sous Linux, deux processus ne peuvent partager PID et tic de début (10 ms) que si le PID est réutilisé
  dans le même tic, ce que l'allocation séquentielle des PID exclut en pratique. Sous macOS, la vérification et le
  `kill()` sont deux appels (quelques microsecondes d'écart).
* Le nom normalisé est une heuristique ; il ne sert qu'en complément du jeton. Un FFmpeg lancé par un enveloppeur qui
  se ré-exécute sous un autre nom (`snap`, `flatpak`) est **épargné** par la vérification (sens sûr) : le groupe du
  gardien (enfants `Popen`) ou le job (Windows) le tuent quand même, mais pas la relecture du registre (le `QProcess`
  de l'export, le balayage au démarrage, l'étape de fermeture).
* **Petits-enfants** : un processus lancé par un enfant enregistré hérite du groupe du gardien (POSIX) ou du job
  (Windows) et meurt avec eux ; le balayage au démarrage, lui, ne connaît que les enfants enregistrés. FFmpeg et
  ffprobe ne lancent pas de processus.
* Un FFmpeg tué laisse ses fichiers partiels : le `.partial` de la file de rendu est supprimé au démarrage suivant,
  ceux des proxies par leur nettoyage des fichiers abandonnés ; un segment d'aperçu interrompu laisse un
  `kut-preview-*.mp4` dans le dossier temporaire du système.

## Tests

`tests/test_process_supervisor.py` et `tests/test_process_launch_guard.py`.

* **Simulés** (fausse plateforme `FakeOps` appliquant la vraie règle `kill_refusal`, faux `kernel32`) : gardien dont
  le tube se ferme (tue les enfants vérifiés, épargne un PID réutilisé, tue son groupe, supprime le registre) ;
  instance vivante intacte ; instance morte balayée ; PID du propriétaire réutilisé = mort ; identités illisibles =
  rien n'est tué ; objet Job refusé, affectation refusée dans un job étranger, registre impossible à écrire : repli
  journalisé, FFmpeg lancé quand même ; lecture de `/proc/<pid>/stat` (nom avec parenthèses, zombie) sur toutes les
  plateformes ; structure `JOBOBJECT_EXTENDED_LIMIT_INFORMATION` (144 octets en 64 bits).
* **Réels** : un parent jetable (`tests/fixtures/supervised_parent.py`) supervise de vrais enfants puis est tué par
  `SIGKILL` / `TerminateProcess` ; on attend, sur condition et avec une échéance de 30 s, que ses enfants aient
  disparu (identité vérifiée : un zombie ne compte pas). Couverts : trois enfants simultanés + un `run()` encore
  actif ; un vrai FFmpeg (`-f lavfi -i testsrc`, sauté avec raison si FFmpeg est absent) ; le `QProcess` de
  l'export ; le rendu annulable de l'aperçu fidèle ; deux instances (on tue l'une, les enfants de l'autre survivent
  et le balayage ne les touche pas) ; balayage au démarrage d'orphelins laissés sans gardien ni job (contrôle négatif
  : ils survivent bien à leur parent) ; fermeture normale ; fermeture de la fenêtre ; auto-contrôle du smoke test ;
  relancement de l'application gelée.
* **Garde-fou** : analyse AST de `core/` et `ui/` ; seul `core/process_supervisor.py` lance des processus, seule
  exception le `QProcess` de `core/export_engine.py`, dont l'enregistrement à `started` est vérifié.
* Vérifié en retirant chaque couche : sans gardien, les tests d'arrêt brutal et l'auto-contrôle échouent ; sans le
  `killpg` du gardien, le registre vérifié suffit ; sans la relecture du registre, le `QProcess` et l'enfant de
  l'auto-contrôle survivent.

L'auto-contrôle (`supervision_self_check`, lancé par `python main.py --smoke-test` et par l'application construite)
déclenche réellement la protection : un enfant (l'application en mode `--kut-process-sleeper`, hors du groupe du
gardien) est enregistré comme un `QProcess`, puis le tube du gardien est fermé (POSIX) ou la poignée du job fermée
(Windows) ; l'enfant doit mourir.

Vérifié aussi à la main sur l'application gelée (PyInstaller, macOS) : `kill -9` pendant qu'un « FFmpeg » factice
long tournait (`KUT_STUDIO_FFMPEG` pointant vers un script qui dort) ; le gardien gelé l'a arrêté par le registre,
son petit-enfant par le groupe, et a supprimé le registre. Puis `kill -9` de l'application **et** du gardien :
l'enfant survit, et le démarrage suivant le balaie (« balayage au démarrage — 1 enfant(s) orphelin(s) arrêté(s) »).

## Tester à la main

macOS / Linux, dans un terminal :

```sh
python main.py &                       # ou l'application construite
APP=$!
# Ouvrir un projet, lancer un export long (ou un aperçu fidèle, ou des proxies), puis :
pgrep -fl ffmpeg                       # les FFmpeg de l'instance
pgrep -fl -- --kut-process-reaper      # son gardien
kill -9 $APP
sleep 1; pgrep -fl ffmpeg              # plus aucun FFmpeg de cette instance
ls "$HOME/Library/Caches/Kut-Studio/processes"   # macOS ; Linux : ~/.cache/kut-studio/processes — vide
grep Supervision "$HOME/Library/Logs/Kut-Studio/kut-studio.log"   # bilan du gardien
```

Pour vérifier le balayage au démarrage, tuer **aussi** le gardien (`kill -9 $APP <pid du gardien>`) : les FFmpeg
survivent ; relancer Kut-Studio, ils disparaissent et le journal contient « balayage au démarrage ».

Windows : lancer un export, puis dans le gestionnaire des tâches « Fin de tâche » sur `Kut-Studio.exe` (ou
`taskkill /F /PID <pid>`) : les `ffmpeg.exe` disparaissent avec lui (objet Job). `Get-Process ffmpeg` le confirme.

Plusieurs instances : en lancer deux, un export dans chacune, tuer l'une : les FFmpeg de l'autre continuent.
