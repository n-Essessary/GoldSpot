from __future__ import annotations

import pytest

from parser.g2g_parser import _parse_title


@pytest.mark.parametrize(
    "title,expected",
    [
        (
            "Spineshatter [EU - Anniversary] - Alliance",
            ("Spineshatter", "EU", "Anniversary", "Alliance"),
        ),
        (
            "Lava Lash [EU - Seasonal] - Horde",
            ("Lava Lash", "EU", "Seasonal", "Horde"),
        ),
        (
            "Firemaw [EU] - Alliance",
            ("Firemaw", "EU", "Classic", "Alliance"),
        ),
        (
            "Classic Era Gold EU",
            ("Classic Era Gold EU", "EU", "Classic Era", "Horde"),
        ),
        (
            "",
            ("", "", "", "Horde"),
        ),
    ],
)
def test_parse_title_cases(title, expected):
    assert _parse_title(title) == expected


def test_parse_title_none_safe():
    out = _parse_title(None)  # type: ignore[arg-type]
    assert isinstance(out, tuple) and len(out) == 4
