from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class ModelSecretError(ValueError):
    pass


class ModelSecretCipher:
    """Versioned authenticated encryption for provider credentials."""

    prefix = "aesgcm:v1"

    def __init__(self, master_secret: str) -> None:
        if len(master_secret) < 24:
            raise ModelSecretError("model master secret must contain at least 24 characters")
        self._cipher = AESGCM(hashlib.sha256(master_secret.encode("utf-8")).digest())

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            raise ModelSecretError("model credential cannot be empty")
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, plaintext.encode("utf-8"), self.prefix.encode("ascii"))
        encoded_nonce = base64.urlsafe_b64encode(nonce).decode("ascii")
        encoded_ciphertext = base64.urlsafe_b64encode(ciphertext).decode("ascii")
        return f"{self.prefix}:{encoded_nonce}:{encoded_ciphertext}"

    def decrypt(self, envelope: str) -> str:
        try:
            prefix, version, encoded_nonce, encoded_ciphertext = envelope.split(":", 3)
            associated_data = f"{prefix}:{version}"
            if associated_data != self.prefix:
                raise ModelSecretError("unsupported model credential envelope")
            plaintext = self._cipher.decrypt(
                base64.urlsafe_b64decode(encoded_nonce),
                base64.urlsafe_b64decode(encoded_ciphertext),
                associated_data.encode("ascii"),
            )
            return plaintext.decode("utf-8")
        except ModelSecretError:
            raise
        except Exception as exc:
            raise ModelSecretError("model credential cannot be decrypted") from exc
