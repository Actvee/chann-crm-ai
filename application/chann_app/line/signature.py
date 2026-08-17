"""LINE webhook signature verification.

Each OA has its own channel secret, so the OA that a request arrived on
determines which secret validates it. Verifying against "any" secret would
let a message from the Customer OA be replayed as a Technician message.
"""
import base64
import hashlib
import hmac


def compute_signature(channel_secret: str, body: bytes) -> str:
    digest = hmac.new(channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def verify_signature(channel_secret: str, body: bytes, header_signature: str) -> bool:
    if not channel_secret or not header_signature:
        return False
    return hmac.compare_digest(compute_signature(channel_secret, body), header_signature)
