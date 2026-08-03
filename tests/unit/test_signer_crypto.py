"""Cifrado y autenticacion del signer (SPEC.md 16, SECURITY.md 2-3).

**Todo el material de estos tests es efimero y generado aqui.** No hay ninguna clave real, ni
la habra: el sistema no usa seed phrases y estos tests cifran bytes opacos.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from mit_signer.auth import (
    DEFAULT_WINDOW,
    AuthError,
    SignedRequest,
    compute_signature,
    sign_request,
    verify_request,
)
from mit_signer.crypto import DecryptionError, EncryptedBlob, decrypt, encrypt

NOW = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
MATERIAL = b"bytes-opacos-de-prueba-generados-aqui"
PASSWORD = "una-contrasena-suficientemente-larga"  # noqa: S105  (efimera, de test)


# --- Cifrado ---------------------------------------------------------------------------------


def test_round_trip_recovers_the_material() -> None:
    assert decrypt(encrypt(MATERIAL, PASSWORD), PASSWORD) == MATERIAL


def test_a_wrong_password_is_rejected() -> None:
    with pytest.raises(DecryptionError):
        decrypt(encrypt(MATERIAL, PASSWORD), "otra-cosa")


def test_the_error_does_not_reveal_which_check_failed() -> None:
    """Decirle a un atacante si fallo la contrasena o la integridad le da informacion gratis."""
    blob = encrypt(MATERIAL, PASSWORD)
    tampered = EncryptedBlob(ciphertext=blob.ciphertext[:-4] + "AAAA", salt=blob.salt)

    with pytest.raises(DecryptionError) as wrong_password:
        decrypt(blob, "otra-cosa")
    with pytest.raises(DecryptionError) as manipulated:
        decrypt(tampered, PASSWORD)
    assert str(wrong_password.value) == str(manipulated.value)


def test_tampering_is_detected_not_silently_ignored() -> None:
    """Cifrado AUTENTICADO: un archivo manipulado falla, no devuelve basura."""
    blob = encrypt(MATERIAL, PASSWORD)
    tampered = EncryptedBlob(ciphertext=blob.ciphertext[:-4] + "BBBB", salt=blob.salt)
    with pytest.raises(DecryptionError):
        decrypt(tampered, PASSWORD)


def test_encrypting_twice_gives_different_ciphertexts() -> None:
    """Salt nuevo cada vez: nadie puede deducir que dos archivos guardan lo mismo."""
    first = encrypt(MATERIAL, PASSWORD)
    second = encrypt(MATERIAL, PASSWORD)
    assert first.ciphertext != second.ciphertext
    assert first.salt != second.salt
    assert decrypt(first, PASSWORD) == decrypt(second, PASSWORD)


def test_an_empty_password_is_rejected() -> None:
    with pytest.raises(ValueError, match="contrasena"):
        encrypt(MATERIAL, "")


def test_empty_material_is_rejected() -> None:
    with pytest.raises(ValueError, match="nada que cifrar"):
        encrypt(b"", PASSWORD)


def test_the_blob_survives_a_file_round_trip() -> None:
    blob = encrypt(MATERIAL, PASSWORD)
    restored = EncryptedBlob.from_json(blob.to_json())
    assert decrypt(restored, PASSWORD) == MATERIAL


def test_the_serialised_blob_never_contains_the_plaintext() -> None:
    blob = encrypt(MATERIAL, PASSWORD)
    payload = json.loads(blob.to_json())
    assert MATERIAL.decode() not in blob.to_json()
    assert PASSWORD not in blob.to_json()
    assert set(payload) == {"algorithm", "salt", "ciphertext"}


def test_the_module_never_mentions_a_seed_phrase() -> None:
    """SECURITY.md 3: la seed phrase no existe en el sistema, en ningun formato."""
    import inspect

    from mit_signer import crypto

    source = inspect.getsource(crypto).lower()
    for forbidden in ("mnemonic", "seed_phrase", "seedphrase", "recovery_phrase"):
        assert forbidden not in source.replace("seed phrase", ""), forbidden


# --- Autenticacion ---------------------------------------------------------------------------


def test_a_valid_request_verifies() -> None:
    request = sign_request('{"amount":1}', NOW, "secreto-compartido")
    verify_request(request, "secreto-compartido", NOW)


def test_a_tampered_body_is_rejected() -> None:
    """Cambiar el importe despues de firmar tiene que invalidar la peticion."""
    request = sign_request('{"amount":1}', NOW, "secreto-compartido")
    forged = SignedRequest(
        body='{"amount":999}', timestamp=request.timestamp, signature=request.signature
    )
    with pytest.raises(AuthError, match="invalida"):
        verify_request(forged, "secreto-compartido", NOW)


def test_a_wrong_secret_is_rejected() -> None:
    request = sign_request('{"amount":1}', NOW, "secreto-compartido")
    with pytest.raises(AuthError, match="invalida"):
        verify_request(request, "otro-secreto", NOW)


def test_a_replayed_request_is_rejected() -> None:
    """Sin ventana temporal, capturar una peticion valida permite reenviarla manana."""
    request = sign_request('{"amount":1}', NOW, "secreto-compartido")
    with pytest.raises(AuthError, match="ventana"):
        verify_request(request, "secreto-compartido", NOW + timedelta(minutes=10))


def test_a_future_timestamp_is_also_rejected() -> None:
    """La ventana es simetrica: un reloj adelantado no puede ampliar el margen."""
    request = sign_request('{"amount":1}', NOW + timedelta(minutes=10), "secreto-compartido")
    with pytest.raises(AuthError, match="ventana"):
        verify_request(request, "secreto-compartido", NOW)


def test_the_timestamp_is_inside_the_signature() -> None:
    """Si quedara fuera, se podria cambiar para revivir una peticion antigua."""
    first = compute_signature("cuerpo", NOW, "secreto")
    second = compute_signature("cuerpo", NOW + timedelta(seconds=1), "secreto")
    assert first != second


def test_an_empty_secret_is_rejected() -> None:
    with pytest.raises(AuthError, match="secreto"):
        compute_signature("cuerpo", NOW, "")


def test_the_window_is_short_by_default() -> None:
    """Alargar el margen alarga la ventana de replay."""
    assert timedelta(minutes=1) >= DEFAULT_WINDOW


def test_verification_uses_constant_time_comparison() -> None:
    """Un `==` filtra por tiempo cuantos bytes coinciden y permite forjar la firma."""
    import inspect

    from mit_signer import auth

    source = inspect.getsource(auth)
    assert "compare_digest" in source
