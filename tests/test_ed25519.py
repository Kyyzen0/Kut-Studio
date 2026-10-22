"""Ed25519 en Python pur (``core.ed25519``) : vecteurs de la RFC 8032, signatures d'OpenSSL, refus des entrées invalides.

* TEST 1 à 3 : vecteurs de la RFC 8032 § 7.1 ;
* les autres vecteurs ont été produits par OpenSSL 3 (Node ``crypto.sign``) et recopiés ici : la vérification ne dépend donc
  d'aucun outil à l'exécution. Si ``cryptography`` est installée, un test de non-régression compare aussi au vol.
"""

from __future__ import annotations

import hashlib
import random

import pytest

from core import ed25519

_LONG = bytes((i * 7 + 3) & 255 for i in range(1023))

# (nom, graine, clé publique, message, signature)
VECTORS = [
    ("RFC 8032 TEST 1 (message vide)",
     "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", b"",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("RFC 8032 TEST 2 (1 octet)",
     "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", bytes.fromhex("72"),
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("RFC 8032 TEST 3 (2 octets)",
     "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", bytes.fromhex("af82"),
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
    ("OpenSSL : SHA-512(\"abc\") comme message",
     "f5e5767cf153319517630f226876b86c8160cc583bc013744c6bf255f5cc0ee5",
     "278117fc144c72340f67d0f2316e8386ceffbf2b2428c9c51fef7c597f1d426e", hashlib.sha512(b"abc").digest(),
     "f43f7b7aa9758bce7bdcc41d3774acb1950b27fff41aab020cd8be565f6428b8675a79a6b50708dbe80c10686dc84377affbe4aef5a66c750fa2237cc085b20f"),
    ("OpenSSL : message de 1023 octets",
     "00" * 31 + "01",
     "4cb5abf6ad79fbf5abbccafcc269d85cd2651ed4b885b5869f241aedf0a5ba29", _LONG,
     "d8db1545b21405eceff22b1cdb40c73aea936994cf83283f64856ca81bd879d30761a2160db7e479e90281ea403340a95e16c853e217ec3368b994f825a9f708"),
    ("OpenSSL : graine 0xFF…FF",
     "ff" * 32,
     "76a1592044a6e4f511265bca73a604d90b0529d1df602be30a19a9257660d1f5", b"Kut-Studio",
     "1429900308e5ae7930a189cc2af2892aa8a5eec1e4853e828994d3dcdd5a4dad5fc224392bfe5d2cd3df60336b3ecac05f692f60a69efb48c15795634aa09b01"),
]
IDS = [vector[0] for vector in VECTORS]


@pytest.mark.parametrize("name,seed,public,message,signature", VECTORS, ids=IDS)
def test_known_vectors_derive_sign_and_verify(name, seed, public, message, signature):
    assert ed25519.public_key_from_seed(bytes.fromhex(seed)).hex() == public
    assert ed25519.sign(bytes.fromhex(seed), message).hex() == signature       # Ed25519 est déterministe
    assert ed25519.verify(bytes.fromhex(public), message, bytes.fromhex(signature))


def _first():
    _name, _seed, public, message, signature = VECTORS[1]
    return bytes.fromhex(public), message, bytes.fromhex(signature)


@pytest.mark.parametrize("bit", [0, 7, 100, 255, 256, 300, 511])
def test_a_signature_changed_by_one_bit_is_refused(bit):
    public, message, signature = _first()
    altered = bytearray(signature)
    altered[bit // 8] ^= 1 << (bit % 8)
    assert not ed25519.verify(public, message, bytes(altered))


def test_a_modified_key_or_message_is_refused():
    public, message, signature = _first()
    other = bytearray(public)
    other[5] ^= 0x10
    assert not ed25519.verify(bytes(other), message, signature)
    assert not ed25519.verify(public, message + b"\x00", signature)
    assert not ed25519.verify(public, b"", signature)
    wrong_key = bytes.fromhex(VECTORS[0][2])
    assert not ed25519.verify(wrong_key, message, signature)


def test_s_not_below_the_group_order_is_refused_even_when_the_equation_holds():
    """Malléabilité : (R, S + L) vérifie la même équation mais n'est pas une signature canonique."""
    public, message, signature = _first()
    order = ed25519._L
    s = int.from_bytes(signature[32:], "little")
    assert s < order
    malleated = signature[:32] + (s + order).to_bytes(32, "little")
    assert not ed25519.verify(public, message, malleated)
    assert not ed25519.verify(public, message, signature[:32] + (2**256 - 1).to_bytes(32, "little"))


def test_points_outside_the_curve_are_refused():
    public, message, signature = _first()
    # y = 2 : x² = 3/(4d+1) n'a pas de racine dans ce corps -> hors de la courbe.
    off_curve = (2).to_bytes(32, "little")
    assert ed25519._decode(off_curve) is None
    assert not ed25519.verify(off_curve, message, signature)
    assert not ed25519.verify(public, message, off_curve + signature[32:])
    # y >= p : encodage non canonique.
    assert ed25519._decode((ed25519._P).to_bytes(32, "little")) is None
    assert ed25519._decode((2**255 - 1).to_bytes(32, "little")) is None
    # x = 0 avec bit de signe 1 : non canonique.
    assert ed25519._decode((1 | 1 << 255).to_bytes(32, "little")) is None


@pytest.mark.parametrize("length", [0, 1, 32, 63, 65, 128])
def test_wrong_signature_lengths_are_refused(length):
    public, message, signature = _first()
    assert not ed25519.verify(public, message, (signature * 3)[:length])


@pytest.mark.parametrize("length", [0, 31, 33, 64])
def test_wrong_key_lengths_are_refused_without_exception(length):
    _public, message, signature = _first()
    assert not ed25519.verify(b"\x01" * length, message, signature)


def test_seeds_of_the_wrong_size_are_refused():
    with pytest.raises(ValueError):
        ed25519.sign(b"\x00" * 31, b"x")
    with pytest.raises(ValueError):
        ed25519.public_key_from_seed(b"\x00" * 33)


def test_random_round_trips_and_cross_key_failures():
    rng = random.Random(8032)
    for _ in range(5):
        seed = bytes(rng.getrandbits(8) for _ in range(32))
        message = bytes(rng.getrandbits(8) for _ in range(rng.randrange(0, 300)))
        public = ed25519.public_key_from_seed(seed)
        signature = ed25519.sign(seed, message)
        assert ed25519.verify(public, message, signature)
        assert not ed25519.verify(public, message + b"!", signature)


def test_agrees_with_the_cryptography_library_when_it_is_installed():
    primitives = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519",
                                     reason="la bibliothèque « cryptography » n'est pas installée (pas une dépendance)")
    rng = random.Random(7)
    for _ in range(5):
        seed = bytes(rng.getrandbits(8) for _ in range(32))
        message = bytes(rng.getrandbits(8) for _ in range(rng.randrange(0, 200)))
        reference = primitives.Ed25519PrivateKey.from_private_bytes(seed)
        assert ed25519.sign(seed, message) == reference.sign(message)


def _low_order_points():
    """Identité, ordre 2, ordre 4 (deux), et un point d'ordre 8 : tous de torsion."""
    p = ed25519._P
    encodings = [
        (1).to_bytes(32, "little"),                       # identité (0, 1)
        (p - 1).to_bytes(32, "little"),                   # (0, -1), ordre 2
        (0).to_bytes(32, "little"),                       # y = 0 : ordre 4
        ((0) | (1 << 255)).to_bytes(32, "little"),        # y = 0, autre signe : ordre 4
    ]
    return [e for e in encodings if ed25519._decode(e) is not None]


def test_low_order_public_keys_cannot_forge_signatures():
    """Clé identité + R = B, S = 1 : l'équation [S]B = R + [h]A tient pour tout h, sans clé privée."""
    base = ed25519._encode(ed25519._BASE)
    forged = base + (1).to_bytes(32, "little")
    identity = (1).to_bytes(32, "little")
    # Sans la garde, cette « signature » serait valide pour n'importe quel message : on le démontre.
    assert ed25519._decode(identity) is not None
    assert not ed25519.verify(identity, b"n'importe quel message", forged)
    points = _low_order_points()
    assert len(points) >= 3
    for encoded in points:
        assert not ed25519.is_valid_public_key(encoded)
        assert not ed25519.verify(encoded, b"message", forged)
        assert not ed25519.verify(encoded, b"message", encoded + (0).to_bytes(32, "little"))


def test_a_key_with_a_torsion_component_is_refused():
    a = ed25519._decode(bytes.fromhex(VECTORS[1][2]))
    torsion = ed25519._decode((ed25519._P - 1).to_bytes(32, "little"))
    mixed = ed25519._encode(ed25519._add(a, torsion))
    assert not ed25519.is_valid_public_key(mixed)
    assert ed25519.is_valid_public_key(bytes.fromhex(VECTORS[1][2]))


def test_the_public_key_type_refuses_weak_keys():
    from core.update_keys import PublicKey

    with pytest.raises(ValueError):
        PublicKey((1).to_bytes(32, "little"))
