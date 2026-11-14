from __future__ import annotations

import json
import urllib.request


def metrics_headers(api_key: str = "") -> dict[str, str]:
    """Headers for Strata /metrics (and other JSON metrics endpoints)."""
    headers = {"Accept": "application/json"}
    key = (api_key or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def fetch_metrics(url, api_key="", timeout=3.0):
    req = urllib.request.Request(url, method="GET", headers=metrics_headers(api_key))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))
