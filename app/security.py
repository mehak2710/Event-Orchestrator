import hashlib
import hmac


def sign(body: bytes, secret: str) -> str:
    """Return the signature header value for a raw request body."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(body: bytes, header: str | None, secret: str) -> bool:
    if not header:
        return False
    return hmac.compare_digest(sign(body, secret), header.strip())