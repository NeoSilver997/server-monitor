from resource_collectors import format_bytes, format_pct, group_private_bytes


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
