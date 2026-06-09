"""
utils/security.py
=================
Security helpers for DataPipe-RSS.

Responsibilities:
  1. Validate that required credentials are present before connectors run.
  2. Mask sensitive strings in log output (prevent accidental key leaks).
  3. Provide a lightweight HMAC signature verifier for future webhook
     inbound validation (e.g., if Telegram or Notion sends callbacks).

No cryptographic secrets are stored here.
All values are read from config.settings.SETTINGS at call-time.
"""

import hashlib
import hmac
import re
from typing import Optional

from utils.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------

def mask_secret(value: str, visible_chars: int = 6) -> str:
    """
    Partially mask a secret string for safe log output.

    Example:
        mask_secret("ABCDEFGHIJKLMNOP", 4)  →  "ABCD************"

    Args:
        value:         The secret string to mask.
        visible_chars: How many leading characters to keep visible.

    Returns:
        A masked string safe for logging.
    """
    if not value:
        return "<not set>"
    if len(value) <= visible_chars:
        return "*" * len(value)
    return value[:visible_chars] + "*" * (len(value) - visible_chars)


def mask_url(url: str) -> str:
    """
    Mask the path/query-string portion of a webhook URL, keeping only
    the scheme and host visible.

    Example:
        "https://script.google.com/macros/s/SECRETID/exec"
        → "https://script.google.com/***"

    Args:
        url: The full webhook URL.

    Returns:
        A masked URL string safe for logging.
    """
    if not url:
        return "<not set>"
    # Keep scheme + host (up to the 3rd slash), mask the rest
    parts = url.split("/", 3)
    if len(parts) >= 4:
        return "/".join(parts[:3]) + "/***"
    return url[:20] + "***"


# ---------------------------------------------------------------------------
# Credential Validation
# ---------------------------------------------------------------------------

class CredentialError(ValueError):
    """Raised when a required credential is missing before a connector runs."""


def require_credential(value: str, credential_name: str) -> str:
    """
    Assert that a credential string is non-empty.
    Raises CredentialError with a helpful message if it is empty.

    Usage (in a connector's __init__ or send method):
        url = require_credential(SETTINGS.google_sheets_webhook_url,
                                 "GOOGLE_SHEETS_WEBHOOK_URL")

    Args:
        value:           The credential string to check.
        credential_name: The .env variable name (for the error message).

    Returns:
        The original value if non-empty.

    Raises:
        CredentialError: If value is empty or whitespace.
    """
    if not value or not value.strip():
        raise CredentialError(
            f"Missing required credential: '{credential_name}'. "
            f"Please set it in your .env file."
        )
    return value


def validate_url(url: str, credential_name: str) -> str:
    """
    Validate that a URL looks like a real HTTPS URL before we send
    data to it.  Lightweight check — not a full RFC 3986 parser.

    Args:
        url:             The URL string to validate.
        credential_name: Used in the error message.

    Returns:
        The original URL if valid.

    Raises:
        CredentialError: If the URL is missing or malformed.
    """
    require_credential(url, credential_name)
    pattern = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)
    if not pattern.match(url.strip()):
        raise CredentialError(
            f"Credential '{credential_name}' does not look like a valid URL: "
            f"{mask_url(url)}"
        )
    return url.strip()


# ---------------------------------------------------------------------------
# HMAC Signature Verification (for inbound webhooks — future use)
# ---------------------------------------------------------------------------

def verify_hmac_signature(
    payload: bytes,
    received_signature: str,
    secret: str,
    algorithm: str = "sha256",
) -> bool:
    """
    Verify an HMAC signature on an inbound webhook payload.

    Use this when a service (e.g., GitHub, Stripe) sends a signed
    POST request to DataPipe-RSS and you need to confirm it's genuine.

    Args:
        payload:            Raw request body bytes.
        received_signature: The hex-digest signature from the request header.
        secret:             The shared HMAC secret.
        algorithm:          Hash algorithm to use (default: sha256).

    Returns:
        True if the signature is valid, False otherwise.
    """
    try:
        mac = hmac.new(
            key=secret.encode("utf-8"),
            msg=payload,
            digestmod=getattr(hashlib, algorithm),
        )
        expected = mac.hexdigest()
        # Use compare_digest to prevent timing attacks
        is_valid: bool = hmac.compare_digest(expected, received_signature)
        if not is_valid:
            log.warning(
                "HMAC signature mismatch. Expected: %s, Received: %s",
                mask_secret(expected, 8),
                mask_secret(received_signature, 8),
            )
        return is_valid
    except Exception as exc:
        log.error("HMAC verification failed with exception: %s", exc)
        return False
