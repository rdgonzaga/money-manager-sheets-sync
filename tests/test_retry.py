import pytest

import money_manager_to_notion as m


def test_returns_result_on_first_success(monkeypatch):
    monkeypatch.setattr(m.time, "sleep", lambda *_: pytest.fail("should not sleep"))

    result = m.retry_with_backoff(lambda: "ok")

    assert result == "ok"


def test_retries_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr(m.time, "sleep", lambda seconds: sleeps.append(seconds))

    attempts = {"count": 0}

    def flaky():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise ConnectionError("transient")
        return "recovered"

    result = m.retry_with_backoff(flaky, max_retries=3, base_delay=1)

    assert result == "recovered"
    assert attempts["count"] == 3
    assert sleeps == [1, 2]


def test_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)

    def always_fails():
        raise ConnectionError("permanent")

    with pytest.raises(ConnectionError):
        m.retry_with_backoff(always_fails, max_retries=3, base_delay=1)
