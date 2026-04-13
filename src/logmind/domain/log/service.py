"""
Log Domain — ES Query Service

Handles log retrieval, search, and aggregation from Elasticsearch.
Supports arbitrary index patterns (per user requirement: indexes named by site).
Supports GYYX Filebeat log format with gy.* fields and data-stream indices.
"""

import json
import re
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

# gy.filetype → standard log level mapping
_FILETYPE_LEVEL_MAP: dict[str, str] = {
    "error.log": "error",
    "info.log": "info",
    "warn.log": "warning",
    "warning.log": "warning",
    "debug.log": "debug",
    "trace.log": "debug",
}

# Reverse mapping: severity → filetype values (for ES filter)
_SEVERITY_FILETYPE_MAP: dict[str, list[str]] = {
    "error": ["error.log"],
    "warning": ["warn.log", "warning.log"],
    "info": ["info.log"],
    "debug": ["debug.log", "trace.log"],
}

# Regex to extract level from message content like "[ERROR]", "[WARN]" etc.
_MSG_LEVEL_RE = re.compile(
    r"\[(ERROR|WARN|WARNING|INFO|DEBUG|CRITICAL|FATAL|TRACE)\]",
    re.IGNORECASE,
)


class LogService:
    """Elasticsearch log query and aggregation service."""

    def __init__(self):
        self.es = get_es_client()

    async def search_logs(self, request: LogQueryRequest) -> LogQueryResponse:
        """
        Search logs from ES with flexible filtering.
        Supports arbitrary index patterns (named by site, etc.)
        Compatible with both kubernetes.* and gy.* field formats.
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
        # Supports: level, log.level, severity, loglevel, AND gy.filetype
        if request.severity:
            severity_should = [
                {"term": {"level": request.severity}},
                {"term": {"log.level": request.severity}},
                {"term": {"severity": request.severity}},
                {"term": {"loglevel": request.severity.upper()}},
            ]
            # Also match gy.filetype for GYYX format
            filetype_values = _SEVERITY_FILETYPE_MAP.get(request.severity.lower(), [])
            for ft in filetype_values:
                severity_should.append({"term": {"gy.filetype": ft}})

            filter_clauses.append({
                "bool": {
                    "should": severity_should,
                    "minimum_should_match": 1,
                }
            })

        # K8s metadata filters (backward compatible)
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

        # GYYX gy.* field filters
        if request.domain:
            filter_clauses.append(
                {"term": {"gy.domain": request.domain}}
            )
        if request.filetype:
            filter_clauses.append(
                {"term": {"gy.filetype": request.filetype}}
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
            gy_meta = self._extract_gy_metadata(source)
            logs.append(LogEntry(
                id=hit["_id"],
                timestamp=source.get("@timestamp", ""),
                level=self._extract_level(source),
                message=self._extract_message(source),
                source=source,
                kubernetes=source.get("kubernetes", {}),
                raw=source,
                # GYYX metadata
                domain=gy_meta.get("domain", ""),
                pod_name=gy_meta.get("pod_name", ""),
                branch=gy_meta.get("branch", ""),
                image_version=gy_meta.get("image_version", ""),
                filetype=gy_meta.get("filetype", ""),
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
                "by_domain": {
                    "terms": {
                        "field": "gy.domain",
                        "size": 50,
                    }
                },
                "by_filetype": {
                    "terms": {
                        "field": "gy.filetype",
                        "size": 10,
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
            by_domain=[
                LogAggregation(key=b["key"], count=b["doc_count"])
                for b in aggs.get("by_domain", {}).get("buckets", [])
            ],
            by_filetype=[
                LogAggregation(key=b["key"], count=b["doc_count"])
                for b in aggs.get("by_filetype", {}).get("buckets", [])
            ],
            time_histogram=[
                {"time": b["key_as_string"], "count": b["doc_count"]}
                for b in aggs.get("time_histogram", {}).get("buckets", [])
            ],
        )

    async def list_indices(self, pattern: str = "*") -> list[ESIndexInfo]:
        """
        List ES indices matching a pattern.
        Supports both regular indices and .ds-* data stream backing indices.
        """
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
                or idx.get("index", "").startswith(".ds-")  # Keep data stream indices
            ]
        except Exception as e:
            logger.error("list_indices_failed", error=str(e))
            return []

    # ── Helpers ──────────────────────────────────────────

    @staticmethod
    def _extract_level(source: dict) -> str:
        """
        Extract log level from varied field names.
        Priority:
          1. Dedicated fields: level, log.level, severity, loglevel
          2. GYYX gy.filetype mapping (error.log → error, etc.)
          3. Regex from message content ([ERROR], [WARN], etc.)
        """
        # 1. Dedicated level fields
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

        # 2. GYYX gy.filetype mapping
        gy = source.get("gy", {})
        if isinstance(gy, dict):
            filetype = gy.get("filetype", "")
            if filetype in _FILETYPE_LEVEL_MAP:
                return _FILETYPE_LEVEL_MAP[filetype]

        # 3. Parse from message content
        message = source.get("message", "")
        if isinstance(message, str):
            match = _MSG_LEVEL_RE.search(message)
            if match:
                level_str = match.group(1).lower()
                if level_str in ("warn", "warning"):
                    return "warning"
                if level_str == "fatal":
                    return "critical"
                if level_str == "trace":
                    return "debug"
                return level_str

        return ""

    @staticmethod
    def _extract_message(source: dict) -> str:
        """Extract message from varied field names."""
        for field in ["message", "msg", "log", "content"]:
            if field in source and isinstance(source[field], str):
                return source[field]
        return json.dumps(source, ensure_ascii=False)[:500]

    @staticmethod
    def _extract_gy_metadata(source: dict) -> dict:
        """
        Extract GYYX business metadata from gy.* fields.

        Fields:
          gy.domain   → site domain name
          gy.podname  → pod name with version suffix
          gy.branch   → code branch (master=prod, develop=test)
          gy.filetype → log file type (error.log, info.log, etc.)
          image.version → container image version
        """
        gy = source.get("gy", {})
        if not isinstance(gy, dict):
            return {}

        image = source.get("image", {})
        if not isinstance(image, dict):
            image = {}

        return {
            "domain": gy.get("domain", ""),
            "pod_name": gy.get("podname", ""),
            "branch": gy.get("branch", ""),
            "filetype": gy.get("filetype", ""),
            "image_version": image.get("version", ""),
        }


# Singleton
log_service = LogService()
