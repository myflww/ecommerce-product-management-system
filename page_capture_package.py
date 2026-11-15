"""Download only the reviewed, public current-page extension source files."""
from __future__ import annotations

import io
import stat
import sys
import zipfile
from pathlib import Path

from fastapi import HTTPException, Response

EXTENSION_FILES = frozenset({
    'manifest.json', 'popup.html', 'popup.css', 'popup.js', 'capture-core.js',
    'collector.js', 'README.md', 'icon.svg',
})
MAX_SOURCE_BYTES = 2 * 1024 * 1024
ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))


def _read_public(path: Path) -> bytes:
    # A same-named link must never turn the public download into a private-file
    # reader. Check lexical ancestors too, before resolving/opening anything.
    for candidate in (path, *path.parents):
        info = candidate.lstat()
        if (stat.S_ISLNK(info.st_mode) or
                getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)):
            raise ValueError('Extension resource link rejected')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SOURCE_BYTES:
        raise ValueError('Invalid extension resource')
    with path.open('rb') as stream:
        data = stream.read(MAX_SOURCE_BYTES + 1)
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError('Extension resource too large')
    return data


def extension_archive(root: Path | None = None) -> bytes:
    """Create an in-memory ZIP; runtime captures are never enumerated."""
    root = Path(root) if root is not None else ROOT
    output, total = io.BytesIO(), 0
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(EXTENSION_FILES):
            data = _read_public(root / 'browser-extension' / name)
            total += len(data)
            if total > MAX_SOURCE_BYTES:
                raise ValueError('Extension source archive too large')
            archive.writestr('Midoo-Page-Capture/' + name, data)
    return output.getvalue()


def extension_response() -> Response:
    try:
        data = extension_archive()
    except (OSError, ValueError):
        raise HTTPException(503, '插件文件未安装完整，请更新软件或联系管理员。') from None
    return Response(data, media_type='application/zip', headers={
        'Content-Disposition': 'attachment; filename="Midoo-Page-Capture.zip"',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
    })
