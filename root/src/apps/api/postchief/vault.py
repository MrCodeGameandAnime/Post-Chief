import json
from cryptography.fernet import Fernet


class Vault:
    def __init__(self, key: str):
        self.cipher = Fernet(key.encode())

    def encrypt(self, data: dict) -> str:
        return self.cipher.encrypt(json.dumps(data).encode()).decode()

    def decrypt(self, ciphertext: str) -> dict:
        return json.loads(self.cipher.decrypt(ciphertext.encode()))
