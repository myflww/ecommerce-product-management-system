"""Native shell ownership and navigation tests; no native UI or real service."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import desktop_shell as shell


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, callback):
        self.handlers.append(callback)
        return self


class FakeWindow:
    def __init__(self):
        core = SimpleNamespace(NewWindowRequested=Event(), FrameNavigationStarting=Event())
        control = SimpleNamespace(NavigationStarting=Event(), CoreWebView2=core,
                                  CoreWebView2InitializationCompleted=Event())
        self.native = SimpleNamespace(webview=control)
        self.events = SimpleNamespace(before_show=Event(), closing=Event(), closed=Event())
        self.confirm = True
        self.destroyed = False

    def create_confirmation_dialog(self, title, message):
        self.confirm_title, self.confirm_message = title, message
        return self.confirm

    def destroy(self):
        self.destroyed = True


class FakeNative:
    def __init__(self, fail=False):
        self.settings = {}
        self.window = FakeWindow()
        self.fail = fail
        self.confirmations = []

    def create_window(self, title, **kwargs):
        self.title, self.create_options = title, kwargs
        return self.window

    def start(self, **kwargs):
        self.start_options = kwargs
        for handler in self.window.events.before_show.handlers:
            handler()
        if self.fail:
            raise RuntimeError("test native renderer error")
        self.window.confirm = False
        self.confirmations.extend(handler() for handler in self.window.events.closing.handlers)
        self.window.confirm = True
        self.confirmations.extend(handler() for handler in self.window.events.closing.handlers)
        for handler in self.window.events.closed.handlers:
            handler()


@pytest.fixture
def application(tmp_path, monkeypatch):
    monkeypatch.delenv('MIDOO_DATA_DIR', raising=False)
    monkeypatch.delenv('MIDOO_PORT', raising=False)
    return SimpleNamespace(USER_ROOT=tmp_path, ROOT=tmp_path, DATA_DIR=tmp_path/'data', VERSION='1.6.0', app=object())


def test_no_config_preserves_default_and_does_not_create_data(application):
    args=['desktop.py','--serve','--host','127.0.0.1']
    assert shell.configure_data_dir(application,args)==application.DATA_DIR
    assert args==['desktop.py','--serve','--host','127.0.0.1']
    assert not application.DATA_DIR.exists()


def test_installer_bom_config_and_relative_directory(application):
    (application.USER_ROOT/'desktop-config.json').write_text(json.dumps({'data_dir':'stored-data'}),encoding='utf-8-sig')
    assert shell.configure_data_dir(application,['desktop.py'])==application.USER_ROOT/'stored-data'
    assert not application.DATA_DIR.exists()


def test_environment_overrides_explicit_directory_and_installer_config(application,monkeypatch):
    chosen=application.USER_ROOT/'env-data'
    monkeypatch.setenv('MIDOO_DATA_DIR',str(chosen))
    (application.USER_ROOT/'desktop-config.json').write_text('{not valid json',encoding='utf-8')
    args=['desktop.py','--data-dir',str(application.USER_ROOT/'ignored'),'--serve','--port','8779']
    assert shell.configure_data_dir(application,args)==chosen
    assert args==['desktop.py','--serve','--port','8779']


def test_explicit_directory_overrides_installer_config(application):
    chosen=application.USER_ROOT/'explicit-data'
    (application.USER_ROOT/'desktop-config.json').write_text('{bad',encoding='utf-8')
    args=['desktop.py','--data-dir='+str(chosen),'--service-manager']
    assert shell.configure_data_dir(application,args)==chosen
    assert args==['desktop.py','--service-manager']


@pytest.mark.parametrize('args',[['desktop.py','--data-dir'],['desktop.py','--data-dir','--serve'],
                                 ['desktop.py','--data-dir='],['desktop.py','--data-dir=a','--data-dir=b']])
def test_bad_data_flag_is_rejected_without_starting(application,args):
    with pytest.raises(shell.ServiceError):
        shell.configure_data_dir(application,args)
    assert not application.DATA_DIR.exists()


@pytest.mark.parametrize('config',['{bad','[]','{"data_dir":true}','{"data_dir":""}'])
def test_bad_local_config_is_rejected(application,config):
    (application.USER_ROOT/'desktop-config.json').write_text(config,encoding='utf-8')
    with pytest.raises(shell.ServiceError):
        shell.configure_data_dir(application,['desktop.py'])


def test_file_cannot_be_selected_as_data_directory(application):
    path=application.USER_ROOT/'not-a-directory'
    path.write_text('example',encoding='utf-8')
    with pytest.raises(shell.ServiceError):
        shell.configure_data_dir(application,['desktop.py','--data-dir',str(path)])


@pytest.mark.parametrize('value',['0','65536','not-a-number','-1'])
def test_bad_port_is_rejected(monkeypatch,value):
    monkeypatch.setenv('MIDOO_PORT',value)
    with pytest.raises(shell.ServiceError):
        shell.configured_port()


def test_existing_service_is_reused_and_never_owned(application,monkeypatch):
    monkeypatch.setattr(shell,'port_available',lambda port:False)
    monkeypatch.setattr(shell,'is_midoo_service',lambda url,**kwargs:True)
    lease=shell.start_or_connect(application,port=8765)
    assert lease.local_url=='http://127.0.0.1:8765'
    assert not lease.owned and lease.server is None
    lease.stop()


def test_foreign_port_is_not_connected_or_stopped(application,monkeypatch):
    monkeypatch.setattr(shell,'port_available',lambda port:False)
    monkeypatch.setattr(shell,'is_midoo_service',lambda url,**kwargs:False)
    with pytest.raises(shell.ServiceError,match='其他程序'):
        shell.start_or_connect(application,port=8765)


@pytest.mark.parametrize('version,expected', [('1.6.0',True),('1.5.1',False)])
def test_older_backend_cannot_silently_serve_new_desktop(application,monkeypatch,version,expected):
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,limit):return json.dumps({'application':'midoo','version':version}).encode()
    monkeypatch.setattr(shell.urllib.request,'build_opener',lambda *args:SimpleNamespace(open=lambda *args,**kw:Response()))
    monkeypatch.setattr(shell,'port_available',lambda port:False)
    if expected:
        assert not shell.start_or_connect(application,port=8765).owned
    else:
        with pytest.raises(shell.ServiceError,match='正常关闭'):
            shell.start_or_connect(application,port=8765)


def test_own_service_startup_and_graceful_stop(application,monkeypatch):
    import uvicorn
    configs=[]
    fake_server=SimpleNamespace(started=False,should_exit=False)
    def run():fake_server.started=True
    fake_server.run=run
    class Thread:
        def __init__(self,target,**kwargs):self.target=target;self.joins=[]
        def start(self):self.target()
        def is_alive(self):return True
        def join(self,timeout):self.joins.append(timeout)
    monkeypatch.setattr(shell,'port_available',lambda port:True)
    monkeypatch.setattr(uvicorn,'Config',lambda *a,**kw:configs.append(kw) or object())
    monkeypatch.setattr(uvicorn,'Server',lambda config:fake_server)
    monkeypatch.setattr(shell.threading,'Thread',Thread)
    lease=shell.start_or_connect(application,port=8779)
    assert lease.owned and fake_server.started
    assert not fake_server.should_exit
    assert configs[0]['proxy_headers'] is False
    assert configs[0]['timeout_graceful_shutdown']==10
    lease.stop()
    assert fake_server.should_exit
    assert lease.thread.joins==[15]


def test_startup_failure_stops_only_its_own_server(application,monkeypatch):
    import uvicorn
    fake_server=SimpleNamespace(started=False,should_exit=False,run=lambda:None)
    class Thread:
        def __init__(self,**kwargs):pass
        def start(self):pass
        def is_alive(self):return False
        def join(self,timeout):pass
    monkeypatch.setattr(shell,'port_available',lambda port:True)
    monkeypatch.setattr(uvicorn,'Config',lambda *a,**kw:object())
    monkeypatch.setattr(uvicorn,'Server',lambda config:fake_server)
    monkeypatch.setattr(shell.threading,'Thread',Thread)
    with pytest.raises(shell.ServiceError,match='就绪'):
        shell.start_or_connect(application,port=8779)
    assert fake_server.should_exit


@pytest.mark.parametrize('url,allowed',[
    ('http://127.0.0.1:8765/#library',True),('http://127.0.0.1:8765/api/backup',True),
    ('http://127.0.0.1:8766/',False),('http://localhost:8765/',False),
    ('https://127.0.0.1:8765/',False),('http://127.0.0.1.evil.example:8765/',False),
    ('file:///C:/private.txt',False),('data:text/html,bad',False),
    ('http://user@127.0.0.1:8765/',False),('https://example.com',False)])
def test_web_shell_only_accepts_its_exact_origin(url,allowed):
    assert shell.same_service_origin(url,'http://127.0.0.1:8765') is allowed


def test_navigation_guards_cancel_remote_and_local_file_before_loading():
    window=FakeWindow();opened=[]
    navigation,new_window,frame,_=shell.install_navigation_guards(window,'http://127.0.0.1:8765',opener=opened.append)
    for url in ('file:///C:/private.txt','javascript:alert(1)','data:text/html,bad'):
        args=SimpleNamespace(Uri=url,Cancel=False,IsUserInitiated=True)
        navigation(None,args)
        assert args.Cancel
    assert opened==[]
    args=SimpleNamespace(Uri='https://shop.example.com/item',Cancel=False,IsUserInitiated=True)
    navigation(None,args)
    assert args.Cancel and opened==['https://shop.example.com/item']
    args=SimpleNamespace(Uri='https://shop.example.com/scripted',Cancel=False,IsUserInitiated=False)
    navigation(None,args)
    assert args.Cancel and len(opened)==1
    args=SimpleNamespace(Uri='https://shop.example.com/blank',Handled=False,IsUserInitiated=True)
    new_window(None,args)
    assert args.Handled and opened[-1]=='https://shop.example.com/blank'
    args=SimpleNamespace(Uri='https://remote.example.com/frame',Cancel=False,IsUserInitiated=True)
    frame(None,args)
    assert args.Cancel


def test_navigation_hooks_survive_delayed_webview2_initialization():
    window=FakeWindow()
    control=window.native.webview
    core=control.CoreWebView2
    control.CoreWebView2=None
    _,new_window,frame,ready=shell.install_navigation_guards(window,'http://127.0.0.1:8765')
    assert control.CoreWebView2InitializationCompleted.handlers==[ready]
    assert core.NewWindowRequested.handlers==[]
    control.CoreWebView2=core
    ready(control,SimpleNamespace(IsSuccess=True))
    assert core.NewWindowRequested.handlers==[new_window]
    assert core.FrameNavigationStarting.handlers==[frame]


def test_native_window_has_no_js_api_and_reuses_existing_service(application,monkeypatch):
    native=FakeNative()
    fake_server=SimpleNamespace(started=True,should_exit=False)
    lease=shell.ServiceLease('http://127.0.0.1:8765',False,fake_server)
    monkeypatch.setattr(shell.sys,'platform','win32')
    monkeypatch.setattr(shell,'_webview2_versions',lambda:['133.0.0.0'])
    monkeypatch.setattr(shell,'start_or_connect',lambda *a,**kw:lease)
    shell.run_desktop(application,webview_module=native)
    assert native.title==shell.APP_TITLE
    assert native.create_options['js_api'] is None
    assert native.create_options['url']==lease.local_url
    assert native.start_options['gui']=='edgechromium'
    assert native.start_options['private_mode'] is False
    assert Path(native.start_options['storage_path'])==application.DATA_DIR/'desktop-webview'
    assert native.settings['ALLOW_FILE_URLS'] is False
    assert native.settings['REMOTE_DEBUGGING_PORT'] is None
    assert native.confirmations==[True,True]  # no owned server means no shutdown warning.
    assert not fake_server.should_exit


def test_owned_service_close_requires_confirmation(application,monkeypatch):
    native=FakeNative()
    fake_server=SimpleNamespace(started=True,should_exit=False)
    lease=shell.ServiceLease('http://127.0.0.1:8765',True,fake_server)
    monkeypatch.setattr(shell.sys,'platform','win32')
    monkeypatch.setattr(shell,'_webview2_versions',lambda:['133.0.0.0'])
    monkeypatch.setattr(shell,'start_or_connect',lambda *a,**kw:lease)
    shell.run_desktop(application,webview_module=native)
    assert native.confirmations==[False,True]
    assert '共享服务' in native.window.confirm_message
    assert fake_server.should_exit


def test_native_creation_failure_stops_own_backend_before_fallback(application,monkeypatch):
    native=FakeNative(fail=True)
    fake_server=SimpleNamespace(started=True,should_exit=False)
    lease=shell.ServiceLease('http://127.0.0.1:8765',True,fake_server)
    monkeypatch.setattr(shell.sys,'platform','win32')
    monkeypatch.setattr(shell,'_webview2_versions',lambda:['133.0.0.0'])
    monkeypatch.setattr(shell,'start_or_connect',lambda *a,**kw:lease)
    with pytest.raises(shell.DesktopUnavailable,match='兼容服务管理器'):
        shell.run_desktop(application,webview_module=native)
    assert fake_server.should_exit
    assert native.window.destroyed


def test_native_unavailable_does_not_start_a_server(application,monkeypatch):
    monkeypatch.setattr(shell.sys,'platform','win32')
    monkeypatch.setattr(shell,'_webview2_versions',lambda:[])
    def unwanted(*a,**kw):raise AssertionError('backend must not start without renderer')
    monkeypatch.setattr(shell,'start_or_connect',unwanted)
    with pytest.raises(shell.DesktopUnavailable,match='WebView2'):
        shell.run_desktop(application,webview_module=FakeNative())
