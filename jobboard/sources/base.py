"""
Shared source interface, HTTP helpers and YAML config loading.

Every job source (Greenhouse, Lever, Ashby, Reed, Adzuna, LinkedIn-via-Apify)
implements the `Source` interface defined here, so refresh.py can treat them
all the same way: call `is_configured()` to see whether it should even try,
then call `safe_fetch()` to get a list of Job objects back without worrying
about that one source crashing the whole refresh.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import httpx
import yaml

logger = logging.getLogger("jobboard.sources")

# jobboard/sources/base.py -> jobboard/sources -> jobboard -> project root.
# We compute this so config/*.yaml can be loaded no matter what directory
# the app happens to be run from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"

# A descriptive User-Agent, as the spec asks for, so any API we call can see
# which app is making requests (some APIs block generic/blank user agents).
USER_AGENT = "london-early-careers-jobs/1.0 (+https://github.com/)"
TIMEOUT = 20.0          # seconds, per request
MAX_CONCURRENCY = 5     # how many requests a source may have in flight at once
MAX_RETRIES = 3         # retry attempts before giving up on a single request


def load_yaml(name: str) -> Any:
    """Load a YAML file from config/, e.g. load_yaml('companies.yaml')."""
    path = CONFIG_DIR / name
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def request_with_retry(
    client: httpx.Client, method: str, url: str, **kwargs
) -> httpx.Response:
    """
    Make an HTTP request, retrying with exponential backoff (1s, 2s, 4s) if
    the server responds with 429 (rate limited) or any 5xx (server error),
    or if the connection itself fails. This is shared by every source so we
    don't have to write the same retry loop six times.
    """
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.request(method, url, timeout=TIMEOUT, **kwargs)
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = 2 ** attempt  # 1s, then 2s, then 4s
                logger.warning(
                    "%s %s -> %s, retrying in %ss", method, url, resp.status_code, wait
                )
                time.sleep(wait)
                continue
            return resp  # success, or a 4xx we shouldn't retry (e.g. 404, 401)
        except httpx.RequestError as exc:
            # Network-level failure (DNS, connection refused, timeout, etc.)
            last_exc = exc
            wait = 2 ** attempt
            logger.warning("%s %s -> %s, retrying in %ss", method, url, exc, wait)
            time.sleep(wait)
    if last_exc:
        raise last_exc
    return resp  # every attempt returned a bad status code; hand back the last one


class Source(ABC):
    """Interface every job source implements."""

    name: str = "base"

    @abstractmethod
    def is_configured(self) -> bool:
        """Whether this source has what it needs (e.g. an API key) to run at all."""

    @abstractmethod
    def fetch(self) -> list:
        """Fetch and return a list of jobboard.models.Job. May raise on failure."""

    def safe_fetch(self) -> list:
        """
        Wrapper around fetch() that catches any exception and logs it,
        returning an empty list instead of raising. refresh.py calls this
        (not fetch() directly) so that one broken source — a changed API,
        a timeout, a bad response — never takes down the whole refresh.
        """
        try:
            return self.fetch()
        except Exception:
            logger.exception("Source %s failed", self.name)
            return []
