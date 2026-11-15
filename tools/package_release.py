"""Build public archives from exact file sets; never include runtime data."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / 'VERSION').read_text().strip()
FILES = ('LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md', 'README.md', 'README.en.md',
         'VERSION', 'Install-Desktop.cmd', 'Uninstall-Desktop.cmd', 'PRICE_TRACKING.md',
         'PRODUCT_CLASSIFICATION.md', 'ENTERPRISE_KNOWLEDGE.md', 'STORE_IMPORT.md',
         'PAGE_CAPTURE_EXTENSION.md')
INSTALL_SCRIPTS = ('Install-Desktop.ps1', 'Uninstall-Desktop.ps1')
MODEL_FILES = frozenset({'LICENSE.txt', 'README.md', 'model.onnx',
                         'preprocessor_config.json', 'provenance.json'})
LICENSE_EXTRAS = frozenset({'manifest.json', 'python/LICENSE.txt', 'tcl/license.terms',
                            'tk/license.terms', 'dinov2-small/LICENSE.txt'})
FORBIDDEN = {'data', 'qa-data', '.work', '.git', '.venv', '__pycache__',
             'desktop-webview', 'browser-price-profiles', 'browser-store-profiles',
             '.midoo-runtime', 'backups'}
PRIVATE_NAMES = {'desktop-config.json', 'ai-config.json', '.ai-vault-key',
                 'launch-desktop.vbs', '.ecommerce-product-manager-install.json'}


def _reject_links(path: Path) -> None:
    """Inspect lexical ancestors before opening or walking a path."""
    path = Path(path).absolute()
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if (candidate.is_symlink() or
                getattr(candidate, 'is_junction', lambda: False)() or
                getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)):
            raise RuntimeError('Link or reparse point rejected from release: ' + str(candidate))


def _private(relative: Path) -> bool:
    parts = {part.casefold() for part in relative.parts}
    name = relative.name.casefold()
    return (bool(parts & FORBIDDEN) or name in PRIVATE_NAMES or name.startswith('.env') or
            bool(re.search(r'\.(?:db|sqlite)[^/]*$', name)))


def _relative_name(value: str) -> str:
    if not isinstance(value, str) or not value or '\\' in value:
        raise RuntimeError('Invalid release allowlist path')
    relative = PurePosixPath(value)
    if (relative.is_absolute() or relative == PurePosixPath('.') or '..' in relative.parts or
            relative.as_posix() != value or ':' in value or _private(Path(value))):
        raise RuntimeError('Private or invalid release allowlist path: ' + value)
    return relative.as_posix()


def _inventory(folder: Path):
    """Walk without following symbolic links, junctions or other reparse points."""
    folder = Path(folder)
    _reject_links(folder)
    if not folder.is_dir():
        raise RuntimeError('Missing release directory: ' + str(folder))
    pending = [folder]
    while pending:
        current = pending.pop()
        for path in sorted(current.iterdir()):
            _reject_links(path)
            relative = path.relative_to(folder)
            if _private(relative):
                raise RuntimeError('Private file or directory rejected from release: ' + str(relative))
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                yield path, relative, True
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                yield path, relative, False
            else:
                raise RuntimeError('Non-regular release file rejected: ' + str(relative))


def checked_files(folder: Path, allowed, *, require_complete: bool = False):
    """Only exact allowlisted files and their necessary directories are valid."""
    expected = {_relative_name(name) for name in allowed}
    directories = {parent.as_posix() for name in expected for parent in PurePosixPath(name).parents
                   if parent != PurePosixPath('.')}
    found = set()
    for path, relative, is_directory in _inventory(folder):
        name = relative.as_posix()
        if is_directory:
            if name not in directories:
                raise RuntimeError('Unexpected release directory: ' + name)
        elif name not in expected:
            raise RuntimeError('Unexpected release file: ' + name)
        else:
            found.add(name)
            yield path, relative
    if require_complete and expected - found:
        raise RuntimeError('Missing release file(s): ' + ', '.join(sorted(expected - found)))


def document_files() -> set[str]:
    files = set()
    for _, relative, is_directory in _inventory(ROOT / 'docs'):
        if not is_directory:
            if relative.suffix.lower() != '.md':
                raise RuntimeError('Only public Markdown documents are allowed in docs: ' + str(relative))
            files.add(relative.as_posix())
    if not files:
        raise RuntimeError('Missing release documentation')
    return files


def license_files() -> set[str]:
    folder = ROOT / 'third_party_licenses'
    manifest = folder / 'manifest.json'
    _reject_links(manifest)
    data = json.loads(manifest.read_text(encoding='utf-8'))
    expected = set(LICENSE_EXTRAS)
    for package in data['packages']:
        for value in package['license_files']:
            name = _relative_name(value)
            basename = PurePosixPath(name).name.casefold()
            if not (basename.startswith(('license', 'copying', 'notice', 'thirdpartynotices', 'authors', 'upstream-notice')) or
                    basename.endswith(('.license', '-license.txt')) or basename == 'mit-reference.txt'):
                raise RuntimeError('Unexpected license manifest file: ' + name)
            expected.add(name)
    list(checked_files(folder, expected, require_complete=True))
    return expected


def package_files(documents, licenses) -> set[str]:
    return ({'Midoo.exe', 'Uninstall.exe', *FILES} |
            {'docs/' + name for name in documents} |
            {'third_party_licenses/' + name for name in licenses} |
            {'tools/' + name for name in INSTALL_SCRIPTS})


def release_files() -> set[str]:
    if not re.fullmatch(r'[0-9A-Za-z][0-9A-Za-z.-]*', VERSION):
        raise RuntimeError('Invalid release version')
    return {f'EcommerceProductManager-{VERSION}-Windows.zip',
            f'EcommerceProductManager-Setup-{VERSION}.exe',
            'dinov2-small-onnx-model.zip', 'SHA256SUMS.txt'}


def _ensure_directory(folder: Path) -> None:
    _reject_links(folder)
    folder.mkdir(parents=True, exist_ok=True)
    _reject_links(folder)


def make_zip(folder, destination, allowed, prefix=''):
    # Validate everything before replacing an existing valid archive.
    entries = list(checked_files(folder, allowed, require_complete=True))
    destination = Path(destination)
    _reject_links(destination)
    _ensure_directory(destination.parent)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.midoo-archive-', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
            for path, relative in entries:
                _reject_links(path)
                archive.write(path, (PurePosixPath(prefix) / relative.as_posix()).as_posix())
        _reject_links(destination)
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def checksums(output, *, require_complete: bool = False):
    output = Path(output)
    entries = list(checked_files(output, release_files()))
    names = {relative.as_posix() for _, relative in entries}
    required = release_files() - {'SHA256SUMS.txt'}
    if require_complete and required - names:
        raise RuntimeError('Missing release artifact(s): ' + ', '.join(sorted(required - names)))
    lines = []
    for path, relative in entries:
        if relative.as_posix() != 'SHA256SUMS.txt':
            with path.open('rb') as source:
                digest = hashlib.file_digest(source, 'sha256').hexdigest()
            lines.append(digest + '  ' + relative.as_posix())
    destination = output / 'SHA256SUMS.txt'
    _reject_links(destination)
    destination.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _copy(source: Path, destination: Path) -> None:
    _reject_links(source)
    _reject_links(destination)
    if not source.is_file():
        raise RuntimeError('Missing release source: ' + str(source))
    _ensure_directory(destination.parent)
    shutil.copy2(source, destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, default=ROOT / 'dist' / 'windows' / 'Midoo.exe')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / 'release' / VERSION)
    parser.add_argument('--package', type=Path, default=ROOT / 'dist' / 'windows-package' / VERSION)
    parser.add_argument('--checksums-only', action='store_true')
    args = parser.parse_args()
    _ensure_directory(args.output)
    # Previous releases or arbitrary files must not enter checksums or new ZIPs.
    list(checked_files(args.output, release_files()))
    if args.checksums_only:
        checksums(args.output, require_complete=True)
        return
    _reject_links(args.exe)
    uninstaller = args.exe.parent / 'Uninstall.exe'
    _reject_links(uninstaller)
    if not args.exe.is_file() or not uninstaller.is_file():
        parser.error('Build Midoo.exe and Uninstall.exe before packaging.')
    documents, licenses = document_files(), license_files()
    allowed = package_files(documents, licenses)
    model = ROOT / 'models' / 'dinov2-small'
    list(checked_files(model, MODEL_FILES, require_complete=True))
    _ensure_directory(args.package)
    list(checked_files(args.package, allowed))
    for filename in FILES:
        _reject_links(ROOT / filename)
        if not (ROOT / filename).is_file():
            parser.error('Missing release document: ' + filename)
    _copy(args.exe, args.package / 'Midoo.exe')
    _copy(uninstaller, args.package / 'Uninstall.exe')
    for filename in FILES:
        _copy(ROOT / filename, args.package / filename)
    for folder, expected in (('docs', documents), ('third_party_licenses', licenses)):
        for source, relative in checked_files(ROOT / folder, expected, require_complete=True):
            _copy(source, args.package / folder / relative)
    for filename in INSTALL_SCRIPTS:
        _copy(ROOT / 'tools' / filename, args.package / 'tools' / filename)
    make_zip(args.package, args.output / f'EcommerceProductManager-{VERSION}-Windows.zip',
             allowed, f'EcommerceProductManager-{VERSION}')
    make_zip(model, args.output / 'dinov2-small-onnx-model.zip', MODEL_FILES, 'models/dinov2-small')
    checksums(args.output)
    print('PUBLIC_RELEASE_PACKAGE_OK: exact application, documentation, installer and model file sets; no runtime material')


if __name__ == '__main__':
    main()
