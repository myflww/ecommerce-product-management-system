# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_data_files
from pathlib import Path
from tools.package_release import checked_files

# Explicit application resources prevent private images or test samples from
# entering the executable when the workspace also contains company data.
resources = {
    'static': {
        'index.html', 'app.js', 'styles.css', 'assets/mark.svg', 'assets/midoo.ico',
        'price-tracking.js', 'price-tracking.css', 'announcements.js', 'announcements.css',
        'knowledge-nebula.js', 'knowledge-nebula.css', 'product-merge.js', 'product-merge.css',
        'store-import.js', 'store-import.css',
    },
    'models': {
        'dinov2-small/model.onnx', 'dinov2-small/LICENSE.txt', 'dinov2-small/README.md',
        'dinov2-small/preprocessor_config.json', 'dinov2-small/provenance.json',
    },
    'browser-extension': {
        'manifest.json', 'popup.html', 'popup.css', 'popup.js',
        'capture-core.js', 'collector.js', 'README.md', 'icon.svg',
    },
}
bundle_datas = []
for folder, allowed in resources.items():
    for source, relative in checked_files(Path(SPECPATH) / folder, allowed, require_complete=True):
        bundle_datas.append((str(source), str(Path(folder) / relative.parent)))

hiddenimports = []
hiddenimports += collect_submodules('uvicorn')
hiddenimports += ['playwright.sync_api']
hiddenimports += ['page_capture_import', 'page_capture_package']
hiddenimports += ['webview.platforms.winforms', 'webview.platforms.edgechromium']


a = Analysis(
    ['desktop.py'],
    pathex=[],
    binaries=[],
    datas=bundle_datas + collect_data_files('webview'),
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', '_pytest'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Midoo',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['static/assets/midoo.ico'],
)
