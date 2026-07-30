"""
Install the HTTP access normalization ingest pipeline.

The script deliberately does not replace existing Filebeat composable index
templates because those templates are owned by the logging platform. After
installation, configure both Filebeat inputs/data streams to send events with:

    pipeline: logmind-http-access-normalize-v1

Then run this command with --rollover so new backing indices receive the
organization's corrected numeric mappings.
"""

from __future__ import annotations

import argparse
import asyncio

from logmind.core.config import get_settings
from logmind.core.elasticsearch import close_es
from logmind.domain.http_access.service import HttpAccessService


async def _run(*, rollover: bool) -> None:
    settings = get_settings()
    service = HttpAccessService()
    try:
        await service.install_ingest_pipeline()
        print("Installed ingest pipeline: logmind-http-access-normalize-v1")
        await service.install_canonical_mappings()
        print("Installed canonical mappings on configured data streams")
        if rollover:
            for index_name in settings.http_access_index_list:
                await service.es.indices.rollover(alias=index_name)
                print(f"Rolled over data stream: {index_name}")
            await service.install_canonical_mappings()
            print("Installed canonical mappings on new write indices")
    finally:
        await close_es()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install LogMind Nginx/Ingress normalization pipeline",
    )
    parser.add_argument(
        "--rollover",
        action="store_true",
        help=(
            "roll over configured data streams after Filebeat/template has "
            "been configured to use the installed pipeline"
        ),
    )
    args = parser.parse_args()
    asyncio.run(_run(rollover=args.rollover))


if __name__ == "__main__":
    main()
