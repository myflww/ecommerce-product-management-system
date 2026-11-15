"""Installer filesystem behavior with shortcuts mocked; no real user install."""
import base64
import json
from types import SimpleNamespace
from pathlib import PureWindowsPath

import pytest

import desktop_installer as installer


@pytest.fixture
def package(tmp_path,monkeypatch):
    source=tmp_path/'package';source.mkdir()
    (source/'Midoo.exe').write_bytes(b'fixture-executable')
    (source/'LICENSE').write_text('fixture license')
    (source/'ENTERPRISE_KNOWLEDGE.md').write_text('public knowledge guide')
    (source/'STORE_IMPORT.md').write_text('public import guide')
    (source/'PAGE_CAPTURE_EXTENSION.md').write_text('public capture guide')
    (source/'data').mkdir();(source/'data'/'library.db').write_text('must not copy')
    (source/'desktop-config.json').write_text('{"data_dir":"private"}')
    (source/'docs').mkdir();(source/'docs'/'guide.md').write_text('guide')
    desktop=tmp_path/'Desktop';desktop.mkdir()
    start=tmp_path/'Start';start.mkdir()
    encoded=base64.b64encode(json.dumps({'desktop':str(desktop),'start':str(start)}).encode()).decode()
    monkeypatch.setattr(installer,'installation_running',lambda _:False)
    monkeypatch.setattr(installer,'wscript_host',lambda:tmp_path/'Windows'/'System32'/'wscript.exe')
    monkeypatch.setattr(installer,'powershell',lambda _:encoded)
    monkeypatch.setattr(installer.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout='',stderr=''))
    return source


def test_install_allowlist_preserves_existing_external_data(tmp_path,package):
    target=tmp_path/'app';data=tmp_path/'original-data';data.mkdir()
    sentinel=data/'library.db';sentinel.write_bytes(b'private product library')
    result=installer.install(package,target,data,launch=False)
    assert sentinel.read_bytes()==b'private product library'
    assert not (target/'data'/'library.db').exists()
    assert (target/'docs'/'guide.md').read_text(encoding='utf-8')=='guide'
    assert (target/'ENTERPRISE_KNOWLEDGE.md').read_text(encoding='utf-8')=='public knowledge guide'
    assert (target/'STORE_IMPORT.md').read_text(encoding='utf-8')=='public import guide'
    assert (target/'PAGE_CAPTURE_EXTENSION.md').read_text(encoding='utf-8')=='public capture guide'
    assert json.loads((target/'desktop-config.json').read_text(encoding='utf-8'))['data_dir']==str(data)
    assert len(result['shortcuts'])==3
    installer.install(package,target,launch=False)
    assert json.loads((target/'desktop-config.json').read_text(encoding='utf-8'))['data_dir']==str(data)


def test_running_installation_is_never_stopped_or_replaced(tmp_path,package,monkeypatch):
    target=tmp_path/'app';target.mkdir()
    exe=target/'Midoo.exe';exe.write_bytes(b'old')
    (target/installer.MARKER).write_text(json.dumps({'product':'EcommerceProductManager','install_path':str(target)}))
    monkeypatch.setattr(installer,'installation_running',lambda _:True)
    with pytest.raises(RuntimeError,match='close'):
        installer.install(package,target,launch=False)
    assert exe.read_bytes()==b'old'


def test_unknown_directory_and_missing_data_are_rejected(tmp_path,package):
    target=tmp_path/'unrelated';target.mkdir();(target/'Midoo.exe').write_bytes(b'other')
    with pytest.raises(RuntimeError,match='unrecognized'):
        installer.install(package,target,launch=False)
    with pytest.raises(RuntimeError,match='not found'):
        installer.install(package,tmp_path/'new',tmp_path/'missing-data',launch=False)


def test_powershell_literal_does_not_interpolate_user_path():
    assert installer.ps_literal("C:/One's $file") == "'C:/One''s $file'"


def test_uninstall_keeps_data_and_untracked_files(tmp_path,package):
    target=tmp_path/'app'
    installer.install(package,target,launch=False)
    (target/'data'/'library.db').write_bytes(b'private')
    (target/'personal-note.txt').write_text('keep')
    result=installer.uninstall(target)
    assert (target/'data'/'library.db').read_bytes()==b'private'
    assert (target/'personal-note.txt').read_text(encoding='utf-8')=='keep'
    assert not (target/'Midoo.exe').exists()
    assert result['data_preserved']==str(target/'data')


def test_uninstall_rejects_traversal_before_any_removal(tmp_path,package):
    target=tmp_path/'app';installer.install(package,target,launch=False)
    marker=target/installer.MARKER
    value=json.loads(marker.read_text(encoding='utf-8'));value['files'].append('../outside.txt')
    marker.write_text(json.dumps(value))
    with pytest.raises(RuntimeError,match='protected|out-of-directory'):
        installer.uninstall(target)
    assert (target/'Midoo.exe').exists()


def test_relative_saved_data_is_based_on_install_directory(tmp_path,package,monkeypatch):
    target=tmp_path/'app';installer.install(package,target,launch=False)
    (target/'retained').mkdir();(target/'retained'/'library.db').write_bytes(b'original')
    (target/'desktop-config.json').write_text('{"data_dir":"retained"}')
    monkeypatch.chdir(tmp_path)
    result=installer.install(package,target,launch=False)
    assert result['data_dir']==str(target/'retained')
    assert (target/'retained'/'library.db').read_bytes()==b'original'


def test_linked_parent_is_rejected_before_writing(tmp_path,package,monkeypatch):
    target=tmp_path/'linked'/'app'
    original=type(target).is_symlink
    monkeypatch.setattr(type(target),'is_symlink',lambda p: p==tmp_path/'linked' or original(p))
    with pytest.raises(RuntimeError,match='junction'):
        installer.install(package,target,launch=False)
    assert not target.exists()


def test_uninstall_rejects_linked_child_before_removing_application(tmp_path,package,monkeypatch):
    target=tmp_path/'app';installer.install(package,target,launch=False)
    original=type(target).is_symlink
    monkeypatch.setattr(type(target),'is_symlink',lambda p: p==target/'docs' or original(p))
    with pytest.raises(RuntimeError,match='junction'):
        installer.uninstall(target)
    assert (target/'Midoo.exe').exists()


def test_default_runtime_follows_data_volume_without_touching_that_volume():
    data=PureWindowsPath('D:/企业资料/商品 data')
    assert installer.default_runtime_dir(data)==PureWindowsPath('D:/企业资料/.midoo-runtime')
    assert not installer.default_runtime_dir(data).is_relative_to(data)


def test_upgrade_keeps_marker_data_and_runtime_when_config_is_missing(tmp_path,package):
    target=tmp_path/'app';data=tmp_path/'separate data';data.mkdir()
    (data/'library.db').write_bytes(b'private')
    runtime=tmp_path/'chosen runtime'
    installer.install(package,target,data,launch=False,runtime_dir=runtime)
    (runtime/'retained.cache').write_bytes(b'keep runtime')
    (target/'desktop-config.json').unlink()
    result=installer.install(package,target,launch=False)
    assert result['data_dir']==str(data)
    assert result['runtime_dir']==str(runtime)
    assert (data/'library.db').read_bytes()==b'private'
    assert (runtime/'retained.cache').read_bytes()==b'keep runtime'
    assert not (target/'data').exists()


def test_runtime_directory_is_a_sibling_and_config_unknown_fields_survive(tmp_path,package):
    target=tmp_path/'app';data=tmp_path/'chosen'/'business';data.mkdir(parents=True)
    result=installer.install(package,target,data,launch=False)
    assert result['runtime_dir']==str(data.parent/'.midoo-runtime')
    config=target/'desktop-config.json'
    config.write_text(json.dumps({'data_dir':str(data),'future_setting':{'enabled':True}}))
    installer.install(package,target,launch=False)
    assert json.loads(config.read_text(encoding='utf-8'))['future_setting']=={'enabled':True}


@pytest.mark.parametrize('runtime_location',['data-child','data-parent','app','runtime-file'])
def test_invalid_runtime_is_rejected_before_copy_or_data_change(tmp_path,package,runtime_location):
    target=tmp_path/'app';data=tmp_path/'business';data.mkdir()
    sentinel=data/'library.db';sentinel.write_bytes(b'private')
    runtime={'data-child':data/'temp','data-parent':tmp_path,'app':target,'runtime-file':tmp_path/'file'}[runtime_location]
    if runtime_location=='runtime-file':runtime.write_text('keep')
    with pytest.raises(RuntimeError,match='runtime'):
        installer.install(package,target,data,launch=False,runtime_dir=runtime)
    assert sentinel.read_bytes()==b'private'
    assert not target.exists()


def test_linked_runtime_parent_is_rejected_before_copy(tmp_path,package,monkeypatch):
    target=tmp_path/'app';runtime=tmp_path/'linked'/'runtime'
    original=type(target).is_symlink
    monkeypatch.setattr(type(target),'is_symlink',lambda p: p==tmp_path/'linked' or original(p))
    with pytest.raises(RuntimeError,match='junction'):
        installer.install(package,target,launch=False,runtime_dir=runtime)
    assert not target.exists()


def test_generated_launcher_is_owned_utf16_and_first_launch_uses_explicit_host(tmp_path,package,monkeypatch):
    target=tmp_path/'中文 安装';runtime=tmp_path/'runtime space'
    launched=[]
    monkeypatch.setattr(installer.subprocess,'Popen',lambda *a,**k:launched.append((a,k)))
    installer.install(package,target,launch=True,runtime_dir=runtime)
    marker=json.loads((target/installer.MARKER).read_text(encoding='utf-8'))
    identity=marker['launcher']
    assert marker['runtime_path']==str(runtime)
    assert installer.LAUNCHER in marker['files']
    assert all('.midoo-runtime' not in item for item in marker['files'])
    script_bytes=(target/installer.LAUNCHER).read_bytes()
    assert script_bytes.startswith(b'\xff\xfe')
    script=script_bytes.decode('utf-16')
    assert script==installer.launcher_script(target/'Midoo.exe',runtime,target)
    assert 'shell.Environment("Process")' in script
    assert 'processEnvironment("TEMP") = runtimePath' in script
    assert 'processEnvironment("TMP") = runtimePath' in script
    assert 'processEnvironment("MIDOO_LAUNCH_LITERAL_PERCENT") = "%"' in script
    assert 'shell.Run LiteralCommand(command), 1, False' in script
    assert '--uninstall' in script
    assert 'MsgBox message, 16, "Midoo"' in script
    assert launched[0][0][0]==[identity['host'],'//nologo',str(target/installer.LAUNCHER)]
    assert launched[0][1]['cwd']==target
    assert identity['arguments']=='//nologo '+installer.windows_quote_argument(target/installer.LAUNCHER)


def test_vbs_constants_escape_quotes_and_windows_arguments_escape_slashes():
    script=installer.launcher_script(PureWindowsPath('D:/中文 a/Midoo.exe'),PureWindowsPath('D:/runtime'),PureWindowsPath('D:/a"b'))
    assert 'workingPath = "D:\\a""b"' in script
    assert installer.windows_quote_argument('a"b')=='"a\\"b"'
    assert installer.windows_quote_argument('ends\\')=='"ends\\\\"'
    assert installer.windows_quote_argument('')=='""'


def test_uninstall_keeps_runtime_and_rejects_manifest_entry_inside_it(tmp_path,package):
    target=tmp_path/'app';runtime=target/'runtime'
    installer.install(package,target,launch=False,runtime_dir=runtime)
    cache=runtime/'keep.cache';cache.write_bytes(b'keep')
    marker=target/installer.MARKER;state=json.loads(marker.read_text(encoding='utf-8'))
    state['files'].append('runtime/keep.cache');marker.write_text(json.dumps(state))
    with pytest.raises(RuntimeError,match='protected'):
        installer.uninstall(target)
    assert (target/'Midoo.exe').exists()
    assert cache.read_bytes()==b'keep'
    state['files'].remove('runtime/keep.cache');marker.write_text(json.dumps(state))
    installer.uninstall(target)
    assert cache.read_bytes()==b'keep'
    assert not (target/installer.LAUNCHER).exists()


def test_tampered_launcher_identity_cannot_delete_managed_files(tmp_path,package):
    target=tmp_path/'app';installer.install(package,target,launch=False)
    marker=target/installer.MARKER;state=json.loads(marker.read_text(encoding='utf-8'))
    state['launcher']['arguments']+=' --other-script'
    marker.write_text(json.dumps(state))
    with pytest.raises(RuntimeError,match='launcher identity'):
        installer.uninstall(target)
    assert (target/'Midoo.exe').exists()


def test_shortcut_creation_failure_preserves_existing_data(tmp_path,package,monkeypatch):
    target=tmp_path/'app';data=tmp_path/'business';data.mkdir()
    sentinel=data/'library.db';sentinel.write_bytes(b'private')
    monkeypatch.setattr(installer.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1,stdout='',stderr='mock COM failure'))
    with pytest.raises(RuntimeError,match='shortcuts'):
        installer.install(package,target,data,launch=False)
    assert sentinel.read_bytes()==b'private'


def test_untracked_launcher_is_not_overwritten(tmp_path,package):
    target=tmp_path/'app';target.mkdir()
    existing=target/installer.LAUNCHER;existing.write_bytes(b'user-created script')
    with pytest.raises(RuntimeError,match='unrecognized launcher'):
        installer.install(package,target,launch=False)
    assert existing.read_bytes()==b'user-created script'
    assert not (target/'Midoo.exe').exists()


def test_saved_data_file_is_rejected_before_replacing_application(tmp_path,package):
    target=tmp_path/'app';installer.install(package,target,launch=False)
    data_file=tmp_path/'not-a-directory';data_file.write_bytes(b'keep')
    (target/'desktop-config.json').write_text(json.dumps({'data_dir':str(data_file)}))
    (target/'Midoo.exe').write_bytes(b'old installed executable')
    with pytest.raises(RuntimeError,match='data directory is an existing file'):
        installer.install(package,target,launch=False)
    assert (target/'Midoo.exe').read_bytes()==b'old installed executable'
    assert data_file.read_bytes()==b'keep'
