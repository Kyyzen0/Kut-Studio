# Supervision des processus FFmpeg

Aperçu fidèle, proxies, export, file de rendu, tracking, miniatures et détection matérielle lancent FFmpeg (ou
ffprobe) en processus enfant. La fermeture normale les arrête (`MainWindow._shutdown_steps`) ; ce document décrit
ce qui les arrête quand Kut-Studio meurt **sans** passer par la fermeture (`kill -9`, plantage, coupure du
gestionnaire de tâches).

## Conception (résumé)

* **Un seul point de lancement** : `core/process_supervisor.py`. `supervised_run()` remplace `subprocess.run`,
  `supervised_popen()` remplace `subprocess.Popen` (gestionnaire de contexte) ; le seul `QProcess` (l'export, que la
  file de rendu réutilise) enregistre son PID dès `started` via `register_pid()`. Aucun autre lancement dans
  `core/` ni `ui/` (test garde-fou par analyse AST).
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
