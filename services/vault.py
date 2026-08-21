"""
Minimal secret vault: Fernet symmetric encryption around the Credential.value_encrypted
column. Good enough for an MVP; swap for HashiCorp Vault / AWS Secrets Manager when the
farm grows past a handful of operators (per doc's open question §10.5).

CRITICAL: decrypt() must only ever be called from the posting/backend layer,
right before a service call — never surfaced to the bot or dashboard UI.
"""
from cryptography.fernet import Fernet

from config import settings

_fernet = Fernet(settings.VAULT_ENCRYPTION_KEY.encode())


def encrypt(plaintext: str) -> bytes:
    return _fernet.encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    return _fernet.decrypt(ciphertext).decode()
