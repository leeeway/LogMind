"""Pure models and detection rules for HTTP access-log patrol."""

from __future__ import annotations

import ipaddress
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qsl, urlsplit

_UUID_SEGMENT_RE = re.compile(
    r"(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_INTEGER_SEGMENT_RE = re.compile(r"^\d+$")
_HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z]{2,63}$",
    re.IGNORECASE,
)
_KNOWN_PROBE_PATH_RE = re.compile(
    r"(?i)(?:^|/)(?:nuclei(?:\.svg)?|\.env|\.git|wp-admin|wp-login\.php|"
    r"phpinfo\.php|vendor/phpunit|actuator/env)(?:/|$)"
)
_NGINX_STATIC_PATH_RE = re.compile(
    r"(?i)\.(?:avif|bmp|css|data|eot|gif|ico|ini|jpe?g|js|m3u8|map|mp3|"
    r"mp4|otf|pdf|svg|ts|ttf|webp|woff2?|zip)(?:/)?$"
)
_NGINX_REPOSITORY_PATH_RE = re.compile(
    r"(?i)(?:/info/refs|/git-upload-pack|/git-receive-pack)(?:/)?$"
)
_SAFE_PARAMETER_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-\[\]]{0,63}$")
_SENSITIVE_PARAMETER_NAME_RE = re.compile(
    r"(?i)(?:^|[_.\-\[])(?:authorization|cookie|password|passwd|pwd|secret|"
    r"sign(?:ature)?|token|access_token|refresh_token|api_?key)(?:$|[_.\-\]])"
)
_MAX_PARAMETER_NAMES = 12


@dataclass(slots=True)
class AccessMetric:
    """One source/site/minute aggregation."""

    source: str
    site: str
    minute: datetime
    request_count: int
    status_4xx: int = 0
    status_5xx: int = 0
    gateway_5xx: int = 0
    upstream_5xx: int = 0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0

    def to_document(self) -> dict:
        return {
            "source": self.source,
            "site": self.site,
            "minute": self.minute.isoformat(),
            "request_count": self.request_count,
            "status_4xx": self.status_4xx,
            "status_5xx": self.status_5xx,
            "gateway_5xx": self.gateway_5xx,
            "upstream_5xx": self.upstream_5xx,
            "p50_ms": round(self.p50_ms, 3),
            "p95_ms": round(self.p95_ms, 3),
            "p99_ms": round(self.p99_ms, 3),
        }


@dataclass(slots=True)
class AccessWindow:
    """Five-minute (or configured window) view for one source/site."""

    source: str
    site: str
    request_count: int = 0
    status_4xx: int = 0
    status_5xx: int = 0
    gateway_5xx: int = 0
    upstream_5xx: int = 0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0

    @property
    def rate_4xx(self) -> float:
        return self.status_4xx / self.request_count if self.request_count else 0.0

    @property
    def rate_5xx(self) -> float:
        return self.status_5xx / self.request_count if self.request_count else 0.0

    @property
    def successful_count(self) -> int:
        return max(0, self.request_count - self.status_4xx - self.status_5xx)

    @property
    def success_rate(self) -> float:
        return (
            self.successful_count / self.request_count
            if self.request_count
            else 0.0
        )


def is_rejected_traffic_window(window: AccessWindow) -> bool:
    """Identify windows dominated by scans or requests rejected at the edge."""
    return (
        window.source == "nginx"
        and window.request_count >= 100
        and window.rate_4xx >= 0.80
    )


@dataclass(slots=True)
class AccessRouteMetric:
    """Current-window aggregation for one normalized method/route."""

    source: str
    site: str
    route_key: str
    request_count: int
    status_4xx: int = 0
    status_5xx: int = 0
    p95_ms: float = 0.0
    status_counts: dict[int, int] = field(default_factory=dict)

    @property
    def rate_4xx(self) -> float:
        return self.status_4xx / self.request_count if self.request_count else 0.0


@dataclass(slots=True)
class AccessBaseline:
    """Historical per-window baseline for one source/site."""

    source: str
    site: str
    sample_count: int = 0
    request_count: float = 0.0
    rate_4xx: float = 0.0
    rate_5xx: float = 0.0
    p95_ms: float = 0.0

    @property
    def is_ready(self) -> bool:
        return self.sample_count >= 3


@dataclass(slots=True)
class AccessSample:
    """A privacy-safe representative access event."""

    timestamp: str
    method: str
    route: str
    status: int
    request_time_ms: float
    upstream_status: int | None = None
    upstream_time_ms: float | None = None
    upstream_addr: str = ""
    query_parameters: list[str] = field(default_factory=list)
    body_fields: list[str] = field(default_factory=list)

    def to_ai_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "method": self.method,
            "route": self.route,
            "status": self.status,
            "request_time_ms": round(self.request_time_ms, 3),
            "upstream_status": self.upstream_status,
            "upstream_time_ms": (
                round(self.upstream_time_ms, 3)
                if self.upstream_time_ms is not None
                else None
            ),
            "upstream_addr": self.upstream_addr[:120],
            "query_parameters": self.query_parameters[:_MAX_PARAMETER_NAMES],
            "body_fields": self.body_fields[:_MAX_PARAMETER_NAMES],
        }


@dataclass(slots=True)
class AccessIncident:
    """One independently deduplicated anomaly signal."""

    source: str
    site: str
    kind: str
    priority: str
    request_count: int
    current_value: float
    baseline_value: float
    status_4xx: int = 0
    status_5xx: int = 0
    gateway_5xx: int = 0
    upstream_5xx: int = 0
    p95_ms: float = 0.0
    route_key: str = ""
    status_counts: dict[int, int] = field(default_factory=dict)
    samples: list[AccessSample] = field(default_factory=list)
    ai_summary: str = ""

    @property
    def key(self) -> str:
        suffix = f"|{self.route_key}" if self.route_key else ""
        return f"{self.source}|{self.site}|{self.kind}{suffix}"

    @property
    def impact(self) -> float:
        if self.kind == "http_5xx":
            return float(self.status_5xx)
        if self.kind in {"http_4xx", "route_4xx"}:
            return float(self.status_4xx)
        if self.kind == "latency":
            return self.p95_ms
        return max(0.0, self.baseline_value - self.current_value)

    @property
    def top_route(self) -> str:
        if self.route_key:
            return self.route_key
        routes = [
            f"{sample.method} {sample.route}"
            for sample in self.samples
            if sample.route
        ]
        return Counter(routes).most_common(1)[0][0] if routes else ""

    @property
    def top_failing_upstream(self) -> str:
        upstreams = [
            sample.upstream_addr
            for sample in self.samples
            if sample.upstream_addr
            and (
                (sample.upstream_status or 0) >= 500
                or sample.status in {502, 503, 504}
            )
        ]
        return Counter(upstreams).most_common(1)[0][0] if upstreams else ""


@dataclass(slots=True)
class AccessRecovery:
    """A previously notified signal that stayed normal for two windows."""

    source: str
    site: str
    kind: str
    priority: str
    route_key: str = ""

    @property
    def key(self) -> str:
        suffix = f"|{self.route_key}" if self.route_key else ""
        return f"{self.source}|{self.site}|{self.kind}{suffix}"


def normalize_site(value: object) -> str:
    """Normalize an HTTP Host value without accepting ports or malformed hosts."""
    site = str(value or "").strip().lower().rstrip(".")
    return site


def is_allowed_site(site: str, allowed_suffixes: tuple[str, ...]) -> bool:
    """Reject empty/scanner Host values and keep configured enterprise suffixes."""
    normalized = normalize_site(site)
    if not normalized or normalized in {"_", "localhost"} or ":" in normalized:
        return False
    try:
        ipaddress.ip_address(normalized)
        return False
    except ValueError:
        pass
    if not _HOST_RE.fullmatch(normalized):
        return False
    return any(
        normalized == suffix or normalized.endswith(f".{suffix}")
        for suffix in allowed_suffixes
    )


def normalize_request(request: object) -> tuple[str, str]:
    """
    Return a method and privacy-safe route.

    Query strings and fragments are discarded. UUID and numeric path segments
    are normalized so high-cardinality resource IDs do not create new routes.
    """
    raw = str(request or "").strip()
    if not raw:
        return "UNKNOWN", "/"

    parts = raw.split()
    if len(parts) >= 2 and parts[0].isalpha():
        method = parts[0].upper()[:16]
        target = parts[1]
    else:
        method = "UNKNOWN"
        target = parts[0]

    try:
        path = urlsplit(target).path
    except ValueError:
        path = target.split("?", 1)[0].split("#", 1)[0]
    if not path:
        path = "/"
    if not path.startswith("/"):
        path = f"/{path}"

    normalized_segments: list[str] = []
    for segment in path.split("/"):
        if _UUID_SEGMENT_RE.fullmatch(segment):
            normalized_segments.append("{uuid}")
        elif _INTEGER_SEGMENT_RE.fullmatch(segment):
            normalized_segments.append("{id}")
        else:
            normalized_segments.append(segment[:160])
    route = "/".join(normalized_segments)
    route = re.sub(r"/{2,}", "/", route)
    return method, route[:500] or "/"


def extract_query_parameter_names(request: object) -> list[str]:
    """Return only safe query-string field names; values are never retained."""
    raw = str(request or "").strip()
    if not raw:
        return []
    parts = raw.split()
    target = parts[1] if len(parts) >= 2 and parts[0].isalpha() else parts[0]
    try:
        query = urlsplit(target).query
        names = [name for name, _value in parse_qsl(query, keep_blank_values=True)]
    except ValueError:
        return []
    return _safe_parameter_names(names)


def extract_body_field_names(request_body: object) -> list[str]:
    """
    Extract JSON/form field names without retaining request values.

    Only the first 16 KiB is inspected and sensitive credential/signature field
    names are excluded as an extra guard before AI or WeCom receives metadata.
    """
    if isinstance(request_body, dict):
        return _json_field_names(request_body)
    raw = str(request_body or "").strip()
    if not raw or raw in {"（空）", "(empty)", "-"}:
        return []
    raw = raw[:16384]
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        return _json_field_names(parsed)
    try:
        names = [name for name, _value in parse_qsl(raw, keep_blank_values=True)]
    except ValueError:
        names = []
    if names:
        return _safe_parameter_names(names)
    return _safe_parameter_names(
        match.group(1)
        for match in re.finditer(r'["\']?([A-Za-z_][\w.\-\[\]]{0,63})["\']?\s*[:=]', raw)
    )


def _json_field_names(value: dict) -> list[str]:
    names: list[str] = []

    def visit(item: object, prefix: str = "", depth: int = 0) -> None:
        if not isinstance(item, dict) or depth > 1:
            return
        for key, child in item.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            names.append(name)
            if len(names) >= _MAX_PARAMETER_NAMES * 2:
                return
            visit(child, name, depth + 1)

    visit(value)
    return _safe_parameter_names(names)


def _safe_parameter_names(names) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in names:
        name = str(value or "").strip()
        lowered = name.lower()
        compact = re.sub(r"[^a-z0-9]", "", lowered)
        if (
            not _SAFE_PARAMETER_NAME_RE.fullmatch(name)
            or _SENSITIVE_PARAMETER_NAME_RE.search(name)
            or any(
                marker in compact
                for marker in (
                    "authorization",
                    "cookie",
                    "password",
                    "passwd",
                    "secret",
                    "signature",
                    "token",
                    "apikey",
                )
            )
            or lowered in seen
        ):
            continue
        seen.add(lowered)
        result.append(name)
        if len(result) >= _MAX_PARAMETER_NAMES:
            break
    return result


def is_nginx_noise_route(route_key: str) -> bool:
    """Suppress Nginx/C# edge traffic that rarely indicates an app incident."""
    method, _, path = route_key.partition(" ")
    path = path or "/"
    if method.upper() == "OPTIONS" or path == "/":
        return True
    return bool(
        _NGINX_STATIC_PATH_RE.search(path)
        or _NGINX_REPOSITORY_PATH_RE.search(path)
    )


def route_4xx_threshold(
    source: str,
    *,
    nginx_min_count: int = 100,
    nginx_min_rate: float = 0.30,
    ingress_min_count: int = 20,
    ingress_min_rate: float = 0.10,
) -> tuple[int, float]:
    """Return source-specific interface 4xx thresholds."""
    if source == "ingress":
        return ingress_min_count, ingress_min_rate
    return nginx_min_count, nginx_min_rate


def aggregate_metrics(metrics: list[AccessMetric]) -> dict[tuple[str, str], AccessWindow]:
    """Combine per-minute metrics into the current patrol window."""
    windows: dict[tuple[str, str], AccessWindow] = {}
    for metric in metrics:
        key = (metric.source, metric.site)
        window = windows.setdefault(
            key,
            AccessWindow(source=metric.source, site=metric.site),
        )
        window.request_count += metric.request_count
        window.status_4xx += metric.status_4xx
        window.status_5xx += metric.status_5xx
        window.gateway_5xx += metric.gateway_5xx
        window.upstream_5xx += metric.upstream_5xx
        # Per-minute percentiles cannot be merged exactly without a histogram.
        # Taking the maximum retains short latency spikes instead of hiding them.
        window.p50_ms = max(window.p50_ms, metric.p50_ms)
        window.p95_ms = max(window.p95_ms, metric.p95_ms)
        window.p99_ms = max(window.p99_ms, metric.p99_ms)
    return windows


def detect_incidents(
    windows: dict[tuple[str, str], AccessWindow],
    baselines: dict[tuple[str, str], AccessBaseline],
) -> list[AccessIncident]:
    """Apply deterministic low-noise access anomaly rules."""
    incidents: list[AccessIncident] = []
    for key, window in windows.items():
        baseline = baselines.get(key)
        baseline_ready = bool(baseline and baseline.is_ready)
        baseline_5xx = baseline.rate_5xx if baseline else 0.0
        baseline_p95 = baseline.p95_ms if baseline else 0.0

        p0_5xx = (
            window.request_count >= 100
            and window.status_5xx >= 100
            and window.rate_5xx >= 0.05
            and (
                not baseline_ready
                or window.rate_5xx >= max(0.05, baseline_5xx * 3)
            )
        )
        p1_5xx = (
            window.request_count >= 100
            and window.status_5xx >= 20
            and window.rate_5xx >= 0.01
            and (
                not baseline_ready
                or window.rate_5xx >= max(0.01, baseline_5xx * 3)
            )
        )
        if p0_5xx or p1_5xx:
            incidents.append(
                AccessIncident(
                    source=window.source,
                    site=window.site,
                    kind="http_5xx",
                    priority="P0" if p0_5xx else "P1",
                    request_count=window.request_count,
                    current_value=window.rate_5xx,
                    baseline_value=baseline_5xx,
                    status_5xx=window.status_5xx,
                    gateway_5xx=window.gateway_5xx,
                    upstream_5xx=window.upstream_5xx,
                    p95_ms=window.p95_ms,
                )
            )

        if (
            window.successful_count >= 100
            and window.success_rate >= 0.80
            and baseline_ready
            and baseline_p95 > 0
            and window.p95_ms >= 2000
            and window.p95_ms >= baseline_p95 * 3
        ):
            incidents.append(
                AccessIncident(
                    source=window.source,
                    site=window.site,
                    kind="latency",
                    priority="P1",
                    request_count=window.request_count,
                    current_value=window.p95_ms,
                    baseline_value=baseline_p95,
                    status_4xx=window.status_4xx,
                    status_5xx=window.status_5xx,
                    p95_ms=window.p95_ms,
                )
            )

        if (
            baseline_ready
            and baseline
            and baseline.request_count >= 100
            and window.request_count <= baseline.request_count * 0.20
        ):
            incidents.append(
                AccessIncident(
                    source=window.source,
                    site=window.site,
                    kind="traffic_drop",
                    priority="P1",
                    request_count=window.request_count,
                    current_value=float(window.request_count),
                    baseline_value=baseline.request_count,
                    p95_ms=window.p95_ms,
                )
            )

    priority_rank = {"P0": 0, "P1": 1, "P2": 2}
    return sorted(
        incidents,
        key=lambda item: (
            priority_rank.get(item.priority, 9),
            -item.impact,
            item.site,
            item.source,
            item.kind,
        ),
    )


def detect_route_incidents(
    route_metrics: list[AccessRouteMetric],
    windows: dict[tuple[str, str], AccessWindow] | None = None,
    *,
    nginx_min_count: int = 100,
    nginx_min_rate: float = 0.30,
    ingress_min_count: int = 20,
    ingress_min_rate: float = 0.10,
) -> list[AccessIncident]:
    """
    Detect concentrated 4xx failures hidden by healthy site-wide traffic.

    This finds a failing interface inside an otherwise useful site. Windows
    dominated by rejected scan traffic and single 400 responses never qualify.
    """
    combined: dict[tuple[str, str, str], AccessRouteMetric] = {}
    for metric in route_metrics:
        key = (metric.source, metric.site, metric.route_key)
        current = combined.get(key)
        if current is None:
            combined[key] = AccessRouteMetric(
                source=metric.source,
                site=metric.site,
                route_key=metric.route_key,
                request_count=metric.request_count,
                status_4xx=metric.status_4xx,
                status_5xx=metric.status_5xx,
                p95_ms=metric.p95_ms,
                status_counts=dict(metric.status_counts),
            )
            continue
        current.request_count += metric.request_count
        current.status_4xx += metric.status_4xx
        current.status_5xx += metric.status_5xx
        current.p95_ms = max(current.p95_ms, metric.p95_ms)
        for status, count in metric.status_counts.items():
            current.status_counts[status] = (
                current.status_counts.get(status, 0) + count
            )

    incidents: list[AccessIncident] = []
    for metric in combined.values():
        site_window = (windows or {}).get((metric.source, metric.site))
        if site_window and is_rejected_traffic_window(site_window):
            continue
        route_path = metric.route_key.partition(" ")[2]
        if _KNOWN_PROBE_PATH_RE.search(route_path):
            continue
        if metric.source == "nginx" and is_nginx_noise_route(metric.route_key):
            continue
        min_count, min_rate = route_4xx_threshold(
            metric.source,
            nginx_min_count=nginx_min_count,
            nginx_min_rate=nginx_min_rate,
            ingress_min_count=ingress_min_count,
            ingress_min_rate=ingress_min_rate,
        )
        qualifies = (
            metric.request_count >= min_count
            and metric.status_4xx >= min_count
            and metric.rate_4xx >= min_rate
        )
        if not qualifies:
            continue
        incidents.append(
            AccessIncident(
                source=metric.source,
                site=metric.site,
                kind="route_4xx",
                priority="P1",
                request_count=metric.request_count,
                current_value=metric.rate_4xx,
                baseline_value=0.0,
                status_4xx=metric.status_4xx,
                status_5xx=metric.status_5xx,
                p95_ms=metric.p95_ms,
                route_key=metric.route_key,
                status_counts=dict(metric.status_counts),
            )
        )

    return sorted(
        incidents,
        key=lambda item: (-item.impact, item.site, item.source, item.route_key),
    )


def safe_float(value: object) -> float:
    """Coerce Elasticsearch numeric values, including NaN/null, safely."""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def safe_int(value: object) -> int:
    """Coerce source status fields that may be numbers, strings, or '-'. """
    if value is None:
        return 0
    raw = str(value).strip()
    if not raw or raw == "-":
        return 0
    # Nginx can record retry chains such as "502, 200"; the last status is the
    # response ultimately returned by the selected upstream.
    raw = raw.rsplit(",", 1)[-1].strip()
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return 0
