import pytest
from meva_fetch import CANDIDATE_KEYS, clip_url, main


def test_clip_url_is_anonymous_https_with_a_quoted_key():
    assert clip_url("drops-123-r13/a b.avi") == (
        "https://mevadata-public-01.s3.amazonaws.com/drops-123-r13/a%20b.avi"
    )


def test_candidate_keys_are_indoor_r13_clips():
    for key in CANDIDATE_KEYS:
        assert key.startswith("drops-123-r13/") and key.endswith(".r13.avi")
        assert any(f".{site}." in key for site in ("school", "bus", "admin"))


def test_main_rejects_an_empty_range():
    with pytest.raises(SystemExit):
        main(["some/key.avi", "--start", "5", "--end", "2"])
