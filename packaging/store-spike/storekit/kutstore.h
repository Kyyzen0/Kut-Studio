/* Interface C du pont StoreKit 2 (spike ADR-0013 étape 13). JETABLE : rien de core/ ni ui/ ne l'inclut.
 *
 * Règles (toutes vérifiées par storekit_contract.py et tests/test_store_spike_storekit.py) :
 *  - Aucun callback, aucun thread Swift qui appelle Python : l'appelant (un thread de travail Python, JAMAIS le thread
 *    Qt principal) appelle une fonction BLOQUANTE ; ctypes relâche le GIL pendant l'appel.
 *  - Aucune exception, aucune erreur Swift ne franchit la frontière : tout est renvoyé en code + JSON UTF-8.
 *  - Mémoire : *out est alloué par le pont (malloc) ; l'appelant le libère UNE fois avec kutstore_free. Jamais le contraire.
 *  - Une réponse ne contient jamais une transaction non vérifiée (JWS) : elle est comptée dans "rejected", pas rendue.
 *  - L'entier d'ABI se lit au chargement (kutstore_abi_version) AVANT tout autre appel ; s'il diffère, on ne l'utilise pas.
 */
#ifndef KUTSTORE_H
#define KUTSTORE_H
#include <stdint.h>

#define KUTSTORE_ABI_VERSION 1

enum kutstore_op {
    KUTSTORE_OP_ENTITLEMENTS    = 1, /* arg: ignoré. Transaction.currentEntitlements */
    KUTSTORE_OP_APP_TRANSACTION = 2, /* arg: ignoré. AppTransaction.shared (originalPurchaseDate) */
    KUTSTORE_OP_SYNC            = 3, /* arg: ignoré. AppStore.sync() (restauration) */
    KUTSTORE_OP_PRODUCTS        = 4, /* arg: identifiants séparés par des virgules (<= 32). Product.products(for:) */
    KUTSTORE_OP_START_UPDATES   = 5  /* arg: ignoré. Démarre l'écoute de Transaction.updates (idempotent) */
};

enum kutstore_status {
    KUTSTORE_OK = 0,
    KUTSTORE_TIMEOUT = 1,     /* la tâche continue, son résultat est jeté */
    KUTSTORE_BAD_ARGUMENT = 2,
    KUTSTORE_STOREKIT_ERROR = 3,
    KUTSTORE_UNVERIFIED = 4,  /* AppTransaction non vérifiée : refusée */
    KUTSTORE_SHUTDOWN = 5,
    KUTSTORE_INTERNAL = 6,
    KUTSTORE_POLL_TIMEOUT = 7 /* kutstore_poll_update : aucun événement dans le délai */
};

int32_t kutstore_abi_version(void);
/* Bloquant. timeout_ms borné à [1, 120000]. *out toujours renseigné (JSON), à libérer avec kutstore_free. */
int32_t kutstore_call(int32_t op, const char *arg, int32_t timeout_ms, char **out);
/* Bloquant jusqu'à un événement de Transaction.updates (déjà vérifié et terminé par finish()) ou au délai. */
int32_t kutstore_poll_update(int32_t timeout_ms, char **out);
void kutstore_free(char *p);
/* Annule les tâches, réveille les kutstore_poll_update en attente (ils rendent KUTSTORE_SHUTDOWN). Idempotent. */
void kutstore_shutdown(void);
#endif
