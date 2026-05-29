"""
Business Noise Recognition — Intelligent Non-Fault Log Classification

Provides a multi-layer noise pattern registry for identifying log messages
that are ERROR-level but represent normal business flow (not real faults).

Three layers:
  Layer 1: Static patterns — hand-curated, always available, immediate effect
  Layer 2: Per-BusinessLine custom patterns — stored in DB, operator-configurable
  Layer 3: AI-learned patterns — stored in ES, auto-populated from AI analysis

Examples of business noise:
  - SMS channel unavailable (业务限制, not system fault)
  - User login password error (user-side input error)
  - Verification code expired (normal business validation)
  - Account lockout after N failures (rate limiting, not fault)
  - Insufficient balance / inventory (business validation)
  - Duplicate submission (idempotency check)

Protection against false positives:
  Noise rules are NOT applied when the log also contains:
  - Java/C# exception stack traces (at ..., Caused by:, etc.)
  - Infrastructure fault signals (timeout, connection refused, OOM, etc.)
  - HTTP 5xx status codes
  - Database / middleware error indicators
"""

import hashlib
import json
import re
import time

from logmind.core.logging import get_logger

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════
#  Layer 1: Static Noise Patterns (hand-curated)
# ══════════════════════════════════════════════════════════

# Each rule has:
#   pattern: substring or regex to match in the log message
#   category: classification group for reporting
#   reason: human-readable explanation (used in digest/UI)
#   is_regex: whether pattern should be compiled as regex (default: False = substring)

STATIC_NOISE_PATTERNS: list[dict] = [
    # ── 短信服务流程日志 ──────────────────────────────────
    {"pattern": "暂无发送渠道", "category": "sms_flow", "reason": "短信渠道不可用属于业务限制，非系统故障"},
    {"pattern": "插入队列失败", "category": "sms_flow", "reason": "短信队列操作结果属于流程日志"},
    {"pattern": "短信发送失败", "category": "sms_flow", "reason": "短信发送结果日志"},
    {"pattern": "短信发送频率限制", "category": "sms_flow", "reason": "发送频率限制属于正常防护"},
    {"pattern": "短信验证码发送过于频繁", "category": "sms_flow", "reason": "频率限制属于正常防护"},
    {"pattern": "SendSmsService", "category": "sms_flow", "reason": "短信服务流程日志",
     "requires_additional": ["发送失败", "队列失败", "发送渠道"]},

    # ── 登录认证/账号安全 ──────────────────────────────────
    {"pattern": "账号或密码错误", "category": "auth_flow", "reason": "用户输入错误属于正常业务"},
    {"pattern": "密码错误", "category": "auth_flow", "reason": "登录失败属于正常业务"},
    {"pattern": "用户名或密码错误", "category": "auth_flow", "reason": "登录失败属于正常业务"},
    {"pattern": "账号不存在", "category": "auth_flow", "reason": "账号查询结果属于正常业务"},
    {"pattern": "用户不存在", "category": "auth_flow", "reason": "用户查询结果属于正常业务"},
    {"pattern": "连续失败", "category": "auth_flow", "reason": "连续登录失败计数属于安全防护流程"},
    {"pattern": "账号将被限制登录", "category": "auth_flow", "reason": "账号锁定告警属于安全防护"},
    {"pattern": "账号已被锁定", "category": "auth_flow", "reason": "账号锁定状态属于安全防护"},
    {"pattern": "账号已被冻结", "category": "auth_flow", "reason": "账号冻结状态属于安全防护"},
    {"pattern": "登录已过期", "category": "auth_flow", "reason": "会话过期属于正常业务"},
    {"pattern": "token已过期", "category": "auth_flow", "reason": "Token 过期属于正常业务"},
    {"pattern": "Token已过期", "category": "auth_flow", "reason": "Token 过期属于正常业务"},
    {"pattern": "token无效", "category": "auth_flow", "reason": "Token 无效属于正常业务"},
    {"pattern": "Token无效", "category": "auth_flow", "reason": "Token 无效属于正常业务"},
    {"pattern": "未登录", "category": "auth_flow", "reason": "未登录访问属于正常业务"},
    {"pattern": "登录状态失效", "category": "auth_flow", "reason": "登录态失效属于正常业务"},
    {"pattern": "statusError", "category": "auth_flow", "reason": "业务状态错误码属于正常业务响应",
     "requires_additional": ["密码错误", "账号", "登录"]},

    # ── 验证码相关 ────────────────────────────────────────
    {"pattern": "验证码错误", "category": "captcha_flow", "reason": "验证码校验失败属于正常业务"},
    {"pattern": "验证码已过期", "category": "captcha_flow", "reason": "验证码过期属于正常业务"},
    {"pattern": "验证码不正确", "category": "captcha_flow", "reason": "验证码校验失败属于正常业务"},
    {"pattern": "验证码已失效", "category": "captcha_flow", "reason": "验证码失效属于正常业务"},
    {"pattern": "图形验证码错误", "category": "captcha_flow", "reason": "图形验证码校验失败属于正常业务"},

    # ── 业务校验/参数校验 ──────────────────────────────────
    {"pattern": "余额不足", "category": "biz_validation", "reason": "余额校验属于正常业务逻辑"},
    {"pattern": "库存不足", "category": "biz_validation", "reason": "库存校验属于正常业务逻辑"},
    {"pattern": "重复提交", "category": "biz_validation", "reason": "幂等校验属于正常防护"},
    {"pattern": "重复请求", "category": "biz_validation", "reason": "幂等校验属于正常防护"},
    {"pattern": "重复操作", "category": "biz_validation", "reason": "幂等校验属于正常防护"},
    {"pattern": "订单已存在", "category": "biz_validation", "reason": "订单幂等校验属于正常业务"},
    {"pattern": "参数不合法", "category": "biz_validation", "reason": "参数校验属于正常业务逻辑"},
    {"pattern": "参数错误", "category": "biz_validation", "reason": "参数校验属于正常业务逻辑"},
    {"pattern": "参数不能为空", "category": "biz_validation", "reason": "参数校验属于正常业务逻辑"},
    {"pattern": "请求参数异常", "category": "biz_validation", "reason": "参数校验属于正常业务逻辑"},
    {"pattern": "数据不存在", "category": "biz_validation", "reason": "数据查询为空属于正常业务"},
    {"pattern": "记录不存在", "category": "biz_validation", "reason": "数据查询为空属于正常业务"},
    {"pattern": "没有找到", "category": "biz_validation", "reason": "查询为空属于正常业务"},
    {"pattern": "无权限", "category": "biz_validation", "reason": "权限校验属于正常业务逻辑"},
    {"pattern": "权限不足", "category": "biz_validation", "reason": "权限校验属于正常业务逻辑"},
    {"pattern": "操作频繁", "category": "biz_validation", "reason": "频率限制属于正常防护"},
    {"pattern": "请求过于频繁", "category": "biz_validation", "reason": "频率限制属于正常防护"},
    {"pattern": "请稍后再试", "category": "biz_validation", "reason": "限流降级属于正常防护"},

    # ── 支付/交易流程日志 ──────────────────────────────────
    {"pattern": "支付失败", "category": "payment_flow", "reason": "支付失败属于业务流程日志",
     "requires_additional_absent": ["Exception", "Caused by", "StackTrace"]},
    {"pattern": "交易失败", "category": "payment_flow", "reason": "交易失败属于业务流程日志",
     "requires_additional_absent": ["Exception", "Caused by", "StackTrace"]},
    {"pattern": "退款失败", "category": "payment_flow", "reason": "退款结果属于业务流程日志",
     "requires_additional_absent": ["Exception", "Caused by", "StackTrace"]},

    # ── 游戏业务特有 ────────────────────────────────────
    {"pattern": "角色不存在", "category": "game_flow", "reason": "角色查询结果属于正常业务"},
    {"pattern": "服务器维护中", "category": "game_flow", "reason": "服务器维护状态属于正常运维"},
    {"pattern": "排队中", "category": "game_flow", "reason": "排队状态属于正常业务"},
    {"pattern": "游戏服务器未开放", "category": "game_flow", "reason": "服务器状态属于正常业务"},
    {"pattern": "该区服暂未开放", "category": "game_flow", "reason": "区服状态属于正常业务"},
    {"pattern": "CDKey已使用", "category": "game_flow", "reason": "CDKey 校验属于正常业务"},
    {"pattern": "CDKey无效", "category": "game_flow", "reason": "CDKey 校验属于正常业务"},
    {"pattern": "道具不足", "category": "game_flow", "reason": "道具校验属于正常业务逻辑"},
    {"pattern": "充值金额不正确", "category": "game_flow", "reason": "金额校验属于正常业务"},

    # ── 成功业务流程日志 ──────────────────────────────────
    {"pattern": "Account successfully charged", "category": "success_flow",
     "reason": "游戏充值/兑换成功结果属于正常业务流程日志"},
    {"pattern": r"\berrorCode\s*=\s*0\b", "category": "success_flow", "is_regex": True,
     "reason": "errorCode=0 表示业务调用成功",
     "requires_additional": ["successfully", "成功", "兑换", "发元宝", "charged"]},
    {"pattern": r"\bsuccess\s*=\s*true\b", "category": "success_flow", "is_regex": True,
     "reason": "success=true 表示业务调用成功",
     "requires_additional": ["成功", "兑换", "发元宝", "ResultBean"]},
    {"pattern": "游戏兑换结果", "category": "success_flow",
     "reason": "游戏兑换成功结果属于正常业务流程日志",
     "requires_additional": ["success=true", "data=成功"]},
    {"pattern": "调用游戏接口发元宝", "category": "success_flow",
     "reason": "发元宝成功回执属于正常业务流程日志",
     "requires_additional": ["success=true", "data=成功"]},

    # ── 通用业务响应 JSON 模式 ──────────────────────────────
    # 匹配 {"success":false, "message":"xxx"} 形式的业务响应日志
    {"pattern": r'"success"\s*:\s*false', "category": "biz_response", "is_regex": True,
     "reason": "业务接口返回失败属于业务流程日志",
     "requires_additional": ["密码", "验证", "校验", "不存在", "已过期", "已失效",
                             "不合法", "重复", "余额", "权限", "频繁", "锁定"]},
]


# ── Real fault indicators (protection against false positives) ──
# If a log line matches ANY of these, noise rules will NOT be applied,
# even if the log also matches a noise pattern.
_FAULT_PROTECTION_PATTERNS = [
    # Stack traces — definitive indicator of real exceptions
    re.compile(r"\bat\s+[\w.$]+\([\w.]+:\d+\)"),                # Java: at com.Foo(Bar.java:42)
    re.compile(r"\bat\s+[\w.]+\s+in\s+\S+:line\s+\d+"),         # C#: at Foo.Bar() in File.cs:line 12
    re.compile(r"Caused by:\s*[\w.]+"),                          # Java chained exceptions
    re.compile(r"--- End of (?:inner )?exception"),              # C# inner exception
    re.compile(r"Traceback \(most recent"),                      # Python traceback
    re.compile(r"^\s+at\s+[\w.$]+\(", re.MULTILINE),            # Stack frame lines

    # Infrastructure fault keywords
    re.compile(r"\b(?:OutOfMemory(?:Error)?|StackOverflow(?:Error)?|Deadlock|OOM)\b", re.IGNORECASE),
    re.compile(r"\b(?:connect(?:ion)?\s+(?:refused|reset|timed?\s*out))\b", re.IGNORECASE),
    re.compile(r"\b(?:Socket(?:Timeout)?Exception|IOException|SQLException)\b"),
    re.compile(r"\b(?:NullPointerException|NullReferenceException)\b"),
    re.compile(r"\b(?:ClassNotFoundException|NoSuchMethod(?:Error|Exception))\b"),
    re.compile(r"\b(?:DataIntegrity|Deadlock|SQLServer)(?:Exception|Error)\b"),

    # HTTP 5xx — server-side errors are real faults
    re.compile(r"\b5\d{2}\s+(?:Internal|Bad Gateway|Service Unavailable|Gateway Timeout)\b"),
    re.compile(r'"(?:status|statusCode|code)"\s*:\s*5\d{2}\b'),
    re.compile(r"\bHTTP/\d\.\d\"\s+5\d{2}\b"),

    # Database / middleware errors
    re.compile(r"\b(?:SQLSTATE|Duplicate entry|Lock wait timeout)\b", re.IGNORECASE),
    re.compile(r"\b(?:Redis(?:Connection)?Exception|JedisException)\b"),
    re.compile(r"\b(?:connection\s+pool\s+exhausted|pool\s+exhausted)\b", re.IGNORECASE),
    re.compile(r"\b(?:Broken pipe|No route to host|UnknownHost)\b", re.IGNORECASE),

    # Panic / fatal signals
    re.compile(r"\b(?:panic|FATAL|core\s+dump|segfault|SIGKILL|SIGABRT)\b"),
]

# Pre-compile static regex patterns
_COMPILED_STATIC: list[tuple[dict, re.Pattern | None]] = []


def _compile_static_patterns():
    """Pre-compile regex patterns from static rules at module load time."""
    global _COMPILED_STATIC
    if _COMPILED_STATIC:
        return
    for rule in STATIC_NOISE_PATTERNS:
        if rule.get("is_regex"):
            compiled = re.compile(rule["pattern"], re.IGNORECASE)
        else:
            compiled = None  # Will use simple substring matching
        _COMPILED_STATIC.append((rule, compiled))


_compile_static_patterns()


# ══════════════════════════════════════════════════════════
#  Layer 3: AI-Learned Noise Rules (ES-backed)
# ══════════════════════════════════════════════════════════

_NOISE_RULES_INDEX = "logmind-noise-rules"

# In-memory cache for learned noise rules
_learned_noise_cache: list[dict] = []
_learned_noise_cache_ts: float = 0.0
_CACHE_TTL = 300  # 5 minutes


async def _ensure_noise_rules_index():
    """Create the noise-rules ES index if it doesn't exist."""
    from logmind.domain.log.service import log_service

    es = log_service.es
    exists = await es.indices.exists(index=_NOISE_RULES_INDEX)
    if not exists:
        await es.indices.create(
            index=_NOISE_RULES_INDEX,
            mappings={
                "properties": {
                    "pattern": {"type": "keyword"},
                    "category": {"type": "keyword"},
                    "reason": {"type": "text"},
                    "business_line_id": {"type": "keyword"},
                    "source_task_id": {"type": "keyword"},
                    "confidence": {"type": "float"},
                    "hit_count": {"type": "integer"},
                    "first_seen": {"type": "date"},
                    "last_seen": {"type": "date"},
                    "created_at": {"type": "date"},
                },
            },
        )
        logger.info("noise_rules_index_created")


async def store_learned_noise_rule(
    pattern: str,
    business_line_id: str,
    category: str = "ai_learned",
    reason: str = "",
    source_task_id: str = "",
    confidence: float = 0.7,
):
    """
    Upsert an AI-learned noise rule into ES.

    Uses MD5(business_line_id + pattern) as doc ID for idempotent upsert.
    Subsequent stores increment hit_count and update confidence upward.
    """
    from datetime import datetime, timezone

    from logmind.domain.log.service import log_service

    if not pattern or len(pattern) < 5:
        return

    try:
        await _ensure_noise_rules_index()
        es = log_service.es

        now_iso = datetime.now(timezone.utc).isoformat()
        doc_id = hashlib.md5(f"{business_line_id}:{pattern}".encode()).hexdigest()

        await es.update(
            index=_NOISE_RULES_INDEX,
            id=doc_id,
            body={
                "script": {
                    "source": """
                        ctx._source.hit_count += 1;
                        ctx._source.last_seen = params.now;
                        if (ctx._source.confidence < params.confidence) {
                            ctx._source.confidence = params.confidence;
                        }
                    """,
                    "params": {"now": now_iso, "confidence": confidence},
                },
                "upsert": {
                    "pattern": pattern,
                    "category": category,
                    "reason": reason,
                    "business_line_id": business_line_id,
                    "source_task_id": source_task_id,
                    "confidence": confidence,
                    "hit_count": 1,
                    "first_seen": now_iso,
                    "last_seen": now_iso,
                    "created_at": now_iso,
                },
            },
        )
        logger.info("noise_rule_stored", pattern=pattern[:60], doc_id=doc_id[:8])

    except Exception as e:
        logger.warning("noise_rule_store_failed", pattern=pattern[:60], error=str(e))


async def load_learned_noise_rules(business_line_id: str | None = None) -> list[dict]:
    """
    Load AI-learned noise rules from ES with in-memory cache (5-min TTL).

    Quality gate: only rules with confidence >= 0.7 are loaded.
    """
    global _learned_noise_cache, _learned_noise_cache_ts

    now = time.monotonic()
    if (now - _learned_noise_cache_ts) < _CACHE_TTL:
        if business_line_id:
            return [r for r in _learned_noise_cache
                    if r.get("business_line_id") == business_line_id or not r.get("business_line_id")]
        return _learned_noise_cache

    try:
        from logmind.domain.log.service import log_service

        es = log_service.es

        try:
            exists = await es.indices.exists(index=_NOISE_RULES_INDEX)
        except Exception:
            _learned_noise_cache = []
            _learned_noise_cache_ts = now
            return []

        if not exists:
            _learned_noise_cache = []
            _learned_noise_cache_ts = now
            return []

        result = await es.search(
            index=_NOISE_RULES_INDEX,
            body={
                "query": {
                    "bool": {
                        "filter": [
                            {"range": {"confidence": {"gte": 0.7}}},
                        ],
                    }
                },
                "size": 200,
                "sort": [{"hit_count": {"order": "desc"}}],
                "_source": ["pattern", "category", "reason", "business_line_id"],
            },
        )

        rules = [
            hit["_source"]
            for hit in result["hits"]["hits"]
            if hit["_source"].get("pattern")
        ]

        _learned_noise_cache = rules
        _learned_noise_cache_ts = now

        if rules:
            logger.info("learned_noise_rules_loaded", count=len(rules))

        if business_line_id:
            return [r for r in rules
                    if r.get("business_line_id") == business_line_id or not r.get("business_line_id")]
        return rules

    except Exception as e:
        logger.warning("learned_noise_rules_load_failed", error=str(e)[:100])
        _learned_noise_cache_ts = now
        return _learned_noise_cache


async def downgrade_noise_rule(source_task_id: str):
    """
    Downgrade noise rules from a negatively-reviewed analysis (false positive).

    Called when an operator marks a noise classification as incorrect.
    Halves confidence; deletes rules that drop below 0.3.
    """
    from logmind.domain.log.service import log_service

    try:
        es = log_service.es
        exists = await es.indices.exists(index=_NOISE_RULES_INDEX)
        if not exists:
            return

        result = await es.search(
            index=_NOISE_RULES_INDEX,
            body={
                "query": {"term": {"source_task_id": source_task_id}},
                "size": 50,
                "_source": ["pattern", "confidence"],
            },
        )

        hits = result.get("hits", {}).get("hits", [])
        if not hits:
            return

        for hit in hits:
            doc_id = hit["_id"]
            conf = hit["_source"].get("confidence", 0.8)
            new_conf = conf * 0.5

            if new_conf < 0.3:
                await es.delete(index=_NOISE_RULES_INDEX, id=doc_id, ignore=[404])
            else:
                await es.update(
                    index=_NOISE_RULES_INDEX,
                    id=doc_id,
                    body={"doc": {"confidence": new_conf}},
                )

        invalidate_noise_cache()
        logger.info("noise_rules_downgraded", task_id=source_task_id, count=len(hits))

    except Exception as e:
        logger.warning("noise_rules_downgrade_failed", error=str(e))


def invalidate_noise_cache():
    """Force refresh of the learned noise rules cache on next query."""
    global _learned_noise_cache_ts
    _learned_noise_cache_ts = 0.0


# ══════════════════════════════════════════════════════════
#  Core Matching Engine
# ══════════════════════════════════════════════════════════

def has_fault_protection(line: str) -> bool:
    """
    Check if a log line contains real fault indicators.

    Returns True if the line should be PROTECTED from noise filtering
    (i.e., it's a real fault even if it contains noise keywords).

    This is the critical safety net that prevents false noise classification
    of genuine exceptions/infrastructure faults.
    """
    for pattern in _FAULT_PROTECTION_PATTERNS:
        if pattern.search(line):
            return True
    return False


def match_static_noise(line: str) -> dict | None:
    """
    Check a log line against static noise patterns.

    Returns the matched rule dict if found, None otherwise.
    Rules with `requires_additional` only match if at least one of the
    additional keywords is also present in the line.
    Rules with `requires_additional_absent` only match if NONE of those
    keywords are present.
    """
    for rule, compiled_re in _COMPILED_STATIC:
        if compiled_re:
            # Regex pattern
            if not compiled_re.search(line):
                continue
        else:
            # Substring match
            if rule["pattern"] not in line:
                continue

        # Check additional requirements
        req_add = rule.get("requires_additional")
        if req_add:
            if not any(kw in line for kw in req_add):
                continue

        req_absent = rule.get("requires_additional_absent")
        if req_absent:
            if any(kw in line for kw in req_absent):
                continue

        return rule

    return None


def match_custom_noise(line: str, custom_patterns: list[dict]) -> dict | None:
    """
    Check a log line against per-BusinessLine custom noise patterns.

    custom_patterns format: [{"pattern": "...", "category": "...", "reason": "..."}]
    """
    for rule in custom_patterns:
        pattern = rule.get("pattern", "")
        if not pattern:
            continue
        if pattern in line:
            return rule
    return None


def match_learned_noise(line: str, learned_rules: list[dict]) -> dict | None:
    """
    Check a log line against AI-learned noise rules from ES.
    """
    for rule in learned_rules:
        pattern = rule.get("pattern", "")
        if not pattern:
            continue
        if pattern in line:
            return rule
    return None


def classify_line(
    line: str,
    custom_patterns: list[dict] | None = None,
    learned_rules: list[dict] | None = None,
) -> tuple[bool, dict | None]:
    """
    Classify a single log line as noise or not.

    Returns: (is_noise: bool, matched_rule: dict | None)

    Priority:
      1. Fault protection check → if line has real fault indicators, always non-noise
      2. Static noise patterns → most common, hand-curated
      3. Custom per-business-line patterns → operator-defined
      4. AI-learned rules → auto-discovered by AI

    This function is the core classifier used by BusinessNoiseFilterStage.
    """
    # Safety first: never filter lines with real fault indicators
    if has_fault_protection(line):
        return False, None

    # Layer 1: Static patterns
    match = match_static_noise(line)
    if match:
        return True, match

    # Layer 2: Custom per-business-line patterns
    if custom_patterns:
        match = match_custom_noise(line, custom_patterns)
        if match:
            return True, match

    # Layer 3: AI-learned rules
    if learned_rules:
        match = match_learned_noise(line, learned_rules)
        if match:
            return True, match

    return False, None
