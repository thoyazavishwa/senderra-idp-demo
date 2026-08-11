"""The only module that talks to Azure. Everything above it sees plain data.

Two things here earn their place, and both are about latency.

**Listing is not downloading.** `list_blobs` returns name, etag, size and
last-modified for up to 5,000 blobs in one call without transferring any
content. That is what makes an incremental refresh possible: the caller diffs
etags and downloads only what changed. See `store.DocumentStore.sync`.

**Downloads run in parallel.** Fetching N metrics records is N independent HTTPS
round trips of ~30-50 ms each, so it is pure network wait, not CPU. Sequentially
300 documents take ~20 s; across 32 threads they take under a second. The GIL is
irrelevant because every thread is blocked on a socket, and `BlobServiceClient`
is documented as safe to share across threads as long as each thread makes its
own blob client — which `_download_one` does.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobServiceClient, ContentSettings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BlobRef:
    """What a listing tells us, before anything is downloaded."""

    name: str
    etag: str
    size: int
    last_modified: Optional[dt.datetime]


class BlobStore:
    def __init__(self, connection_string: str, max_workers: int = 32) -> None:
        self._client = BlobServiceClient.from_connection_string(connection_string)
        self._max_workers = max_workers

    # -- listing ------------------------------------------------------------
    def list(self, container: str, prefix: str | None = None,
             suffixes: tuple[str, ...] | None = None) -> list[BlobRef]:
        """Every blob under `prefix`, optionally filtered by filename suffix.

        Suffix filtering happens here rather than at the caller because it keeps
        `words.json` — one megabyte per document, and useless to this app — out
        of every downstream code path by construction.
        """
        try:
            container_client = self._client.get_container_client(container)
            out: list[BlobRef] = []
            for b in container_client.list_blobs(name_starts_with=prefix):
                if suffixes and not b.name.endswith(suffixes):
                    continue
                out.append(BlobRef(
                    name=b.name,
                    # The SDK returns etags quoted; strip so equality is stable.
                    etag=(b.etag or "").strip('"'),
                    size=b.size or 0,
                    last_modified=b.last_modified,
                ))
            return out
        except ResourceNotFoundError:
            log.warning("container %s does not exist", container)
            return []

    # -- reading ------------------------------------------------------------
    def get_bytes(self, container: str, name: str) -> bytes | None:
        try:
            return self._client.get_blob_client(container, name).download_blob().readall()
        except ResourceNotFoundError:
            return None

    def get_text(self, container: str, name: str) -> str | None:
        raw = self.get_bytes(container, name)
        return raw.decode("utf-8") if raw is not None else None

    def get_json(self, container: str, name: str) -> Any | None:
        raw = self.get_text(container, name)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            log.warning("malformed JSON at %s/%s", container, name)
            return None

    def get_json_many(self, container: str, names: Iterable[str]) -> dict[str, Any]:
        """Download and parse many blobs concurrently.

        A blob that is missing or malformed is omitted from the result rather
        than raising. One corrupt metrics record must not blank the dashboard —
        the whole point of the dashboard is to show that something went wrong.
        """
        names = list(names)
        if not names:
            return {}

        def _download_one(name: str) -> tuple[str, Any | None]:
            return name, self.get_json(container, name)

        workers = min(self._max_workers, len(names))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = pool.map(_download_one, names)
            return {name: obj for name, obj in results if obj is not None}

    # -- writing ------------------------------------------------------------
    def upload(self, container: str, name: str, data: bytes,
               content_type: str = "application/octet-stream",
               overwrite: bool = False) -> str:
        """Put one blob and return its full path.

        `overwrite` defaults to False so a re-upload of the same filename fails
        loudly instead of silently replacing a document whose results are
        already on screen.
        """
        self._client.get_blob_client(container, name).upload_blob(
            data, overwrite=overwrite,
            content_settings=ContentSettings(content_type=content_type))
        return f"{container}/{name}"

    def upload_many(self, container: str,
                    items: list[tuple[str, bytes, str]]) -> list[tuple[str, str | None]]:
        """Upload concurrently. Returns (name, error_or_None) in input order.

        Errors are values, not exceptions: uploading five files where the third
        already exists should still upload the other four and say so.
        """
        def _upload_one(item: tuple[str, bytes, str]) -> tuple[str, str | None]:
            name, data, content_type = item
            try:
                self.upload(container, name, data, content_type)
                return name, None
            except Exception as exc:                      # noqa: BLE001 — reported, not raised
                return name, f"{type(exc).__name__}: {exc}"

        workers = min(self._max_workers, len(items)) or 1
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(_upload_one, items))
