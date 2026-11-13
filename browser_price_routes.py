"""Host-only, session-owned browser quotes. A preview never changes a price."""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import threading
import time

from fastapi import APIRouter, Body, Depends, HTTPException, Request

import backend
import browser_price_engine as engine
import price_storage
import price_tracking

router = APIRouter(prefix='/api/price-tracking', dependencies=[Depends(backend.require_user)])
_confirm_lock = threading.RLock()
_consumed: dict[tuple[str, str], float] = {}


def _local(request: Request) -> bool:
    try:
        address = ipaddress.ip_address(request.client.host if request.client else '')
        return (getattr(address, 'ipv4_mapped', None) or address).is_loopback
    except ValueError:
        return False


def _owner(request: Request) -> str:
    if not _local(request):
        raise HTTPException(403, '浏览器辅助需在运行觅同的主机上操作；局域网同事仍可查看价格记录。')
    return hashlib.sha256(request.cookies.get(backend.COOKIE_NAME, '').encode()).hexdigest()


def _authorized_job(request: Request, job_id: str) -> tuple[str, dict]:
    owner = _owner(request)
    job = engine.snapshot(owner, job_id)
    price_storage.get_target(job['listing_id'], backend._user(request))
    return owner, job


@router.get('/browser/capabilities')
def capabilities(request: Request):
    status = engine.available()
    return {**status, 'local_only': True, 'can_use': _local(request),
            'note': status.get('note', '') if _local(request) else '请在运行觅同的主机浏览器中使用此功能。'}


@router.post('/listings/{listing_id}/browser/start')
def start(listing_id: str, request: Request):
    owner = _owner(request)
    user = backend._user(request)
    target = price_tracking.manual_target(listing_id, user)
    target['_profile_user'] = user['id']
    return engine.start(owner, target)


@router.post('/listings/{listing_id}/browser/profile/clear')
def clear_profile(listing_id: str, request: Request):
    _owner(request)
    user = backend._user(request)
    target = price_storage.get_target(listing_id, user)
    target['_profile_user'] = user['id']
    return engine.clear_profile(target)


@router.get('/browser/{job_id}')
def status(job_id: str, request: Request):
    return _authorized_job(request, job_id)[1]


@router.post('/browser/{job_id}/read')
def read(job_id: str, request: Request):
    owner, _ = _authorized_job(request, job_id)
    return engine.read(owner, job_id)


@router.post('/browser/{job_id}/close')
def close(job_id: str, request: Request):
    # Closing one's own window remains possible after a product was removed.
    return engine.close(_owner(request), job_id)


@router.post('/browser/{job_id}/confirm')
def confirm(job_id: str, request: Request, payload: dict = Body(...)):
    owner = _owner(request)
    if (set(payload) - {'candidate_id', 'confirmed_variant', 'confirmed_price_basis'} or
            payload.get('confirmed_variant') is not True or payload.get('confirmed_price_basis') is not True or
            not isinstance(payload.get('candidate_id'), str) or not 1 <= len(payload['candidate_id']) <= 160):
        raise HTTPException(400, '请选择页面报价，并确认已经核对规格、地区和价格比较口径。')
    with _confirm_lock:
        cutoff = time.monotonic() - 1800
        for key in list(_consumed):
            if _consumed[key] < cutoff:
                del _consumed[key]
        key = (owner, job_id)
        if key in _consumed:
            raise HTTPException(409, '这次浏览器报价已经处理，请重新读取页面后再记录。')
        target, result = engine.candidate(owner, job_id, payload['candidate_id'])
        # Recheck current product rights, not only the rights at browser launch.
        price_storage.get_target(target['listing_id'], backend._user(request))
        applied = price_storage.record_attempt(target['listing_id'], result=result, expected_target=target)
        _consumed[key] = time.monotonic()
        close_warning = ''
        try:
            engine.close(owner, job_id)
        except HTTPException:
            close_warning = ' 关闭辅助窗口未完成，请手动关闭该窗口。'
        if not applied:
            raise HTTPException(409, '链接、规格或平台设置已变化，本次旧报价没有写入，请重新打开辅助采集。')
        same_currency = result['currency'] == target['currency']
        audit_warning = ''
        try:
            with backend._connection(write=True) as conn:
                backend._audit(conn, backend._user(request), 'price_confirm',
                               '确认浏览器页面报价：' + result['currency'] + ' ' + result['price'])
        except Exception:
            # The quote and price history are already committed. Never tell a
            # caller they failed and encourage a duplicate retry after commit.
            logging.getLogger('midoo').warning('Browser quote saved; activity logging unavailable.')
            audit_warning = ' 价格已保存，但本次操作动态暂未写入。'
        return {'ok': True, 'updated': True,
                'message': ('已记录你确认的页面报价。' if same_currency else '已记录页面实际币种与售价；参考汇率可用时换算比较折扣率。') + close_warning + audit_warning}
