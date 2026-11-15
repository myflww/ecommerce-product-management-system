"""觅同 — a shared, local-first product image library."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import socket
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import backend
import ai_service
import product_management
import price_tracking
import browser_price_engine
import browser_price_routes
import semantic_engine
import knowledge_store
import knowledge_graph
import announcements
import product_merge
import store_import_storage
import store_import_engine
import store_import_routes

VERSION = "1.7.0"
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
USER_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else ROOT
DATA_DIR = Path(os.environ.get("MIDOO_DATA_DIR", str(USER_ROOT / "data"))).resolve()


@asynccontextmanager
async def lifespan(application: FastAPI):
    backend.init_storage(DATA_DIR)
    ai_service.init_config(DATA_DIR)
    price_tracking.init_storage()
    knowledge_store.init_storage()
    announcements.init_storage()
    product_merge.init_storage()
    store_import_storage.init_storage()
    store_import_engine.init_engine(DATA_DIR)
    browser_price_engine.init_engine(DATA_DIR)
    try:
        await asyncio.to_thread(semantic_engine.init_model, ROOT / "models")
    except RuntimeError:
        logging.getLogger("midoo").warning("Local visual model is unavailable; image search will show its limited mode.")
    backend.start_indexing()
    backend.register_backup_file(DATA_DIR / "ai-config.json", "ai-config.json")
    backend.register_backup_file(DATA_DIR / ".ai-vault-key", ".ai-vault-key")
    async def backup_loop():
        while True:
            try:
                await asyncio.to_thread(automatic_backup)
            except Exception:
                logging.getLogger("midoo").warning("Automatic backup failed; use the manual backup action to check storage.")
            await asyncio.sleep(3600)
    task = asyncio.create_task(backup_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await asyncio.to_thread(browser_price_engine.shutdown)
        await asyncio.to_thread(store_import_engine.shutdown)


def automatic_backup():
    folder = DATA_DIR / "backups"
    copies = sorted(folder.glob("auto-midoo-backup-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    if copies and time.time() - copies[0].stat().st_mtime < 86400:
        return
    snapshot = backend.create_backup()
    snapshot.replace(snapshot.with_name("auto-" + snapshot.name))
    copies = sorted(folder.glob("auto-midoo-backup-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in copies[7:]:
        old.unlink()


app = FastAPI(title="觅同 · 公司商品库", version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    # Cookies are same-site; reject cross-site writes before parsing any upload.
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse({"detail": "请在软件页面内操作，已拒绝跨站请求。"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
    if request.url.path.startswith(("/api/", "/media/")):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    # Pydantic's default error contains input values, potentially including secrets.
    fields = ", ".join(str(e["loc"][-1]) for e in exc.errors()[:4])
    return JSONResponse({"detail": "提交的信息格式不正确，请检查必填项。", "fields": fields}, status_code=422)


app.include_router(product_management.router)
app.include_router(product_merge.router)
app.include_router(store_import_routes.router)
app.include_router(announcements.router)
app.include_router(knowledge_graph.router)
app.include_router(knowledge_store.router)
app.include_router(browser_price_routes.router)
app.include_router(price_tracking.router)
app.include_router(backend.router)
app.include_router(ai_service.router)
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


@app.get("/api/health", include_in_schema=False)
def health():
    return {"application": "midoo", "version": VERSION}


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    icon = ROOT / "static" / "assets" / "logo.svg"
    if icon.exists():
        return FileResponse(icon, media_type="image/svg+xml")
    return JSONResponse({}, status_code=204)


def main():
    parser = argparse.ArgumentParser(description="觅同 · 公司商品库")
    parser.add_argument("--host", default=os.environ.get("MIDOO_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MIDOO_PORT", "8765")))
    args = parser.parse_args()
    print("\n  MIDOO | 觅同 · 公司商品库 " + VERSION)
    print(f"  本机打开：http://127.0.0.1:{args.port}")
    try:
        addresses = sorted({a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET) if not a[4][0].startswith("127.")})
        for address in addresses:
            print(f"  局域网访问：http://{address}:{args.port}")
    except OSError:
        pass
    print(f"  数据目录：{DATA_DIR}\n  首次打开请创建管理员。关闭此进程后共享服务停止。\n")
    uvicorn.run(app, host=args.host, port=args.port, access_log=False, log_level="warning", log_config=None, ws="none", proxy_headers=False)


if __name__ == "__main__":
    main()
