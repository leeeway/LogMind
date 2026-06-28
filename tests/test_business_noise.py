"""
Tests for Business Noise Recognition Module

Covers:
  - Static pattern matching (SMS, auth, validation, game-specific)
  - Fault protection (real exceptions must NOT be filtered)
  - Custom per-BusinessLine patterns
  - AI-learned noise rules
  - classify_line() integration
"""

import pytest

from logmind.domain.log.business_noise import (
    classify_line,
    has_fault_protection,
    match_custom_noise,
    match_static_noise,
)


# ══════════════════════════════════════════════════════════
#  Static Noise Pattern Tests
# ══════════════════════════════════════════════════════════

class TestStaticNoisePatterns:
    """Test hand-curated noise patterns."""

    def test_sms_no_channel(self):
        line = ("[2026-05-11T10:28:02.513Z] [ERROR] cn.gyyx.securityv5.service.SendSmsService "
                "[SendSmsService.sendMessage] - 发送目标：139*8263，业务：WDLogin，"
                "插入队列失败：短信发送失败,暂无发送渠道")
        result = match_static_noise(line)
        assert result is not None
        assert result["category"] == "sms_flow"

    def test_sms_send_failure(self):
        line = "[ERROR] 短信发送失败: 发送渠道异常"
        result = match_static_noise(line)
        assert result is not None
        assert result["category"] == "sms_flow"

    def test_auth_password_error(self):
        line = ('{"message":"账号或密码错误","status":"statusError"}')
        result = match_static_noise(line)
        assert result is not None
        assert result["category"] == "auth_flow"

    def test_auth_login_lockout(self):
        line = ('{"message":"您登录已连续失败3次若达到5次账号将被限制登录1小时",'
                '"status":false,"success":false}')
        result = match_static_noise(line)
        assert result is not None
        assert result["category"] == "auth_flow"

    def test_auth_account_locked(self):
        line = "[ERROR] 账号已被锁定，请联系管理员"
        result = match_static_noise(line)
        assert result is not None
        assert result["category"] == "auth_flow"

    def test_captcha_expired(self):
        line = "[ERROR] 验证码已过期，请重新获取"
        result = match_static_noise(line)
        assert result is not None
        assert result["category"] == "captcha_flow"

    def test_captcha_error(self):
        line = "[ERROR] 验证码错误"
        result = match_static_noise(line)
        assert result is not None
        assert result["category"] == "captcha_flow"

    def test_biz_balance_insufficient(self):
        line = "[ERROR] 余额不足，请充值后再试"
        result = match_static_noise(line)
        assert result is not None
        assert result["category"] == "biz_validation"

    def test_biz_duplicate_submit(self):
        line = "[ERROR] 重复提交，请勿频繁操作"
        result = match_static_noise(line)
        assert result is not None
        assert result["category"] == "biz_validation"

    def test_biz_param_error(self):
        line = "[ERROR] 参数错误: userId 不能为空"
        result = match_static_noise(line)
        assert result is not None
        assert result["category"] == "biz_validation"

    def test_biz_rate_limit(self):
        line = "[ERROR] 请求过于频繁，请稍后再试"
        result = match_static_noise(line)
        assert result is not None
        assert result["category"] == "biz_validation"

    def test_game_role_not_found(self):
        line = "[ERROR] 角色不存在: serverId=001, roleId=12345"
        result = match_static_noise(line)
        assert result is not None
        assert result["category"] == "game_flow"

    def test_game_server_maintenance(self):
        line = "[ERROR] 服务器维护中，预计 15:00 恢复"
        result = match_static_noise(line)
        assert result is not None
        assert result["category"] == "game_flow"

    def test_token_expired(self):
        line = "[ERROR] token已过期，请重新登录"
        result = match_static_noise(line)
        assert result is not None
        assert result["category"] == "auth_flow"

    def test_no_permission(self):
        line = "[ERROR] 无权限访问该资源"
        result = match_static_noise(line)
        assert result is not None
        assert result["category"] == "biz_validation"

    def test_json_response_with_noise_keyword(self):
        """Test regex pattern for JSON success:false with noise keyword."""
        line = '{"success": false, "message": "验证码已失效"}'
        result = match_static_noise(line)
        assert result is not None

    def test_json_response_without_noise_keyword(self):
        """success:false alone should NOT match (requires additional keyword)."""
        line = '{"success": false, "message": "internal server error"}'
        result = match_static_noise(line)
        # The biz_response pattern requires additional keywords, so this should not match
        # But other patterns might still catch it
        # The key test is that a generic success=false without noise keyword doesn't trigger
        # Let's verify no category is "biz_response"
        if result:
            assert result["category"] != "biz_response"

    def test_charge_success_process_result(self):
        line = (
            "[INFO] WDGameCharge.charge - 问道兑换元宝[订单=R260529150936039315920247652]"
            "结果ProcessResult[description='Account successfully charged', "
            "errorCode=0, requestIndex=0]"
        )
        result = match_static_noise(line)
        assert result is not None
        assert result["category"] == "success_flow"

    def test_charge_success_result_bean(self):
        line = (
            "[INFO] ChangeService.changeGameNew - 调用游戏接口发元宝[changeGameNew]"
            "账号=YH793999202|兑换订单=R260529150936039315920247652"
            "游戏兑换结果ResultBean(success=true, message=, error=, data=成功)"
        )
        result = match_static_noise(line)
        assert result is not None
        assert result["category"] == "success_flow"


# ══════════════════════════════════════════════════════════
#  Fault Protection Tests (False Positive Prevention)
# ══════════════════════════════════════════════════════════

class TestFaultProtection:
    """Ensure real faults are NEVER filtered as noise."""

    def test_java_stack_trace(self):
        line = ("java.lang.NullPointerException: 密码错误 at "
                "cn.gyyx.auth.Service.check(Service.java:42)")
        assert has_fault_protection(line) is True

    def test_caused_by_chain(self):
        line = "Caused by: java.sql.SQLException: 密码错误 Connection refused"
        assert has_fault_protection(line) is True

    def test_csharp_stack_trace(self):
        line = ("System.NullReferenceException: 密码错误\n"
                "   at Gyyx.Core.Auth.Verify() in D:\\src\\Auth.cs:line 42")
        assert has_fault_protection(line) is True

    def test_connection_refused(self):
        line = "[ERROR] 短信发送失败: connection refused localhost:6379"
        assert has_fault_protection(line) is True

    def test_oom_error(self):
        line = "[ERROR] 参数错误 OutOfMemoryError: Java heap space"
        assert has_fault_protection(line) is True

    def test_timeout_exception(self):
        line = "[ERROR] 密码错误处理异常: SocketTimeoutException: connect timed out"
        assert has_fault_protection(line) is True

    def test_http_500(self):
        line = '[ERROR] {"statusCode": 500, "message": "账号或密码错误"}'
        assert has_fault_protection(line) is True

    def test_sql_exception(self):
        line = "[ERROR] 验证码错误: SQLServerException: Deadlock victim"
        assert has_fault_protection(line) is True

    def test_redis_exception(self):
        line = "[ERROR] 短信发送失败 RedisConnectionException: Unable to connect"
        assert has_fault_protection(line) is True

    def test_pool_exhausted(self):
        line = "[ERROR] 请求失败 connection pool exhausted"
        assert has_fault_protection(line) is True

    def test_pure_noise_has_no_protection(self):
        """Pure business noise should NOT have fault protection."""
        line = '{"message":"账号或密码错误","status":"statusError","success":false}'
        assert has_fault_protection(line) is False

    def test_pure_sms_noise(self):
        line = "短信发送失败,暂无发送渠道"
        assert has_fault_protection(line) is False


# ══════════════════════════════════════════════════════════
#  Classify Line Integration Tests
# ══════════════════════════════════════════════════════════

class TestClassifyLine:
    """Test the full classify_line() integration."""

    def test_sms_noise_classified(self):
        line = ("[ERROR] SendSmsService.sendMessage - 发送目标：139*8263，"
                "业务：WDLogin，插入队列失败：短信发送失败,暂无发送渠道")
        is_noise, rule = classify_line(line)
        assert is_noise is True
        assert rule is not None

    def test_auth_noise_classified(self):
        line = '{"message":"账号或密码错误","status":"statusError"}'
        is_noise, rule = classify_line(line)
        assert is_noise is True

    def test_login_lockout_noise(self):
        line = ("连续失败3次若达到5次账号将被限制登录1小时")
        is_noise, rule = classify_line(line)
        assert is_noise is True

    def test_real_exception_not_noise(self):
        """Real NullPointerException should never be classified as noise."""
        line = ("java.lang.NullPointerException: 密码错误\n"
                "  at cn.gyyx.auth.Login.verify(Login.java:88)")
        is_noise, rule = classify_line(line)
        assert is_noise is False

    def test_db_password_error_not_noise(self):
        """Database connection password error is a REAL fault."""
        line = ("[ERROR] 密码错误 Caused by: java.sql.SQLException: "
                "Access denied for user 'root'@'localhost'")
        is_noise, rule = classify_line(line)
        assert is_noise is False

    def test_redis_connection_with_noise_keyword(self):
        """Redis error with '发送失败' keyword should be real fault."""
        line = "[ERROR] 短信发送失败 RedisConnectionException: Unable to connect"
        is_noise, rule = classify_line(line)
        assert is_noise is False

    def test_custom_patterns(self):
        """Test per-business-line custom patterns."""
        custom = [
            {"pattern": "充值回调超时", "category": "payment_custom", "reason": "支付回调超时属于业务流程"},
        ]
        line = "[ERROR] 充值回调超时: orderId=12345"
        is_noise, rule = classify_line(line, custom_patterns=custom)
        assert is_noise is True
        assert rule["category"] == "payment_custom"

    def test_learned_rules(self):
        """Test AI-learned noise rules."""
        learned = [
            {"pattern": "积分兑换失败", "category": "ai_learned", "reason": "AI判定为业务噪声"},
        ]
        line = "[ERROR] 积分兑换失败: 积分不足"
        is_noise, rule = classify_line(line, learned_rules=learned)
        assert is_noise is True
        assert rule["category"] == "ai_learned"

    def test_normal_exception_not_noise(self):
        """Normal Java exception should NOT be noise."""
        line = ("com.mysql.jdbc.exceptions.jdbc4.CommunicationsException: "
                "Communications link failure at "
                "com.mysql.jdbc.ConnectionImpl.createNewIO(ConnectionImpl.java:2062)")
        is_noise, rule = classify_line(line)
        assert is_noise is False

    def test_charge_success_noise_classified(self):
        line = (
            "[INFO] ChangeService.changeGameNew - 调用游戏接口发元宝[changeGameNew]"
            "游戏兑换结果ResultBean(success=true, message=, error=, data=成功)"
        )
        is_noise, rule = classify_line(line)
        assert is_noise is True
        assert rule is not None
        assert rule["category"] == "success_flow"


# ══════════════════════════════════════════════════════════
#  Custom Pattern Matching Tests
# ══════════════════════════════════════════════════════════

class TestCustomPatternMatching:
    """Test per-BusinessLine custom noise patterns."""

    def test_simple_match(self):
        patterns = [
            {"pattern": "游戏服务器未开放", "category": "game", "reason": "服务器未开放"},
        ]
        line = "[ERROR] 游戏服务器未开放: serverId=001"
        result = match_custom_noise(line, patterns)
        assert result is not None

    def test_no_match(self):
        patterns = [
            {"pattern": "游戏服务器未开放", "category": "game", "reason": "服务器未开放"},
        ]
        line = "[ERROR] NullPointerException at GameServer.start()"
        result = match_custom_noise(line, patterns)
        assert result is None

    def test_empty_patterns(self):
        result = match_custom_noise("[ERROR] something", [])
        assert result is None

    def test_empty_pattern_string(self):
        patterns = [{"pattern": "", "category": "test", "reason": "test"}]
        result = match_custom_noise("[ERROR] something", patterns)
        assert result is None
