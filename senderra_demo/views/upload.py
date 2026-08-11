"""Tab 2 — put PDFs where the pipeline is already watching.

THERE IS NO API CALL HERE, AND THAT IS THE POINT
------------------------------------------------
The pipeline is triggered by a blob write, not by an HTTP endpoint:

    docs-in/<run_id>/<doc>.pdf  ->  Event Grid  ->  Service Bus  ->  fn_ocr

`parse_ocr_message` reconstructs the document's identity from that path — the
first segment is the run id, the rest is the document id. So uploading to
`docs-in/ui/<name>.pdf` starts the real production path with **zero changes to
the IDP solution**, which is the constraint this whole app is built under.
`/api/enqueue` exists for smoke tests and is deliberately not used.
"""
from __future__ import annotations

import streamlit as st

from senderra_demo import blobstore, format as fmt
from senderra_demo.blobstore import BlobStore
from senderra_demo.config import Settings

_SAFE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."


def _blob_name(run_id: str, filename: str) -> str:
    """`Prior Auth (1).pdf` -> `ui/Prior_Auth_1.pdf`.

    The document id is derived from this path and then reused as a path segment
    in three more containers, so characters that are legal in a filename but
    awkward in a path are folded to underscores.

    ⚠️ The run of underscores is then COLLAPSED, and that is not cosmetic. The
    pipeline stores documents under `safe_doc_id`, which encodes `/` as `__`
    and decodes it back the same way. A filename containing a literal `__`
    would therefore be read back as a document id containing a `/` — pointing
    stage 2 at a work prefix that does not exist. Collapsing here means an
    uploaded name can never round-trip into a different document id.
    """
    stem = "".join(c if c in _SAFE else "_" for c in filename)
    while "__" in stem:
        stem = stem.replace("__", "_")
    if not stem.lower().endswith(".pdf"):
        stem += ".pdf"
    return f"{run_id}/{stem}"


def render(blobs: BlobStore, settings: Settings) -> None:
    st.subheader("Upload documents")
    st.write(
        f"Files land in **`{settings.container_docs}/{settings.upload_run_id}/`** on "
        f"storage account `{settings.account_name}`. That write is what starts the "
        f"pipeline — Event Grid picks it up within a second or two and stage 1 "
        f"begins. Nothing here calls the Function App directly."
    )

    uploaded = st.file_uploader(
        "PDF documents", type=["pdf"], accept_multiple_files=True,
        help="One PDF per document. The pipeline does not unpack archives.",
    )

    run_id = st.text_input(
        "Run id", value=settings.upload_run_id,
        help="Groups these documents together and becomes the first path "
             "segment. Keep it distinct from benchmark runs (r000-…, r002-…) "
             "so the dashboard can separate demo traffic from benchmark data.",
    ).strip() or settings.upload_run_id

    overwrite = st.checkbox(
        "Replace files that already exist", value=False,
        help="Off by default: re-uploading a name that already exists would "
             "replace a document whose results may be on screen, and the "
             "pipeline would re-process it at full cost.",
    )

    total_bytes = sum(f.size for f in uploaded) if uploaded else 0
    limit_bytes = settings.upload_batch_mb * 1024 * 1024

    if uploaded:
        st.caption(f"{len(uploaded)} file(s) ready · {fmt.size(total_bytes)} total")

    # ⚠️ A batch is charged against memory TWICE — Streamlit buffers each upload
    # in the server process, and `getvalue()` below copies it again to hand bytes
    # to the SDK. On a shared host that memory is not this session's to spend:
    # the process also holds the parsed record cache and everyone else's session,
    # and an OOM kill takes all of them down mid-demo. Per-file size is capped by
    # `maxUploadSize` in `.streamlit/config.toml`; this caps the batch.
    if total_bytes > limit_bytes:
        st.error(
            f"That batch is {fmt.size(total_bytes)}, over the "
            f"{settings.upload_batch_mb} MB limit for one upload. Send it in "
            f"smaller groups — the pipeline processes each document "
            f"independently, so several batches give the same result as one.")
        return

    if not st.button("Upload and process", type="primary", disabled=not uploaded):
        return

    items = [(_blob_name(run_id, f.name), f.getvalue(), "application/pdf")
             for f in uploaded]

    with st.spinner(f"Uploading {len(items)} file(s)…"):
        if overwrite:
            results = []
            for name, data, content_type in items:
                try:
                    blobs.upload(settings.container_docs, name, data,
                                 content_type, overwrite=True)
                    results.append((name, None))
                except Exception as exc:                  # noqa: BLE001 — surfaced below
                    results.append(
                        (name, blobstore.redact(f"{type(exc).__name__}: {exc}")))
        else:
            results = blobs.upload_many(settings.container_docs, items)

    ok = [name for name, error in results if error is None]
    bad = [(name, error) for name, error in results if error is not None]

    if ok:
        st.success(f"{len(ok)} file(s) uploaded. Stage 1 starts automatically.")
        st.code("\n".join(f"{settings.container_docs}/{name}" for name in ok),
                language="text")
        # The store's next sync lists docs-in and will show these as Queued
        # immediately, before any metrics record exists.
        st.session_state["force_sync"] = True
        st.info("Open the **Documents** tab to watch them move from *Queued* to "
                "*Processing* to *Succeeded*. A 50-page scan typically takes "
                "30–60 seconds end to end.")

    for name, error in bad:
        if "BlobAlreadyExists" in error:
            st.warning(f"`{name}` already exists — tick *Replace* to overwrite, "
                       f"or rename the file.")
        else:
            st.error(f"`{name}` failed: {error}")
