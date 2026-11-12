import pytest

from app.access_context import normalize_access_context


@pytest.mark.parametrize("raw, expected", [("", ""), ("/", ""), (" /ops ", "/ops"),
                                             ("/team/ops", "/team/ops")])
def test_access_context_normalization(raw, expected):
    assert normalize_access_context(raw) == expected


@pytest.mark.parametrize("raw", ["ops", "/ops/", "//ops", "/a/../b", "/a?b", "/a%2Fb",
                                    "/a b", "/" + "a" * 81])
def test_access_context_rejects_ambiguous_paths(raw):
    with pytest.raises(ValueError):
        normalize_access_context(raw)
