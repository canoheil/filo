from services.quota import quota_exhausted, idem_key, utc_today


def test_quota_resets_on_new_day():
    assert quota_exhausted("2000-01-01", 999, utc_today(), 30) is False


def test_quota_blocks_at_limit():
    today = utc_today()
    assert quota_exhausted(today, 30, today, 30) is True
    assert quota_exhausted(today, 29, today, 30) is False


def test_idem_key_format():
    assert idem_key("conn", 1, 2, "in") == "ai_reply:conn:1:2:in"
