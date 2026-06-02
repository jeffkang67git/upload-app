# -*- mode: python ; coding: utf-8 -*-
import os, struct

block_cipher = None

a = Analysis(
    ['src/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('src/*.py', 'src'),
        ('fonts/*.otf', 'fonts'),
    ],
    hiddenimports=[
        'PyQt5',
        'PyQt5.QtCore',
        'PyQt5.QtGui',
        'PyQt5.QtWidgets',
        'ftplib',
        'ssl',
        'fitz',
        'selenium',
        'selenium.webdriver.chrome.options',
        'selenium.webdriver.chrome.service',
        'webdriver_manager',
        'webbrowser',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='体检报告上传',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='体检报告上传',
)
