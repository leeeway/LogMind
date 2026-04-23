"""
Unit Tests — Analysis Quality Scorer

Tests quality scoring dimensions and grading logic.
"""

from logmind.domain.analysis.quality_scorer import (
    score_analysis_quality,
    is_low_quality,
)


class TestScoreAnalysisQuality:
    """Test the main scoring function."""

    def test_empty_content(self):
        result = score_analysis_quality("")
        assert result["score"] == 0
        assert result["grade"] == "low"
        assert result["should_retry"] is True

    def test_none_content(self):
        result = score_analysis_quality(None)
        assert result["grade"] == "low"

    def test_short_generic_content(self):
        result = score_analysis_quality("需要进一步分析")
        assert result["grade"] == "low"
        assert result["score"] < 40

    def test_high_quality_analysis(self):
        content = """
        ## 根因分析

        NullPointerException 在 com.app.service.UserService.java:142 行，
        phoneToken 参数未做空值校验。

        ## 影响范围
        影响用户登录流程，涉及 3 个 Pod，过去 1 小时发生 47 次。

        ## 修复建议
        1. 在 UserService.getProfile() 方法增加空值检查
        2. 升级 spring-security 依赖版本至 5.8.x 修复已知 CVE
        3. 设置连接池超时时间从 30s 调整为 60s
        """
        result = score_analysis_quality(content, confidence=0.9)
        assert result["grade"] == "high"
        assert result["score"] >= 70
        assert result["should_retry"] is False

    def test_medium_quality(self):
        content = "数据库连接超时，可能是连接池配置问题。建议检查相关配置和数据库负载情况。"
        result = score_analysis_quality(content, confidence=0.6)
        assert result["grade"] in ("medium", "low")

    def test_should_retry_requires_enough_logs(self):
        """Low quality with too few logs should NOT trigger retry."""
        result = score_analysis_quality("短", log_count=1)
        assert result["should_retry"] is False

    def test_should_retry_with_enough_logs(self):
        """Low quality with enough logs SHOULD trigger retry."""
        result = score_analysis_quality("需要进一步分析", log_count=5)
        assert result["grade"] == "low"
        assert result["should_retry"] is True


class TestGenericPhraseDetection:
    """Test specificity scoring."""

    def test_multiple_generic_phrases(self):
        content = "需要进一步分析。建议查看日志。请检查相关配置。"
        result = score_analysis_quality(content)
        assert any("generic" in r.lower() for r in result["reasons"])

    def test_no_generic_phrases(self):
        content = "NullPointerException at com.app.Service.run(Service.java:42). " * 5
        result = score_analysis_quality(content, confidence=0.9)
        # Should NOT have generic phrase penalty
        assert not any("generic" in r.lower() for r in result["reasons"])


class TestActionabilityScoring:
    """Test actionable recommendation detection."""

    def test_fix_version_detected(self):
        content = "需要升级 spring-boot 版本到 2.7.x 修复此安全漏洞。根因是 CVE-2022-xxxx。"
        result = score_analysis_quality(content, confidence=0.8)
        assert result["score"] > 40  # Actionability should boost score

    def test_timeout_config_detected(self):
        content = "建议调整连接池超时时间从 30s 到 60s，并增加重试次数。根因分析完成。"
        result = score_analysis_quality(content, confidence=0.8)
        assert not any("no actionable" in r.lower() for r in result["reasons"])

    def test_no_actionable_content(self):
        content = "发现了一些异常日志。系统运行状态不太正常。"
        result = score_analysis_quality(content, confidence=0.5)
        assert any("actionable" in r.lower() for r in result["reasons"])


class TestConfidenceAlignment:
    """Test confidence scoring dimension."""

    def test_high_confidence_boost(self):
        content = "标准分析内容" * 20
        high = score_analysis_quality(content, confidence=0.9)
        low = score_analysis_quality(content, confidence=0.3)
        assert high["score"] > low["score"]

    def test_low_confidence_reason(self):
        content = "标准分析内容" * 10
        result = score_analysis_quality(content, confidence=0.3)
        assert any("confidence" in r.lower() for r in result["reasons"])


class TestIsLowQuality:
    """Test the quick-check helper."""

    def test_empty_is_low(self):
        assert is_low_quality("") is True

    def test_good_content_not_low(self):
        content = "NullPointerException 根因分析：代码行 142 的 phoneToken 为空。" * 5
        assert is_low_quality(content, confidence=0.9) is False

    def test_short_generic_is_low(self):
        assert is_low_quality("请检查相关配置") is True
