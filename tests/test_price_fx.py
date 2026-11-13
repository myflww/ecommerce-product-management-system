"""Cross-currency price comparison uses isolated storage and synthetic FX only."""
from contextlib import contextmanager
from decimal import Decimal
import io
import json
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import backend
import exchange_rates
import price_storage as storage
import price_tracking


@pytest.fixture
def fx_prices(tmp_path, monkeypatch):
    backend.init_storage(tmp_path / "data")
    storage.init_storage()
    backend._login_attempts.clear()
    price_tracking._active.clear()
    price_tracking._batch_running = False
    monkeypatch.setattr(backend, "_extract_semantic", lambda _: None)
    state = SimpleNamespace(rate=None, as_of="2026-11-12", available=False,
                            refresh_calls=0, conversion_calls=[], on_refresh=None,
                            active_connections=0, active_writes=0)
    original_connection = backend._connection
    lock = threading.Lock()

    @contextmanager
    def tracked_connection(write=False):
        with original_connection(write=write) as conn:
            with lock:
                state.active_connections += 1
                state.active_writes += int(write)
            try:
                yield conn
            finally:
                with lock:
                    state.active_connections -= 1
                    state.active_writes -= int(write)

    def convert(amount, from_currency, to_currency):
        state.conversion_calls.append((amount, from_currency, to_currency))
        common = {"from_currency": from_currency, "to_currency": to_currency,
                  "as_of": state.as_of, "source_name": "Synthetic FX reference",
                  "source_url": "https://rates.example.invalid/reference"}
        if not state.available or state.rate is None:
            return {**common, "available": False, "status": "unavailable",
                    "rate": None, "converted_amount": None,
                    "message": "测试汇率不可用"}
        if (from_currency, to_currency) == ("USD", "CNY"):
            rate = state.rate
        elif (from_currency, to_currency) == ("CNY", "USD"):
            rate = Decimal(1) / state.rate
        else:
            return {**common, "available": False, "status": "unsupported",
                    "rate": None, "converted_amount": None,
                    "message": "测试不支持此币种"}
        return {**common, "available": True, "status": "ok", "rate": format(rate, "f"),
                "converted_amount": format(Decimal(amount) * rate, "f"), "message": ""}

    def refresh(*args, **kwargs):
        # Saving a quote must commit before a public reference-rate request.
        assert state.active_writes == 0
        assert state.active_connections == 0
        state.refresh_calls += 1
        if state.on_refresh is not None:
            state.on_refresh()
        return {"available": state.available}

    monkeypatch.setattr(backend, "_connection", tracked_connection)
    monkeypatch.setattr(exchange_rates, "convert_cached", convert)
    monkeypatch.setattr(exchange_rates, "refresh_rates", refresh)
    app = FastAPI()
    app.include_router(price_tracking.router)
    app.include_router(backend.router)
    with TestClient(app) as client:
        response = client.post("/api/setup", json={"name": "FX 测试管理员", "password": "test-only-password"})
        response.raise_for_status()
        user = response.json()["user"]
        image = io.BytesIO()
        Image.new("RGB", (64, 64), "green").save(image, "PNG")
        listings = [
            {"platform": "Temu", "store": "测试美国店", "product_number": "SKC-1",
             "product_code": "USD-FRONT-A", "price": "74.62", "currency": "CNY",
             "status": "listed", "link": "https://shop.example.com/product/1",
             "variant_hint": "红色 2件装", "region": "美国"},
            {"platform": "Temu", "store": "测试美国店", "product_number": "SKC-1",
             "product_code": "USD-FRONT-B", "price": "100", "currency": "CNY",
             "status": "pending", "link": "https://shop.example.com/product/1",
             "variant_hint": "蓝色 4件装", "region": "美国"},
            {"platform": "Amazon", "store": "测试美元店", "product_number": "SKC-2",
             "product_code": "CNY-FRONT", "price": "0.01", "currency": "USD",
             "status": "pending", "link": "https://shop.example.com/product/2"},
        ]
        response = client.post("/api/products", data={"metadata": json.dumps({
            "name": "跨币种价格测试样品", "listings": listings})},
            files={"images": ("test.png", image.getvalue(), "image/png")})
        response.raise_for_status()
        yield SimpleNamespace(client=client, user=user, product=response.json(), fx=state)


def view(setup):
    return storage.get_product_tracking(setup.product["id"], setup.user)["listings"]


def quote(setup, index=0, **values):
    row = view(setup)[index]
    payload = {"price": "10.54", "currency": "USD", "expected_context": row["context_token"], **values}
    return setup.client.post("/api/price-tracking/listings/" + row["id"] + "/manual-price", json=payload)


def raw_snapshot():
    with backend._connection() as conn:
        return {table: [dict(row) for row in conn.execute("SELECT * FROM " + table + " ORDER BY 1")]
                for table in ("product_listings", "price_tracking", "price_history")}


def test_same_currency_does_not_fetch_or_convert(fx_prices):
    setup = fx_prices
    response = quote(setup, price="37.31", currency="CNY")
    response.raise_for_status()
    row = response.json()
    assert row["ratio_percent"] == "50.0" and row["fx"] is None
    result = setup.client.get("/api/price-tracking/products/" + setup.product["id"])
    result.raise_for_status()
    assert result.json()["listings"][0]["ratio_percent"] == "50.0"
    assert setup.fx.refresh_calls == 0 and setup.fx.conversion_calls == []


def test_screenshot_values_use_synthetic_rate_without_capping_above_100(fx_prices):
    setup = fx_prices
    setup.fx.rate = Decimal("7.2")  # A test fixture, never a claimed market rate.
    setup.fx.available = True
    response = quote(setup)
    response.raise_for_status()
    row = response.json()
    assert row["original_price"] == "74.62" and row["currency"] == "CNY"
    assert row["current_price"] == "10.54" and row["current_currency"] == "USD"
    assert row["ratio_percent"] == "101.7" and row["status"] == "ok"
    assert row["fx"]["converted_amount"] == "75.888"
    assert row["fx"]["rate"] == "7.2" and row["fx"]["as_of"] == "2026-11-12"
    assert row["fx"]["source_name"] == "Synthetic FX reference"
    history = storage.get_history(row["id"], setup.user)["items"]
    assert len(history) == 1
    assert history[0]["price"] == "10.54" and history[0]["currency"] == "USD"
    assert history[0]["source"] == "manual_entry"
    assert view(setup)[1]["current_price"] == ""


def test_reverse_conversion_uses_full_precision_before_ratio_rounding(fx_prices):
    setup = fx_prices
    setup.fx.rate = Decimal("7.2")
    setup.fx.available = True
    response = quote(setup, index=2, price="0.01", currency="CNY")
    response.raise_for_status()
    row = response.json()
    assert row["ratio_percent"] == "13.9"
    assert Decimal(row["fx"]["converted_amount"]) < Decimal("0.01")
    assert Decimal(row["fx"]["converted_amount"]) > 0
    assert row["current_price"] == "0.01" and row["current_currency"] == "CNY"
    assert row["currency"] == "USD" and row["fx"]["from_currency"] == "CNY"


def test_reference_refresh_runs_after_commit_and_updates_only_the_derived_view(fx_prices):
    setup = fx_prices
    expected_revision = view(setup)[0]["revision"] + 1

    def after_save():
        stored = view(setup)[0]
        assert stored["current_price"] == "10.54" and stored["current_currency"] == "USD"
        assert stored["revision"] == expected_revision
        assert len(storage.get_history(stored["id"], setup.user)["items"]) == 1
        setup.fx.rate = Decimal("7.2")
        setup.fx.available = True

    setup.fx.on_refresh = after_save
    response = quote(setup)
    response.raise_for_status()
    assert response.json()["ratio_percent"] == "101.7"
    assert setup.fx.refresh_calls == 1


def test_fx_outage_does_not_roll_back_the_user_price(fx_prices):
    setup = fx_prices
    old = view(setup)[0]

    def fail_refresh():
        raise RuntimeError("synthetic FX outage")

    setup.fx.on_refresh = fail_refresh
    response = quote(setup)
    response.raise_for_status()
    row = response.json()
    assert row["current_price"] == "10.54" and row["current_currency"] == "USD"
    assert row["ratio_percent"] is None and not row["fx"]["available"]
    assert row["revision"] == old["revision"] + 1
    assert row["context_token"] != old["context_token"]
    assert len(storage.get_history(row["id"], setup.user)["items"]) == 1


def test_fx_refresh_cannot_change_raw_price_history_revision_or_context(fx_prices):
    setup = fx_prices
    quote(setup).raise_for_status()
    before = view(setup)[0]
    database_before = raw_snapshot()
    setup.fx.rate = Decimal("7.2")
    setup.fx.available = True
    setup.fx.as_of = "2026-11-13"
    response = setup.client.get("/api/price-tracking/products/" + setup.product["id"])
    response.raise_for_status()
    after = response.json()["listings"][0]
    assert after["ratio_percent"] == "101.7"
    assert after["context_token"] == before["context_token"]
    assert after["revision"] == before["revision"]
    assert after["fx"]["as_of"] == "2026-11-13"
    assert raw_snapshot() == database_before
    setup.fx.rate = Decimal("7.0")
    response = setup.client.get("/api/price-tracking/products/" + setup.product["id"])
    response.raise_for_status()
    assert response.json()["listings"][0]["ratio_percent"] == "98.9"
    assert raw_snapshot() == database_before


def test_rate_refresh_does_not_invalidate_an_existing_sku_token(fx_prices):
    setup = fx_prices
    quote(setup).raise_for_status()
    token = view(setup)[0]["context_token"]
    setup.fx.rate = Decimal("7.2")
    setup.fx.available = True
    setup.client.get("/api/price-tracking/products/" + setup.product["id"]).raise_for_status()
    response = quote(setup, expected_context=token, price="11")
    response.raise_for_status()
    assert response.json()["current_price"] == "11"
    assert response.json()["context_token"] != token


def test_old_token_and_another_sku_token_are_rejected_without_fx_request(fx_prices):
    setup = fx_prices
    old = view(setup)[0]["context_token"]
    quote(setup).raise_for_status()
    before = raw_snapshot()
    refresh_count = setup.fx.refresh_calls
    assert quote(setup, expected_context=old, price="11").status_code == 409
    other_token = view(setup)[1]["context_token"]
    assert quote(setup, expected_context=other_token, price="12").status_code == 409
    assert setup.fx.refresh_calls == refresh_count
    assert raw_snapshot() == before
    assert view(setup)[1]["current_price"] == ""


def test_unsupported_currency_still_saves_its_actual_value(fx_prices):
    setup = fx_prices
    setup.fx.rate = Decimal("7.2")
    setup.fx.available = True
    response = quote(setup, currency="CHF")
    response.raise_for_status()
    row = response.json()
    assert row["current_price"] == "10.54" and row["current_currency"] == "CHF"
    assert row["ratio_percent"] is None and row["fx"]["status"] == "unsupported"


def test_currency_conversion_does_not_require_a_storefront_link(fx_prices):
    setup = fx_prices
    listings = setup.client.get("/api/products/" + setup.product["id"]).json()["listings"]
    listings[0]["link"] = ""
    setup.client.patch("/api/products/" + setup.product["id"], json={"listings": listings}).raise_for_status()
    setup.fx.rate = Decimal("7.2")
    setup.fx.available = True
    response = quote(setup)
    response.raise_for_status()
    assert response.json()["source_url"] == ""
    assert response.json()["ratio_percent"] == "101.7"
