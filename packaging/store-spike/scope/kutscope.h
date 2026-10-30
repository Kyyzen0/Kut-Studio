/* Interface C de libkutscope : signets à portée de sécurité (ADR-0013, étape 5). Bibliothèque SÉPARÉE de libkutstore
 * (ABI 1 de kutstore inchangée) : un chargement raté de l'une n'empêche pas l'autre.
 *
 * Règles (vérifiées par core/security_scope/bridge.py et tests/test_security_scope_dylib.py) :
 *  - Aucun callback, aucun thread Swift : fonctions synchrones et brèves (pas de réseau, pas de panneau).
 *  - Aucune exception ne franchit la frontière : code + texte UTF-8.
 *  - Mémoire : *out est alloué par la bibliothèque (malloc), l'appelant le libère UNE fois avec kutscope_free.
 *  - L'entier d'ABI se lit AVANT tout autre appel ; s'il diffère, la bibliothèque est refusée.
 *  - Les accès ouverts (kutscope_open) sont désignés par un identifiant opaque ; chacun doit être fermé UNE fois.
 *    kutscope_open_count() dit combien restent ouverts (diagnostic, tests).
 */
#ifndef KUTSCOPE_H
#define KUTSCOPE_H
#include <stdint.h>

#define KUTSCOPE_ABI_VERSION 1

enum kutscope_status {
    KUTSCOPE_OK = 0,
    KUTSCOPE_BAD_ARGUMENT = 1, /* entrée vide, trop longue ou illisible */
    KUTSCOPE_DENIED = 2,       /* création impossible (chemin inaccessible) ou startAccessing refusé */
    KUTSCOPE_UNRESOLVABLE = 3, /* le signet ne désigne plus rien (fichier supprimé, volume absent) : redemander */
    KUTSCOPE_INTERNAL = 4
};

int32_t kutscope_abi_version(void);
/* Crée le signet (portée de sécurité, lecture-écriture) de path. *out = signet encodé en base64. */
int32_t kutscope_bookmark(const char *path, char **out);
/* Résout bookmark_b64, ouvre l'accès (startAccessingSecurityScopedResource) et rend un identifiant dans *handle.
 * *out = JSON {"path": "...", "stale": bool, "refreshed": "<base64>"|null} ; "refreshed" est un signet neuf quand le
 * système a jugé l'ancien périmé et qu'il a pu en recréer un. */
int32_t kutscope_open(const char *bookmark_b64, int64_t *handle, char **out);
/* Ferme l'accès (stopAccessingSecurityScopedResource). Identifiant inconnu ou déjà fermé : BAD_ARGUMENT. */
int32_t kutscope_close(int64_t handle);
int32_t kutscope_open_count(void);
void kutscope_free(char *p);
#endif
