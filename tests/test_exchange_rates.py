"""Reference-rate cache tests use synthetic XML and isolated local libraries."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

import backend
import exchange_rates as fx

NOW = datetime(2026, 11, 13, 5, 0, tzinfo=timezone.utc)


def feed(day="2026-11-12", usd="1.25", cny="9", extra=""):
    return (f'''<?xml version="1.0" encoding="UTF-8"?>
    <gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
       xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
      <Cube><Cube time="{day}"><Cube currency="USD" rate="{usd}"/>
      <Cube currency="CNY" rate="{cny}"/><Cube currency="JPY" rate="150"/>
      {extra}</Cube></Cube></gesmes:Envelope>''').encode()


@pytest.fixture
def library(tmp_path, monkeypatch):
    backend.init_storage(tmp_path / "data")
    monkeypatch.setattr(fx, "_clock", lambda: NOW)
    def forbidden():
        pytest.fail("No test may request a real reference-rate endpoint")
    monkeypatch.setattr(fx, "_fetch_daily", forbidden)
    return tmp_path


def seed(day="2026-11-12", fetched=None, **rates):
    parsed = fx.parse_ecb(feed(day, **rates), now=NOW)
    fx._write_meta(fx.CACHE_KEY, {"version": 1, **parsed,
                                "fetched_at": fx._stamp(fetched or NOW)})


def test_ecb_xml_has_eur_base_and_explicit_publication_date(library):
    result = fx.parse_ecb(feed(), now=NOW)
    assert result == {"base": "EUR", "as_of": "2026-11-12",
                      "rates": {"EUR": "1", "USD": "1.25", "CNY": "9", "JPY": "150"}}


@pytest.mark.parametrize("body", [
    b"", b"x" * (fx.MAX_RESPONSE_BYTES + 1), b"<html>Login required</html>",
    b"<malformed", b"<!DOCTYPE x><x/>", b"<!ENTITY x '1.25'><x/>",
    "<!DOCTYPE x><x/>".encode("utf-16"), b"\x00<\x00x\x00/\x00>",
    feed(extra='<Cube currency="USD" rate="1.25"/>'),
    feed(extra='<Cube currency="usd" rate="1.25"/>'),
    feed(extra='<Cube currency="EUR" rate="1"/>'),
    feed(extra='<Cube currency="GBP" rate="0.8" extra="untrusted"/>'),
    feed().replace(b'<Cube currency="CNY" rate="9"/>', b''),
    feed().replace(b'</Cube></Cube>', b'</Cube><Cube time="2026-11-11"/></Cube>'),
])
def test_invalid_or_ambiguous_feed_is_rejected(library, body):
    with pytest.raises(fx.RateDataError):
        fx.parse_ecb(body, now=NOW)


@pytest.mark.parametrize("rate", ["0", "-1", "NaN", "Infinity", "1e10000", "1,25", "1.2.3",
                                  "1000000000001", "0.0000000000001", "+1.25"])
def test_rates_must_be_bounded_positive_decimals(library, rate):
    with pytest.raises(fx.RateDataError):
        fx.parse_ecb(feed(usd=rate), now=NOW)


@pytest.mark.parametrize("day", ["2026-11-14", "2026-11-05", "2026-02-30", "2026-1-1", "never"])
def test_future_old_and_invalid_dates_cannot_be_published(library, day):
    with pytest.raises(fx.RateDataError):
        fx.parse_ecb(feed(day), now=NOW)


def test_cross_currency_conversion_and_inverse_use_eur_cross_rates(library):
    seed()
    quote = fx.convert_cached("10.54", "USD", "CNY")
    assert quote["available"] and quote["status"] == "ok"
    assert quote["rate"] == "7.2" and quote["converted_amount"] == "75.888"
    assert quote["from_currency"] == "USD" and quote["to_currency"] == "CNY"
    assert quote["as_of"] == "2026-11-12" and quote["source_name"] == "ECB参考汇率"
    assert quote["source_url"] == fx.SOURCE_URL
    inverse = fx.convert_cached("75.888", " cny ", "usd")
    assert abs(Decimal(inverse["converted_amount"]) - Decimal("10.54")) < Decimal("1e-30")
    # An increase over the manual baseline is a real comparison, never capped.
    assert Decimal(quote["converted_amount"]) / Decimal("74.62") * 100 > 100
    euro = fx.convert_cached("1.25", "USD", "EUR")
    assert euro["converted_amount"] == "1"


def test_same_currency_needs_no_cache_or_network(library):
    result = fx.convert_cached("12.50", "USD", "USD")
    assert result["available"] and result["rate"] == "1" and result["converted_amount"] == "12.5"
    assert result["as_of"] is None and result["source_name"] == "同币种"


@pytest.mark.parametrize("amount", [True, {}, [], "NaN", "Infinity", "-1", "", "1e10000", "1e-10000"])
def test_invalid_amount_fails_without_fetching(library, amount):
    result = fx.convert_cached(amount, "USD", "CNY")
    assert result["available"] is False and result["converted_amount"] is None


@pytest.mark.parametrize("source,target", [("USD", "XYZ"), ("BTC", "CNY"), ("US", "CNY"), (None, "CNY")])
def test_unknown_or_invalid_currency_is_not_guessed(library, source, target):
    seed()
    result = fx.convert_cached("1", source, target)
    assert not result["available"] and result["status"] == "unsupported"


def test_conversion_has_no_network_or_writes_even_inside_price_transaction(library, monkeypatch):
    seed()
    writes = []
    real_connection = backend._connection
    @contextmanager
    def observe(write=False):
        if write:
            writes.append(True)
        with real_connection(write=write) as conn:
            yield conn
    monkeypatch.setattr(backend, "_connection", observe)
    with real_connection(write=True):
        assert fx.convert_cached("10.54", "USD", "CNY")["converted_amount"] == "75.888"
    assert writes == []


def test_refresh_fetches_outside_connections_and_reuses_daily_cache(library, monkeypatch):
    active = []
    calls = []
    real_connection = backend._connection
    @contextmanager
    def observe(write=False):
        active.append(write)
        try:
            with real_connection(write=write) as conn:
                yield conn
        finally:
            active.pop()
    def fetch():
        assert active == [], "No DB transaction or read connection may surround network fetching"
        calls.append(True)
        return feed()
    monkeypatch.setattr(backend, "_connection", observe)
    monkeypatch.setattr(fx, "_fetch_daily", fetch)
    assert fx.refresh_rates()["available"]
    assert fx.refresh_rates()["available"] and len(calls) == 1
    monkeypatch.setattr(fx, "_clock", lambda: NOW + timedelta(hours=24))
    assert fx.refresh_rates()["available"] and len(calls) == 2
    assert fx.get_snapshot()["as_of"] == "2026-11-12", "Weekend data retains its actual trading date"


def test_failure_keeps_cached_quote_and_explicit_cached_message(library, monkeypatch):
    seed(fetched=NOW - timedelta(days=1))
    def fail():
        raise OSError("unrelated private diagnostics must not escape")
    monkeypatch.setattr(fx, "_fetch_daily", fail)
    result = fx.refresh_rates()
    assert result["available"] and result["as_of"] == "2026-11-12"
    quote = fx.convert_cached("1", "USD", "CNY")
    assert quote["rate"] == "7.2" and "缓存" in quote["message"]
    assert "private" not in json.dumps(result)


def test_missing_cache_failure_backs_off_and_explicit_force_can_retry(library, monkeypatch):
    calls = []
    def fail():
        calls.append(True)
        raise TimeoutError
    monkeypatch.setattr(fx, "_fetch_daily", fail)
    assert not fx.refresh_rates()["available"]
    assert not fx.refresh_rates()["available"] and len(calls) == 1
    monkeypatch.setattr(fx, "_clock", lambda: NOW + timedelta(minutes=4))
    fx.refresh_rates()
    assert len(calls) == 1
    monkeypatch.setattr(fx, "_clock", lambda: NOW + timedelta(minutes=5))
    fx.refresh_rates()
    assert len(calls) == 2
    fx.refresh_rates(force=True)
    assert len(calls) == 3


def test_old_cached_reference_is_visible_but_never_used_for_conversion(library, monkeypatch):
    seed()
    monkeypatch.setattr(fx, "_clock", lambda: NOW + timedelta(days=7))
    quote = fx.convert_cached("10.54", "USD", "CNY")
    assert quote["status"] == "stale" and not quote["available"]
    assert quote["converted_amount"] is None and quote["as_of"] == "2026-11-12"


@pytest.mark.parametrize("mutate", [
    lambda raw: raw.update(base="USD"), lambda raw: raw.update(version=999),
    lambda raw: raw.update(as_of="2026-11-14"), lambda raw: raw["rates"].update(CNY="NaN"),
    lambda raw: raw["rates"].update(EUR="2"), lambda raw: raw.update(fetched_at="invalid"),
    lambda raw: raw.update(fetched_at="2027-01-01T00:00:00+00:00"),
])
def test_corrupt_cache_fails_closed_without_breaking_price_reads(library, mutate):
    seed()
    raw = fx._read_meta(fx.CACHE_KEY)
    mutate(raw)
    fx._write_meta(fx.CACHE_KEY, raw)
    result = fx.convert_cached("1", "USD", "CNY")
    assert result["available"] is False and result["rate"] is None


def test_cache_does_not_leak_across_temporary_or_company_libraries(library, tmp_path):
    seed()
    assert fx.convert_cached("1", "USD", "CNY")["available"]
    backend.init_storage(tmp_path / "different-library")
    assert not fx.convert_cached("1", "USD", "CNY")["available"]


def test_fixed_https_fetch_is_bounded_and_never_sends_product_data(library, monkeypatch):
    real_client = fx.httpx.Client
    seen = []
    def handler(request):
        seen.append(request)
        return fx.httpx.Response(200, content=feed(), headers={"Content-Type": "text/xml"})
    monkeypatch.setattr(fx.httpx, "Client", lambda **options: real_client(transport=fx.httpx.MockTransport(handler), **options))
    # Restore the real fixed request function hidden by the library fixture.
    monkeypatch.setattr(fx, "_fetch_daily", REAL_FETCH)
    assert fx.refresh_rates()["available"]
    assert len(seen) == 1 and str(seen[0].url) == fx.ECB_DAILY_URL
    assert not seen[0].url.query and not seen[0].content
    assert "authorization" not in seen[0].headers and "cookie" not in seen[0].headers


@pytest.mark.parametrize("status,headers,body", [
    (302, {"Location": "https://untrusted.example/"}, b""),
    (200, {"Content-Length": str(fx.MAX_RESPONSE_BYTES + 1)}, b"x"),
    (200, {}, b"x" * (fx.MAX_RESPONSE_BYTES + 1)),
])
def test_fetch_does_not_follow_redirects_or_accept_oversized_content(library, monkeypatch, status, headers, body):
    real_client = fx.httpx.Client
    seen = []
    def handler(request):
        seen.append(request)
        return fx.httpx.Response(status, headers=headers, content=body)
    monkeypatch.setattr(fx.httpx, "Client", lambda **options: real_client(transport=fx.httpx.MockTransport(handler), **options))
    monkeypatch.setattr(fx, "_fetch_daily", REAL_FETCH)
    assert not fx.refresh_rates()["available"]
    assert len(seen) == 1


REAL_FETCH = fx._fetch_daily
