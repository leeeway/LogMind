"""Elasticsearch collection and metric storage for global HTTP access patrol."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from elasticsearch.helpers import async_bulk

from logmind.core.config import get_settings
from logmind.core.logging import get_logger
from logmind.domain.http_access.models import (
    AccessBaseline,
    AccessMetric,
    AccessRouteBaseline,
    AccessRouteMetric,
    AccessSample,
    AccessWindow,
    extract_body_field_names,
    extract_query_parameter_names,
    is_allowed_site,
    is_rejected_traffic_window,
    normalize_request,
    normalize_site,
    route_4xx_threshold,
    safe_float,
    safe_int,
)

logger = get_logger(__name__)

_COMPOSITE_PAGE_SIZE = 1000
_INGEST_PIPELINE_ID = "logmind-http-access-normalize-v1"

# Historical data has text/long mapping conflicts. Runtime fields read from
# _source so one query works across all existing data-stream backing indices.
_RUNTIME_MAPPINGS = {
    "lm_status_code": {
        "type": "long",
        "script": {
            "source": """
                def value = params._source.containsKey('lm_http_status_code')
                    ? params._source.lm_http_status_code : params._source.status;
                if (value != null) {
                    String raw = value.toString().trim();
                    if (raw.contains(',')) {
                        raw = raw.substring(raw.lastIndexOf(',') + 1).trim();
                    }
                    if (raw.length() > 0 && raw != '-') {
                        try { emit(Long.parseLong(raw)); } catch (Exception ignored) {}
                    }
                }
            """,
        },
    },
    "lm_upstream_status_code": {
        "type": "long",
        "script": {
            "source": """
                def value = params._source.containsKey('lm_upstream_status_code')
                    ? params._source.lm_upstream_status_code : params._source.upstream_status;
                if (value != null) {
                    String raw = value.toString().trim();
                    if (raw.contains(',')) {
                        raw = raw.substring(raw.lastIndexOf(',') + 1).trim();
                    }
                    if (raw.length() > 0 && raw != '-') {
                        try { emit(Long.parseLong(raw)); } catch (Exception ignored) {}
                    }
                }
            """,
        },
    },
    "lm_request_time_ms": {
        "type": "double",
        "script": {
            "source": """
                if (params._source.containsKey('lm_request_time_ms')
                        && params._source.lm_request_time_ms != null) {
                    try {
                        emit(Double.parseDouble(
                            params._source.lm_request_time_ms.toString()
                        ));
                    } catch (Exception ignored) {}
                } else if (params._source.request_time != null) {
                    try {
                        emit(Double.parseDouble(
                            params._source.request_time.toString()
                        ) * 1000.0);
                    } catch (Exception ignored) {}
                }
            """,
        },
    },
    "lm_upstream_time_ms": {
        "type": "double",
        "script": {
            "source": """
                if (params._source.containsKey('lm_upstream_time_ms')
                        && params._source.lm_upstream_time_ms != null) {
                    try {
                        emit(Double.parseDouble(
                            params._source.lm_upstream_time_ms.toString()
                        ));
                    } catch (Exception ignored) {}
                } else if (params._source.upstream_response_time != null) {
                    String raw = params._source.upstream_response_time.toString().trim();
                    if (raw.contains(',')) {
                        raw = raw.substring(raw.lastIndexOf(',') + 1).trim();
                    }
                    if (raw.length() > 0 && raw != '-') {
                        try { emit(Double.parseDouble(raw) * 1000.0); }
                        catch (Exception ignored) {}
                    }
                }
            """,
        },
    },
}

_ROUTE_RUNTIME_MAPPINGS = {
    **_RUNTIME_MAPPINGS,
    "lm_route_key": {
        "type": "keyword",
        "script": {
            "source": """
                String method = "UNKNOWN";
                String path = "/";
                if (params._source.containsKey('lm_http_method')
                        && params._source.lm_http_method != null) {
                    method = params._source.lm_http_method.toString().toUpperCase();
                }
                if (params._source.containsKey('lm_route')
                        && params._source.lm_route != null) {
                    path = params._source.lm_route.toString();
                } else if (params._source.containsKey('request')
                        && params._source.request != null) {
                    String request = params._source.request.toString().trim();
                    String[] parts = request.splitOnToken(' ');
                    if (parts.length >= 2) {
                        method = parts[0].toUpperCase();
                        path = parts[1];
                    } else if (parts.length == 1) {
                        path = parts[0];
                    }
                    int queryAt = path.indexOf('?');
                    if (queryAt >= 0) path = path.substring(0, queryAt);
                    int fragmentAt = path.indexOf('#');
                    if (fragmentAt >= 0) path = path.substring(0, fragmentAt);
                    if (!path.startsWith('/')) path = '/' + path;
                    boolean trailingSlash = path.endsWith("/");
                    String[] segments = path.splitOnToken('/');
                    StringBuilder normalized = new StringBuilder();
                    for (String segment : segments) {
                        if (segment.length() == 0) continue;
                        boolean numeric = true;
                        boolean hexId = segment.length() >= 16
                            && segment.length() <= 64;
                        for (int i = 0; i < segment.length(); i++) {
                            int code = (int)segment.charAt(i);
                            if (code < 48 || code > 57) {
                                numeric = false;
                            }
                            boolean hexChar = (code >= 48 && code <= 57)
                                || (code >= 65 && code <= 70)
                                || (code >= 97 && code <= 102);
                            if (!hexChar) {
                                hexId = false;
                            }
                        }
                        boolean uuid = segment.length() == 36
                            && segment.substring(8, 9).equals("-")
                            && segment.substring(13, 14).equals("-")
                            && segment.substring(18, 19).equals("-")
                            && segment.substring(23, 24).equals("-");
                        normalized.append('/');
                        normalized.append(
                            uuid ? '{uuid}' : numeric || hexId ? '{id}' : segment
                        );
                    }
                    path = normalized.length() == 0
                        ? '/' : normalized.toString();
                    if (trailingSlash && !path.equals("/")) {
                        path += "/";
                    }
                }
                if (method.length() > 16) method = method.substring(0, 16);
                if (path.length() > 500) path = path.substring(0, 500);
                emit(method + ' ' + path);
            """,
        },
    },
}


def source_name_for_index(index_name: str) -> str:
    return "ingress" if "ingress" in index_name.lower() else "nginx"


class HttpAccessService:
    """Collect raw access aggregates and maintain the compact metric index."""

    def __init__(self, es=None):
        self._es = es

    @property
    def es(self):
        if self._es is None:
            from logmind.core.elasticsearch import get_es_client

            self._es = get_es_client()
        return self._es

    async def collect_window(
        self,
        time_from: datetime,
        time_to: datetime,
    ) -> list[AccessMetric]:
        """Collect all source/site/minute buckets from both configured indices."""
        settings = get_settings()
        index_names = settings.http_access_index_list
        tasks = [
            self._collect_source(
                index_name=index_name,
                source=source_name_for_index(index_name),
                time_from=time_from,
                time_to=time_to,
                allowed_suffixes=settings.http_access_allowed_suffix_list,
            )
            for index_name in index_names
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        metrics: list[AccessMetric] = []
        failures: list[str] = []
        for index_name, result in zip(index_names, results, strict=True):
            if isinstance(result, Exception):
                failures.append(index_name)
                logger.error(
                    "http_access_source_collection_failed",
                    index=index_name,
                    error=str(result),
                )
                continue
            metrics.extend(result)
        if failures:
            raise RuntimeError(
                "HTTP access collection incomplete for: " + ", ".join(failures)
            )

        logger.info(
            "http_access_window_collected",
            time_from=time_from.isoformat(),
            time_to=time_to.isoformat(),
            metric_count=len(metrics),
            source_count=len(index_names),
        )
        return metrics

    async def _collect_source(
        self,
        *,
        index_name: str,
        source: str,
        time_from: datetime,
        time_to: datetime,
        allowed_suffixes: tuple[str, ...],
    ) -> list[AccessMetric]:
        after_key: dict | None = None
        metrics: list[AccessMetric] = []

        while True:
            composite: dict[str, Any] = {
                "size": _COMPOSITE_PAGE_SIZE,
                "sources": [
                    {
                        "minute": {
                            "date_histogram": {
                                "field": "@timestamp",
                                "fixed_interval": "1m",
                                "order": "asc",
                            }
                        }
                    },
                    {
                        "site": {
                            "terms": {
                                "field": "server_name.keyword",
                                "order": "asc",
                            }
                        }
                    },
                ],
            }
            if after_key:
                composite["after"] = after_key

            body = {
                "size": 0,
                "track_total_hits": False,
                "runtime_mappings": _RUNTIME_MAPPINGS,
                "query": {
                    "bool": {
                        "filter": [
                            {
                                "range": {
                                    "@timestamp": {
                                        "gte": time_from.isoformat(),
                                        "lt": time_to.isoformat(),
                                    }
                                }
                            },
                            {"exists": {"field": "server_name.keyword"}},
                        ]
                    }
                },
                "aggs": {
                    "by_minute_site": {
                        "composite": composite,
                        "aggs": {
                            "status_4xx": {
                                "filter": {
                                    "range": {
                                        "lm_status_code": {"gte": 400, "lt": 500}
                                    }
                                }
                            },
                            "status_5xx": {
                                "filter": {
                                    "range": {
                                        "lm_status_code": {"gte": 500, "lt": 600}
                                    }
                                }
                            },
                            "gateway_5xx": {
                                "filter": {
                                    "terms": {
                                        "lm_status_code": [502, 503, 504]
                                    }
                                }
                            },
                            "upstream_5xx": {
                                "filter": {
                                    "range": {
                                        "lm_upstream_status_code": {
                                            "gte": 500,
                                            "lt": 600,
                                        }
                                    }
                                }
                            },
                            "successful": {
                                "filter": {
                                    "range": {
                                        "lm_status_code": {
                                            "gte": 200,
                                            "lt": 400,
                                        }
                                    }
                                },
                                "aggs": {
                                    "latency": {
                                        "percentiles": {
                                            "field": "lm_request_time_ms",
                                            "percents": [50, 95, 99],
                                            "keyed": True,
                                        }
                                    }
                                },
                            },
                        },
                    }
                },
            }
            response = await self.es.search(index=index_name, body=body)
            aggregation = (
                response.get("aggregations", {}).get("by_minute_site", {})
            )
            for bucket in aggregation.get("buckets", []):
                site = normalize_site(bucket.get("key", {}).get("site"))
                if not is_allowed_site(site, allowed_suffixes):
                    continue
                minute_ms = bucket.get("key", {}).get("minute")
                if minute_ms is None:
                    continue
                minute = datetime.fromtimestamp(
                    float(minute_ms) / 1000.0,
                    tz=UTC,
                )
                latency_values = (
                    bucket.get("successful", {})
                    .get("latency", {})
                    .get("values", {})
                )
                metrics.append(
                    AccessMetric(
                        source=source,
                        site=site,
                        minute=minute,
                        request_count=int(bucket.get("doc_count", 0)),
                        status_4xx=int(
                            bucket.get("status_4xx", {}).get("doc_count", 0)
                        ),
                        status_5xx=int(
                            bucket.get("status_5xx", {}).get("doc_count", 0)
                        ),
                        gateway_5xx=int(
                            bucket.get("gateway_5xx", {}).get("doc_count", 0)
                        ),
                        upstream_5xx=int(
                            bucket.get("upstream_5xx", {}).get("doc_count", 0)
                        ),
                        p50_ms=safe_float(latency_values.get("50.0")),
                        p95_ms=safe_float(latency_values.get("95.0")),
                        p99_ms=safe_float(latency_values.get("99.0")),
                    )
                )

            after_key = aggregation.get("after_key")
            if not after_key:
                break

        return metrics

    async def collect_route_metrics(
        self,
        windows: dict[tuple[str, str], AccessWindow],
        *,
        time_from: datetime,
        time_to: datetime,
    ) -> list[AccessRouteMetric]:
        """
        Aggregate routes only for sites with a meaningful 4xx candidate.

        There is at most one route aggregation per configured source index,
        rather than one Elasticsearch query or Celery task per server_name.
        """
        candidate_windows: dict[str, list[AccessWindow]] = {}
        rejected_site_count = 0
        settings = get_settings()
        for window in windows.values():
            min_count, _min_rate = route_4xx_threshold(
                window.source,
                nginx_min_count=getattr(
                    settings,
                    "http_access_nginx_4xx_min_count",
                    100,
                ),
                nginx_min_rate=getattr(
                    settings,
                    "http_access_nginx_4xx_min_rate",
                    0.30,
                ),
                ingress_min_count=getattr(
                    settings,
                    "http_access_ingress_4xx_min_count",
                    20,
                ),
                ingress_min_rate=getattr(
                    settings,
                    "http_access_ingress_4xx_min_rate",
                    0.10,
                ),
            )
            if window.status_4xx < min_count:
                continue
            if is_rejected_traffic_window(window):
                rejected_site_count += 1
                continue
            candidate_windows.setdefault(window.source, []).append(window)
        if not candidate_windows:
            if rejected_site_count:
                logger.info(
                    "http_access_route_candidates_rejected",
                    rejected_site_count=rejected_site_count,
                )
            return []

        max_candidates = getattr(
            settings,
            "http_access_max_route_candidate_sites",
            20,
        )
        candidates_by_source: dict[str, list[str]] = {}
        omitted_site_count = 0
        for source, source_windows in candidate_windows.items():
            ranked = sorted(
                source_windows,
                key=lambda item: (
                    -item.status_4xx,
                    -item.rate_4xx,
                    item.site,
                ),
            )
            candidates_by_source[source] = [
                item.site for item in ranked[:max_candidates]
            ]
            omitted_site_count += max(0, len(ranked) - max_candidates)
        if rejected_site_count or omitted_site_count:
            logger.info(
                "http_access_route_candidates_filtered",
                rejected_site_count=rejected_site_count,
                omitted_site_count=omitted_site_count,
                selected_site_count=sum(
                    len(sites) for sites in candidates_by_source.values()
                ),
            )

        queries = [
            (
                index_name,
                source_name_for_index(index_name),
                candidates_by_source.get(
                    source_name_for_index(index_name),
                    [],
                ),
            )
            for index_name in settings.http_access_index_list
            if candidates_by_source.get(source_name_for_index(index_name))
        ]
        results = await asyncio.gather(
            *[
                self._collect_route_source(
                    index_name=index_name,
                    source=source,
                    sites=sites,
                    time_from=time_from,
                    time_to=time_to,
                )
                for index_name, source, sites in queries
            ],
            return_exceptions=True,
        )

        route_metrics: list[AccessRouteMetric] = []
        failures: list[str] = []
        for (index_name, _source, _sites), result in zip(
            queries,
            results,
            strict=True,
        ):
            if isinstance(result, Exception):
                failures.append(index_name)
                logger.error(
                    "http_access_route_collection_failed",
                    index=index_name,
                    error=str(result),
                )
                continue
            route_metrics.extend(result)
        if failures:
            raise RuntimeError(
                "HTTP access route collection incomplete for: "
                + ", ".join(failures)
            )
        return route_metrics

    async def _collect_route_source(
        self,
        *,
        index_name: str,
        source: str,
        sites: list[str],
        time_from: datetime,
        time_to: datetime,
    ) -> list[AccessRouteMetric]:
        after_key: dict | None = None
        route_metrics: list[AccessRouteMetric] = []
        while True:
            composite: dict[str, Any] = {
                "size": _COMPOSITE_PAGE_SIZE,
                "sources": [
                    {
                        "site": {
                            "terms": {
                                "field": "server_name.keyword",
                                "order": "asc",
                            }
                        }
                    },
                    {
                        "route": {
                            "terms": {
                                "field": "lm_route_key",
                                "order": "asc",
                            }
                        }
                    },
                ],
            }
            if after_key:
                composite["after"] = after_key
            body = {
                "size": 0,
                "track_total_hits": False,
                "runtime_mappings": _ROUTE_RUNTIME_MAPPINGS,
                "query": {
                    "bool": {
                        "filter": [
                            {
                                "range": {
                                    "@timestamp": {
                                        "gte": time_from.isoformat(),
                                        "lt": time_to.isoformat(),
                                    }
                                }
                            },
                            {"terms": {"server_name.keyword": sites}},
                        ]
                    }
                },
                "aggs": {
                    "by_site_route": {
                        "composite": composite,
                        "aggs": {
                            "status_4xx": {
                                "filter": {
                                    "range": {
                                        "lm_status_code": {
                                            "gte": 400,
                                            "lt": 500,
                                        }
                                    }
                                },
                                "aggs": {
                                    "status_codes": {
                                        "terms": {
                                            "field": "lm_status_code",
                                            "size": 10,
                                        }
                                    }
                                },
                            },
                            "has_4xx": {
                                "bucket_selector": {
                                    "buckets_path": {
                                        "errors": "status_4xx>_count"
                                    },
                                    "script": "params.errors > 0",
                                }
                            },
                            "latency": {
                                "percentiles": {
                                    "field": "lm_request_time_ms",
                                    "percents": [95],
                                    "keyed": True,
                                }
                            },
                        },
                    }
                },
            }
            response = await self.es.search(index=index_name, body=body)
            aggregation = (
                response.get("aggregations", {}).get("by_site_route", {})
            )
            for bucket in aggregation.get("buckets", []):
                key = bucket.get("key", {})
                route_key = str(key.get("route") or "")[:520]
                if not route_key or route_key == "UNKNOWN /":
                    continue
                status_4xx = int(
                    bucket.get("status_4xx", {}).get("doc_count", 0)
                )
                if status_4xx <= 0:
                    continue
                status_counts = {
                    safe_int(item.get("key")): int(item.get("doc_count", 0))
                    for item in (
                        bucket.get("status_4xx", {})
                        .get("status_codes", {})
                        .get("buckets", [])
                    )
                    if 400 <= safe_int(item.get("key")) < 500
                }
                route_metrics.append(
                    AccessRouteMetric(
                        source=source,
                        site=normalize_site(key.get("site")),
                        route_key=route_key,
                        request_count=int(bucket.get("doc_count", 0)),
                        status_4xx=status_4xx,
                        p95_ms=safe_float(
                            bucket.get("latency", {})
                            .get("values", {})
                            .get("95.0")
                        ),
                        status_counts=status_counts,
                    )
                )
            after_key = aggregation.get("after_key")
            if not after_key:
                break
        return route_metrics

    async def ensure_metrics_index(self) -> None:
        settings = get_settings()
        index_name = settings.http_access_metrics_index
        if await self.es.indices.exists(index=index_name):
            return
        mappings = {
            "dynamic": "strict",
            "properties": {
                "source": {"type": "keyword"},
                "site": {"type": "keyword"},
                "minute": {"type": "date"},
                "request_count": {"type": "long"},
                "status_4xx": {"type": "long"},
                "status_5xx": {"type": "long"},
                "gateway_5xx": {"type": "long"},
                "upstream_5xx": {"type": "long"},
                "p50_ms": {"type": "double"},
                "p95_ms": {"type": "double"},
                "p99_ms": {"type": "double"},
            },
        }
        try:
            await self.es.indices.create(
                index=index_name,
                mappings=mappings,
                settings={"index": {"number_of_shards": 1}},
            )
            logger.info("http_access_metrics_index_created", index=index_name)
        except Exception as exc:
            # Multiple workers may race on first startup.
            if "resource_already_exists_exception" not in str(exc):
                raise

    async def persist_metrics(self, metrics: list[AccessMetric]) -> int:
        if not metrics:
            return 0
        await self.ensure_metrics_index()
        index_name = get_settings().http_access_metrics_index
        actions = []
        for metric in metrics:
            raw_id = f"{metric.source}|{metric.site}|{metric.minute.isoformat()}"
            actions.append(
                {
                    "_op_type": "index",
                    "_index": index_name,
                    "_id": hashlib.sha256(raw_id.encode()).hexdigest(),
                    "_source": metric.to_document(),
                }
            )
        success, errors = await async_bulk(
            self.es,
            actions,
            raise_on_error=False,
            raise_on_exception=False,
        )
        if errors:
            logger.warning(
                "http_access_metric_bulk_partial_failure",
                failures=len(errors),
            )
        return int(success)

    async def ensure_route_metrics_index(self) -> None:
        """Create the compact interface-level history used for noise learning."""
        settings = get_settings()
        index_name = settings.http_access_route_metrics_index
        if await self.es.indices.exists(index=index_name):
            return
        mappings = {
            "dynamic": "strict",
            "properties": {
                "source": {"type": "keyword"},
                "site": {"type": "keyword"},
                "route_key": {"type": "keyword", "ignore_above": 520},
                "observed_at": {"type": "date"},
                "request_count": {"type": "long"},
                "status_4xx": {"type": "long"},
                "rate_4xx": {"type": "double"},
            },
        }
        try:
            await self.es.indices.create(
                index=index_name,
                mappings=mappings,
                settings={"index": {"number_of_shards": 1}},
            )
            logger.info(
                "http_access_route_metrics_index_created",
                index=index_name,
            )
        except Exception as exc:
            if "resource_already_exists_exception" not in str(exc):
                raise

    async def persist_route_metrics(
        self,
        metrics: list[AccessRouteMetric],
        *,
        observed_at: datetime,
    ) -> int:
        """Persist one compact record per candidate interface and window."""
        if not metrics:
            return 0
        await self.ensure_route_metrics_index()
        index_name = get_settings().http_access_route_metrics_index
        actions = []
        for metric in metrics:
            raw_id = (
                f"{metric.source}|{metric.site}|{metric.route_key}|"
                f"{observed_at.isoformat()}"
            )
            actions.append(
                {
                    "_op_type": "index",
                    "_index": index_name,
                    "_id": hashlib.sha256(raw_id.encode()).hexdigest(),
                    "_source": {
                        "source": metric.source,
                        "site": metric.site,
                        "route_key": metric.route_key,
                        "observed_at": observed_at.isoformat(),
                        "request_count": metric.request_count,
                        "status_4xx": metric.status_4xx,
                        "rate_4xx": round(metric.rate_4xx, 6),
                    },
                }
            )
        success, errors = await async_bulk(
            self.es,
            actions,
            raise_on_error=False,
            raise_on_exception=False,
        )
        if errors:
            logger.warning(
                "http_access_route_metric_bulk_partial_failure",
                failures=len(errors),
            )
        return int(success)

    async def load_route_baselines(
        self,
        *,
        before: datetime,
        days: int = 7,
    ) -> dict[tuple[str, str, str], AccessRouteBaseline]:
        """Read compact route history instead of rescanning raw access logs."""
        settings = get_settings()
        index_name = settings.http_access_route_metrics_index
        if not await self.es.indices.exists(index=index_name):
            return {}

        after_key: dict | None = None
        baselines: dict[tuple[str, str, str], AccessRouteBaseline] = {}
        while True:
            composite: dict[str, Any] = {
                "size": _COMPOSITE_PAGE_SIZE,
                "sources": [
                    {"source": {"terms": {"field": "source"}}},
                    {"site": {"terms": {"field": "site"}}},
                    {"route": {"terms": {"field": "route_key"}}},
                ],
            }
            if after_key:
                composite["after"] = after_key
            body = {
                "size": 0,
                "track_total_hits": False,
                "query": {
                    "range": {
                        "observed_at": {
                            "gte": (before - timedelta(days=days)).isoformat(),
                            "lt": before.isoformat(),
                        }
                    }
                },
                "aggs": {
                    "by_route": {
                        "composite": composite,
                        "aggs": {
                            "samples": {
                                "value_count": {"field": "observed_at"}
                            },
                            "days": {
                                "date_histogram": {
                                    "field": "observed_at",
                                    "calendar_interval": "day",
                                    "min_doc_count": 1,
                                }
                            },
                            "request_median": {
                                "percentiles": {
                                    "field": "request_count",
                                    "percents": [50],
                                    "keyed": True,
                                }
                            },
                            "status_4xx_median": {
                                "percentiles": {
                                    "field": "status_4xx",
                                    "percents": [50],
                                    "keyed": True,
                                }
                            },
                            "request_sum": {"sum": {"field": "request_count"}},
                            "status_4xx_sum": {"sum": {"field": "status_4xx"}},
                        },
                    }
                },
            }
            response = await self.es.search(index=index_name, body=body)
            aggregation = response.get("aggregations", {}).get(
                "by_route",
                {},
            )
            for bucket in aggregation.get("buckets", []):
                key = bucket.get("key", {})
                request_sum = safe_float(
                    bucket.get("request_sum", {}).get("value")
                )
                status_sum = safe_float(
                    bucket.get("status_4xx_sum", {}).get("value")
                )
                baseline = AccessRouteBaseline(
                    source=str(key.get("source", "")),
                    site=str(key.get("site", "")),
                    route_key=str(key.get("route", "")),
                    sample_count=int(
                        safe_float(bucket.get("samples", {}).get("value"))
                    ),
                    day_count=len(
                        bucket.get("days", {}).get("buckets", [])
                    ),
                    request_count=safe_float(
                        bucket.get("request_median", {})
                        .get("values", {})
                        .get("50.0")
                    ),
                    status_4xx=safe_float(
                        bucket.get("status_4xx_median", {})
                        .get("values", {})
                        .get("50.0")
                    ),
                    rate_4xx=status_sum / request_sum if request_sum else 0.0,
                )
                baselines[
                    (baseline.source, baseline.site, baseline.route_key)
                ] = baseline
            after_key = aggregation.get("after_key")
            if not after_key:
                break
        return baselines

    async def load_baselines(
        self,
        *,
        before: datetime,
        window_minutes: int,
        days: int = 7,
    ) -> dict[tuple[str, str], AccessBaseline]:
        """Load robust same-time-of-day baselines from the metric index."""
        settings = get_settings()
        index_name = settings.http_access_metrics_index
        if not await self.es.indices.exists(index=index_name):
            return {}

        slot_minutes = max(
            window_minutes,
            getattr(settings, "http_access_baseline_slot_minutes", 60),
        )
        half_slot = timedelta(minutes=slot_minutes / 2)
        comparable_ranges = []
        for day_offset in range(1, days + 1):
            center = before - timedelta(days=day_offset)
            comparable_ranges.append(
                {
                    "range": {
                        "minute": {
                            "gte": (center - half_slot).isoformat(),
                            "lt": (center + half_slot).isoformat(),
                        }
                    }
                }
            )

        after_key: dict | None = None
        baselines: dict[tuple[str, str], AccessBaseline] = {}
        while True:
            composite: dict[str, Any] = {
                "size": _COMPOSITE_PAGE_SIZE,
                "sources": [
                    {"source": {"terms": {"field": "source"}}},
                    {"site": {"terms": {"field": "site"}}},
                ],
            }
            if after_key:
                composite["after"] = after_key

            body = {
                "size": 0,
                "track_total_hits": False,
                "query": {
                    "bool": {
                        "should": comparable_ranges,
                        "minimum_should_match": 1,
                    }
                },
                "aggs": {
                    "by_source_site": {
                        "composite": composite,
                        "aggs": {
                            "samples": {"value_count": {"field": "minute"}},
                            "request_median": {
                                "percentiles": {
                                    "field": "request_count",
                                    "percents": [50],
                                    "keyed": True,
                                }
                            },
                            "request_sum": {"sum": {"field": "request_count"}},
                            "status_4xx_sum": {"sum": {"field": "status_4xx"}},
                            "status_5xx_sum": {"sum": {"field": "status_5xx"}},
                            "p95_median": {
                                "percentiles": {
                                    "field": "p95_ms",
                                    "percents": [50],
                                    "keyed": True,
                                }
                            },
                        },
                    }
                },
            }
            response = await self.es.search(index=index_name, body=body)
            aggregation = (
                response.get("aggregations", {}).get("by_source_site", {})
            )
            for bucket in aggregation.get("buckets", []):
                source = str(bucket.get("key", {}).get("source", ""))
                site = str(bucket.get("key", {}).get("site", ""))
                request_sum = safe_float(
                    bucket.get("request_sum", {}).get("value")
                )
                rate_4xx = (
                    safe_float(bucket.get("status_4xx_sum", {}).get("value"))
                    / request_sum
                    if request_sum
                    else 0.0
                )
                rate_5xx = (
                    safe_float(bucket.get("status_5xx_sum", {}).get("value"))
                    / request_sum
                    if request_sum
                    else 0.0
                )
                comparable_day_count = int(
                    safe_float(bucket.get("samples", {}).get("value"))
                    // slot_minutes
                )
                baselines[(source, site)] = AccessBaseline(
                    source=source,
                    site=site,
                    sample_count=comparable_day_count,
                    request_count=(
                        safe_float(
                            bucket.get("request_median", {})
                            .get("values", {})
                            .get("50.0")
                        )
                        * window_minutes
                    ),
                    rate_4xx=rate_4xx,
                    rate_5xx=rate_5xx,
                    p95_ms=safe_float(
                        bucket.get("p95_median", {})
                        .get("values", {})
                        .get("50.0")
                    ),
                )

            after_key = aggregation.get("after_key")
            if not after_key:
                break

        return baselines

    async def fetch_samples(
        self,
        *,
        source: str,
        site: str,
        time_from: datetime,
        time_to: datetime,
        size: int = 20,
        prefer_latency: bool = False,
        route_keys: list[str] | None = None,
    ) -> list[AccessSample]:
        """Fetch privacy-safe representative events for one anomalous site."""
        settings = get_settings()
        matching_indices = [
            index_name
            for index_name in settings.http_access_index_list
            if source_name_for_index(index_name) == source
        ]
        if not matching_indices:
            return []

        filters: list[dict[str, Any]] = [
            {
                "range": {
                    "@timestamp": {
                        "gte": time_from.isoformat(),
                        "lt": time_to.isoformat(),
                    }
                }
            },
            {"term": {"server_name.keyword": site}},
        ]
        if route_keys:
            filters.append({"terms": {"lm_route_key": route_keys[:3]}})
            filters.append(
                {
                    "range": {
                        "lm_status_code": {
                            "gte": 400,
                            "lt": 500,
                        }
                    }
                }
            )
        elif prefer_latency:
            filters.append(
                {
                    "range": {
                        "lm_status_code": {
                            "gte": 200,
                            "lt": 400,
                        }
                    }
                }
            )

        body = {
            "size": min(max(size, 1), 20),
            "track_total_hits": False,
            "runtime_mappings": (
                _ROUTE_RUNTIME_MAPPINGS if route_keys else _RUNTIME_MAPPINGS
            ),
            "_source": [
                "@timestamp",
                "status",
                "request",
                "request_body",
                "request_time",
                "upstream_status",
                "upstream_response_time",
                "upstream_addr",
                "lm_http_status_code",
                "lm_upstream_status_code",
                "lm_request_time_ms",
                "lm_upstream_time_ms",
                "lm_http_method",
                "lm_route",
            ],
            "query": {
                "bool": {
                    "filter": filters
                }
            },
            "sort": (
                [
                    {"lm_request_time_ms": {"order": "desc"}},
                    {"lm_status_code": {"order": "desc"}},
                    {"@timestamp": {"order": "desc"}},
                ]
                if prefer_latency
                else [
                    {"lm_status_code": {"order": "desc"}},
                    {"lm_request_time_ms": {"order": "desc"}},
                    {"@timestamp": {"order": "desc"}},
                ]
            ),
        }
        response = await self.es.search(
            index=",".join(matching_indices),
            body=body,
        )
        samples: list[AccessSample] = []
        for hit in response.get("hits", {}).get("hits", []):
            raw = hit.get("_source", {})
            method = str(raw.get("lm_http_method") or "").upper()[:16]
            route = str(raw.get("lm_route") or "")[:500]
            if not method or not route:
                method, route = normalize_request(raw.get("request"))
            request_time_ms = (
                safe_float(raw.get("lm_request_time_ms"))
                or safe_float(raw.get("request_time")) * 1000.0
            )
            upstream_time_raw = raw.get("lm_upstream_time_ms")
            if upstream_time_raw is None:
                upstream_time_raw = raw.get("upstream_response_time")
                upstream_time_ms = (
                    _last_numeric(upstream_time_raw) * 1000.0
                    if upstream_time_raw not in (None, "", "-")
                    else None
                )
            else:
                upstream_time_ms = safe_float(upstream_time_raw)

            status = safe_int(
                raw.get("lm_http_status_code", raw.get("status"))
            )
            upstream_status_value = safe_int(
                raw.get(
                    "lm_upstream_status_code",
                    raw.get("upstream_status"),
                )
            )
            samples.append(
                AccessSample(
                    timestamp=str(raw.get("@timestamp", ""))[:40],
                    method=method,
                    route=route,
                    status=status,
                    request_time_ms=request_time_ms,
                    upstream_status=upstream_status_value or None,
                    upstream_time_ms=upstream_time_ms,
                    upstream_addr=str(raw.get("upstream_addr") or "")[:120],
                    query_parameters=extract_query_parameter_names(
                        raw.get("request")
                    ),
                    body_fields=extract_body_field_names(
                        raw.get("request_body")
                    ),
                )
            )
        return samples

    async def cleanup_metrics(self, *, now: datetime | None = None) -> int:
        """Delete compact metrics beyond the configured retention period."""
        settings = get_settings()
        current = now or datetime.now(UTC)
        cutoff = current - timedelta(days=settings.http_access_metrics_retention_days)
        deleted = 0
        targets = (
            (settings.http_access_metrics_index, "minute"),
            (settings.http_access_route_metrics_index, "observed_at"),
        )
        for index_name, time_field in targets:
            if not await self.es.indices.exists(index=index_name):
                continue
            response = await self.es.delete_by_query(
                index=index_name,
                body={
                    "query": {
                        "range": {
                            time_field: {"lt": cutoff.isoformat()}
                        }
                    }
                },
                conflicts="proceed",
                refresh=False,
            )
            deleted += int(response.get("deleted", 0))
        return deleted

    async def install_ingest_pipeline(self) -> None:
        """
        Install, but do not attach, the canonical normalization pipeline.

        Attaching a default pipeline to existing organization-owned data-stream
        templates is intentionally an explicit deployment operation.
        """
        script_source = """
            def parseLongSafe(def value) {
                if (value == null) return null;
                String raw = value.toString().trim();
                if (raw.contains(',')) raw = raw.substring(raw.lastIndexOf(',') + 1).trim();
                if (raw.length() == 0 || raw == '-') return null;
                try { return Long.parseLong(raw); } catch (Exception ignored) { return null; }
            }
            def parseMsSafe(def value) {
                if (value == null) return null;
                String raw = value.toString().trim();
                if (raw.contains(',')) raw = raw.substring(raw.lastIndexOf(',') + 1).trim();
                if (raw.length() == 0 || raw == '-') return null;
                try { return Double.parseDouble(raw) * 1000.0; }
                catch (Exception ignored) { return null; }
            }
            ctx.lm_http_status_code = parseLongSafe(ctx.status);
            ctx.lm_upstream_status_code = parseLongSafe(ctx.upstream_status);
            ctx.lm_request_time_ms = parseMsSafe(ctx.request_time);
            ctx.lm_upstream_time_ms = parseMsSafe(ctx.upstream_response_time);
            if (ctx.request != null) {
                String request = ctx.request.toString().trim();
                String[] parts = request.splitOnToken(' ');
                if (parts.length >= 2) {
                    ctx.lm_http_method = parts[0].toUpperCase();
                    String path = parts[1];
                    int queryAt = path.indexOf('?');
                    if (queryAt >= 0) path = path.substring(0, queryAt);
                    int fragmentAt = path.indexOf('#');
                    if (fragmentAt >= 0) path = path.substring(0, fragmentAt);
                    if (!path.startsWith('/')) path = '/' + path;
                    boolean trailingSlash = path.endsWith("/");
                    String[] segments = path.splitOnToken('/');
                    StringBuilder normalized = new StringBuilder();
                    for (String segment : segments) {
                        if (segment.length() == 0) continue;
                        boolean numeric = true;
                        boolean hexId = segment.length() >= 16
                            && segment.length() <= 64;
                        for (int i = 0; i < segment.length(); i++) {
                            int code = (int)segment.charAt(i);
                            if (code < 48 || code > 57) {
                                numeric = false;
                            }
                            boolean hexChar = (code >= 48 && code <= 57)
                                || (code >= 65 && code <= 70)
                                || (code >= 97 && code <= 102);
                            if (!hexChar) {
                                hexId = false;
                            }
                        }
                        boolean uuid = segment.length() == 36
                            && segment.substring(8, 9).equals("-")
                            && segment.substring(13, 14).equals("-")
                            && segment.substring(18, 19).equals("-")
                            && segment.substring(23, 24).equals("-");
                        normalized.append('/');
                        normalized.append(
                            uuid ? '{uuid}' : numeric || hexId ? '{id}' : segment
                        );
                    }
                    ctx.lm_route = normalized.length() == 0
                        ? '/' : normalized.toString();
                    if (trailingSlash && !ctx.lm_route.equals("/")) {
                        ctx.lm_route += "/";
                    }
                }
            }
        """
        await self.es.ingest.put_pipeline(
            id=_INGEST_PIPELINE_ID,
            processors=[{"script": {"lang": "painless", "source": script_source}}],
            description=(
                "LogMind canonical numeric fields for Nginx/Ingress access logs"
            ),
        )

    async def install_canonical_mappings(self) -> None:
        """Add canonical fields to current backing indices without reindexing."""
        properties = {
            "lm_http_status_code": {"type": "long"},
            "lm_upstream_status_code": {"type": "long"},
            "lm_request_time_ms": {"type": "double"},
            "lm_upstream_time_ms": {"type": "double"},
            "lm_http_method": {"type": "keyword"},
            "lm_route": {"type": "keyword", "ignore_above": 500},
        }
        for index_name in get_settings().http_access_index_list:
            await self.es.indices.put_mapping(
                index=index_name,
                properties=properties,
            )


http_access_service = HttpAccessService()


def _last_numeric(value: object) -> float:
    raw = str(value or "").rsplit(",", 1)[-1].strip()
    return safe_float(raw)
