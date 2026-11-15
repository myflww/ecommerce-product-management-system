"""The downloadable extension must never include captured/company content."""
import io
import zipfile

import pytest

import page_capture_package as package


def source_tree(root):
    folder = root / 'browser-extension'
    folder.mkdir()
    for name in package.EXTENSION_FILES:
        (folder / name).write_text('public-' + name, encoding='utf-8')
    return folder


def test_archive_exactly_lists_public_sources_and_ignores_capture_files(tmp_path):
    folder = source_tree(tmp_path)
    (folder / 'midoo-page-capture.json').write_text('private-capture')
    (folder / 'queue').mkdir()
    (folder / 'queue' / 'product.jpg').write_bytes(b'private-image')
    with zipfile.ZipFile(io.BytesIO(package.extension_archive(tmp_path))) as archive:
        assert set(archive.namelist()) == {'Midoo-Page-Capture/' + x for x in package.EXTENSION_FILES}
        assert all(b'private' not in archive.read(x) for x in archive.namelist())


def test_archive_missing_source_fails_instead_of_partial_plugin(tmp_path):
    source_tree(tmp_path)
    (tmp_path / 'browser-extension' / 'manifest.json').unlink()
    with pytest.raises(OSError):
        package.extension_archive(tmp_path)


def test_archive_oversized_resource_is_rejected(tmp_path, monkeypatch):
    source_tree(tmp_path)
    monkeypatch.setattr(package, 'MAX_SOURCE_BYTES', 10)
    with pytest.raises(ValueError):
        package.extension_archive(tmp_path)


def test_route_reports_incomplete_install_without_leaking_paths(tmp_path, monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(package, 'ROOT', tmp_path)
    with pytest.raises(HTTPException) as caught:
        package.extension_response()
    assert caught.value.status_code == 503
    assert str(tmp_path) not in caught.value.detail


def test_download_response_has_attachment_and_zip_type(tmp_path, monkeypatch):
    source_tree(tmp_path)
    monkeypatch.setattr(package, 'ROOT', tmp_path)
    response = package.extension_response()
    assert response.media_type == 'application/zip'
    assert 'attachment;' in response.headers['content-disposition']
    assert response.headers['cache-control'] == 'no-store'
