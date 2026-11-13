"""Windows desktop entry point, with a compatible local-service manager."""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import tkinter as tk
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import messagebox

import uvicorn

import app as application
import desktop_shell


def service_manager_main(fallback_message: str = ""):
    try:
        port = desktop_shell.configured_port()
    except desktop_shell.ServiceError as error:
        show_startup_error(str(error))
        return
    local_url = f"http://127.0.0.1:{port}"
    root = tk.Tk()
    root.title("电商产品管理系统 · 本地共享服务")
    root.geometry("540x460" if fallback_message else "540x392")
    root.resizable(False, False)
    root.configure(bg="#f5f6fa")
    try:
        root.iconbitmap(str(application.ROOT / "static" / "assets" / "midoo.ico"))
    except tk.TclError:
        pass
    try:
        root.tk.call("tk", "scaling", 1.3)
    except tk.TclError:
        pass
    box = tk.Frame(root, bg="#ffffff", padx=30, pady=26)
    box.pack(fill="both", expand=True, padx=18, pady=18)
    tk.Label(box, text="MIDOO  /  觅同", font=("Microsoft YaHei UI", 20, "bold"), bg="white", fg="#18233d").pack(anchor="w")
    tk.Label(box, text="公司商品库 · 局域网共享", font=("Microsoft YaHei UI", 11), bg="white", fg="#778197").pack(anchor="w", pady=(4, 20))
    if fallback_message:
        tk.Label(box, text=fallback_message, font=("Microsoft YaHei UI", 9), bg="#fff8e8", fg="#987540", wraplength=445, justify="left").pack(fill="x", pady=(0, 14))
    status = tk.StringVar(value="正在启动本地服务…")
    tk.Label(box, textvariable=status, font=("Microsoft YaHei UI", 11, "bold"), bg="white", fg="#5267eb").pack(anchor="w")
    addresses = []
    try:
        addresses = sorted({a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET) if not a[4][0].startswith("127.")})
    except OSError:
        pass
    shown_url = tk.StringVar(value=f"http://{addresses[0]}:{port}" if addresses else local_url)
    tk.Label(box, text="同事在浏览器打开下方地址", bg="white", fg="#778197", font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(18, 5))
    tk.Entry(box, textvariable=shown_url, state="readonly", relief="flat", readonlybackground="#f0f2f8", fg="#26334e", font=("Consolas", 12)).pack(fill="x", ipady=7)
    actions = tk.Frame(box, bg="white")
    actions.pack(fill="x", pady=(19, 12))
    tk.Button(actions, text="打开商品工作台", command=lambda: webbrowser.open(local_url), bg="#5267eb", fg="white", activebackground="#4054d1", activeforeground="white", relief="flat", padx=18, pady=8, cursor="hand2", font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
    def copy_address():
        root.clipboard_clear()
        root.clipboard_append(shown_url.get())
        status.set("局域网地址已复制")
    tk.Button(actions, text="复制共享地址", command=copy_address, bg="#eef0f6", fg="#39465e", relief="flat", padx=14, pady=8, cursor="hand2", font=("Microsoft YaHei UI", 10)).pack(side="left", padx=(10, 0))
    tk.Label(box, text="保持此窗口运行以供同事访问。\n第一次使用，请在工作台创建管理员账户。", bg="white", fg="#8992a5", justify="left", font=("Microsoft YaHei UI", 9)).pack(anchor="w")

    server = None
    thread = None
    try:
        with socket.socket() as probe:
            probe.bind(("0.0.0.0", port))
    except OSError:
        try:
            existing = desktop_shell.is_midoo_service(local_url, expected_version=application.VERSION)
        except desktop_shell.ServiceError as error:
            status.set("请先正常关闭旧版共享服务")
            messagebox.showerror("需要切换新版服务", str(error), parent=root)
            existing = None
        except Exception:
            existing = False
        if existing:
            status.set("本机已有觅同服务正在运行")
            root.after(400, lambda: webbrowser.open(local_url))
        elif existing is False:
            status.set(f"端口 {port} 已被占用，服务未启动")
            messagebox.showerror("启动未完成", f"端口 {port} 正被其他程序占用。请关闭占用程序，或通过 MIDOO_PORT 设置其他端口。", parent=root)
    else:
        config = uvicorn.Config(application.app, host="0.0.0.0", port=port, access_log=False, log_level="warning", log_config=None, ws="none", proxy_headers=False, timeout_graceful_shutdown=10)
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        attempts = 0
        def check_ready():
            nonlocal attempts
            if server.started:
                status.set("● 服务已就绪，商品资料保存在本机")
                webbrowser.open(local_url)
            elif attempts < 80 and thread.is_alive():
                attempts += 1
                root.after(250, check_ready)
            else:
                status.set("启动失败，请查看使用说明或检查目录权限")
        root.after(300, check_ready)

    def close():
        if server and server.started:
            if not messagebox.askokcancel("关闭共享服务", "关闭后，同事将无法访问商品库。已保存的资料会保留。确定关闭吗？", parent=root):
                return
            server.should_exit = True
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", close)
    root.mainloop()
    if server is not None:
        server.should_exit = True
        if thread is not None:
            thread.join(timeout=15)


def show_startup_error(message: str):
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("电商产品管理系统 · 启动未完成", message, parent=root)
    root.destroy()


def main():
    try:
        desktop_shell.run_desktop(application)
    except desktop_shell.DesktopUnavailable as error:
        service_manager_main(str(error))
    except desktop_shell.ServiceError as error:
        show_startup_error(str(error))


if __name__ == "__main__":
    try:
        desktop_shell.configure_data_dir(application, sys.argv)
    except desktop_shell.ServiceError as error:
        show_startup_error(str(error))
        sys.exit(1)
    if "--desktop-probe" in sys.argv:
        argument_index = sys.argv.index("--desktop-probe")
        report = desktop_shell.desktop_probe()
        if len(sys.argv) > argument_index + 1 and not sys.argv[argument_index + 1].startswith("--"):
            Path(sys.argv[argument_index + 1]).write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        elif sys.stdout is not None:
            print(json.dumps(report, ensure_ascii=False))
        sys.exit(0 if report["pywebview_installed"] and report["webview2_found"] else 1)
    elif "--check-browser-runtime" in sys.argv:
        # Developer packaging probe: start only the driver, with no browser,
        # network, application database, API key or interactive UI access.
        output = Path(sys.argv[sys.argv.index('--check-browser-runtime') + 1])
        report = {'ok': False, 'windowed': sys.stdout is None and sys.stderr is None}
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as runtime:
                report.update(ok=runtime.chromium.name == 'chromium', engine=runtime.chromium.name)
        except Exception as exc:
            report['error'] = type(exc).__name__
        output.write_text(json.dumps(report), encoding='utf-8')
        sys.exit(0 if report['ok'] else 1)
    elif "--serve" in sys.argv:
        sys.argv.remove("--serve")
        application.main()
    elif "--service-manager" in sys.argv:
        sys.argv.remove("--service-manager")
        service_manager_main()
    else:
        main()
