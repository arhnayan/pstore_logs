# -*- mode: python ; coding: utf-8 -*-

import os

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

datas = [
    ("app/static", "app/static"),
    ("app/reports/vodafone_theme.xml", "app/reports"),
]
# Optional report data; the vodafone-rapor-2 folder is not in git, so CI builds skip it.
for src, dest in [
    ("vodafone-rapor-2/raw_report_csv", "report_csv"),
    ("vodafone-rapor-2/formatted_report_csv", "report_capacity_csv"),
]:
    if os.path.isdir(src):
        datas.append((src, dest))

a = Analysis(
    ["run_standalone.py"],
    pathex=[],
    binaries=[],
    datas=datas,
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
