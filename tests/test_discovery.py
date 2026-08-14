"""
Tests for ES Index Auto-Discovery

Tests:
  - Index name parsing (service name extraction)
  - Edge cases: no prefix, date suffixes, domain suffixes
"""

import pytest

from logmind.domain.tenant.discovery import parse_service_name


class TestParseServiceName:
    """Test parse_service_name with real-world index naming patterns."""

    def test_standard_with_date(self):
        """master-game-api-2026.08.14 → game-api"""
        assert parse_service_name("master-game-api-2026.08.14") == "game-api"

    def test_domain_suffix_with_date(self):
        """master-stage-billing-pay.gyyx.cn-2026.08.14 → stage-billing-pay.gyyx.cn"""
        result = parse_service_name("master-stage-billing-pay.gyyx.cn-2026.08.14")
        assert result == "stage-billing-pay.gyyx.cn"

    def test_domain_suffix_billing_change(self):
        """master-stage-billing-change.gyyx.cn-2026.08.14 → stage-billing-change.gyyx.cn"""
        result = parse_service_name("master-stage-billing-change.gyyx.cn-2026.08.14")
        assert result == "stage-billing-change.gyyx.cn"

    def test_domain_suffix_directpay(self):
        """master-stage-billing-directpay.gyyx.cn-2026.08.14 → stage-billing-directpay.gyyx.cn"""
        result = parse_service_name("master-stage-billing-directpay.gyyx.cn-2026.08.14")
        assert result == "stage-billing-directpay.gyyx.cn"

    def test_no_date_suffix(self):
        """master-slowcoach-connector-server → slowcoach-connector-server"""
        assert parse_service_name("master-slowcoach-connector-server") == "slowcoach-connector-server"

    def test_numeric_suffix(self):
        """master-app-server-000001 → app-server"""
        assert parse_service_name("master-app-server-000001") == "app-server"

    def test_year_month_only(self):
        """master-user-center-2026.08 → user-center"""
        assert parse_service_name("master-user-center-2026.08") == "user-center"

    def test_dash_date_format(self):
        """master-svc-2026-08-14 → svc"""
        assert parse_service_name("master-svc-2026-08-14") == "svc"

    def test_develop_prefix_ignored(self):
        """develop-game-api-2026.08.14 → None"""
        assert parse_service_name("develop-game-api-2026.08.14") is None

    def test_system_index_ignored(self):
        """.kibana → None"""
        assert parse_service_name(".kibana") is None

    def test_empty_string(self):
        assert parse_service_name("") is None

    def test_prefix_only(self):
        """master- with nothing after → None"""
        assert parse_service_name("master-") is None

    def test_custom_prefix(self):
        """Custom prefix support"""
        result = parse_service_name("prod-my-service-2026.08.14", prefix="prod-")
        assert result == "my-service"

    def test_no_prefix_match(self):
        """Random index name → None"""
        assert parse_service_name("nginx-log-json-2026.08.14") is None

    def test_complex_service_name(self):
        """master-game-community-qr.module.gyyx.cn-2026.08.14"""
        result = parse_service_name("master-game-community-qr.module.gyyx.cn-2026.08.14")
        assert result == "game-community-qr.module.gyyx.cn"
