"""
Log Domain — ES Query Service

Handles log retrieval, search, and aggregation from Elasticsearch.
Supports arbitrary index patterns (per user requirement: indexes named by site).
Supports GYYX Filebeat log format with gy.* fields and data-stream indices.

Language-aware log parsing:
  - Java (K8s): level from gy.filetype (error.log, info.log, etc.)
  - C# (Windows VM): level from message content (NLog/log4net format)
"""

import json
import re
from datetime import UTC, datetime

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

# ── Java & Filebeat: gy.filetype → standard log level mapping ──────
_FILETYPE_LEVEL_MAP: dict[str, str] = {
    "error.log": "error",
    "info.log": "info",
    "warn.log": "warning",
    "warning.log": "warning",
    "debug.log": "debug",
    "trace.log": "debug",
    "error.log.txt": "error",
    "warn.log.txt": "warning",
    "warning.log.txt": "warning",
    "info.log.txt": "info",
    "debug.log.txt": "debug",
    "trace.log.txt": "debug",
}

# Reverse mapping for severity → filetype ES filter
# NOTE: warn.log is included in error mapping because developers
# frequently log real exceptions at WARN level (e.g. Spring's
# DataIntegrityViolationException). QualityFilter handles noise.
_SEVERITY_FILETYPE_MAP: dict[str, list[str]] = {
    "error": [
        "error.log",
        "warn.log",
        "error.log.txt",
        "warn.log.txt",
        "Error.Log.txt",
        "Warn.Log.txt",
    ],
    "warning": [
        "warn.log",
        "warning.log",
        "warn.log.txt",
        "warning.log.txt",
        "Warn.Log.txt",
    ],
    "info": ["info.log", "info.log.txt", "Info.Log.txt"],
    "debug": ["debug.log", "trace.log", "debug.log.txt", "Debug.Log.txt"],
}

# ── C# NLog/log4net filetypes (mixed-level log files) ───
# These files contain ALL levels in one file; a filetype match alone must never
# be treated as a severity match. Their level is determined from structured
# fields or message markers.
_MIXED_LEVEL_FILETYPES: set[str] = {
    "sys.log.txt",
    "sys.log",
    "app.log.txt",
    "application.log",
}

# ── Level extraction regex patterns ─────────────────────
# Pattern 1: Level in brackets — [ERROR], [WARN], [INFO]
_BRACKET_LEVEL_RE = re.compile(
    r"\[(ERROR|WARN|WARNING|INFO|DEBUG|CRITICAL|FATAL|TRACE)\]",
    re.IGNORECASE,
)

# Pattern 2: C# NLog/log4net — level as standalone word after timestamp and thread
# Matches: "2026-04-13 19:09:56,856 [155] DEBUG Gyyx.Core..."
# Also:    "2026-04-13 19:09:56,856 [155] ERROR Gyyx.Core..."
_NLOG_LEVEL_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[,.\d]*\s+"  # timestamp
    r"\[[\w\-]+\]\s+"  # [thread_id]
    r"(ERROR|WARN|WARNING|INFO|DEBUG|CRITICAL|FATAL|TRACE)\b",  # LEVEL
    re.IGNORECASE,
)

# Pattern 3: Serilog compact levels — [ERR], [WRN], [INF], [DBG], [FTL], [VRB]
_SERILOG_LEVEL_RE = re.compile(
    r"\[(ERR|WRN|INF|DBG|FTL|VRB)\]",
    re.IGNORECASE,
)

# Pattern 4: Microsoft.Extensions.Logging console format — fail:/crit:/warn:/info:/dbug:/trce:
_DOTNET_CONSOLE_LEVEL_RE = re.compile(
    r"^\s*(fail|crit|warn|info|dbug|trce):\s",
    re.IGNORECASE | re.MULTILINE,
)

# Pattern 3: Java Logback/Log4j2 — level in message (sometimes)
# Matches: "[2026-04-13 21:49:48.488] ... [ERROR] ..."
_JAVA_MSG_LEVEL_RE = re.compile(
    r"\[(ERROR|WARN|WARNING|INFO|DEBUG|CRITICAL|FATAL|TRACE)\]",
    re.IGNORECASE,
)

# Severity keyword mapping for ES message-level query
_SEVERITY_MSG_KEYWORDS: dict[str, list[str]] = {
    "error": ["ERROR", "FATAL", "CRITICAL"],
    "warning": ["WARN", "WARNING"],
    "info": ["INFO"],
    "debug": ["DEBUG", "TRACE"],
}

_QUERY_STRING_SPECIAL_RE = re.compile(r'([+\-=&|><!(){}\[\]^"~*?:\\/])')


def _escape_query_string(value: str) -> str:
    """Escape ES query_string special chars while preserving CJK punctuation."""
    return _QUERY_STRING_SPECIAL_RE.sub(r"\\\1", value)


def build_base_severity_filter(
    severity: str,
    *,
    language: str | None = None,
) -> dict:
    """Build the static portion of the canonical ES severity predicate."""
    normalized = (severity or "").lower()
    if normalized == "critical":
        normalized = "error"

    level_values = {
        "error": ["error", "ERROR", "fatal", "FATAL", "critical", "CRITICAL"],
        "warning": ["warning", "WARNING", "warn", "WARN"],
        "info": ["info", "INFO"],
        "debug": ["debug", "DEBUG", "trace", "TRACE"],
    }.get(normalized, [normalized, normalized.upper()])

    severity_should: list[dict] = []
    for field in ("level", "level.keyword", "log.level", "severity", "loglevel"):
        for value in level_values:
            severity_should.append({"term": {field: value}})

    def keyword_marker(value: str) -> dict:
        # On a text field match_phrase analyzes punctuation away, so
        # "[ERROR]" degenerates into the bare token "error".
        return {
            "wildcard": {
                "message.keyword": {
                    "value": value,
                    "case_insensitive": True,
                }
            }
        }

    # Filename is a fallback, not authority over an explicit message level.
    explicit = [
        {"exists": {"field": field}} for field in ("level", "log.level", "severity", "loglevel")
    ]
    explicit += [
        keyword_marker(marker)
        for marker in (
            "*[ERROR]*",
            "*[INFO]*",
            "*[DEBUG]*",
            "*[WARN]*",
            "*[TRACE]*",
            "*[FATAL]*",
            "*[CRITICAL]*",
            "*[ERR]*",
            "*[INF]*",
            "*[DBG]*",
            "*[WRN]*",
            "*[FTL]*",
            "*[TRC]*",
            "*] ERROR *",
            "*] INFO *",
            "*] DEBUG *",
            "*] WARN *",
            "*] FATAL *",
            "ERROR *",
            "INFO *",
            "DEBUG *",
            "WARN *",
            "FATAL *",
        )
    ]
    for filetype in dict.fromkeys(f.lower() for f in _SEVERITY_FILETYPE_MAP.get(normalized, [])):
        severity_should.append(
            {
                "bool": {
                    "filter": [
                        {
                            "term": {
                                "gy.filetype.keyword": {"value": filetype, "case_insensitive": True}
                            }
                        }
                    ],
                    "must_not": explicit,
                }
            }
        )

    message_markers = {
        "error": [
            "*[ERROR]*",
            "*[FATAL]*",
            "*[CRITICAL]*",
            "*] ERROR *",
            "*] FATAL *",
            "*[ERR]*",
            "*[FTL]*",
            "ERROR *",
            "FATAL *",
            "fail:*",
            "crit:*",
        ],
        "warning": [
            "*[WARN]*",
            "*[WARNING]*",
            "*] WARN *",
            "*] WARNING *",
            "*[WRN]*",
            "WARN *",
            "warn:*",
        ],
        "info": ["*[INFO]*", "*] INFO *", "*[INF]*", "INFO *", "info:*"],
        "debug": ["*[DEBUG]*", "*] DEBUG *", "*[DBG]*", "DEBUG *", "dbug:*", "*[TRACE]*", "trce:*"],
    }.get(normalized, [])
    for marker in message_markers:
        severity_should.append(keyword_marker(marker))

    return {
        "bool": {
            "should": severity_should,
            "minimum_should_match": 1,
        }
    }


async def build_severity_filter(
    severity: str,
    *,
    business_line_id: str = "",
    language: str | None = None,
) -> dict:
    """Build the canonical ES severity predicate used by search and statistics."""
    predicate = build_base_severity_filter(severity, language=language)
    if (severity or "").lower() in {"error", "critical"}:
        from logmind.domain.log.error_signals import get_all_error_signals

        for signal in await get_all_error_signals(business_line_id):
            predicate["bool"]["should"].append({"match_phrase": {"message": signal}})
    return predicate


class LogService:
    """Elasticsearch log query and aggregation service."""

    def __init__(self):
        pass

    @property
    def es(self):
        from logmind.core.elasticsearch import get_es_client

        return get_es_client()

    async def search_logs(self, request: LogQueryRequest) -> LogQueryResponse:
        """
        Search logs from ES with flexible filtering.
        Supports arbitrary index patterns (named by site, etc.)
        Compatible with both kubernetes.* and gy.* field formats.
        Language-aware severity filtering for Java and C# log formats.
        """
        if not request.index_pattern:
            raise ValueError("index_pattern is required")

        must_clauses = []
        filter_clauses = []

        # Time range
        filter_clauses.append(
            {
                "range": {
                    "@timestamp": {
                        "gte": request.time_from.isoformat(),
                        "lte": request.time_to.isoformat(),
                    }
                }
            }
        )

        # Free text search — use match_phrase for CJK reliability
        # multi_match with phrase_prefix is unreliable for Chinese text
        if request.query:
            escaped_query = _escape_query_string(request.query)
            must_clauses.append(
                {
                    "bool": {
                        "should": [
                            # Strategy 1: match_phrase on message (most reliable for CJK)
                            {"match_phrase": {"message": request.query}},
                            # Strategy 2: keyword wildcard when message.keyword exists
                            {
                                "wildcard": {
                                    "message.keyword": {
                                        "value": f"*{request.query}*",
                                        "case_insensitive": True,
                                    }
                                }
                            },
                            # Strategy 3: escaped query_string wildcard (catches partial matches)
                            {
                                "query_string": {
                                    "query": f"*{escaped_query}*",
                                    "fields": ["message"],
                                    "analyze_wildcard": True,
                                }
                            },
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )

        # ── Severity filter — language-aware ─────────────
        if request.severity:
            filter_clauses.append(
                await build_severity_filter(
                    request.severity,
                    business_line_id=request.business_line_id or "",
                    language=request.language,
                )
            )

        # K8s metadata filters (backward compatible)
        if request.namespace:
            filter_clauses.append({"term": {"kubernetes.namespace": request.namespace}})
        if request.pod_name:
            filter_clauses.append({"wildcard": {"kubernetes.pod.name": f"*{request.pod_name}*"}})
        if request.container_name:
            filter_clauses.append({"term": {"kubernetes.container.name": request.container_name}})

        # GYYX gy.* field filters
        if request.domain:
            if "." in request.domain:
                # Exact domain like "stage-account-login-service.gyyx.cn"
                filter_clauses.append({"term": {"gy.domain.keyword": request.domain}})
            else:
                # Fuzzy domain like "login" — use wildcard
                filter_clauses.append({"wildcard": {"gy.domain.keyword": f"*{request.domain}*"}})
        if request.filetype:
            filter_clauses.append({"term": {"gy.filetype.keyword": request.filetype}})

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
            "track_total_hits": True,
            "_source": True,
        }

        result = await self.es.search(index=request.index_pattern, body=body)

        logs = []
        for hit in result["hits"]["hits"]:
            source = hit["_source"]
            source["_es_index"] = hit.get("_index", request.index_pattern)
            source["_es_id"] = hit["_id"]
            gy_meta = self._extract_gy_metadata(source)
            logs.append(
                LogEntry(
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
                    host_name=gy_meta.get("host_name", ""),
                )
            )

        return LogQueryResponse(
            total=result["hits"]["total"]["value"],
            logs=logs,
            took_ms=result.get("took", 0),
        )

    async def get_log_stats(
        self,
        index_pattern: str,
        time_from: datetime,
        time_to: datetime,
        *,
        severity: str | None = None,
        business_line_id: str = "",
        language: str | None = None,
    ) -> LogStatsResponse:
        """Get log statistics with aggregations."""
        filters = [
            {
                "range": {
                    "@timestamp": {
                        "gte": time_from.isoformat(),
                        "lte": time_to.isoformat(),
                    }
                }
            }
        ]
        if severity:
            filters.append(
                await build_severity_filter(
                    severity,
                    business_line_id=business_line_id,
                    language=language,
                )
            )

        body = {
            "query": {"bool": {"filter": filters}},
            "size": 0,
            "track_total_hits": True,
            "aggs": {
                "by_level": {
                    "terms": {
                        "field": "level",
                        "size": 10,
                        "missing": "unknown",
                    }
                },
                "by_namespace": {
                    "terms": {
                        "field": "kubernetes.namespace.keyword",
                        "size": 20,
                    }
                },
                "by_domain": {
                    "terms": {
                        "field": "gy.domain.keyword",
                        "size": 50,
                    }
                },
                "by_filetype": {
                    "terms": {
                        "field": "gy.filetype.keyword",
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

    # ── RAG Vector Search (Knowledge Base) ───────────

    async def create_kb_index_if_not_exists(self, kb_id: str, vector_dim: int = 1536) -> str:
        """Create an Elasticsearch index for storing knowledge base embeddings."""
        index_name = f"logmind-kb-{kb_id}"
        exists = await self.es.indices.exists(index=index_name)
        if not exists:
            mapping = {
                "properties": {
                    "doc_id": {"type": "keyword"},
                    "kb_id": {"type": "keyword"},
                    "content": {"type": "text"},
                    "metadata": {"type": "object"},
                    "chunk_index": {"type": "integer"},
                    "embedding": {
                        "type": "dense_vector",
                        "dims": vector_dim,
                        "index": True,
                        "similarity": "cosine",
                    },
                }
            }
            await self.es.indices.create(index=index_name, mappings=mapping)
            logger.info("kb_index_created", index=index_name)
        return index_name

    async def insert_chunks(self, index_name: str, chunks: list[dict]):
        """Bulk insert embedding chunks into ES index."""
        from elasticsearch.helpers import async_bulk

        actions = [{"_index": index_name, "_source": chunk} for chunk in chunks]
        success, failed = await async_bulk(self.es, actions)
        logger.info(
            "kb_chunks_inserted",
            index=index_name,
            success=success,
            failed=len(failed) if failed else 0,
        )
        return success

    async def knn_search(self, kb_id: str, query_vector: list[float], k: int = 3) -> list[dict]:
        """Perform KNN search on knowledge base index."""
        index_name = f"logmind-kb-{kb_id}"
        exists = await self.es.indices.exists(index=index_name)
        if not exists:
            return []

        try:
            resp = await self.es.search(
                index=index_name,
                knn={
                    "field": "embedding",
                    "query_vector": query_vector,
                    "k": k,
                    "num_candidates": 100,
                },
                source=["content", "metadata", "doc_id"],
            )
            hits = resp.get("hits", {}).get("hits", [])
            results = []
            for hit in hits:
                source = hit["_source"]
                results.append(
                    {
                        "score": hit["_score"],
                        "content": source.get("content"),
                        "metadata": source.get("metadata"),
                        "doc_id": source.get("doc_id"),
                    }
                )
            return results
        except Exception as e:
            logger.error("knn_search_failed", kb_id=kb_id, error=str(e))
            return []

    # ── Analysis Vector Index (Phase 3 Semantic Dedup) ──

    async def create_analysis_vector_index(self, vector_dim: int = 1536) -> str:
        """Create ES index for storing analysis result embeddings (semantic dedup)."""
        index_name = "logmind-analysis-vectors"
        exists = await self.es.indices.exists(index=index_name)
        if not exists:
            mapping = {
                "properties": {
                    "business_line_id": {"type": "keyword"},
                    "error_signature": {"type": "text"},
                    "analysis_content": {"type": "text"},
                    "severity": {"type": "keyword"},
                    "task_id": {"type": "keyword"},
                    "embedding": {
                        "type": "dense_vector",
                        "dims": vector_dim,
                        "index": True,
                        "similarity": "cosine",
                    },
                    "created_at": {"type": "date"},
                    "ttl_expire_at": {"type": "date"},
                    # ── Known Issue Library fields ──────────
                    "status": {"type": "keyword"},  # open / resolved / ignored
                    "hit_count": {"type": "integer"},  # cumulative match count
                    "first_seen": {"type": "date"},  # first time this issue was seen
                    "last_seen": {"type": "date"},  # last time this issue was matched
                    "resolved_at": {"type": "date"},  # when issue was marked resolved
                    "feedback_quality": {"type": "keyword"},  # verified / poor / null
                }
            }
            await self.es.indices.create(index=index_name, mappings=mapping)
            logger.info("analysis_vector_index_created", index=index_name)
        return index_name

    async def insert_analysis_vector(self, doc: dict) -> bool:
        """Insert a single analysis embedding vector into the index."""
        index_name = "logmind-analysis-vectors"
        try:
            await self.create_analysis_vector_index()
            await self.es.index(index=index_name, body=doc)
            logger.info("analysis_vector_inserted", task_id=doc.get("task_id"))
            return True
        except Exception as e:
            logger.error("analysis_vector_insert_failed", error=str(e))
            return False

    async def knn_search_analysis_history(
        self,
        business_line_id: str,
        query_vector: list[float],
        k: int = 1,
        min_score: float = 0.92,
    ) -> list[dict]:
        """
        KNN search for historically analyzed errors matching the given embedding.

        Returns matches above min_score with their analysis conclusions.
        Filters by business_line_id, excludes expired and poor-quality records.
        """
        index_name = "logmind-analysis-vectors"
        exists = await self.es.indices.exists(index=index_name)
        if not exists:
            return []

        try:
            from datetime import datetime

            now_iso = datetime.now(UTC).isoformat()

            resp = await self.es.search(
                index=index_name,
                knn={
                    "field": "embedding",
                    "query_vector": query_vector,
                    "k": k,
                    "num_candidates": 50,
                    "filter": {
                        "bool": {
                            "must": [
                                {"term": {"business_line_id": business_line_id}},
                                {"range": {"ttl_expire_at": {"gte": now_iso}}},
                            ],
                            "must_not": [
                                # Exclude entries marked as poor quality by feedback
                                {"term": {"feedback_quality": "poor"}},
                                {"term": {"status": "ignored"}},
                            ],
                        }
                    },
                },
                source=[
                    "analysis_content",
                    "severity",
                    "error_signature",
                    "task_id",
                    "created_at",
                    "status",
                    "hit_count",
                    "first_seen",
                    "last_seen",
                    "resolved_at",
                    "feedback_quality",
                ],
                min_score=min_score,
            )
            hits = resp.get("hits", {}).get("hits", [])
            results = []
            for hit in hits:
                source = hit["_source"]
                results.append(
                    {
                        "doc_id": hit["_id"],
                        "score": hit["_score"],
                        "analysis_content": source.get("analysis_content", ""),
                        "severity": source.get("severity", "info"),
                        "error_signature": source.get("error_signature", ""),
                        "task_id": source.get("task_id", ""),
                        "created_at": source.get("created_at", ""),
                        "status": source.get("status", "open"),
                        "hit_count": source.get("hit_count", 1),
                        "first_seen": source.get("first_seen", ""),
                        "last_seen": source.get("last_seen", ""),
                        "resolved_at": source.get("resolved_at"),
                        "feedback_quality": source.get("feedback_quality"),
                    }
                )
            return results
        except Exception as e:
            logger.error("knn_search_analysis_history_failed", error=str(e))
            return []

    async def update_analysis_vector_hit(self, doc_id: str, ttl_hours: int = 168) -> bool:
        """
        Update a known issue's hit_count and last_seen on match.
        Also renews TTL to prevent expiration of frequently-seen issues.
        """
        index_name = "logmind-analysis-vectors"
        try:
            from datetime import datetime, timedelta

            now = datetime.now(UTC)
            new_expire = now + timedelta(hours=ttl_hours)

            await self.es.update(
                index=index_name,
                id=doc_id,
                body={
                    "script": {
                        "source": """
                            ctx._source.hit_count = (ctx._source.hit_count ?: 0) + 1;
                            ctx._source.last_seen = params.now;
                            ctx._source.ttl_expire_at = params.new_expire;
                        """,
                        "params": {
                            "now": now.isoformat(),
                            "new_expire": new_expire.isoformat(),
                        },
                    }
                },
            )
            return True
        except Exception as e:
            logger.warning("analysis_vector_hit_update_failed", doc_id=doc_id, error=str(e))
            return False

    async def update_analysis_vector_status(
        self, doc_id: str, status: str | None, feedback_quality: str | None = None
    ) -> bool:
        """
        Update a known issue's status or feedback quality.

        Used by feedback API to mark issues as verified/poor.
        """
        index_name = "logmind-analysis-vectors"
        try:
            from datetime import datetime

            update_fields = {}
            if status is not None:
                update_fields["status"] = status
            if status == "resolved":
                update_fields["resolved_at"] = datetime.now(UTC).isoformat()
            if feedback_quality is not None:
                update_fields["feedback_quality"] = feedback_quality

            # If verified, extend TTL to 365 days (effectively permanent)
            if feedback_quality == "verified":
                from datetime import timedelta

                update_fields["ttl_expire_at"] = (
                    datetime.now(UTC) + timedelta(days=365)
                ).isoformat()

            await self.es.update(
                index=index_name,
                id=doc_id,
                body={"doc": update_fields},
            )
            return True
        except Exception as e:
            logger.warning("analysis_vector_status_update_failed", doc_id=doc_id, error=str(e))
            return False

    # ── Helpers ──────────────────────────────────────────

    @staticmethod
    def _extract_level(source: dict) -> str:
        """
        Extract log level from varied field names and formats.

        Priority:
          1. Dedicated fields: level, log.level, severity, loglevel
          2. Inner JSON and message prefix (NLog, brackets, Serilog)
          3. Case-insensitive filename fallback
          4. Message keyword fallback
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
                return _normalize_level(str(val))

        # 3. C# NLog/log4net: parse from message content
        message = source.get("message", "")
        if isinstance(message, str):
            from logmind.domain.log.csharp import parse_dotnet

            inner_level = parse_dotnet(message).level
            if inner_level:
                return _normalize_level(inner_level)
            # Try NLog format first (most specific pattern)
            match = _NLOG_LEVEL_RE.search(message)
            if match:
                return _normalize_level(match.group(1))

            # Then try bracket/Serilog/.NET console formats
            match = _BRACKET_LEVEL_RE.search(message)
            if match:
                return _normalize_level(match.group(1))
            match = _SERILOG_LEVEL_RE.search(message)
            if match:
                return _normalize_level(match.group(1))
            match = _DOTNET_CONSOLE_LEVEL_RE.search(message)
            if match:
                return _normalize_level(match.group(1))

        # 4. Filename is used only when structured/message levels are absent.
        gy = source.get("gy", {})
        if isinstance(gy, dict):
            filetype = str(gy.get("filetype") or "").lower()
            if filetype in _FILETYPE_LEVEL_MAP:
                return _FILETYPE_LEVEL_MAP[filetype]

        # 5. Fallback: log level keywords in message
        if isinstance(message, str):
            for level, keywords in _SEVERITY_MSG_KEYWORDS.items():
                for kw in keywords:
                    if kw in message:
                        return level

        return "unknown"

    @staticmethod
    def _extract_message(source: dict) -> str:
        """Extract message from varied field names."""
        for field in ["message", "msg", "log", "content"]:
            if field in source and isinstance(source[field], str):
                return source[field]
        return json.dumps(source, ensure_ascii=False)[:500]

    @staticmethod
    def _extract_gy_metadata(source: dict) -> dict:
        from logmind.domain.log.events import metadata

        return metadata(source)


def _normalize_level(raw: str) -> str:
    """Normalize varied level strings to standard values."""
    upper = raw.strip().upper()
    level_map = {
        "ERROR": "error",
        "ERR": "error",
        "FAIL": "error",
        "FATAL": "critical",
        "FTL": "critical",
        "CRITICAL": "critical",
        "CRIT": "critical",
        "WARN": "warning",
        "WRN": "warning",
        "WARNING": "warning",
        "INFO": "info",
        "INF": "info",
        "INFORMATION": "info",
        "DEBUG": "debug",
        "DBG": "debug",
        "DBUG": "debug",
        "TRACE": "debug",
        "TRCE": "debug",
        "VERBOSE": "debug",
        "VRB": "debug",
    }
    return level_map.get(upper, raw.lower())


# Singleton
log_service = LogService()
