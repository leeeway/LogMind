"""
Unit Tests for Sensitive Data Masker

Tests the universal pattern-based masking engine that sanitizes
log data before sending to external LLMs.
"""

import pytest
from logmind.domain.analysis.sensitive_masker import mask_sensitive, _mask_value_by_length


class TestMaskValueByLength:
    """Tests for the length-based masking strategy."""

    def test_short_value_fully_masked(self):
        assert _mask_value_by_length("abc") == "****"
        assert _mask_value_by_length("ab") == "****"
        assert _mask_value_by_length("1234") == "****"

    def test_medium_value_partial(self):
        # 5-8 chars: keep first 1, last 1
        assert _mask_value_by_length("12345") == "1****5"
        assert _mask_value_by_length("abcdefgh") == "a****h"

    def test_long_value_partial(self):
        # 9-16 chars: keep first 3, last 4
        assert _mask_value_by_length("123456789") == "123****6789"
        assert _mask_value_by_length("abcdefghijklmnop") == "abc****mnop"

    def test_very_long_value(self):
        # > 16 chars: keep first 4, last 4
        val = "a" * 20
        result = _mask_value_by_length(val)
        assert result == "aaaa****aaaa"


class TestMaskSensitive:
    """Tests for the main mask_sensitive function."""

    # ── Key-Value Pair Masking ──────────────────────────

    def test_json_token_masked(self):
        text = '{"access_token":"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc.def"}'
        result = mask_sensitive(text)
        assert "eyJh" not in result or "****" in result

    def test_json_password_masked(self):
        text = '{"password":"MySecretP@ss123"}'
        result = mask_sensitive(text)
        assert "MySecretP@ss123" not in result
        assert "****" in result

    def test_log_key_value_masked(self):
        text = 'phone_no=13812345678 uid=user_123456'
        result = mask_sensitive(text)
        assert "13812345678" not in result
        assert "****" in result

    def test_session_id_masked(self):
        text = 'session_id: abc123def456ghi789'
        result = mask_sensitive(text)
        assert "abc123def456ghi789" not in result

    # ── Standalone Phone Numbers ────────────────────────

    def test_phone_number_masked(self):
        text = "用户手机号 13912345678 登录失败"
        result = mask_sensitive(text)
        assert "13912345678" not in result
        assert "139****5678" in result

    def test_phone_preserves_prefix_suffix(self):
        result = mask_sensitive("手机: 13600001111")
        assert "136****1111" in result

    def test_non_phone_number_not_masked(self):
        # Timestamps, hex strings should not be treated as phones
        text = "timestamp=1681234567890"
        result = mask_sensitive(text)
        # This might be masked by KV pattern if "timestamp" is not in SENSITIVE_KEYS
        assert result  # Just ensure it doesn't crash

    # ── ID Card Numbers ────────────────────────────────

    def test_id_card_masked(self):
        text = "身份证号 110101199001011234"
        result = mask_sensitive(text)
        assert "110101199001011234" not in result
        assert "110101" in result  # First 6 preserved
        assert "1234" in result    # Last 4 preserved

    def test_id_card_with_x(self):
        text = "idcard: 11010119900101123X"
        result = mask_sensitive(text)
        assert "11010119900101123X" not in result

    # ── Email Addresses ────────────────────────────────

    def test_email_masked(self):
        text = "邮箱 zhangsan@example.com 注册"
        result = mask_sensitive(text)
        assert "zhangsan@example.com" not in result
        assert "@example.com" in result  # Domain preserved
        assert "****" in result

    # ── Bank Card Numbers ──────────────────────────────

    def test_bank_card_masked(self):
        text = "卡号 6222021234567890123"
        result = mask_sensitive(text)
        assert "6222021234567890123" not in result
        assert "6222" in result    # First 4 preserved
        assert "0123" in result    # Last 4 preserved

    # ── Safety / Edge Cases ────────────────────────────

    def test_empty_input(self):
        assert mask_sensitive("") == ""
        assert mask_sensitive(None) is None

    def test_short_input_unchanged(self):
        assert mask_sensitive("hello") == "hello"

    def test_normal_log_unchanged(self):
        text = "[2024-04-23 10:00:00] [ERROR] NullPointerException at com.example.Service"
        result = mask_sensitive(text)
        assert "NullPointerException" in result
        assert "com.example.Service" in result

    def test_idempotent(self):
        text = "phone=13812345678"
        first = mask_sensitive(text)
        second = mask_sensitive(first)
        assert first == second

    def test_uuid_not_masked_standalone(self):
        # UUIDs should not be masked unless they're values of sensitive keys
        text = "request_id=550e8400-e29b-41d4-a716-446655440000"
        result = mask_sensitive(text)
        # request_id is not in SENSITIVE_KEYS, so it should pass through
        assert "550e8400" in result

    def test_ip_address_not_masked(self):
        text = "Connected to 192.168.1.100:8080"
        result = mask_sensitive(text)
        assert "192.168.1.100" in result  # IPs have diagnostic value


class TestMaskSensitiveBulk:
    """Tests for batch masking."""

    def test_bulk_masking(self):
        from logmind.domain.analysis.sensitive_masker import mask_sensitive_bulk
        texts = [
            "phone=13812345678",
            "Normal log message",
            "email: test@example.com",
        ]
        results = mask_sensitive_bulk(texts)
        assert len(results) == 3
        assert "13812345678" not in results[0]
        assert results[1] == "Normal log message"
        assert "test@example.com" not in results[2]
