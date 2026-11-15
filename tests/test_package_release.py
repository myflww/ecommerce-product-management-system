"""Public archives accept exact sets and reject stale files without deleting them."""
from __future__ import annotations

import ast
import importlib.util
import io
import json
from pathlib import Path
import sys
import zipfile

import pytest

SPEC = importlib.util.spec_from_file_location('midoo_package_release', Path(__file__).resolve().parents[1] / 'tools' / 'package_release.py')
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    for name in release.FILES:
        (source / name).write_text('public release material', encoding='utf-8')
    (source / 'docs').mkdir()
    (source / 'docs' / 'USER_GUIDE.en.md').write_text('Public guide', encoding='utf-8')
    licenses = source / 'third_party_licenses'
    for name in release.LICENSE_EXTRAS | {'example/LICENSE'}:
        path = licenses / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('Upstream notice', encoding='utf-8')
    (licenses / 'manifest.json').write_text(json.dumps({'packages':[{'license_files':['example/LICENSE']}]}), encoding='utf-8')
    model = source / 'models' / 'dinov2-small'
    model.mkdir(parents=True)
    for name in release.MODEL_FILES:
        (model / name).write_bytes(b'public model fixture')
    tools = source / 'tools'
    tools.mkdir()
    for name in release.INSTALL_SCRIPTS:
        (tools / name).write_text('installer fixture', encoding='utf-8')
    binaries = tmp_path / 'binaries'
    binaries.mkdir()
    for name in ('Midoo.exe', 'Uninstall.exe'):
        (binaries / name).write_bytes(b'MZ test-only executable')
    package, output = tmp_path / 'package', tmp_path / 'output'
    monkeypatch.setattr(release, 'ROOT', source)
    monkeypatch.setattr(release, 'VERSION', '1.6.0')
    monkeypatch.setattr(sys, 'argv', ['package_release.py', '--exe', str(binaries / 'Midoo.exe'),
                                    '--package', str(package), '--output', str(output)])
    return source, package, output


def test_complete_public_package_and_legal_repeat_build(tree):
    source, package, output = tree
    release.main()
    expected = release.package_files(release.document_files(), release.license_files())
    assert {relative.as_posix() for _, relative in release.checked_files(package, expected, require_complete=True)} == expected
    archive = output / 'EcommerceProductManager-1.6.0-Windows.zip'
    with zipfile.ZipFile(archive) as zipped:
        assert set(zipped.namelist()) == {'EcommerceProductManager-1.6.0/' + name for name in expected}
    setup = output / 'EcommerceProductManager-Setup-1.6.0.exe'
    setup.write_bytes(b'MZ setup fixture')
    release.main()
    release.checksums(output, require_complete=True)
    assert setup.name in (output / 'SHA256SUMS.txt').read_text()
    assert setup.read_bytes() == b'MZ setup fixture'


@pytest.mark.parametrize('name', ['customer-export.csv', '.env.local', 'library.db-wal', 'library.sqlite3-journal'])
def test_stale_package_files_are_rejected_and_preserved(tree, name):
    _, package, output = tree
    package.mkdir()
    stale = package / name
    stale.write_text('private fixture', encoding='utf-8')
    with pytest.raises(RuntimeError, match='Private|Unexpected'):
        release.main()
    assert stale.read_text() == 'private fixture'
    assert not list(output.glob('*.zip'))


def test_release_output_extra_is_rejected_before_packaging_or_checksums(tree):
    _, package, output = tree
    output.mkdir()
    stale = output / 'customer-export.csv'
    stale.write_text('private fixture', encoding='utf-8')
    with pytest.raises(RuntimeError, match='Unexpected release file'):
        release.main()
    with pytest.raises(RuntimeError, match='Unexpected release file'):
        release.checksums(output)
    assert stale.exists() and not (output / 'SHA256SUMS.txt').exists()
    assert not package.exists()


@pytest.mark.parametrize('folder_name', ['browser-store-profiles', '.midoo-runtime'])
def test_private_runtime_directories_cannot_enter_the_public_package(tree, folder_name):
    _, package, output = tree
    private = package / folder_name
    private.mkdir(parents=True)
    (private / 'session.txt').write_text('synthetic private session', encoding='utf-8')
    with pytest.raises(RuntimeError, match='Private'):
        release.main()
    assert (private / 'session.txt').exists() and not list(output.glob('*.zip'))


@pytest.mark.parametrize('name', ['Launch-Desktop.vbs', '.ecommerce-product-manager-install.json'])
def test_generated_local_install_files_cannot_be_explicitly_allowlisted(tmp_path, name):
    (tmp_path / name).write_text('synthetic local installation metadata', encoding='utf-8')
    with pytest.raises(RuntimeError, match='Private'):
        list(release.checked_files(tmp_path, {name}, require_complete=True))
    assert (tmp_path / name).is_file()


def test_model_directory_extra_is_rejected_before_any_archive_is_written(tree):
    source, _, output = tree
    stale = source / 'models' / 'dinov2-small' / 'customer.csv'
    stale.write_text('private fixture', encoding='utf-8')
    with pytest.raises(RuntimeError, match='Unexpected release file'):
        release.main()
    assert stale.exists() and not list(output.glob('*.zip'))


def test_symlink_and_junction_rejected_without_reading_targets(tmp_path, monkeypatch):
    folder = tmp_path / 'package'
    folder.mkdir()
    outside = tmp_path / 'outside.txt'
    outside.write_text('outside fixture', encoding='utf-8')
    link = folder / 'README.md'
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip('Creating a synthetic symlink is not permitted on this host')
    with pytest.raises(RuntimeError, match='Link or reparse point'):
        list(release.checked_files(folder, {'README.md'}))
    link.unlink()
    junction = folder / 'docs'
    junction.mkdir()
    monkeypatch.setattr(Path, 'is_junction', lambda path: path == junction, raising=False)
    with pytest.raises(RuntimeError, match='Link or reparse point'):
        list(release.checked_files(folder, {'docs/guide.md'}))


def test_unknown_file_cannot_damage_existing_archive(tmp_path):
    folder = tmp_path / 'package'
    folder.mkdir()
    (folder / 'README.md').write_text('public', encoding='utf-8')
    (folder / 'extra.csv').write_text('private fixture', encoding='utf-8')
    archive = tmp_path / 'release.zip'
    archive.write_bytes(b'previous valid release fixture')
    with pytest.raises(RuntimeError, match='Unexpected release file'):
        release.make_zip(folder, archive, {'README.md'})
    assert archive.read_bytes() == b'previous valid release fixture'


def test_embedded_extension_and_download_share_public_allowlist(tmp_path):
    import page_capture_package

    spec = ast.parse((Path(__file__).resolve().parents[1] / 'Midoo.spec').read_text(encoding='utf-8'))
    resources = next(ast.literal_eval(item.value) for item in spec.body
                     if isinstance(item, ast.Assign) and
                     any(isinstance(target, ast.Name) and target.id == 'resources' for target in item.targets))
    allowed = resources['browser-extension']
    assert allowed == page_capture_package.EXTENSION_FILES
    folder = tmp_path / 'browser-extension'
    folder.mkdir()
    for name in allowed:
        (folder / name).write_text('public source fixture', encoding='utf-8')
    assert {relative.as_posix() for _, relative in release.checked_files(folder, allowed, require_complete=True)} == allowed

    # Runtime downloads must not pick up captures accidentally stored nearby;
    # the build gate must reject the contaminated resource tree altogether.
    (folder / 'capture.json').write_text('{"private_fixture":true}', encoding='utf-8')
    (folder / 'product-photo.png').write_bytes(b'private image fixture')
    with zipfile.ZipFile(io.BytesIO(page_capture_package.extension_archive(tmp_path))) as archive:
        assert set(archive.namelist()) == {'Midoo-Page-Capture/' + name for name in allowed}
    with pytest.raises(RuntimeError, match='Unexpected release file'):
        list(release.checked_files(folder, allowed, require_complete=True))
    assert (folder / 'capture.json').is_file() and (folder / 'product-photo.png').is_file()
