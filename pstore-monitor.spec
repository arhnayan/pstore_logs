# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

hiddenimports = (
    collect_submodules("app")
    + collect_submodules("uvicorn")
    + collect_submodules("starlette")
    + collect_submodules("fastapi")
    + collect_submodules("httpx")
    + collect_submodules("pandas")
    + collect_submodules("openpyxl")
    + [
        "aiosqlite",
        "numpy",
        "pydantic.deprecated.decorator",
    ]
)

a = Analysis(
    ["run_standalone.py"],
    pathex=[],
    binaries=[],
    datas=[("app/static", "app/static")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="pstore-monitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="pstore-monitor",
)
