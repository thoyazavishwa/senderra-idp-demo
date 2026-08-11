"""Every setting, read once, in one place.

The demo reads the SAME storage account the pipeline writes to and changes
nothing about it. That is the whole design constraint: this app must be
deletable without leaving a trace in the IDP solution.

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


def _load_env() -> None:
    """First non-empty value wins, searching the demo folder then the repo root.

    ⚠️ An EMPTY value must not count as set, which is why this does not use
    `load_dotenv(override=False)`. The most likely user mistake is following the
    README's `cp .env.example .env` and not filling it in — that file defines
    `STORAGE_CONNECTION_STRING=` with no value, and `override=False` would treat
    that blank as a real setting and stop the repo-root `.env` from supplying
    the real one. The app would then report "not configured" while sitting next
    to a working configuration.

    A real environment variable still beats both files, so a container that
    injects settings needs no file at all.
    """
    for candidate in (_HERE / ".env", _REPO / ".env"):
        if not candidate.exists():
            continue
        for key, value in dotenv_values(candidate).items():
            if value and not os.environ.get(key, "").strip():
                os.environ[key] = value


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

    @property
    def account_name(self) -> str:
        """Pulled out of the connection string purely for display."""
        for part in self.connection_string.split(";"):
            if part.startswith("AccountName="):
                return part.split("=", 1)[1]
        return "unknown"

    def missing(self) -> list[str]:
        """Required settings that are not set, for the startup banner."""
        return [] if self.connection_string else ["STORAGE_CONNECTION_STRING"]


def load() -> Settings:
    return Settings(
        connection_string=_s("STORAGE_CONNECTION_STRING"),
        container_docs=_s("CONTAINER_DOCS", "docs-in"),
        container_work=_s("CONTAINER_WORK", "work"),
        container_results=_s("CONTAINER_RESULTS", "results"),
        container_metrics=_s("CONTAINER_METRICS", "metrics"),
        upload_run_id=_s("UPLOAD_RUN_ID", "ui"),
        sync_ttl_seconds=_i("SYNC_TTL_SECONDS", 20),
        max_workers=_i("MAX_WORKERS", 32),
    )
