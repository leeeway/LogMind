"""Pure, bounded parsing for embedded .NET events (never evaluates log content)."""

import json
import re
from dataclasses import dataclass

_EXCEPTION = re.compile(r"\b((?:[\w]+\.)*[A-Z]\w*(?:Exception|Error|Fault|Throwable))\b")
_FRAME = re.compile(r"\bat\s+([\w.$+`<>\[\],]+)\s*\(")
_PREFIX = re.compile(r"^.*?\]\s+(?:ERROR|WARN|INFO|DEBUG|FATAL)\s+[^\n]*?\s+-\s+")
_EXPECTED = {
    "ArgumentException",
    "ArgumentNullException",
    "ValidationException",
    "OperationCanceledException",
    "TaskCanceledException",
}


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
        return bool(
            self.is_dotnet
            and (self.method or self.frames)
            and any(e.rsplit(".", 1)[-1] not in _EXPECTED for e in self.exceptions)
            and (
                self.method or any(e.startswith(("System.", "Microsoft.")) for e in self.exceptions)
            )
        )


def parse_dotnet(message: str) -> DotnetEvent:
    text = message if isinstance(message, str) else ""
    payload = None
    # A log prefix may precede a JSON object; decoding tolerates trailing text.
    start = text.find("{")
    if start >= 0:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
            if isinstance(obj, dict) and isinstance(obj.get("content"), str):
                payload = obj
        except (ValueError, RecursionError):
            pass
    level = str(payload.get("level", "")).upper() if payload else ""
    method = str(payload.get("methodName") or "") if payload else ""
    content = payload["content"] if payload else _PREFIX.sub("", text, count=1)
    frames = tuple(dict.fromkeys(_FRAME.findall(content)))
    if not method:
        method = next(
            (f for f in frames if not f.startswith(("System.", "Microsoft.", "Newtonsoft."))), ""
        )
    exceptions = tuple(dict.fromkeys(_EXCEPTION.findall(content)))
    rendered = f"{method} - {content}" if payload and method else content
    is_dotnet = bool(
        (payload and payload.get("methodName"))
        or any(e.startswith(("System.", "Microsoft.")) for e in exceptions)
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
