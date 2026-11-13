# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_data_files

hiddenimports = []
hiddenimports += collect_submodules('uvicorn')
hiddenimports += ['playwright.sync_api']
hiddenimports += ['webview.platforms.winforms', 'webview.platforms.edgechromium']


a = Analysis(
    ['desktop.py'],
    pathex=[],
    binaries=[],
    datas=[('static', 'static'), ('models', 'models')] + collect_data_files('webview'),
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
