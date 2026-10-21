"""Signature des mises à jour (ADR-0012) : format du fichier, vérification, outil de publication, notes.

Les clés sont FACTICES (``tests/fake_update_keys.py``) ; la vraie clé du mainteneur n'existe nulle part dans le dépôt.
"""

from __future__ import annotations

import base64
import os
import stat
import sys
from pathlib import Path

import pytest
from fake_update_keys import FAKE_KEY, FAKE_KEYS, FAKE_SEED, OTHER_KEY, OTHER_SEED, sign_with_fake_key

from core import ed25519, update_keys
from core.release_assets import CHECKSUMS_FILE, RELEASE_TARGETS, SIGNATURE_FILE, asset_name
from core.update_keys import PublicKey
from core.update_signature import (
    MAX_SIGNATURE_BYTES,
    SignatureFormatError,
    SignatureStatus,
    format_signature_file,
    parse_signature_file,
    self_check,
    sign_checksums,
    verify_release_signature,
)
from tools.release import __main__ as cli
from tools.release import packaging, signing
from tools.release.packaging import ReleaseError, release_notes, verify_release_directory, write_checksums

SUMS = b"0" * 64 + b"  Kut-Studio-1.2.3-macos-arm64.zip\n"


# --- Format -----------------------------------------------------------------------------------------------------------


def test_the_signature_file_is_one_ascii_line_with_the_key_id():
    content = sign_with_fake_key(SUMS)
    assert content.endswith(b"\n") and content.count(b"\n") == 1
    algorithm, key_id, encoded = content.decode("ascii").split()
    assert algorithm == "ed25519" and key_id == FAKE_KEY.key_id and len(base64.b64decode(encoded)) == 64
    assert parse_signature_file(content) == (FAKE_KEY.key_id, base64.b64decode(encoded))
    assert parse_signature_file(content.rstrip(b"\n") + b"\r\n")[0] == FAKE_KEY.key_id


@pytest.mark.parametrize("content", [
    b"", b"\n", b"ed25519", b"rsa " + b"a" * 16 + b" " + b"A" * 86 + b"==",
    "ed25519 0123456789abcdef é".encode(), b"ed25519 0123456789abcdef " + b"A" * 86 + b"=",
    b"ed25519 0123456789abcdeg " + b"A" * 86 + b"==", b"ed25519 0123456789abcdef " + b"A" * 90,
    b"ed25519 0123456789abcdef " + b"A" * 86 + b"==\nsecond line\n", b" ed25519 0123456789abcdef " + b"A" * 86 + b"==",
    b"A" * (MAX_SIGNATURE_BYTES + 1),
], ids=lambda value: f"{len(value)}-octets-{value[:12]!r}")
def test_unreadable_signature_files_are_refused(content):
    with pytest.raises(SignatureFormatError):
        parse_signature_file(content)
    result = verify_release_signature(SUMS, content, FAKE_KEYS)
    assert result.status is SignatureStatus.INVALID and not result.unknown_key
    assert len(result.detail) < 200, "le message ne recopie pas l'entrée"


def test_a_signature_of_the_wrong_length_cannot_be_formatted():
    with pytest.raises(SignatureFormatError):
        format_signature_file(FAKE_KEY.key_id, b"\x00" * 63)
    with pytest.raises(SignatureFormatError):
        format_signature_file("XYZ", b"\x00" * 64)


# --- Vérification -----------------------------------------------------------------------------------------------------


def test_a_valid_signature_is_verified_and_names_the_key():
    result = verify_release_signature(SUMS, sign_with_fake_key(SUMS), FAKE_KEYS)
    assert result.status is SignatureStatus.VERIFIED and result.verified and result.key_id == FAKE_KEY.key_id


def test_no_signature_file_is_absent_never_verified_never_an_exception():
    result = verify_release_signature(SUMS, None, FAKE_KEYS)
    assert result.status is SignatureStatus.ABSENT and not result.verified
    assert verify_release_signature(SUMS, None, ()).status is SignatureStatus.ABSENT


def test_changed_sums_after_signing_are_invalid():
    content = sign_with_fake_key(SUMS)
    for tampered in (SUMS.replace(b"0", b"1", 1), SUMS + b"# ajout\n", b""):
        assert verify_release_signature(tampered, content, FAKE_KEYS).status is SignatureStatus.INVALID


def test_an_altered_signature_is_invalid():
    key_id, raw = parse_signature_file(sign_with_fake_key(SUMS))
    altered = bytearray(raw)
    altered[10] ^= 0x01
    result = verify_release_signature(SUMS, format_signature_file(key_id, bytes(altered)), FAKE_KEYS)
    assert result.status is SignatureStatus.INVALID and not result.unknown_key and result.key_id == key_id


def test_a_signature_by_another_key_is_invalid_and_flagged_as_unknown_key():
    result = verify_release_signature(SUMS, sign_with_fake_key(SUMS, OTHER_SEED), FAKE_KEYS)
    assert result.status is SignatureStatus.INVALID and result.unknown_key and result.key_id == OTHER_KEY.key_id


def test_a_signature_claiming_a_known_key_but_made_by_another_is_invalid():
    key_id, _raw = parse_signature_file(sign_with_fake_key(SUMS))
    forged = format_signature_file(key_id, ed25519.sign(OTHER_SEED, SUMS))
    result = verify_release_signature(SUMS, forged, FAKE_KEYS)
    assert result.status is SignatureStatus.INVALID and not result.unknown_key


def test_with_no_configured_key_a_signature_is_refused_not_trusted():
    assert verify_release_signature(SUMS, sign_with_fake_key(SUMS), ()).status is SignatureStatus.INVALID


def test_rotation_current_and_next_key_both_verify():
    both = (FAKE_KEY, OTHER_KEY)
    assert verify_release_signature(SUMS, sign_with_fake_key(SUMS), both).key_id == FAKE_KEY.key_id
    assert verify_release_signature(SUMS, sign_with_fake_key(SUMS, OTHER_SEED), both).key_id == OTHER_KEY.key_id


def test_the_application_self_check_passes():
    assert self_check() is None


def test_key_ids_are_stable_and_the_shipped_list_is_well_formed():
    assert FAKE_KEY.key_id == update_keys.key_id_of(FAKE_KEY.public) and len(FAKE_KEY.key_id) == 16
    assert PublicKey.from_base64(FAKE_KEY.base64) == FAKE_KEY
    with pytest.raises(ValueError):
        PublicKey.from_base64("pas du base64 !")
    with pytest.raises(ValueError):
        PublicKey(b"\x00" * 31)
    shipped = update_keys.UPDATE_PUBLIC_KEYS
    assert len({key.key_id for key in shipped}) == len(shipped) <= 2, "au plus la clé courante et la suivante"


# --- gen-update-key ---------------------------------------------------------------------------------------------------


def test_gen_update_key_writes_a_private_file_and_never_shows_the_seed(tmp_path, capsys):
    destination = tmp_path / "kut.update-key"
    assert cli.main(["gen-update-key", "--out", str(destination)]) == 0
    captured = capsys.readouterr()
    seed_text = destination.read_text(encoding="ascii").strip()
    seed = base64.b64decode(seed_text)
    assert len(seed) == 32
    for output in (captured.out, captured.err):
        assert seed_text not in output and seed.hex() not in output
        assert base64.b64encode(seed).decode() not in output
    public = PublicKey(ed25519.public_key_from_seed(seed))
    assert public.key_id in captured.out and public.base64 in captured.out
    assert "gh secret set UPDATE_SIGNING_KEY <" in captured.out and "core/update_keys.py" in captured.out
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(destination).st_mode) == 0o600


def test_gen_update_key_never_overwrites_and_never_leaks_in_errors(tmp_path, capsys):
    destination = tmp_path / "existing.update-key"
    destination.write_text("contenu précieux", encoding="utf-8")
    assert cli.main(["gen-update-key", "--out", str(destination)]) == 1
    assert destination.read_text(encoding="utf-8") == "contenu précieux"
    assert "existe déjà" in capsys.readouterr().out
    missing = tmp_path / "absent" / "x.update-key"
    assert cli.main(["gen-update-key", "--out", str(missing)]) == 1


def test_two_generated_keys_differ(tmp_path):
    first = signing.generate_update_key(tmp_path / "a.update-key")
    second = signing.generate_update_key(tmp_path / "b.update-key")
    assert first != second


def test_the_update_key_file_pattern_is_ignored_by_git():
    ignored = (Path(__file__).resolve().parent.parent / ".gitignore").read_text(encoding="utf-8")
    assert "*.update-key" in ignored


# --- sign -------------------------------------------------------------------------------------------------------------


def _release(tmp_path: Path) -> Path:
    folder = tmp_path / "release-assets"
    folder.mkdir()
    for target in RELEASE_TARGETS:
        (folder / asset_name("1.2.3", target)).write_bytes(target.slug.encode() * 50)
    write_checksums(folder)
    return folder


def _secret(seed: bytes = FAKE_SEED) -> dict[str, str]:
    return {signing.SIGNING_KEY_ENV: base64.b64encode(seed).decode("ascii")}


def test_without_the_secret_the_release_is_unsigned_and_says_so(tmp_path, monkeypatch, capsys):
    folder = _release(tmp_path)
    status = tmp_path / "status" / "update-signature.txt"
    monkeypatch.delenv(signing.SIGNING_KEY_ENV, raising=False)
    assert cli.main(["sign", str(folder), "--status-file", str(status)]) == 0
    assert status.read_text(encoding="utf-8") == "unsigned\n"
    assert not (folder / SIGNATURE_FILE).exists()
    assert "SANS signature" in capsys.readouterr().out
    assert signing.sign_release_directory(folder, {signing.SIGNING_KEY_ENV: "   "}) == "unsigned"


def test_with_the_secret_the_release_is_signed_and_verified_with_the_application_code(tmp_path, monkeypatch):
    folder = _release(tmp_path)
    status = tmp_path / "update-signature.txt"
    monkeypatch.setenv(signing.SIGNING_KEY_ENV, _secret()[signing.SIGNING_KEY_ENV])
    monkeypatch.setattr(signing, "UPDATE_PUBLIC_KEYS", FAKE_KEYS)
    monkeypatch.setattr(packaging, "UPDATE_PUBLIC_KEYS", FAKE_KEYS)
    assert cli.main(["sign", str(folder), "--status-file", str(status)]) == 0
    assert status.read_text(encoding="utf-8") == f"signed {FAKE_KEY.key_id}\n"
    assert verify_release_signature((folder / CHECKSUMS_FILE).read_bytes(), (folder / SIGNATURE_FILE).read_bytes(),
                                    FAKE_KEYS).verified
    assert verify_release_directory(folder, "1.2.3") == []


def test_a_secret_whose_public_key_is_not_in_the_repository_fails_the_job(tmp_path, monkeypatch, capsys):
    """Sinon l'application refuserait chaque mise à jour (clé inconnue) : jamais de release à moitié signée."""
    folder = _release(tmp_path)
    monkeypatch.setenv(signing.SIGNING_KEY_ENV, _secret()[signing.SIGNING_KEY_ENV])
    monkeypatch.setattr(signing, "UPDATE_PUBLIC_KEYS", ())
    status = tmp_path / "update-signature.txt"
    assert cli.main(["sign", str(folder), "--status-file", str(status)]) == 1
    assert not (folder / SIGNATURE_FILE).exists() and not status.exists()
    assert "UPDATE_PUBLIC_KEYS" in capsys.readouterr().out


@pytest.mark.parametrize("secret", ["pas du base64!", base64.b64encode(b"court").decode(), "A" * 5000],
                         ids=["non-base64", "trop-court", "demesure"])
def test_a_malformed_secret_fails_without_echoing_it(tmp_path, monkeypatch, capsys, secret):
    folder = _release(tmp_path)
    monkeypatch.setenv(signing.SIGNING_KEY_ENV, secret)
    assert cli.main(["sign", str(folder), "--status-file", str(tmp_path / "s.txt")]) == 1
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err
    assert not (folder / SIGNATURE_FILE).exists()


def test_signing_without_sums_fails_when_the_secret_exists(tmp_path):
    with pytest.raises(ReleaseError):
        signing.sign_release_directory(tmp_path, _secret(), FAKE_KEYS)


def test_verification_refuses_a_stale_or_forged_signature_file(tmp_path):
    folder = _release(tmp_path)
    (folder / SIGNATURE_FILE).write_bytes(sign_checksums((folder / CHECKSUMS_FILE).read_bytes(), FAKE_SEED))
    assert verify_release_directory(folder, "1.2.3", keys=FAKE_KEYS) == []
    problems = verify_release_directory(folder, "1.2.3", keys=(OTHER_KEY,))
    assert any(problem.startswith(f"{SIGNATURE_FILE} invalide") for problem in problems)
    # SHA256SUMS.txt modifié après la signature (les empreintes restent justes : seule la signature le détecte).
    sums = folder / CHECKSUMS_FILE
    sums.write_text("# réécrit\n" + sums.read_text(encoding="utf-8"), encoding="utf-8")
    assert any("invalide" in problem for problem in verify_release_directory(folder, "1.2.3", keys=FAKE_KEYS))


# --- Notes ------------------------------------------------------------------------------------------------------------


def test_notes_say_plainly_whether_automatic_updates_are_available():
    signed = release_notes("1.2.3", "adhoc", update_signature="signed", update_key_id=FAKE_KEY.key_id)
    assert "signée" in signed and FAKE_KEY.key_id in signed and "indisponibles" not in signed
    unsigned = release_notes("1.2.3", "adhoc", update_signature="unsigned")
    assert "Mises à jour automatiques indisponibles pour cette version" in unsigned
    assert "Automatic updates unavailable for this version" in unsigned
    assert "automatiques" not in release_notes("1.2.3", "adhoc")
    with pytest.raises(ReleaseError):
        release_notes("1.2.3", "adhoc", update_signature="peut-être")


def test_the_notes_command_refuses_an_inconsistent_state(tmp_path):
    base = ["notes", "--signing", "adhoc", "--version", "1.2.3", "--out", str(tmp_path / "n.md")]
    assert cli.main([*base, "--update-signature", "unsigned"]) == 0
    assert cli.main([*base, "--update-signature", f"signed {FAKE_KEY.key_id}"]) == 0
    assert FAKE_KEY.key_id in (tmp_path / "n.md").read_text(encoding="utf-8")
    for bad in ("signed", "unsigned abc", "", "yes"):
        assert cli.main([*base, "--update-signature", bad]) == 1
