from __future__ import annotations

import json
import os
import subprocess
import time
from collections import defaultdict
from typing import Any

GPUTASKMAN_EXE = os.environ.get(
    "GPUTASKMAN_EXE",
    r"C:\AI_Server\Tools\gputaskman\gputaskman-0.3.4-windows-x64\gputaskman.exe",
)


def format_bytes(n: int | None) -> str:
    if n is None:
        return "-"
    n = int(n)
    for unit, div in (("GB", 1_000_000_000), ("MB", 1_000_000), ("KB", 1_000)):
        if n >= div:
            val = n / div
            return f"{val:.2f} {unit}" if val < 10 else f"{val:.1f} {unit}"
    return f"{n} B"


def format_pct(part, whole) -> str:
    if part is None or not whole:
        return "-"
    return f"{round(part / whole * 100)}%"


def group_private_bytes(rows: list[dict], top_n: int = 8) -> list[dict]:
    buckets: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "private_bytes": 0, "working_set_bytes": 0}
    )
    for row in rows:
        name = row.get("name") or "?"
        b = buckets[name]
        b["count"] += 1
        b["private_bytes"] += int(row.get("private_bytes") or 0)
        b["working_set_bytes"] += int(row.get("working_set_bytes") or 0)
    ranked = sorted(
        ({"name": k, **v} for k, v in buckets.items()),
        key=lambda g: g["private_bytes"],
        reverse=True,
    )
    return ranked[:top_n]


def _commit_charge_windows() -> tuple[int | None, int | None]:
    cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        "(Get-Counter '\\Memory\\Committed Bytes','\\Memory\\Commit Limit').CounterSamples | "
        "ForEach-Object { $_.CookedValue }",
    ]
    try:
        out = subprocess.check_output(cmd, text=True, timeout=5)
        vals = [float(x) for x in out.split() if x.strip()]
        if len(vals) >= 2:
            return int(vals[0]), int(vals[1])
    except (subprocess.SubprocessError, ValueError, OSError):
        pass
    return None, None


def collect_process_groups(top_n: int = 8) -> dict[str, Any]:
    try:
        import psutil
    except ImportError as e:
        raise RuntimeError("psutil required for process groups") from e
    rows = []
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            info = p.info
            mi = info.get("memory_info")
            if not mi:
                continue
            rows.append(
                {
                    "name": info.get("name") or "?",
                    "private_bytes": int(getattr(mi, "private", 0) or 0),
                    "working_set_bytes": int(getattr(mi, "rss", 0) or 0),
                }
            )
        except (psutil.Error, OSError):
            continue
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    commit_bytes, commit_limit_bytes = _commit_charge_windows()
    if commit_bytes is None:
        commit_bytes = int(vm.total - vm.available + getattr(swap, "used", 0))
    if commit_limit_bytes is None:
        commit_limit_bytes = int(vm.total + swap.total)
    return {
        "ram_used_bytes": int(vm.total - vm.available),
        "ram_total_bytes": int(vm.total),
        "commit_bytes": int(commit_bytes),
        "commit_limit_bytes": int(commit_limit_bytes),
        "groups": group_private_bytes(rows, top_n=top_n),
        "captured_at": time.time(),
    }


def parse_gputaskman(data: dict) -> dict[str, Any]:
    device = data.get("device") or {}
    vram_used = data.get("vram_used")
    if vram_used is None:
        vram_used = device.get("mem_used")
    vram_total = data.get("vram_total")
    if vram_total is None:
        vram_total = device.get("mem_total")
    gpu_name = data.get("gpu") or device.get("name") or ""
    temp_c = device.get("temp")
    power_w = device.get("power_w")

    top_processes: list[dict[str, Any]] = []
    models: list[Any] = []
    seen_models: set[str] = set()
    for row in data.get("processes") or []:
        name = row.get("name") or "?"
        pid = row.get("pid")
        vram_bytes = int(row.get("vram_bytes") or 0)
        top_processes.append({"name": name, "pid": pid, "vram_bytes": vram_bytes})
        for key in ("model_files", "installed_models"):
            for item in row.get(key) or []:
                label = item if isinstance(item, str) else str(item)
                if label and label not in seen_models:
                    seen_models.add(label)
                    models.append(item)

    top_processes.sort(key=lambda p: p["vram_bytes"], reverse=True)
    return {
        "vram_used_bytes": int(vram_used) if vram_used is not None else 0,
        "vram_total_bytes": int(vram_total) if vram_total is not None else 0,
        "gpu_name": gpu_name,
        "temp_c": temp_c,
        "power_w": power_w,
        "top_processes": top_processes,
        "models": models,
    }


def collect_gputaskman(timeout: float = 8.0) -> dict[str, Any]:
    exe = GPUTASKMAN_EXE
    if not os.path.isfile(exe):
        return {"error": f"missing {exe}"}
    try:
        kwargs: dict[str, Any] = {"timeout": timeout}
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        raw = subprocess.check_output([exe, "--json"], **kwargs)
        data = json.loads(raw.decode("utf-8", "replace"))
        return parse_gputaskman(data)
    except Exception as e:
        return {"error": str(e)}
