"""Installer filesystem behavior with shortcuts mocked; no real user install."""
import base64
import json
from types import SimpleNamespace

import pytest

import desktop_installer as installer


@pytest.fixture
def package(tmp_path,monkeypatch):
    source=tmp_path/'package';source.mkdir()
    (source/'Midoo.exe').write_bytes(b'fixture-executable')
    (source/'LICENSE').write_text('fixture license')
    (source/'data').mkdir();(source/'data'/'library.db').write_text('must not copy')
    (source/'desktop-config.json').write_text('{"data_dir":"private"}')
    (source/'docs').mkdir();(source/'docs'/'guide.md').write_text('guide')
    desktop=tmp_path/'Desktop';desktop.mkdir()
    start=tmp_path/'Start';start.mkdir()
    encoded=base64.b64encode(json.dumps({'desktop':str(desktop),'start':str(start)}).encode()).decode()
    monkeypatch.setattr(installer,'installation_running',lambda _:False)
    monkeypatch.setattr(installer,'powershell',lambda _:encoded)
    monkeypatch.setattr(installer.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout='',stderr=''))
    return source


def test_install_allowlist_preserves_existing_external_data(tmp_path,package):
    target=tmp_path/'app';data=tmp_path/'original-data';data.mkdir()
    sentinel=data/'library.db';sentinel.write_bytes(b'private product library')
    result=installer.install(package,target,data,launch=False)
    assert sentinel.read_bytes()==b'private product library'
    assert not (target/'data'/'library.db').exists()
    assert (target/'docs'/'guide.md').read_text()=='guide'
    assert json.loads((target/'desktop-config.json').read_text())['data_dir']==str(data)
    assert len(result['shortcuts'])==3
    installer.install(package,target,launch=False)
    assert json.loads((target/'desktop-config.json').read_text())['data_dir']==str(data)


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
    assert (target/'personal-note.txt').read_text()=='keep'
    assert not (target/'Midoo.exe').exists()
    assert result['data_preserved']==str(target/'data')


def test_uninstall_rejects_traversal_before_any_removal(tmp_path,package):
    target=tmp_path/'app';installer.install(package,target,launch=False)
    marker=target/installer.MARKER
    value=json.loads(marker.read_text());value['files'].append('../outside.txt')
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
