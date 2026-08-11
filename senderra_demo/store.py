"""Read the pipeline's blobs, join them into one row per document, cache by ETag.

WHY THIS IS THE ONLY INTERESTING FILE IN THE APP
------------------------------------------------
The dashboard needs every metrics record to compute a single average. Done
naively that is 2N blob downloads on every interaction, and Streamlit re-runs
the whole script on every interaction — so a 300-document corpus would spend
~20 seconds re-downloading identical bytes each time a dropdown moved.

Three things fix that, in order of how much they matter:

1. **Diff before download.** `list_blobs` returns each blob's ETag without
   transferring its content. An ETag changes if and only if the blob changed,
   and these records are written once and never updated. So a steady-state
   refresh is one list call and zero downloads.
2. **Download in parallel.** The first load is unavoidable, but it is network
   wait, not work — 32 threads turn 20 seconds into under one.
3. **Hold the parsed result across re-runs.** The store lives in
   `st.cache_resource`, so it is one object per server process, shared by every
   session and every script re-run.

The cache is keyed on ETag rather than on a TTL alone because correctness and
freshness then stop competing: `SYNC_TTL_SECONDS` only bounds how often we
*ask*, never how stale an answer can be once we have asked.

ONE STORE, SEVERAL VIEWERS
-------------------------
`st.cache_resource` hands this same object to every session, so with five people
watching, five Streamlit script threads call `sync()` against it concurrently.
Two rules keep that honest, and they are why there are two locks below:

* **At most one refresh in flight.** Five simultaneous first loads would be five
  identical listings and five identical download storms against one storage
  account, which is how a demo earns a 503.
* **A viewer never waits on someone else's refresh.** The thread that loses the
  race serves the snapshot it already has and says so, rather than blocking. So
  no network round trip is ever on another session's critical path — which is the
  whole difference between "the dashboard is a second stale" and "the dashboard
  froze for everyone while one person clicked Refresh".

`_lock` therefore guards only in-memory state and is never held across I/O;
`_refresh` is the try-lock that elects the one thread doing the work.

WHAT IS DELIBERATELY NOT READ
-----------------------------
`work/**/words.json` is ~1 MB per document and this app never needs it — the
per-field OCR confidences and polygons it powers are already summarised into
the extract record and the results file. `blobstore.list` filters by suffix so
those blobs cannot enter a code path by accident.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any

import pandas as pd
from azure.core.exceptions import AzureError

from senderra_demo import schema
from senderra_demo.blobstore import BlobRef, BlobStore, redact
from senderra_demo.config import Settings

log = logging.getLogger(__name__)

#: How many results files to keep parsed. Each is tens of kilobytes and they are
#: only ever read one at a time on the detail screen, so this is a cap on a
#: convenience — not a working set. Unbounded, it is a slow leak: a process that
#: lives for days while people click through a few hundred documents would hold
#: every one of them forever, on a host with ~1 GB for the whole app.
_RESULTS_CACHE_MAX = 256


@dataclass
class SyncStats:
    """What the last refresh actually did. Shown in the sidebar so the caching
    claim above is verifiable rather than asserted."""

    listed: int = 0
    downloaded: int = 0
    removed: int = 0
    duration_ms: int = 0
    at: float = field(default_factory=time.time)
    #: True when this refresh was skipped because another session was already
    #: doing it. The sidebar says so rather than implying a fresh listing.
    deferred: bool = False


def _safe_doc_id(doc_id: str) -> str:
    """`prescription/007` -> `prescription__007`. Mirrors `shared/metrics.py`."""
    return doc_id.replace("/", "__")


def _split_docs_in_path(name: str) -> tuple[str, str] | None:
    """`r000-smoke/prescription.pdf` -> ('r000-smoke', 'prescription').

    Same convention `parse_ocr_message` uses to reconstruct identity from an
    Event Grid event: first path segment is the run, the rest is the doc id. A
    PDF at the container root has no run and is ignored — the pipeline would
    treat it as run `prod`, but it cannot have come from this app.
    """
    if not name.lower().endswith(".pdf"):
        return None
    stem = name[: -len(".pdf")]
    run_id, _, doc_id = stem.partition("/")
    return (run_id, doc_id) if doc_id else None


class DocumentStore:
    def __init__(self, blobs: BlobStore, settings: Settings) -> None:
        self._blobs = blobs
        self._settings = settings
        self._lock = threading.RLock()          # in-memory state; never held across I/O
        self._refresh = threading.Lock()        # elects the one thread that syncs

        # blob name -> (etag, parsed record). The unit of caching.
        self._records: dict[str, tuple[str, dict]] = {}
        # doc key -> parsed results file. Populated lazily, on the detail screen.
        self._results: dict[str, dict] = {}
        self._pdfs: dict[tuple[str, str], BlobRef] = {}

        self._frame: pd.DataFrame = pd.DataFrame()
        self._last_sync: float = 0.0
        self.stats = SyncStats()
        #: The last Azure failure, already redacted, or None. Surfaced once in
        #: the sidebar instead of as a traceback in whichever view happened to
        #: touch the network — see `app.py`.
        self.last_error: str | None = None

    @property
    def loaded(self) -> bool:
        """Whether any sync has completed. Lets the caller word its spinner
        honestly — the first load is seconds, every later one is milliseconds."""
        with self._lock:
            return bool(self._last_sync)

    @property
    def blobs(self) -> BlobStore:
        """The upload view needs to write; it goes through the same client so
        there is still exactly one Azure boundary in the app."""
        return self._blobs

    # -- refresh ------------------------------------------------------------
    def _fresh(self, force: bool) -> bool:
        """Whether the snapshot is young enough to serve without asking Azure."""
        if force:
            return False
        return bool(self._last_sync
                    and time.time() - self._last_sync < self._settings.sync_ttl_seconds)

    def sync(self, force: bool = False) -> SyncStats:
        """Bring the in-memory view up to date. Cheap unless something changed.

        Never raises. An Azure failure lands in `last_error` and the previous
        snapshot keeps serving, because a dashboard that says "showing data from
        40 seconds ago, storage is not answering" is more useful than one that
        replaces itself with a stack trace.
        """
        with self._lock:
            if self._fresh(force):
                return self.stats

        # Whoever gets here first does the work. Everyone else returns the
        # snapshot they have — no session ever blocks on another's round trip.
        if not self._refresh.acquire(blocking=False):
            with self._lock:
                return replace(self.stats, deferred=True)

        try:
            with self._lock:
                # The winner may have finished between our TTL check and the
                # try-lock, in which case there is nothing left to do.
                if self._fresh(force):
                    return self.stats
                known = {name: etag for name, (etag, _) in self._records.items()}

            started = time.perf_counter()

            # --- network, with no lock held ---------------------------------
            refs = self._blobs.list(self._settings.container_metrics,
                                    suffixes=schema.METRIC_SUFFIXES)
            live = {r.name: r.etag for r in refs}
            stale = [name for name, etag in live.items() if known.get(name) != etag]
            fetched = self._blobs.get_json_many(
                self._settings.container_metrics, stale)

            # One extra list call, no downloads: PDFs that have no stage-1
            # record yet. Without this a freshly uploaded document is invisible
            # for the 30-60 s that OCR takes, which reads as a broken upload.
            pdfs: dict[tuple[str, str], BlobRef] = {}
            for ref in self._blobs.list(self._settings.container_docs,
                                        suffixes=(".pdf", ".PDF")):
                parsed = _split_docs_in_path(ref.name)
                if parsed:
                    pdfs[parsed] = ref

            # --- commit, under the lock, no I/O -----------------------------
            with self._lock:
                removed = [name for name in self._records if name not in live]
                for name in removed:
                    self._records.pop(name, None)
                for name, record in fetched.items():
                    self._records[name] = (live[name], record)

                self._pdfs = pdfs
                self._frame = self._build_frame()
                self._last_sync = time.time()
                self.last_error = None
                self.stats = SyncStats(
                    listed=len(refs), downloaded=len(fetched), removed=len(removed),
                    duration_ms=int((time.perf_counter() - started) * 1000))
                return self.stats

        except AzureError as exc:
            log.warning("sync failed: %s", redact(exc))
            with self._lock:
                self.last_error = redact(exc)
                # Stamped even though nothing was fetched, so a broken
                # credential is retried once per TTL rather than on every
                # interaction of every session — five people clicking on a
                # dead connection is a retry storm, not diagnostics.
                self._last_sync = time.time()
                return self.stats
        finally:
            self._refresh.release()

    # -- the table ----------------------------------------------------------
    def documents(self) -> pd.DataFrame:
        """One row per document, both stages joined. Never mutated by callers —
        every view takes its own filtered copy."""
        with self._lock:
            return self._frame

    def _build_frame(self) -> pd.DataFrame:
        rows: dict[tuple[str, str], dict[str, Any]] = {}

        for name, (_, record) in self._records.items():
            run_id, doc_id = record.get("run_id"), record.get("doc_id")
            if not run_id or not doc_id:
                continue
            is_ocr = name.endswith(schema.OCR_SUFFIX)
            renames = schema.OCR_RENAMES if is_ocr else schema.EXTRACT_RENAMES

            row = rows.setdefault((run_id, doc_id), {"run_id": run_id, "doc_id": doc_id})
            for key, value in record.items():
                if key in schema.DROP_FROM_TABLE:
                    continue
                row[renames.get(key, key)] = value
            row["has_ocr" if is_ocr else "has_extract"] = True

        # Documents whose PDF is uploaded but whose stage-1 record has not
        # landed. They carry no measurements — only identity and a status.
        for (run_id, doc_id), ref in self._pdfs.items():
            row = rows.setdefault((run_id, doc_id), {"run_id": run_id, "doc_id": doc_id})
            row.setdefault("file_bytes", ref.size)
            row.setdefault("uploaded_at", ref.last_modified)

        if not rows:
            return pd.DataFrame()

        frame = pd.DataFrame(list(rows.values()))

        for column in schema.NUMERIC_COLUMNS:
            if column in frame:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        for column in schema.TIMESTAMP_COLUMNS:
            if column in frame:
                frame[column] = pd.to_datetime(frame[column], errors="coerce", utc=True)

        # `.eq(True)` rather than `.fillna(False).astype(bool)`: the column is
        # object-typed (True where a record was seen, absent elsewhere), and
        # fillna on an object column silently downcasts — deprecated in pandas 2
        # and a FutureWarning on every refresh.
        for flag in ("has_ocr", "has_extract"):
            frame[flag] = frame[flag].eq(True) if flag in frame else False

        frame["doc_key"] = frame["run_id"] + "/" + frame["doc_id"]
        frame["status"] = frame.apply(_overall_status, axis=1)
        frame["processed_at"] = _processed_at(frame)

        # Both function invocations, but only where stage 2 actually ran —
        # otherwise a document still in flight would report the OCR time as if
        # it were the whole pipeline and drag the average down.
        ocr_ms = _column(frame, "ocr_e2e_ms")
        extract_ms = _column(frame, "extract_e2e_ms")
        frame["pipeline_ms"] = (ocr_ms.fillna(0) + extract_ms.fillna(0)).where(
            frame["has_extract"])

        return frame.sort_values("processed_at", ascending=False,
                                 na_position="first").reset_index(drop=True)

    # -- one document -------------------------------------------------------
    def raw_records(self, run_id: str, doc_id: str) -> dict[str, dict]:
        """The two untouched stage records, for the raw-JSON view. Nested
        objects that the flat table drops (`calls`, `pages`) live here."""
        safe = _safe_doc_id(doc_id)
        with self._lock:
            return {
                stage: record
                for stage, suffix in (("ocr", schema.OCR_SUFFIX),
                                      ("extract", schema.EXTRACT_SUFFIX))
                if (record := self._records.get(f"{run_id}/{safe}{suffix}", (None, None))[1])
            }

    def result(self, run_id: str, doc_id: str, doc_type: str | None,
               results_blob: str | None = None) -> dict | None:
        """The extracted fields. Fetched on demand and memoised.

        Loading every results file up front would triple the first-load cost to
        show data that only one document at a time ever needs.

        The path is `results/<run>/<doc_type>/<doc>.fields.json`, so it cannot
        be built before classification has happened. The extract record carries
        it in `results_blob`; the reconstruction below is the fallback for
        records written before that field existed.
        """
        key = f"{run_id}/{doc_id}"
        with self._lock:
            if key in self._results:
                return self._results[key]

        # ⚠️ `isinstance(..., str)`, not a bare truthiness test. A row assembled
        # from two stage records carries float('nan') in every column the other
        # stage owns — and NaN is TRUTHY, so `if results_blob:` passed for a
        # document that never produced one and then called .startswith on a
        # float. Same trap as `_text()` below; there is no cheap general guard,
        # so every read of an optional column has to be explicit.
        results_blob = results_blob if isinstance(results_blob, str) else None
        doc_type = doc_type if isinstance(doc_type, str) else None

        if results_blob:
            # Stored with the container as the first segment: strip it.
            prefix = f"{self._settings.container_results}/"
            name = results_blob[len(prefix):] if results_blob.startswith(prefix) else results_blob
        elif doc_type:
            folder = "_other" if doc_type == "other" else doc_type
            name = f"{run_id}/{folder}/{_safe_doc_id(doc_id)}.fields.json"
        else:
            return None

        payload = self._read(lambda: self._blobs.get_json(
            self._settings.container_results, name))
        if payload is not None:
            with self._lock:
                self._results[key] = payload
                # Oldest insertion first — dicts preserve insertion order, and
                # "the one opened longest ago" is as good an eviction rule as any
                # for a screen that shows one document at a time.
                while len(self._results) > _RESULTS_CACHE_MAX:
                    self._results.pop(next(iter(self._results)))
        return payload

    def markdown(self, run_id: str, doc_id: str) -> str | None:
        """The OCR checkpoint both LLM calls actually read. Never cached — it is
        tens of kilobytes and opened rarely."""
        return self._read(lambda: self._blobs.get_text(
            self._settings.container_work,
            f"{run_id}/{_safe_doc_id(doc_id)}/markdown.md"))

    def _read(self, fetch):
        """Run a lazy per-document fetch, turning an Azure failure into `None`.

        The detail screen's three network reads (results, evidence, markdown) all
        go through here. They cannot be allowed to raise: an expired SAS or a
        container the credential cannot see would otherwise render a traceback
        inside whichever tab the viewer happened to open, five times over. The
        error is recorded once, the view shows its own "not available yet"
        message, and the sidebar carries the real reason.
        """
        try:
            return fetch()
        except AzureError as exc:
            log.warning("lazy read failed: %s", redact(exc))
            with self._lock:
                self.last_error = redact(exc)
            return None


def _text(row: pd.Series, key: str) -> str | None:
    """A cell as a string, or None when it is absent.

    A row assembled from two stage records has NaN in every column the other
    stage owns, and `float('nan')` is TRUTHY — so a plain `if row.get(key)` on a
    missing status passes and renders the literal string "nan". This is the
    guard against that.
    """
    value = row.get(key)
    if value is None or (isinstance(value, float) and pd.isna(value)) or value == "":
        return None
    return str(value)


def _overall_status(row: pd.Series) -> str:
    """Collapse two stage statuses into the one a reader wants.

    Order matters. A document is described by the furthest stage that reached a
    verdict, so stage 2 wins when it exists — except for `DuplicateSkipped`,
    which says the work was already done elsewhere and tells you nothing about
    this document's outcome.
    """
    extract_status = _text(row, "extract_status")
    ocr_status = _text(row, "ocr_status")

    if extract_status and extract_status != "DuplicateSkipped":
        return extract_status
    if ocr_status is None:
        # A PDF in docs-in with no stage-1 record. Either just uploaded, or the
        # message never arrived — the Documents tab shows how long it has sat.
        return schema.STATUS_QUEUED
    if ocr_status == schema.STATUS_SUCCEEDED:
        # Stage 1 finished. Stage 2 has either not written yet, or was skipped
        # as a duplicate — both mean stage 1's verdict is the whole story.
        return (schema.STATUS_SUCCEEDED if row.get("has_extract")
                else schema.STATUS_PROCESSING)
    return ocr_status


def _column(frame: pd.DataFrame, name: str) -> pd.Series:
    """A numeric column, or an all-NaN one of matching length.

    Records written before a field existed simply lack the key, so no view may
    assume a column is present.
    """
    if name in frame:
        return pd.to_numeric(frame[name], errors="coerce")
    return pd.Series([float("nan")] * len(frame), index=frame.index, dtype="float64")


def _processed_at(frame: pd.DataFrame) -> pd.Series:
    """The most recent evidence that anything happened to this document.

    Three candidates, and the latest wins: the PDF's upload time (all a queued
    document has), stage 1's record, stage 2's record. Taking the maximum rather
    than a priority order means the column is monotonic as a document
    progresses, so sorting by it always puts the freshest activity on top.
    """
    candidates = [pd.to_datetime(frame[name], errors="coerce", utc=True)
                  for name in ("uploaded_at", "ocr_recorded_at", "extract_recorded_at")
                  if name in frame]
    if not candidates:
        return pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]")
    return pd.concat(candidates, axis=1).max(axis=1)
