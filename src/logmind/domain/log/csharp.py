"""Pure, bounded parsing for embedded .NET events (never evaluates log content)."""

import json
import re
from dataclasses import dataclass

_EXCEPTION = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"((?:[A-Za-z_][A-Za-z0-9_]*\.)+"
    r"(?:Exception|Error|Fault|Throwable|[A-Z][A-Za-z0-9_+]*?(?:Exception|Error|Fault|Throwable))"
    r"|[A-Z][A-Za-z0-9_+]+(?:Exception|Error|Fault|Throwable))"
    r"(?![A-Za-z0-9_])"
)
_FRAME = re.compile(r"\bat\s+([\w.$+`<>\[\],]+)\s*\(")
_PREFIX = re.compile(
    r"^.*?\]\s+(ERROR|WARN|WARNING|INFO|DEBUG|FATAL|CRITICAL)\s+([^\n]*?)\s+-\s+",
    re.IGNORECASE,
)
_EXPECTED = {
    "ArgumentException",
    "ValidationException",
    "OperationCanceledException",
    "TaskCanceledException",
}
_NON_EXCEPTION_SUFFIXES = (
    "OnException",
    "OnError",
    "RaiseOnError",
    "HandleException",
    "HandleError",
)
_NON_EXCEPTION_NAMES = {
    "ParameterError",
    "SignError",
    "PassWordError",
    "PasswordError",
    "AccountError",
    "UserError",
    "CaptchaError",
    "CodeError",
    "TokenError",
}
_DOTNET_PREFIXES = (
    "System.",
    "Microsoft.",
    "Newtonsoft.",
    "Gyyx.",
    "BeIT.",
    "ZooKeeperNet.",
    "log4net.",
)


def _is_valid_exception_token(token: str) -> bool:
    leaf = token.rsplit(".", 1)[-1].rsplit("+", 1)[-1]
    if leaf in _NON_EXCEPTION_NAMES:
        return False
    if leaf.startswith(_NON_EXCEPTION_SUFFIXES):
        return False
    return True


@dataclass(frozen=True)
class DotnetEvent:
    message: str
    level: str = ""
    method: str = ""
    exceptions: tuple[str, ...] = ()
    frames: tuple[str, ...] = ()
    is_dotnet: bool = False

    @property
    def concrete_fault(self) -> bool:
        if self.level in {"DEBUG", "TRACE"} and not self.frames:
            return False
        return bool(
            self.is_dotnet
            and (self.method or self.frames)
            and any(
                e.rsplit(".", 1)[-1].rsplit("+", 1)[-1] not in _EXPECTED
                for e in self.exceptions
            )
            and (
                self.method
                or any(e.startswith(_DOTNET_PREFIXES) for e in self.exceptions)
            )
        )


def parse_dotnet(message: str) -> DotnetEvent:
    text = message if isinstance(message, str) else ""
    payload = None
    prefix_match = _PREFIX.match(text)
    header_level = prefix_match.group(1).upper() if prefix_match else ""
    header_logger = prefix_match.group(2).strip() if prefix_match else ""
    # A log prefix may precede a JSON object; decoding tolerates trailing text.
    start = text.find("{")
    if start >= 0:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
            if isinstance(obj, dict) and isinstance(obj.get("content"), str):
                payload = obj
        except (ValueError, RecursionError):
            pass
    level = str(payload.get("level", "")).upper() if payload else header_level
    method = str(payload.get("methodName") or "") if payload else ""
    content = payload["content"] if payload else _PREFIX.sub("", text, count=1)
    frames = tuple(dict.fromkeys(_FRAME.findall(content)))
    if not method:
        method = next(
            (f for f in frames if not f.startswith(("System.", "Microsoft.", "Newtonsoft."))), ""
        )
    if not method and header_logger and header_logger != "LogModule" and "." in header_logger:
        method = header_logger
    found_exceptions = [
        e for e in _EXCEPTION.findall(content) if _is_valid_exception_token(e)
    ]
    if not found_exceptions and level in {"ERROR", "FATAL", "CRITICAL"}:
        if "超时时间已到" in content or "在操作完成之前超时时间已过或服务器未响应" in content:
            found_exceptions.append("System.Data.SqlClient.SqlException")
        elif header_logger.startswith("Gyyx.") and "执行错误:" in content:
            found_exceptions.append("System.Data.SqlClient.SqlException")
    exceptions = tuple(dict.fromkeys(found_exceptions))
    rendered = f"{method} - {content}" if payload and method else content
    is_dotnet = bool(
        (payload and payload.get("methodName"))
        or any(e.startswith(_DOTNET_PREFIXES) for e in exceptions)
        or any(f.startswith(_DOTNET_PREFIXES) for f in frames)
        or (header_logger and (header_logger == "LogModule" or header_logger.startswith(_DOTNET_PREFIXES) or header_logger.endswith("OnException")))
        or re.search(r"\.cs:line\s+\d+", content)
    )
    return DotnetEvent(rendered, level, method, exceptions, frames, is_dotnet)


def normalized_fault(message: str) -> str:
    """Remove volatile values without discarding exception/route identity."""
    from logmind.domain.analysis.sensitive_masker import mask_sensitive

    message = re.sub(
        r"^\[[^\]]*\]\s*\[(?:ERROR|WARN|FATAL|UNKNOWN)\](?:\s*\[[^\]]*\])*\s*", "", message
    )
    event = parse_dotnet(message)
    text = mask_sensitive(event.message)
    text = re.sub(r"^\[[^\]]*\]\s*\[(?:ERROR|WARN|FATAL|UNKNOWN)\](?:\s*\[[^\]]*\])*\s*", "", text)
    text = re.sub(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}\b", "{uuid}", text)
    text = re.sub(r"(?i)\b(?:request_?id|trace_?id|span_?id)\s*[:=]\s*[^\s,;]+", "id={id}", text)
    text = re.sub(r"\b\d+(?:\.\d+)*\b", "{n}", text)
    # Stack line numbers/instances must not make the same failure a new event.
    core = re.split(r"\s+at\s+", text, maxsplit=1)[0]
    method = next(
        (f for f in event.frames if not f.startswith(("System.", "Microsoft.", "Newtonsoft."))),
        event.method,
    )
    return "|".join((*event.exceptions, method, core[:1000]))
