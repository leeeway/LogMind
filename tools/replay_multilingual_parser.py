"""Read-only multilingual parser replay; prints sanitized aggregate statistics only."""

import argparse
import asyncio
import json
import re
from collections import Counter, defaultdict

from elasticsearch import AsyncElasticsearch

from logmind.core.config import get_settings
from logmind.domain.analysis.sensitive_masker import mask_sensitive
from logmind.domain.analysis.stages.log_preprocess import LogPreprocessStage
from logmind.domain.log.events import (
    NORMALIZATION_VERSION,
    event_key,
    is_concrete_fault,
    metadata,
    normalize_message,
)
from logmind.domain.log.service import LogService, build_base_severity_filter

_CREDENTIAL_RESIDUAL = re.compile(
    r"(?i)(?:password|passwd|pwd|salt|secret|token|api[_-]?key|authorization|cookie)"
    r"\s*[:=]\s*(?!\[REDACTED\])[^\s,;}]+"
)


async def replay(
    index: str,
    hours: int,
    limit: int,
    filetype: str | None = None,
    query_mode: str = "current",
) -> dict:
    settings = get_settings()
    client = AsyncElasticsearch(
        settings.es_hosts_list,
        basic_auth=(settings.es_username, settings.es_password) if settings.es_username else None,
        verify_certs=settings.es_verify_certs,
        request_timeout=45,
        max_retries=0,
    )
    severity_filter = (
        {"match_phrase": {"message": "error"}}
        if query_mode == "error_word"
        else build_base_severity_filter("error")
    )
    filters = [
        {"range": {"@timestamp": {"gte": f"now-{hours}h"}}},
        severity_filter,
    ]
    if filetype:
        filters.append(
            {
                "term": {
                    "gy.filetype.keyword": {
                        "value": filetype,
                        "case_insensitive": True,
                    }
                }
            }
        )
    query = {
        "bool": {
            "filter": filters,
        }
    }
    body = {
        "size": limit,
        "track_total_hits": True,
        "query": query,
        "sort": [{"@timestamp": "asc"}, {"_doc": "asc"}],
        "_source": [
            "@timestamp",
            "message",
            "msg",
            "content",
            "level",
            "severity",
            "loglevel",
            "log.level",
            "log.file.path",
            "log.offset",
            "log.flags",
            "gy.*",
            "host.name",
            "agent.name",
            "image.version",
            "kubernetes.pod.name",
            "trace.id",
            "request_id",
        ],
    }
    try:
        response = await client.search(index=index, body=body)
    finally:
        await client.close()

    hits = response.get("hits", {}).get("hits", [])
    sources = []
    for hit in hits:
        source = hit.get("_source", {})
        source["_es_index"] = hit.get("_index", "")
        sources.append(source)

    stage = LogPreprocessStage()
    old_to_new: dict[str, set[str]] = defaultdict(set)
    new_counts: Counter = Counter()
    old_counts: Counter = Counter()
    levels: Counter = Counter()
    languages: Counter = Counter()
    version_sources: Counter = Counter()
    file_level_conflicts = 0
    credential_residuals = 0
    normalized_chars = 0
    raw_chars = 0
    concrete_faults = 0
    templates: Counter = Counter()
    field_values: dict[str, set[str]] = defaultdict(set)

    for source in sources:
        message = LogService._extract_message(source)
        old_key = stage._legacy_dedup_key(message)
        new_key = event_key(message)
        old_counts[old_key] += 1
        new_counts[new_key] += 1
        old_to_new[old_key].add(new_key)
        levels[LogService._extract_level(source)] += 1
        detected = stage._detect_language([source]) or "unknown"
        languages[detected] += 1
        meta = metadata(source)
        version_sources[meta["version_source"] or "missing"] += 1
        raw_chars += len(message)
        concrete_faults += is_concrete_fault(message)
        normalized_chars += len(mask_sensitive(message))
        templates[normalize_message(message)[:300]] += 1
        for key, value in re.findall(
            r"(?i)(?<![\w.])([a-z][\w.-]{1,40})\s*[:=]\s*([^\s,;\]}]+)",
            message,
        ):
            if len(field_values[key.lower()]) < 1001:
                field_values[key.lower()].add(value)
        credential_residuals += bool(_CREDENTIAL_RESIDUAL.search(mask_sensitive(message)))
        source_filetype = str(meta["filetype"]).lower()
        if source_filetype.startswith("info") and LogService._extract_level(source) in {
            "error",
            "critical",
        }:
            file_level_conflicts += 1

    merged = stage._merge_stack_traces(sources)
    total = response.get("hits", {}).get("total", {})
    return {
        "index": index,
        "filetype": filetype,
        "query_mode": query_mode,
        "window_hours": hours,
        "normalization_version": NORMALIZATION_VERSION,
        "matched": total.get("value", 0) if isinstance(total, dict) else total,
        "matched_relation": total.get("relation", "eq") if isinstance(total, dict) else "eq",
        "sampled": len(sources),
        "canonical_levels": dict(levels),
        "concrete_faults_in_sample": concrete_faults,
        "detected_languages": dict(languages),
        "version_sources": dict(version_sources),
        "file_message_level_conflicts": file_level_conflicts,
        "legacy_unique": len(old_counts),
        "v3_unique": len(new_counts),
        "legacy_keys_split_by_v3": sum(len(keys) > 1 for keys in old_to_new.values()),
        "legacy_max_semantic_variants": max(map(len, old_to_new.values()), default=0),
        "duplicate_occurrences_v3": sum(count - 1 for count in new_counts.values()),
        "stack_documents_before": len(sources),
        "stack_events_after": len(merged),
        "masked_character_count": normalized_chars,
        "raw_character_count": raw_chars,
        "credential_residual_candidates": credential_residuals,
        "representative_templates": [
            {"occurrences": count, "template": template}
            for template, count in templates.most_common(5)
        ],
        "representative_singletons": [
            template[:500] for template, count in templates.items() if count == 1
        ][:5],
        "highest_cardinality_fields": [
            {"field": field, "distinct_in_sample": len(values)}
            for field, values in sorted(
                field_values.items(), key=lambda item: len(item[1]), reverse=True
            )[:15]
        ],
    }


async def main(args) -> int:
    results = []
    for index in args.index:
        results.append(await replay(index, args.hours, args.limit, args.filetype, args.query_mode))
    if args.summary_only:
        for result in results:
            result.pop("representative_templates", None)
            result.pop("representative_singletons", None)
            result.pop("highest_cardinality_fields", None)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", action="append", required=True)
    parser.add_argument("--hours", type=int, default=24, choices=range(1, 169))
    parser.add_argument("--limit", type=int, default=500, choices=range(1, 1001))
    parser.add_argument("--filetype")
    parser.add_argument("--query-mode", choices=("current", "error_word"), default="current")
    parser.add_argument("--summary-only", action="store_true")
    arguments = parser.parse_args()
    raise SystemExit(asyncio.run(main(arguments)))
