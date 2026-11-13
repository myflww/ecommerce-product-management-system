"""ECB reference-rate cache for price comparisons, never a settlement quote.

Only refresh_rates performs network I/O. convert_cached and get_snapshot are
read-only so product/price transactions can call them safely. A refresh sends
one fixed public request, with no product, price, account, or storefront data.
"""
from __future__ import annotations

import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

import httpx

import backend

ECB_DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
SOURCE_URL = "https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html"
SOURCE_NAME = "ECB参考汇率"
CACHE_KEY = "exchange_rates:ecb"
ATTEMPT_KEY = "exchange_rates:ecb:last_attempt"
CACHE_TTL = timedelta(hours=24)
FAILURE_BACKOFF = timedelta(minutes=5)
MAX_RATE_AGE_DAYS = 7
MAX_RESPONSE_BYTES = 64 * 1024
FETCH_BUDGET_SECONDS = 6
_refresh_lock = threading.Lock()
_CURRENCY = re.compile(r"[A-Z]{3}")
_RATE = re.compile(r"(?:0|[1-9]\d{0,11})(?:\.\d{1,12})?")


class RateDataError(ValueError):
    """Invalid or unusable reference-rate data, without private diagnostics."""


def _clock() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not 10 <= len(value) <= 64:
        raise RateDataError("汇率缓存时间无效。")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except ValueError:
        raise RateDataError("汇率缓存时间无效。") from None


def _rate_date(value: Any, today: date) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise RateDataError("参考汇率缺少有效发布日期。")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise RateDataError("参考汇率发布日期无效。") from None
    if parsed > today:
        raise RateDataError("参考汇率发布日期晚于当前日期，未使用。")
    return parsed


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _rate_decimal(value: Any) -> Decimal:
    if not isinstance(value, str) or not _RATE.fullmatch(value):
        raise RateDataError("参考汇率数值格式无效。")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise RateDataError("参考汇率数值格式无效。") from None
    if not number.is_finite() or not Decimal("0.000000000001") <= number <= Decimal("1000000000000"):
        raise RateDataError("参考汇率必须为有效正数。")
    return number


def _validated_rates(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict) or not 3 <= len(raw) <= 200:
        raise RateDataError("参考汇率币种列表无效。")
    rates = {}
    for currency, value in raw.items():
        if not isinstance(currency, str) or not _CURRENCY.fullmatch(currency):
            raise RateDataError("参考汇率币种格式无效。")
        rates[currency] = _decimal_text(_rate_decimal(value))
    if rates.get("EUR") != "1" or not {"USD", "CNY"}.issubset(rates):
        raise RateDataError("参考汇率缺少欧元基准或主要币种。")
    return rates


def parse_ecb(body: bytes, *, now: datetime | None = None) -> dict:
    """Parse the official EUR-base daily feed; reject DTDs and ambiguous data."""
    current = now or _clock()
    if not isinstance(body, bytes) or not body or len(body) > MAX_RESPONSE_BYTES:
        raise RateDataError("参考汇率响应为空或过大。")
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise RateDataError("参考汇率XML编码无效。") from None
    if "\x00" in text:
        raise RateDataError("参考汇率XML编码无效。")
    upper = text.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise RateDataError("参考汇率XML包含不支持的声明。")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise RateDataError("参考汇率XML无法解析。") from None
    namespace = "{http://www.ecb.int/vocabulary/2002-08-01/eurofxref}"
    days = root.findall(".//" + namespace + "Cube[@time]")
    if len(days) != 1:
        raise RateDataError("参考汇率发布日期不唯一。")
    published = _rate_date(days[0].get("time"), current.date())
    if (current.date() - published).days > MAX_RATE_AGE_DAYS:
        raise RateDataError("参考汇率已超过7天，未用于价格比较。")
    raw = {"EUR": "1"}
    for node in days[0]:
        if node.tag != namespace + "Cube" or set(node.attrib) != {"currency", "rate"}:
            raise RateDataError("参考汇率XML格式不符合预期。")
        currency = node.get("currency", "")
        if currency in raw:
            raise RateDataError("参考汇率含重复币种。")
        raw[currency] = node.get("rate", "")
    return {"base": "EUR", "as_of": published.isoformat(), "rates": _validated_rates(raw)}


def _read_meta(key: str) -> Any:
    try:
        with backend._connection() as conn:
            row = conn.execute("SELECT value FROM app_meta WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None
    except Exception:
        # A broken/missing FX cache must not fail a product save or price read.
        return None


def _write_meta(key: str, value: dict) -> None:
    with backend._connection(write=True) as conn:
        conn.execute("INSERT INTO app_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, json.dumps(value, ensure_ascii=False, separators=(",", ":"))))


def _empty(status: str = "unavailable", message: str = "尚无可用参考汇率。") -> dict:
    return {"available": False, "status": status, "as_of": None, "fetched_at": None,
            "rates": {}, "source_name": SOURCE_NAME, "source_url": SOURCE_URL, "message": message}


def get_snapshot() -> dict:
    """Read only this library's cache. Does not create tables, write, or fetch."""
    raw = _read_meta(CACHE_KEY)
    if raw is None:
        return _empty()
    try:
        if not isinstance(raw, dict) or raw.get("base") != "EUR" or raw.get("version") != 1:
            raise RateDataError("参考汇率缓存格式无效。")
        current = _clock()
        published = _rate_date(raw.get("as_of"), current.date())
        fetched = _timestamp(raw.get("fetched_at"))
        if fetched > current + timedelta(minutes=5):
            raise RateDataError("参考汇率缓存时间晚于当前时间，未使用。")
        rates = _validated_rates(raw.get("rates"))
        age = (current.date() - published).days
        available = age <= MAX_RATE_AGE_DAYS
        result = {"available": available, "status": "ok" if available else "stale",
                  "as_of": published.isoformat(), "fetched_at": _stamp(fetched), "rates": rates,
                  "source_name": SOURCE_NAME, "source_url": SOURCE_URL,
                  "message": "参考汇率，仅用于价格比较。" if available else "参考汇率已超过7天，请更新后再比较。"}
        attempt = _read_meta(ATTEMPT_KEY)
        if available and isinstance(attempt, dict) and attempt.get("failed") is True:
            try:
                if _timestamp(attempt.get("at")) >= fetched:
                    result["message"] = "汇率更新暂未成功，使用标明日期的缓存参考汇率。"
            except RateDataError:
                pass
        return result
    except (RateDataError, TypeError, ValueError):
        return _empty(message="参考汇率缓存无效，请更新后再比较。")


def _fetch_daily() -> bytes:
    """One fixed HTTPS GET: no redirects, credentials, query, or company data."""
    started = time.monotonic()
    with httpx.Client(timeout=httpx.Timeout(1.0, connect=2.0), follow_redirects=False, trust_env=False) as client:
        with client.stream("GET", ECB_DAILY_URL,
                           headers={"Accept": "application/xml,text/xml", "User-Agent": "Midoo-Reference-Rates/1.0"}) as response:
            response.raise_for_status()
            if response.status_code != 200:
                raise RateDataError("参考汇率服务未返回完整数据。")
            length = response.headers.get("content-length")
            if length is not None and (not length.isdigit() or int(length) > MAX_RESPONSE_BYTES):
                raise RateDataError("参考汇率响应过大或长度无效。")
            chunks = []
            total = 0
            for chunk in response.iter_bytes():
                if time.monotonic() - started > FETCH_BUDGET_SECONDS:
                    raise RateDataError("参考汇率更新超时。")
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise RateDataError("参考汇率响应过大。")
                chunks.append(chunk)
            return b"".join(chunks)


def refresh_rates(force: bool = False) -> dict:
    """Synchronously refresh if due; call through asyncio.to_thread in routes.

    Network work happens after read connections close and before a short cache
    write transaction. Failure keeps a usable cache and backs off for 5 minutes.
    No scheduler or background task is created by this module.
    """
    if backend.DB_PATH is None:
        return _empty(message="参考汇率缓存尚未初始化。")
    with _refresh_lock:
        current = _clock()
        snapshot = get_snapshot()
        if not force:
            if snapshot["available"] and current - _timestamp(snapshot["fetched_at"]) < CACHE_TTL:
                return snapshot
            attempt = _read_meta(ATTEMPT_KEY)
            if isinstance(attempt, dict) and attempt.get("failed") is True:
                try:
                    elapsed = current - _timestamp(attempt.get("at"))
                    if timedelta(0) <= elapsed < FAILURE_BACKOFF:
                        if not snapshot["available"]:
                            snapshot["message"] = "参考汇率暂不可用，稍后可重试。"
                        return snapshot
                except RateDataError:
                    pass
        try:
            parsed = parse_ecb(_fetch_daily(), now=_clock())
            completed = _clock()
            _write_meta(CACHE_KEY, {"version": 1, **parsed, "fetched_at": _stamp(completed)})
            _write_meta(ATTEMPT_KEY, {"at": _stamp(completed), "failed": False})
        except Exception:
            try:
                _write_meta(ATTEMPT_KEY, {"at": _stamp(_clock()), "failed": True})
            except Exception:
                pass
            snapshot = get_snapshot()
            if not snapshot["available"]:
                snapshot["message"] = "参考汇率暂时无法更新，未计算跨币种折扣。"
            return snapshot
        return get_snapshot()


def convert_cached(amount: Any, from_currency: str, to_currency: str) -> dict:
    """Return a Decimal-derived comparison quote, with no I/O beyond DB reads."""
    origin = from_currency.strip().upper() if isinstance(from_currency, str) else ""
    target = to_currency.strip().upper() if isinstance(to_currency, str) else ""
    result = {"available": False, "status": "unavailable", "rate": None, "converted_amount": None,
              "from_currency": origin, "to_currency": target, "as_of": None,
              "source_name": SOURCE_NAME, "source_url": SOURCE_URL, "message": "尚无可用参考汇率。"}
    if not _CURRENCY.fullmatch(origin) or not _CURRENCY.fullmatch(target):
        result.update(status="unsupported", message="币种格式无效，未进行换算。")
        return result
    try:
        if isinstance(amount, bool) or not isinstance(amount, (str, int, float, Decimal)):
            raise ValueError
        value = Decimal(str(amount))
        if not value.is_finite() or value < 0 or value > Decimal("999999999999999999") or (value and value.adjusted() < -20):
            raise ValueError
    except (ValueError, InvalidOperation):
        result["message"] = "价格格式无效，未进行换算。"
        return result
    if origin == target:
        result.update(available=True, status="ok", rate="1", converted_amount=_decimal_text(value),
                      source_name="同币种", source_url="", message="同币种无需换算。")
        return result
    snapshot = get_snapshot()
    result.update({key: snapshot[key] for key in ("status", "as_of", "source_name", "source_url", "message")})
    if not snapshot["available"]:
        return result
    if origin not in snapshot["rates"] or target not in snapshot["rates"]:
        result.update(status="unsupported", message="参考汇率不支持该币种，未进行换算。")
        return result
    with localcontext() as context:
        context.prec = 40
        rate = Decimal(snapshot["rates"][target]) / Decimal(snapshot["rates"][origin])
        converted = value * rate
    result.update(available=True, status="ok", rate=_decimal_text(rate), converted_amount=_decimal_text(converted))
    return result
