"""Every setting, read once, in one place.

The demo reads the SAME storage account the pipeline writes to and changes
nothing about it. That is the whole design constraint: this app must be
deletable without leaving a trace in the IDP solution.

THREE PLACES A SETTING CAN COME FROM
------------------------------------
Local runs put the connection string in `.env`; Streamlit Community Cloud has no
filesystem to put one on, and injects settings through `st.secrets` instead.
Container hosts (the Dockerfile path) inject real environment variables. All
three feed the same `os.environ`, in that precedence order, so every reader below
stays a plain `os.environ` lookup and no view has to know where it is deployed.

`.env` resolution walks up to the repo root, so a checkout that already has the
pipeline configured needs no second copy of the connection string.
"""
from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass

from dotenv import dotenv_values

_HERE = pathlib.Path(__file__).resolve().parent.parent          # senderra-idp-demo/
_REPO = _HERE.parent                                            # senderra-idp-sol/

#: Streamlit Community Cloud checks the repo out under this path. Used only to
#: decide whether an unset password is a warning or a hard stop — see
#: `Settings.gate_required`. Nothing else in the app behaves differently by host.
_CLOUD_MARKER = "/mount/src"


def _fill(pairs) -> None:
    """Set each key in `os.environ` unless it already holds a non-empty value.

    ⚠️ An EMPTY value must not count as set, which is why this does not use
    `load_dotenv(override=False)`. The most likely user mistake is following the
    README's `cp .env.example .env` and not filling it in — that file defines
    `STORAGE_CONNECTION_STRING=` with no value, and `override=False` would treat
    that blank as a real setting and stop the repo-root `.env` from supplying
    the real one. The app would then report "not configured" while sitting next
    to a working configuration.
    """
    for key, value in pairs:
        if value and not os.environ.get(key, "").strip():
            os.environ[str(key)] = str(value)


def _load_secrets() -> None:
    """Streamlit's secrets, if there are any. This is how Cloud is configured.

    Everything here is defensive on purpose. `st.secrets` reads a file that does
    not exist on a normal local run, and older Streamlit versions raise rather
    than return empty when it is absent — so a missing secrets file must not be
    able to stop the app from starting from `.env`. Nested sections are skipped:
    only flat string values are settings.
    """
    try:
        import streamlit as st

        _fill((k, v) for k, v in st.secrets.items() if isinstance(v, (str, int, float)))
    except Exception:                          # noqa: BLE001 — absence is normal
        pass


def _load_env() -> None:
    """The demo folder's `.env`, then the repo root's. First non-empty wins."""
    for candidate in (_HERE / ".env", _REPO / ".env"):
        if candidate.exists():
            _fill(dotenv_values(candidate).items())


# Order is precedence: a real environment variable beats everything (a container
# that injects settings needs no file at all), then Cloud secrets, then files.
_load_secrets()
_load_env()


def _s(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _i(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    connection_string: str
    container_docs: str
    container_work: str
    container_results: str
    container_metrics: str
    upload_run_id: str
    sync_ttl_seconds: int
    max_workers: int
    app_password: str
    upload_batch_mb: int

    @property
    def account_name(self) -> str:
        """Pulled out of the connection string purely for display.

        Two forms have to work. An account-key string carries `AccountName=`
        directly; a SAS string carries no account name at all, only
        `BlobEndpoint=https://<account>.blob.core.windows.net/`, so the name has
        to come out of the host. Both are valid inputs to
        `BlobServiceClient.from_connection_string`, and the SAS form is the one a
        public deployment should be using.
        """
        for part in self.connection_string.split(";"):
            if part.startswith("AccountName="):
                return part.split("=", 1)[1]
            if part.startswith("BlobEndpoint="):
                host = part.split("=", 1)[1].split("//")[-1].split("/")[0]
                return host.split(".")[0] or "unknown"
        return "unknown"

    @property
    def credential_kind(self) -> str:
        """`account key` or `SAS token`, for the sidebar. Worth showing: it is the
        difference between "if this URL leaks, four containers are exposed until
        the SAS expires" and "the whole storage account is."""
        if "SharedAccessSignature=" in self.connection_string:
            return "SAS token"
        if "AccountKey=" in self.connection_string:
            return "account key"
        return "unknown"

    @property
    def gate_required(self) -> bool:
        """Whether a missing password is fatal rather than merely unwise.

        On a host whose URL is public by construction, an ungated app with a
        working write credential is a stranger's licence to spend the Azure
        budget — so the app refuses to serve rather than defaulting open. Locally
        the same omission is just a convenience and gets a warning.
        """
        return str(_HERE).startswith(_CLOUD_MARKER) or _s("REQUIRE_PASSWORD") == "true"

    def missing(self) -> list[str]:
        """Required settings that are not set, for the startup banner."""
        gaps = [] if self.connection_string else ["STORAGE_CONNECTION_STRING"]
        if self.gate_required and not self.app_password:
            gaps.append("APP_PASSWORD")
        return gaps


def load() -> Settings:
    return Settings(
        connection_string=_s("STORAGE_CONNECTION_STRING"),
        container_docs=_s("CONTAINER_DOCS", "docs-in"),
        container_work=_s("CONTAINER_WORK", "work"),
        container_results=_s("CONTAINER_RESULTS", "results"),
        container_metrics=_s("CONTAINER_METRICS", "metrics"),
        upload_run_id=_s("UPLOAD_RUN_ID", "ui"),
        sync_ttl_seconds=_i("SYNC_TTL_SECONDS", 20),
        # 16, not 32. On a shared single-CPU host this number is multiplied by
        # however many people are looking: five simultaneous first loads at 32
        # would be 160 sockets against one storage account, which is how you get
        # throttled (503) instead of fast. 16 still turns a 300-document first
        # load into ~2 s, and the ETag cache means it happens once per process.
        max_workers=_i("MAX_WORKERS", 16),
        app_password=_s("APP_PASSWORD"),
        # Streamlit holds every uploaded file in memory, and `getvalue()` takes a
        # second copy — so a batch is charged twice against a container that has
        # ~1 GB total for the app, the pandas frame and everyone else's session.
        upload_batch_mb=_i("UPLOAD_BATCH_MB", 100),
    )
