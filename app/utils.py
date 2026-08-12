import base64
import hashlib
from cryptography.fernet import Fernet

# Encrypt/decrypt utilities using a MASTER_KEY from env
# MASTER_KEY should be kept secret (Railway env secret). We derive a 32-byte key via SHA256 and urlsafe_b64encode it.

def _derive_key(master_key: str) -> bytes:
    if not master_key:
        raise ValueError('MASTER_KEY is not set')
    digest = hashlib.sha256(master_key.encode()).digest()
    return base64.urlsafe_b64encode(digest)

def encrypt_value(plaintext: str, master_key: str) -> str:
    key = _derive_key(master_key)
    f = Fernet(key)
    token = f.encrypt(plaintext.encode())
    return token.decode()

def decrypt_value(token: str, master_key: str) -> str:
    key = _derive_key(master_key)
    f = Fernet(key)
    return f.decrypt(token.encode()).decode()
