"""
Client API keys. Only SHA-256 hashes of the keys are kept in memory, and a short hash prefix
is the client id used for rate limits and logs, so a raw key is never stored or printed.
"""
import hashlib


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class ApiKeys:
    def __init__(self, keys: list[str]) -> None:
        self._hashes = {hash_key(k) for k in keys if k}

    def __len__(self) -> int:
        return len(self._hashes)

    def client_id(self, key: str | None) -> str | None:
        """The client id for a valid key, or None if the key is missing or unknown."""
        if not key:
            return None
        digest = hash_key(key)
        return digest[:16] if digest in self._hashes else None
