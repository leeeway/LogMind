"""
Sensitive Data Masker — Universal Log Sanitizer for LLM Safety

Masks sensitive data in log messages BEFORE they are sent to external LLMs.
Uses generic data-format patterns (not site-specific rules), so it works
universally across all business lines without per-site configuration.

Masking strategy (ordered by specificity):
  1. Key-value pairs: JSON/log keys like "phone_no", "access_token" → mask values
  2. Standalone data formats: phone numbers, ID cards, emails, IPs, etc.

Design principles:
  - Pattern-based, not field-name-based: detects data shape, not log structure
  - Preserves diagnostic value: keeps first/last chars for correlation
  - Zero configuration: works out-of-the-box for any business line
  - Idempotent: masking already-masked text produces the same result
"""

import re
from functools import lru_cache

from logmind.core.logging import get_logger

logger = get_logger(__name__)


# ── Sensitive key names (case-insensitive, used in key-value pair detection) ──
# These are generic field names that commonly appear across systems.
# Derived from real production logs (tong-kernel, interface.security, actionv3).
_SENSITIVE_KEYS = frozenset({
    # Authentication tokens
    "access_token", "accesstoken", "refresh_token", "refreshtoken",
    "token", "auth_token", "authtoken", "bearer", "jwt",
    "session_id", "sessionid", "session_token",
    "api_key", "apikey", "secret", "secret_key", "secretkey",
    "password", "passwd", "pwd",
    # Personal identifiers
    "phone", "phone_no", "phoneno", "phone_number", "phonenumber",
    "mobile", "mobile_no", "mobileno", "cellphone", "tel",
    "id_card", "idcard", "id_number", "idnumber", "identity",
    "email", "mail", "e_mail",
    # Account / user identifiers
    "account", "account_no", "accountno",
    "user_id", "userid", "member_id", "memberid",
    "device_id", "deviceid", "device_token", "devicetoken",
    "unique_id", "uniqueid", "uid", "openid", "unionid",
    "imei", "imsi", "mac_address", "macaddress",
    # Financial
    "bank_card", "bankcard", "card_no", "cardno",
})


def _mask_value_by_length(value: str) -> str:
    """
    Mask a sensitive value, preserving first/last chars for correlation.

    Strategy based on value length:
      - <= 4 chars: full mask (****)
      - 5-8 chars: keep first 1, last 1
      - 9-16 chars: keep first 3, last 4
      - > 16 chars: keep first 4, last 4
    """
    length = len(value)
    if length <= 4:
        return "****"
    elif length <= 8:
        return value[0] + "****" + value[-1]
    elif length <= 16:
        return value[:3] + "****" + value[-4:]
    else:
        return value[:4] + "****" + value[-4:]


# ── Phone number format detector (for KV replacer) ──────────
_PHONE_VALUE_RE = re.compile(r'^1[3-9]\d{9}$')


# ── Pattern 1: Key-Value Pairs ──────────────────────────────
# Matches: "key":"value", "key": "value", key=value, key: value
# Works for JSON, log4j MDC, Spring properties, URL params, etc.

def _build_kv_pattern() -> re.Pattern:
    """Build a regex that matches any sensitive key followed by its value."""
    # Escape key names and join with alternation
    keys_pattern = "|".join(re.escape(k) for k in sorted(_SENSITIVE_KEYS, key=len, reverse=True))
    return re.compile(
        r'(?i)'                          # Case-insensitive
        r'(?:"|\')?' + r''               # Optional quote before key
        r'(' + keys_pattern + r')'       # Group 1: key name
        r'(?:"|\')?' + r''               # Optional quote after key
        r'\s*[:=]\s*'                    # Separator (: or =)
        r'(?:"|\')?' + r''               # Optional quote before value
        r'([^"\',}\s&\]]{3,})'           # Group 2: value (at least 3 chars, non-delimiter)
        r'(?:"|\')?' + r'',              # Optional quote after value
    )


_KV_PATTERN = _build_kv_pattern()


def _kv_replacer(match: re.Match) -> str:
    """
    Replace the value portion of a key-value pair while preserving the key.

    Special handling:
      - Phone-like values (11 digits, 1[3-9]x): standard phone mask (keep first 3 + last 4)
      - All others: generic length-based masking
    """
    full = match.group(0)
    value = match.group(2)

    # Phone number detection: apply standard phone masking
    if _PHONE_VALUE_RE.match(value):
        masked = value[:3] + "****" + value[-4:]
    else:
        masked = _mask_value_by_length(value)

    return full.replace(value, masked, 1)


# ── Pattern 2: Standalone Data Formats ──────────────────────
# Detects sensitive data by their format, regardless of key names.

# Chinese mainland phone numbers: 1[3-9]X-XXXX-XXXX
_PHONE_RE = re.compile(
    r'(?<![0-9a-fA-F-])'    # Not preceded by hex/dash (avoid UUID fragments)
    r'(1[3-9]\d)\d{4}(\d{4})'
    r'(?![0-9a-fA-F-])'     # Not followed by hex/dash
)

# Chinese ID card numbers: 18 digits (last may be X)
_IDCARD_RE = re.compile(
    r'(?<![0-9])'
    r'(\d{6})\d{8}(\d{3}[0-9Xx])'
    r'(?![0-9])'
)

# Email addresses
_EMAIL_RE = re.compile(
    r'([a-zA-Z0-9._%+-]{1,3})[a-zA-Z0-9._%+-]*'
    r'(@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})'
)

# Bank card numbers: 16-19 consecutive digits
_BANKCARD_RE = re.compile(
    r'(?<![0-9a-fA-F-])'
    r'(\d{4})\d{8,11}(\d{4})'
    r'(?![0-9a-fA-F-])'
)

# IPv4 internal addresses with port (mask the port is unnecessary, but mask IP)
# We DON'T mask IPs — they have diagnostic value and are not PII.

# UUID/Token values in isolation: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
# Only mask when they appear as standalone values (not as ES _id, log IDs, etc.)
# We handle these via the KV pattern above (key=token, value=uuid).


def mask_sensitive(text: str) -> str:
    """
    Mask sensitive data in a log message using universal pattern matching.

    This is the main entry point. Apply to any log text before sending to LLMs.
    Works across all business lines without site-specific configuration.

    Args:
        text: Raw log message text

    Returns:
        Sanitized text with sensitive values masked
    """
    if not text or len(text) < 10:
        return text

    try:
        # Phase 1: Key-value pair masking (highest priority, most precise)
        result = _KV_PATTERN.sub(_kv_replacer, text)

        # Phase 2: Standalone phone numbers (not already caught by KV)
        result = _PHONE_RE.sub(r'\1****\2', result)

        # Phase 3: ID card numbers
        result = _IDCARD_RE.sub(r'\1********\2', result)

        # Phase 4: Email addresses
        result = _EMAIL_RE.sub(r'\1****\2', result)

        # Phase 5: Bank card numbers (16-19 digits)
        result = _BANKCARD_RE.sub(r'\1********\2', result)

        return result

    except Exception as e:
        # Masking failure should NEVER break the pipeline
        logger.warning("sensitive_mask_error", error=str(e))
        return text


def mask_sensitive_bulk(texts: list[str]) -> list[str]:
    """Mask sensitive data in a list of log messages."""
    return [mask_sensitive(t) for t in texts]
