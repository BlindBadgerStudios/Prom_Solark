from __future__ import annotations

from app.main import build_session


def test_session_retries_only_get_and_handles_429():
    retry = build_session().get_adapter("https://api.example.invalid").max_retries
    assert set(retry.allowed_methods) == {"GET"}
    assert "POST" not in retry.allowed_methods
    assert 429 in retry.status_forcelist
    assert retry.respect_retry_after_header is True
    assert not retry.is_retry("POST", 503)
    assert retry.is_retry("GET", 429)
