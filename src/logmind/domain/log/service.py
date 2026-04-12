"""
Log Domain — ES Query Service

Handles log retrieval, search, and aggregation from Elasticsearch.
Supports arbitrary index patterns (per user requirement: indexes named by site).
"""

import json
from datetime import datetime

from logmind.core.elasticsearch import get_es_client
from logmind.core.logging import get_logger
from logmind.domain.log.schemas import (
    ESIndexInfo,
    LogAggregation,
    LogEntry,
    LogQueryRequest,
    LogQueryResponse,
    LogStatsResponse,
)

logger = get_logger(__name__)


class LogService:
    """Elasticsearch log query and aggregation service."""

    def __init__(self):
        self.es = get_es_client()

    async def search_logs(self, request: LogQueryRequest) -> LogQueryResponse:
        """
        Search logs from ES with flexible filtering.
        Supports arbitrary index patterns (named by site, etc.)
        """
        must_clauses = []
        filter_clauses = []

        # Time range
        filter_clauses.append({
            "range": {
                "@timestamp": {
                    "gte": request.time_from.isoformat(),
                    "lte": request.time_to.isoformat(),
                }
            }
        })

        # Free text search
        if request.query:
            must_clauses.append({
                "multi_match": {
                    "query": request.query,
                    "fields": ["message", "log", "msg", "content"],
                    "type": "phrase_prefix",
                }
            })

        # Severity filter — flexible field matching for varied log formats
        if request.severity:
            filter_clauses.append({
                "bool": {
                    "should": [
                        {"term": {"level": request.severity}},
                        {"term": {"log.level": request.severity}},
                        {"term": {"severity": request.severity}},
                        {"term": {"loglevel": request.severity.upper()}},
                    ],
                    "minimum_should_match": 1,
                }
            })

        # K8s metadata filters
        if request.namespace:
            filter_clauses.append(
                {"term": {"kubernetes.namespace": request.namespace}}
            )
        if request.pod_name:
            filter_clauses.append(
                {"wildcard": {"kubernetes.pod.name": f"*{request.pod_name}*"}}
            )
        if request.container_name:
            filter_clauses.append(
                {"term": {"kubernetes.container.name": request.container_name}}
            )

        # Extra filters from business line config
        for field, value in request.extra_filters.items():
            if isinstance(value, str) and "*" in value:
                filter_clauses.append({"wildcard": {field: value}})
            else:
                filter_clauses.append({"term": {field: value}})

        body = {
            "query": {
                "bool": {
                    "must": must_clauses or [{"match_all": {}}],
                    "filter": filter_clauses,
                }
            },
            "sort": [{"@timestamp": {"order": request.sort_order}}],
            "size": request.size,
            "_source": True,
        }

        result = await self.es.search(
            index=request.index_pattern, body=body
        )

        logs = []
        for hit in result["hits"]["hits"]:
            source = hit["_source"]
            logs.append(LogEntry(
                id=hit["_id"],
                timestamp=source.get("@timestamp", ""),
                level=self._extract_level(source),
                message=self._extract_message(source),
                source=source,
                kubernetes=source.get("kubernetes", {}),
                raw=source,
            ))

        return LogQueryResponse(
            total=result["hits"]["total"]["value"],
            logs=logs,
            took_ms=result.get("took", 0),
        )

    async def get_log_stats(
        self, index_pattern: str, time_from: datetime, time_to: datetime
    ) -> LogStatsResponse:
        """Get log statistics with aggregations."""
        body = {
            "query": {
                "range": {
                    "@timestamp": {
                        "gte": time_from.isoformat(),
                        "lte": time_to.isoformat(),
                    }
                }
            },
            "size": 0,
            "aggs": {
                "by_level": {
                    "terms": {
                        "field": "level",
                        "size": 10,
                    }
                },
                "by_namespace": {
                    "terms": {
                        "field": "kubernetes.namespace",
                        "size": 20,
                    }
                },
                "time_histogram": {
                    "date_histogram": {
                        "field": "@timestamp",
                        "fixed_interval": "5m",
                    }
                },
            },
        }

        result = await self.es.search(index=index_pattern, body=body)
        aggs = result.get("aggregations", {})

        return LogStatsResponse(
            total_logs=result["hits"]["total"]["value"],
            by_level=[
                LogAggregation(key=b["key"], count=b["doc_count"])
                for b in aggs.get("by_level", {}).get("buckets", [])
            ],
            by_namespace=[
                LogAggregation(key=b["key"], count=b["doc_count"])
                for b in aggs.get("by_namespace", {}).get("buckets", [])
            ],
            time_histogram=[
                {"time": b["key_as_string"], "count": b["doc_count"]}
                for b in aggs.get("time_histogram", {}).get("buckets", [])
            ],
        )

    async def list_indices(self, pattern: str = "*") -> list[ESIndexInfo]:
        """List ES indices matching a pattern."""
        try:
            indices = await self.es.cat.indices(
                index=pattern, format="json", h="index,docs.count,store.size,status"
            )
            return [
                ESIndexInfo(
                    name=idx.get("index", ""),
                    docs_count=int(idx.get("docs.count", 0) or 0),
                    size=idx.get("store.size", "0b"),
                    status=idx.get("status", "unknown"),
                )
                for idx in indices
                if not idx.get("index", "").startswith(".")
            ]
        except Exception as e:
            logger.error("list_indices_failed", error=str(e))
            return []

    # ── Helpers ──────────────────────────────────────────

    @staticmethod
    def _extract_level(source: dict) -> str:
        """Extract log level from varied field names."""
        for field in ["level", "log.level", "severity", "loglevel"]:
            parts = field.split(".")
            val = source
            for p in parts:
                if isinstance(val, dict):
                    val = val.get(p)
                else:
                    val = None
                    break
            if val:
                return str(val).lower()
        return ""

    @staticmethod
    def _extract_message(source: dict) -> str:
        """Extract message from varied field names."""
        for field in ["message", "msg", "log", "content"]:
            if field in source and isinstance(source[field], str):
                return source[field]
        return json.dumps(source, ensure_ascii=False)[:500]


# Singleton
log_service = LogService()
