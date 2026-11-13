"""Native Windows shell for the existing local Midoo service.

The shell has no application JS API. An already running service is reused and
never stopped by this process; a service created here is shut down gracefully.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

APP_TITLE = "电商产品管理系统 · Midoo"
CONFIG_NAME = "desktop-config.json"
LOG = logging.getLogger("midoo.desktop")


class DesktopUnavailable(RuntimeError):
    """Native rendering is unavailable; the caller can show the old launcher."""


class ServiceError(RuntimeError):
    """A local service could not be safely started or reused."""


def configure_data_dir(application: Any, arguments: list[str]) -> Path:
    """Honor environment > explicit --data-dir > local installer config.

    Only the new argument is removed, leaving the existing --serve CLI intact.
    Relative config paths are based on the executable directory, not the cwd.
    No database or directory is created by this function.
    """
    explicit = None
    remaining = []
    index = 0
    while index < len(arguments):
        value = arguments[index]
        if value == "--data-dir":
            if explicit is not None or index + 1 >= len(arguments) or arguments[index + 1].startswith("--"):
                raise ServiceError("--data-dir 需要且只能指定一个资料目录。")
            explicit = arguments[index + 1]
            index += 2
            continue
        if value.startswith("--data-dir="):
            if explicit is not None or not value.partition("=")[2]:
                raise ServiceError("--data-dir 需要且只能指定一个资料目录。")
            explicit = value.partition("=")[2]
            index += 1
            continue
        remaining.append(value)
        index += 1
    arguments[:] = remaining
    base = Path(application.USER_ROOT).resolve()
    environment = os.environ.get("MIDOO_DATA_DIR", "").strip()
    if environment:
        chosen = Path(environment).expanduser()
    elif explicit is not None:
        if not explicit.strip():
            raise ServiceError("资料目录不能为空。")
        chosen = Path(explicit).expanduser()
    else:
        config_path = base / CONFIG_NAME
        if not config_path.exists():
            return Path(application.DATA_DIR)
        try:
            if config_path.stat().st_size > 16384:
                raise ValueError
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
            if not isinstance(config, dict):
                raise ValueError
            value = config.get("data_dir")
            if value is None:
                return Path(application.DATA_DIR)
            if not isinstance(value, str) or not value.strip() or "\x00" in value:
                raise ValueError
            chosen = Path(value).expanduser()
        except (OSError, ValueError, TypeError):
            raise ServiceError("桌面版资料目录配置无法读取，请检查 desktop-config.json。") from None
        if not chosen.is_absolute():
            chosen = base / chosen
    try:
        resolved = chosen.resolve()
    except (OSError, ValueError, RuntimeError):
        raise ServiceError("资料目录无效，请检查 --data-dir 或桌面版配置。") from None
    if resolved.exists() and not resolved.is_dir():
        raise ServiceError("资料位置须是文件夹，不能是文件。")
    application.DATA_DIR = resolved
    return resolved


def configured_port() -> int:
    try:
        port = int(os.environ.get("MIDOO_PORT", "8765"))
    except ValueError:
        raise ServiceError("MIDOO_PORT 需要是 1–65535 的端口号。") from None
    if not 1 <= port <= 65535:
        raise ServiceError("MIDOO_PORT 需要是 1–65535 的端口号。")
    return port


def is_midoo_service(local_url: str, expected_version: str | None = None) -> bool:
    """Read only the small loopback health response, without proxy settings."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, _request, _file, _code, _message, _headers, _new_url):
            return None

    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(local_url + "/api/health", timeout=2) as response:
            if response.status != 200:
                return False
            raw = response.read(65537)
            if len(raw) > 65536:
                return False
            payload = json.loads(raw)
        recognized = isinstance(payload, dict) and payload.get("application") == "midoo" and isinstance(payload.get("version"), str)
        if recognized and expected_version and payload['version'] != expected_version:
            raise ServiceError(f"本机仍在运行 {payload['version']} 版服务。请先在旧程序中正常关闭共享服务，再打开 {expected_version} 版桌面程序；商品资料会保留。")
        return recognized
    except (OSError, ValueError, TypeError):
        return False


def port_available(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False


@dataclass
class ServiceLease:
    local_url: str
    owned: bool = False
    server: Any = None
    thread: Any = None

    def stop(self) -> None:
        if not self.owned or self.server is None:
            return
        self.server.should_exit = True
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=15)


def start_or_connect(application: Any, *, port: int, timeout: float = 60) -> ServiceLease:
    local_url = f"http://127.0.0.1:{port}"
    if not port_available(port):
        if is_midoo_service(local_url, expected_version=getattr(application, 'VERSION', None)):
            return ServiceLease(local_url)
        raise ServiceError(f"端口 {port} 已被其他程序占用；没有连接或关闭该程序。请更改 MIDOO_PORT 后重试。")
    import uvicorn

    config = uvicorn.Config(application.app, host="0.0.0.0", port=port, access_log=False,
                            log_level="warning", log_config=None, ws="none", proxy_headers=False,
                            timeout_graceful_shutdown=10)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="midoo-local-service", daemon=True)
    lease = ServiceLease(local_url, True, server, thread)
    try:
        thread.start()
        deadline = time.monotonic() + timeout
        while thread.is_alive() and time.monotonic() < deadline:
            if server.started:
                return lease
            time.sleep(0.1)
    except Exception:
        lease.stop()
        raise ServiceError("本地服务启动未完成，请检查资料目录权限或使用服务管理窗口。") from None
    lease.stop()
    raise ServiceError("本地服务未能就绪，请检查端口、资料目录权限或使用服务管理窗口。")


def _external_http_url(url: str) -> bool:
    try:
        value = urlsplit(url)
        return bool(value.scheme in {"http", "https"} and value.hostname and
                    value.username is None and value.password is None and
                    not any(ord(char) < 32 for char in url))
    except ValueError:
        return False


def same_service_origin(url: str, local_url: str) -> bool:
    try:
        value, allowed = urlsplit(url), urlsplit(local_url)
        return (value.scheme, value.hostname, value.port) == (allowed.scheme, allowed.hostname, allowed.port) and value.username is None and value.password is None
    except ValueError:
        return False


def install_navigation_guards(window: Any, local_url: str, *, opener=webbrowser.open) -> tuple:
    """Use the documented native WebView2 control before the window is shown.

    Its top-level NavigationStarting event can cancel a navigation before any
    remote document is loaded. New-window requests stay outside the app shell.
    """
    control = window.native.webview

    def on_navigation(_sender, args):
        url = str(args.Uri)
        if same_service_origin(url, local_url) or url == "about:blank":
            return
        args.Cancel = True
        if bool(getattr(args, "IsUserInitiated", False)) and _external_http_url(url):
            opener(url)

    def on_new_window(_sender, args):
        args.Handled = True
        url = str(args.Uri)
        if bool(getattr(args, "IsUserInitiated", False)) and _external_http_url(url):
            opener(url)

    def on_frame_navigation(_sender, args):
        if not same_service_origin(str(args.Uri), local_url) and str(args.Uri) != "about:blank":
            args.Cancel = True

    def on_ready(sender, args):
        if bool(args.IsSuccess):
            sender.CoreWebView2.NewWindowRequested += on_new_window
            sender.CoreWebView2.FrameNavigationStarting += on_frame_navigation

    control.NavigationStarting += on_navigation
    if control.CoreWebView2 is not None:
        control.CoreWebView2.NewWindowRequested += on_new_window
        control.CoreWebView2.FrameNavigationStarting += on_frame_navigation
    else:
        control.CoreWebView2InitializationCompleted += on_ready
    handlers = (on_navigation, on_new_window, on_frame_navigation, on_ready)
    window._midoo_navigation_handlers = handlers
    return handlers


def _webview2_versions() -> list[str]:
    versions = set()
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if not base:
            continue
        directory = Path(base) / "Microsoft" / "EdgeWebView" / "Application"
        try:
            for entry in directory.iterdir():
                if entry.is_dir() and entry.name[0:1].isdigit() and (entry / "msedgewebview2.exe").is_file():
                    versions.add(entry.name)
        except OSError:
            continue
    return sorted(versions)


def desktop_probe() -> dict:
    """No GUI, database, network, cookies or application settings are accessed."""
    try:
        installed = importlib.util.find_spec("webview") is not None
    except (ImportError, ValueError):
        installed = False
    try:
        version = importlib.metadata.version("pywebview") if installed else ""
    except importlib.metadata.PackageNotFoundError:
        version = "bundled" if installed else ""
    runtime_versions = _webview2_versions() if sys.platform == "win32" else []
    return {"platform":sys.platform, "native_engine":"edgechromium", "pywebview_installed":installed,
            "pywebview_version":version, "webview2_found":bool(runtime_versions),
            "webview2_versions":runtime_versions, "custom_js_api":False,
            "requires_dotnet":".NET Framework 4.6.2 or newer", "windowed":sys.stdout is None and sys.stderr is None}


def run_desktop(application: Any, *, webview_module=None) -> None:
    if sys.platform != "win32":
        raise DesktopUnavailable("桌面窗口当前面向 Windows；将使用兼容服务管理器。")
    try:
        # This explicit import also lets packagers collect the chosen renderer.
        import webview
        native = webview_module or webview
    except Exception:
        raise DesktopUnavailable("未能载入桌面窗口组件，将使用兼容服务管理器。") from None
    if not _webview2_versions():
        raise DesktopUnavailable("未找到 Microsoft Edge WebView2 Runtime，将使用兼容服务管理器。")
    lease = start_or_connect(application, port=configured_port())
    window = None
    guard_errors = []
    try:
        native.settings.update(ALLOW_FILE_URLS=False, ALLOW_DOWNLOADS=True,
                               OPEN_EXTERNAL_LINKS_IN_BROWSER=False, IGNORE_SSL_ERRORS=False,
                               REMOTE_DEBUGGING_PORT=None)
        profile = Path(application.DATA_DIR) / "desktop-webview"
        profile.mkdir(parents=True, exist_ok=True)
        window = native.create_window(APP_TITLE, url=lease.local_url, js_api=None,
                                      width=1360, height=900, min_size=(900, 640),
                                      text_select=True, zoomable=True, background_color="#f5f6fa")
        if window is None:
            raise RuntimeError("No native window")

        def before_show():
            try:
                install_navigation_guards(window, lease.local_url)
            except Exception:
                guard_errors.append(True)
                # Closing our just-created window is not a stop of another
                # process. Do not display an unguarded native web shell.
                threading.Thread(target=window.destroy, daemon=True).start()

        def closing():
            if guard_errors:
                return True
            if lease.owned and lease.server is not None and lease.server.started:
                return window.create_confirmation_dialog(
                    "关闭电商产品管理系统？",
                    "关闭本窗口会停止此程序启动的本地共享服务，同事将暂时无法访问商品库。已保存资料会保留。确定关闭吗？")
            return True

        window.events.before_show += before_show
        window.events.closing += closing
        window.events.closed += lease.stop
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Midoo.EcommerceProductManager")
        except (AttributeError, OSError):
            pass
        native.start(gui="edgechromium", debug=False, private_mode=False,
                     storage_path=str(profile), http_server=False,
                     icon=str(Path(application.ROOT) / "static" / "assets" / "midoo.ico"))
        if guard_errors:
            raise RuntimeError("Navigation guard unavailable")
    except ServiceError:
        raise
    except Exception:
        if window is not None:
            try:
                shown = getattr(window.events, "shown", None)
                # pywebview.destroy waits for its shown event; do not add a
                # 20-second wait when renderer loading failed before a window.
                if shown is None or shown.is_set():
                    window.destroy()
            except Exception:
                pass
        raise DesktopUnavailable("独立桌面窗口启动未完成，将使用兼容服务管理器。请确认 WebView2 Runtime 与 .NET Framework 可用。") from None
    finally:
        lease.stop()
