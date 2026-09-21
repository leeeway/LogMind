"""Conservative, language-independent event identity and evidence metadata."""

import hashlib
import re
from dataclasses import asdict, dataclass

from logmind.domain.log.csharp import parse_dotnet

NORMALIZATION_VERSION = "event-v3"
_TIMESTAMP = re.compile(
    r"^\[?\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\]?\s*"
)
_THREAD = re.compile(
    r"^(?:\[(?:\d+|(?:http|https|pool|Thread|thread|nio|grpc|worker)[\w.:/-]*)\]\s*)+"
)
_CONTEXT = re.compile(r"^\[[^\]\n]*\b(?:request_?id|trace_?id|span_?id)=[^\]\n]*\]\s*", re.I)
_VOLATILE = re.compile(
    r"""(?i)(?<![\w])(["']?(?:request_?id|trace_?id|span_?id|correlation_?id|user_?id|merchant_?id|order_?id|task_?id|logTime|timestamp)["']?\s*[:=]\s*)"""
    r"""(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;\]}]+)"""
)
_ENVELOPE = re.compile(
    r"^\[[^\]\n]+\]\s*\[(?:ERROR|WARN|WARNING|FATAL|CRITICAL|UNKNOWN|INFO|DEBUG)\](?:\s*\[(?:domain:|host:|branch:|occurrences:)[^\]]*\])*\s*"
)


def normalize_message(message: str) -> str:
    from logmind.domain.analysis.sensitive_masker import mask_sensitive

    text = _ENVELOPE.sub("", message)
    text = _TIMESTAMP.sub("", text)
    text = _THREAD.sub("", text)
    text = _CONTEXT.sub("", text)
    text = parse_dotnet(text).message
    # Normalize identifiers before masking, so partial PII masks cannot alter
    # identity. Never normalize unlabelled numbers/status codes/SQL codes.
    text = _VOLATILE.sub(lambda m: m.group(1) + "<id>", text)
    text = re.sub(r"\bgoroutine\s+\d+", "goroutine <id>", text)
    text = re.sub(r"(\.(?:go|cs|java|py)):\d+\b", r"\1:<line>", text)
    text = re.sub(r'(File\s+"[^"\n]+",\s+line)\s+\d+', r"\1 <line>", text)
    text = mask_sensitive(text)
    # Preserve full stack and trailing error details. Whitespace only is benign.
    return re.sub(r"[ \t]+", " ", text).strip()


def event_key(message: str) -> str:
    return hashlib.sha256(normalize_message(message).encode()).hexdigest() if message else ""


def metadata(source: dict) -> dict:
    def obj(key):
        value = source.get(key)
        return value if isinstance(value, dict) else {}

    gy, image, host, agent = (obj(k) for k in ("gy", "image", "host", "agent"))
    k8s_pod = obj("kubernetes").get("pod")
    pod = gy.get("podname") or (k8s_pod.get("name", "") if isinstance(k8s_pod, dict) else "")
    version = image.get("version") or ""
    version_source = "image.version" if version else ""
    suffix = re.search(r"_(v?\d+(?:\.\d+)+(?:[-+][\w.-]+)?)$", str(pod))
    if not version and suffix:
        version, version_source = suffix.group(1), "gy.podname"
    return {
        "domain": gy.get("domain", ""),
        "branch": gy.get("branch", ""),
        "filetype": gy.get("filetype", ""),
        "pod_name": pod,
        "image_version": version,
        "version_source": version_source,
        "host_name": host.get("name") or agent.get("name", ""),
        "host_source": "host.name"
        if host.get("name")
        else "agent.name"
        if agent.get("name")
        else "",
    }


@dataclass
class EventEvidence:
    key: str
    level: str
    exceptions: list[str]
    methods: list[str]
    status_codes: list[str]
    error_codes: list[str]
    language: str | None
    source: dict


def evidence(source: dict, language: str | None = None) -> dict:
    from logmind.domain.log.service import LogService

    message = LogService._extract_message(source)
    event = parse_dotnet(message)
    return asdict(
        EventEvidence(
            key=event_key(message),
            level=LogService._extract_level(source),
            exceptions=list(event.exceptions)[:20],
            methods=list(event.frames)[:20] or ([event.method] if event.method else []),
            status_codes=re.findall(
                r"(?i)\b(?:status|status_code|http_status)\s*[:=]\s*(\d{3})", message
            ),
            error_codes=re.findall(
                r"(?i)\b(?:sqlstate|error_?code|errno)\s*[:=]\s*([\w-]+)", message
            ),
            language=language,
            source=metadata(source),
        )
    )
