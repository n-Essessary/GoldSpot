from __future__ import annotations

import pytest

from db import writer


class ArraySubscriptError(Exception):
    pass


@pytest.mark.parametrize(
    "value,expected",
    [
        ([], []),
        ([1], [1]),
        ([1, 2, 3], [1, 2, 3]),
        ([1, [2, 3], "x"], [1, 2, 3, "x"]),
    ],
)
def test_flatten_param_1d(value, expected):
    assert writer._flatten_param(value) == expected


@pytest.mark.asyncio
async def test_query_index_history_no_array_dimension_error(monkeypatch):
    calls = {"n": 0}

    class FakePool:
        async def fetch(self, query, *params):
            calls["n"] += 1
            # emulate legacy SQL failure signature only when old array_agg pattern is present
            if "unnest(array_agg(sources)" in query:
                raise ArraySubscriptError("cannot accumulate arrays of different dimensionality")
            # verify query params are scalar/1D-safe
            for p in params:
                if isinstance(p, list):
                    for x in p:
                        assert not isinstance(x, list)
            return []

    async def fake_get_pool():
        return FakePool()

    monkeypatch.setattr(writer, "get_pool", fake_get_pool)
    out = await writer.query_index_history("(EU) Anniversary", "all", 24, 200)
    assert out == [] and calls["n"] == 1
