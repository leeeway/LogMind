"""
Unit Tests for Log Quality Filter Stage

Tests false-positive elimination, shallow error detection,
and business noise filtering.
"""

import pytest
from logmind.domain.analysis.stages.quality_filter import (
    LogQualityFilterStage,
    _extract_message_level,
    _has_real_error_indicator,
)


class TestMessageLevelExtraction:
    """Tests for extracting log levels from message content."""

    def test_bracket_error(self):
        assert _extract_message_level("[ERROR] Something failed") == "ERROR"

    def test_bracket_warn(self):
        assert _extract_message_level("[WARN] Low disk space") == "WARN"

    def test_bracket_info(self):
        assert _extract_message_level("[INFO] Service started") == "INFO"

    def test_nlog_format(self):
        line = "2024-04-23 10:00:00,123 [worker-1] DEBUG Gyyx.Core.CacheManager - Cache hit"
        assert _extract_message_level(line) == "DEBUG"

    def test_nlog_error(self):
        line = "2024-04-23 10:00:00,123 [155] ERROR Gyyx.Core.UserService - Failed"
        assert _extract_message_level(line) == "ERROR"

    def test_java_logback_format(self):
        line = "[2024-04-23 10:00:00.123] [main] [FATAL] Application crashed"
        assert _extract_message_level(line) == "FATAL"

    def test_no_level_found(self):
        assert _extract_message_level("Just a plain message") is None

    def test_case_insensitive(self):
        assert _extract_message_level("[error] lowercase") == "ERROR"


class TestRealErrorIndicator:
    """Tests for detecting real error content vs noise."""

    def test_java_exception(self):
        assert _has_real_error_indicator("java.lang.NullPointerException") is True

    def test_csharp_exception(self):
        assert _has_real_error_indicator("System.NullReferenceException") is True

    def test_timeout_keyword(self):
        assert _has_real_error_indicator("Connection timed out after 30s") is True

    def test_http_500(self):
        assert _has_real_error_indicator("HTTP 500 Internal Server Error") is True

    def test_chinese_fault(self):
        assert _has_real_error_indicator("数据库连接超时") is True
        assert _has_real_error_indicator("服务异常") is True

    def test_normal_info_not_error(self):
        assert _has_real_error_indicator("User logged in successfully") is False

    def test_stack_frame(self):
        assert _has_real_error_indicator("at com.example.Foo.bar(Foo.java:10)") is True


class TestBusinessNoise:
    """Tests for business operation noise filtering."""

    def test_success_response_is_noise(self):
        stage = LogQualityFilterStage()
        assert stage._is_business_noise('{"status": true, "message": "获取成功"}') is True

    def test_error_response_not_noise(self):
        stage = LogQualityFilterStage()
        assert stage._is_business_noise('{"status": true, "Exception": "NullRef"}') is False

    def test_normal_log_not_noise(self):
        stage = LogQualityFilterStage()
        assert stage._is_business_noise("[ERROR] Database connection failed") is False


class TestShallowError:
    """Tests for detecting misused log.error() calls."""

    def test_error_without_real_indicator(self):
        stage = LogQualityFilterStage()
        # An [ERROR] log that's just a routine message
        assert stage._is_shallow_error("[ERROR] User preference updated successfully") is True

    def test_error_with_exception_not_shallow(self):
        stage = LogQualityFilterStage()
        assert stage._is_shallow_error("[ERROR] NullPointerException at service") is False

    def test_warn_level_not_shallow(self):
        stage = LogQualityFilterStage()
        # Shallow error detection only applies to ERROR level
        assert stage._is_shallow_error("[WARN] Something happened") is False

    def test_info_level_not_shallow(self):
        stage = LogQualityFilterStage()
        assert stage._is_shallow_error("[INFO] Service started") is False
