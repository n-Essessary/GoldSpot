from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bs4 import BeautifulSoup

from parser.funpay_parser import _parse_float, _parse_item, _parse_html


def test_parse_float_us_format():
    assert _parse_float("1,234.56") == 1234.56


def test_parse_float_eu_format():
    assert _parse_float("1.234,56") == 1234.56


def test_parse_float_none():
    assert _parse_float(None) is None


def test_parse_item_near_zero_guard_raises():
    html = """
    <a class="tc-item" href="/x?id=1" data-online="1">
      <span class="tc-server">(EU) Classic</span>
      <span class="tc-side">Horde</span>
      <span class="tc-amount">1</span>
      <span class="tc-price">0.001</span>
      <span class="tc-seller">s</span>
    </a>
    """
    item = BeautifulSoup(html, "html.parser").select_one(".tc-item")
    with pytest.raises(ValueError):
        _parse_item(item, datetime.now(timezone.utc))  # type: ignore[arg-type]


def test_parse_html_empty_returns_empty():
    assert _parse_html("", datetime.now(timezone.utc)) == []


def test_parse_html_no_items_returns_empty():
    assert _parse_html("<div>none</div>", datetime.now(timezone.utc)) == []


def test_parse_html_only_offline_returns_empty():
    html = """
    <a class="tc-item" href="/x?id=1" data-online="0">
      <span class="tc-server">(EU) Classic</span>
      <span class="tc-side">Horde</span>
      <span class="tc-amount">1000</span>
      <span class="tc-price">3.00</span>
      <span class="tc-seller">s</span>
    </a>
    """
    assert _parse_html(html, datetime.now(timezone.utc)) == []


def test_parse_item_per_unit_price_is_not_near_zero():
    html = """
    <a class="tc-item" href="/x?id=1" data-online="1">
      <span class="tc-server">(EU) Classic</span>
      <span class="tc-side">Horde</span>
      <span class="tc-amount">7000000</span>
      <span class="tc-price">0.50</span>
      <span class="tc-seller">s</span>
    </a>
    """
    item = BeautifulSoup(html, "html.parser").select_one(".tc-item")
    offer = _parse_item(item, datetime.now(timezone.utc))  # type: ignore[arg-type]
    assert offer.price_per_1k == 500.0


def test_parse_item_correct_price_per_1k():
    html = """
    <a class="tc-item" href="/x?id=1" data-online="1">
      <span class="tc-server">(EU) Classic</span>
      <span class="tc-side">Horde</span>
      <span class="tc-amount">83</span>
      <span class="tc-price">0.0139</span>
      <span class="tc-seller">s</span>
    </a>
    """
    item = BeautifulSoup(html, "html.parser").select_one(".tc-item")
    offer = _parse_item(item, datetime.now(timezone.utc))  # type: ignore[arg-type]
    assert offer.price_per_1k == 13.9
