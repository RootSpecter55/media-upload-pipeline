import hashlib
import hmac
import secrets


def keys_match(given: str, expected: str) -> bool:
    """Constant-time comparison of two secrets of any length (both are hashed first)."""
    return hmac.compare_digest(
        hashlib.sha256(given.encode()).digest(), hashlib.sha256(expected.encode()).digest()
    )


def bearer_token(authorization: str | None) -> str | None:
    if authorization and authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ")
    return None


def sha256_hex(value: str) -> str:
    """For API keys: random and long, so a fast hash is enough, and lookups need it to be stable."""
    return hashlib.sha256(value.encode()).hexdigest()


def new_api_key() -> str:
    return f"mk_{secrets.token_urlsafe(32)}"


def sign(secret: str, *parts: str | int) -> str:
    """HMAC-SHA256 over the parts joined by newlines, hex. Used for signed URLs."""
    message = "\n".join(str(part) for part in parts).encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def signature_valid(secret: str, signature: str, *parts: str | int) -> bool:
    return hmac.compare_digest(sign(secret, *parts), signature)
