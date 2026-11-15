"""Authenticated local browser jobs and server-owned import previews."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, Response, UploadFile

import backend
import browser_price_engine
import browser_price_routes
import store_import_engine as engine
import store_import_storage as storage
import page_capture_import
import permissions
import page_capture_package

router = APIRouter(prefix='/api/store-import', dependencies=[Depends(backend.require_user)])


@router.get('/page-extension.zip')
def page_extension(request: Request):
    _permission(request)
    _owner(request)
    return page_capture_package.extension_response()


def _owner(request):
    # Reuse the host/session guard; LAN users can use ordinary product features,
    # but cannot start or control a browser window on somebody else's computer.
    return browser_price_routes._owner(request)


def _permission(request):
    user = backend._user(request)
    permissions.require(user, 'products.import')
    return user


@router.post('/page-file')
def page_file(request: Request, file: UploadFile = File(...)):
    user = _permission(request)
    return page_capture_import.upload_preview(_owner(request), user, file)


@router.get('/capabilities')
def capabilities(request: Request):
    _permission(request)
    runtime = browser_price_engine.available()
    # Installed browser/driver availability is not proof that Temu login or
    # complete SKU collection works. Keep those two signals explicit for UIs
    # and packaged checks instead of implying a successful platform adapter.
    note = '实验性采集：已知 Temu 登录可能回跳或失败，真实店铺的完整规格与报价尚未验收通过。'
    if not runtime.get('available') and runtime.get('note'):
        note += ' ' + runtime['note']
    return {**runtime, 'can_use': browser_price_routes._local(request),
            'platforms': ['Temu'], 'batch_limit': 20,
            'experimental': True, 'live_capture_verified': False,
            'limitations': ['temu_login_redirect_unresolved', 'live_variant_completeness_unverified'],
            'runtime_note': runtime.get('note', ''), 'note': note}


@router.post('/browser/start')
def start(request: Request, payload: dict = Body(...)):
    user = _permission(request)
    if set(payload) != {'url'} or not isinstance(payload.get('url'), str):
        raise HTTPException(400, '请填写 Temu 店铺前端链接。')
    return engine.start(_owner(request), user, payload['url'])


@router.get('/browser/{job_id}')
def status(job_id: str, request: Request):
    _permission(request)
    return engine.snapshot(_owner(request), job_id)


@router.post('/browser/{job_id}/discover')
def discover(job_id: str, request: Request):
    _permission(request)
    return engine.command(_owner(request), job_id, 'discover')


@router.post('/browser/{job_id}/collect')
def collect(job_id: str, request: Request, payload: dict = Body(...)):
    _permission(request)
    if set(payload) != {'ids'}:
        raise HTTPException(400, '请选择本次发现的商品。')
    return engine.command(_owner(request), job_id, 'collect', payload['ids'])


@router.post('/browser/{job_id}/close')
def close(job_id: str, request: Request):
    return engine.close(_owner(request), job_id)


@router.get('/previews/{draft_id}')
def preview(draft_id: str, request: Request):
    return storage.get_preview(_owner(request), draft_id, _permission(request))


@router.get('/previews/{draft_id}/images/{item_id}')
def image(draft_id: str, item_id: str, request: Request):
    result = storage.preview_image(_owner(request), draft_id, item_id, _permission(request))
    return Response(result['data'], media_type=result['mime'], headers={'Cache-Control': 'no-store'})


@router.post('/previews/{draft_id}/commit')
def commit(draft_id: str, request: Request, payload: dict = Body(...)):
    user = _permission(request)
    if set(payload) - {'selections', 'mark_listed'} or type(payload.get('mark_listed', False)) is not bool:
        raise HTTPException(400, '导入选择格式无效。')
    owner = _owner(request)
    result = storage.commit_preview(owner, draft_id, user, payload.get('selections'),
                                    mark_listed=payload.get('mark_listed', False))
    engine.committed(owner, draft_id, result)
    return result


@router.delete('/previews/{draft_id}')
def discard(draft_id: str, request: Request):
    owner = _owner(request)
    user = _permission(request)
    try:
        result = storage.close_preview(owner, draft_id, user)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        result = {'ok': True}
    engine.release_preview(owner, draft_id)
    return result
