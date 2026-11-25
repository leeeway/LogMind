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

    @pytest.mark.parametrize(
        "line",
        [
            "动态码校验失败，共尝试8次均未匹配",
            "动态码输入错误，请重新输入动态码",
            "光宇通乾坤码验证失败，结果：null",
        ],
    )
    def test_dynamic_code_validation_is_business_noise(self, line):
        result = match_static_noise(line)
        assert result is not None
        assert result["category"] == "captcha_flow"

    def test_tls_v1_negotiation_warning_is_not_realtime_incident(self):
        result = match_static_noise(
            "TDSChannel.enableSSL - TLSv1 was negotiated. "
            "Please update server and client to use TLSv1.2 at minimum."
        )
        assert result is not None
        assert result["category"] == "compatibility_warning"

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

    def test_custom_forced_pattern_overrides_fault_protection(self):
        """Explicit force=True custom rule can suppress expected 3rd-party exceptions."""
        custom = [
            {
                "pattern": "qiankunsuo.in",
                "category": "decommissioned_third_party",
                "reason": "三方域名已下线，预期失败",
                "force": True,
            },
        ]
        line = (
            "[ERROR] System.Net.WebException: 未能解析此远程名称: 'qiankunsuo.in'\n"
            "  at System.Net.HttpWebRequest.GetResponse()"
        )
        # Without force=True, fault protection keeps it
        is_noise_default, _ = classify_line(
            line, custom_patterns=[{"pattern": "qiankunsuo.in", "category": "test"}]
        )
        assert is_noise_default is False

        # With force=True, operator override filters it out cleanly
        is_noise_forced, rule = classify_line(line, custom_patterns=custom)
        assert is_noise_forced is True
        assert rule["category"] == "decommissioned_third_party"

    def test_connector_bootstrap_degrade_and_visitor_leave_conn_7002_noise(self):
        """Connector bootstrap degrade and visitor-leave CONN_7002 are suppressed."""
        bootstrap_line = (
            "[2026-11-25 17:38:20.687] [WARN] [sc.connection.core.protocol.capability.MessageBootstrapService]"
            " - [traceId=5545de1598e4000] bootstrap 消息落库失败，降级为普通消息, sessionId:abc, roomId:room-1"
        )
        is_noise, rule = classify_line(bootstrap_line)
        assert is_noise is True
        assert rule["category"] == "connector_degrade_flow"

        leave_line = (
            "[ERROR] Connector推送到WebSocket失败 - msgType=1120 | error=WebSocket 消息投递失败 [CONN_7002]\n"
            "sc.connection.core.exception.ConnectionBusinessException: WebSocket 消息投递失败 [CONN_7002]\n"
            "\tat sc.connection.core.protocol.impl.control.ControlVisitorLeaveProtocolImpl.receive(ControlVisitorLeaveProtocolImpl.java:42)"
        )
        is_leave_noise, leave_rule = classify_line(leave_line)
        assert is_leave_noise is True
        assert leave_rule["category"] == "websocket_client_disconnect"

        # Non-leave CONN_7002 without msgType=1120 / ControlVisitorLeaveProtocolImpl must NOT be suppressed by static rule
        normal_chat_line = (
            "[ERROR] Connector推送到WebSocket失败 - msgType=1001 | error=WebSocket 消息投递失败 [CONN_7002]\n"
            "sc.connection.core.exception.ConnectionBusinessException: WebSocket 消息投递失败 [CONN_7002]\n"
            "\tat sc.connection.core.protocol.impl.chat.ChatSendProtocolImpl.receive(ChatSendProtocolImpl.java:88)"
        )
        is_chat_noise, _ = classify_line(normal_chat_line)
        assert is_chat_noise is False

    def test_client_disconnect_csrf_method_and_ratelimit_noise(self):
        """Common client-side and rate-limit noises are cleanly suppressed."""
        cases = [
            (
                "[ERROR] 访客网关校验访客身份失败,clientName:IOS,result:ResultBean(retCode=STATUS_ERROR, message=登录失效)",
                "auth_flow",
            ),
            (
                "[ERROR] 互斥锁 mutexLock:chat_assign:seat_lock:3:262 RedisLockException: 操作过快，请稍后重试\n\tat cn.gydev.lib.MutexLockUtil.lock(MutexLockUtil.java:55)",
                "biz_rate_limit",
            ),
            (
                "[ERROR] reactor.netty.channel.AbortedException: Connection has been closed BEFORE send operation\n\tat reactor.netty.NettyOutbound.send(NettyOutbound.java:10)",
                "client_disconnect",
            ),
            (
                "[ERROR] System.Web.Mvc.HttpAntiForgeryException: 缺少必需的防伪表单字段“__RequestVerificationToken”\n\tat System.Web.Helpers.AntiForgery.Validate()",
                "client_invalid_request",
            ),
            (
                "[ERROR] org.springframework.web.HttpRequestMethodNotSupportedException: Request method 'POST' not supported\n\tat org.springframework.web.servlet.DispatcherServlet.doDispatch(DispatcherServlet.java:10)",
                "client_invalid_request",
            ),
        ]
        for line, expected_cat in cases:
            is_noise, rule = classify_line(line)
            assert is_noise is True, f"Expected noise for: {line[:60]}"
            assert rule["category"] == expected_cat

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
