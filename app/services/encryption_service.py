"""AES-256-GCM encryption for storing API keys in the database."""
from __future__ import annotations
import base64
import os
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class EncryptionService:
    """
    AES-256-GCM symmetric encryption.

    The ENCRYPTION_KEY env var must be a base64-encoded 32-byte secret.
    Generate one with:
        python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"

    In development, if ENCRYPTION_KEY is not set, a deterministic dev key is derived
    from the app SECRET_KEY so the service still starts — never use this in production.
    """

    # Stable dev fallback — predictable so encrypted values survive restarts in dev.
    _DEV_FALLBACK_KEY = base64.b64encode(b"dev-key-not-for-production-use!!").decode()

    def __init__(self, raw_key: str = ""):
        if not raw_key:
            raw_key = self._DEV_FALLBACK_KEY
        try:
            key_bytes = base64.b64decode(raw_key)
        except Exception:
            # Try hex
            key_bytes = bytes.fromhex(raw_key)
        if len(key_bytes) != 32:
            raise ValueError(
                "ENCRYPTION_KEY must decode to exactly 32 bytes. "
                "Generate with: python -c \"import os,base64; print(base64.b64encode(os.urandom(32)).decode())\""
            )
        self._aesgcm = AESGCM(key_bytes)

    def encrypt(self, plaintext: str) -> str:
        """Returns base64(nonce + ciphertext). Each call uses a fresh 96-bit nonce."""
        nonce = os.urandom(12)
        ct = self._aesgcm.encrypt(nonce, plaintext.encode(), None)
        return base64.b64encode(nonce + ct).decode()

    def decrypt(self, token: str) -> str:
        """Decrypts a value produced by encrypt()."""
        raw = base64.b64decode(token)
        nonce, ct = raw[:12], raw[12:]
        return self._aesgcm.decrypt(nonce, ct, None).decode()

    def is_encrypted(self, value: str) -> bool:
        """Heuristic: encrypted blobs are base64 and long enough to contain a nonce."""
        try:
            raw = base64.b64decode(value)
            return len(raw) > 12  # nonce(12) + tag(16) + at least 1 byte payload
        except Exception:
            return False


# ── Singleton ────────────────────────────────────────────────────────────────
_service: Optional[EncryptionService] = None


def get_encryption_service() -> EncryptionService:
    global _service
    if _service is None:
        from config import settings
        _service = EncryptionService(settings.ENCRYPTION_KEY)
    return _service
