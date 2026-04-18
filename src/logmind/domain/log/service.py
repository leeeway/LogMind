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

# ── Java: gy.filetype → standard log level mapping ──────
_FILETYPE_LEVEL_MAP: dict[str, str] = {
    "error.log": "error",
    "info.log": "info",
    "warn.log": "warning",
    "warning.log": "warning",
    "debug.log": "debug",
    "trace.log": "debug",
}

# Java: reverse mapping for severity → filetype ES filter
_SEVERITY_FILETYPE_MAP: dict[str, list[str]] = {
    "error": ["error.log"],
    "warning": ["warn.log", "warning.log"],
    "info": ["info.log"],
    "debug": ["debug.log", "trace.log"],
}

# ── C# NLog/log4net filetypes (mixed-level log files) ───
# These files contain ALL levels in one file; level is embedded in message
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
    r"\[[\w\-]+\]\s+"                                       # [thread_id]
    r"(ERROR|WARN|WARNING|INFO|DEBUG|CRITICAL|FATAL|TRACE)\b",  # LEVEL
    re.IGNORECASE,
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

        # ── Severity filter — language-aware ─────────────
        if request.severity:
            severity_should = [
                # Standard level fields (exact term match, no false positives)
                {"term": {"level": request.severity}},
                {"term": {"log.level": request.severity}},
                {"term": {"severity": request.severity}},
                {"term": {"loglevel": request.severity.upper()}},
            ]

            # Java: match gy.filetype (error.log, info.log, etc.)
            filetype_values = _SEVERITY_FILETYPE_MAP.get(request.severity.lower(), [])
            for ft in filetype_values:
                severity_should.append({"term": {"gy.filetype": ft}})

            # C# / mixed-level: match level markers in message content
            # IMPORTANT: Use phrase matching with brackets/delimiters to avoid
            # false positives from JSON field names like "error":"" or "errorMessage":""
            if request.severity.lower() in ("error", "critical"):
                severity_should.extend([
                    # Standard log format: [ERROR], [FATAL], [CRITICAL]
                    {"match_phrase": {"message": "[ERROR]"}},
                    {"match_phrase": {"message": "[FATAL]"}},
                    {"match_phrase": {"message": "[CRITICAL]"}},
                    # C# NLog format: "] ERROR " (after thread ID bracket)
                    {"match_phrase": {"message": "] ERROR "}},
                    {"match_phrase": {"message": "] FATAL "}},
                    # Java/C# exception indicators (high-confidence error markers)
                    {"match_phrase": {"message": "Exception:"}},
                    {"match_phrase": {"message": "Caused by:"}},
                    {"match_phrase": {"message": "Traceback (most recent"}},
                ])
            elif request.severity.lower() == "warning":
                severity_should.extend([
                    {"match_phrase": {"message": "[WARN]"}},
                    {"match_phrase": {"message": "[WARNING]"}},
                    {"match_phrase": {"message": "] WARN "}},
                    {"match_phrase": {"message": "] WARNING "}},
                ])

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
                host_name=gy_meta.get("host_name", ""),
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
                        "similarity": "cosine"
                    }
                }
            }
            await self.es.indices.create(index=index_name, mappings=mapping)
            logger.info("kb_index_created", index=index_name)
        return index_name

    async def insert_chunks(self, index_name: str, chunks: list[dict]):
        """Bulk insert embedding chunks into ES index."""
        from elasticsearch.helpers import async_bulk
        
        actions = [
            {
                "_index": index_name,
                "_source": chunk
            }
            for chunk in chunks
        ]
        success, failed = await async_bulk(self.es, actions)
        logger.info("kb_chunks_inserted", index=index_name, success=success, failed=len(failed) if failed else 0)
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
                    "num_candidates": 100
                },
                source=["content", "metadata", "doc_id"]
            )
            hits = resp.get("hits", {}).get("hits", [])
            results = []
            for hit in hits:
                source = hit["_source"]
                results.append({
                    "score": hit["_score"],
                    "content": source.get("content"),
                    "metadata": source.get("metadata"),
                    "doc_id": source.get("doc_id")
                })
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
                        "similarity": "cosine"
                    },
                    "created_at": {"type": "date"},
                    "ttl_expire_at": {"type": "date"},
                    # ── Known Issue Library fields ──────────
                    "status": {"type": "keyword"},      # open / resolved / ignored
                    "hit_count": {"type": "integer"},    # cumulative match count
                    "first_seen": {"type": "date"},      # first time this issue was seen
                    "last_seen": {"type": "date"},        # last time this issue was matched
                    "resolved_at": {"type": "date"},      # when issue was marked resolved
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
            from datetime import datetime, timezone
            now_iso = datetime.now(timezone.utc).isoformat()

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
                            ],
                        }
                    }
                },
                source=[
                    "analysis_content", "severity", "error_signature", "task_id",
                    "created_at", "status", "hit_count", "first_seen", "last_seen",
                    "resolved_at", "feedback_quality",
                ],
                min_score=min_score,
            )
            hits = resp.get("hits", {}).get("hits", [])
            results = []
            for hit in hits:
                source = hit["_source"]
                results.append({
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
                })
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
            from datetime import datetime, timedelta, timezone
            now = datetime.now(timezone.utc)
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
        self, doc_id: str, status: str, feedback_quality: str | None = None
    ) -> bool:
        """
        Update a known issue's status or feedback quality.

        Used by feedback API to mark issues as verified/poor.
        """
        index_name = "logmind-analysis-vectors"
        try:
            from datetime import datetime, timezone
            update_fields = {"status": status}
            if status == "resolved":
                update_fields["resolved_at"] = datetime.now(timezone.utc).isoformat()
            if feedback_quality is not None:
                update_fields["feedback_quality"] = feedback_quality

            # If verified, extend TTL to 365 days (effectively permanent)
            if feedback_quality == "verified":
                from datetime import timedelta
                update_fields["ttl_expire_at"] = (
                    datetime.now(timezone.utc) + timedelta(days=365)
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
          2. Java: gy.filetype mapping (error.log → error, etc.)
          3. C# NLog: parse from message "timestamp [thread] LEVEL Class - msg"
          4. Bracket format: [ERROR], [WARN], etc. in message
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

        # 2. Java: gy.filetype mapping (only for known single-level files)
        gy = source.get("gy", {})
        if isinstance(gy, dict):
            filetype = gy.get("filetype", "")
            if filetype in _FILETYPE_LEVEL_MAP:
                return _FILETYPE_LEVEL_MAP[filetype]

        # 3. C# NLog/log4net: parse from message content
        message = source.get("message", "")
        if isinstance(message, str):
            # Try NLog format first (most specific pattern)
            match = _NLOG_LEVEL_RE.search(message)
            if match:
                return _normalize_level(match.group(1))

            # Then try bracket format [ERROR], [WARN]
            match = _BRACKET_LEVEL_RE.search(message)
            if match:
                return _normalize_level(match.group(1))

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

        Common fields (all sites):
          gy.domain   → site domain name
          gy.filetype → log file type
        Java K8s only:
          gy.podname  → pod name with version suffix
          gy.branch   → code branch (master=prod, develop=test)
          image.version → container image version
        C# Windows VM:
          host.name   → Windows machine name (e.g. 10_14_83_74)
        """
        gy = source.get("gy", {})
        if not isinstance(gy, dict):
            gy = {}

        image = source.get("image", {})
        if not isinstance(image, dict):
            image = {}

        host = source.get("host", {})
        if not isinstance(host, dict):
            host = {}

        return {
            "domain": gy.get("domain", ""),
            "pod_name": gy.get("podname", ""),
            "branch": gy.get("branch", ""),
            "filetype": gy.get("filetype", ""),
            "image_version": image.get("version", ""),
            "host_name": host.get("name", ""),
        }


def _normalize_level(raw: str) -> str:
    """Normalize varied level strings to standard values."""
    upper = raw.strip().upper()
    level_map = {
        "ERROR": "error",
        "ERR": "error",
        "FATAL": "critical",
        "CRITICAL": "critical",
        "WARN": "warning",
        "WARNING": "warning",
        "INFO": "info",
        "INFORMATION": "info",
        "DEBUG": "debug",
        "TRACE": "debug",
        "VERBOSE": "debug",
    }
    return level_map.get(upper, raw.lower())


# Singleton
log_service = LogService()
