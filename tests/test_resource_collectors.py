from resource_collectors import (
    format_bytes,
    format_pct,
    group_private_bytes,
    parse_gputaskman,
    collect_gputaskman,
    collect_resource_snapshot,
    snapshot_display_fields,
    GPUTASKMAN_EXE,
)


def test_format_bytes():
    assert format_bytes(7301 * 1024 * 1024).endswith("GB") or "MB" in format_bytes(7301 * 1024 * 1024)
    assert format_bytes(512) == "512 B"


def test_format_pct():
    assert format_pct(50, 100) == "50%"
    assert format_pct(None, 100) == "-"


def test_group_private_bytes_sums_same_name():
    rows = [
        {"name": "chrome.exe", "private_bytes": 100, "working_set_bytes": 40},
        {"name": "chrome.exe", "private_bytes": 50, "working_set_bytes": 20},
        {"name": "node.exe", "private_bytes": 80, "working_set_bytes": 10},
    ]
    groups = group_private_bytes(rows, top_n=2)
    assert groups[0]["name"] == "chrome.exe"
    assert groups[0]["count"] == 2
    assert groups[0]["private_bytes"] == 150
    assert groups[1]["name"] == "node.exe"


def _sample_gputaskman():
    # Keys match live `gputaskman --json` on this machine (no top-level gpus[]).
    return {
        "gpu": "NVIDIA GeForce RTX 5070 Ti",
        "vram_total": 17094934528,
        "vram_used": 15595106304,
        "device": {
            "name": "NVIDIA GeForce RTX 5070 Ti",
            "mem_total": 17094934528,
            "mem_used": 15500496896,
            "temp": 34,
            "power_w": 13.293,
            "power_limit_w": 300.0,
        },
        "processes": [
            {
                "name": "strata.exe",
                "pid": 27324,
                "vram_bytes": 12835364864,
                "model_files": [],
                "installed_models": [],
            },
            {
                "name": "chrome.exe",
                "pid": 28436,
                "vram_bytes": 42254336,
                "model_files": ["weights.bin"],
                "installed_models": ["llama-7b"],
            },
            {
                "name": "strata-vision.exe",
                "pid": 26748,
                "vram_bytes": 1350070272,
                "model_files": [],
                "installed_models": [],
            },
        ],
    }


def test_parse_gputaskman_totals_and_top():
    sample = _sample_gputaskman()
    parsed = parse_gputaskman(sample)
    assert parsed["vram_total_bytes"] == 17094934528
    assert parsed["vram_used_bytes"] == 15595106304
    assert parsed["gpu_name"] == "NVIDIA GeForce RTX 5070 Ti"
    assert parsed["temp_c"] == 34
    assert parsed["power_w"] == 13.293
    assert parsed["top_processes"][0]["name"].lower().startswith("strata")
    assert parsed["top_processes"][0]["vram_bytes"] == 12835364864
    assert parsed["top_processes"][0]["pid"] == 27324
    assert parsed["top_processes"][1]["name"] == "strata-vision.exe"
    assert "weights.bin" in parsed["models"]
    assert "llama-7b" in parsed["models"]


def test_parse_gputaskman_falls_back_to_device_mem():
    sample = {
        "device": {
            "name": "RTX Fallback",
            "mem_total": 1000,
            "mem_used": 400,
            "temp": 50,
            "power_w": 99.0,
        },
        "processes": [],
    }
    parsed = parse_gputaskman(sample)
    assert parsed["vram_total_bytes"] == 1000
    assert parsed["vram_used_bytes"] == 400
    assert parsed["gpu_name"] == "RTX Fallback"
    assert parsed["temp_c"] == 50
    assert parsed["power_w"] == 99.0
    assert parsed["top_processes"] == []
    assert parsed["models"] == []


def test_collect_gputaskman_soft_fails_when_missing(monkeypatch, tmp_path):
    missing = str(tmp_path / "no-such-gputaskman.exe")
    monkeypatch.setattr("resource_collectors.GPUTASKMAN_EXE", missing)
    result = collect_gputaskman(timeout=1.0)
    assert "error" in result
    assert "missing" in result["error"].lower() or missing in result["error"]


def _fake_process_groups():
    return {
        "ram_used_bytes": 8_000_000_000,
        "ram_total_bytes": 16_000_000_000,
        "commit_bytes": 10_000_000_000,
        "commit_limit_bytes": 32_000_000_000,
        "groups": [
            {"name": "chrome.exe", "count": 12, "private_bytes": 2_500_000_000, "working_set_bytes": 1_000_000_000},
            {"name": "node.exe", "count": 3, "private_bytes": 800_000_000, "working_set_bytes": 400_000_000},
        ],
        "captured_at": 1_700_000_000.0,
    }


def _fake_gpu_ok():
    return {
        "vram_used_bytes": 12_000_000_000,
        "vram_total_bytes": 16_000_000_000,
        "gpu_name": "RTX Test",
        "temp_c": 40,
        "power_w": 100.0,
        "top_processes": [
            {"name": "strata.exe", "pid": 1, "vram_bytes": 11_000_000_000},
            {"name": "chrome.exe", "pid": 2, "vram_bytes": 50_000_000},
        ],
        "models": [],
    }


def test_snapshot_prefers_gputaskman_for_vram_top(monkeypatch):
    monkeypatch.setattr("resource_collectors.collect_process_groups", lambda top_n=5: _fake_process_groups())
    monkeypatch.setattr("resource_collectors.collect_gputaskman", lambda **kwargs: _fake_gpu_ok())
    snap = collect_resource_snapshot(top_n=5)
    fields = snapshot_display_fields(snap)
    assert "strata.exe" in fields["vram_top"]
    assert fields["vram_used"] != "-"
    assert "error" not in snap or "gpu_error" not in snap or snap.get("gpu_error") in (None, "")
    assert snap.get("gpu", {}).get("top_processes", [{}])[0]["name"] == "strata.exe"


def test_snapshot_prefers_process_groups_for_top_app(monkeypatch):
    monkeypatch.setattr("resource_collectors.collect_process_groups", lambda top_n=5: _fake_process_groups())
    monkeypatch.setattr("resource_collectors.collect_gputaskman", lambda **kwargs: _fake_gpu_ok())
    snap = collect_resource_snapshot(top_n=5)
    fields = snapshot_display_fields(snap)
    assert fields["top_app"].startswith("chrome.exe")
    assert "2.50 GB" in fields["top_app"] or "2.5 GB" in fields["top_app"]
    assert fields["top_app_n"] == "12"
    assert snap["groups"][0]["name"] == "chrome.exe"
    assert snap["ram_used_bytes"] == 8_000_000_000
    assert snap["commit_bytes"] == 10_000_000_000


def test_snapshot_soft_fails_when_gputaskman_errors(monkeypatch):
    monkeypatch.setattr("resource_collectors.collect_process_groups", lambda top_n=5: _fake_process_groups())
    monkeypatch.setattr(
        "resource_collectors.collect_gputaskman",
        lambda **kwargs: {"error": "gputaskman boom"},
    )
    snap = collect_resource_snapshot(top_n=5)
    fields = snapshot_display_fields(snap)
    assert fields["vram_used"] == "-"
    assert fields["vram_top"] == "-"
    assert snap.get("gpu_error") == "gputaskman boom"
    assert snap.get("gpu") == {}
    assert fields["top_app"].startswith("chrome.exe")
    assert fields["ram_phys"] != "-"
    assert fields["ram_commit"] != "-"
