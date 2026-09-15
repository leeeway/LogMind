"""Read-only replay. No AI, database, Redis writes, or notification calls.

PYTHONPATH=src python tools/replay_csharp_patrol.py --index master-example \
    --until 2026-09-12T13:00:00+08:00
"""

import argparse
import asyncio
import json
from datetime import datetime

from elasticsearch import AsyncElasticsearch

from logmind.core.config import get_settings
from logmind.domain.anomaly.detector import AnomalyDetector


async def replay(index, until):
    import logmind.core.elasticsearch as es_module

    settings = get_settings()
    es = AsyncElasticsearch(
        settings.es_hosts_list,
        basic_auth=(settings.es_username, settings.es_password) if settings.es_username else None,
        verify_certs=settings.es_verify_certs,
        request_timeout=30,
        max_retries=0,
    )
    original = es_module.get_es_client
    es_module.get_es_client = lambda: es
    try:
        old = await AnomalyDetector().detect(index, until=until)
        new = await AnomalyDetector().detect(index, until=until, inspect_concrete_faults=True)
        print(
            json.dumps(
                {
                    "index": index,
                    "window_end": until.isoformat(),
                    "errors": new.current_errors,
                    "concrete_faults": new.concrete_faults,
                    "volume_only_triggered": old.is_anomaly,
                    "new_triggered": new.is_anomaly,
                    "trigger": new.trigger,
                    "query_failed": old.detection_failed or new.detection_failed,
                },
                ensure_ascii=False,
            )
        )
        return 1 if old.detection_failed or new.detection_failed else 0
    finally:
        es_module.get_es_client = original
        await es.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", required=True, help="明确的业务日志索引（不接受通配符）")
    parser.add_argument("--until", required=True, help="5分钟窗口结束时间，必须带时区")
    args = parser.parse_args()
    if any(c in args.index for c in "*?,"):
        parser.error("一次只回放一个明确索引")
    try:
        until = datetime.fromisoformat(args.until)
    except ValueError:
        parser.error("时间必须为 ISO8601 格式")
    if until.tzinfo is None:
        parser.error("时间必须包含 +08:00 或 Z 等时区")
    raise SystemExit(asyncio.run(replay(args.index, until)))
