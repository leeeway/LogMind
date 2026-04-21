"""
Global Error Signal Registry — Content-Aware Error Detection

Provides a curated set of high-confidence failure signal phrases that
indicate real errors regardless of the declared log level (gy.filetype,
log.level, etc.).

These signals are used in two places:
  1. ES queries (Channel B):  match_phrase clauses appended to the
     severity filter's bool.should list, so logs containing these
     phrases are fetched even from debug.log / info.log files.
  2. Quality Filter rescue:  _has_real_error_indicator() uses these
     signals to prevent filtering out DEBUG/INFO logs that contain
     genuine fault information.

Design principles:
  - Phrases are optimised for ES match_phrase (exact substring match).
  - Only high-confidence signals — avoids false-positives from normal
    business JSON like {"error": ""} or {"errorMessage": "成功"}.
  - Language-agnostic: covers both English infra errors and Chinese
    business failure patterns.
  - Zero per-business-line configuration required.
"""

# ── Infrastructure fault signals ─────────────────────────
# Network, I/O, timeout, resource exhaustion — language-agnostic.
INFRA_SIGNALS: list[str] = [
    # Timeout variants
    "connect timed out",
    "connection timed out",
    "read timed out",
    "socket timeout",
    "SocketTimeoutException",
    "ConnectTimeoutException",
    "TimeoutException",
    # Connection failures
    "connection refused",
    "Connection refused",
    "connection reset",
    "Connection reset",
    "No route to host",
    "broken pipe",
    "Broken pipe",
    # Resource exhaustion
    "OutOfMemoryError",
    "out of memory",
    "Cannot allocate memory",
    "Too many open files",
    "pool exhausted",
    "thread pool rejected",
    # DNS / network
    "UnknownHostException",
    "Name or service not known",
    "Temporary failure in name resolution",
]

# ── Business failure signals (Chinese) ───────────────────
# Common Chinese error phrases used in GYYX Java/C# services.
# These indicate real business-level failures regardless of log level.
BUSINESS_FAILURE_SIGNALS: list[str] = [
    "请求失败",
    "操作失败",
    "调用失败",
    "处理失败",
    "通知失败",
    "发送失败",
    "同步失败",
    "执行失败",
    "连接超时",
    "响应超时",
    "服务不可用",
    "服务异常",
    "系统异常",
    "产生异常",
]

# ── Error code signals ───────────────────────────────────
# Negative error codes and common failure indicators in structured logs.
# ES match_phrase on "errorCode=-" will match errorCode=-1000, -999, etc.
ERROR_CODE_SIGNALS: list[str] = [
    "errorCode=-",
    "error_code=-",
    "resultCode=-",
    "errCode=-",
]

# ── Exception class signals ──────────────────────────────
# High-confidence exception markers that transcend log level.
# Note: "Exception" alone is already matched by the existing severity
# filter's Channel A, but including Caused by / Traceback here ensures
# Channel B also catches them in non-error filetypes.
EXCEPTION_SIGNALS: list[str] = [
    "Caused by:",
    "Traceback (most recent",
    "NullPointerException",
    "NullReferenceException",
    "StackOverflowError",
    "ClassNotFoundException",
    "NoSuchMethodError",
    "IllegalStateException",
    "IllegalArgumentException",
    "ConcurrentModificationException",
    "DataIntegrityViolationException",
    "DeadlockLoserDataAccessException",
    "SQLServerException",
    "SQLException",
    "连接被拒",
]

# ── Aggregate: all signals for ES query Channel B ────────
ALL_ERROR_SIGNALS: list[str] = (
    INFRA_SIGNALS
    + BUSINESS_FAILURE_SIGNALS
    + ERROR_CODE_SIGNALS
    + EXCEPTION_SIGNALS
)
