from metrics_client import metrics_headers


def test_headers_without_key_have_no_authorization():
    h = metrics_headers("")
    assert h["Accept"] == "application/json"
    assert "Authorization" not in h


def test_headers_strip_and_send_bearer():
    h = metrics_headers("  secret-key  ")
    assert h["Authorization"] == "Bearer secret-key"
    assert h["Accept"] == "application/json"


def test_headers_none_like_empty():
    assert "Authorization" not in metrics_headers(None)
