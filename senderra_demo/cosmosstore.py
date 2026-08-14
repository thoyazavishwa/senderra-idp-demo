"""Read the pipeline's Cosmos projection: one query instead of N downloads.

WHY THIS EXISTS ALONGSIDE blobstore.py, NOT INSTEAD OF IT

`store.py` earns its dashboard performance by diffing ETags and downloading only
what changed. That works, and at a few hundred documents it is genuinely fast.
What it cannot do is scale: the table needs EVERY metrics record to compute one
average, so the listing grows linearly and a year of production (540,000
documents) is ~45,000 blobs per page load.

Cosmos answers the same question in one query, because `shared/cosmos.py`
projects each stage record into an item. So:

    table and aggregates  ->  Cosmos, one query          (this file)
    per-document detail   ->  blobs, unchanged           (store.py lazy reads)

THAT SPLIT IS NOT LAZINESS — THE PROJECTION IS DELIBERATELY LOSSY

`shared/cosmos.py` drops four things from every item, and two of them this app
displays:

    classify_evidence   up to 500 chars of document prose. It is PHI, and it is
                        stripped so it never reaches a store that feeds a
                        dashboard. `views/documents.py` renders it.
    calls, pages        bulky nested arrays. `store.raw_records()` shows them on
                        the raw-JSON tab.
    cost_warnings       list of strings.

So the detail screen keeps reading blobs, which hold the full truth. The table
reads Cosmos, which holds every scalar the table actually charts. Neither is a
fallback for the other — they answer different questions, and `store.py` uses
both in the same refresh.

NO ETag DIFF HERE, ON PURPOSE

Items are small and the query is one round trip, so re-reading the whole
projection every `SYNC_TTL_SECONDS` is cheaper than the bookkeeping to avoid it.
The RU charge is reported in `last_ru` and shown in the sidebar, because an
aggregate whose cost grows with the corpus should be visible rather than
discovered on a bill.
"""
from __future__ import annotations

import logging
from typing import Any

from senderra_demo.config import Settings

log = logging.getLogger(__name__)

#: Cosmos system properties and the projection's own routing keys. Stripped so
#: they never become DataFrame columns — `store._build_frame` copies every key it
#: is handed, and `_rid`/`_self`/`documentId` are noise in a table of
#: measurements.
_STRIP = ("_rid", "_self", "_etag", "_attachments", "_ts",
          "id", "itemType", "documentId", "runId", "docId")

#: Only the two stage records. The `fields` item is ~10 KB of field detail that
#: the table never charts, and pulling it would multiply this query's RU cost by
#: five for data the detail screen reads per-document anyway.
_QUERY = "SELECT * FROM c WHERE c.itemType IN ('ocr', 'extract')"


def _safe_doc_id(doc_id: str) -> str:
    return doc_id.replace("/", "__")


class CosmosStore:
    """Lazily-connected reader for the `documents` container."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._container = None
        #: RU charged by the last query. Shown in the sidebar so the cost curve
        #: in guide/12 §12 is observed rather than trusted.
        self.last_ru: float | None = None

    @property
    def enabled(self) -> bool:
        s = self._settings
        return bool(s.cosmos_enabled and s.cosmos_endpoint and s.cosmos_key)

    def container(self):
        if self._container is not None:
            return self._container

        from azure.cosmos import CosmosClient

        s = self._settings
        client = CosmosClient(s.cosmos_endpoint, credential=s.cosmos_key)
        self._container = (client.get_database_client(s.cosmos_database)
                                 .get_container_client(s.cosmos_container))
        return self._container

    def stage_records(self) -> dict[str, dict[str, Any]]:
        """Every ocr/extract record, keyed exactly as the blob path would be.

        `<run_id>/<safe_doc_id>.<stage>.json` — the same key `store.py` uses for
        blobs, so `_build_frame` and `raw_records` need no idea where a record
        came from. That is the whole trick that keeps this file additive.
        """
        container = self.container()
        out: dict[str, dict[str, Any]] = {}

        for item in container.query_items(query=_QUERY,
                                          enable_cross_partition_query=True):
            run_id = item.get("run_id") or item.get("runId")
            doc_id = item.get("doc_id") or item.get("docId")
            stage = item.get("stage") or item.get("itemType")
            if not (run_id and doc_id and stage):
                continue
            record = {k: v for k, v in item.items() if k not in _STRIP}
            out[f"{run_id}/{_safe_doc_id(doc_id)}.{stage}.json"] = record

        try:
            headers = container.client_connection.last_response_headers
            self.last_ru = float(headers.get("x-ms-request-charge", 0) or 0)
        except Exception:                      # noqa: BLE001 — telemetry, not logic
            self.last_ru = None

        return out
