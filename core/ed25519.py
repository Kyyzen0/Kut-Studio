"""Ed25519 (RFC 8032) en Python pur : vérification dans l'application, signature dans l'outil de publication.

Pourquoi ce module existe : l'application gelée ne contient ni ``cryptography`` ni PyNaCl, et l'ADR-0012 refuse d'ajouter
une dépendance native à ce qui sert de racine de confiance des mises à jour (`docs/adr/0012-mise-a-jour-automatique.md`).
Le code suit l'algorithme de la section 6 et de l'annexe de la RFC 8032, avec ``hashlib.sha512`` de la bibliothèque
standard. Il est vérifié contre les vecteurs de la RFC (§ 7.1) et contre des signatures produites par OpenSSL
(``tests/test_ed25519.py``).

**Limites assumées**

* Ce n'est **pas** un code à temps constant : l'arithmétique sur les entiers de Python dépend des valeurs. Cela
  n'importe pas pour la **vérification** (clé publique, message et signature sont publics). Pour la **signature**, la
  clé secrète ne doit tourner que dans le workflow isolé de GitHub Actions et, pour ``gen-update-key``, sur la machine de
  l'utilisateur, jamais sur un serveur partagé où un autre processus pourrait mesurer les durées.
* Lent (quelques dizaines de millisecondes par opération) : une signature par release, une vérification par mise à jour.
* La vérification est **stricte** : ``S`` doit être inférieur à l'ordre du groupe (pas de malléabilité), les encodages
  de ``A`` et de ``R`` doivent être canoniques (coordonnée ``y`` < p, signe de ``x`` cohérent), et l'équation est vérifiée
  sans cofacteur par comparaison des encodages (``[S]B - [h]A`` doit s'encoder exactement en ``R``).

Les valeurs publiques d'entrée (clé, signature) sont de longueur fixe : toute autre longueur est refusée sans calcul.
"""

from __future__ import annotations

import hashlib
import hmac

PUBLIC_KEY_SIZE = 32
SEED_SIZE = 32
SIGNATURE_SIZE = 64

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, -1, _P)) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)

Point = tuple[int, int, int, int]
"""Coordonnées étendues (X, Y, Z, T) avec x = X/Z, y = Y/Z, x·y = T/Z."""


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, -1, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_BASE_Y = 4 * pow(5, -1, _P) % _P
_BASE_X = _recover_x(_BASE_Y, 0)
assert _BASE_X is not None
_BASE: Point = (_BASE_X, _BASE_Y, 1, _BASE_X * _BASE_Y % _P)
_ZERO: Point = (0, 1, 1, 0)


def _add(p: Point, q: Point) -> Point:
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _multiply(scalar: int, point: Point) -> Point:
    result = _ZERO
    while scalar > 0:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _negate(point: Point) -> Point:
    return ((-point[0]) % _P, point[1], point[2], (-point[3]) % _P)


def _encode(point: Point) -> bytes:
    z_inverse = pow(point[2], -1, _P)
    x, y = point[0] * z_inverse % _P, point[1] * z_inverse % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decode(data: bytes) -> Point | None:
    """Point d'un encodage de 32 octets ; ``None`` si non canonique ou hors de la courbe."""
    if len(data) != 32:
        return None
    value = int.from_bytes(data, "little")
    sign = value >> 255
    y = value & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _sha512_int(*parts: bytes) -> int:
    return int.from_bytes(hashlib.sha512(b"".join(parts)).digest(), "little")


def _expand(seed: bytes) -> tuple[int, bytes]:
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return scalar, digest[32:]


def public_key_from_seed(seed: bytes) -> bytes:
    """Clé publique (32 octets) de la graine secrète (32 octets)."""
    if len(seed) != SEED_SIZE:
        raise ValueError(f"graine de {SEED_SIZE} octets attendue")
    scalar, _prefix = _expand(seed)
    return _encode(_multiply(scalar, _BASE))


def sign(seed: bytes, message: bytes) -> bytes:
    """Signature Ed25519 (64 octets) de ``message`` par la graine ``seed`` (RFC 8032 § 5.1.6)."""
    if len(seed) != SEED_SIZE:
        raise ValueError(f"graine de {SEED_SIZE} octets attendue")
    scalar, prefix = _expand(seed)
    public = _encode(_multiply(scalar, _BASE))
    r = _sha512_int(prefix, message) % _L
    commitment = _encode(_multiply(r, _BASE))
    h = _sha512_int(commitment, public, message) % _L
    s = (r + h * scalar) % _L
    return commitment + int.to_bytes(s, 32, "little")


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """``True`` seulement pour une signature valide de ``message`` par ``public_key`` (jamais d'exception).

    Refuse : longueur fausse de la clé ou de la signature, ``S`` ≥ L, clé ou ``R`` hors de la courbe ou non canoniques,
    équation fausse.
    """
    if len(public_key) != PUBLIC_KEY_SIZE or len(signature) != SIGNATURE_SIZE:
        return False
    a = _decode(bytes(public_key))
    if a is None:
        return False
    commitment, s_bytes = bytes(signature[:32]), bytes(signature[32:])
    if _decode(commitment) is None:
        return False
    s = int.from_bytes(s_bytes, "little")
    if s >= _L:
        return False
    h = _sha512_int(commitment, bytes(public_key), message) % _L
    # [S]B - [h]A doit être exactement R.
    expected = _add(_multiply(s, _BASE), _multiply(h, _negate(a)))
    return _constant_time_equal(_encode(expected), commitment)


def _constant_time_equal(left: bytes, right: bytes) -> bool:
    return hmac.compare_digest(left, right)


__all__ = [
    "PUBLIC_KEY_SIZE",
    "SEED_SIZE",
    "SIGNATURE_SIZE",
    "public_key_from_seed",
    "sign",
    "verify",
]
