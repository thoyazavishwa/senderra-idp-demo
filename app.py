"""Senderra IDP demo — entry point.

    streamlit run app.py
STREAMLIT'S EXECUTION MODEL, AND THE ONE THING TO KNOW ABOUT IT
--------------------------------------------------------------
Streamlit re-runs this entire file top to bottom on every interaction — every
checkbox, every row click. Anything expensive therefore has to live outside the
script's own lifetime, which is what `st.cache_resource` is for: the objects
below are created once per server process and shared by every re-run and every
session.

That is why `DocumentStore` holds its own ETag cache rather than relying on
`st.cache_data`. Re-running is free; re-downloading is not.

The corollary, which matters as soon as more than one person opens the app:
`st.cache_resource` is shared across SESSIONS as well as re-runs, so the store
below is one object serving everybody. Its concurrency rules live in
`store.DocumentStore` — nothing here needs to know them beyond "sync() is safe to
call on every re-run and will not block on someone else's".
"""
from __future__ import annotations

import streamlit as st

from senderra_demo import auth, config
from senderra_demo.blobstore import BlobStore, redact
from senderra_demo.store import DocumentStore
from senderra_demo.views import dashboard, documents, upload

st.set_page_config(page_title="Senderra IDP", page_icon="📄",
                   layout="wide", initial_sidebar_state="expanded")


@st.cache_resource
def _settings():
    return config.load()


@st.cache_resource
def _store(connection_string: str, max_workers: int) -> DocumentStore:
    """One store per process.

    Keyed on the settings that define it rather than taking the Settings object,
    because `st.cache_resource` hashes its arguments and a frozen dataclass of
    strings is a needlessly fragile cache key.
    """
    settings = _settings()
    return DocumentStore(BlobStore(connection_string, max_workers), settings)


def _sidebar(store: DocumentStore, settings) -> dict:
    with st.sidebar:
        st.title("Senderra IDP")
        st.caption("Two-stage split extraction · Content Understanding as a "
                   "pure OCR engine, then two chat calls sharing one cached "
                   "prompt prefix.")

        if st.button("Refresh", width="stretch"):
            st.session_state["force_sync"] = True

        force = st.session_state.pop("force_sync", False)
        # The first sync is the only slow one — it downloads every metrics record
        # that is not already in the process cache. Later ones are a single list
        # call, so the spinner is worded for what is actually happening rather
        # than showing "Loading…" over a 40 ms no-op.
        message = ("Loading pipeline data from Azure Blob Storage — this takes a "
                   "few seconds the first time"
                   if not store.loaded else "Checking for new documents")
        with st.spinner(message):
            stats = store.sync(force=force)
        frame = store.documents()

        if store.last_error:
            st.warning("Azure Blob Storage did not answer. Showing the last "
                       "snapshot that loaded.")
            with st.expander("What went wrong"):
                st.code(store.last_error, language="text")

        st.caption(
            f"{stats.listed} record(s) listed · {stats.downloaded} downloaded · "
            f"{stats.duration_ms} ms"
            + (" · another session was refreshing, so this is its snapshot"
               if stats.deferred else "")
            + "\n\nOnly blobs whose ETag changed are fetched, so a refresh with "
              "nothing new costs one list call.")

        st.divider()

        runs = sorted(frame["run_id"].dropna().unique()) if not frame.empty else []
        default_runs = [settings.upload_run_id] if settings.upload_run_id in runs else runs
        selected_runs = st.multiselect(
            "Runs", runs, default=default_runs,
            help="Benchmark runs (r000-…, r002-…) and demo uploads share the "
                 "storage account. Select none to see everything.")

        statuses = sorted(frame["status"].dropna().unique()) if not frame.empty else []
        selected_statuses = st.multiselect("Status", statuses, default=[])

        types = (sorted(frame["doc_type_predicted"].dropna().unique())
                 if not frame.empty and "doc_type_predicted" in frame else [])
        selected_types = st.multiselect("Document type", types, default=[])

        st.divider()
        st.caption(f"Account `{settings.account_name}` ({settings.credential_kind}) "
                   f"· uploads go to "
                   f"`{settings.container_docs}/{settings.upload_run_id}/`")

        if not settings.app_password:
            # Reachable only off a public host — `Settings.gate_required` makes
            # this configuration refuse to start on one. Said out loud anyway,
            # because "I thought the password was on" is the failure mode.
            st.caption("⚠️ No `APP_PASSWORD` set — anyone who can reach this URL "
                       "can upload documents and start billed pipeline runs.")

    return {"runs": selected_runs, "statuses": selected_statuses,
            "types": selected_types}


def _apply(frame, filters: dict):
    """Filters are subtractive and an empty selection means 'all' — so the
    default view is everything and no filter can silently hide data."""
    if frame.empty:
        return frame
    if filters["runs"]:
        frame = frame[frame["run_id"].isin(filters["runs"])]
    if filters["statuses"]:
        frame = frame[frame["status"].isin(filters["statuses"])]
    if filters["types"] and "doc_type_predicted" in frame:
        frame = frame[frame["doc_type_predicted"].isin(filters["types"])]
    return frame


def main() -> None:
    settings = _settings()

    gaps = settings.missing()
    if gaps:
        st.error(f"Not configured: {', '.join(gaps)}")
        st.write("**Locally**: copy `.env.example` to `.env` and set the storage "
                 "connection string — the same one the pipeline uses. The app "
                 "also reads the repository root `.env` if one exists.")
        st.write("**On Streamlit Community Cloud**: set these in *Manage app → "
                 "Settings → Secrets*, in the format shown in "
                 "`.streamlit/secrets.toml.example`. `APP_PASSWORD` is required "
                 "there, not optional: the app's URL is public, and its upload "
                 "tab starts billed pipeline runs.")
        st.stop()

    # Everything below the gate is behind the shared password. The dashboard is
    # read-only, but it shares a session with an upload tab that is not.
    if not auth.gate(settings.app_password):
        return

    try:
        store = _store(settings.connection_string, settings.max_workers)
    except Exception as exc:                       # noqa: BLE001 — shown, not raised
        # A malformed connection string raises here, before any request is made.
        # `st.cache_resource` caches nothing on failure, so fixing the secret and
        # reloading is enough — no reboot needed.
        st.error("Could not build a storage client from "
                 "`STORAGE_CONNECTION_STRING`.")
        st.code(redact(f"{type(exc).__name__}: {exc}"), language="text")
        st.write("Expected either an account-key string "
                 "(`DefaultEndpointsProtocol=…;AccountName=…;AccountKey=…`) or a "
                 "SAS string "
                 "(`BlobEndpoint=https://<account>.blob.core.windows.net/;"
                 "SharedAccessSignature=sv=…`).")
        st.stop()

    filters = _sidebar(store, settings)
    frame = _apply(store.documents(), filters)

    tab_dashboard, tab_upload, tab_documents = st.tabs(
        ["Dashboard", "Upload", "Documents"])

    with tab_dashboard:
        dashboard.render(frame)
    with tab_upload:
        upload.render(store.blobs, settings)
    with tab_documents:
        documents.render(frame, store)


if __name__ == "__main__":
    main()
