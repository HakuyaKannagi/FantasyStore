# -*- mode: python ; coding: utf-8 -*-
# Phase 7 Windows onedir build. Run from repository root via build_windows.ps1.
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

project_root = Path(SPECPATH).parent.resolve()
entry = project_root / "fantasy_store" / "main.py"

datas = [
    (str(project_root / "ui"), "ui"),
    (str(project_root / "resources"), "resources"),
    (str(project_root / "docs" / "RELEASE_README.md"), "."),
]

# pywebview 6.2.1 ships its own PyInstaller hook for webview/lib and webview/js.
# Renderer selection is dynamic; explicitly retain the Windows EdgeChromium module.
hiddenimports = [
    "clr",  # pythonnet bootstrap used by pywebview on Windows
    "webview.platforms.edgechromium",
]

# jsonschema validators are selected dynamically through referencing/jsonschema internals.
hiddenimports += collect_submodules("jsonschema")

a = Analysis(
    [str(entry)],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FantasyStore",
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
    contents_directory="_internal",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="FantasyStore",
)
